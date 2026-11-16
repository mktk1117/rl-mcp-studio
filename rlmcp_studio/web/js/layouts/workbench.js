// Workbench — three columns, everything at once. Right for comparison work.

import { el } from "../ui.js";
import { runRail, metricCards, agentPanel, feedbackCard, robotCard, recordBrief, closeCard, measuredCard, nothingHere, noRunsYet } from "../components.js";
import { VIEWS, renderView, FULL_HEIGHT, TASK_VIEWS } from "../views.js";
import { divider, placed, fits } from "../resize.js";

export const meta = { id: "workbench", name: "Workbench", blurb: "Three columns, everything at once" };


export function render(state, actions) {
  panelActions = actions;
  const run = state.runs.find(r => r.id === state.runId);
  const live = state.live;
  const series = (live && live.series) || {};

  const tabs = el("nav", { class: "b-tabs" },
    VIEWS.map(v => el("button", {
      class: "b-tab", title: v.blurb,
      "aria-selected": state.view === v.id ? "true" : "false",
      onclick: () => actions.setView(v.id),
    }, v.label)),
    // No tab without a view behind it. A disabled control whose tooltip is a
    // roadmap note is a promise to the user that the app cannot keep.
    );

  // The agent column is a *column*, not a fixture: closing it gives the stage
  // its width back, and on a narrow window it is a drawer over the top, so
  // leaving it open there would hide the thing you came to look at.
  const bench = el("div", {
    class: `bench${state.agentOpen ? "" : " noagent"}`,
    // The agent column's width is a preference, so the grid takes it from
    // state rather than from a constant. A transcript you are reading and a
    // chart you are reading want different splits.
    style: {
      // Through `fits`, as the dividers are: the raw saved width can put a
      // wide agent column into a narrow window and leave the stage a strip.
      gridTemplateColumns: state.agentOpen
        ? `${fits("rail", state.widths.rail)}px minmax(0, 1fr) ${fits("agent", state.widths.agent)}px`
        : `${fits("rail", state.widths.rail)}px minmax(0, 1fr)`,
    },
  },
    runRail(state.runs, state.runId, actions.selectRun, "records", state, actions),
    el("main", { class: "b-stage" }, tabs,
      el("div", {
        class: `b-scroll${FULL_HEIGHT.has(state.view) ? " full" : ""}`,
        // Two columns on Control, and where they meet is a preference: the
        // robot wants width when you are watching and the record wants it when
        // you are reading.
        style: state.view === "control"
          ? { gridTemplateColumns: `minmax(0, ${state.widths.controlSplit}fr) `
                                 + `minmax(0, ${100 - state.widths.controlSplit}fr)` }
          : {},
      }, ...panel(state, run, live, series))),
    state.agentOpen
      ? el("aside", { class: "agentcol" }, agentPanel({ onHide: actions.toggleAgent }))
      : null,
    // Placed by the grid, not by the panels: inside the rail the handle sits
    // over the rail's own scrollbar, and only a 2px strip of it takes the
    // pointer -- in Chrome the rail cannot be dragged at all. Under 1180px
    // the agent column is a drawer of fixed width, and the stylesheet hides
    // its handle there.
    placed("rail", { side: "left", widths: state.widths, actions }),
    state.agentOpen ? placed("agent", { side: "right", widths: state.widths, actions }) : null);
  return bench;
}

function headlineCount(series, live) {
  const nominated = Object.keys(((live && live.status) || {}).headline_metrics || {})
      .filter(n => (series || {})[n]);
  if (nominated.length <= 2) return null;
  return el("button", {
    class: "act", title: "every series this run publishes",
    onclick: () => panelActions && panelActions.setView("plots"),
  }, `2 of ${nominated.length}`);
}

let panelActions = null;

function panel(state, run, live, series) {
  // A view that orbits the task renders with no run, which is the point of it:
  // Build is where a task with no runs is worked on.
  if (TASK_VIEWS.has(state.view)) {
    const shared = renderView(state.view, state, panelActions);
    if (shared) return [el("div", { style: { gridColumn: "1 / -1" } }, shared)];
  }
  if (!run) {
    return [el("div", { style: { gridColumn: "1 / -1" } },
               !(state.tasks || []).length ? nothingHere(state)
               : !(state.runs || []).length ? noRunsYet(state)
               : el("div", { class: "empty", text: "no run selected" }))];
  }

  // The record views want the full width; the run views are two columns.
  const shared = renderView(state.view, state, panelActions);
  if (shared) return [el("div", { style: { gridColumn: "1 / -1" } }, shared)];

  // Run: what it is doing on the left, what it claimed and what came of it on
  // the right, on one tab: a run's claim belongs beside the run, not one
  // click away from it.
  const left = el("div", { class: "b-col left" },
    robotCard(state, panelActions),
    recordBrief(state, panelActions));
  left.append(splitDivider(state, panelActions));
  return [
    left,
    el("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
      el("div", { class: "card" },
        el("div", { class: "card-h" },
          el("h3", { text: "Headline metrics" }),
          el("span", { class: "sp" }),
          // The card shows two. The run may nominate many more. Saying so is the
          // difference between "these are the headline metrics" -- which would
          // be false -- and "here are two of them, the rest are next door".
          headlineCount(series, live)),
        el("div", { class: "card-b" }, metricCards(series, 2, live))),
      feedbackCard(state, panelActions),
      measuredCard(run, state, panelActions),
      closeCard(state, panelActions)),
  ];
}


/** The divider between Control's two columns, as a share of the stage. */
export function splitDivider(state, actions) {
  return divider("controlSplit", {
    side: "left",
    // Stored as a percentage, so that is what to step: reading the rendered
    // element gives a number the grid derived after padding and the gap, which
    // is not the same quantity and drifts if you round-trip through it.
    current: () => state.widths.controlSplit,
    perPixel: (handle) => {
      const row = handle.closest(".b-scroll");
      const width = row ? row.getBoundingClientRect().width : 0;
      return width ? 100 / width : 0.1;
    },
    onResize: (pct) => actions.setPanelWidth("controlSplit", pct),
    onReset: (pct) => actions.setPanelWidth("controlSplit", pct),
  });
}

