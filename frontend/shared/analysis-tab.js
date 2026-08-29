// Analysis tab (historical 2020-2025 vs current season): season pace,
// per-block/variety yield, season length, monthly volume, PDF export.
// Shared between the admin app (/api/analysis/summary, JWT auth) and the
// Owner View (/api/owner-view/analysis, token auth) - identical markup
// (same element IDs) and identical figures, so this one module renders
// both rather than the two screens carrying their own copies to drift.
const LWAnalysisTab = (() => {
  let _data = null;
  let _bound = false;

  function bind() {
    // The admin app calls its bind* helpers from showApp(), which re-runs on
    // every sign-in without reloading the page (logout() just swaps the
    // visible div). Without this guard a sign-out/sign-in cycle would stack a
    // second delegated listener on #tab-analysis, and one click on a chart's
    // PDF button would rasterize and download the same file twice.
    if (_bound) return;
    _bound = true;

    document.getElementById("blockYieldMetric").addEventListener("change", () => {
      if (_data) renderBlockYield(_data);
    });
    document.getElementById("yieldPerTreeMetric").addEventListener("change", () => {
      if (_data) renderYieldPerTreeHeatmap(_data);
    });
    document.getElementById("varietyFilter").addEventListener("change", () => {
      if (_data) renderVarietyYield(_data);
    });

    // Delegated so it keeps working after any chart's own re-render, and
    // covers every "download as PDF" button with one listener.
    document.getElementById("tab-analysis").addEventListener("click", async (e) => {
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

  // fetchSummary: () => Promise<data> - each screen supplies its own call
  // (admin: Boord.api with a bearer token; owner: Boord.api with the link's key).
  // onAuthError: called ONLY on a genuine 401/403 - admin signs out, owner
  // shows the "link isn't valid" screen. Anything else (a 500, a malformed
  // response) must fall through to the toast: signing an admin out or
  // telling an owner their link is dead because the server hit a bug would
  // be both wrong and, for the owner, a direct contradiction of the manual's
  // promise that that message never means a server problem.
  async function load(fetchSummary, { onAuthError } = {}) {
    let data;
    try {
      data = await fetchSummary();
    } catch (e) {
      if (Boord.isNetworkError(e)) { Boord.setOffline(true); return; }
      if (Boord.isAuthError(e) && onAuthError) { onAuthError(e); return; }
      console.error("Analysis load failed:", e);
      Boord.toast("Could not load analysis data");
      return;
    }
    Boord.setOffline(false);
    _data = data;
    renderAnalysisKpis(data);
    renderSeasonPace(data);
    renderBlockYield(data);
    renderBlockSeasonBubble(data);
    renderYieldPerTreeHeatmap(data);
    renderVarietyYield(data);
    renderSeasonLength(data);
    renderMonthlyHeatmap(data);
  }

  function renderAnalysisKpis(data) {
    const pctText = data.pct_vs_average == null ? "-" : `${data.pct_vs_average > 0 ? "+" : ""}${data.pct_vs_average}%`;
    const pctColor = data.pct_vs_average == null ? "" : data.pct_vs_average >= 0 ? "text-green-700" : "text-red-700";
    const cards = [
      ["Season to Date", `${data.season_to_date_kg.toLocaleString()} kg`, ""],
      ["vs 5-Yr Average Pace", pctText, pctColor],
      ["Current Season", data.current_year, ""],
      ["Years of History", `${data.historical_years.length} seasons`, ""],
    ];
    document.getElementById("analysisKpiGrid").innerHTML = cards.map(([label, value, color]) => `
      <div class="bg-white rounded-xl shadow p-4">
        <div class="text-xs text-slate-500">${label}</div>
        <div class="text-xl font-bold ${color}">${value}</div>
      </div>
    `).join("");
  }

  // season_day -> real date, using a fixed non-leap reference year purely for
  // formatting (the actual year never appears on the axis). season_day is
  // days since 1 Aug (1 = 1 Aug), matching the backend's season-day anchor.
  function _seasonDayToDate(seasonDay) {
    return new Date(2001, 7, seasonDay); // month 7 = August (0-indexed)
  }
  function _seasonDayLabel(seasonDay) {
    return _seasonDayToDate(seasonDay).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }

  // One distinct color per year - navy reserved for the current season so it
  // always reads as "the one that matters", historical years cycling through
  // the rest of the palette so each one is visually distinguishable.
  function _yearColor(year, data) {
    if (year === data.current_year) return "#0A2F6B";
    const palette = LWCharts.PALETTE.slice(1);
    const idx = data.historical_years.indexOf(year);
    return palette[(idx < 0 ? 0 : idx) % palette.length];
  }

  function renderSeasonPace(data) {
    const years = data.season_pace.years;
    const series = years.map((y) => ({
      label: `${y.year}`,
      color: _yearColor(y.year, data),
      points: y.points.map((p) => ({ x: p.season_day, y: p.cumulative_kg })),
      emphasize: y.is_current,
    }));
    if (data.season_pace.average.length) {
      series.push({
        label: "5-Yr Average", color: "#0f172a",
        points: data.season_pace.average.map((p) => ({ x: p.season_day, y: p.cumulative_kg })),
        dashed: true, emphasize: true,
      });
    }
    LWCharts.lineChart(document.getElementById("seasonPaceChart"), {
      series, xLabel: _seasonDayLabel, yLabel: (y) => Math.round(y).toLocaleString(), xMin: 1,
    });
    LWCharts.legend(document.getElementById("seasonPaceLegend"),
      series.map((s) => ({ label: s.label === `${data.current_year}` ? `${s.label} (current)` : s.label,
                            color: s.color, dashed: s.dashed })));
  }

  function renderBlockYield(data) {
    const metric = document.getElementById("blockYieldMetric").value; // "kg_ha" | "kg_tree"
    const metricLabel = metric === "kg_tree" ? "Kg/Tree" : "Kg/Ha";
    const blocks = data.block_yield;

    const rows = blocks.map((b) => {
      const current = b.by_year[data.current_year] ? b.by_year[data.current_year][metric] : null;
      const histVals = Object.entries(b.by_year)
        .filter(([y]) => parseInt(y, 10) !== data.current_year)
        .map(([, v]) => v[metric]).filter((v) => v != null);
      const historicalAvg = histVals.length
        ? Math.round((histVals.reduce((s, v) => s + v, 0) / histVals.length) * 10) / 10 : null;
      const pct = current != null && historicalAvg
        ? Math.round(((current - historicalAvg) / historicalAvg) * 1000) / 10 : null;
      return { block: b, current, historicalAvg, pct };
    });

    const validCurrent = rows.map((r) => r.current).filter((v) => v != null);
    const farmAvg = validCurrent.length
      ? Math.round((validCurrent.reduce((s, v) => s + v, 0) / validCurrent.length) * 10) / 10 : null;

    LWCharts.barChart(document.getElementById("blockYieldChart"), {
      categories: blocks.map((b) => b.block_id),
      series: [
        { label: "This Season", color: "#0A2F6B", values: rows.map((r) => r.current) },
        { label: "Historical Avg", color: "#94a3b8", values: rows.map((r) => r.historicalAvg) },
      ],
      averageLine: farmAvg != null
        ? { value: farmAvg, label: `Farm Avg This Season: ${farmAvg} ${metricLabel}`, color: "#C8102E" } : null,
    });

    document.getElementById("blockYieldCol1").textContent = `This Season ${metricLabel}`;
    document.getElementById("blockYieldCol2").textContent = `Historical Avg ${metricLabel}`;

    document.getElementById("blockYieldRows").innerHTML = rows.map(({ block: b, current, historicalAvg, pct }) => {
      const pctClass = pct == null ? "text-slate-400" : pct >= 0 ? "text-green-700" : "text-red-700";
      const pctText = pct == null ? "-" : `${pct > 0 ? "+" : ""}${pct}%`;
      const flag = b.estimated
        ? ` <i class="fa-solid fa-circle-info text-slate-300" title="Historical figures for this block are estimated - split from a combined pre-app record"></i>`
        : "";
      return `
      <tr class="border-b">
        <td class="p-2">${b.name || b.block_id}${flag}</td>
        <td class="p-2">${b.variety || "-"}</td>
        <td class="p-2">${current ?? "-"}</td>
        <td class="p-2">${historicalAvg ?? "-"}</td>
        <td class="p-2 font-semibold ${pctClass}">${pctText}</td>
      </tr>`;
    }).join("") || `<tr><td class="p-2 text-slate-400" colspan="5">No data</td></tr>`;
  }

  function renderBlockSeasonBubble(data) {
    const years = [...data.historical_years, data.current_year];
    const blockTotal = (b) => Object.values(b.by_year).reduce((s, v) => s + (v.kg || 0), 0);
    const blocks = [...data.block_yield].sort((a, b) => blockTotal(b) - blockTotal(a));
    const kFormat = (v) => (v >= 1000 ? `${Math.round(v / 1000)}K` : Math.round(v));
    LWCharts.bubbleMatrix(document.getElementById("blockSeasonBubbleChart"), {
      rowLabels: blocks.map((b) => b.name || b.block_id),
      colLabels: years.map(String),
      values: blocks.map((b) => years.map((y) => (b.by_year[y] ? b.by_year[y].kg : null))),
      valueFormat: kFormat,
      totalColumn: {
        label: "Total Harvest", color: "#4c6ef5", valueFormat: kFormat,
        values: blocks.map(blockTotal),
      },
    });
  }

  function renderYieldPerTreeHeatmap(data) {
    const metric = document.getElementById("yieldPerTreeMetric").value; // "kg_tree" | "kg_ha"
    const metricLabel = metric === "kg_ha" ? "Hectare" : "Tree";
    document.getElementById("yieldPerTreeTitle").textContent = `Yield per ${metricLabel} by Block and Season`;
    const years = [...data.historical_years, data.current_year];
    const blocks = data.block_yield;
    LWCharts.heatmap(document.getElementById("yieldPerTreeHeatmap"), {
      rowLabels: blocks.map((b) => b.name || b.block_id),
      colLabels: years.map(String),
      values: blocks.map((b) => years.map((y) => (b.by_year[y] ? b.by_year[y][metric] : null))),
      valueFormat: (v) => v.toFixed(1),
    });
  }

  function renderVarietyYield(data) {
    const varieties = data.variety_yield;
    const years = [...data.historical_years, data.current_year];
    const categories = years.map(String);

    const select = document.getElementById("varietyFilter");
    // Rebuilt whenever the set of varieties actually changes (rather than
    // once ever), so a newly planted variety shows up without a page reload
    // and - more importantly - a variety that has disappeared from the data
    // can't leave a dangling selection behind. The user's current choice is
    // preserved across the rebuild when it still exists.
    const names = varieties.map((v) => v.variety);
    const signature = names.join("|");
    if (select.dataset.varieties !== signature) {
      const previous = select.value;
      select.innerHTML = `<option value="">All Varieties</option>` +
        names.map((n) => `<option value="${n}">${n}</option>`).join("");
      select.dataset.varieties = signature;
      select.value = names.includes(previous) ? previous : "";
    }
    const idx = varieties.findIndex((v) => v.variety === select.value);
    const selected = idx >= 0 ? varieties[idx] : null;  // null => "All Varieties"

    const chartEl = document.getElementById("varietyYieldChart");
    const exportEl = document.getElementById("varietyYieldExport");
    exportEl.querySelectorAll(".variety-legend").forEach((el) => el.remove());

    if (!selected) {
      const series = varieties.map((v, i) => ({
        label: v.variety,
        color: LWCharts.PALETTE[i % LWCharts.PALETTE.length],
        values: years.map((y) => (v.by_year[y] && v.by_year[y].kg_tree != null ? v.by_year[y].kg_tree : 0)),
      }));
      LWCharts.stackedBarChart(chartEl, { categories, series, yLabel: (y) => y.toFixed(1) });
      const legendHost = document.createElement("div");
      legendHost.className = "mt-2 variety-legend";
      LWCharts.legend(legendHost, series.map((s) => ({ label: s.label, color: s.color })));
      exportEl.appendChild(legendHost);
    } else {
      LWCharts.barChart(chartEl, {
        categories,
        series: [{
          label: selected.variety, color: LWCharts.PALETTE[idx % LWCharts.PALETTE.length],
          values: years.map((y) => (selected.by_year[y] ? selected.by_year[y].kg_tree : null)),
        }],
        yLabel: (y) => y.toFixed(1),
      });
    }
  }

  function renderSeasonLength(data) {
    const rows = [...data.season_length].sort((a, b) => b.year - a.year).map((y) => ({
      label: `${y.year}`,
      start: y.first_day,
      end: y.last_day,
      color: y.is_current ? "#0A2F6B" : "#e08e5c",
      annotation: y.first_day != null ? `${y.span_days}d span, ${y.pick_days} pick days` : "No picking yet",
    }));
    LWCharts.rangeBarChart(document.getElementById("seasonLengthChart"), { rows });
  }

  const MONTH_ORDER = ["Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul"];

  function renderMonthlyHeatmap(data) {
    // Latest season at top; a year with no by_month entries yet (the current
    // season before picking starts) still gets its row, just empty - see the
    // hasData() checks below.
    const monthly = [...data.monthly].sort((a, b) => b.year - a.year);
    const hasData = (y) => Object.keys(y.by_month).length > 0;

    const present = new Set();
    monthly.forEach((y) => Object.keys(y.by_month).forEach((m) => present.add(m)));
    const colLabels = MONTH_ORDER.filter((m) => present.has(m));
    const values = monthly.map((y) => colLabels.map((m) => (y.by_month[m] ? y.by_month[m].kg : null)));
    const cellText = monthly.map((y) => colLabels.map((m) => {
      const cell = y.by_month[m];
      if (!cell) return null;
      return `${Math.round(cell.kg).toLocaleString()}<br><span style="opacity:.75;font-size:10px">${cell.pct}%</span>`;
    }));
    LWCharts.heatmap(document.getElementById("monthlyHeatmap"), {
      rowLabels: monthly.map((y) => `${y.year}`), colLabels, values, text: cellText, cellMinWidth: 84,
      valueFormat: (v) => Math.round(v).toLocaleString(),
      totalColumn: {
        label: "Total Kg (100%)",
        values: monthly.map((y) => (hasData(y) ? y.total_kg : null)),
        valueFormat: (v) => Math.round(v).toLocaleString(),
      },
    });
  }

  return { bind, load };
})();
