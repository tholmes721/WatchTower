"""
PDU Dashboard — FastAPI application entry point.
Serves the REST API and static frontend files.
"""

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from .analysis import analyse
from .auth import (
    SESSION_COOKIE,
    create_session_token,
    ensure_default_admin,
    hash_password,
    require_admin,
    require_auth,
    verify_password,
)
from .database import AsyncSessionLocal, PDUConfig as PDUConfigModel, Snapshot, User, get_db, init_db
from .models import (
    BulkCredentialUpdate,
    BulkPDUAdd,
    BulkPDUAddResponse,
    LoginRequest,
    LoginResponse,
    PasswordChange,
    PDUConfigCreate,
    PDUConfigResponse,
    PDUConfigUpdate,
    PDUDashboardSummary,
    PDUTrendResponse,
    SnapshotDetail,
    SnapshotSummary,
    TrendPoint,
    TrendSeries,
    UserCreate,
    UserResponse,
)
from .parser import parse_prometheus_text
from .poller import reload_all_schedules, schedule_pdu, scrape_pdu, unschedule_pdu
from .poller import scheduler as bg_scheduler, close_http_client

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="WatchTower",
    description="Raritan PDU monitoring — WatchTower",
    version="1.0.0",
)


# ── Lifecycle ─────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def startup():
    await init_db()
    await ensure_default_admin()
    bg_scheduler.start()
    await reload_all_schedules()
    logger.info("PDU Dashboard started")


@app.on_event("shutdown")
async def shutdown():
    bg_scheduler.shutdown(wait=False)
    await close_http_client()


# ── Authentication endpoints ──────────────────────────────────────────────────

@app.post("/api/auth/login", response_model=LoginResponse)
async def login(payload: LoginRequest, response: Response, db: AsyncSession = Depends(get_db)):
    """Authenticate and set session cookie."""
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Invalid username or password")
    user.last_login_at = datetime.utcnow()
    await db.commit()
    token = create_session_token(user.id, user.username, user.role)
    response.set_cookie(
        key=SESSION_COOKIE, value=token, httponly=True,
        samesite="lax", max_age=86400, path="/",
    )
    return LoginResponse(username=user.username, role=user.role, display_name=user.display_name)


@app.post("/api/auth/logout")
async def logout(response: Response):
    """Clear session cookie."""
    response.delete_cookie(key=SESSION_COOKIE, path="/")
    return {"message": "Logged out"}


@app.get("/api/auth/me", response_model=LoginResponse)
async def get_me(user: User = Depends(require_auth)):
    """Return the current authenticated user info."""
    return LoginResponse(username=user.username, role=user.role, display_name=user.display_name)


@app.post("/api/auth/change-password")
async def change_password(payload: PasswordChange, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Change the current user's password. Demo accounts cannot change passwords."""
    if user.role == "demo":
        raise HTTPException(403, "Demo accounts cannot change passwords")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    user.password_hash = hash_password(payload.new_password)
    await db.commit()
    return {"message": "Password changed successfully"}


# ── User management (admin only) ─────────────────────────────────────────────

@app.get("/api/users", response_model=List[UserResponse])
async def list_users(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).order_by(User.username))
    return result.scalars().all()


@app.post("/api/users", response_model=UserResponse, status_code=201)
async def create_user(payload: UserCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Create a new user account (admin only)."""
    result = await db.execute(select(User).where(User.username == payload.username))
    if result.scalar_one_or_none():
        raise HTTPException(400, f"Username '{payload.username}' already exists")
    if payload.role not in ("admin", "viewer", "demo"):
        raise HTTPException(400, "Role must be 'admin', 'viewer', or 'demo'")
    new_user = User(
        username=payload.username, password_hash=hash_password(payload.password),
        role=payload.role, display_name=payload.display_name,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return new_user


@app.delete("/api/users/{user_id}", status_code=204)
async def delete_user(user_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Delete a user account (admin only). Cannot delete yourself."""
    if user_id == admin.id:
        raise HTTPException(400, "Cannot delete your own account")
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "User not found")
    await db.delete(target)
    await db.commit()


@app.patch("/api/users/{user_id}/reset-password")
async def reset_user_password(user_id: int, payload: dict, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Reset another user's password (admin only)."""
    result = await db.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(404, "User not found")
    new_password = payload.get("new_password", "")
    if not new_password or len(new_password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    target.password_hash = hash_password(new_password)
    await db.commit()
    return {"message": f"Password reset for user '{target.username}'"}


# ── PDU Configuration endpoints (read: any auth, write: admin) ────────────────

@app.get("/api/pdus", response_model=List[PDUConfigResponse])
async def list_pdus(user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PDUConfigModel).order_by(PDUConfigModel.name))
    return result.scalars().all()


@app.post("/api/pdus", response_model=PDUConfigResponse, status_code=201)
async def create_pdu(config: PDUConfigCreate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    pdu = PDUConfigModel(name=config.host, **config.model_dump())
    db.add(pdu)
    await db.commit()
    await db.refresh(pdu)
    schedule_pdu(pdu)
    return pdu


@app.get("/api/pdus/{pdu_id}", response_model=PDUConfigResponse)
async def get_pdu(pdu_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    return await _get_pdu_or_404(pdu_id, db)


@app.patch("/api/pdus/{pdu_id}", response_model=PDUConfigResponse)
async def update_pdu(pdu_id: int, update: PDUConfigUpdate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    pdu = await _get_pdu_or_404(pdu_id, db)
    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(pdu, field, value)
    pdu.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(pdu)
    schedule_pdu(pdu)
    return pdu


@app.delete("/api/pdus/{pdu_id}", status_code=204)
async def delete_pdu(pdu_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    pdu = await _get_pdu_or_404(pdu_id, db)
    unschedule_pdu(pdu_id)
    await db.delete(pdu)
    await db.commit()


@app.post("/api/pdus/bulk-credentials", response_model=List[PDUConfigResponse])
async def bulk_update_credentials(payload: BulkCredentialUpdate, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Apply the same credentials (and optionally polling settings) to multiple PDUs."""
    updated = []
    for pid in payload.pdu_config_ids:
        result = await db.execute(select(PDUConfigModel).where(PDUConfigModel.id == pid))
        pdu = result.scalar_one_or_none()
        if pdu is None:
            continue
        pdu.username = payload.username
        pdu.password = payload.password
        if payload.poll_interval_seconds is not None:
            pdu.poll_interval_seconds = payload.poll_interval_seconds
        if payload.polling_enabled is not None:
            pdu.polling_enabled = payload.polling_enabled
        pdu.updated_at = datetime.utcnow()
        schedule_pdu(pdu)
        updated.append(pdu)
    await db.commit()
    for pdu in updated:
        await db.refresh(pdu)
    return updated


@app.post("/api/pdus/bulk-add", response_model=BulkPDUAddResponse, status_code=201)
async def bulk_add_pdus(payload: BulkPDUAdd, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Add multiple PDUs at once from a list of IP addresses/hostnames."""
    created = []
    skipped = []
    for host in payload.hosts:
        host = host.strip()
        if not host:
            continue
        result = await db.execute(
            select(PDUConfigModel).where(PDUConfigModel.host == host, PDUConfigModel.port == payload.port)
        )
        if result.scalar_one_or_none():
            skipped.append(host)
            continue
        pdu = PDUConfigModel(
            name=host, host=host, port=payload.port, use_https=payload.use_https,
            username=payload.username, password=payload.password,
            poll_interval_seconds=payload.poll_interval_seconds, polling_enabled=payload.polling_enabled,
        )
        db.add(pdu)
        created.append(pdu)
    await db.commit()
    for pdu in created:
        await db.refresh(pdu)
        schedule_pdu(pdu)
    return BulkPDUAddResponse(created=created, skipped=skipped)


@app.post("/api/pdus/{pdu_id}/poll-now", status_code=202)
async def poll_now(pdu_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    """Trigger an immediate scrape of a PDU regardless of schedule."""
    await _get_pdu_or_404(pdu_id, db)
    import asyncio
    asyncio.create_task(scrape_pdu(pdu_id))
    return {"message": "Scrape initiated"}


# ── File upload (admin only) ──────────────────────────────────────────────────

@app.post("/api/upload", response_model=SnapshotSummary, status_code=201)
async def upload_metrics(
    file: UploadFile = File(...),
    pdu_config_id: Optional[int] = Form(None),
    captured_at: Optional[str] = Form(None),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Upload a Prometheus text export file."""
    content = await file.read()
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    parsed = parse_prometheus_text(text)
    if not parsed.pdu_id and not parsed.pdu_name:
        raise HTTPException(400, "File does not appear to contain Raritan PDU metrics")
    if pdu_config_id is None:
        display_name = parsed.pdu_name or parsed.pdu_id or "Unknown PDU"
        existing = None
        if parsed.serial:
            result = await db.execute(select(PDUConfigModel).where(PDUConfigModel.name == display_name))
            existing = result.scalar_one_or_none()
        if existing:
            pdu_config_id = existing.id
        else:
            new_pdu = PDUConfigModel(name=display_name, host=parsed.serial or "unknown", polling_enabled=False)
            db.add(new_pdu)
            await db.flush()
            pdu_config_id = new_pdu.id
    ts = datetime.utcnow()
    if captured_at:
        try:
            ts = datetime.fromisoformat(captured_at)
        except ValueError:
            pass
    snapshot = Snapshot(
        pdu_config_id=pdu_config_id, captured_at=ts, source="upload",
        pdu_id=parsed.pdu_id, pdu_name=parsed.pdu_name, model=parsed.model,
        serial=parsed.serial, firmware_version=parsed.firmware_version,
        inlet_metrics_json=json.dumps(parsed.inlet_metrics),
        outlet_metrics_json=json.dumps(parsed.outlet_metrics),
        ocp_metrics_json=json.dumps(parsed.ocp_metrics),
        peripheral_metrics_json=json.dumps(parsed.peripheral_metrics),
        exported_families_json=json.dumps(sorted(parsed.exported_families)),
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return snapshot


# ── Snapshots (read: any auth, delete: admin) ────────────────────────────────

@app.get("/api/pdus/{pdu_id}/snapshots", response_model=List[SnapshotSummary])
async def list_snapshots(pdu_id: int, limit: int = Query(50, le=500), user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Snapshot).where(Snapshot.pdu_config_id == pdu_id).order_by(desc(Snapshot.captured_at)).limit(limit)
    )
    return result.scalars().all()


@app.get("/api/snapshots/{snapshot_id}", response_model=SnapshotDetail)
async def get_snapshot(snapshot_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Snapshot).where(Snapshot.id == snapshot_id))
    snap = result.scalar_one_or_none()
    if snap is None:
        raise HTTPException(404, "Snapshot not found")
    return _snapshot_to_detail(snap)


@app.delete("/api/snapshots/{snapshot_id}", status_code=204)
async def delete_snapshot(snapshot_id: int, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Snapshot).where(Snapshot.id == snapshot_id))
    snap = result.scalar_one_or_none()
    if snap is None:
        raise HTTPException(404, "Snapshot not found")
    await db.delete(snap)
    await db.commit()


# ── Dashboard summary (any auth) ─────────────────────────────────────────────

@app.get("/api/dashboard", response_model=List[PDUDashboardSummary])
async def get_dashboard(user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Return a summary card for every configured PDU."""
    result = await db.execute(select(PDUConfigModel).order_by(PDUConfigModel.name))
    pdus = result.scalars().all()
    summaries = []
    for pdu in pdus:
        snap_result = await db.execute(
            select(Snapshot).where(Snapshot.pdu_config_id == pdu.id).order_by(desc(Snapshot.captured_at)).limit(1)
        )
        snap = snap_result.scalar_one_or_none()
        if snap is None:
            summaries.append(PDUDashboardSummary(
                pdu_config_id=pdu.id, pdu_name=pdu.name, model="", serial="",
                firmware_version="", host=pdu.host, polling_enabled=pdu.polling_enabled,
                last_snapshot_at=None, total_active_power_w=None,
                total_apparent_power_va=None, total_current_a=None,
                outlet_count=0, active_outlet_count=0,
                alert_count_critical=0, alert_count_warning=0, alerts=[],
            ))
            continue
        outlets = snap.outlet_metrics
        outlet_count = len(outlets)
        active_outlet_count = sum(
            1 for o in outlets.values()
            if o.get("outletstate") in (1, 1.0) and o.get("activepower_watt", 0) > 0
        )
        total_power = total_apparent = total_current = None
        for inlet in snap.inlet_metrics.values():
            t = inlet.get("total", {})
            if "activepower_watt" in t: total_power = t["activepower_watt"]
            if "apparentpower_voltampere" in t: total_apparent = t["apparentpower_voltampere"]
            if "current_ampere" in t: total_current = t["current_ampere"]
        from .parser import ParsedSnapshot, CORE_METRIC_FAMILIES, _infer_families
        ps = ParsedSnapshot(
            pdu_id=snap.pdu_id, pdu_name=snap.pdu_name, model=snap.model,
            serial=snap.serial, firmware_version=snap.firmware_version,
            inlet_metrics=snap.inlet_metrics, outlet_metrics=snap.outlet_metrics,
            ocp_metrics=snap.ocp_metrics, peripheral_metrics=snap.peripheral_metrics,
            exported_families=set(snap.exported_families),
        )
        ps.exported_families |= _infer_families(ps)
        alerts = analyse(ps)
        critical = sum(1 for a in alerts if a.severity == "critical")
        warning = sum(1 for a in alerts if a.severity == "warning")
        missing = sorted(CORE_METRIC_FAMILIES - ps.exported_families)
        summaries.append(PDUDashboardSummary(
            pdu_config_id=pdu.id, pdu_name=snap.pdu_name or pdu.name,
            model=snap.model, serial=snap.serial, firmware_version=snap.firmware_version,
            host=pdu.host, polling_enabled=pdu.polling_enabled,
            last_snapshot_at=snap.captured_at, total_active_power_w=total_power,
            total_apparent_power_va=total_apparent, total_current_a=total_current,
            outlet_count=outlet_count, active_outlet_count=active_outlet_count,
            alert_count_critical=critical, alert_count_warning=warning, alerts=alerts,
            exported_family_count=len(ps.exported_families & CORE_METRIC_FAMILIES),
            total_family_count=len(CORE_METRIC_FAMILIES), missing_families=missing,
        ))
    return summaries


@app.get("/api/pdus/{pdu_id}/analysis")
async def get_analysis(pdu_id: int, user: User = Depends(require_auth), db: AsyncSession = Depends(get_db)):
    """Run analysis on the latest snapshot for a specific PDU."""
    await _get_pdu_or_404(pdu_id, db)
    snap_result = await db.execute(
        select(Snapshot).where(Snapshot.pdu_config_id == pdu_id).order_by(desc(Snapshot.captured_at)).limit(1)
    )
    snap = snap_result.scalar_one_or_none()
    if snap is None:
        return {"alerts": []}
    from .parser import ParsedSnapshot, _infer_families
    ps = ParsedSnapshot(
        pdu_id=snap.pdu_id, pdu_name=snap.pdu_name,
        inlet_metrics=snap.inlet_metrics, outlet_metrics=snap.outlet_metrics,
        ocp_metrics=snap.ocp_metrics, peripheral_metrics=snap.peripheral_metrics,
        exported_families=set(snap.exported_families),
    )
    ps.exported_families |= _infer_families(ps)
    return {"alerts": analyse(ps)}


# ── Trend data (any auth) ────────────────────────────────────────────────────

@app.get("/api/pdus/{pdu_id}/trends", response_model=PDUTrendResponse)
async def get_trends(
    pdu_id: int,
    metrics: str = Query("activepower_watt,current_ampere,voltage_volt"),
    outlet_id: Optional[str] = Query(None),
    sensor_id: Optional[str] = Query(None),
    limit: int = Query(100, le=500),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return time-series trend data for one PDU."""
    pdu = await _get_pdu_or_404(pdu_id, db)
    snap_result = await db.execute(
        select(Snapshot).where(Snapshot.pdu_config_id == pdu_id).order_by(Snapshot.captured_at).limit(limit)
    )
    snapshots = snap_result.scalars().all()
    requested_metrics = [m.strip() for m in metrics.split(",")]
    series_map: dict[str, list] = {m: [] for m in requested_metrics}
    for snap in snapshots:
        ts = snap.captured_at
        if sensor_id:
            sensor = snap.peripheral_metrics.get(sensor_id, {})
            for metric in requested_metrics:
                val = sensor.get(metric)
                if val is not None:
                    series_map[metric].append(TrendPoint(captured_at=ts, value=val))
        elif outlet_id:
            outlet = snap.outlet_metrics.get(outlet_id, {})
            for metric in requested_metrics:
                val = outlet.get(metric)
                if val is not None:
                    series_map[metric].append(TrendPoint(captured_at=ts, value=val))
        else:
            peripheral_req = [m for m in requested_metrics if m.startswith("peripheral_")]
            inlet_req = [m for m in requested_metrics if not m.startswith("peripheral_")]
            if inlet_req:
                for inlet in snap.inlet_metrics.values():
                    totals = inlet.get("total", {})
                    for metric in inlet_req:
                        val = totals.get(metric)
                        if val is not None:
                            series_map[metric].append(TrendPoint(captured_at=ts, value=val))
                    break
            if peripheral_req:
                for sensor_data in snap.peripheral_metrics.values():
                    for metric in peripheral_req:
                        val = sensor_data.get(metric)
                        if val is not None:
                            series_map[metric].append(TrendPoint(captured_at=ts, value=val))
                    break
    series = [TrendSeries(label=m, metric=m, points=pts) for m, pts in series_map.items() if pts]
    return PDUTrendResponse(pdu_config_id=pdu_id, pdu_name=pdu.name, series=series)


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_pdu_or_404(pdu_id: int, db: AsyncSession) -> PDUConfigModel:
    result = await db.execute(select(PDUConfigModel).where(PDUConfigModel.id == pdu_id))
    pdu = result.scalar_one_or_none()
    if pdu is None:
        raise HTTPException(404, f"PDU config {pdu_id} not found")
    return pdu


def _snapshot_to_detail(snap: Snapshot) -> SnapshotDetail:
    from .parser import CORE_METRIC_FAMILIES
    exported = snap.exported_families
    missing = sorted(CORE_METRIC_FAMILIES - set(exported))
    return SnapshotDetail(
        id=snap.id, pdu_config_id=snap.pdu_config_id, captured_at=snap.captured_at,
        source=snap.source, pdu_id=snap.pdu_id or "", pdu_name=snap.pdu_name or "",
        model=snap.model or "", serial=snap.serial or "",
        firmware_version=snap.firmware_version or "",
        inlet_metrics=snap.inlet_metrics, outlet_metrics=snap.outlet_metrics,
        ocp_metrics=snap.ocp_metrics, peripheral_metrics=snap.peripheral_metrics,
        exported_families=sorted(exported), missing_families=missing,
    )


# ── Static files (frontend) ───────────────────────────────────────────────────

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")

if os.path.isdir(_frontend_dir):
    app.mount("/static", StaticFiles(directory=_frontend_dir), name="static")

    @app.get("/", include_in_schema=False)
    async def serve_index():
        return FileResponse(os.path.join(_frontend_dir, "index.html"))

    @app.get("/login", include_in_schema=False)
    async def serve_login():
        return FileResponse(os.path.join(_frontend_dir, "login.html"))
