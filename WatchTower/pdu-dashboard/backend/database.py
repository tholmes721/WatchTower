"""
SQLAlchemy async database setup and models.
SQLite for dev — swap DATABASE_URL for PostgreSQL in production/AWS.
"""

import json
import os
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint, event
)
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, relationship, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./data/pdu.db"
)

engine = create_async_engine(DATABASE_URL, echo=False)

AsyncSessionLocal = sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False
)


class Base(DeclarativeBase):
    pass


# ── PDU configuration ────────────────────────────────────────────────────────

class PDUConfig(Base):
    """Stores connection details for each monitored PDU."""
    __tablename__ = "pdu_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=True)           # populated from raritan_pdu_info after first poll
    host = Column(String(256), nullable=False)          # IP or hostname
    port = Column(Integer, default=443)
    use_https = Column(Boolean, default=True)
    username = Column(String(128), nullable=True)
    password = Column(String(256), nullable=True)       # stored plain for now; encrypt in prod
    poll_interval_seconds = Column(Integer, default=300)
    polling_enabled = Column(Boolean, default=False)
    # Discovered from raritan_pdu_info on first successful poll
    discovered_name = Column(String(128), nullable=True)
    discovered_model = Column(String(128), nullable=True)
    discovered_serial = Column(String(128), nullable=True)
    discovered_firmware = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    snapshots = relationship("Snapshot", back_populates="pdu_config", cascade="all, delete-orphan")


# ── Snapshots (one per scrape/upload) ────────────────────────────────────────

class Snapshot(Base):
    """
    One snapshot = one complete Prometheus scrape from a PDU.
    Heavy metric data is stored as JSON blobs for flexibility.
    """
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, index=True)
    pdu_config_id = Column(Integer, ForeignKey("pdu_configs.id"), nullable=False, index=True)
    captured_at = Column(DateTime, nullable=False, index=True)
    source = Column(String(32), default="upload")   # 'upload' | 'poll'

    # Device identity (denormalised for quick access)
    pdu_id = Column(String(32))
    pdu_name = Column(String(128))
    model = Column(String(128))
    serial = Column(String(128))
    firmware_version = Column(String(64))

    # JSON blobs
    inlet_metrics_json = Column(Text)
    outlet_metrics_json = Column(Text)
    ocp_metrics_json = Column(Text)
    peripheral_metrics_json = Column(Text)
    exported_families_json = Column(Text)   # JSON array of metric family names

    pdu_config = relationship("PDUConfig", back_populates="snapshots")

    @property
    def inlet_metrics(self):
        return json.loads(self.inlet_metrics_json or "{}")

    @property
    def outlet_metrics(self):
        return json.loads(self.outlet_metrics_json or "{}")

    @property
    def ocp_metrics(self):
        return json.loads(self.ocp_metrics_json or "{}")

    @property
    def peripheral_metrics(self):
        return json.loads(self.peripheral_metrics_json or "{}")

    @property
    def exported_families(self):
        return set(json.loads(self.exported_families_json or "[]"))


# ── User accounts ─────────────────────────────────────────────────────────────

class User(Base):
    """User accounts for dashboard access control."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)
    role = Column(String(16), nullable=False, default="viewer")  # 'admin' or 'viewer'
    display_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create all tables (idempotent)."""
    os.makedirs("data", exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
