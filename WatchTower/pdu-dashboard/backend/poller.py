"""
Background polling engine.
Uses APScheduler to periodically scrape each enabled PDU's Prometheus endpoint.

Scalability features (designed for 1000+ PDU environments):
- Semaphore-based concurrency limiter prevents overwhelming the network
- Staggered scheduling spreads PDUs across the poll interval window
- Shared httpx client with connection pooling for efficiency
- Extended timeouts for slow PDUs that take up to 30s to export metrics
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select, delete, desc

from .database import AsyncSessionLocal
from .database import PDUConfig as PDUConfigModel
from .database import Snapshot
from .parser import parse_prometheus_text
import json

logger = logging.getLogger(__name__)

# ── Scalability configuration ────────────────────────────────────────────────
# Maximum number of PDUs being scraped simultaneously.
# Each scrape can take up to 60s on slow devices, so this limits how many
# connections are open at once. Adjust based on available memory/bandwidth.
MAX_CONCURRENT_SCRAPES = 100

# HTTP timeout for individual PDU scrapes.
# Raritan PDUs can take 20-30s to collect and export metrics on busy devices.
# Set generously to avoid premature timeouts.
SCRAPE_TIMEOUT_SECONDS = 60.0

# Connection pool limits for the shared httpx client.
# max_connections: total connections across all hosts
# max_keepalive_connections: idle connections kept warm for reuse
MAX_CONNECTIONS = 200
MAX_KEEPALIVE_CONNECTIONS = 100

# Maximum number of snapshots to retain per PDU.
# Older snapshots are automatically deleted after each successful poll.
MAX_SNAPSHOTS_PER_PDU = 100

# ─────────────────────────────────────────────────────────────────────────────

scheduler = AsyncIOScheduler()

# Track job IDs keyed by PDU config id
_job_map: Dict[int, str] = {}

# Semaphore limits concurrent scrapes — prevents connection pile-up
_scrape_semaphore: Optional[asyncio.Semaphore] = None

# Shared httpx client — reuses connections across scrapes for efficiency
_http_client: Optional[httpx.AsyncClient] = None


def _get_semaphore() -> asyncio.Semaphore:
    """Lazily create the semaphore (must be created inside a running event loop)."""
    global _scrape_semaphore
    if _scrape_semaphore is None:
        _scrape_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCRAPES)
    return _scrape_semaphore


def _get_http_client() -> httpx.AsyncClient:
    """Lazily create the shared httpx client with connection pooling."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            verify=False,
            timeout=httpx.Timeout(
                connect=10.0,
                read=SCRAPE_TIMEOUT_SECONDS,
                write=10.0,
                pool=SCRAPE_TIMEOUT_SECONDS,
            ),
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS,
                max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
    return _http_client


async def close_http_client():
    """Gracefully close the shared client (call on app shutdown)."""
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def get_metrics_url(config: PDUConfigModel) -> str:
    scheme = "https" if config.use_https else "http"
    return f"{scheme}://{config.host}:{config.port}/cgi-bin/dump_prometheus.cgi?include_names=1"


def _stagger_offset(pdu_config_id: int, interval_seconds: int) -> int:
    """
    Calculate a deterministic stagger offset for a PDU within its poll interval.

    Uses a hash of the PDU's config ID to evenly distribute start times across
    the interval window. This ensures that even if 1000 PDUs all have the same
    300s interval, their first polls are spread across those 300 seconds rather
    than all firing at t=0.

    Returns offset in seconds (0 to interval_seconds-1).
    """
    # Hash the config ID to get a stable, well-distributed number
    h = hashlib.md5(str(pdu_config_id).encode()).hexdigest()
    hash_int = int(h[:8], 16)  # First 8 hex chars = 32 bits
    return hash_int % interval_seconds


async def scrape_pdu(pdu_config_id: int):
    """
    Fetch metrics from a single PDU and store as a new snapshot.
    Uses a semaphore to limit concurrent scrapes across all PDUs.
    """
    sem = _get_semaphore()

    async with sem:
        # Log when a scrape starts
        logger.info("Scraping PDU config %d (slots in use: %d/%d)",
                    pdu_config_id, MAX_CONCURRENT_SCRAPES - sem._value, MAX_CONCURRENT_SCRAPES)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PDUConfigModel).where(PDUConfigModel.id == pdu_config_id)
            )
            config = result.scalar_one_or_none()
            if config is None:
                logger.warning("PDU config %d no longer exists, skipping scrape", pdu_config_id)
                return
            if not config.polling_enabled:
                logger.debug("PDU %s polling disabled, skipping scrape", config.name)
                return

            url = get_metrics_url(config)
            try:
                auth = None
                if config.username:
                    auth = (config.username, config.password or "")

                client = _get_http_client()
                response = await client.get(url, auth=auth)
                response.raise_for_status()
                text = response.text

            except httpx.TimeoutException:
                logger.warning("Timeout scraping PDU %s (%s) after %ds",
                               config.name, url, SCRAPE_TIMEOUT_SECONDS)
                return
            except Exception as exc:
                logger.error("Failed to scrape PDU %s (%s): %s", config.name, url, exc)
                return

            try:
                parsed = parse_prometheus_text(text)
            except Exception as exc:
                logger.error("Failed to parse metrics from PDU %s: %s", config.name, exc)
                return

            snapshot = Snapshot(
                pdu_config_id=config.id,
                captured_at=datetime.utcnow(),
                source="poll",
                pdu_id=parsed.pdu_id,
                pdu_name=parsed.pdu_name,
                model=parsed.model,
                serial=parsed.serial,
                firmware_version=parsed.firmware_version,
                inlet_metrics_json=json.dumps(parsed.inlet_metrics),
                outlet_metrics_json=json.dumps(parsed.outlet_metrics),
                ocp_metrics_json=json.dumps(parsed.ocp_metrics),
                peripheral_metrics_json=json.dumps(parsed.peripheral_metrics),
                exported_families_json=json.dumps(sorted(parsed.exported_families)),
            )
            db.add(snapshot)

            # Backfill discovered device info onto the config if not already set
            changed = False
            if parsed.pdu_name and config.discovered_name != parsed.pdu_name:
                config.discovered_name = parsed.pdu_name
                # Also update the display name if it's still the host placeholder
                if config.name == config.host:
                    config.name = parsed.pdu_name
                changed = True
            if parsed.model and config.discovered_model != parsed.model:
                config.discovered_model = parsed.model
                changed = True
            if parsed.serial and config.discovered_serial != parsed.serial:
                config.discovered_serial = parsed.serial
                changed = True
            if parsed.firmware_version and config.discovered_firmware != parsed.firmware_version:
                config.discovered_firmware = parsed.firmware_version
                changed = True
            if changed:
                config.updated_at = datetime.utcnow()

            await db.commit()
            logger.info("Scraped PDU %s — snapshot id %s", config.name, snapshot.id)

            # ── Retention: delete old snapshots beyond MAX_SNAPSHOTS_PER_PDU ──
            count_result = await db.execute(
                select(Snapshot.id)
                .where(Snapshot.pdu_config_id == config.id)
                .order_by(desc(Snapshot.captured_at))
                .offset(MAX_SNAPSHOTS_PER_PDU)
            )
            old_ids = [row[0] for row in count_result.fetchall()]
            if old_ids:
                await db.execute(
                    delete(Snapshot).where(Snapshot.id.in_(old_ids))
                )
                await db.commit()
                logger.info("Pruned %d old snapshots for PDU %s (keeping %d)",
                            len(old_ids), config.name, MAX_SNAPSHOTS_PER_PDU)


def schedule_pdu(config: PDUConfigModel, immediate: bool = False):
    """
    Add or update a polling job for a PDU config.
    Uses staggered scheduling to spread polls across the interval window.

    If immediate=True, fires the first poll right away (used when a new PDU
    is added so you don't have to manually hit "poll now").
    """
    job_id = f"pdu_{config.id}"
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    if config.polling_enabled:
        # jitter adds randomness to smooth out bursts
        jitter = int(config.poll_interval_seconds * 0.05)  # 5% jitter

        if immediate:
            # First poll fires immediately, then regular interval after that
            first_run = datetime.utcnow() + timedelta(seconds=2)  # 2s grace for DB commit
        else:
            # Stagger offset distributes PDUs across the interval window
            offset_seconds = _stagger_offset(config.id, config.poll_interval_seconds)
            first_run = datetime.utcnow() + timedelta(seconds=offset_seconds)

        scheduler.add_job(
            scrape_pdu,
            trigger=IntervalTrigger(
                seconds=config.poll_interval_seconds,
                jitter=jitter,
            ),
            id=job_id,
            args=[config.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            next_run_time=first_run,
        )
        _job_map[config.id] = job_id
        logger.info(
            "Scheduled polling for PDU %s every %ss (first poll: %s, jitter: ±%ds)",
            config.name, config.poll_interval_seconds,
            "immediate" if immediate else f"staggered {_stagger_offset(config.id, config.poll_interval_seconds)}s",
            jitter
        )
    else:
        _job_map.pop(config.id, None)
        logger.info("Polling disabled for PDU %s", config.name)


def unschedule_pdu(pdu_config_id: int):
    job_id = f"pdu_{pdu_config_id}"
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
    _job_map.pop(pdu_config_id, None)


async def reload_all_schedules():
    """Called at startup to reinstate schedules from DB."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PDUConfigModel))
        configs = result.scalars().all()
        enabled_count = 0
        for config in configs:
            schedule_pdu(config)
            if config.polling_enabled:
                enabled_count += 1
    logger.info(
        "Loaded %d PDU schedules (%d enabled, max concurrent: %d, timeout: %ds)",
        len(configs), enabled_count, MAX_CONCURRENT_SCRAPES, SCRAPE_TIMEOUT_SECONDS
    )
    # Log the next few scheduled jobs for verification
    jobs = scheduler.get_jobs()
    if jobs:
        next_jobs = sorted(jobs, key=lambda j: j.next_run_time or datetime.max)[:5]
        for job in next_jobs:
            logger.info("  Next scheduled: %s at %s", job.id, job.next_run_time)
