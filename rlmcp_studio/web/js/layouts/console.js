// Console — one column, the run as a stream. No tabs, nearly no navigation.

import { el, dot, on, add } from "../ui.js";
import { verbs, claimList, metricCards, notYet, feedbackCard, robotCard, closeCard, nothingHere, noRunsYet } from "../components.js";
import { VIEWS, renderView, TASK_VIEWS } from "../views.js";
import { transcriptPane } from "../agent.js";
import { composer } from "../agent/composer.js";

export const meta = { id: "console", name: "Console", blurb: "One column, the run as a stream" };

export function render(state, actions) {
  const run = state.runs.find(r => r.id === state.runId);
  const live = state.live;

  const stream = el("div", { class: "c-inner" });
  const shared = renderView(state.view, state, actions);
  if (shared) {
    stream.append(shared);
    // The composer is at the bottom of every view, so the conversation has to
    // be reachable from every view. Rendered only under the default stream,
    // you could ask a question from the Story view, watch the box clear, and
    // see nothing -- the answer would be on a page you are not on.
    add(stream, agentEntry(state));
    return shell(state, actions, run, live, stream);
  }

  if (TASK_VIEWS.has(state.view)) {
    const shared = renderView(state.view, state, actions);
    if (shared) stream.append(shared);
  } else if (!run) {
    stream.append(!(state.tasks || []).length ? nothingHere(state)
      : !(state.runs || []).length ? noRunsYet(state)
      : el("div", { class: "empty", text: "no run selected" }));
  } else {
    stream.append(entry(run.date, "record", claimList(state.detail)));
    // How it ended, or how to say how it ended. The Console has no Record tab
    // -- every view is this stream -- so a run closed from Workbench and a run
    // closed from here have to be closable in the same place the claim is.
    add(stream, entry("", "verdict", closeCard(state, actions)));

    if (run.change && run.change.length) {
      stream.append(entry("", "change from parent", el("div", { class: "block" },
        el("div", { class: "block-b" },
          run.change.map(c => el("div", { class: "kv" }, el("span", { text: c })))))));
    }

    if (live && live.ok && Object.keys(live.series || {}).length) {
      stream.append(entry(iterOf(live), "metrics", metricCards(live.series, 3, live)));
    } else {
      stream.append(entry("", "metrics", notYet(
        "No live session",
        live && live.reason ? live.reason : "this run has exited; its record is still here.",
        run.session || "")));
    }

    // The verbs are in the composer bar of *every* Console view, so their
    // results have to be in the stream: Look advances `state.lastFrame` and
    // Diagnose fills `state.diagnosis`, and a stream that rendered neither
    // would be pressing a button, getting a 200, and seeing nothing at all --
    // the worst thing this app can do.
    // Without verbs: this layout's are in the composer bar at the bottom,
    // where they act on whatever is on screen, and a second copy in the card
    // header would be two buttons for one act. The other two layouts have no
    // composer bar, so the card carries them there.
    add(stream, entry(clipStamp(state), "what it looks like",
                      robotCard(state, actions, { withVerbs: false })));

    // The Console is the layout for talking to a run, and without this the
    // composer at the bottom would send messages whose answers appear only in
    // a panel this layout does not show. The conversation belongs in the
    // stream.
    add(stream, agentEntry(state));

    if (run.metrics && run.metrics.length) {
      stream.append(entry("", "measured", el("div", { class: "block" },
        el("div", { class: "block-b" },
          run.metrics.map(([k, v]) => el("div", { class: "kv" },
            el("span", { text: k }), el("span", { text: String(v) })))))));
    }

    stream.append(entry("", "feedback", feedbackCard(state, actions)));

    const feedback = [];
    for (const f of feedback) {
      stream.append(entry("", f.kind, el("div", { class: "block" },
        el("div", { class: "block-b" },
          el("div", { text: f.text }),
          f.response
            ? el("div", { style: { marginTop: "5px", color: "var(--muted)", fontSize: "11.5px" },
                          text: `→ ${f.response}` })
            : el("div", { class: "k", style: { marginTop: "5px", color: "var(--warn)" },
                          text: "unanswered" })))));
    }
  }

  return shell(state, actions, run, live, stream);
}

function shell(state, actions, run, live, stream) {
  return el("div", { class: "console" },
    el("div", { class: "c-top" },
      run ? dot(run.verdict, run.live === true) : dot("planned"),
      el("span", { class: "id", text: run ? run.id : "—" }),
      el("span", { class: "slug", text: run ? run.slug : "" }),
      el("span", { class: "spacer", style: { marginLeft: "auto" } }),
      // It is a link to the Story view, not a menu -- said in the label,
      // because "Runs" beside a count reads as something that opens a list
      // in place.
      el("button", { class: "act", title: "one card per run, in this task",
                     onclick: actions.openRuns },
        "All runs", el("small", { text: String(state.runs.length) }))),
    streamBox(stream, state),
    el("div", { class: "c-composer" },
      el("div", { class: "c-comp-inner" },
        verbs({ variant: "chip", state, actions,
                live: !!(live && live.ok && live.state === "running") }),
        // The agent's own composer, not a second copy of one: this is the
        // layout whose whole idea is talking to the run, so the box that looks
        // like the way to do it has to be one.
        el("div", { class: "c-input" }, composer(state.agent || {})),
        el("div", { class: "c-hints" },
          // `/control` is in the list: without it, choosing any other view is
          // a one-way trip -- the chip row offers no way back to the run, the
          // "Runs" button goes to Story, and the header is not a button.
          VIEWS.map(v => hint(`/${v.id}`,
                              v.id === "control" ? "the run" : v.label.toLowerCase(),
                              () => actions.setView(v.id), state.view === v.id))))));
}

// Follow the stream only while you are *at* the stream. Sending a message and
// seeing nothing move -- the answer on screen, four screens down -- is the
// same bug as a list that will not scroll: the app and the person disagree
// about where they are.
let following = true;
let lastShown = "";
let lastVerb = "";

function streamBox(stream, state) {
  const box = el("div", { class: "c-stream" }, stream);
  on(box, "scroll", (e) => {
    const node = e.currentTarget;
    following = node.scrollHeight - node.scrollTop - node.clientHeight < 60;
  });
  // What you *asked* for wins over what is arriving. Otherwise choosing
  // `/params` from the hint row, or pressing Diagnose, leaves its result far
  // above the scroll position while the stream follows the conversation at
  // the bottom -- the "press a button, get a 200, see nothing" case again.
  const showing = `${state.view}:${state.runId}`;
  const changed = showing !== lastShown;
  lastShown = showing;

  // A verb's answer is mid-stream, so following the conversation hides it too.
  const verb = `${state.lastFrame || ""}|${(state.diagnosis || {}).verdict || ""}`;
  const answered = !changed && verb !== lastVerb && lastVerb !== "";
  lastVerb = verb;

  queueMicrotask(() => {
    const live = document.querySelector(".c-stream");
    if (!live) return;
    if (changed) { live.scrollTop = 0; following = false; return; }
    if (answered) {
      const card = live.querySelector(".diagnosis, .frame-wrap");
      if (card) { card.scrollIntoView({ block: "center" }); following = false; return; }
    }
    if (following) live.scrollTop = live.scrollHeight;
  });
  return box;
}

/** The conversation, wherever the Console is showing it. */
function agentEntry(state) {
  if (!(state.transcript || []).length) return null;
  return entry("", `agent · ${state.thread}`,
               el("div", { class: "c-agent" }, transcriptPane(state.agent)));
}

function entry(when, who, body) {
  return el("div", { class: "e" },
    el("span", { class: "e-t", text: when || "" }),
    el("div", { class: "e-b" },
      el("div", { class: "e-who", text: who }),
      body));
}

function hint(cmd, what, onclick, on = false) {
  return el("button", { class: `hint${on ? " on" : ""}`, onclick },
    el("b", { text: cmd }), " ", what);
}

function clipStamp(state) {
  const at = state.clips && state.clips.latest_iteration;
  return at === null || at === undefined ? "" : `it ${Number(at).toLocaleString("en-US")}`;
}

function iterOf(live) {
  const s = (live && live.status) || {};
  const it = s.iteration ?? s.iter;
  return it === undefined || it === null ? "" : Number(it).toLocaleString("en-US");
}
