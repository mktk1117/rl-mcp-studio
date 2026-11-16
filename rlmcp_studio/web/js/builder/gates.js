// The gate strip: `rlmcp check`'s answers, and whether the code has changed
// since they were given.

import { el, fmt, add } from "../ui.js";

// rl-mcp's order, which is the order they fail in. The first five build the
// environment and step it with a zero policy — a callable returning zeros,
// which never constructs the PPO actor — so a hole in the task's `rl_cfg` is
// invisible to every one of them and shows up at iteration 0 of the first real
// run. `trains` builds what `rlmcp train` builds and takes one iteration on it.
const GATE_ORDER = ["imports", "constructs", "steps", "rewards_finite",
                    "terminations", "trains"];

const GATE_LABEL = {
  imports: "imports",
  constructs: "constructs",
  steps: "steps",
  rewards_finite: "rewards finite",
  terminations: "terminations",
  trains: "trains",
};

// Three clocks, and this is what they are for.
//
// The preview holds the environment it constructed when it started. The gate
// strip holds the answer `rlmcp check` gave when it ran. Editing a file
// invalidates both, and neither notices: the gates come back green on the new
// code while the preview is still stepping the old one, on the same screen,
// with nothing to say they are about different versions of the task. It is the
// one way this view can be confidently wrong.
//
// `build.code.at` is the newest mtime under the task's package, from the
// server. Anything built before it is out of date, and says so.

export function staleBy(build, at) {
  const changed = ((build || {}).code || {}).at || 0;
  if (!changed || !at) return 0;
  return changed > at ? changed - at : 0;
}

/** The file whose change made this answer old. */
export function staleFile(build) {
  const file = String(((build || {}).code || {}).file || "");
  return file.split("/").slice(-1)[0] || "a file in the task";
}

/** "rewards.py, 331 s later" -- for a tooltip, where there is no room to say it. */
export function staleNote(build, at) {
  const gap = staleBy(build, at);
  return gap ? `${staleFile(build)} changed ${fmt(gap, 0)} s later` : "";
}

// ── the gates ───────────────────────────────────────────────────────────

export function gateStrip(build, s, actions) {
  const gates = build.gates || {};
  const answer = gates.answer;
  const rows = (answer && answer.gates) || [];
  const byName = {};
  for (const row of rows) byName[row.gate] = row;

  const strip = el("div", { class: "gates" });

  for (const name of GATE_ORDER) {
    const row = byName[name];
    // Three states, never two: a gate that did not run is not a gate that
    // passed, and drawing it as one is how a broken task comes back looking
    // mostly fine.
    const cls = !row ? "gate wait"
      : row.ok === true ? "gate ok"
      : row.ok === false ? "gate fail" : "gate wait";
    add(strip, el("span", { class: cls, title: row ? (row.detail || "") : "not run yet" },
      el("span", { class: "tick", text: !row ? "·" : row.ok === true ? "✓" : row.ok === false ? "✗" : "·" }),
      GATE_LABEL[name] || name,
      row && row.detail && row.ok === true
        ? el("span", { class: "gate-d", text: ` ${row.detail}` }) : null));
  }

  add(strip,
    gates.running
      ? el("span", { class: "stamp", text: "checking…" })
      : answer
        ? el("span", { class: `stamp${staleBy(build, answer.at) ? " warn" : ""}`,
                       title: staleBy(build, answer.at)
                         ? staleNote(build, answer.at)
                         : (answer.error || ""),
                       text: staleBy(build, answer.at)
                         ? "answered before your last edit"
                         : answer.ok
                           ? `${answer.device || ""} · ${answer.steps || 0} steps · ${fmt(gates.age_s, 0)} s ago`
                           : "the check could not run" })
        : el("span", { class: "stamp", text: "not checked yet" }),
    el("span", { class: "sp" }),
    el("button", { class: "act", disabled: gates.running || undefined,
                   title: "build it, roll it, build the runner, and answer the "
                        + "six questions",
                   onclick: () => actions.runCheck() },
      gates.running ? "checking…" : answer ? "Check again" : "Run the checks"));

  const failed = rows.find(r => r.ok === false);
  const wrap = el("div", { class: "gates-wrap" }, strip);
  if (answer && !answer.ok && answer.error) {
    add(wrap, el("div", { class: "m-error", text: answer.error }));
  } else if (!failed && answer && answer.will_train === null) {
    // Nothing failed and the one gate that reads the RL config did not run --
    // an rl-mcp that does not have it, or a check that skipped it. Written out
    // rather than left to one pale cell at the end of five green ones, because
    // a strip that is green except for a grey tick is read as green, and that
    // reading is what puts a run into iteration 0 with no actor.
    add(wrap, el("div", { class: "gate-fix" },
      el("b", { text: "Nothing failed, and that does not yet mean it will train. " }),
      (byName.trains || {}).detail || "The trains gate did not run.",
      el("p", { text: "The gates before it step the environment with a zero "
                    + "policy, which never constructs the PPO actor — so a "
                    + "missing field in this task's own RL config is invisible "
                    + "to all of them, and appears at iteration 0 of the first "
                    + "real run." })));
  } else if (failed) {
    // The fix, not just the failure: which one applies depends on which gate
    // failed, and guessing wrong sends somebody to look at working code.
    //
    // And a button to hand it over, because the traceback is on this screen
    // and the agent is in the next panel, and copying text between the two by
    // hand is the alternative. What it sends is what is on screen -- gate,
    // detail, fix, and whatever traceback came with it.
    add(wrap, el("div", { class: "gate-fix" },
      el("b", { text: `${GATE_LABEL[failed.gate] || failed.gate}: ` }),
      failed.detail || "",
      failed.fix ? el("p", { text: failed.fix }) : null,
      el("button", { class: "act", disabled: s.agentBusy || undefined,
                     title: "send this gate to the agent, as it is written here",
                     onclick: () => actions.askAgent(gateBrief(failed, answer)) },
        s.agentBusy ? "the agent is working…" : "Ask the agent to fix it")));
  }
  return wrap;
}

/** What to say to the agent about a gate that failed.
 *
 * Quoted rather than summarised. The gate's own `detail` is the environment's
 * traceback and the `fix` is rl-mcp's sentence about that class of failure;
 * paraphrasing either would be the studio putting words in the mouth of the
 * thing that actually knows.
 */
function gateBrief(failed, answer) {
  const lines = [
    `The \`${failed.gate}\` gate failed. This is what \`rlmcp check\` said:`,
    "",
    failed.detail || "(no detail)",
  ];
  if (failed.fix) lines.push("", failed.fix);
  for (const key of ["offenders", "traceback"]) {
    const extra = failed[key];
    if (Array.isArray(extra) && extra.length) lines.push("", extra.join("\n"));
    else if (typeof extra === "string" && extra) lines.push("", extra);
  }
  const ends = (answer && answer.terminations) || [];
  if (failed.gate === "terminations" && ends.length) {
    lines.push("", "Termination terms, per step: "
      + ends.map(e => `${e.term} ${(e.per_step * 100).toFixed(1)}%`).join(", "));
  }
  lines.push("", "Fix it in the task package and tell me when to check again.");
  return lines.join("\n");
}
