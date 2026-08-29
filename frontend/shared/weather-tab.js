// Weather tab (historical weather, 1987-present): an interactive chart
// filterable by calendar year and by which measurements to plot, a dynamic
// legend, and PDF export. Shared between the admin app
// (/api/weather/history, JWT auth) and the Owner View
// (/api/owner-view/weather, token auth) - identical markup (same element
// IDs), same as analysis-tab.js, so this one module renders both.
//
// Years are always overlaid on a shared 1 Jan - 31 Dec x-axis, one line per
// selected year, defaulting to the most recent year on file. There used to
// be an "All Years" mode plotting one continuous line across the whole
// record instead; it was dropped once the history reached back to 1987,
// where it compressed 39 years into an unreadable smear and buried the
// year-on-year comparison this tab exists for.
const LWWeatherTab = (() => {
  let _data = null;
  let _bound = false;
  let _firstLoad = true;
  let _selectedYears = new Set();
  let _selectedMetrics = new Set();

  function bind() {
    // Same double-bind guard as analysis-tab.js: the admin app re-runs its
    // bind* helpers on every sign-in without reloading the page, so without
    // this a sign-out/sign-in cycle would stack duplicate listeners.
    if (_bound) return;
    _bound = true;

    document.getElementById("tab-weather").addEventListener("change", (e) => {
      if (e.target.classList.contains("weather-year-cb")) {
        const year = parseInt(e.target.value, 10);
        if (e.target.checked) _selectedYears.add(year); else _selectedYears.delete(year);
        if (_data) _render();
      } else if (e.target.classList.contains("weather-metric-cb")) {
        if (e.target.checked) _selectedMetrics.add(e.target.value); else _selectedMetrics.delete(e.target.value);
        if (_data) _render();
      }
    });

    // Delegated PDF export, identical mechanics to analysis-tab.js's
    // handler - LWCharts.exportPDF() needs no changes for this chart.
    document.getElementById("tab-weather").addEventListener("click", async (e) => {
      const btn = e.target.closest(".chart-pdf-btn");
      if (!btn) return;
      const target = document.getElementById(btn.dataset.target);
      if (!target) return;
      const icon = btn.querySelector("i");
      icon.className = "fa-solid fa-spinner fa-spin";
      btn.disabled = true;
      try {
        const filename = `${btn.dataset.title.replace(/[^a-zA-Z0-9]+/g, "_")}.pdf`;
        await LWCharts.exportPDF(target, { title: btn.dataset.title, filename });
      } catch (err) {
        console.error("PDF export failed:", err);
        Boord.toast("Could not create PDF");
      } finally {
        icon.className = "fa-solid fa-file-pdf";
        btn.disabled = false;
      }
    });
  }

  // fetchHistory: () => Promise<data> - each screen supplies its own call
  // (admin: Boord.api with a bearer token; owner: Boord.api with the link's key).
  async function load(fetchHistory, { onAuthError } = {}) {
    let data;
    try {
      data = await fetchHistory();
    } catch (e) {
      if (Boord.isNetworkError(e)) { Boord.setOffline(true); return; }
      if (Boord.isAuthError(e) && onAuthError) { onAuthError(e); return; }
      console.error("Weather load failed:", e);
      Boord.toast("Could not load weather data");
      return;
    }
    Boord.setOffline(false);
    _data = data;
    _rebuildFilters(data);
    const synced = document.getElementById("weatherLastSynced");
    synced.textContent = data.last_synced
      ? `Weather data current to ${_formatSynced(data.last_synced)}`
      : "No weather data yet";
    // Hours that were downloaded for somewhere other than where the farm now
    // says it is - which only happens if the GPS was corrected after weather
    // had already been fetched. Normally absent entirely. Worth saying here
    // because this chart is where somebody would notice the numbers looking
    // wrong, and nothing else on the screen could explain why.
    if (data.hours_elsewhere) {
      const note = document.createElement("div");
      note.className = "text-amber-700 text-xs mt-1";
      note.textContent = `${data.hours_elsewhere.toLocaleString()} hours on this chart were `
        + `downloaded for a different location, before the farm's GPS was corrected. They are `
        + `replaced the next time the weather history is refreshed on the server PC.`;
      synced.appendChild(note);
    }
    _render();
  }

  // WeatherHistory.timestamp (and therefore last_synced) is naive LOCAL
  // farm time, not UTC (see models.py) - deliberately NOT run through
  // Boord.parseServerDate/new Date(), which would treat it as UTC and shift
  // it. The digits are already the farm's own wall clock, so just format
  // them directly.
  function _formatSynced(iso) {
    const [datePart, timePart] = iso.split("T");
    return `${datePart} ${(timePart || "").slice(0, 5)}`;
  }

  function _rebuildFilters(data) {
    if (_firstLoad) {
      // The latest year actually on file, which is not always
      // data.current_year - a new calendar year has no weather rows until
      // the first sync of the year lands, and defaulting to an empty year
      // would open the tab on a blank chart.
      _selectedYears = new Set([data.years.length ? data.years[data.years.length - 1] : data.current_year]);
      _selectedMetrics = new Set(["temp_c"]);
    }

    // Rebuilt only when the set of years/metrics actually changes (mirrors
    // analysis-tab.js's renderVarietyYield() pattern), preserving whichever
    // of the current selection still exists.
    const yearsSig = data.years.join("|");
    const yearEl = document.getElementById("weatherYearFilter");
    if (yearEl.dataset.years !== yearsSig) {
      yearEl.innerHTML = data.years.map((y) => `
        <label class="inline-flex items-center gap-1.5 text-sm mr-3">
          <input type="checkbox" class="weather-year-cb" value="${y}" ${_selectedYears.has(y) ? "checked" : ""}>
          ${y}${y === data.current_year ? " (current)" : ""}
        </label>`).join("");
      yearEl.dataset.years = yearsSig;
    }

    const metricsSig = data.metrics.map((m) => m.key).join("|");
    const metricEl = document.getElementById("weatherMetricFilter");
    if (metricEl.dataset.metrics !== metricsSig) {
      metricEl.innerHTML = data.metrics.map((m) => `
        <label class="inline-flex items-center gap-1.5 text-sm mr-3">
          <input type="checkbox" class="weather-metric-cb" value="${m.key}" ${_selectedMetrics.has(m.key) ? "checked" : ""}>
          ${m.label}
        </label>`).join("");
      metricEl.dataset.metrics = metricsSig;
    }
    _firstLoad = false;
  }

  // day_of_year -> real date, using a fixed non-leap reference year purely
  // for formatting (1 Jan - 31 Dec calendar-year anchor, matching the
  // backend's build_weather_history() - NOT analysis-tab.js's Aug-anchored
  // season_day, a different concept for harvest data).
  function _dayOfYearToDate(dayOfYear) {
    return new Date(2001, 0, dayOfYear); // month 0 = January (0-indexed)
  }
  function _dayOfYearLabel(dayOfYear) {
    return _dayOfYearToDate(dayOfYear).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  // Color scheme, two dimensions: hue says WHICH measurement, shade says
  // WHICH year. The eight hue families below are ordered so that any two
  // neighbours in the list sit far apart on the color wheel - the metrics
  // are handed hues in the order they appear in the filter row, so the
  // pairs people actually tick together (Temperature + Humidity, say)
  // come out red vs blue rather than the old red vs orange.
  //
  // Each family carries four shades, light to dark, so several years of
  // the same measurement stay apart from each other as well: within one
  // measurement the oldest selected year gets the lightest shade and the
  // most recent the darkest. With only one measurement on the chart there
  // is no hue conflict to worry about, so each year gets its own hue
  // instead - the widest possible spread for the common "compare years"
  // view. The current, still-in-progress year is additionally drawn with a
  // thicker line (`emphasize`) in every mode, so it stands out whichever
  // way the colors fall.
  const HUE_FAMILIES = [
    ["#fca5a5", "#f87171", "#dc2626", "#991b1b"], // red
    ["#bfdbfe", "#60a5fa", "#2563eb", "#1e40af"], // blue
    ["#bbf7d0", "#4ade80", "#16a34a", "#166534"], // green
    ["#fde68a", "#fbbf24", "#d97706", "#92400e"], // amber
    ["#ddd6fe", "#a78bfa", "#7c3aed", "#5b21b6"], // violet
    ["#99f6e4", "#2dd4bf", "#0d9488", "#115e59"], // teal
    ["#fbcfe8", "#f472b6", "#db2777", "#9d174d"], // magenta
    ["#cbd5e1", "#94a3b8", "#64748b", "#334155"], // slate
  ];
  const SHADES = HUE_FAMILIES[0].length;
  // Order the hue-per-year mode walks the shades in once it has been
  // round the eight families: strongest first, so the first eight years -
  // all anyone normally ticks - are drawn in full-strength color rather
  // than in the pale end of the ramp.
  const SHADE_CYCLE = [2, 0, 3, 1];

  // Oldest selected year -> lightest shade, most recent -> darkest, spread
  // across the whole ramp however many years are on the chart.
  function _shadeIndex(rank, count) {
    if (count <= 1) return SHADES - 1;
    return Math.round((rank / (count - 1)) * (SHADES - 1));
  }

  function _render() {
    const chartEl = document.getElementById("weatherChart");
    const legendEl = document.getElementById("weatherLegend");

    if (!_selectedMetrics.size) {
      chartEl.innerHTML = `<div class="text-sm text-slate-400 p-8 text-center">Pick at least one measurement above</div>`;
      LWCharts.legend(legendEl, []);
      return;
    }

    const metricsByKey = {};
    _data.metrics.forEach((m) => { metricsByKey[m.key] = m; });
    const metricKeys = _data.metrics.map((m) => m.key).filter((k) => _selectedMetrics.has(k));
    // Hue index comes from the metric's fixed position in _data.metrics,
    // not its position among the ticked ones, so a measurement keeps the
    // same color as other measurements are ticked on and off.
    const hueOfMetric = {};
    _data.metrics.forEach((m, i) => { hueOfMetric[m.key] = i; });

    const years = _data.years.filter((y) => _selectedYears.has(y));
    // One measurement on the chart: hue per year. Several: hue per
    // measurement, shade per year. Past eight years in the single-metric
    // case the hues start over at the next shade in SHADE_CYCLE, so even
    // ticking the whole 1987-present record never repeats a color.
    const hueByYear = metricKeys.length === 1;
    const series = [];
    metricKeys.forEach((key) => {
      const m = metricsByKey[key];
      years.forEach((year, yi) => {
        const points = _data.points
          .filter((p) => p.year === year && p[key] != null)
          .map((p) => ({ x: p.day_of_year, y: p[key] }));
        if (!points.length) return;
        const isCurrent = year === _data.current_year;
        const family = HUE_FAMILIES[(hueByYear ? yi : hueOfMetric[key]) % HUE_FAMILIES.length];
        const shade = hueByYear
          ? SHADE_CYCLE[Math.floor(yi / HUE_FAMILIES.length) % SHADES]
          : _shadeIndex(yi, years.length);
        const color = family[shade];
        series.push({
          label: `${m.label} — ${year}`, color,
          unit: m.unit, decimals: m.decimals, points, emphasize: isCurrent,
        });
      });
    });

    if (!series.length) {
      chartEl.innerHTML = `<div class="text-sm text-slate-400 p-8 text-center">Pick at least one year above</div>`;
      LWCharts.legend(legendEl, []);
      return;
    }

    const pointEvery = Math.max(1, Math.ceil(Math.max(1, ...series.map((s) => s.points.length)) / 150));

    LWCharts.normalizedLineChart(chartEl, { series, xLabel: _dayOfYearLabel, xMin: 1, pointEvery });
    LWCharts.legend(legendEl, series.map((s) => ({ label: s.label, color: s.color })));
  }

  return { bind, load };
})();
