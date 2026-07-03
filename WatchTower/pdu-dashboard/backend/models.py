"""
Pydantic schemas for API request/response validation.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


# ── PDU Config ───────────────────────────────────────────────────────────────

class PDUConfigCreate(BaseModel):
    host: str
    port: int = 443
    use_https: bool = True
    username: Optional[str] = None
    password: Optional[str] = None
    poll_interval_seconds: int = 300
    polling_enabled: bool = False


class PDUConfigUpdate(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    use_https: Optional[bool] = None
    username: Optional[str] = None
    password: Optional[str] = None
    poll_interval_seconds: Optional[int] = None
    polling_enabled: Optional[bool] = None
    # Internal — set automatically after first successful poll
    discovered_name: Optional[str] = None
    discovered_model: Optional[str] = None
    discovered_serial: Optional[str] = None
    discovered_firmware: Optional[str] = None


class PDUConfigResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    use_https: bool
    username: Optional[str]
    poll_interval_seconds: int
    polling_enabled: bool
    discovered_name: Optional[str] = None
    discovered_model: Optional[str] = None
    discovered_serial: Optional[str] = None
    discovered_firmware: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # password intentionally omitted

    class Config:
        from_attributes = True


class BulkCredentialUpdate(BaseModel):
    """Apply the same credentials to a list of PDU config IDs."""
    pdu_config_ids: List[int]
    username: str
    password: str
    poll_interval_seconds: Optional[int] = None
    polling_enabled: Optional[bool] = None


# ── Snapshots ────────────────────────────────────────────────────────────────

class SnapshotSummary(BaseModel):
    id: int
    pdu_config_id: int
    captured_at: datetime
    source: str
    pdu_id: str
    pdu_name: str
    model: str
    serial: str
    firmware_version: str

    class Config:
        from_attributes = True


class SnapshotDetail(SnapshotSummary):
    inlet_metrics: Dict[str, Any]
    outlet_metrics: Dict[str, Any]
    ocp_metrics: Dict[str, Any]
    peripheral_metrics: Dict[str, Any]
    exported_families: List[str] = []
    missing_families: List[str] = []


# ── Dashboard / Analysis ─────────────────────────────────────────────────────

class AlertItem(BaseModel):
    severity: str           # 'critical' | 'warning' | 'info'
    category: str           # e.g. 'phase_imbalance', 'high_thd', 'voltage_anomaly'
    title: str
    detail: str
    outlet_id: Optional[str] = None
    value: Optional[float] = None
    threshold: Optional[float] = None


class PDUDashboardSummary(BaseModel):
    pdu_config_id: int
    pdu_name: str
    model: str
    serial: str
    firmware_version: str
    host: str
    polling_enabled: bool
    last_snapshot_at: Optional[datetime]
    total_active_power_w: Optional[float]
    total_apparent_power_va: Optional[float]
    total_current_a: Optional[float]
    outlet_count: int
    active_outlet_count: int
    alert_count_critical: int
    alert_count_warning: int
    alerts: List[AlertItem]
    # Export capability info
    exported_family_count: int = 0
    total_family_count: int = 0
    missing_families: List[str] = []


class TrendPoint(BaseModel):
    captured_at: datetime
    value: float


class TrendSeries(BaseModel):
    label: str
    metric: str
    points: List[TrendPoint]


class PDUTrendResponse(BaseModel):
    pdu_config_id: int
    pdu_name: str
    series: List[TrendSeries]
