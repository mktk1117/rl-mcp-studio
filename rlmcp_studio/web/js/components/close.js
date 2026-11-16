// Closing a run out, and the feedback ledger beside it.

import { el, dot, chip, verdictColor, on } from "../ui.js";
import { set } from "../state.js";
import { KINDS } from "../annotate.js";

// ── closing a run out ────────────────────────────────────────────────────
//
// Without this, a run launched from the studio stays `running` for ever, and
// the status bar shows that word beside `trainer gone`; closing it would take
// the CLI, which is the terminal this view exists to remove.
//
// What a verdict *means* is rl-mcp's, not ours. This collects
// three sentences and a list of measurements and hands them to
// `rlmcp record close`, which gathers the evidence, folds in what was said to
// the live trainer, evaluates the falsifier and refuses the verdicts a record
// has not earned. Its refusals are printed here word for word: "`validated`
// is not available to a warm-started run" teaches the rule, and any
// sentence this file wrote instead would be a second opinion about the one
// thing this app must not have a second opinion about.

/** The opening of a sentence, for a placeholder that has to fit on a line. */
function firstWords(text, n) {
  const line = String(text || "").replace(/\s+/g, " ").trim();
  return line.length > n ? `${line.slice(0, n)}…` : line;
}

export function closeCard(state, actions) {
  const detail = state.detail;
  if (!detail) return null;
  const record = detail.record || {};
  const words = (state.config || {}).verdicts || {};
  const open = words.open || ["planned", "running"];
  const terminal = words.terminal || [];
  const results = words.result || [];
  const verdict = record.verdict || "";

  const head = el("div", { class: "card-h" },
    el("h3", { text: "How it ended" }),
    verdict ? chip(verdict, open.includes(verdict)) : null,
    el("span", { class: "sp" }),
    state.closeOpen
      ? el("button", { class: "act", onclick: () => set({ closeOpen: false }) },
          "Never mind")
      : null);

  // `interrupted` is the one closed verdict rl-mcp lets you close again: a job
  // that died is not a result, and it still owes a real close-out.
  const closable = open.includes(verdict) || verdict === "interrupted";
  if (!closable) {
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b close-out" },
        el("p", { class: "muted", text: record.line || "closed." }),
        el("p", { class: "muted",
                  text: "Closed. Closing it again would duplicate its evidence "
                      + "rows, so rl-mcp refuses — reopening one means editing "
                      + "the verdict in its meta.json by hand." }),
        // Only just closed, from here: the write-up rl-mcp made is a file
        // somebody may want to open, and naming it is cheaper than hunting
        // for it in the records directory.
        state.closeReport
          ? el("p", { class: "muted" }, "rl-mcp wrote ",
              el("code", { text: state.closeReport }))
          : null));
  }

  // The words are rl-mcp's, carried in `/api/config`. An empty list means a
  // studio whose backend predates them, and an empty row of buttons under the
  // word "verdict" would read as a control that does nothing.
  if (!terminal.length) {
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b" },
        el("div", { class: "notyet" },
          el("b", { text: "No verdict vocabulary" }),
          "this studio's backend did not publish one, and the words a verdict "
          + "may be belong to rl-mcp rather than to this page. Closing it takes "
          + "a terminal until it does: ",
          el("code", { text: `rlmcp record close ${record.id || "<id>"} <verdict>` }))));
  }

  // Read-only until asked. The human says what they saw to the *agent* and the
  // agent writes the record -- that is the loop this app is for, and a form
  // sitting open beside the transcript is a second habit that ends with the
  // agent not knowing what was concluded.
  //
  // The form does not go away, though, and that is deliberate. Closing a run
  // is a *decision* rather than a comment, rl-mcp judges the verdict and can
  // refuse it, and an agent that is signed out would otherwise leave a run
  // that cannot be closed at all. So: behind a control, one click away.
  if (!state.closeOpen) {
    // A run whose trainer has gone is the case this control exists for, and
    // saying so here is how the app stops reading as though the run were still
    // training: the verdict says `running` because nobody has closed it, not
    // because anything is.
    const trainer = state.live && state.live.ok ? state.live.state : "";
    const gone = verdict === "running" && trainer && trainer !== "running";
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b close-out" },
        el("p", { class: "muted",
                  text: (gone ? "Its trainer is gone, and the record stays "
                              + "“running” until it is closed. "
                              : "Still open. ")
                      + "Tell the agent what you concluded and it "
                      + "closes the record — the verdict is rl-mcp's to judge, "
                      + "and it can refuse one the evidence does not carry." }),
        el("button", { class: "act",
                       title: "close it from here instead, without the agent",
                       onclick: () => set({ closeOpen: true }) },
          "Close it myself")));
  }

  const picked = state.closeVerdict || "";
  const claimsResult = results.includes(picked);

  const headline = el("input", {
    class: "fb-input", type: "text", maxlength: "300",
    // Clipped: `line` falls back to the hypothesis when no headline is set,
    // and a hypothesis is a paragraph. The whole of one in a placeholder is
    // unreadable and pushes the field's own purpose off the end of it.
    placeholder: record.line
      ? `one sentence for the tree (now: ${firstWords(record.line, 48)})`
      : "one sentence for the tree",
    value: state.closeHeadline || "",
  });
  on(headline, "input", (e) => { state.closeHeadline = e.currentTarget.value; });

  const outcome = el("textarea", { class: "fb-input why", rows: "3",
    placeholder: "what actually happened, in your words" });
  // A textarea's text is a property, not an attribute -- see the launch form.
  outcome.value = state.closeOutcome || "";
  on(outcome, "input", (e) => { state.closeOutcome = e.currentTarget.value; });

  const measured = el("input", { class: "fb-input", type: "text",
    placeholder: "hf_share=0.11, Train/mean_reward=1.9",
    value: state.closeMetrics || "" });
  on(measured, "input", (e) => { state.closeMetrics = e.currentTarget.value; });

  const words_row = el("div", { class: "verdicts" },
    terminal.map(name => el("button", {
      class: "act verdict-pick", "aria-pressed": picked === name ? "true" : "false",
      style: picked === name ? { borderColor: verdictColor(name), color: verdictColor(name) } : {},
      onclick: () => actions.pickVerdict(name),
    }, dot(name), name)));

  return el("div", { class: "card" }, head,
    el("div", { class: "card-b close-out" },
      el("label", {}, el("span", { text: "headline" }), headline),
      el("div", { class: "eyebrow", text: "verdict" }),
      words_row,
      el("label", {}, el("span", { text: "outcome" }), outcome),
      el("label", {}, el("span", { text: "measured" }), measured),
      // Said before the refusal rather than after it. rl-mcp will not let a
      // run claim a result with nothing to point at, and finding that out by
      // being refused is a worse way to learn it.
      claimsResult
        ? el("p", { class: "muted",
                    text: `\u201c${picked}\u201d claims a result, so it needs an outcome `
                        + "and at least one measurement. A claim without a "
                        + "measurement is not knowledge." })
        : null,
      state.closeError ? el("div", { class: "m-error", text: state.closeError }) : null,
      state.closeReport
        ? el("p", { class: "muted" }, "Closed. rl-mcp wrote ",
            el("code", { text: state.closeReport }))
        : null,
      el("div", { class: "fb-row" },
        el("span", { class: "eyebrow",
                     text: "this writes the record, and rl-mcp judges it" }),
        el("span", { class: "sp" }),
        el("button", { class: "act primary",
                       disabled: !picked || !!state.closeBusy || undefined,
                       onclick: () => actions.closeRun() },
          state.closeBusy ? "closing\u2026" : picked ? `Close as ${picked}` : "Pick a verdict"))));
}

// rl-mcp's feedback ledger, made reachable. Six kinds; the four that ask for
// something are owed an answer, and the record tracks which are outstanding.
// This is the feature that only becomes real in a GUI -- nobody types
// `rlmcp feedback` in the middle of watching a run. The kinds come from
// annotate.js so the box-on-a-frame composer and this box offer the same six.

export function feedbackCard(state, actions) {
  const entries = (state.detail && state.detail.feedback) || [];
  const open = entries.filter(f => !f.response &&
    ["steer", "correct", "reject", "approve"].includes(f.kind));

  const list = el("div", { class: "ledger" });
  if (!entries.length) {
    list.append(el("div", { class: "empty",
      text: "nothing said about this run yet" }));
  }
  for (const f of entries) {
    // A record written before the kinds were validated can hold anything.
    // `${f.kind}` on an object renders "[object Object]"; a record is read by
    // people, so it says what it can and admits the rest.
    const kind = typeof f.kind === "string" && KINDS.includes(f.kind) ? f.kind : "unknown";
    const owed = !f.response && ["steer", "correct", "reject", "approve"].includes(kind);
    // A remark made by pointing at a frame carries the frame with the box on
    // it. Showing it here is the point of writing it down: months later the
    // sentence alone would not say which wrist, at which moment.
    const shown = (f.images || [])[0];
    list.append(el("div", { class: "entry" },
      el("span", { class: `kind ${kind}`, text: kind }),
      el("div", {},
        el("div", { class: "said", text: f.text }),
        shown ? el("button", {
          class: "annot-shot", title: "the frame this was said about",
          onclick: () => actions.showFrame(shown),
        }, el("img", { src: state.artifactUrl(shown), alt: "annotated frame" }),
           f.iteration !== null && f.iteration !== undefined
             ? el("span", { class: "cap", text: `it ${Number(f.iteration).toLocaleString("en-US")}` })
             : null) : null,
        f.response
          ? el("div", { class: "answer", text: `→ ${f.response}` })
          : owed ? el("div", { class: "open", text: "unanswered" }) : null)));
  }

  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Feedback" }),
      open.length ? chip(`${open.length} unanswered`) : null,
      el("span", { class: "sp" }),
      el("span", { class: "eyebrow", text: `${entries.length} on the record` })),
    el("div", { class: "card-b" },
      list,
      // No box. This panel *shows* the ledger; the way to add to it is to say
      // so in the agent panel, and the agent writes it -- `record_feedback`
      // and `add_note` are approval-free precisely so that capturing what was
      // observed never waits on a prompt. Two places to write the same thing
      // would mean two habits, and the one here would leave the agent not
      // knowing what had been said.
      el("div", { class: "fb-hint",
                  text: entries.length
                    ? "Say it to the agent and it goes on the record here."
                    : "Nothing said yet. Tell the agent what you see — it "
                      + "writes to this ledger." })));
}
