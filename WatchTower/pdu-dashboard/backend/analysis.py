"""
Analysis engine — inspects a ParsedSnapshot and returns a list of AlertItems.

Design principle: only alert on metrics that were actually present in the
export (tracked via snapshot.exported_families).  This ensures older PX2/PX3
devices that produce limited exports are not penalised for missing data.

Every check that depends on a specific metric family first calls
snapshot.exports('<family>') before evaluating values.
"""

from typing import List
from .models import AlertItem
from .parser import ParsedSnapshot

# ── Configurable thresholds ──────────────────────────────────────────────────
THD_WARNING_PCT           = 20.0
THD_CRITICAL_PCT          = 30.0
VOLTAGE_THD_WARNING_PCT   = 5.0

PHASE_IMBALANCE_WARNING_PCT  = 15.0
PHASE_IMBALANCE_CRITICAL_PCT = 25.0

PF_WARNING  = 0.85
PF_CRITICAL = 0.75

# Voltage tiers — auto-detected based on measured value
VOLTAGE_TIER_120_NOMINAL = 120.0   # North American 120V single-phase
VOLTAGE_TIER_208_NOMINAL = 208.0   # North American 208V 3-phase L-L
VOLTAGE_TIER_230_NOMINAL = 230.0   # International 230V single-phase
VOLTAGE_TOLERANCE_PCT    = 10.0

# Detection boundaries: if measured voltage falls within these ranges,
# use the corresponding nominal for threshold calculations.
# Range: (min_detect, max_detect, nominal)
VOLTAGE_TIERS = [
    (90.0,  145.0, VOLTAGE_TIER_120_NOMINAL),   # ~100–140V → 120V tier
    (175.0, 225.0, VOLTAGE_TIER_208_NOMINAL),   # ~180–220V → 208V tier
    (215.0, 265.0, VOLTAGE_TIER_230_NOMINAL),   # ~220–260V → 230V tier
]

CURRENT_HIGH_WARNING_PCT  = 80.0
CURRENT_HIGH_CRITICAL_PCT = 90.0

NEUTRAL_CURRENT_WARNING_A = 5.0

# Temperature thresholds (defined in °F, converted to °C for sensor comparison)
# Warning: below 60°F or above 89°F
# Critical: below 55°F or above 95°F
TEMP_HIGH_WARNING_F   = 89.0
TEMP_HIGH_CRITICAL_F  = 95.0
TEMP_LOW_WARNING_F    = 60.0
TEMP_LOW_CRITICAL_F   = 55.0

# Sensors report in °C — pre-convert thresholds
TEMP_HIGH_WARNING_C   = (TEMP_HIGH_WARNING_F - 32) * 5 / 9     # ~31.7°C
TEMP_HIGH_CRITICAL_C  = (TEMP_HIGH_CRITICAL_F - 32) * 5 / 9    # ~35.0°C
TEMP_LOW_WARNING_C    = (TEMP_LOW_WARNING_F - 32) * 5 / 9      # ~15.6°C
TEMP_LOW_CRITICAL_C   = (TEMP_LOW_CRITICAL_F - 32) * 5 / 9     # ~12.8°C

HUMIDITY_WARNING  = 70.0
HUMIDITY_CRITICAL = 80.0
# ─────────────────────────────────────────────────────────────────────────────


def analyse(snapshot: ParsedSnapshot) -> List[AlertItem]:
    alerts: List[AlertItem] = []

    _check_phase_imbalance(snapshot, alerts)
    _check_current_thd(snapshot, alerts)
    _check_voltage_thd(snapshot, alerts)
    _check_power_factor(snapshot, alerts)
    _check_voltage_anomalies(snapshot, alerts)
    _check_ocp_loading(snapshot, alerts)
    _check_neutral_current(snapshot, alerts)
    _check_unnamed_loaded_outlets(snapshot, alerts)
    _check_on_but_zero_power(snapshot, alerts)
    _check_environmental(snapshot, alerts)

    _severity_order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: _severity_order.get(a.severity, 3))
    return alerts


# ── Individual checks ────────────────────────────────────────────────────────

def _check_phase_imbalance(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Requires per-phase current data — not available on single-phase PDUs
    # or older models that only export inlet totals.
    # Guard: need at least two L-phases with current_ampere data present.
    if not snapshot.exports("current_ampere"):
        return

    for inlet_id, inlet in snapshot.inlet_metrics.items():
        phases = inlet.get("phases", {})
        currents = {
            ph: data.get("current_ampere")
            for ph, data in phases.items()
            if ph in ("L1", "L2", "L3") and data.get("current_ampere") is not None
        }
        # Need at least 2 phases with real data to calculate imbalance
        if len(currents) < 2:
            continue
        vals = list(currents.values())
        max_c, min_c = max(vals), min(vals)
        if max_c == 0:
            continue
        imbalance_pct = (max_c - min_c) / max_c * 100

        if imbalance_pct >= PHASE_IMBALANCE_CRITICAL_PCT:
            severity = "critical"
        elif imbalance_pct >= PHASE_IMBALANCE_WARNING_PCT:
            severity = "warning"
        else:
            continue

        phase_str = ", ".join(f"{ph}={v:.1f}A" for ph, v in sorted(currents.items()))
        threshold = PHASE_IMBALANCE_CRITICAL_PCT if severity == "critical" else PHASE_IMBALANCE_WARNING_PCT
        alerts.append(AlertItem(
            severity=severity,
            category="phase_imbalance",
            title=f"Phase imbalance on inlet {inlet_id}",
            detail=(f"Current imbalance of {imbalance_pct:.1f}% ({phase_str}). "
                    f"Threshold: {threshold}%"),
            value=round(imbalance_pct, 1),
            threshold=threshold,
        ))


def _check_current_thd(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # THD is not exported by all models — skip entirely if absent
    if not snapshot.exports("currentthd_percent"):
        return

    # Outlet THD
    for outlet_id, outlet in snapshot.outlet_metrics.items():
        thd = outlet.get("currentthd_percent")
        if thd is None:
            continue
        severity = _thd_severity(thd)
        if severity is None:
            continue
        threshold = THD_CRITICAL_PCT if severity == "critical" else THD_WARNING_PCT
        alerts.append(AlertItem(
            severity=severity,
            category="high_thd",
            title=f"High current THD on outlet {_outlet_label(outlet_id, outlet)}",
            detail=f"THD = {thd:.1f}% (threshold: {threshold}%)",
            outlet_id=outlet_id,
            value=thd,
            threshold=threshold,
        ))

    # Inlet phase THD
    for inlet_id, inlet in snapshot.inlet_metrics.items():
        for phase, data in inlet.get("phases", {}).items():
            if phase == "Neutral":
                continue
            thd = data.get("currentthd_percent")
            if thd is None:
                continue
            severity = _thd_severity(thd)
            if severity is None:
                continue
            threshold = THD_CRITICAL_PCT if severity == "critical" else THD_WARNING_PCT
            alerts.append(AlertItem(
                severity=severity,
                category="high_thd",
                title=f"High current THD on inlet {inlet_id} phase {phase}",
                detail=f"THD = {thd:.1f}%",
                value=thd,
                threshold=threshold,
            ))


def _check_voltage_thd(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    if not snapshot.exports("voltagethd_percent"):
        return

    for inlet_id, inlet in snapshot.inlet_metrics.items():
        for phase, data in inlet.get("phases", {}).items():
            thd = data.get("voltagethd_percent")
            if thd is None or thd < VOLTAGE_THD_WARNING_PCT:
                continue
            alerts.append(AlertItem(
                severity="warning",
                category="voltage_thd",
                title=f"Elevated voltage THD on inlet {inlet_id} {phase}",
                detail=f"Voltage THD = {thd:.1f}% (threshold: {VOLTAGE_THD_WARNING_PCT}%)",
                value=thd,
                threshold=VOLTAGE_THD_WARNING_PCT,
            ))


def _check_power_factor(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Check either powerfactor or displacementpowerfactor; skip if neither exported
    has_pf  = snapshot.exports("powerfactor")
    has_dpf = snapshot.exports("displacementpowerfactor")
    if not has_pf and not has_dpf:
        return

    for outlet_id, outlet in snapshot.outlet_metrics.items():
        if outlet.get("activepower_watt", 0) == 0:
            continue
        # Prefer true powerfactor over displacement if both present
        pf = None
        if has_pf:
            pf = outlet.get("powerfactor")
        if pf is None and has_dpf:
            pf = outlet.get("displacementpowerfactor")
        if pf is None:
            continue

        if pf <= PF_CRITICAL:
            severity = "critical"
        elif pf <= PF_WARNING:
            severity = "warning"
        else:
            continue

        threshold = PF_CRITICAL if severity == "critical" else PF_WARNING
        alerts.append(AlertItem(
            severity=severity,
            category="low_power_factor",
            title=f"Low power factor on outlet {_outlet_label(outlet_id, outlet)}",
            detail=f"PF = {pf:.2f} (threshold: {threshold})",
            outlet_id=outlet_id,
            value=pf,
            threshold=threshold,
        ))


def _check_voltage_anomalies(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Requires per-outlet voltage — not always present on older models
    if not snapshot.exports("voltage_volt"):
        return

    for outlet_id, outlet in snapshot.outlet_metrics.items():
        v = outlet.get("voltage_volt")
        if v is None or v == 0:
            continue

        # Auto-detect voltage tier based on measured value
        nominal = _detect_voltage_nominal(v)
        if nominal is None:
            # Voltage doesn't fit any known tier — flag as anomaly
            alerts.append(AlertItem(
                severity="warning",
                category="voltage_anomaly",
                title=f"Unexpected voltage on outlet {_outlet_label(outlet_id, outlet)}",
                detail=f"Voltage = {v:.1f}V — does not match any standard tier (120V/208V/230V)",
                outlet_id=outlet_id,
                value=v,
            ))
            continue

        low  = nominal * (1 - VOLTAGE_TOLERANCE_PCT / 100)
        high = nominal * (1 + VOLTAGE_TOLERANCE_PCT / 100)

        if v < low or v > high:
            severity = "critical" if (v < low * 0.95 or v > high * 1.05) else "warning"
            alerts.append(AlertItem(
                severity=severity,
                category="voltage_anomaly",
                title=f"Voltage out of range on outlet {_outlet_label(outlet_id, outlet)}",
                detail=f"Voltage = {v:.1f}V (nominal {nominal:.0f}V ±{VOLTAGE_TOLERANCE_PCT}%)",
                outlet_id=outlet_id,
                value=v,
                threshold=nominal,
            ))


def _check_ocp_loading(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Requires both current_ampere on OCPs and ocprating
    if not snapshot.exports("ocprating") or not snapshot.exports("current_ampere"):
        return

    for ocp_id, ocp in snapshot.ocp_metrics.items():
        rating  = ocp.get("ocprating")
        current = ocp.get("current_ampere")
        if rating is None or current is None or rating == 0:
            continue
        load_pct = current / rating * 100
        if load_pct >= CURRENT_HIGH_CRITICAL_PCT:
            severity = "critical"
        elif load_pct >= CURRENT_HIGH_WARNING_PCT:
            severity = "warning"
        else:
            continue
        threshold = CURRENT_HIGH_CRITICAL_PCT if severity == "critical" else CURRENT_HIGH_WARNING_PCT
        alerts.append(AlertItem(
            severity=severity,
            category="ocp_overload",
            title=f"OCP {ocp_id} high load",
            detail=f"{current:.1f}A of {rating:.0f}A rating ({load_pct:.1f}%)",
            value=load_pct,
            threshold=threshold,
        ))


def _check_neutral_current(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Neutral current is only meaningful on 3-phase PDUs that export it
    if not snapshot.exports("current_ampere"):
        return

    for inlet_id, inlet in snapshot.inlet_metrics.items():
        neutral = inlet.get("phases", {}).get("Neutral", {})
        current = neutral.get("current_ampere")
        if current is None or current < NEUTRAL_CURRENT_WARNING_A:
            continue
        alerts.append(AlertItem(
            severity="warning",
            category="high_neutral_current",
            title=f"Elevated neutral current on inlet {inlet_id}",
            detail=(f"Neutral current = {current:.2f}A (threshold: {NEUTRAL_CURRENT_WARNING_A}A). "
                    "May indicate load imbalance or harmonics."),
            value=current,
            threshold=NEUTRAL_CURRENT_WARNING_A,
        ))


def _check_unnamed_loaded_outlets(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    # Active power is a baseline metric available on virtually all models
    if not snapshot.exports("activepower_watt"):
        return

    for outlet_id, outlet in snapshot.outlet_metrics.items():
        name  = outlet.get("outletname", "").strip()
        power = outlet.get("activepower_watt", 0)
        if not name and power > 0:
            alerts.append(AlertItem(
                severity="info",
                category="unnamed_outlet",
                title=f"Unnamed outlet {outlet_id} is drawing power",
                detail=f"Outlet {outlet_id} has no label and is consuming {power:.1f}W. Consider assigning a name.",
                outlet_id=outlet_id,
                value=power,
            ))


def _check_on_but_zero_power(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    """
    Outlet switched ON with historical energy but currently 0W.
    Requires both outletstate AND activepower_watt AND activeenergy to be
    exported — otherwise we can't make a reliable determination.
    """
    if not (snapshot.exports("outletstate")
            and snapshot.exports("activepower_watt")
            and snapshot.exports("activeenergy_watthour_total")):
        return

    for outlet_id, outlet in snapshot.outlet_metrics.items():
        state  = outlet.get("outletstate")
        power  = outlet.get("activepower_watt", 0)
        energy = outlet.get("activeenergy_watthour_total", 0)
        if state == 1 and power == 0 and energy > 100:
            alerts.append(AlertItem(
                severity="info",
                category="zero_power_on_outlet",
                title=f"Outlet {_outlet_label(outlet_id, outlet)} is ON but drawing 0W",
                detail=(f"Outlet is switched on with {energy:.0f}Wh historical energy. "
                        "Connected device may be off or failed."),
                outlet_id=outlet_id,
                value=power,
            ))


def _check_environmental(snapshot: ParsedSnapshot, alerts: List[AlertItem]):
    has_temp  = snapshot.exports("peripheral_temperature_degreecelsius")
    has_humid = snapshot.exports("peripheral_relativehumidity_percent")

    if not has_temp and not has_humid:
        return

    for slot, sensor in snapshot.peripheral_metrics.items():
        if has_temp:
            temp = sensor.get("peripheral_temperature_degreecelsius")
            if temp is not None:
                severity = None
                category = None
                # Check high temperature
                if temp >= TEMP_HIGH_CRITICAL_C:
                    severity = "critical"
                    category = "high_temperature"
                    threshold_c = TEMP_HIGH_CRITICAL_C
                    threshold_f = TEMP_HIGH_CRITICAL_F
                elif temp >= TEMP_HIGH_WARNING_C:
                    severity = "warning"
                    category = "high_temperature"
                    threshold_c = TEMP_HIGH_WARNING_C
                    threshold_f = TEMP_HIGH_WARNING_F
                # Check low temperature
                elif temp <= TEMP_LOW_CRITICAL_C:
                    severity = "critical"
                    category = "low_temperature"
                    threshold_c = TEMP_LOW_CRITICAL_C
                    threshold_f = TEMP_LOW_CRITICAL_F
                elif temp <= TEMP_LOW_WARNING_C:
                    severity = "warning"
                    category = "low_temperature"
                    threshold_c = TEMP_LOW_WARNING_C
                    threshold_f = TEMP_LOW_WARNING_F

                if severity:
                    temp_f = temp * 9 / 5 + 32
                    title_prefix = "High" if category == "high_temperature" else "Low"
                    alerts.append(AlertItem(
                        severity=severity,
                        category=category,
                        title=f"{title_prefix} temperature: {sensor.get('sensorname', 'Sensor ' + slot)}",
                        detail=f"Temperature = {temp_f:.1f}°F / {temp:.1f}°C (threshold: {threshold_f:.0f}°F / {threshold_c:.1f}°C)",
                        value=round(temp_f, 1),
                        threshold=threshold_f,
                    ))

        if has_humid:
            humidity = sensor.get("peripheral_relativehumidity_percent")
            if humidity is not None:
                if humidity >= HUMIDITY_CRITICAL:
                    severity = "critical"
                elif humidity >= HUMIDITY_WARNING:
                    severity = "warning"
                else:
                    severity = None
                if severity:
                    threshold = HUMIDITY_CRITICAL if severity == "critical" else HUMIDITY_WARNING
                    alerts.append(AlertItem(
                        severity=severity,
                        category="high_humidity",
                        title=f"High humidity: {sensor.get('sensorname', 'Sensor ' + slot)}",
                        detail=f"Humidity = {humidity:.1f}% (threshold: {threshold}%)",
                        value=humidity,
                        threshold=threshold,
                    ))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_voltage_nominal(measured_voltage: float) -> float | None:
    """
    Auto-detect the voltage tier based on the measured value.
    Returns the nominal voltage (120, 208, or 230) or None if unrecognised.
    """
    for min_v, max_v, nominal in VOLTAGE_TIERS:
        if min_v <= measured_voltage <= max_v:
            return nominal
    return None


def _outlet_label(outlet_id: str, outlet: dict) -> str:
    name = outlet.get("outletname", "").strip()
    return f"{outlet_id} ({name})" if name else outlet_id


def _thd_severity(thd: float):
    if thd >= THD_CRITICAL_PCT:
        return "critical"
    if thd >= THD_WARNING_PCT:
        return "warning"
    return None
