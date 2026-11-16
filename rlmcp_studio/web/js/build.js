// Build — the task as it is being written, rather than a run of it.
//
// Most of the other views orbit a run. This one orbits the task: it works before
// anything has trained, which is the whole point, and it is the only view a
// task spends its first hour in.
//
// Three panels, and none of them owns what it shows:
//
//   the gates      rl-mcp's own `rlmcp check` -- six questions in the order
//                  they fail in, the last of which builds the runner `rlmcp
//                  train` would build, plus what each reward term paid.
//   the preview    `rlmcp play --policy zero --mode hold`, a task built and
//                  stepping, with rlmcp's viser view framed into the panel.
//   the terms      the same numbers the gates came back with, drawn as bars,
//                  because "which term is paying" is a comparison and a table
//                  of six numbers is not a comparison.
//
// The studio starts a process and renders an answer. Everything that decides
// what the answer *is* belongs upstream, which is the rule this feature was
// most likely to break.
//
// The panels are in `builder/`; this module loads the build state and lays
// them out.

import { el } from "./ui.js";
import { state, set, setView, buildable, UNKNOWN_TASK } from "./state.js";
import { api } from "./api.js";
import { placed, sideVar } from "./resize.js";
import { gateStrip } from "./builder/gates.js";
import { verifyStrip, verifyCard } from "./builder/verify.js";
import { previewPanel, termsCard } from "./builder/preview.js";
import { launchCard, whereCard } from "./builder/launch.js";

// The loader lives here rather than in main.js, and that is not tidiness.
// Every other view is fed by the websocket, because a run publishes events; a
// preview does not -- it is a process this app started, whose two interesting
// transitions are "it constructed" and "it died", and neither is a record
// changing. So Build polls, and it polls from the module that draws it, which
// means anything that puts it on screen loads it: the tab, a task switch, a
// restored layout, and the dev page that drives the store directly.

let timer = 0;

/** Read the task's build state, and keep reading while it is on screen. */
export async function refreshBuild() {
  clearTimeout(timer);
  if (!state.task || state.view !== "build") return;
  const task = state.task;
  try {
    const payload = await api.build(task);
    if (state.task !== task || state.view !== "build") return;
    set({ build: payload });

    // The preview's session is where its 3-D view's url lives, and it is a
    // session like any other -- the same endpoint the rail's runs use.
    const session = (payload.preview || {}).session;
    if (session && payload.preview.state === "holding") {
      const snapshot = await api.session(session, 1).catch(() => null);
      if (state.task === task && state.view === "build") set({ previewLive: snapshot });
    } else if (state.previewLive) {
      set({ previewLive: null });
    }

    // Runs this studio started. Not from the rail: a launch that fails does so
    // in its first ten seconds, in a traceback, and a record with no session
    // looks exactly like a record whose run has not started yet.
    const launches = await api.launches().catch(() => null);
    if (launches && state.view === "build") set({ launches });
    // Recipes written for this task: a run made runnable again is a launch
    // candidate beside "a new run", and it is on disk, so it is listed.
    const recipes = await api.recipes(task).catch(() => null);
    if (recipes && state.task === task && state.view === "build") {
      set({ recipes: recipes.recipes || [] });
    }
  } catch (err) {
    if (state.view === "build") set({ buildError: err.message });
  }
  // Faster while something is in flight: constructing is the part you watch.
  const preview = (state.build || {}).preview || {};
  const gates = (state.build || {}).gates || {};
  const verify = (state.build || {}).verify || {};
  const busy = preview.state === "starting" || gates.running || verify.running;
  if (state.view === "build") timer = setTimeout(refreshBuild, busy ? 1200 : 4000);
}

export function buildView(s, actions) {
  const build = s.build || null;
  if (!s.task) return el("div", { class: "empty", text: "no task selected" });
  // The legacy bucket is not a task: asking the server to import, construct
  // and step `(unknown)` fails every gate for a reason that has nothing to do
  // with the code, and the agent panel beside it would have pointed a build
  // agent at a package that cannot exist.
  if (!buildable(s.task)) {
    return el("div", { class: "empty" },
      el("div", { text: `${UNKNOWN_TASK} is not a task.` }),
      el("div", { class: "k", style: { marginTop: "8px", color: "var(--dim)" },
                  text: "It groups records written before rlmcp stamped a task "
                        + "on them, so they stay reachable. There is no package "
                        + "behind it to build or preview." }),
      el("button", { class: "act", style: { marginTop: "12px" },
                     onclick: () => setView("control") },
        "Open the run instead"));
  }
  if (!build || build.task !== s.task) {
    // Nothing has fetched it yet -- a reload straight into this view, or the
    // store driven from outside the app. Kick it; the store repaints us.
    refreshBuild();
    return el("div", { class: "empty", text: "reading the task…" });
  }

  return el("div", { class: "build" },
    gateStrip(build, s, actions),
    verifyStrip(build, s, actions),
    el("div", { class: "build-body", style: { "--side": sideVar("buildSide", s.widths) } },
      previewPanel(build, s, actions),
      el("div", { class: "build-side" },
        verifyCard(build, s, actions),
        termsCard(build, s, actions),
        launchCard(build, s, actions),
        whereCard(build)),
      // Half the 10px gap.
      placed("buildSide", { side: "right", widths: s.widths, actions, offset: 5 })));
}
