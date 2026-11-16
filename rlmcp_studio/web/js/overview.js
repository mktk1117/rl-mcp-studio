// Overview — what the task *is*, as opposed to how any run of it went.
//
// Every other view in this app answers a question about a run: what the curves
// did, what the parameters were, what was concluded. This one answers the
// question asked before all of them and answered nowhere — what does the policy
// see, what do its actions mean, what is it paid for, what ends the episode,
// on which robot, in what scene.
//
// Two halves, and the line between them is the whole design:
//
//   the report   `TASK.md` beside the package, written by an agent that read
//                the code. Prose, pictures and a clip — the half a heuristic
//                cannot produce, because "what this task is about" is a
//                judgement.
//   the facts    the registry row and the last `rlmcp check`: the action
//                count, the joints, the step, the episode length, what each
//                reward term paid. Measured, not written, and labelled as
//                measured so a reader can tell the two apart at a glance.
//
// **It is a definition, never a result.** No curves, no verdicts, no headline
// metric — those have five views already. A learning curve appearing here is a
// bug in the instruction that wrote the document, not a missing feature.
//
// The report is written in a conversation of its own (`report`), so asking for
// one does not queue behind — or fill the context of — the agent you already
// have open on a run.

import { el, add, chip, fmt } from "./ui.js";
import { state, set, buildable, loadOnce } from "./state.js";
import { api } from "./api.js";
import { placed, sideVar } from "./resize.js";
import { renderMarkdown, written } from "./docs.js";

/** Re-read the task's report. Called when a `.md` changes and while one is
 *  being written. */
export function refreshOverview() {
  set({ overviewStamp: null });
}

// While an agent is writing, the document on disk is the thing being watched,
// and nothing publishes an event for a file in the task repository — it is not
// on the watcher's roots. So this view polls, in Build's shape: one timer,
// rescheduled by the paint, and never running for a view that is not on
// screen. An interval left behind by a view you navigated away from repaints
// the whole app every four seconds for the rest of the session.
let timer = 0;

function keepWatching(writing) {
  clearTimeout(timer);
  if (!writing || state.view !== "overview") return;
  timer = setTimeout(() => {
    if (state.view === "overview") refreshOverview();
  }, 4000);
}

export function overviewView(s, actions) {
  if (!buildable(s.task)) {
    return el("div", { class: "notyet" },
      el("b", { text: "No task selected" }),
      "This view is about a task — pick one in the switcher. Records with no "
      + "task on them are a bucket rather than a task, so there is no package "
      + "for a report to describe.");
  }
  loadOnce("overview", `${s.task}::${s.runs ? s.runs.length : 0}`,
           () => api.overview(s.task));
  const payload = s.overview;
  if (!payload) return el("div", { class: "empty", text: "reading the task…" });
  if (payload.error) {
    return el("div", { class: "notyet" },
      el("b", { text: "The task could not be read" }), payload.error);
  }
  keepWatching(!!payload.writing);
  // The wrapper is what the rail's collapse is measured against. An element
  // cannot query its own width -- `@container` asks the nearest *ancestor*
  // container -- so a `container-type` on `.overview` itself would match
  // nothing and leave the rail stacked on a wide stage: a rule that can never
  // be true, which looks like a broken media query.
  return el("div", { class: "ovwrap" },
    el("div", { class: "overview", style: { "--side": sideVar("overviewFacts", s.widths) } },
      reportPane(s, payload, actions),
      factsRail(s, payload, actions),
      placed("overviewFacts", { side: "right", widths: s.widths, actions })));
}

// ── the document ────────────────────────────────────────────────────────

function reportPane(s, payload, actions) {
  const report = payload.report || {};
  const pane = el("div", { class: "ovpane" });
  add(pane, header(s, payload, actions));
  if (s.overviewError) {
    add(pane, el("div", { class: "m-error ovwarn", text: s.overviewError }));
  }
  if (!report.exists) {
    add(pane, emptyReport(payload));
    return pane;
  }
  add(pane,
    s.overviewRaw
      ? el("pre", { class: "doc-source", text: report.text })
      // Two nodes, not one: the scroller is full width and the reading column
      // is centred inside it, so the scrollbar is on the pane and not in the
      // middle of the text. Same reason as the Docs pane.
      : el("div", { class: "md" },
          el("article", { class: "md-body prose" },
            ...renderMarkdown(report.text, report.path, { players: reportPlayers }))));
  return pane;
}

/** The report's clips, kept across the polls while an agent writes it -- see
 *  `video` in docs.js. */
const reportPlayers = new Map();

function header(s, payload, actions) {
  const report = payload.report || {};
  const stale = (report.freshness || {}).stale;
  return el("div", { class: "ovhead" },
    el("span", { class: "fp", text: report.path || "no package on disk" }),
    report.exists ? el("small", { class: "doc-when", text: written(report.at) }) : null,
    // The one thing this view can be confidently wrong about, said on its
    // face: a definition written before somebody changed a reward term reads
    // exactly like a current one. The same clock the Build view's gates use.
    stale
      ? el("span", { class: "chip warn",
                     title: `${(report.freshness || {}).file || "a file"} changed `
                            + `${fmt((report.freshness || {}).by_s, 0)} s after this was written`,
                     text: "code changed since" })
      : null,
    incomplete(payload)
      ? el("span", { class: "chip warn", title: missingTermsTitle(payload),
                     text: `${incomplete(payload)} terms missing` })
      : null,
    payload.writing
      ? chip(payload.needs_you ? "the agent needs you" : "an agent is writing it", true)
      : null,
    el("span", { style: { marginLeft: "auto" } }),
    report.exists
      ? el("button", { class: s.overviewRaw ? "act on" : "act",
                       onclick: () => set({ overviewRaw: !s.overviewRaw }) },
          s.overviewRaw ? "Source" : "Rendered")
      : null,
    payload.writing
      ? el("button", { class: "act",
                       onclick: () => actions.openConversation(
                         `task:${payload.task}`, payload.thread) },
          "Open the conversation")
      // Disabled and renamed for as long as the request is out. Starting a
      // conversation spawns a provider session, which is seconds to tens of
      // seconds, and a button that stays live and unchanged through that is
      // one you press again -- and the second press is refused as "still
      // working on the last message".
      : el("button", { class: report.exists ? "act" : "act primary",
                       disabled: !payload.writable || s.overviewBusy,
                       title: payload.why_not || "",
                       onclick: () => write(payload, actions) },
          s.overviewBusy ? "asking…"
            : report.exists ? "Update it" : "Write the report"));
}

/** Ask for the report, and go and watch it being written.
 *
 * Two things happen on one press, deliberately: a conversation starts *and*
 * the panel is pointed at it. An agent working invisibly in a thread nobody
 * opened is a button that appears to do nothing for a minute or more. */
async function write(payload, actions) {
  if (state.overviewBusy) return;
  set({ overviewError: "", overviewBusy: true });
  try {
    const answer = await api.writeOverview(payload.task, state.overviewNote || "");
    // The note is cleared and the payload re-read *after* the server has the
    // message: by then the thread is busy, so the answer carries `writing`,
    // which is what draws the chip and starts the poll that notices the file.
    set({ overviewNote: "", overviewBusy: false });
    refreshOverview();
    await actions.openConversation(answer.subject, answer.thread);
  } catch (err) {
    set({ overviewError: err.message, overviewBusy: false });
  }
}

/** How many named terms the report does not mention. 0 when it names them all,
 *  and when there is nothing to check against. */
function incomplete(payload) {
  const c = payload.coverage || {};
  return ((c.rewards || {}).missing || []).length
       + ((c.terminations || {}).missing || []).length;
}

function missingTermsTitle(payload) {
  const c = payload.coverage || {};
  return [...((c.rewards || {}).missing || []),
          ...((c.terminations || {}).missing || [])].join(", ");
}

function emptyReport(payload) {
  const box = el("div", { class: "ovempty" },
    el("b", { text: "This task has no report yet" }),
    el("p", { text: "A task report says what the task is — the robot, the "
                    + "scene, what the policy observes, what its actions mean, "
                    + "what it is paid for and what ends an episode. Not how "
                    + "any run of it went: that is what the records are." }),
    el("p", {}, "An agent writes it, reading the package and the last check, "
                + "in a conversation of its own so it does not interrupt the "
                + "one you have open. It lands in ",
      el("code", { text: payload.report.path || "the task's package" }), "."));
  if (payload.why_not) {
    add(box, el("small", { class: "muted", text: payload.why_not }));
    return box;
  }
  add(box,
    el("div", { class: "ovsections" },
      ...(payload.sections || []).map(section =>
        el("div", { class: "ovsection" },
          el("b", { text: section.heading }),
          el("small", { text: section.asks })))),
    // A note is optional and is the difference between a generic definition
    // and one that answers the question you actually have. Kept in the store,
    // not read off the node: a repaint discards the node and a handler that
    // read it would send the empty string.
    el("textarea", { class: "ovnote", rows: 2,
                     placeholder: "anything the report should cover in "
                                  + "particular (optional)",
                     value: state.overviewNote || "",
                     oninput: (e) => { state.overviewNote = e.target.value; } }));
  return box;
}

// ── what the studio measured ────────────────────────────────────────────

function factsRail(s, payload, actions) {
  const facts = payload.facts || {};
  const rail = el("div", { class: "ovrail" });
  add(rail,
    el("div", { class: "ovrail-h" }, el("b", { text: "What the studio knows" }),
      el("small", { text: "the registry, and the last check" })),
    el("div", { class: "ovfacts" },
      ...(facts.rows || []).map(row =>
        el("div", { class: "ovfact", key: row.label },
          el("span", { class: "k", text: row.label }),
          el("span", { class: "v", text: String(row.value) },
            row.from === "check" ? el("i", { class: "src", text: "measured" }) : null)))));

  // Said here rather than at launch, because it is a property of the task and
  // not of a run: two tasks whose RL config names one `experiment_name` write
  // their checkpoints, sessions and progress videos into one directory, and
  // nothing anywhere fails to say so.
  const shared = ((facts.sharing || {}).with || []);
  if (shared.length) {
    add(rail, el("div", { class: "ovhint ovshared" },
      el("b", { text: `Shares its log directory with ${shared.length} other task(s)` }),
      el("p", { text: `${shared.join(", ")} — every one of them writes to `
                      + `logs/<root>/${(facts.sharing || {}).experiment}/, so their `
                      + `checkpoints and progress videos land together, told apart `
                      + `only by the run name.` }),
      el("small", { class: "muted",
                    text: "experiment_name is one line of the task's own RL "
                          + "config; give this task its own and they separate." })));
  }

  if (!facts.checked) {
    add(rail, el("div", { class: "ovhint" },
      el("b", { text: "No check has run" }),
      "The action count, the joints, the step and what each reward term pays "
      + "come from `rlmcp check`. Run it in Build and they appear here — and "
      + "in the report, when it is next written."));
  } else {
    // The completeness line comes *before* the terms it is about: it is the
    // actionable half, and under a long list it is below the fold on exactly
    // the reports that need it read.
    add(rail,
      el("div", { class: "ovrail-h" }, el("b", { text: "Reward terms" }),
        el("small", { text: `${(facts.rewards || []).length}` })),
      covered(payload, "rewards", "reward"),
      terms(facts.rewards, "mean"),
      el("div", { class: "ovrail-h" }, el("b", { text: "Terminations" }),
        el("small", { text: `${(facts.terminations || []).length}` })),
      covered(payload, "terminations", "termination"),
      terms(facts.terminations, "per_step"));
  }

  // Every claim this panel cannot make, said out loud. A missing capability
  // degrades visibly, because a panel that silently omits a number reads as a
  // panel that says the number is not interesting.
  add(rail, el("div", { class: "ovhint" },
    el("b", { text: "Not measured here" }),
    ...(facts.not_measured || []).map(line => el("p", { text: line }))));

  if ((payload.media || []).length) {
    add(rail, el("div", { class: "ovrail-h" },
      el("b", { text: "Media beside the report" }),
      el("small", { text: `${payload.media.length} file(s)` })));
    add(rail, el("div", { class: "ovmedia" },
      ...payload.media.map(item =>
        el("a", { class: "ovmedia-i", key: item.path, href: api.artifactUrl(item.path),
                  target: "_blank", rel: "noopener noreferrer" },
          el("span", { class: "k", text: item.kind }),
          el("span", { class: "v", text: item.name })))));
  }

  if ((payload.report || {}).exists && (payload.missing || []).length) {
    add(rail, el("div", { class: "ovhint" },
      el("b", { text: "Sections it does not have" }),
      el("p", { text: payload.missing.join(", ") }),
      el("small", { class: "muted",
                    text: "Matched on the heading, so a section written under "
                          + "another name counts as written." })));
  }
  return rail;
}

/** Whether the report accounts for every term of one manager.
 *
 * A report listing nine of fourteen reward terms reads complete, and the term
 * somebody is looking for is the one that is not in it. `rlmcp check` names
 * all of them, so this is a fact rather than an impression — and it is matched
 * on the name appearing in the text, which the line says, because a document
 * that mentions a term without defining it would pass. */
function covered(payload, group, word) {
  const answer = (payload.coverage || {})[group];
  if (!answer || !answer.total) return null;
  if (!answer.missing.length) {
    return el("div", { class: "ovcover ok",
                       text: `all ${answer.total} named in the report` });
  }
  return el("div", { class: "ovcover" },
    el("b", { text: `${answer.missing.length} of ${answer.total} `
                    + `${word} terms are not in the report` }),
    el("span", { text: answer.missing.join(", ") }),
    el("small", { text: "matched on the term's name appearing in the text" }));
}

/** One term breakdown, as rl-mcp measured it.
 *
 * The numbers are handed over whole from the same answer the Build view draws
 * as bars — this is a list because the question here is "what are the terms",
 * not "which one dominates", and the view that asks the second one exists. */
function terms(rows, field) {
  if (!(rows || []).length) return null;
  // Every one of them, never the first few: this rail is what the report
  // is checked against, and a list that truncates is a list that disagrees
  // with the count printed beside its own heading.
  return el("div", { class: "ovterms" },
    ...rows.map(row =>
      el("div", { class: "ovfact", key: row.term },
        el("span", { class: "k", text: row.term }),
        el("span", { class: "v", text: fmt(row[field], 4) }))));
}
