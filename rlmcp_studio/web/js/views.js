// The views of a run, and the registry that names them.
//
// These are not modal. A view is *what the stage is showing*, so switching is
// the same act as switching a tab — you never have to close anything to get
// back. Layouts decide where the switcher lives and how much room the view
// gets; none of them owns a view.
//
// Story, Tree and Parameters are rl-mcp's own three views of the records. The
// tree's coordinates and folded recipe come from `rlmcp.records.lineage`, so
// this tree and the one `rlmcp record graph` draws cannot disagree.
//
// The run's own views live in `views/`, one module each; the task's views
// (Build, Overview, Docs, Library) in modules of their own. This one names
// them and renders whichever is selected.

import { el } from "./ui.js";
import { api } from "./api.js";
import { state, set } from "./state.js";
import { buildView } from "./build.js";
import { docsView } from "./docs.js";
import { overviewView } from "./overview.js";
import { libraryView } from "./library.js";
import { nodes, enter } from "./views/shared.js";
import { plotsView } from "./views/plots.js";
import { recordsView } from "./views/records.js";
import { paramsView } from "./views/params.js";
import { sourceView } from "./views/source.js";

export const VIEWS = [
  // Build is first because it is the only view that works before anything has
  // been run, and because it is where a task's life starts.
  { id: "build", label: "Build", blurb: "the task as it is being written" },
  // Second, between the task being written and the run being watched, because
  // that is where it is read: what this task *is* — the robot, the scene, the
  // observations, the actions, the reward, the terminations — as opposed to
  // how any run of it went. Definition, never results.
  { id: "overview", label: "Overview", blurb: "what the task is: robot, obs, actions, reward, terminations" },
  // Labelled "Run", because that is what it is about. The id stays `control`:
  // it is internal, three layouts and the checks page branch on it, and a
  // rename there would be churn nobody sees.
  { id: "control", label: "Run", blurb: "the run now, and what you say about it" },
  // Published metrics and TensorBoard curves ask one question -- "what did the
  // curves do" -- and two tabs would split it by *who wrote the number* rather
  // than by what you want to know. They are one view with that split as a
  // control inside it.
  { id: "plots", label: "Plots", blurb: "every curve, published and logged" },
  // Story and Tree are one question too: what else has this task run. Cards
  // answer "what happened", the graph answers "what came from what", and
  // which you want changes minute to minute -- a button, not a tab.
  { id: "records", label: "Records", blurb: "every run of this task, as cards or a graph" },
  { id: "params", label: "Parameters", blurb: "the knobs, and how they moved" },
  { id: "source", label: "Code", blurb: "the task package on disk" },
  // Last, beside Source, because it reads the same repository from the other
  // end: Source is what the task *is*, Docs is what was written about it.
  { id: "docs", label: "Docs", blurb: "the markdown an agent wrote" },
  // Last, because it is the view you leave the run for: what this task is,
  // what its runs concluded, and what wasted time.
  { id: "library", label: "Library", blurb: "the task's card, and every other task" },
];

const RECORD_VIEWS = new Set(["records", "params"]);

/** Views that orbit the *task* rather than a run of it.
 *
 * They render with no run selected, which every other view cannot: there is
 * nothing for Metrics to draw before something has trained. This is the set
 * the layouts check before they fall back to "no runs yet". */
export const TASK_VIEWS = new Set(["build", "overview", "docs", "library"]);

// Async views render from the store, never into a node they filled themselves.
// The live feed repaints constantly, so a view that fetches on every render
// both flickers and hammers the server — and, because the node it filled was
// already discarded, never shows anything at all.

/** Load the record graph for the current task, once, into the store. */
export async function ensureGraph() {
  if (state.graphTask === state.task && state.graph) return state.graph;
  const payload = await api.recordGraph(state.task);
  set({ graph: payload.nodes || [], graphTask: state.task });
  return state.graph;
}

/** Render whichever view is selected. Returns an element, never an overlay. */
export function renderView(id, ctxState, actions) {
  enter(ctxState, actions);

  if (RECORD_VIEWS.has(id) && !nodes.length) {
    // Kick the load; the store update repaints us.
    ensureGraph().catch(() => set({ graph: [] }));
    return el("div", { class: "empty", text: "reading records…" });
  }
  if (id === "build") return buildView(ctxState, actions);
  if (id === "plots") return plotsView();
  if (id === "records") return recordsView();
  if (id === "params") return paramsView();
  if (id === "source") return sourceView();
  if (id === "overview") return overviewView(ctxState, actions);
  if (id === "docs") return docsView(ctxState, actions);
  if (id === "library") return libraryView(ctxState, actions);
  return null;                   // control / metrics / record belong to layouts
}

// The views that take the stage's whole height rather than the height of
// whatever they happen to contain.
//
// Overview is here for the same reason Docs is: it is a document with a rail
// beside it. Without it the panel draws at the height of its own text with
// empty ground underneath, and the rail's own scroll has nothing to scroll
// inside.
//
// Library is the same shape. `.b-scroll` is a two-column grid of `max-content`
// rows, so `height: 100%` on a view inside it resolves against a row sized by
// its own content. Left out of this set, the shelf and the card stop scrolling
// inside themselves, the view lays out at its full length in the left column
// with the right one empty, and every repaint of a live run lays the whole of
// it out again so the page jumps under the pointer.
export const FULL_HEIGHT = new Set(["source", "build", "docs", "overview",
                                    "library"]);
