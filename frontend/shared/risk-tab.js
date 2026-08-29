// Critical Season Risk Indicator (Risk tab): a transparent 0-100 score of
// how risky a season's weather looks for a poor harvest, built from the
// four weather factors that best explained this farm's own harvest
// variation across its full 1987-2025 record (see backend/routers/risk.py's
// build_risk_summary() docstring for the correlation study and its honest
// caveats, which this tab surfaces rather than hides). Prose here never
// hardcodes the reference range - it comes from the API's reference_label,
// so widening it on the backend can't leave this quoting a stale range.
// Also renders the Harvest Forecast card (build_harvest_forecast()): three
// current-season kg scenarios - Favorable/Expected/Unfavorable - built by
// blending real short-range weather forecast with historical-scenario
// ranges for whatever part of the season hasn't happened yet.
// Shared between the admin app (/api/risk/summary + /api/risk/forecast,
// JWT auth) and the Owner View (/api/owner-view/risk + /risk-forecast,
// token auth), same split as analysis-tab.js and weather-tab.js.
const LWRiskTab = (() => {
  let _data = null;
  let _bound = false;
  let _selectedYear = null;

  function bind() {
    // Same double-bind guard as analysis-tab.js/weather-tab.js: the admin
    // app re-runs its bind* helpers on every sign-in without reloading the
    // page, so without this a sign-out/sign-in cycle would stack listeners.
    if (_bound) return;
    _bound = true;

    document.getElementById("riskSeasonFilter").addEventListener("change", (e) => {
      _selectedYear = parseInt(e.target.value, 10);
      if (_data) _renderSeason();
    });

    document.getElementById("tab-risk").addEventListener("click", async (e) => {
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

  // fetchSummary/fetchForecast: () => Promise<data> - each screen supplies
  // its own calls (admin: Boord.api with a bearer token; owner: Boord.api with
  // the link's key). The forecast call is kicked off alongside the summary
  // one but handled independently: a forecast/Open-Meteo hiccup shows a
  // "currently unavailable" note in just that card rather than failing the
  // whole tab - the score header, back-test charts and methodology all
  // still render from the summary call alone.
  async function load(fetchSummary, fetchForecast, { onAuthError } = {}) {
    const forecastPromise = fetchForecast().catch((e) => {
      console.error("Forecast load failed:", e);
      return null;
    });

    let data;
    try {
      data = await fetchSummary();
    } catch (e) {
      if (Boord.isNetworkError(e)) { Boord.setOffline(true); return; }
      if (Boord.isAuthError(e) && onAuthError) { onAuthError(e); return; }
      console.error("Risk load failed:", e);
      Boord.toast("Could not load risk data");
      return;
    }
    Boord.setOffline(false);
    _data = data;
    if (_selectedYear == null || !data.seasons.some((s) => s.year === _selectedYear)) {
      _selectedYear = data.current_year;
    }
    _rebuildSeasonFilter(data);
    _renderSeason();
    _renderBackTest(data);

    // The forecast endpoint does real work (a live call out to the weather
    // service, plus a full history scan) and can take several seconds on a
    // slow link - say so, rather than leaving an empty card that reads as
    // broken while it's simply still loading.
    const cardEl = document.getElementById("harvestForecastCard");
    if (cardEl && !cardEl.innerHTML.trim()) {
      cardEl.innerHTML = `<div class="text-sm text-slate-400 p-4 text-center">
        <i class="fa-solid fa-spinner fa-spin"></i> Working out this season's forecast...</div>`;
    }

    const forecast = await forecastPromise;
    _renderForecast(forecast);
    _renderMethodology(data, forecast);
  }

  function _rebuildSeasonFilter(data) {
    const select = document.getElementById("riskSeasonFilter");
    const years = [...data.seasons].sort((a, b) => b.year - a.year).map((s) => s.year);
    const signature = years.join("|");
    if (select.dataset.years !== signature) {
      select.innerHTML = years.map((y) => `<option value="${y}">${y}${y === data.current_year ? " (current)" : ""}</option>`).join("");
      select.dataset.years = signature;
    }
    select.value = _selectedYear;
  }

  const BAND_COLORS = { Low: "#16a34a", Moderate: "#eab308", Elevated: "#ea580c", High: "#dc2626" };
  function _riskColor(points25) {
    // Same 25/50/75-of-max thresholds as the backend's _band(), just
    // applied to a single component's 0-25 scale instead of the 0-100 sum.
    const frac = points25 / 25;
    if (frac < 0.25) return BAND_COLORS.Low;
    if (frac < 0.5) return BAND_COLORS.Moderate;
    if (frac < 0.75) return BAND_COLORS.Elevated;
    return BAND_COLORS.High;
  }

  function _statusLabel(status, window) {
    if (status === "final") return "Final";
    if (status === "in_progress") return "In progress";
    return `Pending - opens ${window.split(" - ")[0]}`;
  }

  function _renderSeason() {
    const season = _data.seasons.find((s) => s.year === _selectedYear);
    if (!season) return;
    const driversByKey = {};
    _data.drivers.forEach((d) => { driversByKey[d.key] = d; });

    // --- Score header -------------------------------------------------
    const headerEl = document.getElementById("riskScoreHeader");
    if (season.risk_score != null) {
      const color = BAND_COLORS[season.band] || "#64748b";
      headerEl.innerHTML = `
        <div class="flex items-center gap-3">
          <div class="text-4xl font-bold" style="color:${color}">${season.risk_score}</div>
          <div>
            <div class="font-semibold" style="color:${color}">${season.band} Risk</div>
            <div class="text-xs text-slate-500">out of 100 - all ${_data.driver_count} factors known</div>
          </div>
        </div>
        <div class="text-xs text-slate-500">${season.total_kg ? `Actual harvest: ${Math.round(season.total_kg).toLocaleString()} kg` : ""}</div>`;
    } else {
      const pct = Math.round((season.known_count / _data.driver_count) * 100);
      headerEl.innerHTML = `
        <div class="flex items-center gap-3">
          <div class="text-4xl font-bold text-slate-400">${season.score_so_far ?? "-"}</div>
          <div>
            <div class="font-semibold text-slate-500">Score so far</div>
            <div class="text-xs text-slate-500">${season.known_count} of ${_data.driver_count} factors known (${pct}%) - final score once the season's windows close</div>
          </div>
        </div>`;
    }

    // --- Per-driver breakdown ------------------------------------------
    const compEl = document.getElementById("riskComponents");
    compEl.innerHTML = season.components.map((c) => {
      const d = driversByKey[c.key];
      const known = c.risk_points != null;
      const barColor = known ? _riskColor(c.risk_points) : "#cbd5e1";
      const barPct = known ? Math.round((c.risk_points / 25) * 100) : 100;
      const valueText = c.value != null ? `${c.value} ${d.unit}` : "no data yet";
      const rangeText = d.historical_min != null ? `${_data.reference_label} range: ${d.historical_min}-${d.historical_max} ${d.unit}` : "";
      return `
        <div class="border border-slate-200 rounded-lg p-3">
          <div class="flex justify-between items-start gap-2">
            <div>
              <div class="font-semibold text-sm">${d.label}</div>
              <div class="text-xs text-slate-500">${d.window}</div>
            </div>
            <span class="text-xs px-2 py-0.5 rounded-full whitespace-nowrap ${c.status === "final" ? "bg-slate-100 text-slate-600" : "bg-amber-50 text-amber-700"}">${_statusLabel(c.status, d.window)}</span>
          </div>
          <div class="text-xs text-slate-500 mt-1.5">${d.why}</div>
          <div class="mt-2.5">
            <div class="flex justify-between text-xs mb-1">
              <span class="font-medium">${valueText}</span>
              <span class="text-slate-500">${known ? `${c.risk_points}/25 risk points` : "not scored yet"}</span>
            </div>
            <div class="w-full bg-slate-100 rounded-full h-2 ${known ? "" : "opacity-40"}">
              <div class="h-2 rounded-full" style="width:${barPct}%;background:${barColor}${known ? "" : ";background-image:repeating-linear-gradient(45deg,#cbd5e1,#cbd5e1 4px,#e2e8f0 4px,#e2e8f0 8px)"}"></div>
            </div>
            <div class="text-[10px] text-slate-400 mt-1">${rangeText}</div>
          </div>
        </div>`;
    }).join("");
  }

  function _renderBackTest(data) {
    const years = [...data.seasons].sort((a, b) => a.year - b.year);
    const categories = years.map((s) => `${s.year}${s.is_current ? " *" : ""}`);

    const scored = years.filter((s) => s.risk_score != null);
    if (scored.length) {
      LWCharts.barChart(document.getElementById("riskScoreChart"), {
        categories,
        series: [{
          label: "Risk Score", color: "#64748b",
          colors: years.map((s) => (s.risk_score != null ? (BAND_COLORS[s.band] || "#64748b") : null)),
          values: years.map((s) => s.risk_score),
        }],
        yLabel: (y) => Math.round(y),
      });
    } else {
      document.getElementById("riskScoreChart").innerHTML =
        `<div class="text-sm text-slate-400 p-8 text-center">No fully-scored seasons yet</div>`;
    }

    LWCharts.barChart(document.getElementById("riskKgChart"), {
      categories,
      series: [{ label: "Harvest (kg)", color: "#0A2F6B", values: years.map((s) => s.total_kg || null) }],
      yLabel: (y) => Math.round(y).toLocaleString(),
    });
  }

  const SCENARIO_LABELS = { favorable: "Favorable", expected: "Expected", unfavorable: "Unfavorable" };

  // Actual/forecast/assumed day-count breakdown for one driver row, e.g.
  // "14d actual + 16d forecast + 16d assumed" - only the segments that
  // actually have days in them appear, joined with " + ".
  function _basisText(d) {
    if (d.data_gap) return `<span class="text-amber-700">data gap - historical range only</span>`;
    const parts = [];
    if (d.actual_days) parts.push(`${d.actual_days}d actual`);
    if (d.forecast_days) parts.push(`${d.forecast_days}d forecast`);
    if (d.assumed_days) parts.push(`${d.assumed_days}d assumed`);
    return parts.join(" + ");
  }

  function _renderForecast(forecast) {
    const cardEl = document.getElementById("harvestForecastCard");
    if (!cardEl) return;
    if (!forecast) {
      cardEl.innerHTML = `<div class="text-sm text-slate-400 p-4 text-center">Harvest forecast is currently unavailable</div>`;
      return;
    }

    const scenarioCards = Object.keys(SCENARIO_LABELS).map((key) => {
      const s = forecast.scenarios[key];
      if (!s || s.predicted_kg == null) {
        return `<div class="border border-slate-200 rounded-lg p-3 text-center text-slate-400 text-sm">${SCENARIO_LABELS[key]}<br>Not available</div>`;
      }
      const color = BAND_COLORS[s.band] || "#64748b";
      const avgLabel = forecast.regression_label ? `${forecast.regression_label} avg` : "historical avg";
      const pctText = s.vs_avg_pct == null ? "" : `${s.vs_avg_pct > 0 ? "+" : ""}${s.vs_avg_pct}% vs ${avgLabel}`;
      return `
        <div class="border border-slate-200 rounded-lg p-3 text-center">
          <div class="text-xs font-semibold text-slate-500 uppercase tracking-wide">${SCENARIO_LABELS[key]}</div>
          <div class="text-2xl font-bold mt-1">${Math.round(s.predicted_kg).toLocaleString()} kg</div>
          <div class="text-xs mt-1 font-medium" style="color:${color}">${s.band} Risk (${s.risk_score})</div>
          <div class="text-xs text-slate-400 mt-0.5">${pctText}</div>
        </div>`;
    }).join("");

    const driverRows = forecast.drivers.map((d) => `
      <tr class="border-b">
        <td class="p-2">${d.label}</td>
        <td class="p-2 text-slate-500 whitespace-nowrap">${_basisText(d)}</td>
        <td class="p-2">${d.scenarios.favorable.value ?? "-"}</td>
        <td class="p-2">${d.scenarios.expected.value ?? "-"}</td>
        <td class="p-2">${d.scenarios.unfavorable.value ?? "-"}</td>
      </tr>`).join("");

    const horizonNote = forecast.forecast_unavailable
      ? `<div class="text-xs text-amber-700 mb-3"><i class="fa-solid fa-triangle-exclamation"></i> Live weather forecast temporarily unavailable - showing historical-scenario estimates only for the near term too.</div>`
      : forecast.forecast_horizon_end
        ? `<div class="text-xs text-slate-500 mb-3">Real forecast data through ${forecast.forecast_horizon_end}; beyond that, scenarios use the ${forecast.reference_label} historical range.</div>`
        : "";

    cardEl.innerHTML = `
      ${horizonNote}
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-4">${scenarioCards}</div>
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead><tr class="text-left border-b text-slate-500">
            <th class="p-2">Factor</th><th class="p-2">Basis</th>
            <th class="p-2">Favorable</th><th class="p-2">Expected</th><th class="p-2">Unfavorable</th>
          </tr></thead>
          <tbody>${driverRows}</tbody>
        </table>
      </div>`;
  }

  function _renderMethodology(data, forecast) {
    const el = document.getElementById("riskMethodologyDrivers");
    if (el) {
      el.innerHTML = data.drivers.map((d) => `
        <div class="border-l-2 border-slate-200 pl-3">
          <div class="font-medium text-slate-700">${d.label} <span class="text-slate-400 font-normal">(${d.window})</span></div>
          <div class="text-slate-500">${d.why}</div>
        </div>`).join("");
    }

    // The forecast bullets and their heading are shown together or not at
    // all - without hiding the heading too, a failed forecast fetch leaves
    // "About the Harvest Forecast card above:" sitting over an empty list.
    const forecastEl = document.getElementById("riskMethodologyForecast");
    const headingEl = document.getElementById("riskMethodologyForecastHeading");
    if (!forecastEl) return;
    if (!forecast || !forecast.regression) {
      forecastEl.innerHTML = "";
      if (headingEl) headingEl.classList.add("hidden");
      return;
    }
    if (headingEl) headingEl.classList.remove("hidden");
    const reg = forecast.regression;
    forecastEl.innerHTML = `
      <li>The Harvest Forecast card converts each scenario's risk score to a predicted kg figure
        via a straight line fit through the (risk score, harvest total) pairs from ${reg.n_seasons}
        seasons (r = ${reg.r}). Those start at 2016, when the replanted blocks first bore fruit -
        before that only block 7 was cropping, so earlier totals reflect a young orchard rather
        than its weather.</li>
      <li>For whatever part of a factor's time window is still ahead, the forecast uses a real
        weather forecast (up to 15 days out) where available, and the ${forecast.reference_label}
        historical range beyond that - see each factor's "Basis" column for exactly how much of
        each is actual, forecast, or assumed.</li>
      <li>The Unfavorable scenario combines each factor's own worst historical year - four
        different real years, not one real season that was worst on everything at once - so its
        score can land outside anything a real season reached. Predictions are held to the best and
        worst harvests actually on record rather than running the line off past them, so the two
        extremes read as "about as good/bad as it has ever gone", not exact figures.</li>
      <li>This is a description of what the historical pattern implies about this season's specific
        weather, not a guarantee of what will be harvested.</li>`;
  }

  return { bind, load };
})();
