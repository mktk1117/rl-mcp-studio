// A record, small: its claim, what it measured, and how it differs from its parent.

import { el, dot, chip, fmtVal, verdictColor } from "../ui.js";
import { set } from "../state.js";
import { api } from "../api.js";
import { notYet } from "./rail.js";

// A record, small enough to sit on Control.
//
// Control is where you are while the run is going, and the two questions you
// ask there are "what did I claim" and "what did I change" -- both currently
// two clicks away in Record and Diff. Neither needs its full page here: the
// claim needs its falsifier and whether it still holds, and the diff needs the
// handful of parameters that actually differ.
/** What the run measured, and the falsifier's own answer among it.
 *
 * Here rather than in a layout: two layouts draw it, and a component that
 * two layouts draw does not live inside one of them.
 */
export function measuredCard(run, state, actions) {
  const rows = (run && run.metrics) || [];
  return el("div", { class: "card" },
    el("div", { class: "card-h" }, el("h3", { text: "Measured" })),
    el("div", { class: "card-b" },
      rows.length
        ? rows.map(([k, v]) => measuredRow(k, v))
        // Measurements are the evidence for a verdict, and rlmcp writes them
        // when the run is closed with one -- which is also why the studio does
        // not offer to snapshot the live numbers here: training-time values
        // are not an evaluation.
        : notYet("Nothing measured yet.",
                 " Measurements are written when the run is closed with a "
                 + "verdict, in the card below.", "")));
}

/** One measured value.
 *
 * `falsifier` is not a measurement like the others -- it is the answer to
 * whether the claim survived, which is what the whole record is for -- so it
 * is not drawn as one more grey row in a list of grey rows.
 */
function measuredRow(key, value) {
  const text = String(value);
  if (key !== "falsifier") {
    return el("div", { class: "kv" }, el("span", { text: key }),
              el("span", { text }));
  }
  const held = /held|survived/i.test(text);
  return el("div", { class: "kv falsifier" },
    el("span", { text: "falsifier" }),
    el("span", {},
      dot(held ? "validated" : "falsified"),
      el("b", { style: { color: verdictColor(held ? "validated" : "falsified") },
                text: held ? "held" : "fired" }),
      el("span", { class: "muted", text: text.replace(/^(held|fired)\s*/i, " ") })));
}

export function recordBrief(state, actions) {
  const detail = state.detail || {};
  const record = detail.record || {};
  const falsifier = detail.falsifier || {};
  const line = record.line || detail.hypothesis || "";

  // The record's claim, and -- only when it has stopped being true -- the
  // trainer's state beside it. `running` is what a record says from the moment
  // it opens until somebody closes it out, which is hours after the process
  // ended: the word alone, in the panel headed "This run", reads as a live
  // claim and would be the thing on screen most often wrong.
  const trainer = state.live && state.live.ok ? state.live.state : "";
  const stale = record.verdict === "running" && trainer && trainer !== "running";

  const head = el("div", { class: "card-h" },
    el("h3", { text: "This run" }),
    record.verdict ? el("span", { class: "chip",
      style: { color: verdictColor(record.verdict), borderColor: verdictColor(record.verdict) },
      text: record.verdict }) : null,
    stale ? el("span", { class: "chip",
      style: { color: "var(--dim)", borderColor: "var(--line)" },
      title: "the verdict is the record's claim about the experiment; this is "
           + "the process. Close the run out to make them agree.",
      text: trainer === "stalled" ? "trainer stalled" : "trainer gone" }) : null,
    el("span", { class: "sp" }),
    // The comparison lives in Code ("source"). `setView` silently drops an id
    // it does not know, so a wrong id here is a button that does nothing.
    el("button", { class: "act", onclick: () => { set({ changedOpen: true });
                   actions.setView("source"); } }, "Diff"));

  // The whole claim, not a line of it: hypothesis / prediction / falsifier /
  // outcome belong in the same panel as the run, not one click away in a tab
  // whose name would sit one letter from Records.
  const body = el("div", { class: "card-b" });
  const full = claimList(state.detail);
  if (full && full.className !== "empty") body.append(full);
  else if (line) body.append(el("div", { class: "brief-line", text: line }));

  if (falsifier.prose) {
    // The falsifier is the half of a claim that can be wrong, so it carries
    // its own state rather than sitting in the prose as a sentence.
    const fired = detail.falsifier_state || record.falsifier_state || "";
    body.append(el("div", { class: "brief-fals" },
      el("span", { class: "eyebrow", text: "falsifier" }),
      el("span", { class: "brief-fals-t", text: falsifier.prose }),
      fired ? chip(fired, fired !== "held") : null));
  }
  body.append(keyDiff(state, actions));
  return el("div", { class: "card" }, head, body);
}

// The parameters that differ from the parent, and nothing else.
function keyDiff(state, actions) {
  const diff = state.diff;
  const runId = state.runId;
  if (!runId) return null;
  if (!diff || diff.record !== runId) {
    if (state.diffStamp !== runId) {
      set({ diffStamp: runId });
      api.diff(runId).then(d => { if (state.diffStamp === runId) set({ diff: { ...d, record: runId } }); })
        .catch(() => {});
    }
    return el("div", { class: "empty", text: "reading the diff…" });
  }
  if (diff.no_parent) {
    return el("div", { class: "brief-none", text: "from scratch — no parent to differ from" });
  }
  const config = diff.config || { changed: [], added: [], removed: [] };
  const rows = [...config.changed, ...config.added, ...config.removed];
  const moved = (config.moved_after_launch || []).length;
  const compared = config.compared === undefined ? config.total : config.compared;
  if (!rows.length) {
    // Scoped to the launch config. A bare "identical to <parent>" about two
    // *launch snapshots*, beside a record whose own words say a weight went
    // from 3.0 to 8.0, is true and still reads as the app contradicting itself.
    return el("div", { class: "brief-none",
      text: `same launch config as ${diff.against} across all ${compared} recorded fields`
        + (moved ? ` — but ${moved} of them moved during the run` : "") });
  }
  // Three kinds, and they are not the same claim. A *removed* parameter has no
  // new value, and rendering it as `old → old` would say "changed" over two
  // identical numbers -- parameters a run simply no longer has would read as
  // no-op edits.
  const kinds = [
    ["changed", config.changed, (r) => [el("span", { class: "old", text: fmtVal(r.old) }),
                                        el("span", { class: "new", text: fmtVal(r.new) })]],
    ["added", config.added, (r) => [el("span", { class: "new", text: `+ ${fmtVal(r.new)}` })]],
    ["gone", config.removed, (r) => [el("span", { class: "old", text: `− ${fmtVal(r.old)}` })]],
  ].filter(([, list]) => list.length);

  const summary = kinds.map(([label, list]) => `${list.length} ${label}`).join(" · ");
  const wrap = el("div", { class: "brief-diff" },
    // "at launch", because that is all these two snapshots cover.
    el("span", { class: "eyebrow", text: `vs ${diff.against} at launch — ${summary}` }),
    // Not "of these": the field that moved is one of the compared fields, and
    // is usually not one of the handful listed below as differing at launch.
    moved ? el("span", { class: "brief-none",
      text: `${moved} launch field${moved === 1 ? "" : "s"} moved during the run` }) : null);

  let shown = 0;
  for (const [label, list, render] of kinds) {
    for (const row of list) {
      if (shown >= 6) break;
      shown += 1;
      wrap.append(el("div", { class: `dline ${label === "gone" ? "removed" : label}` },
        el("span", { class: "dk", text: row.key }),
        el("span", { class: "dv" }, render(row))));
    }
  }
  if (rows.length > shown) {
    wrap.append(el("button", { class: "act", onclick: () => { set({ changedOpen: true });
                               actions.setView("source"); } },
      `${rows.length - shown} more`));
  }
  return wrap;
}

export function claimList(detail) {
  if (!detail) return el("div", { class: "empty", text: "select a run" });
  const rows = [
    ["hypothesis", detail.hypothesis],
    ["prediction", detail.prediction],
    ["falsifier", detail.falsifier && detail.falsifier.prose],
    ["outcome", detail.outcome],
  ].filter(([, v]) => v);
  if (!rows.length) return el("div", { class: "empty", text: "this record has no claim written" });
  const dl = el("dl", { style: { display: "grid", gridTemplateColumns: "auto minmax(0,1fr)",
                                 gap: "6px 14px", margin: "0" } });
  for (const [k, v] of rows) {
    dl.append(el("dt", { class: "eyebrow", style: { paddingTop: "2px" }, text: k }));
    dl.append(el("dd", { style: { margin: "0", fontSize: "12.5px" }, text: v }));
  }
  return dl;
}
