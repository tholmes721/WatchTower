"""
Background polling engine.
Uses APScheduler to periodically scrape each enabled PDU's Prometheus endpoint.
"""

import logging
from datetime import datetime
from typing import Dict

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from .database import AsyncSessionLocal
from .database import PDUConfig as PDUConfigModel
from .database import Snapshot
from .parser import parse_prometheus_text
import json

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()
# Track job IDs keyed by PDU config id
_job_map: Dict[int, str] = {}


def get_metrics_url(config: PDUConfigModel) -> str:
    scheme = "https" if config.use_https else "http"
    return f"{scheme}://{config.host}:{config.port}/cgi-bin/dump_prometheus.cgi?include_names=1"


async def scrape_pdu(pdu_config_id: int):
    """Fetch metrics from a single PDU and store as a new snapshot."""
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(PDUConfigModel).where(PDUConfigModel.id == pdu_config_id)
        )
        config = result.scalar_one_or_none()
        if config is None or not config.polling_enabled:
            return

        url = get_metrics_url(config)
        try:
            auth = None
            if config.username:
                auth = (config.username, config.password or "")

            async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
                response = await client.get(url, auth=auth)
                response.raise_for_status()
                text = response.text

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


def schedule_pdu(config: PDUConfigModel):
    """Add or update a polling job for a PDU config."""
    job_id = f"pdu_{config.id}"
    existing = scheduler.get_job(job_id)
    if existing:
        existing.remove()

    if config.polling_enabled:
        scheduler.add_job(
            scrape_pdu,
            trigger=IntervalTrigger(seconds=config.poll_interval_seconds),
            id=job_id,
            args=[config.id],
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        _job_map[config.id] = job_id
        logger.info(
            "Scheduled polling for PDU %s every %ss",
            config.name, config.poll_interval_seconds
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
        for config in configs:
            schedule_pdu(config)
