// Minimal dependency-free SVG chart helpers for the admin Analysis tab.
// No vendored charting library in this app - these cover the line/bar
// shapes the dashboard needs without adding an offline-cache dependency.
const LWCharts = (() => {
  const PALETTE = ["#0A2F6B", "#16a34a", "#eab308", "#C8102E", "#7c3aed", "#0891b2", "#ea580c", "#64748b"];

  function svg(tag, attrs = {}, children = []) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    children.forEach((c) => el.appendChild(c));
    return el;
  }

  function text(x, y, str, attrs = {}) {
    const t = svg("text", { x, y, ...attrs });
    t.textContent = str;
    return t;
  }

  function niceMax(v) {
    if (v <= 0) return 1;
    const mag = Math.pow(10, Math.floor(Math.log10(v)));
    const norm = v / mag;
    const step = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10;
    return step * mag;
  }

  function emptyState(container, msg = "No data for this period") {
    container.innerHTML = `<div class="text-sm text-slate-400 p-8 text-center">${msg}</div>`;
  }

  // Light-yellow -> dark-red heat scale (a hand-rolled stand-in for
  // matplotlib's YlOrRd, used by both heatmap() and bubbleMatrix()).
  const HEAT_STOPS = [[255, 255, 229], [255, 237, 160], [254, 178, 76], [240, 59, 32], [128, 0, 38]];
  // Light-blue -> dark-blue, deliberately a different hue from HEAT_STOPS -
  // used for heatmap()'s optional totalColumn so a row total reads as a
  // distinct kind of figure from the per-cell breakdown next to it.
  const TOTAL_STOPS = [[239, 246, 255], [191, 219, 254], [96, 165, 250], [37, 99, 235], [30, 58, 138]];
  function heatColorRGB(t, stops = HEAT_STOPS) {
    const n = stops.length - 1;
    const pos = Math.min(0.999, Math.max(0, t)) * n;
    const i = Math.floor(pos);
    const frac = pos - i;
    const c0 = stops[i], c1 = stops[Math.min(i + 1, n)];
    return [0, 1, 2].map((k) => Math.round(c0[k] + (c1[k] - c0[k]) * frac));
  }
  function textColorFor([r, g, b]) {
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 ? "#1e293b" : "#ffffff";
  }

  // series: [{label, color, points:[{x,y}], emphasize, dashed, muted}]
  // xMin/xMax force the plotted domain (e.g. pinning every chart to the
  // same "starts 1 Aug" left edge) instead of fitting to whichever series
  // happens to have the earliest/latest point.
  function lineChart(container, { series, xLabel = (x) => x, yLabel = (y) => y, height = 260, minWidthPerPoint = 3,
                                    xMin: xMinOverride, xMax: xMaxOverride }) {
    const withPoints = series.filter((s) => s.points && s.points.length);
    if (!withPoints.length) return emptyState(container);
    const allX = withPoints.flatMap((s) => s.points.map((p) => p.x));
    const allY = withPoints.flatMap((s) => s.points.map((p) => p.y));
    const xMin = xMinOverride != null ? Math.min(xMinOverride, ...allX) : Math.min(...allX);
    const xMax = xMaxOverride != null ? Math.max(xMaxOverride, ...allX) : Math.max(...allX);
    const yMax = niceMax(Math.max(...allY, 0));
    const padL = 54, padB = 26, padT = 12, padR = 16;
    const width = Math.max(420, Math.min(1400, (xMax - xMin + 1) * minWidthPerPoint + padL + padR));
    const w = width - padL - padR, h = height - padT - padB;
    const sx = (x) => padL + (xMax > xMin ? ((x - xMin) / (xMax - xMin)) * w : w / 2);
    const sy = (y) => padT + h - (yMax ? (y / yMax) * h : 0);

    const children = [];
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
      const val = (yMax / ySteps) * i;
      const y = sy(val);
      children.push(svg("line", { x1: padL, x2: padL + w, y1: y, y2: y, stroke: "#e2e8f0", "stroke-width": 1 }));
      children.push(text(padL - 8, y + 4, yLabel(val), { "text-anchor": "end", fill: "#94a3b8", style: "font-size:10px" }));
    }
    const labelCount = Math.min(8, Math.max(1, xMax - xMin));
    for (let i = 0; i <= labelCount; i++) {
      const x = xMin + Math.round(((xMax - xMin) * i) / labelCount);
      const px = sx(x);
      children.push(text(px, padT + h + 18, xLabel(x), { "text-anchor": "middle", fill: "#94a3b8", style: "font-size:10px" }));
    }

    withPoints.forEach((s) => {
      const d = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.x).toFixed(1)},${sy(p.y).toFixed(1)}`).join(" ");
      const titleEl = svg("title");
      titleEl.textContent = s.label;
      children.push(svg("path", {
        d, fill: "none", stroke: s.color, "stroke-width": s.emphasize ? 3 : 1.5,
        "stroke-dasharray": s.dashed ? "5,4" : "", opacity: s.muted ? 0.55 : 1,
      }, [titleEl]));
      if (s.emphasize) {
        const last = s.points[s.points.length - 1];
        children.push(svg("circle", { cx: sx(last.x), cy: sy(last.y), r: 4, fill: s.color }));
      }
    });

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                               preserveAspectRatio: "xMinYMin meet" }, children);
    container.innerHTML = "";
    container.appendChild(root);
  }

  // Like lineChart(), but each series is scaled independently to fill
  // 0%(its own min)-100%(its own max) of the chart height, rather than
  // sharing one literal y-axis - lets wildly different-scale metrics (e.g.
  // temp in °C vs. sunshine duration in seconds) overlay for shape/timing
  // comparison without one metric flattening the others. There is
  // therefore no single "correct" unit for a y-axis label once several
  // series are combined, so this draws no gridline value labels - only the
  // x-axis. Real per-point values surface on hover instead: a <path> can
  // only carry one <title> for its whole length (see lineChart()), so
  // real values live on small circle markers plotted every `pointEvery`-th
  // point (plus always the last point), each with its own <title>.
  // series: [{label, color, unit, decimals, points:[{x,y}], emphasize, muted}]
  function normalizedLineChart(container, { series, xLabel = (x) => x, height = 260, minWidthPerPoint = 3,
                                              xMin: xMinOverride, xMax: xMaxOverride, pointEvery = 1 }) {
    const withPoints = series.filter((s) => s.points && s.points.length);
    if (!withPoints.length) return emptyState(container);
    const allX = withPoints.flatMap((s) => s.points.map((p) => p.x));
    const xMin = xMinOverride != null ? Math.min(xMinOverride, ...allX) : Math.min(...allX);
    const xMax = xMaxOverride != null ? Math.max(xMaxOverride, ...allX) : Math.max(...allX);
    const padL = 20, padB = 26, padT = 12, padR = 16;
    const width = Math.max(420, Math.min(1600, (xMax - xMin + 1) * minWidthPerPoint + padL + padR));
    const w = width - padL - padR, h = height - padT - padB;
    const sx = (x) => padL + (xMax > xMin ? ((x - xMin) / (xMax - xMin)) * w : w / 2);

    withPoints.forEach((s) => {
      const ys = s.points.map((p) => p.y);
      s._min = Math.min(...ys);
      s._max = Math.max(...ys);
    });
    const sy = (s, y) => padT + h - (s._max > s._min ? (y - s._min) / (s._max - s._min) : 0.5) * h;

    const children = [];
    for (let i = 0; i <= 4; i++) {
      const y = padT + (h / 4) * i;
      children.push(svg("line", { x1: padL, x2: padL + w, y1: y, y2: y, stroke: "#e2e8f0", "stroke-width": 1 }));
    }
    const labelCount = Math.min(8, Math.max(1, xMax - xMin));
    for (let i = 0; i <= labelCount; i++) {
      const x = xMin + Math.round(((xMax - xMin) * i) / labelCount);
      children.push(text(sx(x), padT + h + 18, xLabel(x), { "text-anchor": "middle", fill: "#94a3b8", style: "font-size:10px" }));
    }

    withPoints.forEach((s) => {
      const d = s.points.map((p, i) => `${i === 0 ? "M" : "L"}${sx(p.x).toFixed(1)},${sy(s, p.y).toFixed(1)}`).join(" ");
      children.push(svg("path", { d, fill: "none", stroke: s.color, "stroke-width": s.emphasize ? 3 : 1.5,
                                   opacity: s.muted ? 0.5 : 1 }));
      s.points.forEach((p, i) => {
        if (i % pointEvery !== 0 && i !== s.points.length - 1) return;
        const titleEl = svg("title");
        titleEl.textContent = `${s.label}: ${p.y.toFixed(s.decimals ?? 1)}${s.unit || ""} (${xLabel(p.x)})`;
        children.push(svg("circle", { cx: sx(p.x), cy: sy(s, p.y), r: 2.5, fill: s.color,
                                       opacity: s.muted ? 0.5 : 0.9 }, [titleEl]));
      });
    });

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                               preserveAspectRatio: "xMinYMin meet" }, children);
    container.innerHTML = "";
    container.appendChild(root);
  }

  // categories: [str,...]; series: [{label, color, values:[num|null,...], colors:[str,...]}]
  // A series' optional `colors` array overrides `color` per-bar (e.g. to
  // highlight one category, like the current season, within a single series).
  // averageLine (optional): {value, label, color} - a dashed horizontal
  // reference line across the whole chart, e.g. a farm-wide average.
  function barChart(container, { categories, series, height = 260, yLabel = (y) => y, groupWidth = 64, averageLine }) {
    const allVals = series.flatMap((s) => s.values.filter((v) => v != null));
    if (averageLine && averageLine.value != null) allVals.push(averageLine.value);
    if (!categories.length || !allVals.length) return emptyState(container);
    const yMax = niceMax(Math.max(...allVals, 0));
    const padL = 54, padB = 34, padT = 12, padR = 16;
    const width = Math.max(420, padL + padR + categories.length * groupWidth);
    const w = width - padL - padR, h = height - padT - padB;
    const catW = w / categories.length;
    const barW = Math.min(28, (catW - 8) / series.length);
    const sy = (y) => padT + h - (yMax ? (y / yMax) * h : 0);

    const children = [];
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
      const val = (yMax / ySteps) * i;
      const y = sy(val);
      children.push(svg("line", { x1: padL, x2: padL + w, y1: y, y2: y, stroke: "#e2e8f0", "stroke-width": 1 }));
      children.push(text(padL - 8, y + 4, yLabel(val), { "text-anchor": "end", fill: "#94a3b8", style: "font-size:10px" }));
    }

    categories.forEach((cat, ci) => {
      const groupX = padL + ci * catW;
      series.forEach((s, si) => {
        const v = s.values[ci];
        if (v == null) return;
        const bx = groupX + (catW - series.length * barW) / 2 + si * barW;
        const by = sy(v);
        const titleEl = svg("title");
        titleEl.textContent = `${s.label}: ${yLabel(v)}`;
        children.push(svg("rect", { x: bx, y: by, width: Math.max(1, barW - 2), height: Math.max(0, padT + h - by),
                                     fill: (s.colors && s.colors[ci]) || s.color, rx: 2 }, [titleEl]));
      });
      children.push(text(groupX + catW / 2, padT + h + 18, cat, { "text-anchor": "middle", fill: "#64748b", style: "font-size:10px" }));
    });

    if (averageLine && averageLine.value != null) {
      const ly = sy(averageLine.value);
      const color = averageLine.color || "#C8102E";
      const lineTitle = svg("title");
      lineTitle.textContent = averageLine.label || `Average: ${yLabel(averageLine.value)}`;
      children.push(svg("line", { x1: padL, x2: padL + w, y1: ly, y2: ly, stroke: color, "stroke-width": 2,
                                   "stroke-dasharray": "6,4" }, [lineTitle]));
      children.push(text(padL + w, ly - 6, averageLine.label || `Avg: ${yLabel(averageLine.value)}`,
                          { "text-anchor": "end", fill: color, style: "font-size:10px;font-weight:600" }));
    }

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                               preserveAspectRatio: "xMinYMin meet" }, children);
    container.innerHTML = "";
    container.appendChild(root);
  }

  // One bar per category, its segments stacked bottom-to-top.
  // categories: [str,...]; series: [{label, color, values:[num|null,...]}]
  function stackedBarChart(container, { categories, series, height = 260, yLabel = (y) => y, barWidth = 56 }) {
    const totals = categories.map((_, ci) => series.reduce((s, ser) => s + (ser.values[ci] || 0), 0));
    if (!categories.length || !totals.some((t) => t > 0)) return emptyState(container);
    const yMax = niceMax(Math.max(...totals, 0));
    const padL = 60, padB = 34, padT = 12, padR = 16;
    const groupWidth = Math.max(barWidth + 24, 80);
    const width = Math.max(420, padL + padR + categories.length * groupWidth);
    const w = width - padL - padR, h = height - padT - padB;
    const catW = w / categories.length;
    const sy = (y) => padT + h - (yMax ? (y / yMax) * h : 0);

    const children = [];
    const ySteps = 4;
    for (let i = 0; i <= ySteps; i++) {
      const val = (yMax / ySteps) * i;
      const y = sy(val);
      children.push(svg("line", { x1: padL, x2: padL + w, y1: y, y2: y, stroke: "#e2e8f0", "stroke-width": 1 }));
      children.push(text(padL - 8, y + 4, yLabel(val), { "text-anchor": "end", fill: "#94a3b8", style: "font-size:10px" }));
    }

    categories.forEach((cat, ci) => {
      const groupX = padL + ci * catW;
      const bx = groupX + (catW - barWidth) / 2;
      let offset = 0;
      series.forEach((s) => {
        const v = s.values[ci];
        if (!v) return;
        const yTop = sy(offset + v), yBottom = sy(offset);
        const titleEl = svg("title");
        titleEl.textContent = `${s.label}: ${yLabel(v)}`;
        children.push(svg("rect", { x: bx, y: yTop, width: barWidth, height: Math.max(0, yBottom - yTop),
                                     fill: s.color }, [titleEl]));
        offset += v;
      });
      children.push(text(groupX + catW / 2, padT + h + 18, cat, { "text-anchor": "middle", fill: "#64748b", style: "font-size:10px" }));
    });

    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                               preserveAspectRatio: "xMinYMin meet" }, children);
    container.innerHTML = "";
    container.appendChild(root);
  }

  // rowLabels x colLabels grid, cell shade = value on a single scale across
  // the whole grid (min = lightest, max = darkest) - built as an HTML table
  // rather than SVG since it's really a colored table, not a plotted shape.
  // values: values[rowIdx][colIdx] = number|null, used for both shading and
  // (unless `text` is given) the displayed label. `text` lets the displayed
  // label differ from what's being shaded on - e.g. "108,215 (30%)" while
  // still shading by the raw kg.
  // totalColumn (optional): {label, values:[num,...], valueFormat} - an
  // extra trailing column shaded on its OWN min/max scale in a different
  // hue (blue, not the main grid's yellow-red) so a row total reads as a
  // distinct figure, not just another cell in the same scale.
  function heatmap(container, { rowLabels, colLabels, values, text: cellText, valueFormat = (v) => v, cellMinWidth = 64, totalColumn }) {
    const flat = values.flat().filter((v) => v != null);
    if (!rowLabels.length || !flat.length) return emptyState(container);
    const min = Math.min(...flat), max = Math.max(...flat);
    const range = max - min || 1;
    const totalFlat = totalColumn ? totalColumn.values.filter((v) => v != null) : [];
    const totalMin = totalFlat.length ? Math.min(...totalFlat) : 0;
    const totalRange = totalFlat.length ? (Math.max(...totalFlat) - totalMin || 1) : 1;
    const totalFormat = totalColumn && totalColumn.valueFormat ? totalColumn.valueFormat : valueFormat;

    let table = '<table style="border-collapse:separate;border-spacing:3px;font-size:12px">';
    table += "<tr><td></td>" + colLabels.map((c) =>
      `<td style="padding:4px 8px;text-align:center;color:#64748b;font-weight:600">${c}</td>`).join("") +
      (totalColumn ? `<td style="padding:4px 8px 4px 16px;text-align:center;color:#64748b;font-weight:600;white-space:nowrap">${totalColumn.label}</td>` : "") +
      "</tr>";
    rowLabels.forEach((r, ri) => {
      table += `<tr><td style="padding:4px 8px;color:#64748b;font-weight:600;white-space:nowrap">${r}</td>`;
      colLabels.forEach((_, ci) => {
        const v = values[ri][ci];
        if (v == null) {
          table += `<td style="min-width:${cellMinWidth}px;padding:8px;text-align:center;color:#cbd5e1;background:#f8fafc;border-radius:4px">-</td>`;
        } else {
          const rgb = heatColorRGB((v - min) / range);
          const label = cellText ? cellText[ri][ci] : valueFormat(v);
          table += `<td style="min-width:${cellMinWidth}px;padding:8px;text-align:center;font-weight:600;border-radius:4px;line-height:1.4;` +
            `background:rgb(${rgb.join(",")});color:${textColorFor(rgb)}">${label}</td>`;
        }
      });
      if (totalColumn) {
        const tv = totalColumn.values[ri];
        if (tv == null) {
          table += `<td style="min-width:${cellMinWidth}px;padding:8px 8px 8px 16px;text-align:center;color:#cbd5e1;background:#f8fafc;border-radius:4px">-</td>`;
        } else {
          const rgb = heatColorRGB((tv - totalMin) / totalRange, TOTAL_STOPS);
          table += `<td style="min-width:${cellMinWidth}px;padding:8px 8px 8px 16px;text-align:center;font-weight:600;border-radius:4px;` +
            `background:rgb(${rgb.join(",")});color:${textColorFor(rgb)}">${totalFormat(tv)}</td>`;
        }
      }
      table += "</tr>";
    });
    table += "</table>";

    container.innerHTML = `<div style="overflow-x:auto">${table}</div>`;
  }

  // rowLabels x colLabels grid of circles, area proportional to value (not
  // radius) so the visual size comparison between cells is honest.
  // values: values[rowIdx][colIdx] = number|null
  // totalColumn (optional): {label, values:[num,...], valueFormat, color} -
  // an extra trailing column, one inline bar per row scaled to its own max,
  // for a per-row grand total alongside the per-season breakdown.
  function bubbleMatrix(container, { rowLabels, colLabels, values, valueFormat = (v) => v, maxBubble = 48, minBubble = 8, totalColumn }) {
    const flat = values.flat().filter((v) => v != null && v > 0);
    if (!rowLabels.length || !flat.length) return emptyState(container);
    const max = Math.max(...flat);
    const totalMax = totalColumn ? Math.max(...totalColumn.values.filter((v) => v != null)) : 0;
    const totalFormat = totalColumn && totalColumn.valueFormat ? totalColumn.valueFormat : valueFormat;

    let html = '<div style="overflow-x:auto"><table style="border-collapse:collapse;font-size:11px">';
    html += "<tr><td></td>" + colLabels.map((c) =>
      `<td style="padding:2px 4px;text-align:center;color:#64748b;font-weight:600">${c}</td>`).join("") +
      (totalColumn ? `<td style="padding:2px 4px 2px 16px;text-align:left;color:#64748b;font-weight:600;white-space:nowrap">${totalColumn.label}</td>` : "") +
      "</tr>";
    rowLabels.forEach((r, ri) => {
      html += `<tr><td style="padding:2px 8px;color:#64748b;font-weight:600;white-space:nowrap;text-align:right">${r}</td>`;
      colLabels.forEach((colLabel, ci) => {
        const v = values[ri][ci];
        html += `<td style="width:60px;height:56px;text-align:center;vertical-align:middle">`;
        if (v != null && v > 0) {
          const size = Math.max(minBubble, Math.sqrt(v / max) * maxBubble);
          const showLabel = size >= 30;
          html += `<div title="${r} · ${colLabel}: ${valueFormat(v)}" style="margin:0 auto;width:${size}px;height:${size}px;` +
            `border-radius:50%;background:#e08e5c;display:flex;align-items:center;justify-content:center;` +
            `color:white;font-size:9px;font-weight:700;line-height:1">${showLabel ? valueFormat(v) : ""}</div>`;
        }
        html += "</td>";
      });
      if (totalColumn) {
        const tv = totalColumn.values[ri];
        const barW = tv != null && totalMax ? Math.round((tv / totalMax) * 110) : 0;
        html += `<td style="padding:2px 4px 2px 16px;vertical-align:middle">` +
          `<div style="display:flex;align-items:center;gap:6px"><div style="height:14px;border-radius:2px;` +
          `background:${totalColumn.color || "#4c6ef5"};width:${barW}px"></div>` +
          `<span style="white-space:nowrap;color:#334155;font-weight:600">${tv != null ? totalFormat(tv) : "-"}</span></div></td>`;
      }
      html += "</tr>";
    });
    html += "</table></div>";
    container.innerHTML = html;
  }

  // One horizontal range-bar per row, e.g. "first pick -> last pick" spans.
  // rows: [{label, start, end, color, annotation}] - start/end share a
  // single x-domain (season_day: 1 = 1 Aug) across all rows, with month
  // gridlines/labels computed from that same anchor.
  function rangeBarChart(container, { rows, rowHeight = 40, barHeight = 20 }) {
    if (!rows.length) return emptyState(container);
    const xMin = 1;
    const xMax = Math.max(...rows.map((r) => r.end), 154);
    // Season-day of the 1st of each month, Aug (day 1) through the
    // following Jan (day 154), on the same fixed non-leap reference year
    // used elsewhere for season-day <-> date conversions.
    const MONTH_TICKS = [[1, "Aug"], [32, "Sep"], [62, "Oct"], [93, "Nov"], [123, "Dec"], [154, "Jan"]]
      .filter(([d]) => d <= xMax + 5);

    const padL = 56, padR = 190, padT = 12, padB = 26;
    const plotH = rows.length * rowHeight;
    const width = 760;
    const w = width - padL - padR;
    const sx = (x) => padL + ((x - xMin) / (xMax - xMin)) * w;

    const children = [];
    MONTH_TICKS.forEach(([d, label]) => {
      const px = sx(d);
      children.push(svg("line", { x1: px, x2: px, y1: padT, y2: padT + plotH, stroke: "#e2e8f0", "stroke-width": 1 }));
      children.push(text(px, padT + plotH + 18, label, { "text-anchor": "middle", fill: "#94a3b8", style: "font-size:10px" }));
    });

    rows.forEach((row, i) => {
      const cy = padT + i * rowHeight + rowHeight / 2;
      children.push(text(padL - 10, cy + 4, row.label, { "text-anchor": "end", fill: "#334155", style: "font-size:12px;font-weight:600" }));
      if (row.start == null || row.end == null) {
        // Reserved row (e.g. the current season before picking starts) -
        // hold the vertical space without a bar spanning a nonexistent range.
        children.push(text(padL + 10, cy + 4, row.annotation,
                            { fill: "#94a3b8", style: "font-size:11px;font-style:italic" }));
        return;
      }
      const x1 = sx(row.start), x2 = sx(row.end);
      const titleEl = svg("title");
      titleEl.textContent = `${row.label}: ${row.annotation}`;
      children.push(svg("rect", { x: x1, y: cy - barHeight / 2, width: Math.max(2, x2 - x1), height: barHeight,
                                   fill: row.color, rx: 3 }, [titleEl]));
      children.push(text(x2 + 10, cy + 4, row.annotation, { fill: "#475569", style: "font-size:11px" }));
    });

    const height = padT + plotH + padB;
    const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width: "100%", height,
                               preserveAspectRatio: "xMinYMin meet" }, children);
    container.innerHTML = "";
    container.appendChild(root);
  }

  function legend(container, items) {
    // items: [{label, color, dashed}]
    container.innerHTML = items.map((it) => `
      <span class="inline-flex items-center gap-1.5 text-xs text-slate-600 mr-3">
        <span style="display:inline-block;width:14px;height:${it.dashed ? "0" : "3px"};margin-top:${it.dashed ? "1px" : "0"};border-top:${it.dashed ? "2px dashed " + it.color : "3px solid " + it.color}"></span>
        ${it.label}
      </span>
    `).join("");
  }

  // Renders a chart container (an <svg>, or an inline-styled HTML table
  // like heatmap()/bubbleMatrix() produce) to a canvas via html2canvas, then
  // embeds that image (below a farm-name + chart-title header) in a
  // one-page PDF and downloads it. html2canvas draws the DOM manually
  // rather than rasterizing an image, which is what an SVG+foreignObject+
  // <img> approach would need - and browsers refuse to read pixels back
  // out of a canvas that was drawn from a foreignObject image ("tainted
  // canvas"), so that simpler approach doesn't work here.
  async function exportPDF(el, { title = "", filename = "chart.pdf" } = {}) {
    if (!window.jspdf || !window.jspdf.jsPDF || !window.html2canvas) {
      alert("PDF export isn't available offline until this page has loaded online at least once.");
      return;
    }
    const canvas = await window.html2canvas(el, {
      backgroundColor: "#ffffff",
      scale: 2,
      // html2canvas otherwise captures relative to the page's current
      // scroll position, not the element itself - since the button is
      // usually clicked after scrolling down to the chart, that silently
      // clips or shifts the bottom of the image off the canvas without
      // these. Negating the current scroll and pinning the capture
      // viewport to the full document forces it to grab the whole element.
      scrollX: -window.scrollX,
      scrollY: -window.scrollY,
      windowWidth: document.documentElement.scrollWidth,
      windowHeight: document.documentElement.scrollHeight,
    });
    const width = canvas.width / 2, height = canvas.height / 2;
    // JPEG, not PNG: these charts are flat colors/text on white, which PNG
    // handles losslessly but at many MB per page at 2x scale - JPEG at high
    // quality is visually indistinguishable here and a fraction of the size.
    const imgData = canvas.toDataURL("image/jpeg", 0.85);

    const farmNameEl = document.getElementById("headerFarmName");
    const farmName = farmNameEl ? farmNameEl.textContent.trim() : "";

    const { jsPDF } = window.jspdf;
    const marginX = 24, marginTop = 20, marginBottom = 24, lineHeight = 18;
    const headerLines = [];
    if (farmName) headerLines.push({ text: farmName, size: 14, color: [10, 47, 107], bold: true });
    if (title) headerLines.push({ text: title, size: 11, color: [71, 85, 105], bold: false });
    const headerH = headerLines.length ? headerLines.length * lineHeight + 8 : 0;

    // Orientation must be derived from the FINAL page dimensions (after
    // margins/header), not the raw chart's width/height - jsPDF silently
    // swaps a custom format array to match whichever orientation you
    // declare, so if the padding pushes the page from landscape-shaped to
    // portrait-shaped (or vice versa) and "orientation" still reflects the
    // pre-padding shape, jsPDF swaps the page dimensions out from under the
    // addImage() call below, clipping the bottom of the image.
    const pageW = width + marginX * 2;
    const pageH = marginTop + headerH + height + marginBottom;
    const pdf = new jsPDF({
      orientation: pageW > pageH ? "landscape" : "portrait",
      unit: "pt",
      format: [pageW, pageH],
    });
    let y = marginTop;
    headerLines.forEach((line) => {
      pdf.setFontSize(line.size);
      pdf.setTextColor(line.color[0], line.color[1], line.color[2]);
      pdf.setFont(undefined, line.bold ? "bold" : "normal");
      pdf.text(line.text, marginX, y);
      y += lineHeight;
    });
    pdf.addImage(imgData, "JPEG", marginX, marginTop + headerH, width, height);
    pdf.save(filename);
  }

  return { lineChart, normalizedLineChart, barChart, stackedBarChart, heatmap, bubbleMatrix, rangeBarChart, legend, exportPDF, PALETTE };
})();
