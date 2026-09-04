// Boord Owner: read-only dashboard. There is no sign-in - the app opens
// straight onto the Dashboard. Anyone who can reach the server can read
// everything here, which is deliberate: the server binds 127.0.0.1 and
// `tailscale serve` publishes it, so tailnet membership IS the access
// control. See README.md's Access section.

let _systemSettings = null;

function updateBannerFarmName() {
  const el = document.getElementById("headerFarmName");
  if (el) el.textContent = (_systemSettings && _systemSettings.packhouse_name) || "Boord";
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
      el.innerHTML = `<i class="fa-solid fa-location-dot"></i> Set pack house location in Settings`;
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

// Shows one tab and loads it. Also called once at startup to land on the
// Dashboard.
function activateTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
  const panel = document.getElementById(`tab-${name}`);
  if (panel) panel.classList.remove("hidden");
  if (name === "analysis") loadAnalysis();
  else if (name === "weather") loadWeather();
  else if (name === "risk") loadRisk();
}

// The one download this app offers: every harvest figure on file, 1987 to
// the current season, in one workbook. Fetched rather than linked to: the
// server takes long enough building it to need the spinner below, and a
// plain <a href> would report a failure as a broken-looking blank tab.
function bindHistoricalDataDownload() {
  const btn = document.getElementById("historicalDataBtn");
  if (!btn) return;
  btn.addEventListener("click", async () => {
    const original = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Building...`;
    try {
      // Reads both databases and pivots every season it finds, so it is
      // slower than the tab loads around it.
      const blob = await Boord.api("/api/reports/historical-harvest-data",
                                   { timeoutMs: 60000 });
      Boord.downloadBlob(blob, "Historical_Harvest_Data.xlsx");
    } catch (e) {
      console.error("Historical Harvest Data download failed:", e);
      Boord.toast("Could not build the workbook");
    } finally {
      btn.disabled = false;
      btn.innerHTML = original;
    }
  });
}

function bindTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => activateTab(btn.dataset.tab));
  });
}

async function loadAnalysis() {
  await LWAnalysisTab.load(
    () => Boord.api("/api/analysis/summary"),
  );
}

async function loadWeather() {
  await LWWeatherTab.load(
    // Like /api/risk/forecast, this does real work the default 8s network
    // timeout was never sized for: it may first sync the newest hours from
    // Open-Meteo (capped at 3s, and skipped entirely once the current hour
    // is already stored), and then aggregates the WHOLE WeatherHistory
    // record - close to 350,000 hourly rows, grouped into ~14,500 daily
    // points, once a farm has backfilled to 1987. Empty, as it is before
    // that backfill, it returned instantly; that is why this only started
    // failing the day the history was imported.
    //
    // On an 8s budget that raced, and losing the race did not look like
    // slowness: isNetworkError treats the abort as "server unreachable", so
    // the tab set the GLOBAL offline flag and the amber banner appeared on
    // every screen until some other tab loaded successfully. The server was
    // fine and still working the whole time.
    (years) => Boord.api(
      `/api/weather/history${years && years.length ? `?years=${years.join(",")}` : ""}`,
      { timeoutMs: 45000 }),
  );
}

async function loadRisk() {
  await LWRiskTab.load(
    // Both risk calls need a real budget, not just the forecast. summary
    // runs the whole analysis AND reads every weather hour from the first
    // reference season onward - it was left on the 8s default purely
    // because forecast was the slower of the two when they were written,
    // which stopped being true once a farm backfilled forty years.
    () => Boord.api("/api/risk/summary", { timeoutMs: 45000 }),
    // This call does real work the default 8s network timeout isn't built for.
    () => Boord.api("/api/risk/forecast", { timeoutMs: 45000 }),
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
    seasonAnchor: () => ({
      month: (_systemSettings && _systemSettings.season_start_month) || 1,
      day: (_systemSettings && _systemSettings.season_start_day) || 1,
    }),
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
      Boord.api(`/api/dashboard/summary?${qs}`),
    ]);
  } catch (e) {
    // A network failure is NOT an expired session - fall back to cached
    // figures. Only a real HTTP 401/403 sends the user back to sign in.
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
    Boord.toast("Could not load the dashboard");
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

function init() {
  document.getElementById("appVersion").textContent = `v${Boord.VERSION}`;
  _systemSettings = Boord.getCachedJSON("boord_cached_settings");
  updateBannerFarmName();
  updateBannerClock();
  setInterval(updateBannerClock, 1000);

  bindDashboard();
  bindCollapsibles();
  bindTabs();
  bindHistoricalDataDownload();
  LWAnalysisTab.bind();
  LWWeatherTab.bind();
  LWRiskTab.bind();

  Boord.offlineBanner("Offline - data may be out of date");
  Boord.onOfflineChange = () => { refreshActiveTab(); };
  LWPTR.attach(async () => {
    await loadSuppliers();
    await refreshActiveTab();
  });

  activateTab("dashboard");

  // Show the last figures this device saw before touching the network.
  const cachedDash = findCachedDashboard(currentQuery());
  if (cachedDash && renderCachedDashboard(currentQuery())) {
    setOfflineBannerText(`Offline - showing figures from ${describeAge(cachedDash.at)}`);
  }

  refreshAppData();
}

async function refreshAppData() {
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
