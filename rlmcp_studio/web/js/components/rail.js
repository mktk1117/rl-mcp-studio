// The run rail, and what the app says when there is nothing to show yet.

import { el, dot, on, scrollListTo } from "../ui.js";

/** Which run the rail was last scrolled to, so it scrolls once and not on
 *  every repaint -- which with a live run would fight the scroll wheel. */
let inView = "";

/** Bring the selected run back onto the rail when it is the app that moved.
 *
 * A run is chosen in more places than this rail: the record graph, a citation
 * on a library card, `open run`, and the task switch that selects the newest
 * one for you. In every one of those the rail is left marking a row that can
 * be forty above the scrollport -- and in a task with dozens of records the
 * mark is the only thing that says which run the whole app is about.
 *
 * `scrollListTo`, so a row already on the rail does not move and nothing but
 * the rail ever scrolls; once per run, so scrolling away from your own
 * selection sticks; on a timeout rather than a frame, because a hidden tab is
 * handed no frames and would come back marking a row that is not on the rail.
 * Same rules as the library's shelf, because it is the same promise.
 */
function keepRunInView(runId) {
  if (!runId || runId === inView) return;
  inView = runId;
  setTimeout(() => {
    scrollListTo(document.querySelector(`.rail .runrow[key="${CSS.escape(runId)}"]`), ".rail");
  }, 0);
}

export function runRail(runs, runId, onPick, title = "runs", state = null, actions = null) {
  // Dozens of records in one task, in id order, with no way to ask for one by
  // name, would mean reading the whole rail to find one. It matches the id,
  // the slug and the headline, because those are the three things anybody
  // remembers about a run.
  const query = ((state && state.runFilter) || "").trim().toLowerCase();
  const shown = query
    ? runs.filter(r => `${r.id} ${r.slug} ${r.line || ""} ${r.stage || ""}`
                        .toLowerCase().includes(query))
    : runs;

  const rail = el("aside", { class: "rail" },
    el("span", { class: "eyebrow railhead",
                 text: query ? `${title} · ${shown.length} of ${runs.length}`
                             : `${title} · ${runs.length}` }));

  // Shown for a long list, and for any list that is *being* filtered -- a
  // filter with no box to clear it in is a rail you cannot recover.
  if (state && actions && (runs.length > 8 || query)) {
    const box = el("input", { class: "railfilter", type: "text",
                              placeholder: "find a run…", value: state.runFilter || "" });
    on(box, "input", (e) => actions.typeRunFilter(e.currentTarget.value));
    rail.append(box);
  }

  if (!runs.length) {
    rail.append(el("div", { class: "empty", text: "no runs in this task" }));
    return rail;
  }
  if (!shown.length) {
    rail.append(el("div", { class: "empty", text: `nothing matches “${query}”` }));
  }
  for (const run of shown.slice().reverse()) {
    rail.append(el("button", {
      class: "runrow",
      // Keyed, or a record landing rewrites the whole rail. The list is drawn
      // newest first, so a new record is an insert at the *top*: with nothing
      // to match rows by, `patch()` falls back to position and every row
      // becomes the run above it -- eight nodes rewritten to move one list
      // down by one, and the row under the pointer quietly becomes a different
      // run between deciding to click it and clicking it. An agent writing the
      // next record mid-session is the ordinary case here, not the unusual one.
      key: run.id,
      "aria-current": run.id === runId ? "true" : null,
      onclick: () => onPick(run.id),
    },
      // The pulse is liveness, never the verdict. `running` is the record's
      // claim and it stays until somebody closes the run out, so a rail keyed
      // to the word would pulse beside runs whose trainer has been gone for
      // hours -- the app asserting, in the one panel that has to be
      // trustworthy, that something is training when nothing is.
      dot(run.verdict, run.live === true),
      el("span", {},
        el("span", { class: "rid", text: run.id }),
        el("span", { class: "rsl", text: run.slug }))));
  }
  keepRunInView(runId);
  // Nothing else goes in here. The rail is the records of this task and only
  // those: unrecorded sessions are not records and live in the status bar,
  // and conversations live in the agent panel, which is where the one you
  // pick is then shown. A rail that mixed all three would be three lists deep
  // and answer no question completely.
  return rail;
}

/** Nothing to show, and what to do about it.
 *
 * An empty studio that only said "no runs in this task" beside "no task"
 * beside "no run selected" would show the same picture whether you are new,
 * or your records live somewhere else and `--records` is pointing at the wrong
 * directory -- which is the far more likely of the two, and the one the app
 * can actually help with by saying where it looked.
 */
/** A task that exists and has never trained.
 *
 * The switcher lists registered tasks as well as recorded ones, which makes
 * this state reachable, and it is the state a task spends its first hour in.
 * "No runs yet" is an answer, not a blank — so it says what the task is and
 * what happens next.
 */
export function noRunsYet(state) {
  const task = (state.tasks || []).find(t => t.task === state.task) || {};
  const registered = !!task.registered;

  return el("div", { class: "nothing" },
    el("h3", { text: registered ? "No runs yet" : "No runs under this task" }),
    el("p", {}, el("code", { text: state.task || "—" }),
      registered
        ? " is registered here and has never trained."
        : " has records elsewhere, but nothing importable here registers it."),
    registered && (task.package || task.experiment)
      ? el("p", { class: "muted" },
          task.package
            ? el("span", {}, "Registered by ", el("code", { text: task.package }))
            : el("span", {}, "Registered by the backend itself"),
          task.experiment ? el("span", {}, " · runs would land in ",
                               el("code", { text: task.experiment })) : null)
      : null,
    el("p", { text: registered
      ? "Build it, then launch the first run. Until one exists there is no "
        + "story, no tree and no parameters — those are what a run leaves behind."
      : "The record views work from what was written down. Anything that needs "
        + "the package — code, previews, launching — needs it importable here." }));
}

export function nothingHere(state) {
  const roots = ((state.roots || {}).records) || [];
  const unrecorded = (state.unrecorded || []).length;

  return el("div", { class: "nothing wide" },
    el("h3", { text: "No records here yet" }),
    el("p", { text: roots.length
      ? "This studio is reading:"
      : "No records directory was given, so there is nowhere to read." }),
    roots.length ? el("div", { class: "pol-list" },
      roots.map(r => el("code", { text: r }))) : null,
    unrecorded
      ? el("p", {}, el("b", { text: `${unrecorded} session(s) with no record` }),
           " are visible in the rail — a run that trained but never had a "
           + "record written. Adopting one is the quickest way to see this app "
           + "with something in it.")
      : el("p", {}, "Nothing under those paths is a record store. Either point "
           + "the studio at one — ",
           el("code", { text: "rlmcp-studio --records <dir>" }),
           " — or start a run under rlmcp and it will appear here."),
    // A fresh install lands here, and "no records" is the least of what it
    // needs to know. The same list `rlmcp-studio --check` prints: what the
    // studio's machine has, and the fix for what it lacks -- then what to do
    // first.
    machineCard(state),
    firstSteps(state),
    el("p", { class: "muted",
      text: "The rail is built from records, so an empty rail means there are "
          + "none, never that a run is missing." }));
}

/** What this install and its machine can do -- `/api/doctor`, drawn.
 *
 * One row per finding, the fix under any that failed. Shared by the empty
 * state (a fresh install) and Settings (any day after), so the two never say
 * different things about the same machine. Absent findings render as a
 * one-line "asking..." rather than nothing: an empty card looks like a
 * healthy machine, and a machine nobody has checked yet is not one. */
export function machineCard(state, { title = "This machine" } = {}) {
  const doc = state.doctor;
  const rows = doc && doc.findings ? doc.findings : null;
  return el("div", { class: "machine" },
    el("div", { class: "machine-h" },
      el("h4", { text: title }),
      doc && doc.where
        ? el("span", { class: "machine-where", title: doc.where, text: doc.where })
        : null),
    rows
      ? el("div", { class: "machine-rows" },
          rows.map(f => el("div", { class: `machine-row${f.ok ? " ok" : f.required ? " bad" : " warn"}` },
            el("span", { class: "machine-mark",
                         text: f.ok ? "ok" : f.required ? "err" : "--" }),
            el("span", { class: "machine-name", text: f.name }),
            el("span", { class: "machine-detail" },
              el("span", { text: f.detail }),
              !f.ok && f.fix
                ? el("span", { class: "machine-fix" }, "fix: ", f.fix)
                : null))))
      : el("p", { class: "muted", text: doc === null
          ? "the studio could not answer /api/doctor"
          : "asking this machine what it has…" }));
}

/** The first three things to do on a machine with no tasks.
 *
 * Ordered by what unblocks the most: a task is the one thing every view
 * needs; an agent is how the task gets written without a repo; a GPU is only
 * needed at the moment of launch. Each step names the control that does it,
 * because "make a task" is advice and "+ New task, in the task switcher" is
 * a place to click. */
function firstSteps(state) {
  const findings = ((state.doctor || {}).findings) || [];
  const has = (name) => findings.some(f => f.name === name && f.ok);
  const tasks = (state.tasks || []).length;
  const steps = [];
  if (!tasks) {
    steps.push([
      "Give it a task.",
      "Either point the studio at a package that registers one ",
      el("code", { text: "--task-package your.module" }),
      " (or ", el("code", { text: "$RLMCP_TASK_PACKAGES" }), "), or make one "
      + "from nothing with ", el("b", { text: "+ New task" }),
      " in the task switcher at the top: the studio makes the directory and "
      + "the agent writes the task into it."]);
  }
  if (!has("agent")) {
    steps.push([
      "Sign an agent in.",
      "The agent panel offers Claude and Antigravity; a sign-in there, or ",
      el("code", { text: "ANTHROPIC_API_KEY" }), " / ",
      el("code", { text: "GEMINI_API_KEY" }), " in the environment, is enough."]);
  }
  if (!has("gpu")) {
    steps.push([
      "Training needs a CUDA GPU on this machine.",
      "Everything else here works without one: building a task, reading "
      + "records, talking to the agent. Launch is the step that will say no."]);
  }
  if (!steps.length) return null;
  return el("div", { class: "first-steps" },
    el("h4", { text: "What to do first" }),
    el("ol", {}, steps.map(([lead, ...rest]) =>
      el("li", {}, el("b", { text: lead + " " }), ...rest))));
}

// Capability that is not built yet says so, and says what would provide it,
// rather than being hidden or crashing.
export function notYet(title, detail, provider) {
  return el("div", { class: "notyet" },
    el("b", { text: title }), detail, " ",
    provider ? el("code", { text: provider }) : null);
}
