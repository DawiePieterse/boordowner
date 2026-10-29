// Shared API helpers for the Boord Owner app. Vendored from Boord's
// frontend/shared/api.js and trimmed to what this app uses. Boord's token
// handling did not come across: this app has no sign-in, so api() sends no
// credentials and there is nothing stored per-user.
// Everything is served from the same origin as the backend, so API_BASE is relative.
const API_BASE = "";

const Boord = {
  // Bump on every deploy that touches frontend code. Shown in the header so
  // it's obvious whether a device's cached copy is up to date.
  VERSION: "1.10.1",

  // A device whose WiFi is up but that cannot actually reach the farm server
  // gets no error from fetch() - the request just hangs until the OS gives up,
  // which can be minutes. Every request is therefore given a deadline, and a
  // blown deadline is reported as a normal network failure so callers fall
  // back to cached data instead of waiting.
  NETWORK_TIMEOUT_MS: 8000,

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
  // How long a tab's last successful load is reused before a tab tap
  // refetches it. Analysis and Risk are the heaviest calls the server
  // makes (a full-season scan, every reference-season weather hour) and
  // their figures change at most a few times an hour, so flicking between
  // tabs must not re-run them. Pull-to-refresh and coming back online
  // always bypass this.
  TAB_FRESH_MS: 5 * 60 * 1000,

  isFresh(loadedAt) {
    return !!loadedAt && Date.now() - loadedAt < Boord.TAB_FRESH_MS;
  },

  // Runs fetchFn and keeps its result under `key`; when the server can't be
  // reached, hands back the last result instead. Resolves to
  // {data, cached, at} - `cached` true means the figures are the saved
  // ones, from `at`. Any other failure (a 500, a bad response) rejects as
  // usual, and so does a network failure with nothing saved. The
  // analytical tabs change at most daily, so an owner away from the farm
  // still sees the last known picture rather than a blank tab.
  async cachedLoad(key, fetchFn) {
    try {
      const data = await fetchFn();
      try {
        localStorage.setItem(key, JSON.stringify({ at: Date.now(), data }));
      } catch (e) { /* out of quota - the live figures still render */ }
      return { data, cached: false, at: Date.now() };
    } catch (e) {
      if (Boord.isNetworkError(e)) {
        const saved = Boord.getCachedJSON(key);
        if (saved && saved.data) return { data: saved.data, cached: true, at: saved.at };
      }
      throw e;
    }
  },

  // The load scaffold the Analysis, Risk and Weather tabs share: runs
  // fetchFn through cachedLoad under `key` and keeps the offline banner
  // truthful. `noun` names the figures in the banner ("analysis", "risk
  // figures", "weather"); `label` names the tab in the console and the
  // toast ("Analysis" -> "Could not load analysis data"). Resolves to
  // cachedLoad's {data, cached, at}, or null when there is nothing to show
  // - the banner or toast has already said why.
  async loadTab({ key, fetchFn, noun, label }) {
    let result;
    try {
      result = await Boord.cachedLoad(key, fetchFn);
    } catch (e) {
      if (Boord.isNetworkError(e)) {
        // api() has already raised the banner.
        Boord.setOfflineBannerText(`Offline - no saved ${noun} on this device yet`);
        return null;
      }
      console.error(`${label} load failed:`, e);
      Boord.toast(`Could not load ${label.toLowerCase()} data`);
      return null;
    }
    // Saved figures came back only because the server couldn't be reached.
    Boord.setOffline(result.cached);
    if (result.cached) {
      Boord.setOfflineBannerText(`Offline - showing ${noun} from ${Boord.describeAge(result.at)}`);
    }
    return result;
  },

  // "moments ago" / "12 min ago" / "3 hours ago" / a full date-time.
  describeAge(at) {
    const mins = Math.round((Date.now() - at) / 60000);
    if (mins < 1) return "moments ago";
    if (mins < 60) return `${mins} min ago`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    return Boord.fmtDateTime(new Date(at));
  },

  // Updates the offline banner's text in place. offlineBanner() re-registers
  // its online/offline listeners on every call, so it must not be called
  // again just to change the wording.
  setOfflineBannerText(message) {
    const el = document.getElementById("boord-offline-banner");
    if (el) el.innerHTML = `<i class="fa-solid fa-wifi"></i> ${message}`;
  },

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

  // Anything from the server that is interpolated into innerHTML goes through
  // here - supplier and worker names, slip numbers, block names, estimate
  // notes, error text. They are typed by people in Boord (or quoted back from
  // an imported spreadsheet), and a stray "<" or quote otherwise becomes
  // markup: at best it silently eats the rest of the line, at worst it runs.
  // Safe inside attribute values as well as text. Copied from Boord's
  // shared/api.js - keep the two the same.
  escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  },

  // The human-readable part of a rejected request's body. FastAPI sends
  // {"detail": "..."} for the errors this app raises itself, but a request
  // that fails validation (a 422) gets {"detail": [{"loc": [...], "msg":
  // "...", ...}, ...]} instead - and handing that list back as-is put a lump
  // of raw JSON in a toast. The first problem's message is enough for the
  // owner to act on; anything else JSON-shaped is summarised rather than
  // shown, because nobody on a phone can do anything with braces.
  _errorDetail(text) {
    let parsed;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      return text;   // not JSON - the body text is the detail
    }
    const detail = parsed && parsed.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail.length) {
      const first = detail[0];
      if (first && typeof first.msg === "string") return first.msg;
      if (typeof first === "string") return first;
    }
    return "The server could not accept that request";
  },

  async api(path, { method = "GET", body, isForm = false, timeoutMs } = {}) {
    const headers = {};
    let payload = body;
    if (body && !isForm) {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(body);
    }
    // Offline bookkeeping lives here rather than at every call site: a
    // request that never got an answer means the server is unreachable,
    // and ANY answer - a 500 included - means it is reachable. Screens only
    // set the flag themselves when it comes from somewhere else (saved
    // figures served by cachedLoad).
    let res;
    try {
      res = await Boord._fetchWithTimeout(
        `${API_BASE}${path}`, { method, headers, body: payload }, timeoutMs);
    } catch (e) {
      if (Boord.isNetworkError(e)) Boord.setOffline(true);
      throw e;
    }
    Boord.setOffline(false);
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      // The message keeps its "<status> <body>" shape; status and detail
      // ride along so callers need not parse it back out. detail is
      // FastAPI's JSON "detail" when there is a string one, else the body.
      const err = new Error(`${res.status} ${text}`);
      err.status = res.status;
      err.detail = Boord._errorDetail(text);
      throw err;
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

  // Every date, time and kg figure the owner reads is printed through these,
  // in South African English, so the same screen reads the same on every
  // device: left to the browser's own locale, a laptop set to US English
  // showed "10/18/2026, 2:05:00 PM" beside a phone's "2026/10/18 14:05", and
  // the owner had to stop and work out which was which. Times are 24-hour
  // with no seconds - that is how the farm reads a clock, and the seconds
  // only made the header twitch. These are for display only: values sent to
  // the server or put in a form input keep their own fixed formats.
  LOCALE: "en-ZA",

  // "14:05"
  fmtTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleTimeString(Boord.LOCALE, { hour: "2-digit", minute: "2-digit", hour12: false }) : fallback;
  },

  // "18 Oct 2026" by default; `opts` replaces the Intl date fields, e.g.
  // { day: "numeric", month: "short" } for "18 Oct". A bare "YYYY-MM-DD" is
  // read as midday local time, not as midnight UTC, so it never prints as the
  // day before on a device west of Greenwich.
  fmtDate(value, opts, fallback = "") {
    const isDay = typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value.trim());
    const d = isDay ? new Date(`${value.trim()}T12:00:00`) : Boord.parseServerDate(value);
    if (!d || isNaN(d.getTime())) return fallback;
    return d.toLocaleDateString(Boord.LOCALE, opts || { day: "numeric", month: "short", year: "numeric" });
  },

  // "18 Oct 2026, 14:05"
  fmtDateTime(value, fallback = "") {
    const d = Boord.parseServerDate(value);
    return d ? d.toLocaleString(Boord.LOCALE, {
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false,
    }) : fallback;
  },

  // Whole kilograms with thousands separators - "12 346 kg". Per-crate
  // averages keep their one decimal and do not come through here.
  fmtKg(v, maximumFractionDigits = 0) {
    if (v === null || v === undefined || isNaN(Number(v))) return "-";
    return `${Number(v).toLocaleString(Boord.LOCALE, { maximumFractionDigits })} kg`;
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
  // offline. Wired to the browser's online/offline events, and api() also
  // calls Boord.setOffline(true/false) from every request's outcome:
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

  // Screens can set this to react to offline flips (e.g. recolor a status pill).
  onOfflineChange: null,

  // A status region, so a screen reader announces the message without
  // moving focus. Long messages stay up longer: a fixed 2.2s was gone before
  // anyone could read a server's "Not saved: ..." reason through to the end.
  // 40ms a character is a comfortable reading pace; 6s is the cap so a toast
  // never sits over the bottom of the screen for long.
  toast(message) {
    let el = document.getElementById("boord-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "boord-toast";
      el.className = "toast";
      el.setAttribute("role", "status");
      el.setAttribute("aria-live", "polite");
      document.body.appendChild(el);
    }
    const text = String(message ?? "");
    el.textContent = text;
    el.classList.add("show");
    clearTimeout(el._timer);
    el._timer = setTimeout(() => el.classList.remove("show"),
                           Math.min(6000, 2200 + 40 * text.length));
  },

  // Runs a screen's start-up and, if it throws, says what is wrong in words
  // rather than leaving a white page. Copied from Boord's shared/api.js.
  //
  // init() opens by reaching for elements out of index.html. If a device ends
  // up with one release's HTML and another's JavaScript - which a service
  // worker CAN do, by revalidating the two files at different moments - that
  // lookup returns null, init() throws, and the screen paints nothing at all.
  // That happened to Boord's Admin app on a farm server on 2026-09-04, and
  // from the outside it looked exactly like the server was down.
  //
  // A blank screen is the worst possible presentation of this, because it
  // gives the owner nothing to act on. So: catch it, say what it is, and
  // offer the one button that actually fixes it.
  boot(init) {
    try {
      Promise.resolve(init()).catch(Boord._bootFailed);
    } catch (e) {
      Boord._bootFailed(e);
    }
  },

  _bootFailed(error) {
    console.error("Boord Owner: this screen failed to start", error);
    // Built with inline styles and no classes on purpose. The stylesheet is
    // one of the files that may be stale or missing, so this has to look
    // right without it.
    const panel = document.createElement("div");
    panel.setAttribute("style", [
      "position:fixed", "inset:0", "z-index:99999",
      "background:#f1f5f9", "color:#0f172a",
      "font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif",
      "display:flex", "align-items:center", "justify-content:center",
      "padding:24px", "text-align:center",
    ].join(";"));
    panel.innerHTML = `
      <div style="max-width:26rem">
        <div style="font-size:20px;font-weight:700;margin-bottom:12px">
          This device is running an old copy
        </div>
        <div style="margin-bottom:20px">
          The app on this phone or PC does not match the farm server, so this
          screen could not start. Nothing has been lost, and nothing is wrong
          with the server - this device just needs to fetch the new version.
        </div>
        <button id="boord-boot-reload" style="
          background:#0A2F6B;color:#fff;border:0;border-radius:10px;
          padding:14px 22px;font-size:16px;font-weight:600;cursor:pointer">
          Update this device
        </button>
        <div style="margin-top:16px;font-size:13px;color:#475569">
          If this keeps happening, close the app completely and open it again.
        </div>
      </div>`;
    document.body.appendChild(panel);
    panel.querySelector("#boord-boot-reload")
      .addEventListener("click", () => Boord._reinstall());
  },

  // Throw away everything this device has cached for the app and load it
  // fresh. Deliberately more than location.reload(): a reload is answered by
  // the very service worker whose cache is the problem, which is why "just
  // refresh the page" does not clear this and people end up clearing site
  // data by hand.
  async _reinstall() {
    try {
      if ("serviceWorker" in navigator) {
        const registrations = await navigator.serviceWorker.getRegistrations();
        await Promise.all(registrations.map((r) => r.unregister()));
      }
      if (window.caches) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch (e) {
      // Nothing useful to do - reload anyway, it may be enough on its own.
      console.error("Boord Owner: could not clear the cache", e);
    }
    location.reload();
  },

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
};
