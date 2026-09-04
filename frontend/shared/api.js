// Shared API helpers for the Boord Owner app. Vendored from Boord's
// frontend/shared/api.js and trimmed to what this app uses. Boord's token
// handling did not come across: this app has no sign-in, so api() sends no
// credentials and there is nothing stored per-user.
// Everything is served from the same origin as the backend, so API_BASE is relative.
const API_BASE = "";

const Boord = {
  // Bump on every deploy that touches frontend code. Shown in the header so
  // it's obvious whether a device's cached copy is up to date.
  VERSION: "1.5.1",

  getLastReceivedBy() { return localStorage.getItem("boord_last_received_by") || ""; },
  setLastReceivedBy(name) { localStorage.setItem("boord_last_received_by", name); },

  // A device whose WiFi is up but that cannot actually reach the farm server
  // gets no error from fetch() - the request just hangs until the OS gives up,
  // which can be minutes. Every request is therefore given a deadline, and a
  // blown deadline is reported as a normal network failure so callers fall
  // back to cached data instead of waiting.
  NETWORK_TIMEOUT_MS: 8000,
  // File transfers are legitimately slow; they opt into a longer deadline.
  UPLOAD_TIMEOUT_MS: 120000,

  async _fetchWithTimeout(url, options = {}, timeoutMs) {
    const limit = timeoutMs || Boord.NETWORK_TIMEOUT_MS;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), limit);
    try {
      return await fetch(url, { ...options, signal: controller.signal });
    } finally {
      clearTimeout(timer);
    }
  },

  // True when a request failed because the server could not be reached
  // (offline, unreachable, or timed out) rather than because it answered
  // with an error. Screens use this to tell "no connection" apart from
  // "rejected" - e.g. the device setup screen must not wipe a saved device
  // id just because the server is unreachable.
  isNetworkError(e) {
    return e instanceof TypeError || (!!e && (e.name === "AbortError" || e.name === "TimeoutError"));
  },

  // Reads a cached JSON blob, tolerating a missing or corrupted entry.
  getCachedJSON(key) {
    try {
      const raw = localStorage.getItem(key);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      localStorage.removeItem(key);
      return null;
    }
  },

  async api(path, { method = "GET", body, isForm = false, timeoutMs } = {}) {
    const headers = {};
    let payload = body;
    if (body && !isForm) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    const res = await Boord._fetchWithTimeout(
      `${API_BASE}${path}`, { method, headers, body: payload }, timeoutMs);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`${res.status} ${text}`);
    }
    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) return res.json();
    return res.blob();
  },

  // The server records every timestamp in UTC, but SQLite hands them back
  // without a timezone marker, so they reach the browser looking like
  // "2026-08-08T13:46:21". JavaScript reads a bare date-time string as LOCAL
  // time, which meant every screen printed UTC digits as if they were farm
  // time (two hours slow in SAST). parseServerDate pins a naive string to UTC
  // first; the fmt* helpers then render it in the device's own timezone.
  // Always format server timestamps through these - never new Date(x) directly.
  parseServerDate(value) {
    if (value === null || value === undefined || value === "") return null;
    if (value instanceof Date) return isNaN(value.getTime()) ? null : value;
    let s = String(value).trim();
    // A bare "YYYY-MM-DD" is a calendar date, not an instant, so it is left
    // as-is; only strings carrying a time-of-day need the UTC marker.
    if (/\d{1,2}:\d{2}/.test(s)) {
      s = s.replace(" ", "T");
      if (!/(Z|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
    }
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  },

  fmtDateTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleString() : fallback;
  },

  fmtTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleTimeString() : fallback;
  },

  fmtDate(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleDateString() : fallback;
  },

  // "Today" as the farm sees it, formatted for a date input. toISOString()
  // would give the UTC date, which is still yesterday between midnight and
  // 02:00 local - early enough to matter once picking starts before dawn.
  localDateStr(d = new Date()) {
    const pad = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  },

  // Maps backend/weather.py's fixed condition strings to a Font Awesome
  // icon class - update both places together if a new condition is added.
  weatherIcon(condition) {
    const icons = {
      "Clear": "fa-sun",
      "Partly Cloudy": "fa-cloud-sun",
      "Overcast": "fa-cloud",
      "Cloudy": "fa-cloud",
      "Foggy": "fa-smog",
      "Drizzle": "fa-cloud-rain",
      "Rain": "fa-cloud-rain",
      "Heavy Rain": "fa-cloud-showers-heavy",
      "Showers": "fa-cloud-rain",
      "Heavy Showers": "fa-cloud-showers-heavy",
      "Snow": "fa-snowflake",
      "Heavy Snow": "fa-snowflake",
      "Storm": "fa-bolt",
    };
    return icons[condition] || "fa-cloud";
  },

  // Slim amber banner pinned under the header telling the user the screen is
  // offline. Wired to the browser's online/offline events, but screens should
  // ALSO call Boord.setOffline(true/false) from their own request results:
  // navigator.onLine only reflects the radio, not whether the farm server is
  // actually reachable (WiFi up + server unreachable is the common case).
  offlineBanner(message) {
    let el = document.getElementById("boord-offline-banner");
    if (!el) {
      el = document.createElement("div");
      el.id = "boord-offline-banner";
      el.className = "offline-banner hidden";
      const header = document.querySelector(".boord-header");
      if (header && header.parentNode) header.parentNode.insertBefore(el, header.nextSibling);
      else document.body.prepend(el);
    }
    el.innerHTML = `<i class="fa-solid fa-wifi"></i> ${message}`;
    window.addEventListener("offline", () => Boord.setOffline(true));
    window.addEventListener("online", () => Boord.setOffline(false));
    if (!navigator.onLine) Boord._offline = true;
    // Reflect state already set by requests that ran before this call.
    el.classList.toggle("hidden", !Boord._offline);
  },

  setOffline(isOffline) {
    const val = !!isOffline;
    if (Boord._offline === val) return; // only react to actual flips
    Boord._offline = val;
    const el = document.getElementById("boord-offline-banner");
    if (el) el.classList.toggle("hidden", !val);
    if (typeof Boord.onOfflineChange === "function") Boord.onOfflineChange(val);
  },

  isOffline() { return !!Boord._offline; },

  // Screens can set this to react to offline flips (e.g. recolor a status pill).
  onOfflineChange: null,

  toast(message) {
    let el = document.getElementById("boord-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "boord-toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = message;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"), 2200);
  },

  // Short synthesized tones (no audio files needed, works fully offline).
  // Two distinct patterns so a worker can tell them apart by ear:
  // a single beep for a QR match, a two-note rising chime for a saved crate.
  _tone(frequency, duration, delay = 0) {
    try {
      const ctx = Boord._audioCtx || (Boord._audioCtx = new (window.AudioContext || window.webkitAudioContext)());
      if (ctx.state === "suspended") ctx.resume();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = frequency;
      const startAt = ctx.currentTime + delay;
      gain.gain.setValueAtTime(0.2, startAt);
      gain.gain.exponentialRampToValueAtTime(0.001, startAt + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(startAt);
      osc.stop(startAt + duration);
    } catch (e) { /* audio isn't critical - never block the capture flow on it */ }
  },
  beepScanned() { Boord._tone(880, 0.12); },
  beepSaved() { Boord._tone(660, 0.09); Boord._tone(988, 0.14, 0.1); },

  // Wires a Today/Week/Season button group to a pair of date inputs: clicking
  // a button sets the inputs and highlights that button; editing a date input
  // directly clears the highlight since the selection no longer matches a preset.
  // Which season a date falls in, labelled by the year that season began.
  // The exact twin of Boord's Boord.seasonYearFor and of the backend's
  // timeutil.season_year_for - all three must agree or the Season preset
  // and the Analysis charts disagree about the same season.
  seasonYearFor(month, day, d = new Date()) {
    const m = parseInt(month, 10) || 1;
    const dy = parseInt(day, 10) || 1;
    const anchorThisYear = new Date(d.getFullYear(), m - 1, dy);
    return d >= anchorThisYear ? d.getFullYear() : d.getFullYear() - 1;
  },

  // `seasonAnchor()` returns {month, day} (1-based month) for the season
  // start; the Season preset then spans [anchor(year), anchor(year+1) - 1 day].
  bindDateRangePresets({ todayBtn, weekBtn, seasonBtn, startInput, endInput, seasonAnchor, onChange }) {
    const buttons = [todayBtn, weekBtn, seasonBtn];
    const setActive = (btn) => buttons.forEach((b) => b.classList.toggle("active", b === btn));
    const clearActive = () => buttons.forEach((b) => b.classList.remove("active"));

    todayBtn.addEventListener("click", () => {
      const t = Boord.localDateStr();
      startInput.value = t; endInput.value = t;
      setActive(todayBtn); if (onChange) onChange();
    });
    weekBtn.addEventListener("click", () => {
      const end = new Date(); const start = new Date();
      start.setDate(end.getDate() - 6);
      startInput.value = Boord.localDateStr(start); endInput.value = Boord.localDateStr(end);
      setActive(weekBtn); if (onChange) onChange();
    });
    seasonBtn.addEventListener("click", () => {
      const anchor = seasonAnchor ? seasonAnchor() : null;
      const month = anchor && anchor.month ? parseInt(anchor.month, 10) : 1;
      const day = anchor && anchor.day ? parseInt(anchor.day, 10) : 1;
      const year = Boord.seasonYearFor(month, day);
      const start = new Date(year, month - 1, day);
      const end = new Date(year + 1, month - 1, day);
      end.setDate(end.getDate() - 1);
      startInput.value = Boord.localDateStr(start);
      endInput.value = Boord.localDateStr(end);
      setActive(seasonBtn); if (onChange) onChange();
    });
    startInput.addEventListener("change", clearActive);
    endInput.addEventListener("change", clearActive);
    setActive(todayBtn); // screens all initialize inputs to "today"
  },

  downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  },

  uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  },
};
