/* PDU Dashboard — main frontend application */
'use strict';

// ── State ─────────────────────────────────────────────────────────────────
const state = {
  pdus: [],           // PDUConfigResponse[]
  dashboard: [],      // PDUDashboardSummary[]
  currentDetailPduId: null,
  currentSnapshot: null,
  trendChart: null,
  outletSortCol: null,
  outletSortDir: 'asc',
  refreshTimer: null,
  user: null,         // { username, role, display_name } or null
};

const AUTO_REFRESH_MS = 30_000;

// ── Boot ──────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  // Load banner first (shown even on login page context)
  await loadBanner();
  // Check authentication before loading anything
  await checkAuth();
  bindHeaderButtons();
  bindDashboardSearch();
  bindModalClose();
  bindDetailTabs();
  bindOutletTableControls();
  bindTrendControls();
  bindFormSubmits();
  applyRoleVisibility();
  loadDashboard();
  state.refreshTimer = setInterval(loadDashboard, AUTO_REFRESH_MS);
});

// ── Banner ────────────────────────────────────────────────────────────────
async function loadBanner() {
  try {
    const res = await fetch('/api/banner');
    if (!res.ok) return;
    const config = await res.json();
    if (!config.enabled || !config.text) return;

    const el = document.getElementById('classification-banner');
    if (!el) return;

    el.textContent = config.text;
    el.style.backgroundColor = config.color || '#4f8ef7';
    el.style.color = config.text_color || '#ffffff';
    el.classList.remove('hidden');

    // Adjust sticky toolbar position to account for banner height
    requestAnimationFrame(() => {
      const bannerHeight = el.offsetHeight;
      const toolbar = document.querySelector('.dash-toolbar');
      if (toolbar) {
        toolbar.style.top = (72 + bannerHeight) + 'px';
      }
    });
  } catch (e) {
    // Banner is non-critical — silently ignore errors
  }
}

// ── Auth check ────────────────────────────────────────────────────────────
async function checkAuth() {
  try {
    const res = await fetch('/api/auth/me');
    if (!res.ok) throw new Error('Not authenticated');
    state.user = await res.json();
    // Show username in header
    const userEl = document.getElementById('header-user');
    if (userEl) {
      userEl.textContent = state.user.display_name || state.user.username;
      userEl.title = `${state.user.username} (${state.user.role})`;
    }
  } catch (e) {
    // Not logged in — redirect to login page
    window.location.href = '/login';
  }
}

function isAdmin() {
  return state.user && state.user.role === 'admin';
}

function applyRoleVisibility() {
  // Hide admin-only buttons for viewers/demo
  const adminEls = document.querySelectorAll('.admin-only');
  adminEls.forEach(el => {
    el.style.display = isAdmin() ? '' : 'none';
  });
  // Hide change password button for demo accounts
  const changePwBtn = document.getElementById('btn-change-pw');
  if (changePwBtn) {
    changePwBtn.style.display = (state.user && state.user.role === 'demo') ? 'none' : '';
  }
}

// ── API helpers ───────────────────────────────────────────────────────────
async function api(method, path, body) {
  const opts = {
    method,
    headers: body instanceof FormData ? {} : { 'Content-Type': 'application/json' },
    body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined,
  };
  const res = await fetch('/api' + path, opts);
  if (res.status === 401) {
    // Session expired — redirect to login
    window.location.href = '/login';
    throw new Error('Session expired');
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || res.statusText);
  }
  if (res.status === 204) return null;
  return res.json();
}


// ── Dashboard load ────────────────────────────────────────────────────────
async function loadDashboard() {
  try {
    const [pdus, dashboard] = await Promise.all([
      api('GET', '/pdus'),
      api('GET', '/dashboard'),
    ]);
    state.pdus = pdus;
    state.dashboard = dashboard;
    renderDashboard();
  } catch (e) {
    showGlobalAlert('error', 'Failed to load dashboard: ' + e.message);
  }
}

function renderDashboard() {
  const grid = document.getElementById('dashboard-grid');
  if (state.dashboard.length === 0) {
    grid.innerHTML = `<div class="loading-state">
      No PDUs configured yet.<br><br>
      <button class="btn btn-primary" onclick="openAddPduModal()">+ Add your first PDU</button>
      &nbsp; or &nbsp;
      <button class="btn btn-secondary" onclick="openUploadModal()">↑ Upload a file</button>
    </div>`;
    document.getElementById('dash-count').textContent = '';
    return;
  }

  const query  = (document.getElementById('dash-search')?.value || '').toLowerCase().trim();
  const filter = document.querySelector('.pill.active')?.dataset.filter || 'all';

  const visible = state.dashboard.filter(d => {
    // Status filter
    if (filter === 'critical' && d.alert_count_critical === 0) return false;
    if (filter === 'warning'  && d.alert_count_warning === 0)  return false;
    if (filter === 'ok'       && (d.alert_count_critical > 0 || d.alert_count_warning > 0)) return false;
    if (filter === 'polling'  && !d.polling_enabled) return false;

    // Text search across name, model, serial, firmware
    if (query) {
      const haystack = [
        d.pdu_name, d.model, d.serial, d.firmware_version, d.host
      ].join(' ').toLowerCase();
      if (!haystack.includes(query)) return false;
    }

    return true;
  });

  const countEl = document.getElementById('dash-count');
  if (query || filter !== 'all') {
    countEl.textContent = `${visible.length} of ${state.dashboard.length} PDUs`;
  } else {
    countEl.textContent = `${state.dashboard.length} PDU${state.dashboard.length !== 1 ? 's' : ''}`;
  }

  if (visible.length === 0) {
    grid.innerHTML = `<div class="loading-state">
      No PDUs match your search.<br>
      <button class="btn btn-ghost" style="margin-top:12px" onclick="clearDashSearch()">Clear filters</button>
    </div>`;
    return;
  }

  grid.innerHTML = visible.map(renderPduCard).join('');
}

function renderPduCard(d) {
  const hasCrit = d.alert_count_critical > 0;
  const hasWarn = d.alert_count_warning > 0;
  const cardClass = hasCrit ? 'has-critical' : hasWarn ? 'has-warning' : '';

  const badgeHtml = hasCrit
    ? `<span class="badge badge-critical">⚠ ${d.alert_count_critical} Critical</span>`
    : hasWarn
      ? `<span class="badge badge-warning">⚡ ${d.alert_count_warning} Warning</span>`
      : `<span class="badge badge-ok">✓ OK</span>`;

  const capPct = d.total_family_count > 0
    ? Math.round(d.exported_family_count / d.total_family_count * 100) : 100;
  const capBadge = capPct < 100
    ? `<span class="badge badge-offline" title="Limited export: ${d.missing_families.length} metric families not available on this device">⚠ ${capPct}% export</span>`
    : '';

  let pollHtml;
  if (!d.polling_enabled) {
    pollHtml = `<span class="poll-dot"></span>Manual`;
  } else if (d.poll_status === 'green') {
    const ago = d.poll_last_success_at ? timeAgo(d.poll_last_success_at) : '';
    pollHtml = `<span class="poll-dot poll-green"></span>Polling OK${ago ? ' · ' + ago : ''}`;
  } else if (d.poll_status === 'yellow') {
    pollHtml = `<span class="poll-dot poll-yellow"></span>Last poll failed` +
      (d.poll_last_success_at ? ` · Last OK: ${timeAgo(d.poll_last_success_at)}` : '');
  } else if (d.poll_status === 'red') {
    pollHtml = `<span class="poll-dot poll-red"></span>${d.poll_consecutive_failures}× failed` +
      (d.poll_last_success_at ? ` · Last OK: ${timeAgo(d.poll_last_success_at)}` : ' · Never succeeded');
  } else {
    pollHtml = `<span class="poll-dot poll-waiting"></span>Waiting for first poll`;
  }

  const lastSeen = d.last_snapshot_at
    ? timeAgo(d.last_snapshot_at)
    : 'No data';

  const pW  = d.total_active_power_w   != null ? `${(d.total_active_power_w/1000).toFixed(2)} kW`  : '—';
  const pVA = d.total_apparent_power_va != null ? `${(d.total_apparent_power_va/1000).toFixed(2)} kVA` : '—';
  const pA  = d.total_current_a         != null ? `${d.total_current_a.toFixed(1)} A` : '—';

  const topAlerts = d.alerts.slice(0, 3).map(a => `
    <div class="alert-row ${a.severity}">
      <span class="alert-icon">${severityIcon(a.severity)}</span>
      <span>${a.title}</span>
    </div>`).join('');

  return `
  <div class="pdu-card ${cardClass}" id="pdu-card-${d.pdu_config_id}">
    <div class="card-header">
      <div>
        <div class="card-title">${esc(d.pdu_name)}</div>
        <div class="card-subtitle">${esc(d.model)} · ${esc(d.serial)} · <a href="${pduWebUrl(d)}" target="_blank" rel="noopener" class="pdu-host-link" title="Open PDU web interface">${esc(d.host)}</a></div>
        <div class="card-subtitle">FW: ${esc(d.firmware_version || '—')} · Last: ${lastSeen}</div>
      </div>
      <div class="card-badges">${badgeHtml}${capBadge}</div>
    </div>
    <div class="card-metrics">
      <div class="metric-cell">
        <div class="metric-value">${pW}</div>
        <div class="metric-label">Active Power</div>
      </div>
      <div class="metric-cell">
        <div class="metric-value">${pVA}</div>
        <div class="metric-label">Apparent Power</div>
      </div>
      <div class="metric-cell">
        <div class="metric-value">${pA}</div>
        <div class="metric-label">Total Current</div>
      </div>
      <div class="metric-cell">
        <div class="metric-value">${d.active_outlet_count}</div>
        <div class="metric-label">Active Outlets</div>
        <div class="metric-sub">of ${d.outlet_count} total</div>
      </div>
      <div class="metric-cell">
        <div class="metric-value ${hasCrit ? 'val-crit' : hasWarn ? 'val-warn' : ''}">${d.alert_count_critical + d.alert_count_warning}</div>
        <div class="metric-label">Total Alerts</div>
        <div class="metric-sub">${d.alert_count_critical} crit / ${d.alert_count_warning} warn</div>
      </div>
      <div class="metric-cell">
        <div class="metric-value dim">${d.outlet_count - d.active_outlet_count}</div>
        <div class="metric-label">Idle Outlets</div>
      </div>
    </div>
    ${topAlerts ? `<div class="alert-strip">${topAlerts}</div>` : ''}
    <div class="card-footer">
      <div class="poll-status">${pollHtml}</div>
      <div class="card-actions">
        <button class="btn btn-secondary btn-sm" onclick="openDetailModal(${d.pdu_config_id})">Detail</button>
        ${isAdmin() ? `<button class="btn btn-secondary btn-sm" onclick="openEditPduModal(${d.pdu_config_id})">Edit</button>
        <button class="btn btn-ghost btn-sm" onclick="pollNow(${d.pdu_config_id})" title="Poll now">↻</button>
        <button class="btn btn-danger btn-sm" onclick="openDeletePduModal(${d.pdu_config_id})" title="Delete this PDU">🗑</button>` : ''}
      </div>
    </div>
  </div>`;
}


// ── Detail modal ──────────────────────────────────────────────────────────
async function openDetailModal(pduConfigId) {
  state.currentDetailPduId = pduConfigId;
  try {
    // Load latest snapshot
    const snaps = await api('GET', `/pdus/${pduConfigId}/snapshots?limit=1`);
    if (!snaps || snaps.length === 0) {
      showGlobalAlert('info', 'No snapshots available for this PDU yet.');
      return;
    }
    const snap = await api('GET', `/snapshots/${snaps[0].id}`);
    state.currentSnapshot = snap;

    const pdu = state.dashboard.find(d => d.pdu_config_id === pduConfigId);
    document.getElementById('detail-title').textContent = pdu ? pdu.pdu_name : 'PDU Detail';

    // Populate outlet scope selector for trends
    populateTrendScopeOptions(snap.outlet_metrics, snap.peripheral_metrics);

    // Activate first tab
    switchTab('outlets');
    renderOutletTable();

    openModal('modal-detail');
  } catch (e) {
    showGlobalAlert('error', 'Failed to load PDU detail: ' + e.message);
  }
}

function populateTrendScopeOptions(outletMetrics, peripheralMetrics) {
  const sel = document.getElementById('trend-scope');
  sel.innerHTML = '<option value="">Inlet totals</option>';
  Object.entries(outletMetrics).sort((a,b) => +a[0]-+b[0]).forEach(([id, o]) => {
    const name = o.outletname ? ` — ${o.outletname}` : '';
    sel.innerHTML += `<option value="outlet:${id}">Outlet ${id}${esc(name)}</option>`;
  });
  // Add sensor options
  if (peripheralMetrics && Object.keys(peripheralMetrics).length) {
    sel.innerHTML += '<option disabled>──────────</option>';
    Object.entries(peripheralMetrics).sort((a,b) => +a[0]-+b[0]).forEach(([slot, s]) => {
      const name = s.sensorname ? ` — ${s.sensorname}` : '';
      sel.innerHTML += `<option value="sensor:${slot}">Sensor ${slot}${esc(name)}</option>`;
    });
  }
}

// ── Outlet table ──────────────────────────────────────────────────────────
function renderOutletTable() {
  const snap = state.currentSnapshot;
  if (!snap) return;

  const searchVal = document.getElementById('outlet-search').value.toLowerCase();
  const activeOnly = document.getElementById('outlet-active-only').checked;

  let rows = Object.entries(snap.outlet_metrics)
    .sort((a,b) => +a[0] - +b[0])
    .filter(([id, o]) => {
      if (activeOnly && !(o.outletstate === 1 && o.activepower_watt > 0)) return false;
      const name = (o.outletname || '').toLowerCase();
      return !searchVal || id.includes(searchVal) || name.includes(searchVal);
    });

  if (state.outletSortCol !== null) {
    rows = sortOutletRows(rows, state.outletSortCol, state.outletSortDir);
  }

  const tbody = document.getElementById('outlet-tbody');
  tbody.innerHTML = rows.map(([id, o]) => {
    const state_on = o.outletstate === 1;
    const is_active = state_on && (o.activepower_watt > 0);
    const pf = o.powerfactor ?? o.displacementpowerfactor;
    const thd = o.currentthd_percent;
    const energy_kwh = o.activeenergy_watthour_total != null
      ? (o.activeenergy_watthour_total / 1000).toFixed(2) : '—';

    return `<tr>
      <td>${id}</td>
      <td>${o.outletname ? esc(o.outletname) : '<span class="outlet-name-empty">unnamed</span>'}</td>
      <td class="${is_active ? 'state-on' : state_on ? 'val-dim' : 'state-off'}">${is_active ? 'ON' : state_on ? 'ON (idle)' : 'OFF'}</td>
      <td class="${voltClass(o.voltage_volt)}">${fmt(o.voltage_volt, 1, 'V')}</td>
      <td>${fmt(o.current_ampere, 2, 'A')}</td>
      <td>${fmt(o.activepower_watt, 1, 'W')}</td>
      <td>${fmt(o.apparentpower_voltampere, 1, 'VA')}</td>
      <td class="${pfClass(pf)}">${pf != null ? pf.toFixed(2) : '—'}</td>
      <td class="${thdClass(thd)}">${thd != null ? thd.toFixed(1) + '%' : '—'}</td>
      <td>${energy_kwh !== '—' ? energy_kwh + ' kWh' : '—'}</td>
    </tr>`;
  }).join('');
}

function bindOutletTableControls() {
  document.getElementById('outlet-search').addEventListener('input', renderOutletTable);
  document.getElementById('outlet-active-only').addEventListener('change', renderOutletTable);

  document.getElementById('outlet-table').querySelectorAll('th').forEach((th, i) => {
    th.addEventListener('click', () => {
      if (state.outletSortCol === i) {
        state.outletSortDir = state.outletSortDir === 'asc' ? 'desc' : 'asc';
      } else {
        state.outletSortCol = i;
        state.outletSortDir = 'asc';
      }
      document.querySelectorAll('#outlet-table th').forEach(h => {
        h.classList.remove('sorted-asc', 'sorted-desc');
      });
      th.classList.add(state.outletSortDir === 'asc' ? 'sorted-asc' : 'sorted-desc');
      renderOutletTable();
    });
  });
}

const SORT_KEYS = [
  null,                        // # (use id)
  'outletname',
  'outletstate',
  'voltage_volt',
  'current_ampere',
  'activepower_watt',
  'apparentpower_voltampere',
  'powerfactor',
  'currentthd_percent',
  'activeenergy_watthour_total',
];

function sortOutletRows(rows, colIdx, dir) {
  return [...rows].sort((a, b) => {
    let va, vb;
    if (colIdx === 0) {
      va = +a[0]; vb = +b[0];
    } else {
      const key = SORT_KEYS[colIdx];
      va = a[1][key] ?? -Infinity;
      vb = b[1][key] ?? -Infinity;
      if (typeof va === 'string') va = va.toLowerCase();
      if (typeof vb === 'string') vb = vb.toLowerCase();
    }
    return dir === 'asc' ? (va > vb ? 1 : -1) : (va < vb ? 1 : -1);
  });
}


// ── Inlet tab ─────────────────────────────────────────────────────────────
function renderInletTab() {
  const snap = state.currentSnapshot;
  const container = document.getElementById('inlet-content');
  if (!snap || !Object.keys(snap.inlet_metrics).length) {
    container.innerHTML = '<div class="no-alerts"><span class="no-alerts-icon">📡</span>No inlet data available</div>';
    return;
  }

  let html = '';
  for (const [inletId, inlet] of Object.entries(snap.inlet_metrics)) {
    const t = inlet.total || {};
    html += `<div class="inlet-section">
      <h3>Inlet ${inletId} — ${esc(inlet.inletname || '')}</h3>
      <div class="phase-grid">
        <div class="phase-card">
          <div class="phase-label">TOTAL</div>
          ${phaseMetric('Active Power',    t.activepower_watt,       'W')}
          ${phaseMetric('Apparent Power',  t.apparentpower_voltampere,'VA')}
          ${phaseMetric('Current',         t.current_ampere,          'A')}
          ${phaseMetric('Frequency',       t.linefrequency_hertz,     'Hz')}
          ${phaseMetric('Unbalanced I',    t.unbalancedcurrent_percent,'%')}
        </div>`;

    const phases = inlet.phases || {};
    for (const [phase, data] of Object.entries(phases)) {
      html += `<div class="phase-card">
        <div class="phase-label">${phase}</div>
        ${phaseMetric('Voltage (L-N)', data.voltageln_volt,          'V')}
        ${phaseMetric('Current',       data.current_ampere,           'A')}
        ${phaseMetric('Active Power',  data.activepower_watt,         'W')}
        ${phaseMetric('Power Factor',  data.powerfactor,              '')}
        ${phaseMetric('THD I',         data.currentthd_percent,       '%')}
        ${phaseMetric('THD V',         data.voltagethd_percent,       '%')}
        ${phaseMetric('Phase Angle',   data.phaseangle_degree,        '°')}
      </div>`;
    }

    const linepairs = inlet.linepairs || {};
    for (const [pair, data] of Object.entries(linepairs)) {
      html += `<div class="phase-card">
        <div class="phase-label">${pair}</div>
        ${phaseMetric('Voltage (L-L)', data.voltage_volt, 'V')}
      </div>`;
    }

    html += '</div></div>';
  }

  const ocps = snap.ocp_metrics || {};
  if (Object.keys(ocps).length) {
    html += `<div class="inlet-section"><h3>Overcurrent Protectors</h3><div class="phase-grid">`;
    for (const [ocpId, ocp] of Object.entries(ocps)) {
      const load = ocp.ocprating ? (ocp.current_ampere / ocp.ocprating * 100).toFixed(0) : null;
      html += `<div class="phase-card">
        <div class="phase-label">OCP ${ocpId} ${esc(ocp.ocpname || '')}</div>
        ${phaseMetric('Current',  ocp.current_ampere, 'A')}
        ${phaseMetric('Rating',   ocp.ocprating,      'A')}
        ${load != null ? `<div class="phase-metric"><span>Load</span><span class="pm-val ${+load >= 90 ? 'val-crit' : +load >= 80 ? 'val-warn' : ''}">${load}%</span></div>` : ''}
      </div>`;
    }
    html += '</div></div>';
  }

  container.innerHTML = html;
}

function phaseMetric(label, value, unit) {
  if (value == null) return '';
  return `<div class="phase-metric"><span>${label}</span><span class="pm-val">${value % 1 === 0 ? value : value.toFixed(2)}${unit}</span></div>`;
}

// ── Environment tab ───────────────────────────────────────────────────────
function useFahrenheit() {
  return document.getElementById('temp-unit-toggle')?.checked ?? false;
}

function toDisplayTemp(celsius) {
  if (celsius == null) return null;
  return useFahrenheit() ? (celsius * 9/5 + 32) : celsius;
}

function tempUnit() {
  return useFahrenheit() ? '°F' : '°C';
}

// Thresholds are stored in °C; convert for display comparison only
function tempClass(celsius) {
  if (celsius == null) return '';
  if (celsius >= 40) return 'temp-crit';
  if (celsius >= 35) return 'temp-warn';
  return 'temp-ok';
}

function dewClass(celsius) {
  // Dew point: same visual treatment as temperature
  return tempClass(celsius);
}

function renderEnvTab() {
  const snap = state.currentSnapshot;
  const container = document.getElementById('env-content');
  const sensors = snap?.peripheral_metrics || {};

  if (!Object.keys(sensors).length) {
    container.innerHTML = '<div class="no-alerts"><span class="no-alerts-icon">🌡️</span>No environmental sensors detected</div>';
    return;
  }

  // Wire up toggle to re-render on change (only once)
  const toggle = document.getElementById('temp-unit-toggle');
  if (toggle && !toggle._bound) {
    toggle.addEventListener('change', renderEnvTab);
    toggle._bound = true;
  }

  const unit = tempUnit();

  const cards = Object.values(sensors).map(s => {
    const tempC = s.peripheral_temperature_degreecelsius;
    const hum   = s.peripheral_relativehumidity_percent;
    const dewC  = s.peripheral_dewpoint_degreecelsius;
    const aflow = s.peripheral_airflow_meterpersecond;
    const apres = s.peripheral_airpressure_pascal;
    const ahum  = s.peripheral_absolutehumidity_gpercubicmeter;

    let html = '';

    if (tempC != null) {
      const display = toDisplayTemp(tempC);
      const cls = tempClass(tempC);
      html += `<div class="env-card ${cls}">
        <div class="env-value">${display.toFixed(1)}${unit}</div>
        <div class="env-label">Temperature</div>
        <div class="env-name">${esc(s.sensorname || '')}</div>
      </div>`;
    }

    if (hum != null) {
      const cls = hum >= 80 ? 'humid-crit' : hum >= 70 ? 'humid-warn' : 'humid-ok';
      html += `<div class="env-card ${cls}">
        <div class="env-value">${hum.toFixed(1)}%</div>
        <div class="env-label">Relative Humidity</div>
        <div class="env-name">${esc(s.sensorname || '')}</div>
      </div>`;
    }

    if (dewC != null) {
      const display = toDisplayTemp(dewC);
      const cls = dewClass(dewC);
      html += `<div class="env-card ${cls}">
        <div class="env-value" style="font-size:22px">${display.toFixed(1)}${unit}</div>
        <div class="env-label">Dew Point</div>
        <div class="env-name">${esc(s.sensorname || '')}</div>
      </div>`;
    }

    if (ahum  != null) html += envSimpleCard(`${ahum.toFixed(2)} g/m³`,  'Absolute Humidity', s.sensorname);
    if (aflow != null) html += envSimpleCard(`${aflow.toFixed(2)} m/s`,  'Airflow',            s.sensorname);
    if (apres != null) html += envSimpleCard(`${apres.toFixed(0)} Pa`,   'Air Pressure',       s.sensorname);

    return html;
  }).join('');

  container.innerHTML = `<div class="env-grid">${cards}</div>`;
}

function envSimpleCard(val, label, name) {
  return `<div class="env-card">
    <div class="env-value" style="font-size:22px;color:var(--accent)">${val}</div>
    <div class="env-label">${label}</div>
    <div class="env-name">${esc(name || '')}</div>
  </div>`;
}


// ── Trend tab ─────────────────────────────────────────────────────────────
function bindTrendControls() {
  document.getElementById('btn-load-trend').addEventListener('click', loadTrend);
}

async function loadTrend() {
  const pduId  = state.currentDetailPduId;
  const metric = document.getElementById('trend-metric').value;
  const scope  = document.getElementById('trend-scope').value;
  const limit  = document.getElementById('trend-limit').value;
  const wrap   = document.querySelector('.chart-wrap');

  // Ensure canvas exists
  if (!wrap.querySelector('canvas')) {
    wrap.innerHTML = '<canvas id="trend-chart"></canvas>';
  }

  try {
    const params = new URLSearchParams({ metrics: metric, limit });
    if (scope.startsWith('outlet:')) {
      params.set('outlet_id', scope.replace('outlet:', ''));
    } else if (scope.startsWith('sensor:')) {
      params.set('sensor_id', scope.replace('sensor:', ''));
    }
    const data = await api('GET', `/pdus/${pduId}/trends?${params}`);
    renderTrendChart(data, metric);
  } catch (e) {
    wrap.innerHTML = `<div class="no-alerts">
      <span class="no-alerts-icon">⚠️</span>
      Failed to load trend:<br><span style="color:var(--text-muted);font-size:12px">${esc(e.message)}</span>
    </div>`;
  }
}

function renderTrendChart(data, metric) {
  const ctx = document.getElementById('trend-chart');
  if (state.trendChart) {
    state.trendChart.destroy();
    state.trendChart = null;
  }

  if (!data.series.length || !data.series[0].points.length) {
    ctx.parentElement.innerHTML = `<div class="no-alerts">
      <span class="no-alerts-icon">📈</span>
      Not enough data points yet.<br>
      <span style="color:var(--text-muted);font-size:12px">
        Import snapshots with different timestamps to see trends.<br>
        Tip: use the timestamp field in the upload dialog to spread test files over time.
      </span>
    </div>`;
    return;
  }

  const tz = document.getElementById('trend-timezone').value;
  const palette = ['#4f8ef7', '#36d399', '#fbbd23', '#f87272', '#a855f7', '#f97316'];
  const isTemp = metric === 'peripheral_temperature_degreecelsius';
  const datasets = data.series.map((s, i) => ({
    label: metricLabel(s.metric),
    data: s.points.map(p => ({
      x: new Date(p.captured_at).getTime(),
      y: isTemp ? (p.value * 9/5 + 32) : p.value,
    })),
    borderColor: palette[i % palette.length],
    backgroundColor: palette[i % palette.length] + '22',
    borderWidth: 2,
    pointRadius: s.points.length < 50 ? 4 : 1,
    tension: 0.3,
    fill: false,
  }));

  // Get timezone short name for axis label
  const tzShortName = tz === 'UTC' ? 'UTC' :
    new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: 'short' })
      .formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || tz;

  state.trendChart = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      responsive: true,
      animation: { duration: 300 },
      scales: {
        x: {
          type: 'time',
          time: { tooltipFormat: 'MMM d, HH:mm' },
          ticks: {
            color: '#8892b0',
            maxRotation: 0,
            callback: function(value) {
              const d = new Date(value);
              return d.toLocaleTimeString('en-US', {
                timeZone: tz, hour: '2-digit', minute: '2-digit', hour12: false
              });
            }
          },
          grid: { color: '#2e3348' },
          title: { display: true, text: tzShortName, color: '#8892b0', font: { size: 11 } },
        },
        y: {
          ticks: { color: '#8892b0' },
          grid: { color: '#2e3348' },
          title: { display: true, text: metricLabel(metric), color: '#8892b0' },
        },
      },
      plugins: {
        legend: { labels: { color: '#e2e6f0' } },
        tooltip: {
          callbacks: {
            title: (items) => {
              if (!items.length) return '';
              const d = new Date(items[0].parsed.x);
              return d.toLocaleString('en-US', {
                timeZone: tz, month: 'short', day: 'numeric',
                hour: '2-digit', minute: '2-digit', hour12: true
              }) + ' ' + tzShortName;
            },
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(2)} ${metricUnit(metric)}`,
          },
        },
      },
    },
  });
}

function metricLabel(m) {
  const map = {
    activepower_watt: 'Active Power (W)',
    current_ampere: 'Current (A)',
    voltage_volt: 'Voltage (V)',
    apparentpower_voltampere: 'Apparent Power (VA)',
    powerfactor: 'Power Factor',
    currentthd_percent: 'Current THD (%)',
    peripheral_temperature_degreecelsius: 'Temperature (°F)',
    peripheral_relativehumidity_percent: 'Relative Humidity (%)',
  };
  return map[m] || m;
}

function metricUnit(m) {
  const map = { activepower_watt:'W', current_ampere:'A', voltage_volt:'V',
    apparentpower_voltampere:'VA', powerfactor:'', currentthd_percent:'%',
    peripheral_temperature_degreecelsius:'°F', peripheral_relativehumidity_percent:'%' };
  return map[m] || '';
}

// ── Alerts tab ────────────────────────────────────────────────────────────
function renderAlertsTab() {
  const pdu = state.dashboard.find(d => d.pdu_config_id === state.currentDetailPduId);
  const container = document.getElementById('alerts-content');
  if (!pdu) return;

  let html = '';

  // ── Limited export notice ─────────────────────────────────────────────
  if (pdu.missing_families && pdu.missing_families.length > 0) {
    const capPct = Math.round(pdu.exported_family_count / pdu.total_family_count * 100);
    const familyList = pdu.missing_families
      .map(f => `<span class="family-tag">${esc(f)}</span>`)
      .join('');
    html += `<div class="export-capability-card">
      <div class="export-cap-header">
        <span class="export-cap-icon">📋</span>
        <div>
          <div class="export-cap-title">Limited export — ${capPct}% of metrics available (${pdu.exported_family_count} of ${pdu.total_family_count} families)</div>
          <div class="export-cap-detail">This device does not export the following metric families.
            Alerts that depend on these metrics are automatically suppressed.</div>
        </div>
      </div>
      <div class="family-tag-list">${familyList}</div>
    </div>`;
  }

  // ── Alerts list ───────────────────────────────────────────────────────
  if (!pdu.alerts.length) {
    html += `<div class="no-alerts"><span class="no-alerts-icon">✅</span>No alerts detected</div>`;
  } else {
    html += `<div class="alerts-list">` +
      pdu.alerts.map(a => `
        <div class="alert-card ${a.severity}">
          <div class="alert-severity-icon">${severityIcon(a.severity)}</div>
          <div class="alert-body">
            <div class="alert-title">${esc(a.title)}</div>
            <div class="alert-detail">${esc(a.detail)}</div>
            ${a.value != null ? `<div class="alert-meta">Value: ${a.value}${a.threshold != null ? ' · Threshold: ' + a.threshold : ''}</div>` : ''}
          </div>
        </div>`).join('') + `</div>`;
  }

  container.innerHTML = html;
}


// ── Tab switching ─────────────────────────────────────────────────────────
function bindDetailTabs() {
  document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
  });
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === tabName);
  });
  document.querySelectorAll('.tab-content').forEach(c => {
    c.classList.toggle('hidden', c.id !== 'tab-' + tabName);
  });
  // Lazy-render tab content
  if (tabName === 'inlet')       renderInletTab();
  if (tabName === 'environment') renderEnvTab();
  if (tabName === 'alerts')      renderAlertsTab();
  if (tabName === 'trends') {
    // Restore canvas if it was replaced by no-data message
    const wrap = document.querySelector('.chart-wrap');
    if (!wrap.querySelector('canvas')) {
      wrap.innerHTML = '<canvas id="trend-chart"></canvas>';
    }
  }
}

// ── Modal management ──────────────────────────────────────────────────────
function openModal(id) {
  document.getElementById(id).classList.remove('hidden');
  document.getElementById('modal-backdrop').classList.remove('hidden');
}

function closeAllModals() {
  document.querySelectorAll('.modal').forEach(m => m.classList.add('hidden'));
  document.getElementById('modal-backdrop').classList.add('hidden');
}

function bindModalClose() {
  document.getElementById('modal-backdrop').addEventListener('click', closeAllModals);
  document.querySelectorAll('.modal-close').forEach(btn => {
    btn.addEventListener('click', closeAllModals);
  });
}

// ── Header buttons ────────────────────────────────────────────────────────
function bindHeaderButtons() {
  document.getElementById('btn-upload').addEventListener('click', openUploadModal);
  document.getElementById('btn-add-pdu').addEventListener('click', openAddPduModal);
  document.getElementById('btn-bulk-add').addEventListener('click', openBulkAddModal);
  document.getElementById('btn-bulk-creds').addEventListener('click', openBulkCredsModal);
  document.getElementById('btn-refresh').addEventListener('click', loadDashboard);
  document.getElementById('btn-change-pw').addEventListener('click', openChangePwModal);
  document.getElementById('btn-logout').addEventListener('click', doLogout);
  const btnUsers = document.getElementById('btn-users');
  if (btnUsers) btnUsers.addEventListener('click', openUsersModal);
}

async function doLogout() {
  await fetch('/api/auth/logout', { method: 'POST' });
  localStorage.removeItem('watchtower_user');
  window.location.href = '/login';
}

// ── Change password modal ─────────────────────────────────────────────────
function openChangePwModal() {
  document.getElementById('form-change-pw').reset();
  openModal('modal-change-pw');
}

async function handleChangePw(e) {
  e.preventDefault();
  const current = document.getElementById('pw-current').value;
  const newPw = document.getElementById('pw-new').value;
  const confirm = document.getElementById('pw-confirm').value;

  if (newPw !== confirm) {
    showGlobalAlert('error', 'New passwords do not match.');
    return;
  }
  if (newPw.length < 4) {
    showGlobalAlert('error', 'Password must be at least 4 characters.');
    return;
  }

  try {
    setButtonLoading(e.submitter, true);
    await api('POST', '/auth/change-password', {
      current_password: current,
      new_password: newPw,
    });
    closeAllModals();
    showGlobalAlert('success', 'Password changed successfully.');
  } catch (err) {
    showGlobalAlert('error', 'Failed to change password: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}

// ── Dashboard search & filter ─────────────────────────────────────────────
function bindDashboardSearch() {
  const search = document.getElementById('dash-search');
  const clearBtn = document.getElementById('dash-search-clear');
  let debounce;

  // Show/hide the X button based on whether there's text
  function updateClearBtn() {
    clearBtn.classList.toggle('hidden', search.value.length === 0);
  }

  search.addEventListener('input', () => {
    updateClearBtn();
    clearTimeout(debounce);
    debounce = setTimeout(renderDashboard, 150);
  });
  // Clear on Escape
  search.addEventListener('keydown', e => {
    if (e.key === 'Escape') { search.value = ''; updateClearBtn(); renderDashboard(); }
  });

  // Clear button click
  clearBtn.addEventListener('click', () => {
    search.value = '';
    updateClearBtn();
    search.focus();
    renderDashboard();
  });

  document.querySelectorAll('.pill').forEach(pill => {
    pill.addEventListener('click', () => {
      document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
      pill.classList.add('active');
      renderDashboard();
    });
  });
}

function clearDashSearch() {
  document.getElementById('dash-search').value = '';
  document.getElementById('dash-search-clear').classList.add('hidden');
  document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  document.querySelector('.pill[data-filter="all"]').classList.add('active');
  renderDashboard();
}

// ── Upload modal ──────────────────────────────────────────────────────────
function openUploadModal() {
  // Populate PDU selector
  const sel = document.getElementById('upload-pdu-select');
  sel.innerHTML = '<option value="">Auto-detect from file</option>';
  state.pdus.forEach(p => {
    sel.innerHTML += `<option value="${p.id}">${esc(p.name)}</option>`;
  });
  document.getElementById('form-upload').reset();
  openModal('modal-upload');
}

// ── Add / Edit PDU modal ──────────────────────────────────────────────────
function openAddPduModal() {
  document.getElementById('modal-pdu-title').textContent = 'Add PDU';
  document.getElementById('modal-pdu-desc').textContent =
    'Enter the PDU\'s address and credentials. Name, model, serial, and firmware are discovered automatically on first poll.';
  document.getElementById('modal-pdu-desc').classList.remove('hidden');
  document.getElementById('form-pdu').reset();
  document.getElementById('pdu-edit-id').value = '';
  document.getElementById('pdu-port').value = '443';
  document.getElementById('pdu-interval').value = '300';
  document.getElementById('pdu-https').value = 'true';
  document.getElementById('pdu-polling').checked = false;
  openModal('modal-pdu');
}

function openEditPduModal(pduConfigId) {
  const pdu = state.pdus.find(p => p.id === pduConfigId);
  if (!pdu) return;
  // For edit, show what was discovered so the user knows what device this is
  const snap = state.dashboard.find(d => d.pdu_config_id === pduConfigId);
  const discovered = snap && snap.model
    ? `Discovered: ${snap.pdu_name} · ${snap.model} · S/N ${snap.serial}`
    : 'Not yet polled — device info will be discovered on first poll.';
  document.getElementById('modal-pdu-title').textContent = 'Edit PDU';
  document.getElementById('modal-pdu-desc').textContent = discovered;
  document.getElementById('modal-pdu-desc').classList.remove('hidden');
  document.getElementById('pdu-edit-id').value = pdu.id;
  document.getElementById('pdu-host').value = pdu.host;
  document.getElementById('pdu-port').value = pdu.port;
  document.getElementById('pdu-https').value = String(pdu.use_https);
  document.getElementById('pdu-username').value = pdu.username || '';
  document.getElementById('pdu-password').value = '';
  document.getElementById('pdu-interval').value = pdu.poll_interval_seconds;
  document.getElementById('pdu-polling').checked = pdu.polling_enabled;
  openModal('modal-pdu');
}

// ── Bulk credentials modal ────────────────────────────────────────────────
function openBulkCredsModal() {
  const list = document.getElementById('bulk-pdu-list');
  list.innerHTML = state.pdus.length
    ? state.pdus.map(p => `
        <label>
          <input type="checkbox" value="${p.id}" />
          ${esc(p.name)} <span style="color:var(--text-muted);font-size:11px">(${esc(p.host)})</span>
        </label>`).join('')
    : '<span style="color:var(--text-muted)">No PDUs configured yet.</span>';

  document.getElementById('form-bulk').reset();
  openModal('modal-bulk');
}


// ── Bulk add PDUs modal ───────────────────────────────────────────────────
function openBulkAddModal() {
  document.getElementById('form-bulk-add').reset();
  document.getElementById('bulk-add-port').value = '443';
  document.getElementById('bulk-add-interval').value = '300';
  document.getElementById('bulk-add-https').value = 'true';
  openModal('modal-bulk-add');
}

async function handleBulkAdd(e) {
  e.preventDefault();
  const rawText = document.getElementById('bulk-add-hosts').value;
  const hosts = rawText.split('\n').map(h => h.trim()).filter(h => h.length > 0);

  if (hosts.length === 0) {
    showGlobalAlert('error', 'Enter at least one IP address or hostname.');
    return;
  }

  const payload = {
    hosts,
    port:                  parseInt(document.getElementById('bulk-add-port').value),
    use_https:             document.getElementById('bulk-add-https').value === 'true',
    username:              document.getElementById('bulk-add-username').value.trim() || null,
    password:              document.getElementById('bulk-add-password').value || null,
    poll_interval_seconds: parseInt(document.getElementById('bulk-add-interval').value),
    polling_enabled:       document.getElementById('bulk-add-polling').checked,
  };

  try {
    setButtonLoading(e.submitter, true);
    const result = await api('POST', '/pdus/bulk-add', payload);
    closeAllModals();
    const parts = [];
    if (result.created.length) parts.push(`${result.created.length} PDU(s) added`);
    if (result.skipped.length) parts.push(`${result.skipped.length} skipped (already exist)`);
    showGlobalAlert('success', parts.join(' · ') || 'No changes made.');
    await loadDashboard();
  } catch (err) {
    showGlobalAlert('error', 'Bulk add failed: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}


// ── Form submissions ──────────────────────────────────────────────────────
function bindFormSubmits() {
  document.getElementById('form-upload').addEventListener('submit', handleUpload);
  document.getElementById('form-pdu').addEventListener('submit', handleSavePdu);
  document.getElementById('form-bulk').addEventListener('submit', handleBulkCreds);
  document.getElementById('form-bulk-add').addEventListener('submit', handleBulkAdd);
  document.getElementById('form-add-user').addEventListener('submit', handleAddUser);
  document.getElementById('form-change-pw').addEventListener('submit', handleChangePw);
  document.getElementById('btn-confirm-delete-pdu').addEventListener('click', confirmDeletePdu);
}

async function handleUpload(e) {
  e.preventDefault();
  const fileInput = document.getElementById('upload-file');
  const tsInput   = document.getElementById('upload-ts').value;
  const pduSel    = document.getElementById('upload-pdu-select').value;

  if (!fileInput.files.length) return;

  const form = new FormData();
  form.append('file', fileInput.files[0]);
  if (tsInput)  form.append('captured_at', tsInput);
  if (pduSel)   form.append('pdu_config_id', pduSel);

  try {
    setButtonLoading(e.submitter, true);
    const snap = await api('POST', '/upload', form);
    closeAllModals();
    showGlobalAlert('success', `Imported snapshot #${snap.id} for "${snap.pdu_name || 'unknown PDU'}"`);
    await loadDashboard();
  } catch (err) {
    showGlobalAlert('error', 'Upload failed: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}

async function handleSavePdu(e) {
  e.preventDefault();
  const editId = document.getElementById('pdu-edit-id').value;
  const payload = {
    host:                  document.getElementById('pdu-host').value.trim(),
    port:                  parseInt(document.getElementById('pdu-port').value),
    use_https:             document.getElementById('pdu-https').value === 'true',
    username:              document.getElementById('pdu-username').value.trim() || null,
    poll_interval_seconds: parseInt(document.getElementById('pdu-interval').value),
    polling_enabled:       document.getElementById('pdu-polling').checked,
  };
  const pwd = document.getElementById('pdu-password').value;
  if (pwd) payload.password = pwd;

  try {
    setButtonLoading(e.submitter, true);
    if (editId) {
      await api('PATCH', `/pdus/${editId}`, payload);
      showGlobalAlert('success', 'PDU updated.');
    } else {
      await api('POST', '/pdus', payload);
      showGlobalAlert('success', 'PDU added.');
    }
    closeAllModals();
    await loadDashboard();
  } catch (err) {
    showGlobalAlert('error', 'Save failed: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}

async function handleBulkCreds(e) {
  e.preventDefault();
  const checked = [...document.querySelectorAll('#bulk-pdu-list input:checked')];
  if (!checked.length) {
    showGlobalAlert('error', 'Select at least one PDU.');
    return;
  }

  const intervalVal = document.getElementById('bulk-interval').value;
  const payload = {
    pdu_config_ids: checked.map(c => parseInt(c.value)),
    username:       document.getElementById('bulk-username').value.trim(),
    password:       document.getElementById('bulk-password').value,
    polling_enabled: document.getElementById('bulk-polling').checked,
  };
  if (intervalVal) payload.poll_interval_seconds = parseInt(intervalVal);

  try {
    setButtonLoading(e.submitter, true);
    const updated = await api('POST', '/pdus/bulk-credentials', payload);
    closeAllModals();
    showGlobalAlert('success', `Credentials applied to ${updated.length} PDU(s).`);
    await loadDashboard();
  } catch (err) {
    showGlobalAlert('error', 'Bulk update failed: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}

async function pollNow(pduConfigId) {
  try {
    await api('POST', `/pdus/${pduConfigId}/poll-now`);
    showGlobalAlert('info', 'Poll initiated — refresh in a moment.');
    setTimeout(loadDashboard, 3000);
  } catch (e) {
    showGlobalAlert('error', 'Poll failed: ' + e.message);
  }
}

// ── Delete PDU ────────────────────────────────────────────────────────────
function openDeletePduModal(pduConfigId) {
  const pdu = state.dashboard.find(d => d.pdu_config_id === pduConfigId);
  const name = pdu ? pdu.pdu_name : `PDU ${pduConfigId}`;
  document.getElementById('delete-pdu-id').value = pduConfigId;
  document.getElementById('delete-pdu-name').textContent = name;
  openModal('modal-delete-pdu');
}

async function confirmDeletePdu() {
  const pduId = document.getElementById('delete-pdu-id').value;
  const btn   = document.getElementById('btn-confirm-delete-pdu');
  try {
    setButtonLoading(btn, true);
    await api('DELETE', `/pdus/${pduId}`);
    closeAllModals();
    showGlobalAlert('success', 'PDU deleted.');
    await loadDashboard();
  } catch (e) {
    showGlobalAlert('error', 'Delete failed: ' + e.message);
  } finally {
    setButtonLoading(btn, false);
  }
}


// ── Global alert banner ───────────────────────────────────────────────────
let _alertTimer = null;
function showGlobalAlert(type, msg) {
  const el = document.getElementById('global-alert');
  el.className = `global-alert ${type}`;
  el.textContent = msg;
  el.classList.remove('hidden');
  if (_alertTimer) clearTimeout(_alertTimer);
  _alertTimer = setTimeout(() => el.classList.add('hidden'), 6000);
}

// ── User management modal (admin) ─────────────────────────────────────────
async function openUsersModal() {
  await loadUsersList();
  document.getElementById('form-add-user').reset();
  openModal('modal-users');
}

async function loadUsersList() {
  try {
    const users = await api('GET', '/users');
    const container = document.getElementById('users-list');
    if (!users.length) {
      container.innerHTML = '<span style="color:var(--text-muted)">No users found.</span>';
      return;
    }
    container.innerHTML = `<div class="table-wrap"><table class="data-table">
      <thead><tr><th>Username</th><th>Display Name</th><th>Role</th><th>Last Login</th><th></th></tr></thead>
      <tbody>${users.map(u => `<tr>
        <td>${esc(u.username)}</td>
        <td>${esc(u.display_name || '—')}</td>
        <td><span class="badge ${u.role === 'admin' ? 'badge-critical' : 'badge-ok'}">${u.role}</span></td>
        <td>${u.last_login_at ? timeAgo(u.last_login_at) : 'Never'}</td>
        <td><button class="btn btn-danger btn-sm" onclick="deleteUser(${u.id}, '${esc(u.username)}')">Delete</button></td>
      </tr>`).join('')}</tbody>
    </table></div>`;
  } catch (e) {
    document.getElementById('users-list').innerHTML = `<span style="color:var(--red)">${esc(e.message)}</span>`;
  }
}

async function handleAddUser(e) {
  e.preventDefault();
  const payload = {
    username: document.getElementById('new-user-username').value.trim(),
    password: document.getElementById('new-user-password').value,
    role: document.getElementById('new-user-role').value,
    display_name: document.getElementById('new-user-display').value.trim() || null,
  };
  try {
    setButtonLoading(e.submitter, true);
    await api('POST', '/users', payload);
    showGlobalAlert('success', `User '${payload.username}' created.`);
    document.getElementById('form-add-user').reset();
    await loadUsersList();
  } catch (err) {
    showGlobalAlert('error', 'Failed to create user: ' + err.message);
  } finally {
    setButtonLoading(e.submitter, false);
  }
}

async function deleteUser(userId, username) {
  if (!confirm(`Delete user "${username}"? This cannot be undone.`)) return;
  try {
    await api('DELETE', `/users/${userId}`);
    showGlobalAlert('success', `User '${username}' deleted.`);
    await loadUsersList();
  } catch (err) {
    showGlobalAlert('error', 'Failed to delete user: ' + err.message);
  }
}


// ── Utility / formatting ──────────────────────────────────────────────────
function pduWebUrl(dashboardItem) {
  // Raritan PDU web interface is always https://<ip>
  // Use the IP address directly (not DNS) for airgapped environments
  return `https://${dashboardItem.host}`;
}

function esc(str) {
  return String(str ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function fmt(val, decimals, unit) {
  if (val == null) return '—';
  const n = typeof val === 'number' ? val.toFixed(decimals) : val;
  return unit ? `${n} ${unit}` : `${n}`;
}

function timeAgo(isoStr) {
  if (!isoStr) return '';
  // Backend sends UTC timestamps — ensure they're parsed as UTC
  // (append 'Z' if no timezone indicator present)
  let str = String(isoStr);
  if (!str.endsWith('Z') && !str.includes('+') && !str.includes('-', 10)) {
    str += 'Z';
  }
  const diff = Date.now() - new Date(str).getTime();
  const s = Math.floor(diff / 1000);
  if (s < 0) return 'just now';
  if (s < 60)  return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s/60)}m ago`;
  if (s < 86400) return `${Math.floor(s/3600)}h ago`;
  return `${Math.floor(s/86400)}d ago`;
}

function severityIcon(sev) {
  return sev === 'critical' ? '🔴' : sev === 'warning' ? '🟡' : 'ℹ️';
}

function voltClass(v) {
  if (v == null || v === 0) return 'val-dim';
  // Auto-detect voltage tier and apply ±10% tolerance
  let nominal;
  if (v >= 90 && v <= 145) nominal = 120;
  else if (v >= 175 && v <= 225) nominal = 208;
  else if (v >= 215 && v <= 265) nominal = 230;
  else return 'val-crit'; // doesn't fit any known tier

  const low  = nominal * 0.90;
  const high = nominal * 1.10;
  if (v < low * 0.95 || v > high * 1.05) return 'val-crit';
  if (v < low || v > high) return 'val-warn';
  return 'val-ok';
}

function pfClass(pf) {
  if (pf == null) return '';
  if (pf < 0.75) return 'val-crit';
  if (pf < 0.85) return 'val-warn';
  return 'val-ok';
}

function thdClass(thd) {
  if (thd == null) return '';
  if (thd >= 30) return 'val-crit';
  if (thd >= 20) return 'val-warn';
  return 'val-ok';
}

function setButtonLoading(btn, loading) {
  if (!btn) return;
  btn.disabled = loading;
  btn._orig = btn._orig || btn.textContent;
  btn.textContent = loading ? 'Working…' : btn._orig;
}
