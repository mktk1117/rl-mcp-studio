// The entry point. Boots the studio, then paints it on every change to the
// store: the chrome, the layout's stage, the status bar and the overlays.

import { el, dot, patch } from "./ui.js";
import { state, set, subscribe, setAgentOpen, settingChanged, applySettings } from "./state.js";
import { api, connect } from "./api.js";
import { statusbar, chartOverlay, adoptOverlay, nextRecordOverlay,
         newTaskOverlay } from "./components.js";
import { refreshBuild } from "./build.js";
import { frameOverlay } from "./builder/verify.js";
import { terminalDrawer } from "./terminal.js";
import { settingsOverlay } from "./settings.js";
// The agent panel itself belongs to the layouts; this is the one piece of it
// that sits on top of the app rather than inside a column -- the prompt, read
// as sent, which is too much text for a panel that is often 340 px wide.
import { briefingOverlay } from "./agent/briefing.js";
import { shotOverlay } from "./capture.js";
import { reportWidths } from "./resize.js";
import * as editorHost from "./editor.js";
import { HANDLERS } from "./app/feed.js";
import { actions } from "./app/actions-app.js";
import { refreshDoctor, firstTask, reloadRuns, refreshUnrecorded, refreshGpu,
         refreshConversations } from "./app/load.js";
import { byId, chrome } from "./app/chrome.js";

// ── boot ────────────────────────────────────────────────────────────────

async function boot() {
  if (!document.getElementById("app")) return;   // nothing to boot into
  document.documentElement.setAttribute("data-scheme", state.scheme);
  document.documentElement.setAttribute("data-accent", state.accent);

  const [config, tasks, agent] = await Promise.all([
    api.config(), api.tasks(), api.agentStatus().catch(() => ({ available: false, reason: "agent host unreachable" })),
  ]);
  set({ agent });
  // Its own timer: nothing publishes a GPU event, and the numbers move
  // continuously rather than at moments.
  refreshGpu();
  // Preferences the studio remembers for *you*, before the first real paint,
  // so a browser that has never opened this studio still comes up in the theme
  // and the panel sizes you chose somewhere else.
  applySettings(config.settings);
  set({ roots: config.roots, config, tasks: tasks.tasks,
        // Why the list is what it is: whether the registry answered, which
        // packages it imported, and what it could not. A switcher quietly
        // missing every task that has never run is worse than one that says so.
        registry: tasks.registry || null,
        task: firstTask(tasks.tasks), ready: true });

  // What the install can do, for the empty state and for Settings. After the
  // first paint rather than in the batch above: it shells out to nvidia-smi
  // and probes two CLIs, and a fresh install should not wait on that to show
  // anything at all. `null` means it was asked and did not answer, which the
  // card says; `undefined` means not asked yet.
  refreshDoctor();

  // Panel drags are saved by `resize.js`, which cannot import the store back.
  reportWidths((widths) => settingChanged("panels", widths));

  // Build is the one view whose data does not arrive over the socket, and it
  // can be the view the app was left on.
  if (state.view === "build") refreshBuild();
  await reloadRuns();

  connect({
    lastSeq: () => state.lastSeq,
    onOpen: () => set({ connected: true }),
    onClose: () => set({ connected: false }),
    onEvent: (envelope) => {
      if (envelope.seq) state.lastSeq = envelope.seq;
      const handler = HANDLERS[envelope.kind];
      if (handler) handler(envelope);
    },
  });

  // `?term=1` opens the drawer at boot. Only from the URL, and only when it is
  // asked for: a shell is a *process*, so it is not remembered across a reload
  // (see `state.js`) -- but a link that says "open the terminal" is somebody
  // asking, and it is the only way a headless browser can photograph the
  // drawer. `dev/goto.html` passes it through.
  const asked = new URLSearchParams(location.search);
  if (asked.get("term") === "1" && !state.termOpen) {
    actions.toggleTerminal();
  }
  // Same reason, same shape: Settings is an overlay a click opens, and a
  // headless browser photographing it has no click. Only from the URL.
  if (asked.get("settings") === "1" && !state.settingsOpen) {
    actions.openSettings();
  }

  window.__studioActions = actions;
  refreshConversations();
  refreshUnrecorded();
  subscribe(paint);
  editorHost.onChange(() => paint(state));
  paint(state);
}

// ── paint ───────────────────────────────────────────────────────────────
// A repaint counter, readable from the console, so "it feels laggy" can be a
// measurement instead of a guess.
let paints = 0;
window.__studioPaints = () => paints;

// No scroll memory, focus or caret restore here: `patch()` keeps nodes alive,
// and the browser already does all of that for a node that survives.

export function paint(s) {
  try {
    paintOnce(s);
    if (crashed) { crashed = false; document.getElementById("paint-crash")?.remove(); }
  } catch (err) {
    // Without this, one exception in one panel stops *every* repaint for the
    // rest of the session. The page still looks alive -- the numbers are
    // there, they are just the old ones -- and nothing a person clicks has
    // any effect. A stale layout id in localStorage is enough to do it.
    crashed = true;
    console.error("paint failed:", err);
    showCrash(err);
  }
}

let crashed = false;

function showCrash(err) {
  let bar = document.getElementById("paint-crash");
  if (!bar) {
    bar = document.createElement("div");
    bar.id = "paint-crash";
    bar.className = "crashbar";
    document.body.append(bar);
  }
  // Built by hand, outside `patch`, because the thing that just failed is the
  // renderer.
  bar.textContent = `the display stopped updating: ${err && err.message || err} — `;
  const again = document.createElement("button");
  again.textContent = "reload";
  again.onclick = () => location.reload();
  bar.append(again);
}

function paintOnce(s) {
  const app = document.getElementById("app");
  // Importing this module does not imply a page to paint into: the module
  // check at /static/dev/modules.html imports every module precisely to prove
  // they load, and painting into nothing would throw on every state change.
  if (!app) return;
  paints += 1;

  if (!s.ready) {
    patch(app, [el("div", { class: "boot" }, dot("running", true), "loading records…")]);
    return;
  }
  const layout = byId[s.layout] || byId.workbench;
  const run = s.runs.find(r => r.id === s.runId) || null;
  // Morphed into place rather than rebuilt: a node that is still the same node
  // keeps its caret, its selection, its scroll, its open dropdown and the
  // click you started on it, so the app does not fight the person using it.
  // Keyed, every one of them. `patch()` matches by position where no key is
  // given, and the terminal drawer *appears and disappears* between the stage
  // and the bar -- unkeyed, opening it shifts everything after it by one and
  // the reconciler morphs the status bar into the drawer, leaving the bar's
  // cells frozen in the terminal's scrollback by `data-keep-children`. This is
  // exactly the case ui.js's own note describes: give keys to lists whose
  // items move.
  patch(app, [
    chrome(s),
    el("div", { class: "stage", key: "stage" }, layout.render(s, actions)),
    // Between the stage and the bar, which is where a drawer belongs: it
    // takes room from the work rather than covering it, and the bar stays
    // where it always is.
    terminalDrawer(s, actions),
    statusbar(run, s.live, s, actions),
    // Only meaningful in the overlay breakpoint; CSS hides it above that, so
    // a wide window keeps the agent as a plain column with nothing on top.
    settingsOverlay(s, actions),
    shotOverlay(s, actions),
    s.agentOpen ? el("div", { class: "drawer-scrim", key: "scrim", title: "close the agent panel",
                              onclick: () => setAgentOpen(false) }) : null,
    chartOverlay(s, actions),
    frameOverlay(s, actions),
    briefingOverlay(s),
    adoptOverlay(s, actions),
    newTaskOverlay(s, actions),
    nextRecordOverlay(s, actions),
  ]);

}

// No pointer, composition or <select> holds are needed: the node under the
// finger, the element mid-composition and the open dropdown all survive a
// paint.

boot().catch(err => {
  document.getElementById("app").innerHTML =
    `<div class="boot" style="color:var(--bad)">could not start: ${err.message}</div>`;
});
