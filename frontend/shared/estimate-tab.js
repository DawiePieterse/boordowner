// Estimate tab: the owner's own crop estimate, block by block - kg per tree
// judged in the orchard, set against each block's history, times its trees.
// Renders /api/estimate (see backend/routers/estimate.py). Unlike the other
// tabs this one writes: every save is a version the owner can come back to.
//
// Around the editor, three things that never block it:
//   * the pack-out (estimation only) - the version's mix of channels and
//     pack types turned into kg and cartons, following unsaved edits;
//   * the weather model cross-check - the Risk tab's Harvest Forecast next
//     to the estimate, reusing the Risk tab's saved figures where fresh;
//   * similar past seasons - /api/estimate/analogs, loaded separately
//     because it reads decades of weather.
const LWEstimateTab = (() => {
  let _data = null;       // the last /api/estimate payload
  let _lines = [];        // working copy of the shown estimate's block lines
  let _pack = [];         // working copy of its pack-out mix
  let _dirty = false;
  let _editGen = 0;       // bumped on every edit: a reply that lands after one must not wipe it
  let _loadSeq = 0;       // only the newest /api/estimate reply is applied
  let _season = null;     // chosen season, null = the server's current one
  let _estimateId = null; // chosen version, null = the server's pick (most recent)
  let _loadedAt = 0;
  let _bound = false;

  let _fetchRisk = null;  // supplied by owner.js: one shared /api/risk/summary request
  let _risk = null;       // {forecast, at, offline} or {error}, current season only
  let _riskSeq = 0;
  let _analogs = null;    // the last /api/estimate/analogs payload
  let _analogSeason = null;
  let _analogSeq = 0;
  let _analogFailed = false;

  const RISK_CACHE_KEY = "boord_cached_risk";   // the Risk tab's own saved copy
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tonnes = (kg) => (kg == null ? "-" : `${(kg / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} t`);
  const num = (v, nd = 1) => (v == null ? "-" : Number(v).toLocaleString(undefined, { maximumFractionDigits: nd }));
  const pct = (v) => (v == null ? "-" : `${v > 0 ? "+" : ""}${Math.round(v)}%`);
  const fmtDay = (iso) => (iso ? new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" }) : "-");

  function setDirty(on) {
    _dirty = on;
    if (on) _editGen += 1;
    $("estDirty").classList.toggle("hidden", !on);
  }

  function confirmDiscard() {
    return !_dirty || confirm("Discard the unsaved changes to this estimate?");
  }

  function bind({ fetchRisk } = {}) {
    if (_bound) return;
    _bound = true;
    _fetchRisk = fetchRisk || (() => Boord.api("/api/risk/summary", { timeoutMs: 45000 }));

    $("estSeason").addEventListener("change", (e) => {
      if (!confirmDiscard()) { e.target.value = _data.season_year; return; }
      setDirty(false);
      _season = parseInt(e.target.value, 10);
      _estimateId = null;
      load({ force: true, soft: true });
    });
    $("estVersion").addEventListener("change", (e) => {
      if (!confirmDiscard()) { e.target.value = _data.estimate.id; return; }
      setDirty(false);
      _estimateId = parseInt(e.target.value, 10);
      load({ force: true, soft: true });
    });
    $("estNewBtn").addEventListener("click", createVersion);
    $("estStartBtn").addEventListener("click", createVersion);
    $("estSaveBtn").addEventListener("click", save);
    $("estDeleteBtn").addEventListener("click", removeVersion);
    $("estExportBtn").addEventListener("click", exportVersion);
    $("estFillBtn").addEventListener("click", fill);
    $("estAddBlock").addEventListener("change", addBlock);
    $("estName").addEventListener("input", () => setDirty(true));
    $("estNotes").addEventListener("input", () => setDirty(true));
    $("estXcRetry").addEventListener("click", () => loadCrosscheck({ force: true }));

    // Edits in the tables: delegated, so re-rendering rows needs no rebinding.
    $("estRows").addEventListener("input", (e) => {
      const tr = e.target.closest("tr[data-block]");
      if (!tr) return;
      const line = _lines.find((l) => l.block_id === tr.dataset.block);
      const field = e.target.dataset.field;
      if (field === "note") line.note = e.target.value;
      else if (field === "trees") line.trees = Math.max(0, parseInt(e.target.value, 10) || 0);
      else if (field === "kg_per_tree") {
        const v = parseFloat(e.target.value);
        line.kg_per_tree = Number.isFinite(v) && v >= 0 ? v : null;
      }
      setDirty(true);
      updateRowFigures(tr, line);
      renderTotals();
    });
    $("estRows").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-remove]");
      if (!btn) return;
      _lines = _lines.filter((l) => l.block_id !== btn.dataset.remove);
      setDirty(true);
      renderEditor();
    });

    $("estPackRows").addEventListener("input", (e) => {
      const tr = e.target.closest("tr[data-pack-index]");
      if (!tr) return;
      const p = _pack[parseInt(tr.dataset.packIndex, 10)];
      const field = e.target.dataset.field;
      if (field === "kg_per_carton" || field === "share_pct") {
        const v = parseFloat(e.target.value);
        p[field] = Number.isFinite(v) ? v : null;
      } else {
        p[field] = e.target.value;
      }
      setDirty(true);
      renderPackFigures();
    });
    $("estPackRows").addEventListener("click", (e) => {
      const btn = e.target.closest("[data-pack-remove]");
      if (!btn) return;
      _pack.splice(parseInt(btn.dataset.packRemove, 10), 1);
      setDirty(true);
      renderPack();
    });
    $("estPackAddBtn").addEventListener("click", () => {
      _pack.push({ channel: "", pack_type: "", kg_per_carton: null, share_pct: null, note: "" });
      setDirty(true);
      renderPack();
      const inputs = $("estPackRows").querySelectorAll('input[data-field="channel"]');
      if (inputs.length) inputs[inputs.length - 1].focus();
    });

    window.addEventListener("beforeunload", (e) => {
      if (_dirty) { e.preventDefault(); e.returnValue = ""; }
    });
  }

  // force: reload even if fresh. soft: the reload follows the owner's own
  // save/switch, so the weather model and similar seasons keep what they
  // have (still refetched when stale or for another season) - only a
  // deliberate refresh (pull-to-refresh, reconnect) re-runs those.
  async function load({ force = false, soft = false } = {}) {
    const hard = force && !soft;
    if (_dirty) {
      // Unsaved edits are never replaced by a reload - not on a tab flick,
      // not on reconnect or pull-to-refresh. Those still refresh the
      // weather model and similar seasons around the editor.
      if (hard && _data) { loadCrosscheck({ force: true }); loadAnalogs(); }
      return;
    }
    if (!force && _data && Boord.isFresh(_loadedAt)) return;
    const seq = ++_loadSeq;
    const gen = _editGen;
    if (!_data) LWCharts.loadingState($("estKpiGrid"), "Loading estimate...");
    const qs = new URLSearchParams();
    if (_season != null) qs.set("season", _season);
    if (_estimateId != null) qs.set("estimate_id", _estimateId);
    let data;
    try {
      data = await Boord.api(`/api/estimate?${qs}`, { timeoutMs: 30000 });
    } catch (e) {
      if (seq !== _loadSeq) return;
      console.error("Estimate load failed:", e);
      Boord.toast("Could not load the estimate");
      return;
    }
    // A newer load was started, or the owner started typing into the
    // editor while this one was in flight: keep what's on screen.
    if (seq !== _loadSeq || _editGen !== gen) return;
    _data = data;
    _loadedAt = Date.now();
    _season = data.season_year;
    _estimateId = data.estimate ? data.estimate.id : null;
    _lines = data.estimate ? data.estimate.lines.map((l) => ({ ...l })) : [];
    _pack = data.estimate ? data.estimate.pack.map((p) => ({ ...p })) : [];
    setDirty(false);
    render();
    // Neither is awaited: the editor is usable while they load.
    loadCrosscheck({ force: hard });
    if (hard || _analogSeason !== data.season_year) loadAnalogs();
  }

  function refBlock(blockId) {
    return _data.blocks.find((b) => b.block_id === blockId) || {};
  }

  function render() {
    const d = _data;
    const seasons = [d.current_year - 1, d.current_year, d.current_year + 1];
    if (!seasons.includes(d.season_year)) seasons.push(d.season_year);
    $("estSeason").innerHTML = seasons.sort().map((y) =>
      `<option value="${y}" ${y === d.season_year ? "selected" : ""}>${y}${y === d.current_year ? " (current)" : ""}</option>`).join("");
    $("estVersion").innerHTML = d.estimates.length
      ? d.estimates.map((e) => `<option value="${e.id}" ${d.estimate && e.id === d.estimate.id ? "selected" : ""}>${esc(e.name)} - ${Boord.fmtDateTime(e.updated_at)}</option>`).join("")
      : `<option>None yet</option>`;
    $("estVersion").disabled = !d.estimates.length;
    ["estExportBtn", "estDeleteBtn", "estNewBtn"].forEach((id) => { $(id).disabled = !d.estimate; });

    $("estEditor").classList.toggle("hidden", !d.estimate);
    $("estEmpty").classList.toggle("hidden", !!d.estimate);
    $("estEmptySeason").textContent = d.season_year;
    if (d.estimate) {
      $("estName").value = d.estimate.name;
      $("estNotes").value = d.estimate.notes || "";
      renderEditor();
      renderPack();
    } else {
      renderTotals();
    }
    renderProgress();
    renderFarmChart();
    renderAnalogOption();
  }

  // ------------------------------------------------------------------ blocks
  function renderEditor() {
    const d = _data;
    const years = d.history_years.slice(-3);
    const current = d.season_year === d.current_year;
    $("estHead").innerHTML = `<tr class="text-left border-b">
      <th class="p-2">Block</th><th class="p-2">Variety</th><th class="p-2">Trees</th>
      ${years.map((y) => `<th class="p-2 text-right">${y}${y === d.current_year ? "*" : ""}</th>`).join("")}
      <th class="p-2 text-right" title="Average kg/tree over the last 5 seasons">Last 5 avg</th>
      <th class="p-2 text-right" title="Average of the best 5 of the last 10 seasons, kg/tree">Best 5 of 10</th>
      <th class="p-2 text-right" title="Lowest and highest kg/tree in the last 10 seasons">Range</th>
      <th class="p-2">Estimate kg/tree</th>
      <th class="p-2 text-right">vs last</th>
      <th class="p-2 text-right">Estimate kg</th>
      ${current ? `<th class="p-2 text-right">Picked kg</th><th class="p-2 text-right">% picked</th>` : ""}
      <th class="p-2">Note</th><th class="p-2"></th></tr>`;

    $("estRows").innerHTML = _lines.map((l) => {
      const r = refBlock(l.block_id);
      const hist = r.history || {};
      const missing = !_data.blocks.some((b) => b.block_id === l.block_id);
      return `<tr data-block="${esc(l.block_id)}" class="border-b">
        <td class="p-2 font-semibold">${esc(l.block_id)}${missing ? ` <i class="fa-solid fa-circle-info text-slate-400" title="Not in Boord's block register any more"></i>` : ""}</td>
        <td class="p-2">${esc(r.variety || "")}</td>
        <td class="p-2"><input type="number" min="0" step="1" data-field="trees" value="${l.trees}" class="border border-slate-300 rounded-lg p-1.5" style="width:5.5rem"></td>
        ${years.map((y) => {
          const h = hist[y];
          return `<td class="p-2 text-right" title="${h ? `${num(h.kg, 0)} kg` : ""}">${h ? num(h.kg_tree) : "-"}</td>`;
        }).join("")}
        <td class="p-2 text-right">${num(r.avg5_kg_tree)}</td>
        <td class="p-2 text-right">${num(r.best5_kg_tree)}</td>
        <td class="p-2 text-right text-slate-500" style="white-space:nowrap">${r.low_kg_tree == null ? "-" : `${num(r.low_kg_tree)}-${num(r.high_kg_tree)}`}</td>
        <td class="p-2"><input type="number" min="0" step="0.5" data-field="kg_per_tree" value="${l.kg_per_tree == null ? "" : l.kg_per_tree}" class="border border-slate-300 rounded-lg p-1.5" style="width:5.5rem"></td>
        <td class="p-2 text-right" data-out="vs"></td>
        <td class="p-2 text-right font-semibold" data-out="kg"></td>
        ${current ? `<td class="p-2 text-right">${num(r.actual_kg, 0)}</td><td class="p-2 text-right" data-out="picked"></td>` : ""}
        <td class="p-2"><input type="text" maxlength="500" data-field="note" value="${esc(l.note)}" class="border border-slate-300 rounded-lg p-1.5 w-full" style="min-width:10rem"></td>
        <td class="p-2"><button data-remove="${esc(l.block_id)}" class="text-slate-400 hover:text-slate-600" title="Leave this block out"><i class="fa-solid fa-xmark"></i></button></td>
      </tr>`;
    }).join("");
    $("estRows").querySelectorAll("tr[data-block]").forEach((tr) =>
      updateRowFigures(tr, _lines.find((l) => l.block_id === tr.dataset.block)));

    const absent = _data.blocks.filter((b) => !_lines.some((l) => l.block_id === b.block_id));
    $("estAddBlock").innerHTML = `<option value="">Add a block...</option>` +
      absent.map((b) => `<option value="${esc(b.block_id)}">${esc(b.block_id)} ${esc(b.variety || "")}</option>`).join("");
    $("estAddBlock").classList.toggle("hidden", !absent.length);
    renderTotals();
  }

  function lineKg(l) {
    return l.kg_per_tree == null ? null : l.trees * l.kg_per_tree;
  }

  function updateRowFigures(tr, l) {
    const r = refBlock(l.block_id);
    const kg = lineKg(l);
    tr.querySelector('[data-out="kg"]').textContent = num(kg, 0);
    const vs = tr.querySelector('[data-out="vs"]');
    const change = l.kg_per_tree != null && r.last_kg_tree ? (l.kg_per_tree / r.last_kg_tree - 1) * 100 : null;
    vs.textContent = pct(change);
    vs.className = `p-2 text-right ${change == null ? "" : change >= 0 ? "text-green-700" : "text-red-700"}`;
    const picked = tr.querySelector('[data-out="picked"]');
    if (picked) picked.textContent = kg && r.actual_kg != null ? `${Math.round(r.actual_kg / kg * 100)}%` : "-";
  }

  function estimateTotal() {
    return _lines.reduce((sum, l) => sum + (lineKg(l) || 0), 0);
  }

  // Every figure that follows the working estimate's total.
  function renderTotals() {
    const d = _data;
    const total = d.estimate ? estimateTotal() : null;
    const last = d.farm_history.find((h) => h.year === d.season_year - 1);
    const lastKg = last && !last.partial ? last.kg : null;
    const estimated = _lines.filter((l) => l.kg_per_tree != null).length;
    const cards = [
      ["Estimate", d.estimate ? tonnes(total) : "-", ""],
      [`vs ${d.season_year - 1} (${tonnes(lastKg)})`, total && lastKg ? pct((total / lastKg - 1) * 100) : "-",
       total && lastKg ? (total >= lastKg ? "text-green-700" : "text-red-700") : ""],
      ["Blocks estimated", d.estimate ? `${estimated} of ${_lines.length}` : "-", ""],
      d.progress ? ["Picked so far", tonnes(d.progress.actual_kg), ""]
                 : ["Trees", d.estimate ? _lines.reduce((s, l) => s + (l.trees || 0), 0).toLocaleString() : "-", ""],
    ];
    $("estKpiGrid").innerHTML = cards.map(([label, value, color]) => `
      <div class="bg-white rounded-xl shadow p-4">
        <div class="text-xs text-slate-500">${label}</div>
        <div class="text-xl font-bold ${color}">${value}</div>
      </div>`).join("");
    if (d.estimate) {
      $("estTotalRow").innerHTML = `Total: <span class="font-bold">${num(total, 0)} kg</span> (${tonnes(total)})`;
      renderPackFigures();
    }
    renderCrosscheck();
  }

  // ---------------------------------------------------------------- pack-out
  // Twin of routers/estimate.py _packout() - keep the two identical. Lines
  // with no channel yet (half-typed) are left out, as the server would
  // refuse them.
  function packout(totalKg, rows) {
    const usable = rows.filter((r) => (r.channel || "").trim() && Number.isFinite(r.share_pct));
    if (!usable.length) return null;
    const total = totalKg || 0;
    const channels = new Map();
    let packed = 0, notPacked = 0, cartons = 0, allocated = 0;
    const lines = usable.map((r) => {
      const kg = total * r.share_pct / 100;
      const c = r.kg_per_carton ? kg / r.kg_per_carton : null;
      allocated += r.share_pct;
      if (c == null) notPacked += kg; else { packed += kg; cartons += c; }
      const key = r.channel.trim().toLowerCase();
      if (!channels.has(key)) channels.set(key, { channel: r.channel.trim(), share_pct: 0, kg: 0, cartons: null });
      const ch = channels.get(key);
      ch.share_pct += r.share_pct;
      ch.kg += kg;
      if (c != null) ch.cartons = (ch.cartons || 0) + c;
      return { ...r, kg, cartons: c == null ? null : Math.round(c) };
    });
    return {
      lines,
      channels: [...channels.values()].map((c) => ({ ...c, cartons: c.cartons == null ? null : Math.round(c.cartons) })),
      packed_kg: packed, not_packed_kg: notPacked, cartons: Math.round(cartons),
      avg_kg_per_carton: cartons ? packed / cartons : null,
      allocated_pct: allocated, unallocated_pct: 100 - allocated, unallocated_kg: total * (100 - allocated) / 100,
    };
  }

  function renderPack() {
    $("estPackHead").innerHTML = `<tr class="text-left border-b">
      <th class="p-2">Channel</th><th class="p-2">Pack type</th>
      <th class="p-2" title="What one carton takes from the picked fruit, give-away included. Empty = not cartoned (juice, rejects).">Kg/carton</th>
      <th class="p-2">% of picked</th><th class="p-2 text-right">Kg</th><th class="p-2 text-right">Cartons</th>
      <th class="p-2">Note</th><th class="p-2"></th></tr>`;
    $("estPackRows").innerHTML = _pack.map((p, i) => `<tr data-pack-index="${i}" class="border-b">
      <td class="p-2"><input type="text" maxlength="60" list="estPackChannelList" data-field="channel" value="${esc(p.channel)}" class="border border-slate-300 rounded-lg p-1.5" style="width:9rem" placeholder="e.g. Export sea"></td>
      <td class="p-2"><input type="text" maxlength="60" data-field="pack_type" value="${esc(p.pack_type)}" class="border border-slate-300 rounded-lg p-1.5" style="width:7rem" placeholder="e.g. 4.5 kg"></td>
      <td class="p-2"><input type="number" min="0" max="50" step="0.05" data-field="kg_per_carton" value="${p.kg_per_carton == null ? "" : p.kg_per_carton}" class="border border-slate-300 rounded-lg p-1.5" style="width:5.5rem"></td>
      <td class="p-2"><input type="number" min="0" max="100" step="0.1" data-field="share_pct" value="${p.share_pct == null ? "" : p.share_pct}" class="border border-slate-300 rounded-lg p-1.5" style="width:5.5rem"></td>
      <td class="p-2 text-right" data-out="kg"></td>
      <td class="p-2 text-right font-semibold" data-out="cartons"></td>
      <td class="p-2"><input type="text" maxlength="200" data-field="note" value="${esc(p.note)}" class="border border-slate-300 rounded-lg p-1.5 w-full" style="min-width:8rem"></td>
      <td class="p-2"><button data-pack-remove="${i}" class="text-slate-400 hover:text-slate-600" title="Remove this line"><i class="fa-solid fa-xmark"></i></button></td>
    </tr>`).join("") || `<tr><td class="p-2 text-slate-500" colspan="8">No pack-out mix yet - add a line per channel and pack type.</td></tr>`;
    renderPackFigures();
  }

  function renderPackFigures() {
    const total = estimateTotal();
    const po = packout(total, _pack);
    const byIndex = new Map();
    if (po) {
      const usable = _pack.filter((r) => (r.channel || "").trim() && Number.isFinite(r.share_pct));
      usable.forEach((r, i) => byIndex.set(_pack.indexOf(r), po.lines[i]));
    }
    $("estPackRows").querySelectorAll("tr[data-pack-index]").forEach((tr) => {
      const l = byIndex.get(parseInt(tr.dataset.packIndex, 10));
      tr.querySelector('[data-out="kg"]').textContent = l ? num(l.kg, 0) : "-";
      tr.querySelector('[data-out="cartons"]').textContent = l && l.cartons != null ? num(l.cartons, 0) : "-";
    });
    const names = [...new Set(_pack.map((p) => (p.channel || "").trim()).filter(Boolean))];
    $("estPackChannelList").innerHTML = names.map((n) => `<option value="${esc(n)}">`).join("");

    const warn = $("estPackWarn");
    if (!po) {
      warn.textContent = "";
      $("estPackChannels").innerHTML = "";
      return;
    }
    const notes = [];
    let over = false;
    if (!total) notes.push("Fill in kg per tree first - the pack-out is a share of the estimate.");
    else if (_lines.some((l) => l.kg_per_tree == null)) {
      notes.push(`Based on ${_lines.filter((l) => l.kg_per_tree != null).length} of ${_lines.length} blocks estimated.`);
    }
    if (po.unallocated_pct > 0.05) notes.push(`${num(po.unallocated_pct)}% of the fruit (${tonnes(po.unallocated_kg)}) is not in any line.`);
    if (po.unallocated_pct < -0.05) { over = true; notes.push(`The shares add up to ${num(po.allocated_pct)}% - more than all the fruit. Saving is blocked until they come to 100% or less.`); }
    warn.textContent = notes.join(" ");
    warn.className = `text-xs ${over ? "text-red-700" : "text-amber-700"}`;

    $("estPackChannels").innerHTML = `<tr class="text-left border-b"><th class="p-2">By channel</th><th class="p-2 text-right">% of picked</th><th class="p-2 text-right">Kg</th><th class="p-2 text-right">Cartons</th></tr>` +
      po.channels.map((c) => `<tr class="border-b"><td class="p-2">${esc(c.channel)}</td><td class="p-2 text-right">${num(c.share_pct)}%</td><td class="p-2 text-right">${num(c.kg, 0)}</td><td class="p-2 text-right">${c.cartons == null ? "-" : num(c.cartons, 0)}</td></tr>`).join("") +
      `<tr class="font-semibold"><td class="p-2">Total</td><td class="p-2 text-right">${num(po.allocated_pct)}%</td><td class="p-2 text-right">${num(po.packed_kg + po.not_packed_kg, 0)}</td><td class="p-2 text-right">${num(po.cartons, 0)}${po.avg_kg_per_carton ? ` <span class="text-xs text-slate-500">(avg ${num(po.avg_kg_per_carton, 2)} kg)</span>` : ""}</td></tr>`;
  }

  // Refuses what the server would, in words rather than a 422's JSON.
  function packProblem() {
    const seen = new Set();
    let total = 0;
    for (const p of _pack) {
      const ch = (p.channel || "").trim();
      if (!ch) return "Every pack-out line needs a channel (or remove the empty line).";
      if (!Number.isFinite(p.share_pct) || p.share_pct < 0 || p.share_pct > 100) return `Give ${ch} a share between 0 and 100%.`;
      if (p.kg_per_carton != null && !(p.kg_per_carton > 0 && p.kg_per_carton <= 50)) return `${ch}: kg per carton must be more than 0 and at most 50, or empty.`;
      const key = `${ch.toLowerCase()}|${(p.pack_type || "").trim().toLowerCase()}`;
      if (seen.has(key)) return `${ch} ${(p.pack_type || "").trim()} appears twice.`;
      seen.add(key);
      total += p.share_pct;
    }
    if (total > 100.05) return `The pack-out shares add up to ${num(total)}% - more than all the fruit.`;
    return null;
  }

  // ------------------------------------------------------ weather model check
  // Twin of routers/estimate.py _crosscheck() - keep the two identical.
  function crosscheck(total, fav, exp, unf) {
    if (!total || fav == null || exp == null || unf == null) return null;
    const lo = Math.min(fav, exp, unf), hi = Math.max(fav, exp, unf);
    return {
      gap_kg: total - exp,
      gap_pct: exp ? Math.round((total - exp) / exp * 1000) / 10 : null,
      position: total < lo ? "below" : total > hi ? "above" : "within",
    };
  }

  function forecastFigures(f) {
    if (!f || !f.scenarios) return null;
    const s = f.scenarios;
    const fav = s.favorable && s.favorable.predicted_kg, exp = s.expected && s.expected.predicted_kg,
          unf = s.unfavorable && s.unfavorable.predicted_kg;
    if (fav == null || exp == null || unf == null) return null;
    // Settled = the whole window is measured weather. A factor with forecast
    // days left is not: its scenarios still differ (the forecast fades into
    // each scenario's own assumption), even with no "assumed" days.
    return { fav, exp, unf, count: (f.drivers || []).length,
             settled: (f.drivers || []).filter((d) => !d.data_gap && !d.assumed_days && !d.forecast_days).length };
  }

  // The snapshot a save sends: the figures the card is showing, as built.
  function forecastSnapshot() {
    if (!_data || _data.season_year !== _data.current_year || !_risk) return null;
    const f = _risk.forecast, fig = forecastFigures(f);
    if (!fig || !f.built_at || f.current_year !== _data.season_year) return null;
    return { season_year: f.current_year, built_at: f.built_at, favorable_kg: fig.fav, expected_kg: fig.exp,
             unfavorable_kg: fig.unf, live: !f.forecast_unavailable, settled: fig.settled };
  }

  async function loadCrosscheck({ force = false } = {}) {
    const seq = ++_riskSeq;
    const season = _data.season_year;
    renderCrosscheck();
    if (season !== _data.current_year) return;
    const saved = Boord.getCachedJSON(RISK_CACHE_KEY);
    if (saved && saved.data && saved.data.forecast && saved.data.forecast.current_year === season) {
      // The Risk tab's last good copy - shown at once; "offline" is only for
      // a copy served because the server couldn't be reached.
      _risk = { forecast: saved.data.forecast, at: saved.at, offline: false };
      renderCrosscheck();
      if (!force && Boord.isFresh(saved.at)) return;
    } else if (_risk && _risk.forecast && _risk.forecast.current_year !== season) {
      _risk = null;
    }
    $("estXcStatus").innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Working out the weather model...`;
    $("estXcRetry").classList.add("hidden");
    try {
      // The Risk tab's own cache key: one saved copy, whichever tab fetched it.
      const result = await Boord.cachedLoad(RISK_CACHE_KEY, _fetchRisk);
      if (seq !== _riskSeq) return;
      const f = result.data && result.data.forecast;
      if (f && f.current_year === season) {
        _risk = { forecast: f, at: result.at, offline: result.cached };
      } else if (!_risk || !_risk.forecast) {
        // No forecast in the reply (its build failed server-side), or only a
        // saved copy from another season: nothing to show for this one.
        _risk = { error: result.cached ? "offline" : "failed" };
      } else {
        _risk.refreshFailed = true;
      }
    } catch (e) {
      if (seq !== _riskSeq) return;
      console.error("Weather model load failed:", e);
      if (_risk && _risk.forecast) _risk.refreshFailed = true;
      else _risk = { error: Boord.isNetworkError(e) ? "offline" : "failed" };
    }
    renderCrosscheck();
  }

  function renderCrosscheck() {
    if (!_data) return;
    const d = _data;
    const isCurrent = d.season_year === d.current_year;
    const status = $("estXcStatus"), tiles = $("estXcTiles"), bar = $("estXcBar"),
          verdict = $("estXcVerdict"), notes = $("estXcNotes");
    renderXcHistory();
    if (!isCurrent) {
      status.textContent = "";
      tiles.innerHTML = "";
      bar.classList.add("hidden");
      verdict.textContent = `The weather model covers the current season (${d.current_year}) only.` +
        (d.estimates.some((e) => e.forecast) ? " Below: what it said when each version was saved." : "");
      notes.textContent = "";
      return;
    }
    if (!_risk) {
      if (!status.innerHTML) status.textContent = "Loading...";
      return;
    }
    if (_risk.error || !_risk.forecast) {
      status.textContent = "";
      $("estXcRetry").classList.remove("hidden");
      tiles.innerHTML = "";
      bar.classList.add("hidden");
      verdict.textContent = _risk.error === "offline"
        ? "The farm server can't be reached, and there are no saved weather-model figures on this device."
        : "The weather model is unavailable right now - see the Risk tab.";
      notes.textContent = "";
      return;
    }
    const f = _risk.forecast, fig = forecastFigures(f);
    status.textContent = (_risk.refreshFailed ? "Couldn't refresh - showing figures " : "") +
      (_risk.offline ? `saved on this device, worked out ${Boord.describeAge(_risk.at)} (server unreachable)`
                     : `worked out ${Boord.describeAge(_risk.at)}`);
    status.textContent = status.textContent.charAt(0).toUpperCase() + status.textContent.slice(1);
    $("estXcRetry").classList.toggle("hidden", !_risk.refreshFailed && !_risk.offline);
    const reg = f.regression;
    $("estXcFit").textContent = reg
      ? `${reg.n_seasons} seasons, ${f.regression_label}, r = ${reg.r}${reg.resid_sd_kg != null ? `; those seasons sit about ${tonnes(reg.resid_sd_kg)} off the line` : ""}`
      : "not enough seasons to fit";
    const total = d.estimate ? estimateTotal() : null;
    const noteParts = [];
    if (f.no_location) noteParts.push("The farm's location isn't set in Boord's Settings, so no weather reaches the model - its figures are the historical range only.");
    else if (f.forecast_unavailable) noteParts.push("Live weather forecast unavailable - the days ahead use the historical range only.");
    if (!fig) {
      tiles.innerHTML = "";
      bar.classList.add("hidden");
      verdict.textContent = `The weather model has no kg figures yet - it needs at least two past seasons with weather and a crop (it has ${f.reference_season_count || 0}).`;
      notes.textContent = noteParts.join(" ");
      return;
    }
    const tile = (label, value, sub = "", color = "") => `
      <div class="bg-white border border-slate-300 rounded-xl p-2">
        <div class="text-xs text-slate-500">${label}</div>
        <div class="text-xl font-bold ${color}">${value}</div>
        ${sub ? `<div class="text-xs text-slate-500">${sub}</div>` : ""}
      </div>`;
    const lo = Math.min(fig.fav, fig.unf), hi = Math.max(fig.fav, fig.unf);
    tiles.innerHTML = tile("Your estimate", total ? tonnes(total) : "-", "", "text-red-700") +
      (fig.fav === fig.exp && fig.exp === fig.unf
        ? tile("Weather model", tonnes(fig.exp), `${fig.settled} of ${fig.count} factors settled`)
        : tile("Unfavorable", tonnes(fig.unf)) +
          tile("Expected", tonnes(fig.exp), `${fig.settled} of ${fig.count} factors settled`) +
          tile("Favorable", tonnes(fig.fav)));

    // Unfavorable-Favorable band, the Expected tick, and the estimate.
    const xs = [lo, hi, fig.exp].concat(total ? [total] : []);
    const min = Math.min(...xs) * 0.95, max = Math.max(...xs) * 1.05;
    const at = (v) => `${((v - min) / (max - min || 1)) * 100}%`;
    bar.classList.remove("hidden");
    bar.innerHTML =
      `<div style="position:absolute;top:0;bottom:0;left:${at(lo)};width:calc(${at(hi)} - ${at(lo)});background:#cbd5e1;border-radius:9999px"></div>` +
      `<div title="Expected" style="position:absolute;top:-4px;height:16px;width:3px;left:${at(fig.exp)};background:#0A2F6B"></div>` +
      (total ? `<div title="Your estimate" style="position:absolute;top:-4px;height:16px;width:3px;left:${at(total)};background:#C8102E"></div>` : "");

    const xc = crosscheck(total, fig.fav, fig.exp, fig.unf);
    if (!xc) {
      verdict.textContent = "Fill in kg per tree to compare your estimate with the weather model.";
    } else {
      const dir = xc.gap_kg >= 0 ? "above" : "below";
      // The model never predicts past its fitted seasons' best or worst
      // crop; say so only when a scenario is actually sitting on that cap.
      const range = f.fitted_kg_range;
      const capped = range && Math.max(fig.fav, fig.unf) >= range.high;
      const floored = range && Math.min(fig.fav, fig.unf) <= range.low;
      const where = xc.position === "within"
        ? "between the model's worst- and best-weather cases"
        : xc.position === "above"
          ? (capped ? `above even its best-weather case, which is held at the biggest crop in the seasons it was fitted on (${f.regression_label || "on record"}, ${tonnes(range.high)})`
                    : "above even its best-weather case")
          : (floored ? `below even its worst-weather case, which is held at the smallest crop in the seasons it was fitted on (${tonnes(range.low)})`
                     : "below even its worst-weather case");
      verdict.textContent = `Your estimate is ${Math.abs(xc.gap_pct)}% (${tonnes(Math.abs(xc.gap_kg))}) ${dir} the model's Expected, ${where}.`;
    }
    if (d.estimate && _lines.some((l) => l.kg_per_tree == null)) noteParts.push("Not every block is estimated yet - the model is a whole-farm figure.");
    const missing = _data.blocks.filter((b) => b.active && !_lines.some((l) => l.block_id === b.block_id));
    if (d.estimate && missing.length) noteParts.push(`Blocks left out of the estimate: ${missing.map((b) => esc(b.block_id)).join(", ")}.`);
    notes.innerHTML = noteParts.join(" ");
  }

  function renderXcHistory() {
    const d = _data;
    const withFc = d.estimates.filter((e) => e.forecast);
    const el = $("estXcHistory");
    if (!withFc.length) { el.innerHTML = ""; return; }
    const actual = d.season_total && !d.season_total.partial ? d.season_total.kg : null;
    const off = (x) => (actual ? pct((x - actual) / actual * 100) : "");
    el.innerHTML = `<table class="w-full text-sm"><thead><tr class="text-left border-b">
        <th class="p-2">Version</th><th class="p-2">Model as at</th><th class="p-2 text-right">Estimate</th>
        <th class="p-2 text-right">Model Expected (Unf.-Fav.)</th><th class="p-2 text-right">vs model</th>
        ${actual ? `<th class="p-2 text-right">Actual</th><th class="p-2 text-right">Estimate off</th><th class="p-2 text-right">Model off</th>` : ""}
      </tr></thead><tbody>${withFc.map((e) => {
        const f = e.forecast;
        const lo = Math.min(f.favorable_kg, f.unfavorable_kg), hi = Math.max(f.favorable_kg, f.unfavorable_kg);
        return `<tr class="border-b"><td class="p-2">${esc(e.name)}</td><td class="p-2">${Boord.fmtDateTime(f.built_at)}${f.live === false ? " (no live forecast)" : ""}</td>
          <td class="p-2 text-right">${tonnes(e.total_kg)}</td><td class="p-2 text-right" style="white-space:nowrap">${tonnes(f.expected_kg)} (${tonnes(lo)}-${tonnes(hi)})</td>
          <td class="p-2 text-right">${f.gap_pct == null ? "-" : pct(f.gap_pct)}</td>
          ${actual ? `<td class="p-2 text-right">${tonnes(actual)}</td><td class="p-2 text-right">${off(e.total_kg)}</td><td class="p-2 text-right">${off(f.expected_kg)}</td>` : ""}</tr>`;
      }).join("")}</tbody></table>`;
  }

  // ---------------------------------------------------------- similar seasons
  async function loadAnalogs() {
    const seq = ++_analogSeq;
    const season = _data.season_year;
    _analogSeason = season;
    _analogs = null;
    _analogFailed = false;
    renderAnalogOption();
    LWCharts.loadingState($("estAnalogStatus"), "Finding similar seasons...");
    ["estAnalogFactors", "estAnalogHead", "estAnalogRows", "estAnalogSummary"].forEach((id) => { $(id).innerHTML = ""; });
    let result;
    try {
      result = await Boord.cachedLoad(`boord_cached_est_analogs_${season}`,
        () => Boord.api(`/api/estimate/analogs?season=${season}`, { timeoutMs: 45000 }));
    } catch (e) {
      if (seq !== _analogSeq) return;
      console.error("Similar seasons load failed:", e);
      _analogSeason = null;   // try again on the next load
      _analogFailed = true;
      renderAnalogOption();
      $("estAnalogStatus").textContent = Boord.isNetworkError(e)
        ? "Can't reach the farm server, and there's no saved comparison on this device."
        : "Could not work out similar seasons.";
      return;
    }
    if (seq !== _analogSeq) return;
    _analogs = result.data;
    renderAnalogs(result);
    renderAnalogOption();
  }

  function renderAnalogOption() {
    const opt = $("estFillBasis").querySelector('option[value="analog"]');
    if (!opt) return;
    const a = _analogs;
    const ok = a && a.state === "ok" && a.block_basis_years.length && _data && a.season_year === _data.season_year;
    opt.disabled = !ok;
    opt.textContent = ok ? `Similar seasons (${a.block_basis_years.join(", ")})`
      : a ? "Similar seasons (none to compare)" : _analogFailed ? "Similar seasons (unavailable)" : "Similar seasons (loading...)";
    if (!ok && $("estFillBasis").value === "analog") $("estFillBasis").value = "last_kg_tree";
  }

  function renderAnalogs(result) {
    const a = _analogs;
    const status = $("estAnalogStatus");
    const parts = [];
    if (result.cached) parts.push(`Saved comparison from ${Boord.describeAge(result.at)}.`);
    if (a.weather_behind && a.state !== "weather_behind") parts.push(`Weather on file runs to ${fmtDay(a.weather_through)} - opening the Weather or Risk tab fetches newer days.`);
    const messages = {
      no_weather_yet: `Nothing to compare yet: ${a.season_year}'s first weather factor opens ${fmtDay(a.first_window_opens)}.`,
      no_weather: a.no_location ? "The farm's location isn't set in Boord's Settings, so there is no weather to compare."
        : "No weather on file for this season yet - the Weather tab fetches it.",
      weather_behind: `This season's weather windows have opened, but the weather on file stops at ${fmtDay(a.weather_through)} - opening the Weather or Risk tab fetches newer days.`,
      too_early: "Too little of this season's weather is in yet to compare - matching starts once a factor's window has two weeks in.",
      too_few_seasons: "Too few past seasons with both weather and a crop on file to compare.",
      no_spread: "Past seasons' weather doesn't vary on the factors in so far - nothing to rank them by.",
    };
    if (messages[a.state]) parts.unshift(messages[a.state]);
    else parts.unshift(`Compared on weather to ${fmtDay(a.cutoff)}.` +
      (a.target_timing ? ` This season's first pick: ${fmtDay(a.target_timing.first_date)}.` : ""));
    status.textContent = parts.join(" ");

    const f = a.factors.filter((x) => x.weight > 0);
    $("estAnalogFactors").innerHTML = a.factors.map((x) => {
      const state = x.status === "final" ? "closed" : x.status === "partial" ? `so far, to ${x.compared_until}`
        : x.status === "pending" ? "not open yet" : x.status === "too_short" ? `only ${x.observed_days} days in`
        : x.status === "behind" ? "no weather on file yet" : x.status === "no_spread" ? "no spread between seasons" : "no data";
      return `<span class="px-2 rounded-lg bg-slate-100 text-xs" title="${esc(x.window)}${x.weight ? ` · weight ${x.weight}` : ""}">${esc(x.label)}: ${state}${x.target_value != null && x.weight ? ` · ${num(x.target_value)} ${esc(x.unit)}` : ""}</span>`;
    }).join("");
    if (a.state !== "ok") {
      $("estAnalogHead").innerHTML = $("estAnalogRows").innerHTML = $("estAnalogSummary").innerHTML = "";
      return;
    }
    $("estAnalogHead").innerHTML = `<tr class="text-left border-b"><th class="p-2">Season</th><th class="p-2">Closeness</th>
      <th class="p-2 text-right">Crop</th><th class="p-2 text-right" title="Against the average of its own record: the replanted orchard, or the old one">vs its average</th>
      <th class="p-2">Picking start</th><th class="p-2 text-right">Length</th>
      ${f.map((x) => `<th class="p-2 text-right" title="${esc(x.unit)}">${esc(x.label)}</th>`).join("")}</tr>`;
    const zColor = (z) => (Math.abs(z) < 0.5 ? "text-green-700" : Math.abs(z) < 1 ? "text-slate-600" : "text-amber-700");
    $("estAnalogRows").innerHTML = a.analogs.map((r) => `<tr class="border-b ${r.in_top ? "" : "text-slate-400"}">
      <td class="p-2 font-semibold">${r.year}${r.record === "whole_farm" ? ` <span class="text-xs text-slate-500">old orchard</span>` : ""}${r.in_top ? "" : ` <span class="text-xs">block figures only</span>`}</td>
      <td class="p-2">${r.closeness} <span class="text-xs text-slate-500">(${r.distance})</span></td>
      <td class="p-2 text-right">${tonnes(r.kg)}</td>
      <td class="p-2 text-right ${r.vs_avg_pct == null ? "" : r.vs_avg_pct >= 0 ? "text-green-700" : "text-red-700"}">${pct(r.vs_avg_pct)}</td>
      <td class="p-2">${r.timing ? fmtDay(r.timing.start_date) : "-"}</td>
      <td class="p-2 text-right">${r.timing ? `${r.timing.span_days} d` : "-"}</td>
      ${f.map((x) => { const v = r.factors[x.key]; return `<td class="p-2 text-right ${v ? zColor(v.z) : ""}">${v ? num(v.value) : "-"}</td>`; }).join("")}
    </tr>`).join("");
    const sp = a.spread;
    $("estAnalogSummary").innerHTML = sp
      ? `<p>The ${sp.n} closest seasons came in between <span class="font-semibold">${pct(sp.min_pct)}</span> and <span class="font-semibold">${pct(sp.max_pct)}</span> against their own record's average. That spread, not any one of them, is what the weather so far says.</p>` +
        (a.block_basis_years.length ? `<p class="text-xs text-slate-500">"Similar seasons" in Fill uses each block's kg per tree in ${a.block_basis_years.join(", ")}, weighted by closeness.</p>` : "")
      : "";
  }

  // -------------------------------------------------------------- the rest
  function renderProgress() {
    const p = _data.progress;
    const el = $("estProgress");
    el.classList.toggle("hidden", !p);
    if (!p) return;
    const est = _data.estimate ? estimateTotal() : null;
    const share = p.typical_share == null ? null : Math.round(p.typical_share * 100);
    const years = p.shares.map((s) => s.year);
    const span = years.length ? `${years[0]}-${years[years.length - 1]}` : "";
    const parts = [`<div class="font-semibold mb-1">This season so far</div>`,
      `<p>Picked so far: <span class="font-semibold">${tonnes(p.actual_kg)}</span>.</p>`];
    if (share == null) {
      parts.push(`<p class="text-slate-500">No daily-tracked seasons on file to compare the pace with.</p>`);
    } else {
      parts.push(`<p>By this day of the season, the ${years.length} seasons on file (${span}) had picked ${share}% of their crop on average` +
        (est ? `, so an estimate of ${tonnes(est)} means about <span class="font-semibold">${tonnes(est * p.typical_share)}</span> should be in by now.` : ".") + `</p>`);
      if (p.projected_kg != null) {
        parts.push(`<p>At the typical pace, the season comes to about <span class="font-semibold">${tonnes(p.projected_kg)}</span>` +
          (p.projected_low_kg != null ? ` (${tonnes(p.projected_low_kg)} to ${tonnes(p.projected_high_kg)} depending on which past season it follows` +
            (p.range_years.length < p.shares.length
              ? ` - across ${p.range_years.join(", ")}; the others had picked under ${Math.round(p.min_range_share * 100)}% by this day, so a late start like theirs could take it higher).`
              : ").")
           : ".") + `</p>`);
      } else {
        parts.push(`<p class="text-slate-500">Too early to project from the picking: the pace is only used once a typical season is ${Math.round(p.min_projection_share * 100)}% picked by this day. A late or early start throws it off most at the beginning.</p>`);
      }
      parts.push(`<p class="text-xs text-slate-500">Share picked by this day: ${p.shares.map((s) => `${s.year} ${Math.round(s.share * 100)}%`).join(" · ")}</p>`);
    }
    el.innerHTML = parts.join("");
  }

  function renderFarmChart() {
    const d = _data;
    const hist = d.farm_history.slice(-14);
    const cats = hist.map((h) => `${h.year}${h.partial ? "*" : ""}`);
    const values = hist.map((h) => h.kg);
    const colors = hist.map((h) => (h.partial ? "#94a3b8" : "#0A2F6B"));
    if (d.estimate) {
      cats.push(`${d.season_year} est`);
      values.push(estimateTotal());
      colors.push("#C8102E");
    }
    const done = hist.filter((h) => !h.partial).slice(-5);
    LWCharts.barChart($("estFarmChart"), {
      categories: cats,
      series: [{ label: "Farm total", color: "#0A2F6B", colors, values }],
      yLabel: (v) => `${Math.round(v / 1000)} t`,
      groupWidth: 48,
      averageLine: done.length ? { value: done.reduce((s, h) => s + h.kg, 0) / done.length,
                                   label: `Last ${done.length} avg` } : undefined,
    });
  }

  function fill() {
    const basis = $("estFillBasis").value;
    const change = parseFloat($("estFillPct").value) || 0;
    const onlyEmpty = $("estFillScope").value === "empty";
    const baseFor = (l) => (basis === "analog"
      ? (_analogs && _analogs.blocks ? _analogs.blocks[l.block_id] : null)
      : refBlock(l.block_id)[basis]);
    let filled = 0;
    _lines.forEach((l) => {
      if (onlyEmpty && l.kg_per_tree != null) return;
      const base = baseFor(l);
      if (base == null) return;
      l.kg_per_tree = Math.round(base * (1 + change / 100) * 10) / 10;
      filled += 1;
    });
    if (!filled) { Boord.toast("No blocks had a figure to fill from"); return; }
    setDirty(true);
    renderEditor();
  }

  function addBlock(e) {
    const r = refBlock(e.target.value);
    if (!r.block_id) return;
    _lines.push({ block_id: r.block_id, trees: r.trees || 0, kg_per_tree: null, note: "" });
    setDirty(true);
    renderEditor();
  }

  async function save() {
    if (!_data.estimate) return;
    const name = $("estName").value.trim();
    if (!name) { Boord.toast("Give the estimate a name"); return; }
    const problem = packProblem();
    if (problem) { Boord.toast(problem); return; }
    const btn = $("estSaveBtn");
    btn.disabled = true;
    try {
      const body = {
        name, notes: $("estNotes").value,
        lines: _lines.map(({ block_id, trees, kg_per_tree, note }) => ({ block_id, trees, kg_per_tree, note: note || "" })),
        pack: _pack.map(({ channel, pack_type, kg_per_carton, share_pct, note }) =>
          ({ channel: channel.trim(), pack_type: (pack_type || "").trim(), kg_per_carton: kg_per_carton || null,
             share_pct, note: (note || "").trim() })),
      };
      const fc = forecastSnapshot();
      if (fc) body.forecast = fc;
      const gen = _editGen;
      await Boord.api(`/api/estimate/${_data.estimate.id}`, { method: "PUT", body });
      if (_editGen !== gen) {
        // Typed into while saving: what was sent is saved, the newer edits
        // stay on screen, unsaved.
        Boord.toast("Saved - you've made more changes since; save again to keep them");
        return;
      }
      setDirty(false);
      Boord.toast("Estimate saved");
      await load({ force: true, soft: true });
    } catch (e) {
      console.error("Estimate save failed:", e);
      Boord.toast(typeof e.detail === "string" && e.detail ? `Not saved: ${e.detail}` : "Could not save the estimate");
    } finally {
      btn.disabled = false;
    }
  }

  async function createVersion() {
    if (!confirmDiscard()) return;
    const from = _data.estimate;
    const name = prompt(from ? `Name for the new version (copied from "${from.name}")` : `Name for the ${_data.season_year} estimate`,
                        new Date().toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }));
    if (name == null) return;
    try {
      const body = { season_year: _data.season_year, name, copy_from_id: from ? from.id : null };
      const fc = forecastSnapshot();
      if (fc) body.forecast = fc;
      const est = await Boord.api("/api/estimate", { method: "POST", body });
      if (!from && est.pack_copied_from) {
        const src = est.pack_copied_from;
        Boord.toast(`Pack-out mix copied from "${src.name}" (${src.season_year})`);
      }
      _estimateId = est.id;
      setDirty(false);
      await load({ force: true, soft: true });
    } catch (e) {
      console.error("Estimate create failed:", e);
      Boord.toast("Could not start a new estimate");
    }
  }

  async function removeVersion() {
    const est = _data.estimate;
    if (!est || !confirm(`Delete the estimate "${est.name}"? This can't be undone.`)) return;
    try {
      await Boord.api(`/api/estimate/${est.id}`, { method: "DELETE" });
      _estimateId = null;
      setDirty(false);
      await load({ force: true, soft: true });
    } catch (e) {
      console.error("Estimate delete failed:", e);
      Boord.toast("Could not delete the estimate");
    }
  }

  async function exportVersion() {
    const est = _data.estimate;
    if (!est) return;
    if (_dirty) Boord.toast("Exporting the last saved version - save first to include your changes");
    try {
      const blob = await Boord.api(`/api/estimate/${est.id}/export`, { timeoutMs: 30000 });
      Boord.downloadBlob(blob, `Crop_Estimate_${est.season_year}.xlsx`);
    } catch (e) {
      console.error("Estimate export failed:", e);
      Boord.toast("Could not build the workbook");
    }
  }

  return { bind, load };
})();
