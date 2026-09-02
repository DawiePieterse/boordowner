// Shared pull-to-refresh for the installed PWAs. Standalone mode has no
// browser reload UI, so each screen attaches its own data-refresh callback:
//   LWPTR.attach(async () => { ...reload lists, re-render... });
// Touch-only by design - desktop/mouse users have visible refresh buttons.
const LWPTR = (() => {
  const THRESHOLD = 70;   // px of (damped) pull that triggers a refresh
  const MAX_PULL = 110;
  let onRefresh = null;
  let startY = null;
  let pullDistance = 0;
  let refreshing = false;
  let indicator = null;

  function ensureIndicator() {
    if (indicator) return indicator;
    indicator = document.createElement("div");
    indicator.className = "ptr-indicator";
    indicator.innerHTML = `<i class="fa-solid fa-arrows-rotate"></i>`;
    document.body.appendChild(indicator);
    return indicator;
  }

  function setPull(px) {
    const el = ensureIndicator();
    // Slides down from above the header; the icon turns as you pull.
    el.style.transform = `translate(-50%, ${px - 56}px) rotate(${px * 3}deg)`;
    el.style.opacity = px > 4 ? "1" : "0";
    el.classList.toggle("ready", px >= THRESHOLD);
  }

  // Backstop only - a refresh should finish well inside this, since the
  // requests behind it carry their own (shorter) timeouts.
  const MAX_SPIN_MS = 12000;

  async function trigger() {
    const el = ensureIndicator();
    refreshing = true;
    el.classList.add("spinning");
    setPull(THRESHOLD);
    try {
      // The spinner is a promise the UI makes to the user, so it is capped:
      // a refresh that stalls (unreachable server, wedged request) must still
      // hand the screen back instead of spinning forever.
      if (onRefresh) {
        await Promise.race([
          onRefresh(),
          new Promise((resolve) => setTimeout(resolve, MAX_SPIN_MS)),
        ]);
      }
    } catch (e) { /* refresh errors surface via each screen's own UI */ }
    el.classList.remove("spinning", "ready");
    setPull(0);
    refreshing = false;
  }

  // A pull-to-refresh listener sits on the whole document and cancels the
  // touches it claims, so it has to be careful about which drags are actually
  // its own. A drag that starts on a form control is text selection or a
  // control gesture; one that starts inside a dialog or any self-scrolling
  // pane belongs to that element. Swallowing those makes a dialog impossible
  // to scroll - which reads to the user as the screen having frozen.
  function belongsToSomethingElse(target) {
    if (!target || !target.closest) return false;
    if (target.closest("input, textarea, select, button, a, [contenteditable]")) return true;
    for (let el = target; el && el !== document.body; el = el.parentElement) {
      const style = getComputedStyle(el);
      if (style.position === "fixed") return true; // dialog / overlay on top
      const scrolls = style.overflowY === "auto" || style.overflowY === "scroll";
      if (scrolls && el.scrollHeight > el.clientHeight) return true;
    }
    return false;
  }

  function onTouchStart(e) {
    if (refreshing || window.scrollY > 0 || belongsToSomethingElse(e.target)) {
      startY = null;
      return;
    }
    startY = e.touches[0].clientY;
    pullDistance = 0;
  }

  function onTouchMove(e) {
    if (startY === null || refreshing) return;
    const dy = e.touches[0].clientY - startY;
    if (dy <= 0 || window.scrollY > 0) { pullDistance = 0; setPull(0); return; }
    pullDistance = Math.min(dy * 0.5, MAX_PULL); // damped so it feels elastic
    setPull(pullDistance);
    if (pullDistance > 8 && e.cancelable) e.preventDefault();
  }

  function onTouchEnd() {
    if (startY === null || refreshing) return;
    startY = null;
    if (pullDistance >= THRESHOLD) trigger();
    else setPull(0);
    pullDistance = 0;
  }

  return {
    attach(callback) {
      onRefresh = callback;
      ensureIndicator();
      document.addEventListener("touchstart", onTouchStart, { passive: true });
      document.addEventListener("touchmove", onTouchMove, { passive: false });
      document.addEventListener("touchend", onTouchEnd, { passive: true });
      document.addEventListener("touchcancel", onTouchEnd, { passive: true });
    },
  };
})();
