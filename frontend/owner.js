// Boord Owner: read-only dashboard. Access is a per-user account - the
// session JWT lives in localStorage under "boord_owner_token" (see
// shared/api.js). Managers get an extra Users tab.

let _systemSettings = null;
let _me = null;  // { username, is_manager, must_change_password } from /api/owner-auth/me

function _show(id) {
  ["loginScreen", "passwordSetupScreen", "app"].forEach((s) => {
    document.getElementById(s).classList.toggle("hidden", s !== id);
  });
  // Modals live outside #app, so a screen change has to close them itself -
  // otherwise a revoked session mid-dialog leaves the login form behind a
  // dimmed overlay that nothing can dismiss.
  ["addUserModal", "oneTimePasswordModal"].forEach((m) => {
    document.getElementById(m).classList.add("hidden");
  });
}

function showLogin() { _show("loginScreen"); }
function showPasswordSetup() { _show("passwordSetupScreen"); }

// A real HTTP 401/403 on an authenticated call means the session is no
// longer good (expired, password changed elsewhere, account disabled). Drop
// the token and send the user back to sign in. A NETWORK failure is not this
// - callers handle that separately as "offline".
function sessionExpired() {
  Boord.clearToken();
  _me = null;
  Boord.toast("Session ended - sign in again");
  showLogin();
}

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
    const w = await Boord.api("/api/weather/current", { auth: true });
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

// Shows one tab and loads it. Also used on sign-in to land everyone on the
// Dashboard: without that, signing out while on the Users tab and back in as
// somebody who is not a manager left the Users panel on screen, its button
// hidden but its content still the visible one.
function activateTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.add("hidden"));
  const panel = document.getElementById(`tab-${name}`);
  if (panel) panel.classList.remove("hidden");
  if (name === "analysis") loadAnalysis();
  else if (name === "weather") loadWeather();
  else if (name === "risk") loadRisk();
  else if (name === "users") loadUsers();
}

// The one download this app offers: every harvest figure on file, 1987 to
// the current season, in one workbook. It needs the bearer token, so it
// cannot be a plain <a href> - fetch it, then hand the blob to the browser.
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
                                   { auth: true, timeoutMs: 60000 });
      Boord.downloadBlob(blob, "Historical_Harvest_Data.xlsx");
    } catch (e) {
      if (Boord.isAuthError(e)) { sessionExpired(); return; }
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
    () => Boord.api("/api/analysis/summary", { auth: true }),
    { onAuthError: sessionExpired },
  );
}

async function loadWeather() {
  await LWWeatherTab.load(
    () => Boord.api("/api/weather/history", { auth: true }),
    { onAuthError: sessionExpired },
  );
}

async function loadRisk() {
  await LWRiskTab.load(
    () => Boord.api("/api/risk/summary", { auth: true }),
    // This call does real work the default 8s network timeout isn't built for.
    () => Boord.api("/api/risk/forecast", { auth: true, timeoutMs: 45000 }),
    { onAuthError: sessionExpired },
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
    const suppliers = await Boord.api("/api/suppliers", { auth: true });
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
      Boord.api(`/api/lots/pending?${qs}`, { auth: true }),
      Boord.api(`/api/lots/in-transit?${qs}`, { auth: true }),
      Boord.api(`/api/lots/received?${qs}`, { auth: true }),
      Boord.api(`/api/dashboard/summary?${qs}`, { auth: true }),
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
    if (Boord.isAuthError(e)) sessionExpired();
    else Boord.toast("Could not load the dashboard");
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

// --------------------------------------------------------------------------
// Users tab (managers only)
// --------------------------------------------------------------------------
function showOneTimePassword(username, password) {
  document.getElementById("otpUsername").textContent = username;
  document.getElementById("otpValue").textContent = password;
  document.getElementById("oneTimePasswordModal").classList.remove("hidden");
}

function renderUsers(users) {
  const rows = document.getElementById("usersRows");
  rows.innerHTML = users.map((u) => {
    const isSelf = _me && u.username === _me.username;
    const role = u.is_manager ? "Manager" : "Viewer";
    const status = u.disabled ? "Disabled"
      : u.must_change_password ? "Password not set" : "Active";
    const btn = (act, label, cls) =>
      `<button data-act="${act}" data-id="${u.id}" data-user="${u.username}" class="${cls} text-xs px-2 py-1 rounded">${label}</button>`;
    const actions = isSelf ? '<span class="text-xs text-slate-400">you</span>' : [
      btn("reset", "Reset password", "bg-slate-200"),
      btn(u.is_manager ? "demote" : "promote", u.is_manager ? "Make viewer" : "Make manager", "bg-slate-200"),
      btn(u.disabled ? "enable" : "disable", u.disabled ? "Enable" : "Disable", u.disabled ? "bg-green-100 text-green-800" : "bg-amber-100 text-amber-800"),
      btn("delete", "Delete", "bg-red-100 text-red-800"),
    ].join(" ");
    return `<tr class="border-b">
      <td class="p-2 font-medium">${u.username}</td>
      <td class="p-2">${role}</td>
      <td class="p-2">${status}</td>
      <td class="p-2 text-right space-x-1">${actions}</td>
    </tr>`;
  }).join("") || `<tr><td class="p-2 text-slate-400" colspan="4">No users</td></tr>`;
}

async function loadUsers() {
  if (!_me || !_me.is_manager) return;
  try {
    renderUsers(await Boord.api("/api/owner-users", { auth: true }));
  } catch (e) {
    if (Boord.isAuthError(e)) return sessionExpired();
    Boord.toast("Could not load users");
  }
}

async function onUsersAction(act, id, username) {
  try {
    if (act === "reset") {
      const r = await Boord.api(`/api/owner-users/${id}/reset-password`, { method: "POST", auth: true });
      showOneTimePassword(username, r.initial_password);
    } else if (act === "promote" || act === "demote") {
      await Boord.api(`/api/owner-users/${id}`, { method: "PATCH", auth: true, body: { is_manager: act === "promote" } });
    } else if (act === "disable" || act === "enable") {
      await Boord.api(`/api/owner-users/${id}`, { method: "PATCH", auth: true, body: { disabled: act === "disable" } });
    } else if (act === "delete") {
      if (!confirm(`Delete ${username}? They will not be able to sign in.`)) return;
      await Boord.api(`/api/owner-users/${id}`, { method: "DELETE", auth: true });
    }
    await loadUsers();
  } catch (e) {
    // Any 401/403 on an authenticated call means this session is no longer
    // good - the token was revoked, or this account is no longer a manager.
    // Everything else (the last-manager guard's 400, say) is a message to read.
    if (Boord.isAuthError(e)) return sessionExpired();
    Boord.toast(Boord.errorDetail(e, "That change was not allowed"));
  }
}

function openAddUser() {
  document.getElementById("addUserName").value = "";
  document.getElementById("addUserManager").checked = false;
  document.getElementById("addUserError").classList.add("hidden");
  document.getElementById("addUserModal").classList.remove("hidden");
  document.getElementById("addUserName").focus();
}

async function submitAddUser(e) {
  e.preventDefault();
  const err = document.getElementById("addUserError");
  err.classList.add("hidden");
  const username = document.getElementById("addUserName").value.trim();
  const isManager = document.getElementById("addUserManager").checked;
  if (!username) return;
  try {
    const r = await Boord.api("/api/owner-users", { method: "POST", auth: true, body: { username, is_manager: isManager } });
    document.getElementById("addUserModal").classList.add("hidden");
    showOneTimePassword(username, r.initial_password);
    await loadUsers();
  } catch (ex) {
    if (Boord.isAuthError(ex)) {
      document.getElementById("addUserModal").classList.add("hidden");
      return sessionExpired();
    }
    err.textContent = Boord.errorDetail(ex, "Could not add that user");
    err.classList.remove("hidden");
  }
}

// --------------------------------------------------------------------------
// Auth wiring + routing
// --------------------------------------------------------------------------
function bindAuthForms() {
  document.getElementById("loginForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("loginError");
    err.classList.add("hidden");
    try {
      const data = await Boord.login(
        document.getElementById("loginUsername").value.trim(),
        document.getElementById("loginPassword").value,
      );
      document.getElementById("loginPassword").value = "";
      if (data.must_change_password) { showPasswordSetup(); return; }
      await route();
    } catch (ex) {
      err.textContent = Boord.isNetworkError(ex)
        ? "Can't reach the server. Check the connection and try again."
        : Boord.errorDetail(ex, "Invalid username or password");
      err.classList.remove("hidden");
    }
  });

  document.getElementById("passwordSetupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.getElementById("passwordSetupError");
    err.classList.add("hidden");
    const pw = document.getElementById("newPassword").value;
    const confirmPw = document.getElementById("newPasswordConfirm").value;
    if (pw !== confirmPw) {
      err.textContent = "The two passwords don't match.";
      err.classList.remove("hidden");
      return;
    }
    try {
      const r = await Boord.api("/api/owner-auth/change-password", { method: "POST", auth: true, body: { new_password: pw } });
      Boord.setToken(r.access_token);
      document.getElementById("newPassword").value = "";
      document.getElementById("newPasswordConfirm").value = "";
      await route();
    } catch (ex) {
      // The password rules answer 400; a 401 here means the token that got
      // us to this screen has since been revoked.
      if (Boord.isAuthError(ex)) return sessionExpired();
      err.textContent = Boord.errorDetail(ex, "Could not set that password");
      err.classList.remove("hidden");
    }
  });

  document.getElementById("logoutBtn").addEventListener("click", () => {
    Boord.clearToken();
    _me = null;
    showLogin();
  });

  document.getElementById("oneTimePasswordModal").addEventListener("click", (e) => {
    if (e.target.id === "oneTimePasswordModal" || e.target.id === "otpCloseBtn") {
      document.getElementById("oneTimePasswordModal").classList.add("hidden");
    }
  });
  document.getElementById("addUserBtn").addEventListener("click", openAddUser);
  document.getElementById("addUserForm").addEventListener("submit", submitAddUser);
  document.getElementById("addUserCancel").addEventListener("click", () => {
    document.getElementById("addUserModal").classList.add("hidden");
  });
  document.getElementById("usersRows").addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-act]");
    if (btn) onUsersAction(btn.dataset.act, btn.dataset.id, btn.dataset.user);
  });
}

// Decides which screen to show based on the stored token. Called on load,
// after sign-in, and after the first-login password change.
async function route() {
  if (!Boord.getToken()) { showLogin(); return; }
  try {
    _me = await Boord.api("/api/owner-auth/me", { auth: true });
  } catch (e) {
    if (Boord.isAuthError(e)) { sessionExpired(); return; }
    // Network error: we have a token but can't check it. Go into the app
    // anyway so the offline dashboard cache is usable; the first real call
    // that gets a 401 will bounce back to sign-in.
    _me = _me || { username: "", is_manager: false, must_change_password: false };
  }
  if (_me.must_change_password) { showPasswordSetup(); return; }
  enterApp();
}

let _appBound = false;

function enterApp() {
  document.getElementById("headerUser").textContent = _me.username || "";
  document.getElementById("usersTabBtn").style.display = _me.is_manager ? "" : "none";
  if (!_me.is_manager) document.getElementById("usersRows").innerHTML = "";
  _show("app");

  if (!_appBound) {
    _appBound = true;
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
  }

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
    _systemSettings = await Boord.api("/api/system-settings", { auth: true });
    localStorage.setItem("boord_cached_settings", JSON.stringify(_systemSettings));
    updateBannerFarmName();
  } catch (e) {
    if (Boord.isAuthError(e)) return sessionExpired();
    if (Boord.isNetworkError(e)) Boord.setOffline(true);
  }
  updateBannerWeather();
  await loadSuppliers();
  await refreshDashboard();
}

async function init() {
  bindAuthForms();
  await route();
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("service-worker.js").catch(() => {});
}

document.addEventListener("DOMContentLoaded", init);
