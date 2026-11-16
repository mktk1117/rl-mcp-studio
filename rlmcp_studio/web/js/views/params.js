// Parameters: the knobs a run exposes, and how they moved over the run.

import { el, clear, chip, fmt, fmtVal, sparkline } from "../ui.js";
import { api } from "../api.js";
import { placed, sideVar } from "../resize.js";
import { set, loadOnce } from "../state.js";
import { notYet } from "../components.js";
import { typed, ctx, escape } from "./shared.js";

// ── parameters, over iterations ─────────────────────────────────────────
// Parameters answers two questions and shows both: what the knobs are *now*
// (and lets you turn them), and how they moved over the run.
export function paramsView() {
  // History first: "what moved, and when" is the question the view is opened
  // with, and the table answers "what is it now". `traceSection` stays -- it
  // draws the *live* trace of one key from the running session, which is a
  // different thing from the ancestry the history draws.
  const wrap = el("div", { class: "paramview-outer" },
    historySection(), paramTable(), traceSection());
  return wrap;
}

// ── how a parameter moved ───────────────────────────────────────────────
//
// The question Parameters is opened with is "what moved, and when", and the
// table answers "what is it now". rl-mcp's `build_history` answers the first
// -- runs in draw order with their x offsets, a series of points per key, and
// an index of the keys that ever moved -- so the studio draws it and derives
// none of it. Each point knows where it came from and a live edit carries the
// *reason* that was given, which is the half of "what changed" no chart can
// show and the half a reader most wants.

function historySection() {
  const s = ctx.state;
  loadOnce("history", s.task || "", () => api.paramHistory(s.task));
  const payload = s.history;
  if (!payload) return el("div", { class: "empty", text: "reading the history…" });
  if (payload.error) return null;

  const index = payload.index || [];
  const moved = index.filter(e => e.changes > 0);
  const shown = s.historyAll ? index : moved;
  if (!index.length) return null;

  // All of them at once is the default. "What moved, and when" is a question
  // about the set, not about one key: with one chart on screen the answer
  // takes as many clicks as there are parameters, and comparing two of them is
  // impossible. Picking a key zooms into it; the "All at once" chip comes back.
  const key = shown.find(e => e.key === s.historyKey) ? s.historyKey : "";
  const tiles = shown.slice(0, GRID_MAX);

  return el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "How it moved" }),
      moved.length
        ? chip(`${moved.length} changed`)
        : chip("nothing was changed mid-run"),
      el("span", { class: "sp" }),
      // The keys nobody touched are a flat line at the inherited value, which
      // is information -- it says nobody moved it -- but it is not what you
      // opened this for, so it is behind a button.
      index.length > moved.length
        ? el("button", { class: s.historyAll ? "act on" : "act",
                         onclick: () => set({ historyAll: !s.historyAll }) },
            s.historyAll ? `Only the ${moved.length} that changed`
                         : `All ${index.length}`)
        : null),
    el("div", { class: "card-b" },
      el("div", { class: "hist-keys" },
        el("button", { class: `hist-key${key ? "" : " on"}`,
                       title: "every parameter, one chart each",
                       onclick: () => set({ historyKey: "" }) },
          el("span", { class: "hk-n", text: "All at once" })),
        shown.slice(0, 40).map(e => el("button", {
          class: `hist-key${e.key === key ? " on" : ""}`,
          title: `${e.first} → ${e.last}`,
          onclick: () => set({ historyKey: e.key }),
        }, el("span", { class: "hk-n", text: shortKey(e.key) }),
           e.changes ? el("b", { text: String(e.changes) }) : null))),
      key ? historyChart(payload, key) : historyGrid(payload, tiles),
      key ? historyChanges(payload, key) : null,
      !key && shown.length > tiles.length
        ? el("div", { class: "hist-none",
            text: `Showing ${tiles.length} of ${shown.length}. Pick a key above `
                + "to see the rest one at a time." })
        : null,
      !key && !shown.length
        ? el("div", { class: "hist-none",
            text: "Nothing moved while a run was training. "
                + `\u201cAll ${index.length}\u201d draws every knob at the value it was launched with.` })
        : null));
}

// Enough charts to see the run at a glance, few enough that the page still
// paints at the refresh rate the rest of the app runs at.
const GRID_MAX = 36;

/** The x/y points for one key, laid end to end across the ancestry. */
function historySeries(payload, key) {
  const points = [];
  for (const run of payload.runs || []) {
    for (const p of (run.series || {})[key] || []) {
      points.push([run.offset + p.x, p.v]);
    }
  }
  return points;
}

/** Every key on screen at once, one small chart each.
 *
 * Small multiples rather than one chart with many lines: a learning rate and a
 * reward weight share no scale, so overlaying them says nothing. The same axis
 * on each tile is what makes them comparable -- shape against shape.
 */
function historyGrid(payload, entries) {
  if (!entries.length) return null;
  return el("div", { class: "hist-grid" }, entries.map(e => {
    const points = historySeries(payload, e.key);
    return el("button", {
      class: "hg-tile", title: `${e.key}\n${e.first} → ${e.last}`,
      onclick: () => set({ historyKey: e.key }),
    },
      el("div", { class: "hg-h" },
        el("span", { class: "hg-n", text: shortKey(e.key) }),
        e.changes ? el("b", { text: `${e.changes}\u00d7` }) : null,
        el("span", { class: "hg-v", text: fmt(e.last) })),
      points.length > 1
        ? sparkline(points, { width: 300, height: 104, axes: true,
                              color: e.changes ? "var(--accent)" : "var(--muted)" })
        : el("div", { class: "hg-flat", text: "one value, never moved" }));
  }));
}

/** One key, drawn across every run of the ancestry.
 *
 * The x axis is `offset + iteration`, which rl-mcp already laid out, so runs
 * appear end to end in the order they descend from one another rather than
 * overlapping at zero.
 */
function historyChart(payload, key) {
  const points = historySeries(payload, key);
  if (points.length < 2) {
    return el("div", { class: "empty", text: "one value, so there is no line to draw" });
  }
  return el("div", { class: "hist-chart" },
    sparkline(points, { width: 640, height: 150, axes: true,
                        color: "var(--accent)" }));
}

/** Every point that was a *change*, with the reason given for it.
 *
 * This is the "diff" that was not legible: the table says what a parameter is
 * now, and this says what it was, when it moved, and why -- which is three
 * facts the app already held and never put together.
 */
function historyChanges(payload, key) {
  const rows = [];
  for (const run of payload.runs || []) {
    const series = (run.series || {})[key] || [];
    let previous = null;
    for (const p of series) {
      if (p.source !== "launch" && p.source !== "end" && previous !== null
          && p.v !== previous) {
        rows.push({ run: run.id, at: p.x, from: previous, to: p.v,
                    why: p.why || "", source: p.source });
      }
      previous = p.v;
    }
  }
  if (!rows.length) {
    return el("div", { class: "hist-none",
      text: "This one was never changed while a run was training. Where it "
          + "differs between runs, it was set at launch." });
  }
  return el("div", { class: "hist-changes" }, rows.map(r => el("div", { class: "hist-row" },
    el("span", { class: "hr-run", text: r.run }),
    el("span", { class: "hr-at", text: `it ${Number(r.at).toLocaleString("en-US")}` }),
    el("span", { class: "hr-move" },
      el("b", { text: fmt(r.from) }), " \u2192 ", el("b", { text: fmt(r.to) })),
    r.why ? el("span", { class: "hr-why", text: r.why }) : null)));
}

/** `rewards.track_linear_velocity.params.std` is unreadable in a chip; the
 *  tail is what distinguishes one key from its siblings. */
function shortKey(key) {
  const parts = String(key).split(".");
  return parts.length <= 2 ? key : parts.slice(-2).join(".");
}

function paramTable() {
  // The per-row graphs need the run's interventions. Read only by the section
  // below the table, they would arrive late: on first paint every row draws
  // nothing and fills in when you scroll far enough to trigger it.
  const session = ctx.state.detail && ctx.state.detail.record
                  && ctx.state.detail.record.session;
  if (session) loadOnce("traces", session, () => api.traces(session));

  const runId = ctx.state.runId;
  // Keyed on the iteration as well as the run, so a *live* table follows the
  // trainer. Read once and never again, a parameter changed from anywhere else
  // -- the agent, the CLI, another window -- would leave this table showing the
  // old value under a chip reading "live". Rounded, so a poll a second does not
  // become a fetch a second.
  const at = ((ctx.state.live && ctx.state.live.status) || {}).iteration;
  const era = ctx.state.live && ctx.state.live.state === "running"
    ? Math.floor(Number(at || 0) / 50) : "still";
  loadOnce("params", `${runId}:${era}`, () => api.parameters(runId));
  const payload = ctx.state.params;

  const card = el("div", { class: "card" },
    el("div", { class: "card-h" },
      el("h3", { text: "Parameters" }),
      // Only when there is something to describe: drawn for the `{ok: false}`
      // payload too, "from the last snapshot" would sit directly above "there
      // are no parameters to read -- not even a snapshot".
      payload && payload.ok !== false
        ? chip(payload.live ? "live" : "from the last snapshot", !!payload.live)
        : null,
      el("span", { class: "sp" }),
      el("button", { class: "act", title: "ask the trainer again",
                     onclick: () => set({ paramsStamp: null }) }, "Re-read"),
      el("input", {
        class: "ftree-filter", style: { width: "180px" }, type: "text",
        placeholder: "filter…",
        value: ctx.state.paramFilter || "",
        oninput: (e) => typed("paramFilter", e.target.value),
      })));

  const body = el("div", { class: "card-b" });
  // A refused edit reports into `commandError`, which is otherwise rendered
  // only by the frames card. Without this, pressing Apply on a value the server
  // rejects does nothing at all on this view, in any layout.
  if (ctx.state.commandError) {
    body.append(el("div", { class: "m-error" },
      ctx.state.commandError,
      el("button", { class: "act", style: { marginLeft: "8px" },
                     onclick: () => set({ commandError: "" }) }, "dismiss")));
  }
  if (!payload) { body.append(el("div", { class: "empty", text: "reading…" })); card.append(body); return card; }
  if (payload.error || payload.reason) {
    // A run with no session is not a failure, and an HTTP status code is not
    // an explanation. Say which of the two this is, in words.
    body.append(payload.reason
      ? notYet("No parameters for this run", payload.reason, "")
      : el("div", { class: "notyet" },
           el("b", { text: "Could not read them" }), payload.error));
    card.append(body); return card;
  }

  const specs = normaliseParams(payload);
  const filter = (ctx.state.paramFilter || "").toLowerCase();
  const shown = specs.filter(p => !filter || p.key.toLowerCase().includes(filter));

  if (!shown.length) {
    body.append(el("div", { class: "empty",
      text: specs.length ? "nothing matches" : "this run published no parameter schema" }));
    card.append(body); return card;
  }

  body.append(el("div", { class: "discovered" },
    el("span", { class: "eyebrow", text: "discovered" }),
    el("span", { text: `${specs.length} walked from the live config — the studio has no list of them.` })));

  const table = el("div", { class: "ptable" });
  for (const spec of shown.slice(0, 400)) {
    table.append(paramRow(spec, !!payload.live));
  }
  body.append(table);
  if (shown.length > 400) {
    body.append(el("div", { class: "eyebrow", text: `showing 400 of ${shown.length} — filter to narrow` }));
  }
  card.append(body);
  return card;
}

// rlmcp's schema shape varies by version; take what is there rather than
// insisting on one field name.
function normaliseParams(payload) {
  const raw = payload.parameters || payload.schema || payload.params || {};
  const out = [];
  if (Array.isArray(raw)) {
    for (const item of raw) out.push({ key: item.key || item.name, ...item });
  } else if (raw && typeof raw === "object") {
    for (const [key, item] of Object.entries(raw)) {
      out.push(typeof item === "object" && item !== null
        ? { key, ...item }
        : { key, value: item });
    }
  }
  return out.sort((a, b) => a.key.localeCompare(b.key));
}

// What this parameter did over the run, on the row itself.
//
// A step function, because that is what a parameter is: flat until something
// changes it. A constant draws as a flat line, which is not a wasted graph --
// "never moved" is the answer for most parameters most of the time, and a row
// that says it at a glance is why the column is worth its width.
//
// Only the *shape* lives here. The numbers are on the row already, and axes on
// a 22px sparkline would be unreadable; the tooltip carries the detail.
function paramSpark(key, value) {
  const payload = ctx.state.traces;
  const last = (payload && payload.last_iteration) || 0;
  const trace = payload && (payload.traces || []).find(t => t.key === key);

  if (!last) return el("span", { class: "pspark" });

  const steps = (trace && trace.steps) || [];
  const numeric = typeof value === "number" ? value : null;
  const points = [];

  if (steps.length) {
    let current = typeof steps[0].old === "number" ? steps[0].old : steps[0].new;
    points.push([0, current]);
    for (const step of steps) {
      if (typeof step.new !== "number") continue;
      points.push([step.iteration, current]);
      points.push([step.iteration, step.new]);
      current = step.new;
    }
    points.push([last, current]);
  } else if (numeric !== null) {
    points.push([0, numeric], [last, numeric]);
  } else {
    // Not a number -- a bool, a string, a range. There is no line to draw and
    // pretending otherwise would put a flat zero under every one of them.
    return el("span", { class: "pspark", title: "not a number — nothing to plot" });
  }

  const W = 96, H = 20, pad = 2;
  const xs = points.map(p => p[0]);
  const ys = points.map(p => p[1]);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const flat = hi === lo;
  const span = (hi - lo) || Math.abs(hi) || 1;
  lo -= span * 0.25; hi += span * 0.25;
  const X = v => pad + (v / (Math.max(...xs) || 1)) * (W - pad * 2);
  const Y = v => H - pad - ((v - lo) / ((hi - lo) || 1)) * (H - pad * 2);
  const d = points.map((pt, i) => `${i ? "L" : "M"}${X(pt[0]).toFixed(1)} ${Y(pt[1]).toFixed(1)}`).join(" ");

  const colour = flat ? "var(--dim)" : "var(--accent)";
  const title = flat
    ? `constant at ${fmtVal(value)} for all ${last.toLocaleString("en-US")} iterations`
    : `${steps.length} change${steps.length === 1 ? "" : "s"} — ${steps
        .map(st => `${st.iteration}: ${fmt(st.old)} → ${fmt(st.new)}`).join(", ")}`;

  return el("span", { class: `pspark${flat ? " flat" : ""}`, title, html:
    `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">
       <path d="${d}" fill="none" stroke="${colour}" stroke-width="1.2"
             stroke-linejoin="round" vector-effect="non-scaling-stroke"/>
     </svg>` });
}

function paramRow(spec, live) {
  const pending = ctx.state.pendingEdit;
  const editing = pending && pending.key === spec.key;
  const liveness = spec.liveness || spec.applies || "";
  // `min: null, max: null` is rlmcp saying "unbounded". Drawn as bounds it reads
  // "— … —" on every row: three characters of nothing, once per parameter.
  const pair = spec.bounds || (spec.min !== undefined ? [spec.min, spec.max] : null);
  const bounds = pair && (pair[0] !== null && pair[0] !== undefined
                          || pair[1] !== null && pair[1] !== undefined) ? pair : null;
  const value = spec.value !== undefined ? spec.value : spec.current;

  const row = el("div", { class: "prow" },
    el("span", { class: "pkey", text: spec.key }),
    liveness ? el("span", { class: `liveness ${liveness}`, text: liveness }) : null,
    paramSpark(spec.key, value),
    el("span", { class: "pbounds",
      text: bounds ? `${fmt(bounds[0])} … ${fmt(bounds[1])}` : "" }),
    el("span", { class: "pval", text: fmtVal(value) }));

  if (!editing) {
    row.append(el("button", {
      class: "act", disabled: !live || liveness === "inert",
      title: !live ? "needs a live trainer"
           : liveness === "inert" ? "semantics, not magnitude — not exposed"
           : "change this",
      onclick: () => ctx.actions.proposeEdit({
        key: spec.key, why: "",
        // Arrays through `String()` become "-1,1", which reads like a typo.
        value: Array.isArray(value) ? value.join(", ") : value,
      }),
    }, "edit"));
    return row;
  }

  // `set()` on every keystroke, not a bare mutation: the Apply button and the
  // note below it are computed from this value, and without a repaint they
  // never change -- "abc" sits in a float field with Apply enabled and the
  // usual reassuring note underneath. A repaint costs ~2 ms and the caret
  // survives it, which is the whole point of the reconciler.
  const input = el("input", { class: "pedit", type: "text", value: String(pending.value ?? ""),
    oninput: (e) => { pending.value = e.currentTarget.value; set({ pendingEdit: pending }); } });
  // Where it actually goes: rlmcp writes the change and its rationale into the
  // run's event log, and the record folds that in as an intervention once the
  // installed rl-mcp can read them. "Kept on the record" would promise
  // something the app itself reports as missing, on the same screen.
  const why = el("input", { class: "pedit why", type: "text",
    placeholder: "why? (sent with the change, kept in the run's log)",
    value: pending.why || "",
    oninput: (e) => { pending.why = e.target.value; } });

  // What the trainer would be sent, and what is wrong with it. Checked here
  // rather than only server-side because the answer is instant and the person
  // is looking at the field. Unchecked, `abc` is accepted for a float and
  // sent, and the table then reads `rl.entropy_coef │ -10.00 … 10.00 │ abc`.
  // A string parameter takes the characters as typed: coercing first would
  // turn "123" into the number 123 and then refuse it for not being text.
  const edited = typeof value === "string" ? String(pending.value ?? "")
                                           : coerce(pending.value, value);
  const typed = String(pending.value ?? "");
  const complaint = checkValue(spec, edited, bounds, typed);

  return el("div", { class: "prow editing" },
    el("span", { class: "pkey", text: spec.key }),
    input, why,
    el("button", { class: "primary", disabled: !!complaint,
      title: complaint || "send this to the trainer",
      // `pending` is where both fields write on every keystroke; `input` and
      // `why` here are the copies `patch()` discarded. See `ui.js`.
      onclick: () => ctx.actions.setParameter(spec.key, edited, pending.why || ""),
    }, "Apply"),
    el("button", { class: "ghost", onclick: () => set({ pendingEdit: null }) }, "Cancel"),
    el("span", { class: complaint ? "pnote bad" : "pnote",
      text: complaint
        || "applies at the next batch boundary — it cannot race the simulator" }));
}

/** Why this value cannot be sent, or "" if it can.
 *
 * Without this the declared type and range are decoration: `abc` goes to the
 * trainer as a float parameter, and `999999` goes with `-10 … 10` printed
 * beside it. Bounds are advisory in rlmcp -- some are genuinely soft -- so a
 * value outside them is refused here rather than silently clamped, and the
 * message says the range so the person can decide.
 */
function checkValue(spec, value, bounds, typed = "") {
  const kind = String(spec.type || "").toLowerCase();
  const current = spec.current !== undefined ? spec.current : spec.value;

  // The declared type is a *hint*, and it does not cover everything rlmcp
  // publishes -- `command.twist.ranges.lin_vel_x` is declared `range` with a
  // value of `[-1, 1]`. Checking only float, int and bool lets every other
  // type through: "abc" goes to the trainer, comes back as
  // `{"type": "float", "value": "abc", "min": -10, "max": 10}`, and the
  // parameter cannot be repaired from the GUI afterwards. So the shape of
  // what is there now is the authority whenever the type name is not one this
  // app understands.
  if (Array.isArray(current) || kind.includes("range") || kind.includes("list")) {
    if (!Array.isArray(value)) {
      return typed.trim()
        ? `this one is a list like ${JSON.stringify(current ?? [0, 1])}`
        : "type a list";
    }
    if (Array.isArray(current) && value.length !== current.length) {
      return `this one takes ${current.length} value(s), not ${value.length}`;
    }
    if (value.some(v => typeof v !== "number" || !Number.isFinite(v))) {
      return "every entry has to be a number";
    }
    if (value.length === 2 && value[0] > value[1]) {
      return "the low end is above the high end";
    }
    return "";
  }

  const numeric = kind.includes("float") || kind.includes("int")
                  || (!kind && typeof current === "number");
  if (numeric) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      // Quote what is in the box. `1e400` coerces to Infinity and `[1,2]` to
      // "1,2", and a complaint about a value nobody typed reads as a bug.
      return typed.trim()
        ? `this one is a number; "${typed}" is not`
        : "type a number";
    }
    if (kind.includes("int") && !Number.isInteger(value)) {
      return "this one takes a whole number";
    }
    if (bounds) {
      const [low, high] = bounds;
      if (low !== null && low !== undefined && value < low) return `below the declared minimum ${fmt(low)}`;
      if (high !== null && high !== undefined && value > high) return `above the declared maximum ${fmt(high)}`;
    }
    return "";
  }
  if (kind.includes("bool") || typeof current === "boolean") {
    return typeof value === "boolean" ? "" : "this one takes true or false";
  }
  if (kind.includes("str") || typeof current === "string") {
    return typeof value === "string" ? "" : "this one takes text";
  }
  // A type this app has never heard of, with nothing to compare against.
  // Refusing outright would block a legitimate edit; sending anything at all
  // is the corruption above. Same JS type as what is there.
  if (current !== undefined && current !== null && typeof value !== typeof current) {
    return `this one is a ${typeof current}; what you typed is a ${typeof value}`;
  }
  return "";
}

/** One side of a parameter change, whatever type it turned out to be. */
function traceValue(v) {
  if (typeof v === "number") return fmt(v);
  if (Array.isArray(v)) return `[${v.map(x => typeof x === "number" ? fmt(x) : String(x)).join(", ")}]`;
  if (v === null || v === undefined) return "—";
  return typeof v === "string" ? `"${v}"` : String(v);
}

function coerce(text, current) {
  const trimmed = String(text).trim();
  // A list is what a range parameter is, and `-1, 1` is how a person types
  // one. Without this the only accepted spelling is strict JSON.
  if (Array.isArray(current) && trimmed && !trimmed.startsWith("[")) {
    const parts = trimmed.split(",").map(p => p.trim()).filter(Boolean);
    if (parts.length > 1 && parts.every(p => /^-?\d*\.?\d+(e-?\d+)?$/i.test(p))) {
      return parts.map(Number);
    }
  }
  if (/^-?\d+$/.test(trimmed)) return parseInt(trimmed, 10);
  // `5.` and `-.5` are both numbers a person types into a box.
  if (/^-?(\d+\.?\d*|\.\d+)(e-?\d+)?$/i.test(trimmed)) return parseFloat(trimmed);
  if (trimmed === "true") return true;
  if (trimmed === "false") return false;
  try { return JSON.parse(trimmed); } catch { return trimmed; }
}

function traceSection() {
  const wrap = el("div", { class: "paramview",
                           style: { "--side": sideVar("paramList", ctx.state.widths) } });
  const session = ctx.state.detail && ctx.state.detail.record && ctx.state.detail.record.session;

  if (!session) {
    wrap.append(el("div", { class: "notyet" },
      el("b", { text: "No session for this run" }),
      "Parameter history comes from the run's ", el("code", { text: "events.jsonl" }),
      ". This record does not point at one."));
    return wrap;
  }

  // Into the store, never into a node this filled itself: the repaint discards
  // that node and starts the request again, so on a live run the plot flickers
  // or never appears while /api/traces goes out again and again.
  loadOnce("traces", session, () => api.traces(session));
  const payload = ctx.state.traces;

  if (!payload) {
    wrap.append(el("div", { class: "empty", text: "reading events…" }));
    return wrap;
  }
  if (payload.error) {
    wrap.append(el("div", { class: "notyet" },
      el("b", { text: "Could not read the events log" }), String(payload.error)));
    return wrap;
  }
  if (!payload.traces || !payload.traces.length) {
    wrap.append(el("div", { class: "notyet" },
      el("b", { text: "Nothing was changed during this run" }),
      `It ran from launch to iteration ${Number(payload.last_iteration || 0).toLocaleString("en-US")} `
      + "with no parameter edits, so there is no step to plot. Edits made from the CLI, "
      + "the MCP server or this GUI appear here as they happen."));
    return wrap;
  }

  // Which trace is plotted is a choice, so it lives in the store: kept as an
  // `aria-current` attribute on a button, every repaint would put it back on
  // the first one.
  const picked = payload.traces.find(t => t.key === ctx.state.tracePicked)
                 || payload.traces[0];
  wrap.append(paramList(payload, picked), el("div", { class: "plot-col" }),
              placed("paramList", { side: "left", widths: ctx.state.widths,
                                    actions: ctx.actions, offset: 8 }));
  drawTrace(wrap, payload, picked);
  return wrap;
}

function paramList(payload, picked) {
  const list = el("div", { class: "plist" });
  for (const trace of payload.traces) {
    list.append(el("button", {
      class: "pitem", "data-key": trace.key,
      // The choice goes to the store rather than onto the node: marking the
      // node, the next repaint rebuilds the list and puts the mark back on the
      // first one, so picking the third parameter does not survive a tick.
      "aria-current": trace.key === picked.key ? "true" : null,
      onclick: () => set({ tracePicked: trace.key }),
    },
      el("span", { class: "pk", text: trace.key }),
      el("span", { class: "pm", text: `${trace.steps.length} change${trace.steps.length === 1 ? "" : "s"}` })));
  }
  return list;
}
function drawTrace(wrap, payload, trace) {
  const col = wrap.querySelector(".plot-col");
  if (!col) return;
  clear(col);

  const W = 700, H = 250, L = 58, R = 24, T = 16, B = 34;
  const last = payload.last_iteration || Math.max(...trace.steps.map(s => s.iteration), 1);
  const values = trace.steps.flatMap(s => [s.old, s.new]).filter(v => typeof v === "number");
  let lo = Math.min(...values), hi = Math.max(...values);
  if (!values.length) { lo = 0; hi = 1; }
  const span = (hi - lo) || Math.abs(hi) || 1;
  lo -= span * .2; hi += span * .2;

  const X = it => L + (it / (last || 1)) * (W - L - R);
  const Y = v => H - B - ((v - lo) / ((hi - lo) || 1)) * (H - T - B);

  // Stage bands: entering a curriculum stage changes parameters too, so it
  // belongs on the same axis as a hand edit.
  const bands = payload.stages.map((st, i) => {
    const x = X(st.iteration);
    const next = payload.stages[i + 1] ? X(payload.stages[i + 1].iteration) : X(last);
    return `<rect x="${x.toFixed(1)}" y="${T}" width="${(next - x).toFixed(1)}" height="${H - T - B}"
             fill="var(--raised)" opacity="${i % 2 ? .5 : .26}"/>
            <text x="${(x + 5).toFixed(1)}" y="${T + 11}" fill="var(--dim)"
             font-family="ui-monospace,monospace" font-size="8.5">${escape(st.name)}</text>`;
  }).join("");

  let grid = "", ticks = "";
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi - lo) * (g / 4), y = Y(v);
    grid += `<path d="M${L} ${y.toFixed(1)} H${W - R}" stroke="var(--line-soft)" stroke-width="1"/>`;
    ticks += `<text x="${L - 7}" y="${(y + 3).toFixed(1)}" fill="var(--dim)" text-anchor="end"
               font-family="ui-monospace,monospace" font-size="9">${fmt(v)}</text>`;
  }

  let xt = "";
  const step = niceStep(last);
  for (let it = 0; it <= last; it += step) {
    xt += `<text x="${X(it).toFixed(1)}" y="${H - B + 15}" fill="var(--dim)" text-anchor="middle"
            font-family="ui-monospace,monospace" font-size="9">${it.toLocaleString("en-US")}</text>`;
  }

  // The step function itself: flat, then a vertical jump at the iteration the
  // edit landed on.
  let d = "", marks = "";
  const start = typeof trace.steps[0].old === "number" ? trace.steps[0].old : trace.steps[0].new;
  let current = start;
  d = `M${X(0).toFixed(1)} ${Y(current).toFixed(1)}`;
  for (const s of trace.steps) {
    if (typeof s.new !== "number") continue;
    const x = X(s.iteration);
    d += ` L${x.toFixed(1)} ${Y(current).toFixed(1)} L${x.toFixed(1)} ${Y(s.new).toFixed(1)}`;
    current = s.new;
    marks += `<path d="M${x.toFixed(1)} ${T} V${H - B}" stroke="var(--accent)" stroke-width="1"
               stroke-dasharray="2 3" opacity=".45"/>
              <circle cx="${x.toFixed(1)}" cy="${Y(s.new).toFixed(1)}" r="3.4" fill="var(--accent)"/>`;
  }
  d += ` L${X(last).toFixed(1)} ${Y(current).toFixed(1)}`;

  col.append(el("div", { class: "plot" },
    el("div", { class: "plot-h" },
      el("span", { class: "k", style: { fontSize: "12px" }, text: trace.key }),
      chip(`${trace.steps.length} change${trace.steps.length === 1 ? "" : "s"}`),
      el("span", { class: "eyebrow", style: { marginLeft: "auto" }, text: "x = iteration" })),
    el("div", { html:
      `<svg viewBox="0 0 ${W} ${H}">${bands}${grid}
        <path d="${d}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round"/>
        ${marks}${ticks}${xt}
        <path d="M${L} ${H - B} H${W - R}" stroke="var(--line)" stroke-width="1"/></svg>` }),
    el("div", { class: "changes" },
      trace.steps.map(s => el("div", { class: "chg-row" },
        el("span", { class: "it", text: Number(s.iteration).toLocaleString("en-US") }),
        el("span", { class: "wh" },
          // `fmt` renders anything that is not a number as an em dash, so a
          // string value would make the row read "0.005 → —": a change to
          // nothing, with no way to tell what was actually set.
          el("em", { text: `${traceValue(s.old)} → ${traceValue(s.new)}` }),
          s.why ? ` — ${s.why}` : "",
          el("span", { class: "by", text: ` · ${s.by}${s.applied ? "" : " · refused"}` })))))));
}

function niceStep(last) {
  const raw = last / 6;
  const mag = 10 ** Math.floor(Math.log10(Math.max(raw, 1)));
  return Math.max(Math.round(raw / mag) * mag, 1);
}
