// Ask panel: a question box that sends a tab's figures to the AI model set
// up on the farm server (backend/ai.py, routers/ai.py) and streams the
// answer in. Ported from the Weather Compare app's "Ask about this
// comparison", cloud only, with the key on the server - so there is nothing
// to set up per device, and no key in any browser.
//
// A tab mounts one with LWAsk.create({...}) and supplies:
//   context()   -> {body, key} | null: what /api/ai/ask is sent besides the
//                  question, and a key naming what is being asked about
//                  (a new key starts a new conversation)
//   questions() -> the suggested-question chips for what is on screen
//   actions     -> extra buttons under an answer, e.g. "Add to notes"
//   privacy     -> (provider) => the line saying what asking sends away
//   followUps   -> the follow-up chips under an answer (default FOLLOW_UPS)
//   notesQuestions() -> chips for "Ask the farm notes" mode (optional)
//   onStatus(s) -> called with /api/ai/status whenever the panel (re)loads
//                  it, so the tab can show its own AI buttons from the one copy
// It never writes anything itself - an answer is only read, copied, or
// handed to an action the owner presses.
//
// askWith({endpoint, body, key, question, shownAs}) sends the same panel
// somewhere else on the server with its own body - the Estimate tab's
// "What changed?" (/api/ai/compare) - and follow-ups carry on from there.
// With Boord Notes linked (status.notes), a toggle under the box sends the
// question to Notes' own Ask (/api/ai/notes) instead, and the notes it
// used are listed under the answer.
const LWAsk = (() => {
  const FOLLOW_UPS = ["Why?", "Say that in fewer words", "Break that down by block", "Show the numbers behind that"];
  const PROSE_REMINDER = "Please answer in plain prose or a short bullet list, not JSON or code.";
  const MAX_QUESTION = 500;
  // Until the first line arrives: the server builds the summary (the
  // similar-seasons scan reads decades of weather) and the model starts.
  const FIRST_BYTE_MS = 90000;

  let _status = null;          // /api/ai/status once it has answered
  let _statusLoad = null;

  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  function loadStatus() {
    if (_status) return Promise.resolve(_status);
    if (!_statusLoad) {
      _statusLoad = Boord.api("/api/ai/status")
        .then((s) => { _status = s; return s; })
        .catch(() => null)                       // offline: asked again next time
        .finally(() => { _statusLoad = null; });
    }
    return _statusLoad;
  }

  // Headings, bullet and numbered lists, bold, italics and code - what the
  // models actually write. Everything is escaped first.
  function renderMarkdown(md) {
    const inline = (s) => esc(s)
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[\s(])\*(\S(?:[^*\n]*\S)?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
      .replace(/(^|[\s(])_(\S(?:[^_\n]*\S)?)_(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>");
    const out = [];
    let list = null;
    let para = [];
    const flushPara = () => { if (para.length) { out.push(`<p>${para.map(inline).join(" ")}</p>`); para = []; } };
    const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of md.replace(/\r/g, "").split("\n")) {
      const line = raw.trim();
      const bullet = /^([-*•]|\d+[.)])\s+(.*)$/.exec(line);
      const heading = /^#{1,6}\s+(.*)$/.exec(line);
      if (!line) { flushPara(); closeList(); continue; }
      if (bullet) {
        flushPara();
        const kind = /^\d/.test(bullet[1]) ? "ol" : "ul";
        if (list !== kind) { closeList(); list = kind; out.push(`<${kind}>`); }
        out.push(`<li>${inline(bullet[2])}</li>`);
        continue;
      }
      closeList();
      if (heading) { flushPara(); out.push(`<h4>${inline(heading[1])}</h4>`); continue; }
      para.push(line);
    }
    flushPara();
    closeList();
    return out.join("");
  }

  // The answer as plain text, for the clipboard and the notes field.
  function plainText(md) {
    return md.replace(/\r/g, "")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/\*\*([^*]+)\*\*/g, "$1")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/^\s*[*•]\s+/gm, "- ")
      .trim();
  }

  // Cheap checks on what came back. `retry` when the model echoed the data
  // instead of answering; `note` when it names a season or block that was
  // not in what it was sent - the usual sign of an invented figure.
  function checkAnswer(text, check) {
    const retry = /[{}]/.test(text) || /"\w+"\s*:/.test(text);
    const notes = [];
    if (check) {
      const years = new Set(check.years || []);
      const strays = [...new Set((text.match(/\b(?:19|20)\d\d\b/g) || []).map(Number))].filter((y) => !years.has(y));
      if (strays.length) notes.push(`Mentions ${strays.join(", ")}, which ${strays.length === 1 ? "isn't a season" : "aren't seasons"} on file`);
      // Only a check that lists blocks (the Estimate tab's) can vouch for them.
      if (check.blocks) {
        const blocks = new Set(check.blocks.map((b) => String(b).toLowerCase()));
        const named = [...new Set([...text.matchAll(/\bblocks?\s+(\d[\w-]*)/gi)].map((m) => m[1].toLowerCase()))];
        const unknown = named.filter((b) => !blocks.has(b));
        if (unknown.length) notes.push(`names block ${unknown.join(", ")}, which isn't in this estimate`);
      }
    }
    const note = notes.length ? `${notes.join("; ")} - check this against the table.` : null;
    return { retry, note: note && note[0].toUpperCase() + note.slice(1) };
  }

  function create({ el, title = "Ask about this", context, questions, actions = [], placeholder = "",
                    notesQuestions = null, onStatus = null, privacy = null, followUps = FOLLOW_UPS }) {
    let _history = [];         // [{q, a}] about the current key
    let _key = null;
    let _controller = null;
    let _answer = "";
    let _chipSig = "";
    let _route = null;         // the route an askWith() set; follow-ups keep it, a new question clears it

    el.innerHTML = `
      <div class="flex flex-wrap items-center justify-between gap-2">
        <div class="font-semibold"><i class="fa-solid fa-wand-magic-sparkles text-slate-400"></i> ${esc(title)}</div>
        <div data-ask="engine" class="text-xs text-slate-400"></div>
      </div>
      <div data-ask="setup" class="text-xs text-slate-500 hidden"></div>
      <div data-ask="body" class="space-y-2 hidden">
        <div data-ask="chips" class="flex flex-wrap gap-2"></div>
        <form data-ask="form" class="flex gap-2">
          <input data-ask="input" type="text" maxlength="${MAX_QUESTION}" enterkeyhint="send" class="border border-slate-300 rounded-lg p-2 text-sm flex-1 min-w-0" placeholder="${esc(placeholder)}">
          <button type="submit" data-ask="go" class="px-3 py-2 bg-slate-200 rounded-lg text-sm">Ask</button>
          <button type="button" data-ask="stop" class="px-3 py-2 bg-white border border-slate-300 rounded-lg text-sm hidden">Stop</button>
        </form>
        <label data-ask="notesWrap" class="text-xs text-slate-500 hidden"><input type="checkbox" data-ask="notesMode"> Ask the farm notes instead (Boord Notes)</label>
        <div data-ask="status" class="text-xs text-slate-500"></div>
        <div data-ask="answer" class="ai-answer text-sm hidden"></div>
        <div data-ask="sources" class="text-xs text-slate-500 hidden"></div>
        <div data-ask="note" class="text-xs text-amber-700"></div>
        <div data-ask="tools" class="flex flex-wrap gap-2 hidden"></div>
        <div data-ask="privacy" class="text-xs text-slate-400"></div>
      </div>`;
    const part = (name) => el.querySelector(`[data-ask="${name}"]`);
    const input = part("input");

    function setBusy(on) {
      part("go").disabled = on;
      part("go").classList.toggle("hidden", on);
      part("stop").classList.toggle("hidden", !on);
      part("chips").querySelectorAll("button").forEach((b) => { b.disabled = on; });
      part("tools").querySelectorAll("button").forEach((b) => { b.disabled = on; });
    }

    function chip(label, attr) {
      return `<button type="button" ${attr}="${esc(label)}" class="px-3 py-1 bg-slate-100 hover:bg-slate-200 rounded-full text-xs">${esc(label)}</button>`;
    }

    function notesMode() {
      return !!(part("notesMode").checked && !part("notesWrap").classList.contains("hidden"));
    }

    function renderChips() {
      const source = notesMode() && notesQuestions ? notesQuestions : questions;
      const qs = (source() || []).slice(0, 6);
      const sig = qs.join("|");
      if (sig === _chipSig) return;
      _chipSig = sig;
      part("chips").innerHTML = qs.map((q) => chip(q, "data-q")).join("");
    }

    function renderTools() {
      const tools = part("tools");
      if (!_answer) { tools.classList.add("hidden"); tools.innerHTML = ""; return; }
      const own = actions.filter((a) => !a.show || a.show());
      tools.innerHTML = followUps.map((q) => chip(q, "data-q")).join("") +
        `<button type="button" data-tool="copy" class="px-3 py-1 bg-white border border-slate-300 rounded-full text-xs"><i class="fa-solid fa-copy"></i> Copy</button>` +
        own.map((a, i) => `<button type="button" data-tool="${i}" class="px-3 py-1 bg-white border border-slate-300 rounded-full text-xs">${a.icon ? `<i class="fa-solid ${a.icon}"></i> ` : ""}${esc(a.label)}</button>`).join("");
      tools._actions = own;
      tools.classList.remove("hidden");
    }

    // Shows the question box once the server says Ask is set up, else a
    // line on how to set it up. Called again on every tab render: cheap
    // once the status is known, and it retries after an offline start.
    async function refresh() {
      const s = await loadStatus();
      if (onStatus) onStatus(s);
      if (!s) {
        part("setup").textContent = _status ? "" : "Can't reach the farm server to check whether Ask is set up.";
        part("setup").classList.toggle("hidden", !!_status);
        return;
      }
      if (!s.configured) {
        part("setup").textContent = "Not set up on the farm server yet. Whoever looks after it can add a free Gemini or Groq key - see README, \"Ask AI about this estimate\".";
        part("setup").classList.remove("hidden");
        part("body").classList.add("hidden");
        return;
      }
      part("setup").classList.add("hidden");
      part("body").classList.remove("hidden");
      part("engine").textContent = s.provider + (s.daily_limit ? ` · ${s.calls_today || 0}/${s.daily_limit} today` : "");
      part("notesWrap").classList.toggle("hidden", !s.notes);
      const lookups = s.tools ? ` It can also look up a block's full history, a season's weather and the picking record${s.notes ? ", and ask the farm notes" : ""}.` : "";
      part("privacy").textContent = privacy ? privacy(s.provider)
        : `Asking sends this estimate's figures - block kg, your notes and the pack-out mix - from the farm server to ${s.provider}.${lookups} Answers can be wrong: check them against the tables.`;
      renderChips();
    }

    // Where a new question goes: the notes when the toggle is on, else the
    // estimate. `history` also says whether the answer is checked against
    // the figures (and retried when it echoes them); `reading` is the
    // spinner's word for what is being read.
    function routeFor(question) {
      if (notesMode()) return { endpoint: "/api/ai/notes", body: { question }, key: "notes", history: false, reading: "the notes" };
      const ctx = context();
      return ctx ? { endpoint: "/api/ai/ask", body: ctx.body, key: ctx.key, history: true, reading: "the figures" } : null;
    }

    // A typed or chip question is a new question and drops any askWith()
    // route; a follow-up or retry passes the route it continues.
    async function ask(question, { retried = false, shownAs = null, route = null } = {}) {
      question = (question || "").trim().slice(0, MAX_QUESTION);
      if (!question || _controller) return;
      if (!route) _route = null;
      const r = route || routeFor(question);
      if (!r) return;
      if (r.key !== _key) { _history = []; _key = r.key; }
      const shown = shownAs || question;

      const controller = new AbortController();
      _controller = controller;
      let timedOut = false;
      const timer = setTimeout(() => { timedOut = true; controller.abort(); }, FIRST_BYTE_MS);
      setBusy(true);
      input.value = "";
      _answer = "";
      renderTools();
      part("note").textContent = "";
      part("answer").classList.remove("hidden");
      part("sources").classList.add("hidden");
      part("answer").innerHTML = `<p class="text-slate-500"><i class="fa-solid fa-spinner fa-spin"></i> Reading ${r.reading}...</p>`;
      part("status").textContent = `Q: ${shown}`;

      let text = "", done = null, error = null;
      try {
        const payload = { ...r.body, question: retried ? `${question}\n\n${PROSE_REMINDER}` : question };
        if (r.history) payload.history = _history.slice(-3);
        const res = await fetch(r.endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
          signal: controller.signal,
        });
        clearTimeout(timer);
        Boord.setOffline(false);
        if (!res.ok) {
          const detail = Boord._errorDetail(await res.text().catch(() => ""));
          throw new Error(res.status === 422 ? "Some figure in the estimate is out of range - fix it, then ask again."
                                             : detail || `The server answered ${res.status}`);
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        for (;;) {
          const { done: end, value } = await reader.read();
          if (end) break;
          buf += decoder.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, i).trim();
            buf = buf.slice(i + 1);
            if (!line) continue;
            const msg = JSON.parse(line);
            if (msg.t) {
              text += msg.t;
              part("answer").innerHTML = renderMarkdown(text);
            } else if (msg.step) {
              // The model is looking something up (Claude's tools): say so
              // where the answer will land, until the next piece arrives.
              part("status").textContent = `Q: ${shown} · ${msg.step}`;
              if (!text) part("answer").innerHTML = `<p class="text-slate-500"><i class="fa-solid fa-spinner fa-spin"></i> ${esc(msg.step)}</p>`;
            } else if (msg.error) {
              error = msg.error;
            } else if (msg.done) {
              done = msg;
            }
          }
        }
      } catch (e) {
        clearTimeout(timer);
        if (e.name === "AbortError" && !timedOut) error = text ? null : "Stopped.";
        else if (timedOut) error = "No answer in time - try again, or ask something narrower.";
        else if (Boord.isNetworkError(e)) { Boord.setOffline(true); error = "The farm server can't be reached."; }
        else error = e.message || "Something went wrong.";
      } finally {
        _controller = null;
        setBusy(false);
      }

      if (!error && text && !retried && r.history && checkAnswer(text, done).retry) {
        // Small models sometimes echo the JSON back: ask once more, in words.
        return ask(question, { retried: true, shownAs: shown, route: r });
      }
      if (error && !text) {
        part("answer").innerHTML = `<p class="text-red-700">${esc(error)}</p>`;
        return;
      }
      if (!text) {
        part("answer").innerHTML = `<p class="text-slate-500">No answer came back - try asking another way.</p>`;
        return;
      }
      part("answer").innerHTML = renderMarkdown(text);
      _answer = text;
      _history.push({ q: shown, a: text });
      const { note } = checkAnswer(text, done);
      part("note").textContent = [error, note].filter(Boolean).join(" ");
      const by = done && (done.model || done.provider);
      part("status").textContent = `Q: ${shown}${by ? ` · ${by}` : ""}`;
      renderSources(done);
      renderTools();
    }

    // The notes an answer from Boord Notes relied on (its own Ask lists
    // them); a link when the server knows where phones open Notes.
    function renderSources(done) {
      const el = part("sources");
      const srcs = (done && done.sources) || [];
      if (!srcs.length) { el.classList.add("hidden"); el.innerHTML = ""; return; }
      const foot = done.notes_total != null ? ` Searched ${done.notes_considered < done.notes_total ? `the ${done.notes_considered} most relevant of ` : "all "}${done.notes_total} notes.` : "";
      el.innerHTML = `From the farm notes: ` + srcs.map((x) => {
        const label = `${esc(x.title)}${x.date ? ` (${esc(x.date)})` : ""}`;
        return x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener" class="underline">${label}</a>` : label;
      }).join("; ") + "." + foot;
      el.classList.remove("hidden");
    }

    // Send the panel somewhere else on the server with its own body; the
    // route stays for follow-ups until the owner asks about the estimate again.
    function askWith({ endpoint, body, key, question, shownAs = null, reading = "the figures" }) {
      _route = { endpoint, body, key, history: true, reading };
      part("notesMode").checked = false;
      return ask(question, { shownAs, route: _route });
    }

    part("form").addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
    part("notesMode").addEventListener("change", () => { _chipSig = ""; renderChips(); });
    part("stop").addEventListener("click", () => { if (_controller) _controller.abort(); });
    part("chips").addEventListener("click", (e) => {
      const b = e.target.closest("[data-q]");
      if (b) ask(b.dataset.q);
    });
    part("tools").addEventListener("click", async (e) => {
      const q = e.target.closest("[data-q]");
      if (q) { ask(q.dataset.q, { route: _route }); return; }   // a follow-up stays on the same route
      const t = e.target.closest("[data-tool]");
      if (!t || !_answer) return;
      if (t.dataset.tool === "copy") {
        try {
          await navigator.clipboard.writeText(plainText(_answer));
          Boord.toast("Answer copied");
        } catch (err) {
          Boord.toast("Couldn't copy on this device");
        }
        return;
      }
      const action = part("tools")._actions[parseInt(t.dataset.tool, 10)];
      if (action) action.run(plainText(_answer));
    });

    refresh();
    return { refresh, ask, askWith, status: loadStatus, element: el };
  }

  return { create, renderMarkdown, checkAnswer, plainText };
})();
