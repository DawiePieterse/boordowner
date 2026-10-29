// Markets tab (/api/market/summary, backend/routers/market.py): a daily
// chart of price or units sold for one pack type, per receiver, with the
// seasons ticked laid over each other - and below it each pack type's
// season R/carton. A season with a market workbook comes from its
// per-receiver sheets (scripts/import_market_prices.py); the season still
// being sold comes live from Boord Packhouse's delivery notes and Boord's
// market statements and invoices, so it fills in as they are entered. The
// pack types and receivers listed are Boord Packhouse's own, so one set up
// there during the season is offered at once and charts as soon as it sells.
const LWMarketTab = (() => {
  let _data = null;
  let _loadedAt = 0;
  let _bound = false;
  let _seasonsOn = null;        // the seasons ticked; null until the first load picks them
  let _seenSeasons = new Set(); // so a season appearing later (next year's) arrives ticked
  // The one pack type plotted. undefined = not chosen yet (the load picks
  // the busiest), null = the owner unticked it. One at a time because a 2kg
  // box and a 15kg crate don't belong on the same R/carton axis.
  let _pack;
  // Receivers UNticked, not ticked: one Packhouse adds mid-season shows.
  const _receiversOff = new Set();
  let _refOn = true;   // the Joburg Market dashed line
  const REF_LABEL = "Joburg Market (whole market)";
  const REF_COLOUR = "#334155";
  const EARLIER_DASH = "1.5,4";   // an earlier season's line: dotted, beside the newest one's solid

  const DAY_MS = 86400000;
  const dayNo = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / DAY_MS;
  const dateOf = (n) => new Date(n * DAY_MS);
  const pad = (n) => String(n).padStart(2, "0");

  // ISO 8601 week, the numbering the workbook's own WEEK column uses
  // (29 Oct 2025 is week 44).
  function isoWeek(d) {
    const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
    t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
    return Math.ceil(((t - Date.UTC(t.getUTCFullYear(), 0, 1)) / DAY_MS + 1) / 7);
  }

  // Seasons are laid over each other by day of season, counted from Boord's
  // season start (1 August on this farm): day 90 of 2025 sits over day 90
  // of 2026, whatever weekday each fell on.
  const anchorDay = (season) => {
    const a = _data.daily.season_anchor;
    return dayNo(`${season}-${pad(a.month)}-${pad(a.day)}`);
  };
  const seasonDay = (p) => dayNo(p.date) - anchorDay(p.season_year);

  // A receiver's colour and marker shape, fixed by its place in the list
  // (Packhouse's order) rather than by what is ticked, so it keeps both
  // when others come and go. Shapes run out after ten; the next ten repeat
  // them hollow - so a black-and-white print still tells them apart.
  function receiverStyle(name) {
    const i = Math.max(0, _data.daily.receivers.indexOf(name));
    const shapes = LWCharts.MARKER_SHAPES;
    return {
      color: i < LWCharts.PALETTE.length ? LWCharts.PALETTE[i] : `hsl(${(i * 47) % 360} 65% 40%)`,
      marker: shapes[i % shapes.length],
      hollow: i >= shapes.length,
    };
  }

  function bind() {
    if (_bound) return;
    _bound = true;
    document.getElementById("marketMetric").addEventListener("change", renderChart);
    const card = document.getElementById("marketChartCard");
    card.addEventListener("change", (e) => {
      const { kind } = e.target.dataset;
      if (!kind) return;
      const on = e.target.checked, v = e.target.value;
      if (kind === "ref") _refOn = on;
      else if (kind === "season") { if (on) _seasonsOn.add(+v); else _seasonsOn.delete(+v); }
      else if (kind === "pack") _pack = on ? v : null;
      else if (kind === "receiver") { if (on) _receiversOff.delete(v); else _receiversOff.add(v); }
      renderChart();
    });
    card.addEventListener("click", (e) => {
      const kind = e.target.dataset.checkAll || e.target.dataset.checkNone;
      if (kind !== "receiver") return;
      _data.daily.receivers.forEach((n) => (e.target.dataset.checkAll ? _receiversOff.delete(n) : _receiversOff.add(n)));
      renderChart();
    });
    const tab = document.getElementById("tab-market");
    LWCharts.bindPdfButtons(tab);
    tab.addEventListener("click", (e) => {
      const btn = e.target.closest(".chart-xlsx-btn");
      if (btn) downloadExcel(btn);
    });
  }

  // The workbook carries what the chart shows: the seasons, pack type and
  // receivers ticked (no pack type ticked = every pack type; every receiver
  // ticked = none sent). Built on the server, like every workbook in this
  // app - see routers/market.py market_export.
  async function downloadExcel(btn) {
    const q = new URLSearchParams();
    const seasons = tickedSeasons();
    seasons.forEach((s) => q.append("season", s));
    if (_pack) q.append("pack", _pack);
    if (_receiversOff.size) _data.daily.receivers.filter((n) => !_receiversOff.has(n)).forEach((n) => q.append("receiver", n));
    const icon = btn.querySelector("i");
    icon.className = "fa-solid fa-spinner fa-spin";
    btn.disabled = true;
    try {
      const blob = await Boord.api(`/api/market/export?${q}`, { timeoutMs: 30000 });
      Boord.downloadBlob(blob, seasons.length ? `Market_Prices_${seasons.join("-")}.xlsx` : "Market_Prices.xlsx");
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
    if (_seasonsOn === null) _seasonsOn = new Set(seasons.slice(-2));   // this season and last
    else seasons.filter((s) => !_seenSeasons.has(s)).forEach((s) => _seasonsOn.add(s));
    _seenSeasons = new Set(seasons);
    if (_pack === undefined) _pack = busiestPack();
    renderChart();
    renderTable(_data);
  }

  const tickedSeasons = () => _data.daily.seasons.filter((s) => _seasonsOn.has(s));   // oldest first
  const tickedPoints = () => _data.daily.points.filter((p) => _seasonsOn.has(p.season_year));

  // The pack type with the most days sold in the seasons ticked - the
  // first chart shows something, rather than asking first.
  function busiestPack() {
    const n = {};
    tickedPoints().forEach((p) => { n[p.pack_type] = (n[p.pack_type] || 0) + 1; });
    const best = Object.keys(n).sort((a, b) => n[b] - n[a])[0];
    return best || null;
  }

  function checkbox(kind, value, label, { checked, disabled = false, faded = false, title = "", swatch = "" }) {
    const style = disabled ? "opacity:.4;cursor:not-allowed" : "";
    return `
      <label class="inline-flex items-center gap-1 ${disabled ? "" : "cursor-pointer"} ${faded ? "text-slate-400" : ""}"
             style="${style}" ${title ? `title="${Boord.escapeHtml(title)}"` : ""}>
        <input type="checkbox" data-kind="${kind}" value="${Boord.escapeHtml(String(value))}"
               ${checked ? "checked" : ""} ${disabled ? "disabled" : ""}>
        ${swatch}${Boord.escapeHtml(label)}
      </label>`;
  }

  function renderChecks() {
    const daily = _data.daily;
    const pts = tickedPoints();
    document.getElementById("marketSeasonChecks").innerHTML = [...daily.seasons].reverse().map((s) =>
      checkbox("season", s, String(s), {
        checked: _seasonsOn.has(s),
        title: daily.workbook_seasons.includes(s) ? "From the farm's market workbook" : "Live, from Boord Packhouse",
      })).join("");
    const sold = new Set(pts.map((p) => p.pack_type));
    document.getElementById("marketPackChecks").innerHTML = daily.packs.map((n) => checkbox("pack", n, n, {
      checked: _pack === n,
      disabled: _pack != null && _pack !== n,
      faded: !sold.has(n),
      title: sold.has(n) ? "" : "Set up in Boord Packhouse - no sales in the seasons ticked",
    })).join("");
    const selling = new Set(pts.filter((p) => p.pack_type === _pack).map((p) => p.receiver));
    document.getElementById("marketReceiverChecks").innerHTML = daily.receivers.map((n) => checkbox("receiver", n, n, {
      checked: !_receiversOff.has(n),
      faded: !selling.has(n),
      title: selling.has(n) ? "" : `No ${_pack || ""} sales through them in the seasons ticked`,
      swatch: LWCharts.swatch(receiverStyle(n)),
    })).join("");
  }

  function renderChart() {
    const card = document.getElementById("marketChartCard");
    if (!_data || !_data.daily.seasons.length) { card.classList.add("hidden"); return; }
    card.classList.remove("hidden");
    renderChecks();

    const daily = _data.daily;
    const seasons = tickedSeasons();
    const several = seasons.length > 1;
    const metric = document.getElementById("marketMetric").value;
    const pts = tickedPoints();
    // The weeks are labelled by the newest season ticked, but the solid line
    // goes to the newest one with the farm's own sales on the chart: early
    // in a season, before anything has sold, last season's lines shouldn't
    // fade just because this season has a Joburg Market point.
    const labelSeason = seasons[seasons.length - 1];
    const plotted = seasons.filter((s) => pts.some((p) => p.season_year === s && p.pack_type === _pack
                                                      && !_receiversOff.has(p.receiver)));
    const newest = plotted.length ? plotted[plotted.length - 1] : labelSeason;
    const refPts = daily.reference.filter((r) => _seasonsOn.has(r.season_year));
    const a = daily.season_anchor;
    const anchorText = Boord.fmtDate(`2001-${pad(a.month)}-${pad(a.day)}`, { day: "numeric", month: "long" });

    const sources = seasons.map((s) => (daily.workbook_seasons.includes(s)
      ? `${s} from the farm's market workbook`
      : pts.some((p) => p.season_year === s)
        ? `${s} so far from Boord Packhouse's delivery notes, priced from Boord's market invoices (or the typed statement)`
        : `${s}: no sales of the farm's own on file yet`));
    const notes = [sources.join("; ") + "."];
    if (several) notes.push(`Seasons are laid over each other by day of season from ${anchorText}; the weeks shown are ${labelSeason}'s, and earlier seasons are dotted.`);
    if (refPts.length) notes.push("The dashed line is Joburg Market's whole-market average price per unit sold and units sold, recorded daily.");
    else if (daily.reference.length) notes.push(`No Joburg Market line in these seasons: the market keeps no history, and this app has recorded it only since ${Boord.fmtDate(daily.reference[0].date)}.`);
    if (!_pack) notes.push("Tick a pack type to plot the farm's own sales.");
    document.getElementById("marketChartSource").textContent = `${notes.join(" ")} Hover a marker for the day.`;

    const measure = metric === "price" ? "Daily Price (R per carton / unit)" : "Daily Units Sold";
    document.querySelector("#marketChartCard .chart-pdf-btn").dataset.title =
      `${measure} - ${_pack || "Joburg Market only"} - ${seasons.join(" & ")} Season${several ? "s" : ""}`;

    const money = (v) => `R${v.toLocaleString(Boord.LOCALE, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    const day = (iso) => `${Boord.fmtDate(iso, { weekday: "short", day: "numeric", month: "short", year: "numeric" })}`
      + ` (week ${isoWeek(dateOf(dayNo(iso)))})`;
    const lineStyle = (season) => ({
      dash: season >= newest ? "" : EARLIER_DASH, muted: season < newest, emphasize: plotted.length > 1 && season === newest,
    });

    const series = [];
    seasons.forEach((season) => {
      daily.receivers.filter((r) => !_receiversOff.has(r)).forEach((receiver) => {
        const mine = pts.filter((p) => p.season_year === season && p.receiver === receiver && p.pack_type === _pack);
        if (!mine.length) return;
        series.push({
          label: several ? `${season} · ${receiver}` : receiver, ...receiverStyle(receiver), ...lineStyle(season),
          points: mine.map((p) => ({
            x: seasonDay(p), y: metric === "price" ? p.price : p.cartons,
            title: `${receiver} · ${p.pack_type} · ${season} season\n${day(p.date)}\n`
              + `${money(p.price)}/carton · ${Math.round(p.cartons).toLocaleString(Boord.LOCALE)} cartons`,
          })),
        });
      });
    });
    // Joburg Market last, so its dashed line is drawn over the farm's. Not
    // cut by the pack type or receivers: it is the whole market's litchis.
    if (_refOn) {
      seasons.forEach((season) => {
        const mine = refPts.filter((r) => r.season_year === season && (metric === "price" ? r.price : r.units) != null);
        if (!mine.length) return;
        series.push({
          label: several ? `${REF_LABEL} ${season}` : REF_LABEL, color: REF_COLOUR, dashed: true,
          muted: season < newest, marker: "circle", hollow: true,
          points: mine.map((r) => ({
            x: seasonDay(r), y: metric === "price" ? r.price : r.units,
            title: `${REF_LABEL} · ${season} season\n${day(r.date)}\n`
              + `${r.price != null ? `${money(r.price)}/unit · ` : ""}`
              + `${r.units != null ? `${Math.round(r.units).toLocaleString(Boord.LOCALE)} units · ` : ""}`
              + `${money(r.rand_per_kg)}/kg · ${Math.round(r.kg).toLocaleString(Boord.LOCALE)} kg`,
          })),
        });
      });
    }

    const chart = document.getElementById("marketDailyChart");
    const legendEl = document.getElementById("marketDailyLegend");
    // The axis spans every day the seasons ticked have - any pack type or
    // receiver - so ticking lines on and off never moves the weeks.
    const days = pts.concat(refPts).map(seasonDay);
    if (!series.length || !days.length) {
      LWCharts.emptyState(chart, seasons.length
        ? "Nothing to plot - tick a pack type, a receiver that sold it, or Joburg Market"
        : "Tick a season to plot it");
      legendEl.innerHTML = "";
      return;
    }
    // Week ticks from the newest season's own calendar, from the Monday the
    // earliest day falls in.
    const first = Math.min(...days), last = Math.max(...days);
    const base = anchorDay(labelSeason);
    const start = first - ((dateOf(base + first).getUTCDay() + 6) % 7);
    const ticks = [];
    let month = null;
    for (let x = start; x <= last; x += 7) {
      const d = dateOf(base + x);
      // The month goes under the first week that starts in it - or, for the
      // first tick, under the month the earliest day fell in.
      const m = (x === start ? dateOf(base + first) : d).toLocaleDateString(Boord.LOCALE, { month: "short", timeZone: "UTC" });
      ticks.push({ x, label: `Wk ${isoWeek(d)}`, sub: m !== month ? m : "" });
      month = m;
    }
    // Zoomed on what is drawn: 50 (rand or cartons) below the lowest value
    // and 50 above the highest, never below 0 - so a season whose prices sit
    // between R200 and R300 fills the chart instead of a band of a 0-R1 000
    // axis. It re-zooms as lines are ticked on and off.
    const ys = series.flatMap((s) => s.points.map((p) => p.y));
    const yDomain = [Math.max(0, Math.min(...ys) - 50), Math.max(...ys) + 50];
    LWCharts.lineChart(chart, {
      series, xTicks: ticks, markers: true, xMin: start, xMax: last, minWidthPerPoint: 6, height: 300, ySteps: 10, yDomain,
      yLabel: metric === "price" ? (v) => `R${Math.round(v).toLocaleString(Boord.LOCALE)}`
                                 : (v) => Math.round(v).toLocaleString(Boord.LOCALE),
    });
    // A season is wide (a marker per day), so lineChart's fixed 300px box
    // left a blank band under the plot once it shrank to the card's width -
    // on screen and in the PDF. Its own proportions set the height instead.
    // (Below 720px the card scrolls sideways - see #marketChartExport.)
    const svgEl = chart.querySelector("svg");
    svgEl.removeAttribute("height");
    svgEl.style.height = "auto";
    LWCharts.legend(legendEl, series.map((s) => ({
      label: s.label, color: s.color, marker: s.marker, hollow: s.hollow,
      dash: s.dash || (s.dashed ? "5,4" : ""), muted: s.muted, emphasize: s.emphasize,
    })));
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
