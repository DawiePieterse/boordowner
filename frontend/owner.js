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
      const conditionText = w.condition ? ` · ${w.condition}` : "";
      const humidityText = w.humidity != null ? ` · ${w.humidity}% humidity` : "";
      // rain_today_mm only comes from the farm's own iWeathar station (see
      // backend/weather.py) - Open-Meteo's /current call here never returns
      // it, so its presence is exactly "an on-farm reading exists".
      const rainText = w.rain_today_mm ? ` · ${w.rain_today_mm}mm today` : "";
      // Wind, likewise station-only - and the reading that decides whether
      // today is a spraying day.
      const windText = w.wind_avg_kmh != null
        ? ` · wind ${Math.round(w.wind_avg_kmh)}${w.wind_gust_kmh != null ? `-${Math.round(w.wind_gust_kmh)}` : ""} km/h`
        : "";
      el.innerHTML = `<i class="fa-solid ${icon}"></i> ${Math.round(w.temp)}°C${conditionText}${humidityText}${rainText}${windText}`;
    }
  } catch (e) { /* nice-to-have only */ }
}

// Opens a collapsible section (Harvesting, say) unless the person has
// already toggled it themselves this visit - their choice wins over ours.
function openCollapsible(bodyId) {
  const body = document.getElementById(bodyId);
  const btn = document.querySelector(`.collapsible-header[data-target="${bodyId}"]`);
  if (!body || !btn || btn.dataset.userToggled) return;
  body.classList.remove("hidden");
  const icon = btn.querySelector(".fa-chevron-down");
  if (icon) { icon.classList.remove("fa-chevron-down"); icon.classList.add("fa-chevron-up"); }
}

function bindCollapsibles() {
  document.querySelectorAll(".collapsible-header").forEach((btn) => {
    btn.addEventListener("click", () => {
      btn.dataset.userToggled = "1";
      document.getElementById(btn.dataset.target).classList.toggle("hidden");
      const icon = btn.querySelector(".fa-chevron-down, .fa-chevron-up");
      if (icon) { icon.classList.toggle("fa-chevron-down"); icon.classList.toggle("fa-chevron-up"); }
    });
  });
}

// Shows one tab and loads it. Also called once at startup to land on the
// Dashboard. A tab tap reuses figures loaded within Boord.TAB_FRESH_MS;
// pull-to-refresh and coming back online pass force (see refreshActiveTab).
function activateTab(name, { force = false } = {}) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
  const panel = document.getElementById(`tab-${name}`);
  if (panel) panel.classList.remove("hidden");
  if (name === "analysis") loadAnalysis({ force });
  else if (name === "weather") loadWeather({ force });
  else if (name === "risk") loadRisk({ force });
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

async function loadAnalysis(opts) {
  await LWAnalysisTab.load(
    () => Boord.api("/api/analysis/summary"),
    opts,
  );
}

async function loadWeather(opts) {
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

async function loadRisk(opts) {
  await LWRiskTab.load(
    // Needs a real budget, not the 8s default: this runs the whole
    // analysis, reads every weather hour from the first reference season
    // onward, and fetches a live forecast for the Harvest Forecast card
    // it carries under "forecast".
    () => Boord.api("/api/risk/summary", { timeoutMs: 45000 }),
    opts,
  );
}

// A deliberate refresh (pull-to-refresh, coming back online): always
// refetches, whatever the tab loaded a moment ago.
function refreshActiveTab() {
  const active = document.querySelector(".tab-btn.active");
  const tab = active ? active.dataset.tab : "dashboard";
  if (tab === "analysis") return loadAnalysis({ force: true });
  if (tab === "weather") return loadWeather({ force: true });
  if (tab === "risk") return loadRisk({ force: true });
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
  // Dates typed by hand reload too - only the preset buttons used to, so a
  // custom range sat there showing the previous period's figures.
  document.getElementById("dashStart").addEventListener("change", refreshDashboard);
  document.getElementById("dashEnd").addEventListener("change", refreshDashboard);
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

// Shared with the other tabs' offline fallbacks - see shared/api.js.
const describeAge = Boord.describeAge;
const setOfflineBannerText = Boord.setOfflineBannerText;

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

// Whole kilograms with thousands separators - "12,346 kg", the same way
// Analysis and Risk print them. Per-crate averages keep one decimal.
function fmtKg(v) {
  return `${v.toLocaleString(undefined, { maximumFractionDigits: 0 })} kg`;
}

// The lot colours (see styles.css .urgency-*) mean minutes since the lot
// was opened, against Boord's own thresholds - said once above the lists
// rather than left for the owner to guess.
function urgencyLegend() {
  const s = _systemSettings || {};
  const g = s.green_to_yellow_minutes, y = s.yellow_to_red_minutes;
  if (!g || !y) return "";
  return `<div class="p-2 text-[11px] text-slate-500 flex flex-wrap gap-x-3">
    <span><span class="inline-block w-2.5 h-2.5 rounded-sm align-middle" style="background:#16a34a"></span> under ${g} min</span>
    <span><span class="inline-block w-2.5 h-2.5 rounded-sm align-middle" style="background:#eab308"></span> ${g}-${y} min</span>
    <span><span class="inline-block w-2.5 h-2.5 rounded-sm align-middle" style="background:#C8102E"></span> over ${y} min since the lot was opened</span>
  </div>`;
}

function renderDashboardKpis(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);
  const allLots = [...harvesting, ...inTransit, ...received];
  const grid = document.getElementById("dashKpiGrid");

  // Outside picking hours the default (Today) view is genuinely empty. Ten
  // zero cards read as "broken"; one line that says what to do doesn't.
  if (!allLots.length && !summary.workers.length) {
    const seasonBtn = document.getElementById("dashSeasonBtn");
    const onToday = !seasonBtn || !seasonBtn.classList.contains("active");
    grid.innerHTML = `
      <div class="bg-white rounded-xl shadow p-4 col-span-full text-sm text-slate-500">
        No picking recorded for this period${onToday ? " yet - tap <b>Season</b> for the season to date" : ""}.
      </div>`;
    return;
  }

  const totalCrates = h.crates + t.crates + r.crates;
  const totalKg = h.kg + t.kg + r.kg;
  const avgKgPerLot = allLots.length ? totalKg / allLots.length : 0;
  const avgKgPerCrate = totalCrates ? totalKg / totalCrates : 0;

  const cards = [
    ["Teams Active", `${summary.active_teams} teams`],
    ["Workers Active", `${summary.active_workers} workers`],
    ["Blocks Active", `${summary.active_blocks} blocks`],
    ["Total Kg", fmtKg(totalKg)],
    ["Total Crates", `${totalCrates.toLocaleString()} crates`],
    ["Avg Kg/Lot", avgKgPerLot.toFixed(1), "average net kg per lot (slip) in this period"],
    ["Avg Kg/Crate", avgKgPerCrate.toFixed(1)],
    ["Harvesting", `${h.crates} crates / ${fmtKg(h.kg)}`],
    ["In Transit", `${t.crates} crates / ${fmtKg(t.kg)}`],
    ["Received", `${r.crates} crates / ${fmtKg(r.kg)}`],
  ];
  grid.innerHTML = cards.map(([label, value, hint]) => `
    <div class="bg-white rounded-xl shadow p-4"${hint ? ` title="${hint}"` : ""}>
      <div class="text-xs text-slate-500">${label}</div>
      <div class="text-xl font-bold">${value}</div>
    </div>
  `).join("");
}

function renderDashboardLists(harvesting, inTransit, received, summary) {
  const h = _lotTotals(harvesting);
  const t = _lotTotals(inTransit);
  const r = _lotTotals(received);

  const lotLine = (l, when) => `
    <div class="p-3${l.urgency ? ` urgency-${l.urgency}` : ""}">
      <div class="font-semibold text-sm">${l.slip_number} <span class="text-xs font-normal text-slate-500">${l.supplier_name}</span></div>
      <div class="text-sm">${l.total_crates} crates / ${fmtKg(l.total_kg)} - ${when}</div>
    </div>`;

  document.getElementById("dash-harvesting-title").textContent = `Harvesting - ${h.crates} crates / ${fmtKg(h.kg)}`;
  document.getElementById("dash-harvesting-body").innerHTML = harvesting.length
    ? urgencyLegend() + harvesting.map((l) => lotLine(l, `${l.age_minutes} min ago`)).join("")
    : `<div class="p-3 text-sm text-slate-400">Nothing currently being harvested</div>`;
  // The live list is what an owner opens the page for: show it without a
  // tap when there is anything in it.
  if (harvesting.length) openCollapsible("dash-harvesting-body");

  document.getElementById("dash-intransit-title").textContent = `In transit - ${t.crates} crates / ${fmtKg(t.kg)}`;
  document.getElementById("dash-intransit-body").innerHTML = inTransit.length
    ? urgencyLegend() + inTransit.map((l) => lotLine(l, `${l.age_minutes} min ago`)).join("")
    : `<div class="p-3 text-sm text-slate-400">Nothing currently in transit</div>`;

  document.getElementById("dash-received-title").textContent = `Received - ${r.crates} crates / ${fmtKg(r.kg)}`;
  document.getElementById("dash-received-body").innerHTML = received.map((l) =>
    lotLine({ ...l, urgency: null }, `received ${Boord.fmtDateTime(l.received_at)}`)).join("")
    || `<div class="p-3 text-sm text-slate-400">Nothing received in this period</div>`;

  document.getElementById("dash-workers-title").textContent = `Workers - ${summary.workers.length} workers`;
  document.getElementById("dash-workers-rows").innerHTML = summary.workers.map((w) => `
    <tr class="border-b">
      <td class="p-2">${w.worker_id}</td>
      <td class="p-2">${w.name}</td>
      <td class="p-2">${w.supplier_name}</td>
      <td class="p-2">${w.crates}</td>
      <td class="p-2">${w.total_kg.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
      <td class="p-2">${w.avg_kg_crate.toFixed(1)}</td>
    </tr>
  `).join("") || `<tr><td class="p-2 text-slate-400" colspan="6">No harvest activity in this period</td></tr>`;

  document.getElementById("dash-blocks-title").textContent = `Blocks - ${summary.blocks.length} blocks`;
  document.getElementById("dash-blocks-rows").innerHTML = summary.blocks.map((b) => `
    <tr class="border-b">
      <td class="p-2">${b.name}</td>
      <td class="p-2">${b.crates}</td>
      <td class="p-2">${b.total_kg.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
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
  // Reload when the connection comes back - the browser's own event, not
  // Boord.onOfflineChange: that also fires when a request times out and
  // flips the banner on, which used to re-fire the very request that had
  // just spent 45s timing out.
  window.addEventListener("online", () => { refreshActiveTab(); });
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

// Settings, suppliers and the dashboard's four calls all go out at once:
// nothing the dashboard shows waits on the other two (the Season preset
// reads _systemSettings only when tapped, and falls back to the cached
// copy), so serialising them cost two round-trips before any live figure.
async function refreshAppData() {
  const settings = (async () => {
    try {
      _systemSettings = await Boord.api("/api/system-settings");
      localStorage.setItem("boord_cached_settings", JSON.stringify(_systemSettings));
      updateBannerFarmName();
    } catch (e) {
      if (Boord.isNetworkError(e)) Boord.setOffline(true);
    }
  })();
  updateBannerWeather();
  await Promise.all([settings, loadSuppliers(), refreshDashboard()]);
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

document.addEventListener("DOMContentLoaded", init);
