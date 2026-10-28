// Markets tab (/api/market/summary, backend/routers/market.py): a daily
// chart of price or units sold per receiver and pack type, and below it each
// pack type's season R/carton. A season with a market workbook comes from
// its per-receiver sheets (scripts/import_market_prices.py); the season still
// being sold comes live from Boord Packhouse's delivery notes and Boord's
// market statements and invoices, so it fills in as they are entered.
const LWMarketTab = (() => {
  let _data = null;
  let _loadedAt = 0;
  let _bound = false;
  let _season = null;
  // What is UNticked, not what is ticked: a pack type or receiver that first
  // appears in a later load (a new receiver mid-season) shows by default.
  const _off = { pack: new Set(), receiver: new Set() };
  let _refOn = true;   // the Joburg Market dashed line
  const REF_LABEL = "Joburg Market (whole market)";
  const REF_COLOUR = "#334155";

  const DAY_MS = 86400000;
  const dayNo = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / DAY_MS;
  const dateOf = (n) => new Date(n * DAY_MS);

  // ISO 8601 week, the numbering the workbook's own WEEK column uses
  // (29 Oct 2025 is week 44).
  function isoWeek(d) {
    const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
    return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / DAY_MS + 1) / 7);
  }

  function bind() {
    if (_bound) return;
    _bound = true;
    document.getElementById("marketSeason").addEventListener("change", (e) => {
      _season = +e.target.value;
      renderChart();
    });
    document.getElementById("marketMetric").addEventListener("change", renderChart);
    const card = document.getElementById("marketChartCard");
    card.addEventListener("change", (e) => {
      const kind = e.target.dataset.kind;
      if (!kind) return;
      if (kind === "ref") _refOn = e.target.checked;
      else if (e.target.checked) _off[kind].delete(e.target.value);
      else _off[kind].add(e.target.value);
      renderChart();
    });
    card.addEventListener("click", (e) => {
      const kind = e.target.dataset.checkAll || e.target.dataset.checkNone;
      if (!kind) return;
      const names = seasonNames(kind);
      if (e.target.dataset.checkAll) names.forEach((n) => _off[kind].delete(n));
      else names.forEach((n) => _off[kind].add(n));
      renderChart();
    });
    const tab = document.getElementById("tab-market");
    LWCharts.bindPdfButtons(tab);
    tab.addEventListener("click", (e) => {
      const btn = e.target.closest(".chart-xlsx-btn");
      if (btn) downloadExcel(btn);
    });
  }

  // The workbook carries what the chart shows: its season, and only the
  // ticked pack types and receivers (none sent = all). Built on the server,
  // like every workbook in this app - see routers/market.py market_export.
  async function downloadExcel(btn) {
    const q = new URLSearchParams();
    if (_season != null) {
      q.set("season", _season);
      ["pack", "receiver"].forEach((kind) => {
        const names = seasonNames(kind);
        if (names.some((n) => _off[kind].has(n))) names.filter((n) => !_off[kind].has(n)).forEach((n) => q.append(kind, n));
      });
    }
    const icon = btn.querySelector("i");
    icon.className = "fa-solid fa-spinner fa-spin";
    btn.disabled = true;
    try {
      const blob = await Boord.api(`/api/market/export?${q}`, { timeoutMs: 30000 });
      Boord.downloadBlob(blob, _season != null ? `Market_Prices_${_season}.xlsx` : "Market_Prices.xlsx");
    } catch (e) {
      console.error("Markets Excel download failed:", e);
      Boord.toast("Could not build the workbook");
    } finally {
      icon.className = "fa-solid fa-file-excel";
      btn.disabled = false;
    }
  }

  async function load(fetchSummary, { force = false } = {}) {
    if (!force && _data && Boord.isFresh(_loadedAt)) return;
    const result = await Boord.loadTab({
      key: "boord_cached_markets", fetchFn: fetchSummary, noun: "market prices", label: "Markets",
    });
    if (!result) return;
    _data = result.data;
    _loadedAt = result.cached ? 0 : Date.now();   // saved figures: try again on the next tap
    const seasons = _data.daily.seasons;
    if (!seasons.includes(_season)) _season = seasons.length ? seasons[seasons.length - 1] : null;
    renderChart();
    renderTable(_data);
  }

  const seasonPoints = () => _data.daily.points.filter((p) => p.season_year === _season);
  const seasonNames = (kind) => [...new Set(seasonPoints().map((p) => (kind === "pack" ? p.pack_type : p.receiver)))].sort();

  function checkboxes(kind) {
    return seasonNames(kind).map((n) => `
      <label class="inline-flex items-center gap-1 cursor-pointer">
        <input type="checkbox" data-kind="${kind}" value="${Boord.escapeHtml(n)}" ${_off[kind].has(n) ? "" : "checked"}>
        ${Boord.escapeHtml(n)}
      </label>`).join("");
  }

  function renderChart() {
    const card = document.getElementById("marketChartCard");
    if (!_data || _season == null) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");

    const daily = _data.daily;
    const points = seasonPoints();
    const refPoints = daily.reference.filter((r) => r.season_year === _season);
    document.getElementById("marketSeason").innerHTML = [...daily.seasons].reverse()
      .map((s) => `<option value="${s}" ${s === _season ? "selected" : ""}>${s} season</option>`).join("");
    const source = !points.length
      ? `${_season}: no sales of the farm's own on file yet - Boord Packhouse's delivery notes fill them in `
        + `once Boord has their market invoices or statements.`
      : daily.workbook_seasons.includes(_season)
        ? `${_season}: from the farm's market workbook, one line per receiver and pack type.`
        : `${_season} so far: from Boord Packhouse's delivery notes, priced from Boord's market invoices `
          + `(or the statement the office typed) - fills in as they are entered.`;
    // The site keeps no history, so a season before the app started
    // recording has no dashed line - say so rather than leave it a puzzle.
    const refNote = refPoints.length
      ? " The dashed line is Joburg Market's whole-market average price per unit sold and units sold, recorded daily."
      : daily.reference.length
        ? ` No Joburg Market line this season: the market keeps no history, and this app has recorded it only `
          + `since ${Boord.fmtDate(daily.reference[0].date)}.`
        : "";
    document.getElementById("marketChartSource").textContent = `${source}${refNote} Hover a dot for the day.`;
    document.getElementById("marketPackChecks").innerHTML = checkboxes("pack");
    document.getElementById("marketReceiverChecks").innerHTML = checkboxes("receiver");

    const metric = document.getElementById("marketMetric").value;
    // The PDF's header names the season and measure on screen; the legend,
    // captured with the chart, says which lines were ticked.
    document.querySelector("#marketChartCard .chart-pdf-btn").dataset.title =
      `${metric === "price" ? "Daily Price (R per carton / unit)" : "Daily Units Sold"} - ${_season} Season`;
    // One line per receiver and pack type. Colours are fixed across the
    // whole season, not just what is ticked, so a line keeps its colour
    // when others are switched off.
    const keys = [...new Set(points.map((p) => `${p.receiver} · ${p.pack_type}`))].sort();
    const colour = (k) => {
      const i = keys.indexOf(k);
      return i < LWCharts.PALETTE.length ? LWCharts.PALETTE[i] : `hsl(${(i * 47) % 360} 65% 40%)`;
    };
    const money = (v) => `R${v.toLocaleString(Boord.LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const series = keys.map((k) => ({ label: k, color: colour(k), points: [] }));
    points.forEach((p) => {
      if (_off.pack.has(p.pack_type) || _off.receiver.has(p.receiver)) return;
      const d = dateOf(dayNo(p.date));
      series[keys.indexOf(`${p.receiver} · ${p.pack_type}`)].points.push({
        x: dayNo(p.date),
        y: metric === "price" ? p.price : p.cartons,
        title: `${p.receiver} · ${p.pack_type}\n${Boord.fmtDate(p.date, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}`
          + ` (week ${isoWeek(d)})\n${money(p.price)}/carton · ${Math.round(p.cartons).toLocaleString(Boord.LOCALE)} cartons`,
      });
    });
    // Joburg Market last, so its dashed line is drawn over the farm's. Not
    // cut by the pack-type and receiver ticks: it is the whole market's
    // litchis, every pack and agent at once.
    if (_refOn) {
      series.push({
        label: REF_LABEL, color: REF_COLOUR, dashed: true,
        points: refPoints.filter((r) => (metric === "price" ? r.price : r.units) != null).map((r) => ({
          x: dayNo(r.date),
          y: metric === "price" ? r.price : r.units,
          title: `${REF_LABEL}\n${Boord.fmtDate(r.date, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}`
            + ` (week ${isoWeek(dateOf(dayNo(r.date)))})\n`
            + `${r.price != null ? `${money(r.price)}/unit · ` : ""}`
            + `${r.units != null ? `${Math.round(r.units).toLocaleString(Boord.LOCALE)} units · ` : ""}`
            + `${money(r.rand_per_kg)}/kg · ${Math.round(r.kg).toLocaleString(Boord.LOCALE)} kg`,
        })),
      });
    }
    const shown = series.filter((s) => s.points.length);

    // The axis spans the whole season whatever is ticked, from the Monday
    // its first sale fell in, so switching lines never moves the weeks.
    const days = points.concat(refPoints).map((p) => dayNo(p.date));
    const first = Math.min(...days), last = Math.max(...days);
    const start = first - ((dateOf(first).getUTCDay() + 6) % 7);
    const ticks = [];
    let month = null;
    for (let x = start; x <= last; x += 7) {
      const d = dateOf(x);
      // The month goes under the first week that starts in it - or, for the
      // first tick, under the month the season's first sale fell in.
      const m = (x === start ? dateOf(first) : d).toLocaleDateString(Boord.LOCALE, { month: "short", timeZone: "UTC" });
      ticks.push({ x, label: `Wk ${isoWeek(d)}`, sub: m !== month ? m : "" });
      month = m;
    }
    const chart = document.getElementById("marketDailyChart");
    if (!shown.length) {
      LWCharts.emptyState(chart, "Nothing ticked - tick a pack type and a receiver, or Joburg Market, to plot them");
      document.getElementById("marketDailyLegend").innerHTML = "";
      return;
    }
    LWCharts.lineChart(chart, {
      series: shown, xTicks: ticks, markers: true, xMin: start, xMax: last, minWidthPerPoint: 6, height: 300,
      yLabel: metric === "price" ? (v) => `R${Math.round(v).toLocaleString(Boord.LOCALE)}`
                                 : (v) => Math.round(v).toLocaleString(Boord.LOCALE),
    });
    // A season is wide (a dot per day), so lineChart's fixed 300px box left
    // a blank band under the plot once it shrank to the card's width - on
    // screen and in the PDF. Its own proportions set the height instead.
    // (Below 720px the card scrolls sideways - see #marketChartExport.)
    const svgEl = chart.querySelector("svg");
    svgEl.removeAttribute("height");
    svgEl.style.height = "auto";
    LWCharts.legend(document.getElementById("marketDailyLegend"),
                    shown.map((s) => ({ label: s.label, color: s.color, dashed: s.dashed })));
  }

  function renderTable(market) {
    const card = document.getElementById("marketPriceCard");
    const empty = document.getElementById("marketPriceEmpty");
    const hasDaily = market.daily.points.length > 0;
    empty.classList.toggle("hidden", market.pack_types.length > 0 || hasDaily);
    if (!market.pack_types.length) {
      card.classList.add("hidden");
      return;
    }
    card.classList.remove("hidden");

    const seasons = [...market.seasons].sort((a, b) => b - a);   // latest first
    document.getElementById("marketPriceHead").innerHTML = `<th class="p-2">Pack type</th>` +
      seasons.map((s) => `<th class="p-2 text-right">${s}</th>`).join("");

    document.getElementById("marketPriceRows").innerHTML = market.pack_types.map((pt) => {
      const cells = seasons.map((s) => {
        const line = (market.by_season[s] || {})[pt];
        if (!line) return `<td class="p-2 text-right text-slate-300">-</td>`;
        const price = line.rand_per_carton.toLocaleString(Boord.LOCALE, { maximumFractionDigits: 2 });
        const cartons = Math.round(line.cartons).toLocaleString(Boord.LOCALE);
        return `<td class="p-2 text-right">R${price}<br><span class="text-xs text-slate-400">${cartons} ctn</span></td>`;
      }).join("");
      return `<tr class="border-b"><td class="p-2">${Boord.escapeHtml(pt)}</td>${cells}</tr>`;
    }).join("");

    const ref = market.joburg_market_reference;
    document.getElementById("marketPriceReference").textContent = ref
      ? `Joburg Market reference, whole market (not by pack type): `
        + `R${ref.rand_per_kg.toLocaleString(Boord.LOCALE, { maximumFractionDigits: 2 })}/kg on `
        + `${Boord.fmtDate(ref.date)} (${Math.round(ref.kg_sold).toLocaleString(Boord.LOCALE)} kg sold).`
      : "";
  }

  return { bind, load };
})();
