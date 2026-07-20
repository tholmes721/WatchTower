"""
Background polling engine.
Uses a simple asyncio task loop to periodically scrape each enabled PDU.

Replaced APScheduler with native asyncio for compatibility with Python 3.12+
where the asyncio policy system is deprecated and APScheduler's
AsyncIOScheduler may fail to properly attach to the event loop.

Scalability features (designed for 1000+ PDU environments):
- Semaphore-based concurrency limiter prevents overwhelming the network
- Staggered initial polling spreads PDUs across the first 60 seconds
- Shared httpx client with connection pooling for efficiency
- Extended timeouts for slow PDUs that take up to 30s to export metrics
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Set

import httpx
from sqlalchemy import select, delete, desc

from .database import AsyncSessionLocal
from .database import PDUConfig as PDUConfigModel
from .database import Snapshot
from .parser import parse_prometheus_text
import json

logger = logging.getLogger(__name__)

# ── Scalability configuration ────────────────────────────────────────────────
MAX_CONCURRENT_SCRAPES = 100
SCRAPE_TIMEOUT_SECONDS = 60.0
MAX_CONNECTIONS = 200
MAX_KEEPALIVE_CONNECTIONS = 100
MAX_SNAPSHOTS_PER_PDU = 100
# ─────────────────────────────────────────────────────────────────────────────

# Semaphore limits concurrent scrapes
_scrape_semaphore: Optional[asyncio.Semaphore] = None

# Shared httpx client
_http_client: Optional[httpx.AsyncClient] = None

# Background polling task handle
_polling_task: Optional[asyncio.Task] = None

# Set of PDU config IDs that need an immediate poll (newly added)
_immediate_poll_queue: Set[int] = set()


def _get_semaphore() -> asyncio.Semaphore:
    global _scrape_semaphore
    if _scrape_semaphore is None:
        _scrape_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SCRAPES)
    return _scrape_semaphore


def _get_http_client() -> httpx.AsyncClient:
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
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
        _http_client = None


def get_metrics_url(config: PDUConfigModel) -> str:
    scheme = "https" if config.use_https else "http"
    return f"{scheme}://{config.host}:{config.port}/cgi-bin/dump_prometheus.cgi?include_names=1"


# ── Scrape function ──────────────────────────────────────────────────────────

async def scrape_pdu(pdu_config_id: int):
    """Fetch metrics from a single PDU and store as a new snapshot."""
    sem = _get_semaphore()

    async with sem:
        logger.info("Scraping PDU config %d (slots in use: %d/%d)",
                    pdu_config_id, MAX_CONCURRENT_SCRAPES - sem._value, MAX_CONCURRENT_SCRAPES)

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PDUConfigModel).where(PDUConfigModel.id == pdu_config_id)
            )
            config = result.scalar_one_or_none()
            if config is None:
                logger.warning("PDU config %d no longer exists, skipping", pdu_config_id)
                return
            if not config.polling_enabled:
                logger.debug("PDU %s polling disabled, skipping", config.name)
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

            # Backfill discovered device info
            changed = False
            if parsed.pdu_name and config.discovered_name != parsed.pdu_name:
                config.discovered_name = parsed.pdu_name
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

            # Retention: delete old snapshots beyond MAX_SNAPSHOTS_PER_PDU
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


# ── Background polling loop ──────────────────────────────────────────────────

async def _polling_loop():
    """
    Main background loop that runs forever.
    Every 30 seconds, checks which PDUs are due for a poll and scrapes them.
    """
    logger.info("Background polling loop started.")

    # Track last poll time per PDU
    last_polled: Dict[int, datetime] = {}

    # Initial stagger: spread first polls across 60 seconds
    startup_time = datetime.utcnow()

    while True:
        try:
            # Process immediate poll queue first (newly added PDUs)
            immediate_ids = list(_immediate_poll_queue)
            _immediate_poll_queue.clear()
            if immediate_ids:
                tasks = [asyncio.create_task(scrape_pdu(pid)) for pid in immediate_ids]
                await asyncio.gather(*tasks, return_exceptions=True)
                for pid in immediate_ids:
                    last_polled[pid] = datetime.utcnow()

            # Load all enabled PDU configs
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(PDUConfigModel).where(PDUConfigModel.polling_enabled == True)
                )
                configs = result.scalars().all()

            now = datetime.utcnow()
            due_for_poll = []

            for config in configs:
                # Skip if already handled as immediate
                if config.id in immediate_ids:
                    continue

                last = last_polled.get(config.id)
                if last is None:
                    # Never polled — apply stagger offset (max 60s from startup)
                    h = hashlib.md5(str(config.id).encode()).hexdigest()
                    offset = int(h[:8], 16) % 60
                    stagger_until = startup_time + timedelta(seconds=offset)
                    if now >= stagger_until:
                        due_for_poll.append(config.id)
                else:
                    # Check if interval has elapsed
                    elapsed = (now - last).total_seconds()
                    if elapsed >= config.poll_interval_seconds:
                        due_for_poll.append(config.id)

            # Fire all due polls concurrently (semaphore limits actual concurrency)
            if due_for_poll:
                logger.info("Polling %d PDUs this cycle", len(due_for_poll))
                tasks = [asyncio.create_task(scrape_pdu(pid)) for pid in due_for_poll]
                await asyncio.gather(*tasks, return_exceptions=True)
                for pid in due_for_poll:
                    last_polled[pid] = datetime.utcnow()

        except Exception as exc:
            logger.error("Error in polling loop: %s", exc)

        # Check every 15 seconds
        await asyncio.sleep(15)


# ── Public API (called from main.py) ─────────────────────────────────────────

def schedule_pdu(config: PDUConfigModel, immediate: bool = False):
    """
    Mark a PDU for polling. If immediate=True, it will be polled on the
    next loop cycle (within ~15 seconds).
    """
    if config.polling_enabled and immediate:
        _immediate_poll_queue.add(config.id)
        logger.info("Queued immediate poll for PDU %s", config.name)
    elif config.polling_enabled:
        logger.info("PDU %s scheduled for polling every %ds", config.name, config.poll_interval_seconds)
    else:
        logger.info("Polling disabled for PDU %s", config.name)


def unschedule_pdu(pdu_config_id: int):
    """Remove a PDU from the immediate queue (if present)."""
    _immediate_poll_queue.discard(pdu_config_id)


def start_polling():
    """Start the background polling task. Call from app startup."""
    global _polling_task
    _polling_task = asyncio.create_task(_polling_loop())
    logger.info("Polling engine started (max concurrent: %d, timeout: %ds)",
                MAX_CONCURRENT_SCRAPES, SCRAPE_TIMEOUT_SECONDS)


def stop_polling():
    """Stop the background polling task. Call from app shutdown."""
    global _polling_task
    if _polling_task and not _polling_task.done():
        _polling_task.cancel()
        logger.info("Polling engine stopped")


async def reload_all_schedules():
    """Called at startup to log the initial state."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(PDUConfigModel))
        configs = result.scalars().all()
        enabled_count = sum(1 for c in configs if c.polling_enabled)
    logger.info(
        "Found %d PDUs (%d with polling enabled, interval check every 15s)",
        len(configs), enabled_count
    )
