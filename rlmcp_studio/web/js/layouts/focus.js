// Focus — one subject, large. Verbs live in the side column, off the render.

import { el, chip } from "../ui.js";
import { runRail, headlineStats, agentPanel, feedbackCard, robotCard, recordBrief, closeCard, measuredCard, nothingHere, noRunsYet } from "../components.js";
import { VIEWS, renderView, FULL_HEIGHT, TASK_VIEWS } from "../views.js";
import { placed, sideVar, fits } from "../resize.js";

export const meta = { id: "focus", name: "Focus", blurb: "One subject, big" };

export function render(state, actions) {
  const run = state.runs.find(r => r.id === state.runId);
  const live = state.live;
  const series = (live && live.series) || {};

  return el("div", {
    class: `focus${state.agentOpen ? " with-agent" : ""}`,
    style: {
      gridTemplateColumns: state.agentOpen
        ? `${fits("focusRail", state.widths.focusRail)}px minmax(0, 1fr) ${fits("focusAgent", state.widths.focusAgent)}px`
        : `${fits("focusRail", state.widths.focusRail)}px minmax(0, 1fr)`,
    },
  },
    // "records", not "runs": it is the same list as the Workbench rail, and
    // two names for one thing is two things as far as a reader is concerned.
    runRail(state.runs, state.runId, actions.selectRun, "records", state, actions),
    el("div", { class: "f-main" },
      el("div", { class: "f-head" },
        el("div", { class: "f-title" },
          el("span", { class: "id", text: run ? run.id : "—" }),
          el("h2", { text: run ? run.slug : "no run selected" }),
          // Lit by the trainer, not by the word: see the rail in
          // components.js. A record reads `running` from the moment it opens
          // until somebody closes it, which is long after the run ended.
          run ? chip(run.verdict, run.live === true) : null),
        el("div", { class: "f-sub", text: run ? subtitle(run) : "" })),
      el("div", { class: `f-body${state.view === "control" ? "" : " wide"}` + (FULL_HEIGHT.has(state.view) ? " full" : ""),
                  style: { "--side": sideVar("focusSide", state.widths) } },
        // An empty studio explains itself here too -- see `nothingHere`.
        TASK_VIEWS.has(state.view)
          ? el("div", { class: "f-view" }, renderView(state.view, state, actions))
          : !run && !(state.tasks || []).length
          ? nothingHere(state)
          : !run && !(state.runs || []).length
          ? noRunsYet(state)
          : state.view === "control"
          ? el("div", { class: "f-stage-box-wrap" }, robotCard(state, actions))
          : el("div", { class: "f-view" }, viewFor(state, actions)),
        state.view === "control"
          // The verbs are in the frames card's own header, right beside the
          // frame they act on -- a second copy of the same three buttons in
          // the side column, driven by the same state, would be two places to
          // look and no more to do. The clips card is here instead: the one
          // layout meant for watching has to be able to show the film.
          // Run's side column carries the claim (in `recordBrief`), what it
          // measured, and the control that closes it. One panel, which is
          // what a run *is*.
          ? el("div", { class: "f-side" },
              headlineStats(series, live),
              recordBrief(state, actions),
              feedbackCard(state, actions),
              measuredCard(run, state, actions),
              closeCard(state, actions))
          : null,
        // The body's 20px right padding plus half its 18px gap.
        state.view === "control"
          ? placed("focusSide", { side: "right", widths: state.widths, actions, offset: 29 })
          : null),
      el("div", { class: "f-more" },
        el("span", { class: "lbl", text: "view" }),
        VIEWS.map(v => el("button", {
          title: v.blurb,
          "aria-pressed": state.view === v.id ? "true" : "false",
          onclick: () => actions.setView(v.id),
        }, v.label)),
        el("button", {
          "aria-pressed": state.agentOpen ? "true" : "false",
          onclick: () => actions.toggleAgent(),
        }, "Agent"))),
    state.agentOpen
      ? el("aside", { class: "f-drawer" }, agentPanel({ onHide: () => actions.toggleAgent() }))
      : null,
    // See Workbench: a handle inside the rail sits under its scrollbar, and
    // one on the drawer's edge would sit half under the stage.
    placed("focusRail", { side: "left", widths: state.widths, actions }),
    state.agentOpen ? placed("focusAgent", { side: "right", widths: state.widths, actions }) : null);
}

function subtitle(run) {
  const bits = [];
  if (run.weights) bits.push(`warm started from ${run.weights}`);
  else if (run.parent) bits.push(`config from ${run.parent}`);
  else bits.push("from scratch");
  bits.push(run.task);
  return bits.join(" · ");
}


// Focus shows the same views as every other layout — only the room differs.
function viewFor(state, actions) {
  const shared = renderView(state.view, state, actions);
  if (shared) return shared;
  // The claim, and the control that answers it. Every layout gets the same
  // one, or a run stays `running` for ever in whichever layout you happen to
  // be in when it finishes.
  return el("div", { class: "empty", text: "nothing here yet" });
}
