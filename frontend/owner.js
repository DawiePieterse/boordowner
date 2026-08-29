// Owner View: read-only dashboard, no login - access is a single token in
// the URL (?key=...). See backend/routers/owner_view.py.

const OWNER_KEY = new URLSearchParams(location.search).get("key") || "";
let _systemSettings = null;

function showDenied() {
  document.getElementById("deniedScreen").classList.remove("hidden");
  document.getElementById("app").classList.add("hidden");
}

function updateBannerFarmName() {
  const el = document.getElementById("headerFarmName");
  if (el) el.textContent = (_systemSettings && _systemSettings.farm_name) || "Boord";
}

function updateBannerClock() {
  const el = document.getElementById("headerDateTime");
  if (!el) return;
  const now = new Date();
  const dateStr = now.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const timeStr = now.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  el.textContent = `${dateStr}  ·  ${timeStr}`;
}

async function updateBannerWeather() {
  const el = document.getElementById("headerWeather");
  if (!el) return;
  try {
    const w = await Boord.api("/api/weather/current");
    if (w && w.no_location) {
      // The farm's GPS isn't set. Say so rather than leaving the strip blank:
      // Weather, Risk and the Harvest Forecast all stay empty until it is,
      // and a silent gap gives no clue why.
      el.innerHTML = `<i class="fa-solid fa-location-dot"></i> Set farm location in Settings`;
    } else if (w && w.temp !== undefined && w.temp !== null) {
      const icon = Boord.weatherIcon(w.condition);
      el.innerHTML = `<i class="fa-solid ${icon}"></i> ${Math.round(w.temp)}°C · ${w.condition}${w.humidity != null ? ` · ${w.humidity}% humidity` : ""}`;
    }
  } catch (e) { /* nice-to-have only */ }
}

function bindCollapsibles() {
  document.querySelectorAll(".collapsible-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.getElementById(btn.dataset.target).classList.toggle("hidden");
      const icon = btn.querySelector(".fa-chevron-down, .fa-chevron-up");
      if (icon) { icon.classList.toggle("fa-chevron-down"); icon.classList.toggle("fa-chevron-up"); }
    });
  });
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
      document.getElementById(`tab-${btn.dataset.tab}`).classList.remove("hidden");
      if (btn.dataset.tab === "analysis") loadAnalysis();
      else if (btn.dataset.tab === "weather") loadWeather();
      else if (btn.dataset.tab === "risk") loadRisk();
    });
  });
}

async function loadAnalysis() {
  await LWAnalysisTab.load(
    () => Boord.api(`/api/owner-view/analysis?token=${encodeURIComponent(OWNER_KEY)}`),
    { onAuthError: () => showDenied() },
  );
}

async function loadWeather() {
  await LWWeatherTab.load(
    () => Boord.api(`/api/owner-view/weather?token=${encodeURIComponent(OWNER_KEY)}`),
    { onAuthError: () => showDenied() },
  );
}

async function loadRisk() {
  await LWRiskTab.load(
    () => Boord.api(`/api/owner-view/risk?token=${encodeURIComponent(OWNER_KEY)}`),
    // See the matching comment in admin/app.js's loadRisk() - this call does
    // real work the default 8s network timeout isn't built for.
    () => Boord.api(`/api/owner-view/risk-forecast?token=${encodeURIComponent(OWNER_KEY)}`, { timeoutMs: 45000 }),
    { onAuthError: () => showDenied() },
  );
}

function refreshActiveTab() {
  const active = document.querySelector(".tab-btn.active");
  const tab = active ? active.dataset.tab : "dashboard";
  if (tab === "analysis") return loadAnalysis();
  if (tab === "weather") return loadWeather();
  if (tab === "risk") return loadRisk();
  return refreshDashboard();
}

function bindDashboard() {
  const today = Boord.localDateStr();
  document.getElementById("dashStart").value = today;
  document.getElementById("dashEnd").value = today;

  Boord.bindDateRangePresets({
    todayBtn: document.getElementById("dashTodayBtn"),
    weekBtn: document.getElementById("dashWeekBtn"),
    seasonBtn: document.getElementById("dashSeasonBtn"),
    startInput: document.getElementById("dashStart"),
    endInput: document.getElementById("dashEnd"),
    seasonYear: () => (_systemSettings && _systemSettings.current_harvest_year) || new Date().getFullYear(),
    onChange: refreshDashboard,
  });
  document.getElementById("dashSupplierFilter").addEventListener("change", refreshDashboard);
}

function renderSupplierOptions(suppliers) {
  const select = document.getElementById("dashSupplierFilter");
  const current = select.value;
  select.innerHTML = `<option value="">All farms / suppliers</option>` +
    suppliers.filter((s) => s.active).map((s) => `<option value="${s.id}">${s.name}${s.is_own_farm ? " (Own Farm)" : ""}</option>`).join("");
  if (current && Array.from(select.options).some((o) => o.value === current)) select.value = current;
}

async function loadSuppliers() {
  const cached = Boord.getCachedJSON("boord_cached_suppliers");
  if (cached) renderSupplierOptions(cached);
  try {
    const suppliers = await Boord.api("/api/suppliers");
    localStorage.setItem("boord_cached_suppliers", JSON.stringify(suppliers));
    renderSupplierOptions(suppliers);
  } catch (e) { /* keep the cached list, or just "All", if this fails */ }
}

function _lotTotals(lots) {
  return {
    crates: lots.reduce((s, l) => s + l.total_crates, 0),
    kg: lots.reduce((s, l) => s + l.total_kg, 0),
  };
}

// The figures from recent successful loads, so an owner away from the farm
// sees the last known state instead of an empty page. Each entry is stored
// against the exact query it was fetched for: the same numbers under a
// different period would be a lie, so an entry is only ever reused for its own
// period+supplier. A handful are kept rather than just the newest, because
// flicking to Season and back must not leave the default Today view - the one
// the page opens on - with nothing to show.
const OWNER_CACHE_KEY = "boord_cached_owner_dash";
const OWNER_CACHE_MAX = 4;

function currentQuery() {
  const start = document.getElementById("dashStart").value;
  const end = document.getElementById("dashEnd").value;
  const supplierId = document.getElementById("dashSupplierFilter").value;
  return `period_start=${start}&period_end=${end}${supplierId ? `&supplier_id=${supplierId}` : ""}`;
}

function readDashboardCache() {
  const cached = Boord.getCachedJSON(OWNER_CACHE_KEY);
  return Array.isArray(cached) ? cached : [];
}

function findCachedDashboard(qs) {
  return readDashboardCache().find((e) => e.qs === qs) || null;
}

function cacheDashboard(qs, harvesting, inTransit, received, summary) {
  const entry = { at: Date.now(), qs, harvesting, inTransit, received, summary };
  const entries = [entry, ...readDashboardCache().filter((e) => e.qs !== qs)].slice(0, OWNER_CACHE_MAX);
  try {
    localStorage.setItem(OWNER_CACHE_KEY, JSON.stringify(entries));
  } catch (e) {
    // Out of quota - a long season can hold a lot of lots. Keep the period
    // actually on screen rather than giving up on caching altogether.
    try {
      localStorage.setItem(OWNER_CACHE_KEY, JSON.stringify([entry]));
    } catch (e2) { /* a full quota must never break the live screen */ }
  }
}

function describeAge(at) {
  const mins = Math.round((Date.now() - at) / 60000);
  if (mins < 1) return "moments ago";
  if (mins < 60) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  return Boord.fmtDateTime(new Date(at));
}

// Updates the banner text in place. Boord.offlineBanner() re-registers its
// online/offline listeners on every call, so it must not be called again.
function setOfflineBannerText(message) {
  const el = document.getElementById("boord-offline-banner");
  if (el) el.innerHTML = `<i class="fa-solid fa-wifi"></i> ${message}`;
}

// Paints the last figures this device saw, but only if they belong to the
// period now selected. Returns whether anything was drawn.
function renderCachedDashboard(qs) {
  const cached = findCachedDashboard(qs);
  if (!cached || !cached.summary) return false;
  renderDashboardKpis(cached.harvesting, cached.inTransit, cached.received, cached.summary);
  renderDashboardLists(cached.harvesting, cached.inTransit, cached.received, cached.summary);
  return true;
}

// Offline with nothing cached for this period. The previous period's figures
// must not be left sitting under the new dates, so say plainly that there is
// nothing to show rather than showing something wrong.
function renderNoOfflineData() {
  document.getElementById("dashKpiGrid").innerHTML = `
    <div class="bg-white rounded-xl shadow p-4 col-span-full text-sm text-slate-500">
      No saved figures for this period on this device - reconnect to load them.
    </div>`;
  ["dash-harvesting", "dash-intransit", "dash-received"].forEach((id) => {
    document.getElementById(`${id}-body`).innerHTML =
      `<div class="p-3 text-sm text-slate-400">Not available offline</div>`;
  });
  document.getElementById("dash-workers-rows").innerHTML =
    `<tr><td class="p-2 text-slate-400" colspan="6">Not available offline</td></tr>`;
  document.getElementById("dash-blocks-rows").innerHTML =
    `<tr><td class="p-2 text-slate-400" colspan="6">Not available offline</td></tr>`;
}

async function refreshDashboard() {
  const qs = currentQuery();

  let harvesting, inTransit, received, summary;
  try {
    [harvesting, inTransit, received, summary] = await Promise.all([
      Boord.api(`/api/lots/pending?${qs}`),
      Boord.api(`/api/lots/in-transit?${qs}`),
      Boord.api(`/api/lots/received?${qs}`),
      Boord.api(`/api/owner-view/summary?token=${encodeURIComponent(OWNER_KEY)}&${qs}`),
    ]);
  } catch (e) {
    // A network failure is NOT an invalid key. Only a real HTTP rejection
    // (bad/expired key) gets the denied screen.
    if (Boord.isNetworkError(e)) {
      Boord.setOffline(true);
      const cached = findCachedDashboard(qs);
      if (renderCachedDashboard(qs)) {
        setOfflineBannerText(`Offline - showing figures from ${describeAge(cached.at)}`);
      } else {
        setOfflineBannerText("Offline - no saved figures for this period on this device");
        renderNoOfflineData();
      }
      return;
    }
    showDenied();
    return;
  }
  Boord.setOffline(false);

  renderDashboardKpis(harvesting, inTransit, received, summary);
  renderDashboardLists(harvesting, inTransit, received, summary);
  cacheDashboard(qs, harvesting, inTransit, received, summary);
}

function renderDashboardKpis(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);
  const allLots = [...harvesting, ...inTransit, ...received];
  const totalCrates = h.crates + t.crates + r.crates;
  const totalKg = h.kg + t.kg + r.kg;
  const avgKgPerLot = allLots.length ? totalKg / allLots.length : 0;
  const avgKgPerCrate = totalCrates ? totalKg / totalCrates : 0;

  const cards = [
    ["Teams Active", `${summary.active_teams} teams`],
    ["Workers Active", `${summary.active_workers} workers`],
    ["Blocks Active", `${summary.active_blocks} blocks`],
    ["Total Kg", `${totalKg.toFixed(1)} kg`],
    ["Total Crates", `${totalCrates} crates`],
    ["Avg Kg/Lot", avgKgPerLot.toFixed(1)],
    ["Avg Kg/Crate", avgKgPerCrate.toFixed(1)],
    ["Harvesting", `${h.crates} crates / ${h.kg.toFixed(1)} kg`],
    ["In Transit", `${t.crates} crates / ${t.kg.toFixed(1)} kg`],
    ["Received", `${r.crates} crates / ${r.kg.toFixed(1)} kg`],
  ];
  document.getElementById("dashKpiGrid").innerHTML = cards.map(([label, value]) => `
    <div class="bg-white rounded-xl shadow p-4">
      <div class="text-xs text-slate-500">${label}</div>
      <div class="text-xl font-bold">${value}</div>
    </div>
  `).join("");
}

function renderDashboardLists(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);

  document.getElementById("dash-harvesting-title").textContent = `Harvesting - ${h.crates} crates / ${h.kg.toFixed(1)} kg`;
  document.getElementById("dash-harvesting-body").innerHTML = harvesting.map((l) => `
    <div class="p-3 urgency-${l.urgency}">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently being harvested</div>`;

  document.getElementById("dash-intransit-title").textContent = `In transit - ${t.crates} crates / ${t.kg.toFixed(1)} kg`;
  document.getElementById("dash-intransit-body").innerHTML = inTransit.map((l) => `
    <div class="p-3 urgency-${l.urgency}">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - ${l.age_minutes} min ago</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing currently in transit</div>`;

  document.getElementById("dash-received-title").textContent = `Received - ${r.crates} crates / ${r.kg.toFixed(1)} kg`;
  document.getElementById("dash-received-body").innerHTML = received.map((l) => `
    <div class="p-3">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${l.total_kg.toFixed(1)} kg - received ${Boord.fmtDateTime(l.received_at)}</div>
    </div>
  `).join("") || `<div class="p-3 text-sm text-slate-400">Nothing received in this period</div>`;

  document.getElementById("dash-workers-title").textContent = `Workers - ${summary.workers.length} workers`;
  document.getElementById("dash-workers-rows").innerHTML = summary.workers.map((w) => `
    <tr class="border-b">
      <td class="p-2">${w.worker_id}</td>
      <td class="p-2">${w.name}</td>
      <td class="p-2">${w.supplier_name}</td>
      <td class="p-2">${w.crates}</td>
      <td class="p-2">${w.total_kg.toFixed(1)}</td>
      <td class="p-2">${w.avg_kg_crate.toFixed(1)}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;

  document.getElementById("dash-blocks-title").textContent = `Blocks - ${summary.blocks.length} blocks`;
  document.getElementById("dash-blocks-rows").innerHTML = summary.blocks.map((b) => `
    <tr class="border-b">
      <td class="p-2">${b.name}</td>
      <td class="p-2">${b.crates}</td>
      <td class="p-2">${b.total_kg.toFixed(1)}</td>
      <td class="p-2">${b.avg_kg_crate.toFixed(1)}</td>
      <td class="p-2">${b.avg_kg_tree != null ? b.avg_kg_tree.toFixed(1) : "-"}</td>
      <td class="p-2">${b.avg_kg_hectare != null ? b.avg_kg_hectare.toFixed(1) : "-"}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;
}

// The page is shown before any request is made. Waiting on the server first
// would leave an owner on an unreachable connection staring at a blank screen
// for as long as the OS takes to give up on the request.
async function init() {
  if (!OWNER_KEY) { showDenied(); return; }

  document.getElementById("appVersion").textContent = `v${Boord.VERSION}`;
  _systemSettings = Boord.getCachedJSON("boord_cached_settings");
  updateBannerFarmName();
  updateBannerClock();
  setInterval(updateBannerClock, 1000);

  bindDashboard();
  bindCollapsibles();
  bindTabs();
  LWAnalysisTab.bind();
  LWWeatherTab.bind();
  LWRiskTab.bind();

  Boord.offlineBanner("Offline - data may be out of date");
  // Refresh in both directions: reconnecting fetches the real figures, and
  // dropping offline re-runs the same path so the banner states the actual
  // age of what is on screen instead of a message left over from earlier.
  Boord.onOfflineChange = () => { refreshActiveTab(); };
  LWPTR.attach(async () => {
    await loadSuppliers();
    await refreshActiveTab();
  });

  document.getElementById("app").classList.remove("hidden");

  // Show the last figures this device saw before touching the network, so a
  // phone out of range has something real on screen immediately rather than an
  // empty dashboard for as long as the OS takes to give up on the request.
  const cachedDash = findCachedDashboard(currentQuery());
  if (cachedDash && renderCachedDashboard(currentQuery())) {
    setOfflineBannerText(`Offline - showing figures from ${describeAge(cachedDash.at)}`);
  }

  // Background from here.
  try {
    _systemSettings = await Boord.api("/api/system-settings");
    localStorage.setItem("boord_cached_settings", JSON.stringify(_systemSettings));
    updateBannerFarmName();
  } catch (e) {
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
  }
  updateBannerWeather();
  await loadSuppliers();
  await refreshDashboard();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

document.addEventListener("DOMContentLoaded", init);
