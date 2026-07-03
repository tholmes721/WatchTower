"""
Prometheus text format parser for Raritan PDU metrics.
Handles the full metric set exported by Raritan PX4/PX3/PX2 devices.

Older PX2/PX3 models may export a subset of the full metric catalogue.
The parser tracks exactly which metric families are present in each export
so the analysis engine can avoid alerting on metrics that were never exported.
"""

import re
from dataclasses import dataclass, field
from typing import Optional, Set


# ── Full metric catalogue — all known raritan_pdu_* families ────────────────
# Split into core (always expected on the PDU itself) and peripheral
# (only present when optional environmental sensors are attached).

CORE_METRIC_FAMILIES: Set[str] = {
    "activeenergy_watthour_total",
    "activepower_watt",
    "apparentenergy_voltamperehour_total",
    "apparentpower_voltampere",
    "current_ampere",
    "currentthd_percent",
    "displacementpowerfactor",
    "info",
    "inletrating",
    "linefrequency_hertz",
    "ocprating",
    "outletrating",
    "outletstate",
    "phaseangle_degree",
    "powerfactor",
    "unbalancedcurrent_percent",
    "voltage_volt",
    "voltageln_volt",
    "voltagethd_percent",
}

PERIPHERAL_METRIC_FAMILIES: Set[str] = {
    "peripheral_temperature_degreecelsius",
    "peripheral_relativehumidity_percent",
    "peripheral_dewpoint_degreecelsius",
    "peripheral_absolutehumidity_gpercubicmeter",
    "peripheral_airflow_meterpersecond",
    "peripheral_airpressure_pascal",
}

# Combined set for inference and general lookups
ALL_METRIC_FAMILIES: Set[str] = CORE_METRIC_FAMILIES | PERIPHERAL_METRIC_FAMILIES

# Regex to parse a Prometheus metric line
# Matches: metric_name{label="val", ...} value [timestamp]
_METRIC_RE = re.compile(
    r'^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)'
    r'(?:\{(?P<labels>[^}]*)\})?\s+'
    r'(?P<value>[-+]?(?:inf|nan|\d+(?:\.\d*)?(?:[eE][-+]?\d+)?))'
    r'(?:\s+(?P<ts>\d+))?$'
)

_LABEL_RE = re.compile(r'(\w+)="([^"]*)"')
_TYPE_RE   = re.compile(r'^# TYPE\s+(raritan_pdu_\S+)\s+\w+$')


@dataclass
class MetricSample:
    name: str
    labels: dict
    value: float


@dataclass
class ParsedSnapshot:
    """All metrics extracted from a single Prometheus export file."""
    # Device identity
    pdu_id: str = ""
    pdu_name: str = ""
    model: str = ""
    serial: str = ""
    manufacturer: str = ""
    firmware_version: str = ""

    # Which metric families were declared in this export (via # TYPE lines).
    # Used by the analysis engine to avoid false alerts on limited-export PDUs.
    exported_families: Set[str] = field(default_factory=set)

    # Inlet-level metrics (keyed by inletid, optionally poleline/linepair)
    inlet_metrics: dict = field(default_factory=dict)

    # Per-outlet metrics (keyed by outletid)
    outlet_metrics: dict = field(default_factory=dict)

    # Overcurrent protector metrics (keyed by overcurrentprotectorid)
    ocp_metrics: dict = field(default_factory=dict)

    # Environmental / peripheral sensors (keyed by sensorslot)
    peripheral_metrics: dict = field(default_factory=dict)

    # Raw samples for anything not categorised above
    other_samples: list = field(default_factory=list)

    def exports(self, family: str) -> bool:
        """Return True if this export declared the given metric family."""
        return family in self.exported_families

    def missing_families(self) -> Set[str]:
        """Return the set of standard families absent from this export."""
        return ALL_METRIC_FAMILIES - self.exported_families


def _parse_labels(label_str: str) -> dict:
    return {k: v for k, v in _LABEL_RE.findall(label_str or "")}


def parse_prometheus_text(text: str) -> ParsedSnapshot:
    """
    Parse raw Prometheus exposition text into a structured ParsedSnapshot.
    """
    snapshot = ParsedSnapshot()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        # ── Track declared metric families from # TYPE lines ─────────────────
        if line.startswith("# TYPE"):
            m = _TYPE_RE.match(line)
            if m:
                family = _strip_prefix(m.group(1))
                snapshot.exported_families.add(family)
            continue

        if line.startswith("# HELP"):
            continue

        m = _METRIC_RE.match(line)
        if not m:
            continue

        name = m.group("name")
        labels = _parse_labels(m.group("labels"))
        try:
            value = float(m.group("value"))
        except ValueError:
            continue

        sample = MetricSample(name=name, labels=labels, value=value)

        # ── Device info ─────────────────────────────────────────────────────
        if name == "raritan_pdu_info":
            snapshot.pdu_id = labels.get("pduid", "")
            snapshot.pdu_name = labels.get("pduname", "")
            snapshot.model = labels.get("model", "")
            snapshot.serial = labels.get("serial", "")
            snapshot.manufacturer = labels.get("manufacturer", "")
            snapshot.firmware_version = labels.get("version", "")
            continue

        # ── Shared PDU id / name ─────────────────────────────────────────────
        pdu_id = labels.get("pduid", "")
        if snapshot.pdu_id == "" and pdu_id:
            snapshot.pdu_id = pdu_id
        pdu_name = labels.get("pduname", "")
        if snapshot.pdu_name == "" and pdu_name:
            snapshot.pdu_name = pdu_name

        # ── Inlet metrics ───────────────────────────────────────────────────
        if "inletid" in labels:
            inlet_id = labels["inletid"]
            inlet_name = labels.get("inletname", "")
            poleline = labels.get("poleline")
            linepair = labels.get("linepair")

            if inlet_id not in snapshot.inlet_metrics:
                snapshot.inlet_metrics[inlet_id] = {
                    "inletname": inlet_name,
                    "total": {},
                    "phases": {},
                    "linepairs": {},
                }

            entry = snapshot.inlet_metrics[inlet_id]
            metric_key = _strip_prefix(name)

            if poleline:
                entry["phases"].setdefault(poleline, {})[metric_key] = value
            elif linepair:
                entry["linepairs"].setdefault(linepair, {})[metric_key] = value
            else:
                entry["total"][metric_key] = value
            continue

        # ── Outlet metrics ──────────────────────────────────────────────────
        if "outletid" in labels:
            outlet_id = labels["outletid"]
            outlet_name = labels.get("outletname", "")
            if outlet_id not in snapshot.outlet_metrics:
                snapshot.outlet_metrics[outlet_id] = {"outletname": outlet_name}
            snapshot.outlet_metrics[outlet_id][_strip_prefix(name)] = value
            continue

        # ── OCP metrics ─────────────────────────────────────────────────────
        if "overcurrentprotectorid" in labels:
            ocp_id = labels["overcurrentprotectorid"]
            ocp_name = labels.get("overcurrentprotectorname", "")
            if ocp_id not in snapshot.ocp_metrics:
                snapshot.ocp_metrics[ocp_id] = {"ocpname": ocp_name}
            snapshot.ocp_metrics[ocp_id][_strip_prefix(name)] = value
            continue

        # ── Peripheral / environmental sensors ──────────────────────────────
        if "sensorslot" in labels:
            slot = labels["sensorslot"]
            sensor_name = labels.get("sensorname", "")
            if slot not in snapshot.peripheral_metrics:
                snapshot.peripheral_metrics[slot] = {"sensorname": sensor_name}
            snapshot.peripheral_metrics[slot][_strip_prefix(name)] = value
            continue

        # ── Fallback ─────────────────────────────────────────────────────────
        snapshot.other_samples.append(sample)

    # ── Supplement exported families from parsed data ──────────────────────────
    # TYPE lines may not declare all families (e.g., peripheral sensor metrics
    # are often present as data but not declared with # TYPE). Always merge
    # inferred families so the analysis engine sees everything that's available.
    snapshot.exported_families |= _infer_families(snapshot)

    return snapshot


def _infer_families(snapshot: 'ParsedSnapshot') -> set:
    """
    Infer exported metric families from the actual data present in a snapshot.
    Scans inlet, outlet, OCP, and peripheral metrics for known family keys.
    """
    found = set()

    # Check inlet metrics
    for inlet in snapshot.inlet_metrics.values():
        totals = inlet.get("total", {})
        for key in totals:
            if key in ALL_METRIC_FAMILIES:
                found.add(key)
        for phase_data in inlet.get("phases", {}).values():
            for key in phase_data:
                if key in ALL_METRIC_FAMILIES:
                    found.add(key)
        for lp_data in inlet.get("linepairs", {}).values():
            for key in lp_data:
                if key in ALL_METRIC_FAMILIES:
                    found.add(key)

    # Check outlet metrics
    for outlet in snapshot.outlet_metrics.values():
        for key in outlet:
            if key in ALL_METRIC_FAMILIES:
                found.add(key)

    # Check OCP metrics
    for ocp in snapshot.ocp_metrics.values():
        for key in ocp:
            if key in ALL_METRIC_FAMILIES:
                found.add(key)

    # Check peripheral metrics
    for sensor in snapshot.peripheral_metrics.values():
        for key in sensor:
            if key in ALL_METRIC_FAMILIES:
                found.add(key)

    return found


def _strip_prefix(name: str) -> str:
    """
    Convert raritan_pdu_activepower_watt → activepower_watt
    Convert raritan_pdu_peripheral_temperature_degreecelsius → peripheral_temperature_degreecelsius
    """
    prefix = "raritan_pdu_"
    return name[len(prefix):] if name.startswith(prefix) else name
