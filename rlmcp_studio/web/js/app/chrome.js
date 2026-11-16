// The chrome: the bar along the top, its menus, and the keys and clicks that
// close them.

import { el, dot } from "../ui.js";
import { state, set, setLayout, setScheme, setAccent, setAgentOpen,
         UNKNOWN_TASK, ACCENTS } from "../state.js";
import { closeFrame, stepFrame } from "../builder/verify.js";
import { closeBriefing } from "../agent/briefing.js";
import * as editorHost from "../editor.js";
import * as consoleLayout from "../layouts/console.js";
import * as focusLayout from "../layouts/focus.js";
import * as workbenchLayout from "../layouts/workbench.js";
import { paint } from "../main.js";
import { actions } from "./actions-app.js";
import { selectRun, selectTask } from "./load.js";

// Layouts are registered, not switched on. Adding a fourth is a new module and
// one line here — nothing else in the app learns its name.
const LAYOUTS = [consoleLayout, focusLayout, workbenchLayout];
export const byId = Object.fromEntries(LAYOUTS.map(l => [l.meta.id, l]));

let openMenu = null;

// ── chrome ──────────────────────────────────────────────────────────────
/** Every run training that is not on the screen, as a button that goes to it.
 *
 * A run recorded under a task other than the one on screen -- a child run
 * written under a new task id -- is invisible from where its parent is, and a
 * pulse on one row of a long switcher is not visible either. The same for a
 * live run in this task that is not the selected one.
 */
function trainingElsewhere(s) {
  const here = (s.runs || []).filter(r => r.live === true && r.id !== s.runId)
      .map(r => ({ label: r.id, title: `${r.id} is training in this task`,
                   go: () => selectRun(r.id) }));
  const away = (s.tasks || []).filter(t => t.live && t.task !== s.task)
      .map(t => ({ label: t.task, title: `${t.live} training under ${t.task}`,
                   go: () => selectTask(t.task) }));
  const all = [...here, ...away];
  if (!all.length) return null;
  return el("span", { class: "training-else", key: "training-else" },
    el("span", { class: "eyebrow", text: "training" }),
    all.map(x => el("button", { class: "act livego", title: x.title, onclick: x.go },
      dot("running", true), x.label)));
}

export function chrome(s) {
  const currentTask = s.tasks.find(t => t.task === s.task);
  return el("header", { class: "chrome", key: "chrome" },
    el("span", { class: "brand" }, "rlmcp studio"),

    menu("task",
      el("button", { class: "act taskbtn",
                     title: currentTask ? currentTask.task : "no task",
                     onclick: (e) => { e.stopPropagation(); toggleMenu("task"); },
                     "aria-expanded": openMenu === "task" ? "true" : "false" },
        currentTask ? currentTask.task : "no task", " ▾"),
      () => el("div", { class: "menu" },
        el("div", { class: "menu-h" },
          el("span", { class: "eyebrow", text: `tasks · ${s.tasks.length}` }),
          el("span", { class: "sp" }),
          el("button", { class: "act newtask",
                         title: "a task that does not exist yet",
                         onclick: (e) => { e.stopPropagation(); actions.openNewTask(); } },
            "+ New task"),
          // The registry half is a subprocess and can be absent, slow or
          // broken. When it is, this list is records-only -- which looks
          // exactly like a machine with no tasks, and is not.
          s.registry && !s.registry.ok
            ? el("span", { class: "eyebrow", style: { color: "var(--warn)" },
                           title: s.registry.error || "the task registry did not answer",
                           text: "recorded only" })
            : null),
        s.tasks.map(t => el("button", {
          class: "mrow", "aria-current": t.task === s.task ? "true" : null,
          onclick: () => { closeMenu(); selectTask(t.task); },
        },
          dot(t.live ? "running" : t.recorded ? "planned" : "superseded", !!t.live),
          el("span", {},
            el("span", { class: "mn", text: t.task,
                         title: t.task === UNKNOWN_TASK
                           ? "records written before rlmcp stamped a task on them; "
                             + "they are grouped here rather than hidden"
                           : t.task }),
            // The root, only when there is more than one -- with a single
            // records root every row would show the same truncated path, which
            // distinguishes nothing and hides what does.
            el("span", { class: "mr", title: t.root,
                         text: s.tasks.some(o => o.root !== t.root)
                           ? t.root.replace(/^.*\//, "…/")
                           : (t.latest ? `latest ${t.latest.id} · ${t.latest.date}` : "") })),
          el("span", { class: "mc",
                       html: t.runs
                         ? `${t.runs} run${t.runs === 1 ? "" : "s"}`
                           + (t.live ? `<br><b>${t.live} live</b>` : "")
                         // Registered and never trained: the state a task
                         // being built spends its first hour in. Only the
                         // registry half of this list can show it, because
                         // it has no records to be found by.
                         : "no runs yet" }))))),

    trainingElsewhere(s),
    el("span", { class: "spacer" }),

    // the layout picker: small, names where you are, opens three options
    menu("layout",
      el("button", {
        class: "act layoutbtn", "aria-expanded": openMenu === "layout" ? "true" : "false",
        onclick: (e) => { e.stopPropagation(); toggleMenu("layout"); },
      },
        el("span", { class: "lname", text: (byId[s.layout] || byId.workbench).meta.name }),
        el("small", { text: "layout" })),
      () => el("div", { class: "menu" },
        el("div", { class: "menu-h" },
          el("span", { class: "eyebrow", text: "layout" }),
          // Said here because a pick from this menu does not write the
          // settings file: if it did, "just looking" would quietly reset what
          // the studio opens in -- for every browser reading that file.
          el("span", { class: "menu-note", text: "for now \u2014 Settings sets "
                                               + "the one it opens in" })),
        LAYOUTS.map(l => el("button", {
          class: "mrow", "aria-current": l.meta.id === s.layout ? "true" : null,
          onclick: () => { closeMenu(); setLayout(l.meta.id); },
        },
          el("span", { class: "dot", style: { background: l.meta.id === s.layout ? "var(--accent)" : "var(--dim)" } }),
          el("span", {},
            el("span", { class: "mn", text: l.meta.name }),
            el("span", { class: "mr", text: l.meta.blurb })),
          el("span", { class: "mc" }))))),

    el("button", {
      class: `act agentbtn${s.agentOpen ? " on" : ""}`,
      title: s.agentOpen ? "hide the agent panel" : "show the agent panel",
      onclick: () => actions.toggleAgent(),
    }, "Agent",
       s.attention && s.attention.needs_you
         ? el("b", { class: "attn", text: ` ${s.attention.needs_you}` })
         : null),

    // The accent picker. Beside the light/dark toggle because it is the same
    // kind of thing -- how the app looks to you, saved to the settings file so
    // it follows you rather than this browser.
    menu("accent",
      el("button", {
        class: "act accentbtn", title: "the studio's accent colour",
        "aria-expanded": openMenu === "accent" ? "true" : "false",
        onclick: (e) => { e.stopPropagation(); toggleMenu("accent"); },
      },
        el("span", { class: "sw" }),
        el("small", { text: "colour" })),
      () => el("div", { class: "menu accentmenu" },
        el("div", { class: "menu-h" },
          el("span", { class: "eyebrow", text: "accent" }),
          el("span", { class: "menu-note",
                       text: "rl-mcp owns the verdict colours; these stay clear of them" })),
        ACCENTS.map(name => el("button", {
          class: "mrow", "aria-current": name === s.accent ? "true" : null,
          onclick: () => { closeMenu(); setAccent(name); },
        },
          // The swatch paints itself from the same declaration the root uses,
          // so the dot cannot drift from what choosing it actually does.
          el("span", { class: `sw accent-${name}` }),
          el("span", {}, el("span", { class: "mn", text: name })),
          el("span", { class: "mc" }))))),

    el("span", { class: "schemes" },
      el("button", { "aria-pressed": s.scheme === "dark" ? "true" : "false",
                     onclick: () => { setScheme("dark"); editorHost.applyTheme("dark"); } }, "Dark"),
      el("button", { "aria-pressed": s.scheme === "light" ? "true" : "false",
                     onclick: () => { setScheme("light"); editorHost.applyTheme("light"); } }, "Light")),

    // "connected" is about this browser's socket to the studio, and is not
    // called "live": that is one word away from a run's liveness, and would go
    // on being said beside a trainer that had been dead for an hour.
    // Whether the run on screen is actually training, up here and not only in
    // the status bar at the end of a row of metrics -- it is the first thing
    // you want to know when you look at this app.
    // A *fact about a process*, never the record's verdict: those disagree
    // routinely and the verdict is the one that lies.
    trainingChip(s),

    el("button", { class: "act", title: "settings",
                   onclick: () => actions.openSettings() }, "Settings"),

    el("button", {
      class: `act termbtn${s.termOpen ? " on" : ""}`,
      title: s.termOpen ? "close the shell" : "open a shell on this machine",
      onclick: () => actions.toggleTerminal(),
    }, "Terminal"),

    el("span", { class: `chip conn${s.connected ? " on" : ""}`,
                 title: s.connected
                   ? "this browser is connected to the studio"
                   : "this browser has lost the studio; it retries on its own",
                 text: s.connected ? "connected" : "offline" }));
}

/** Training, or not, for the run on screen.
 *
 * `live.state` is rlmcp's own answer about the process -- running, stalled,
 * dead -- and it is deliberately not `record.verdict`: a run closed as
 * `interrupted` whose trainer is still finishing, and a `running` record whose
 * machine rebooted, are both ordinary. The process is the one to believe.
 */
function trainingChip(s) {
  if (!s.runId) return null;
  const live = s.live;
  const state = live && live.ok ? live.state : "none";
  const look = {
    running: { cls: "on", text: "training", dot: "running", beat: true },
    stalled: { cls: "warn", text: "stalled", dot: "provisional" },
    dead: { cls: "", text: "stopped", dot: "interrupted" },
    none: { cls: "", text: "no session", dot: "planned" },
  }[state] || { cls: "", text: state, dot: "planned" };

  const iteration = ((live && live.status) || {}).iteration;
  return el("span", {
    class: `trainchip ${look.cls}`,
    title: state === "running"
      ? "the trainer process is alive and publishing"
      : state === "stalled"
      ? "the session is there but has not published for a while"
      : state === "dead"
      ? "the trainer process has gone; the record and its files remain"
      : "this record names no session",
  }, dot(look.dot, !!look.beat), el("b", { text: look.text }),
     state === "running" && iteration != null
       ? el("small", { text: ` it ${Number(iteration).toLocaleString("en-US")}` })
       : null);
}

function menu(id, button, body) {
  const wrap = el("span", { class: "menuwrap" }, button);
  if (openMenu === id) { wrap.append(body()); requestAnimationFrame(clampMenu); }
  return wrap;
}

/** Keep the open menu inside the window.
 *
 * A menu hangs from the *right* edge of its trigger, which is right for the
 * controls at the right of a bar and wrong for the ones at the left: the task
 * menu is 232 px wide and the task button is at the left of the chrome bar, so
 * with a short label like `(unknown)` the menu starts at a negative x and
 * half of it is off screen. A long task name makes the button wide enough to
 * hide it, so it shows up mostly on the legacy bucket.
 *
 * Measured rather than hardcoded per menu: there are seven of them across
 * three layouts, and which side fits depends on where the bar put the trigger
 * and how wide the window is -- none of which the call site knows. Runs after
 * paint, because the DOM is the only thing that has the answer.
 */
function clampMenu() {
  const m = document.querySelector(".menuwrap .menu");
  if (!m) return;
  const edge = 8;
  m.style.left = "";
  m.classList.remove("menu-left");
  let box = m.getBoundingClientRect();
  if (box.left < edge) {
    m.classList.add("menu-left");
    box = m.getBoundingClientRect();
  }
  // Wider than the window: neither side fits, so pin it to the viewport
  // instead of letting it hang off whichever edge it happened to reach.
  const over = box.right - (window.innerWidth - edge);
  if (over > 0) {
    const anchor = m.parentElement.getBoundingClientRect().left;
    m.style.left = `${Math.max(edge, box.left - over) - anchor}px`;
  }
}

function toggleMenu(id) { openMenu = openMenu === id ? null : id; paint(state); }
export function closeMenu() { openMenu = null; }

// Coming back to a tab that was hidden: repaint once, because what is on
// screen is as old as the moment it was hidden.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) set({});
});

document.addEventListener("click", () => {
  if (state.scopeOpen) set({ scopeOpen: false });
  if (state.gpuOpen) set({ gpuOpen: false });
  if (openMenu) { closeMenu(); paint(state); }
});
document.addEventListener("keydown", (e) => {
  // An opened verification frame is one of a strip, so the arrows step along
  // it -- the same keys that step a gallery anywhere else. Only while one is
  // open, and never over a field somebody is typing in.
  if (state.frameZoom && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    const tag = (e.target && e.target.tagName) || "";
    if (tag !== "INPUT" && tag !== "TEXTAREA" && !(e.target && e.target.isContentEditable)) {
      e.preventDefault();
      stepFrame(e.key === "ArrowRight" ? 1 : -1);
      return;
    }
  }
  if (e.key !== "Escape") return;
  if (state.zoom) set({ zoom: null });
  if (state.briefing || state.briefingBusy || state.briefingError) closeBriefing();
  if (state.frameZoom) closeFrame();
  if (state.scopeOpen) set({ scopeOpen: false });
  if (state.gpuOpen) set({ gpuOpen: false });
  // Everything that sits on top of the app, the two dialogs and the parameter
  // editor included.
  if (state.nextRecord) set({ nextRecord: false });
  if (state.adopting) set({ adopting: null });
  if (state.pendingEdit) set({ pendingEdit: null });
  if (openMenu) { closeMenu(); paint(state); }
  // Below 1180 px the agent is an overlay drawer, and at 1000 px it covers
  // the Diff and Source tabs outright -- they are in the page and cannot be
  // clicked. Escape closes it there, the way it closes everything else
  // that sits on top of the app.
  else if (state.agentOpen && window.innerWidth <= 1180) setAgentOpen(false);
});
