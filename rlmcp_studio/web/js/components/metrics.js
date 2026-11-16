// Which metrics to chart, the cards that chart them, and one chart opened large.

import { el, fmt, sparkline, on } from "../ui.js";
import { state, set, headlinePrefs, setHeadline, WINDOWS } from "../state.js";

// ── which charts, and in what order ───────────────────────────────────────
//
// Two separate questions, and only one of them is the studio's to answer.
//
// *Which* metrics exist is discovered — whatever the run published. Nothing
// here contains a metric name, so an extension's own metrics appear without a
// change to this file. rlmcp's controller also nominates a subset in
// `status.headline_metrics`: every `rlmcp/` metric plus the core summaries.
//
// *What order to show them in* is a presentation decision, so it lives here.
// It is expressed as roles — concepts a training run has — not as names. A
// backend that calls its reward something else still matches, because the test
// is on the shape of the name, not on a list of the names we happen to know.

const ROLES = [
  { role: "reward", test: (n) => /(^|[\/_])(reward|return)s?([\/_]|$)/i.test(n) },
  { role: "length", test: (n) => /ep(isode)?[_\/]?len(gth)?/i.test(n) },
];

const SUMMARY = /^(mean|avg|average|total)[_\/]|[_\/](mean|avg|average|total)$/i;

export function rankMetrics(series, live, limit = 6) {
  const names = Object.keys(series || {});
  if (!names.length) return [];

  // A choice made for this task wins over every heuristic below, in the
  // order it was made, and `limit` does not cut it: somebody who picked five
  // series wants five cards. Names the run has not published yet are simply
  // not drawn until they exist.
  const chosen = headlinePrefs(state.task).keys;
  if (chosen) {
    const present = chosen.filter(n => names.includes(n));
    if (present.length) return present;
  }

  const status = (live && live.status) || {};
  const nominated = new Set(Object.keys(status.headline_metrics || {}));

  // A namespace with many siblings is a per-term breakdown rather than a
  // summary: Episode_Reward/<term> should never outrank the run's mean reward.
  const siblings = {};
  for (const name of names) {
    const ns = namespaceOf(name);
    siblings[ns] = (siblings[ns] || 0) + 1;
  }

  const score = (name) => {
    let s = 0;
    if (nominated.has(name)) s += 100;              // the run put it in its own headline
    if (SUMMARY.test(leafOf(name))) s += 30;        // a summary, not one term of many
    s -= Math.min(siblings[namespaceOf(name)] - 1, 20);
    return s;
  };

  // Roles give the *order* -- reward, then episode length, then the rest --
  // and the run's own nomination decides *which* series fills each one. Taking
  // the best-scoring name per role would let an undeclared
  // `Train/mean_episode_length` displace a declared `rlmcp/terrain_level_mean`
  // on a card titled "Headline metrics", so the one panel claiming to be the
  // headline would be the one that is not. A role with nothing nominated
  // still falls back to the best available, because an empty slot helps
  // nobody.
  const lead = [];
  const prefer = (pool, test) => pool.filter(test).sort((a, b) => score(b) - score(a))[0];
  const declared = names.filter(n => nominated.has(n));
  for (const { test } of ROLES) {
    const best = (declared.length ? prefer(declared, test) : null) || prefer(names, test);
    if (best && !lead.includes(best)) lead.push(best);
  }

  const rest = names
    .filter((n) => !lead.includes(n))
    .sort((a, b) => score(b) - score(a) || a.localeCompare(b));

  return [...lead, ...rest].slice(0, limit);
}

function namespaceOf(name) {
  const i = name.lastIndexOf("/");
  return i === -1 ? "" : name.slice(0, i);
}

function leafOf(name) {
  return name.slice(name.lastIndexOf("/") + 1);
}

/** The headline column: the chosen series, each as its last value and a sparkline.
 *  The Focus layout's side column, and the report's. */
export function headlineStats(series, live) {
  const chosen = headlineNames(live, 3, series);
  if (!chosen.length) return el("div", { class: "empty", text: "no metrics yet" });
  return el("div", { style: { display: "flex", flexDirection: "column", gap: "11px" } },
    chosen.filter(name => (series[name] || []).length).map(name => {
      const points = series[name];
      const last = points[points.length - 1][1];
      return el("div", { class: "f-stat" },
        el("div", { class: "l", text: name.replace(/^rlmcp\//, "") }),
        el("div", { class: "v", text: fmt(last) }),
        sparkline(points, { height: 34, color: "var(--v-best)" }));
    }));
}

export function headlineNames(live, limit = 3, series = null) {
  return rankMetrics(series || (live && live.series) || {}, live, limit);
}

export function pickHeadline(series, live) {
  return rankMetrics(series, live, 3).map((n) => [n, series[n]]).filter(([, p]) => p && p.length);
}

// The strip is narrow, so drop a namespace only when the leaf still identifies
// the metric on its own. Never truncate to something ambiguous.
export function short(name) {
  const leaf = name.slice(name.lastIndexOf("/") + 1);
  return leaf.length >= 6 ? leaf : name;
}

export function metricCards(series, limit = 6, live = null) {
  const names = Object.keys(series).sort();
  if (!names.length) return el("div", { class: "empty", text: "no metrics published yet" });
  const wrap = el("div", { style: { display: "grid", gap: "12px" } });
  // The controls, when this is a run's own series (the layouts and the Plots
  // view pass `live`): how many points, and which series -- per task.
  if (live && state.task) wrap.append(headlineConfig(series, live));
  const chosen = rankMetrics(series, live, limit).filter(n => (series[n] || []).length);
  for (const name of chosen) {
    const points = series[name];
    // A name with no rows is a metric that was announced and never written.
    // Reading its last point would throw, and a throw in a render function
    // blanks the page -- paint() has already cleared the tree by then.
    const last = points[points.length - 1][1];
    wrap.append(el("div", { class: "card" },
      el("div", { class: "card-b" },
        el("div", { class: "chart-h" },
          el("span", { class: "k", text: name }),
          el("span", { class: "now", text: fmt(last) })),
        openable(name, points,
                 sparkline(points, { color: "var(--v-best)", height: 84,
                                     smoothing: state.smoothing || 0 }),
                 { source: "published by the run" }),
        // What the line covers. Without it "4.74" is a number over a shape,
        // and there is no way to tell 200 iterations from 5,000 -- the
        // TensorBoard cards say the same.
        el("div", { class: "tb-foot" },
          el("span", { text: `${points.length} pts` }),
          el("span", { class: "sp" }),
          el("span", { text: `iteration ${Number(points[0][0]).toLocaleString("en-US")}`
                           + ` \u2013 ${Number(points[points.length - 1][0]).toLocaleString("en-US")}` })))));
  }
  return wrap;
}

// ── which metrics, how many points: per task ──────────────────────────────
//
// Two decisions the studio makes for you by default: the ranking picks the
// series, and the session route's default picks the window. This is where a
// person overrides them, and the override is remembered for the task
// (state.js `setHeadline`).
export function headlineConfig(series, live) {
  const task = state.task;
  const prefs = headlinePrefs(task);
  const chosen = prefs.keys || [];
  const auto = rankMetrics(series, live, 3);
  const open = state.headlinePick === task;
  const names = Object.keys(series).sort();

  const windowSelect = el("select", {
    class: "hl-window", title: "how many recent points each chart covers",
    onchange: (e) => setHeadline(task, { window: Number(e.currentTarget.value) }),
  }, WINDOWS.map(w => el("option", { value: String(w), text: `last ${w} rows` })));
  windowSelect.value = String(prefs.window);

  const toggle = el("button", {
    class: `act hl-pickbtn${open ? " on" : ""}`, "aria-expanded": open ? "true" : "false",
    title: "choose which series the cards show, for this task",
    onclick: () => set({ headlinePick: open ? null : task }),
  }, prefs.keys ? `${chosen.length} chosen ▾` : "auto ▾");

  const picker = open ? el("div", { class: "hl-pick" },
    el("div", { class: "hl-pick-h" },
      el("span", { class: "eyebrow", text: `series · ${names.length}` }),
      el("span", { class: "sp" }),
      el("button", { class: "act", title: "back to the ranking the run nominates",
                     onclick: () => setHeadline(task, { keys: null }) }, "auto")),
    names.map(n => {
      const box = el("input", { type: "checkbox",
        onchange: (e) => {
          const now = prefs.keys || auto;
          const next = e.currentTarget.checked ? [...now.filter(x => x !== n), n] : now.filter(x => x !== n);
          setHeadline(task, { keys: next });
        } });
      box.checked = (prefs.keys || auto).includes(n);
      return el("label", { class: "hl-opt" }, box,
        el("span", { class: "mn", text: n }),
        !prefs.keys && auto.includes(n) ? el("span", { class: "hl-auto", text: "auto" }) : null);
    })) : null;

  return el("div", { class: "hl-config" },
    el("span", { class: "hl-label", text: "x axis" }), windowSelect,
    el("span", { class: "hl-label", text: "series" }), toggle,
    el("span", { class: "sp" }),
    el("span", { class: "hl-note", text: task ? `saved for ${task}` : "" }),
    picker);
}

// ── one chart, large ──────────────────────────────────────────────────────
//
// A 74-pixel card answers "is it going up". Everything else -- where the step
// change was, whether the noise band widened, what the value actually is at
// iteration 12,000 -- needs room. So any chart can be opened, and the opened
// one carries the controls: smoothing, and the numbers a card has no space for.
//
// Smoothing is stored globally rather than per chart. You are asking "how noisy
// is this run", not "how noisy is this one series", and forty cards each with
// their own setting is forty things to keep in your head.

/** Wrap a chart so clicking it opens the large version. */
export function openable(name, points, node, extra = {}) {
  const wrap = el("button", {
    class: "chart-open", title: `${name} — click to enlarge`,
    onclick: () => set({ zoom: { name, points, ...extra } }),
  }, node);
  return wrap;
}

export function chartOverlay(state, actions) {
  const zoom = state.zoom;
  if (!zoom) return null;
  // Read the series again rather than showing the copy taken when it was
  // clicked. A copy would freeze at that instant while the status bar under
  // it keeps moving -- and the numbers are labelled "last", so it would read
  // as current. Falls back to the snapshot for a series that is no longer
  // there.
  const points = livePoints(state, zoom) || zoom.points || [];
  const smoothing = state.smoothing || 0;
  const values = points.map(p => p[1]).filter(Number.isFinite);
  const last = points[points.length - 1] || [0, 0];
  const stat = (label, value) => el("div", { class: "z-stat" },
    el("div", { class: "l", text: label }), el("div", { class: "v", text: value }));

  const slider = el("input", {
    type: "range", min: "0", max: "0.97", step: "0.01", class: "z-slider",
    value: String(smoothing),
  });
  on(slider, "input", (e) => actions.setSmoothing(+e.target.value));

  return el("div", {
    class: "zoom", onclick: (e) => { if (e.target.classList.contains("zoom")) actions.closeZoom(); },
  },
    el("div", { class: "z-box" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: zoom.name }),
        zoom.source ? el("span", { class: "eyebrow", text: zoom.source }) : null,
        el("span", { class: "sp" }),
        // A curve is the other thing worth pointing at, and describing one in
        // words -- "it plateaus around 3k and then dips" -- is exactly the
        // kind of sentence a picture replaces.
        el("button", {
          class: "act cam", title: "take a picture of this chart and add it to a message",
          onclick: (e) => {
            const box = e.currentTarget.closest(".z-box");
            actions.screenshot(box && box.querySelector("svg"), zoom.name);
          },
        }, "\u25A3"),
        el("button", { class: "act", onclick: () => actions.closeZoom() }, "Close")),
      el("div", { class: "z-plot" },
        sparkline(points, { width: 900, height: 380, color: "var(--v-best)",
                            pad: 34, smoothing, axes: true })),
      el("div", { class: "z-controls" },
        el("span", { class: "eyebrow", text: "smoothing" }),
        slider,
        el("span", { class: "z-weight", text: smoothing ? smoothing.toFixed(2) : "off" }),
        el("span", { class: "sp" }),
        el("span", { class: "z-hint",
                     text: smoothing ? "the raw series is behind it" : "" })),
      el("div", { class: "z-stats" },
        // Of the raw series, always: the curve may be smoothed, and a mean
        // that describes a different line than the one on screen is worse than
        // no mean. Said in the label when it matters.
        stat(smoothing ? "last (raw)" : "last", fmt(last[1])),
        stat("min", fmt(Math.min(...values))),
        stat("max", fmt(Math.max(...values))),
        stat("mean", fmt(values.reduce((a, b) => a + b, 0) / (values.length || 1))),
        stat("points", String(points.length)),
        stat("to iteration", Number(last[0]).toLocaleString("en-US")))));
}

/** The current points for whatever the modal is showing, or null.
 *
 * Reads *the source the chart came from*. Preferring the live session
 * unconditionally would quietly replace a TensorBoard curve with the live one
 * of the same name -- and the live snapshot is only the most recent
 * iterations, so a whole-run series would open as its last stretch with no
 * sign anything had been substituted. The x axis would simply look short.
 */
function livePoints(state, zoom) {
  if (!zoom.name) return null;
  const fromTb = ((state.tb || {}).series || {})[zoom.name];
  const fromLive = ((state.live || {}).series || {})[zoom.name];
  const preferTb = String(zoom.source || "").includes("event files");
  const first = preferTb ? fromTb : fromLive;
  const second = preferTb ? fromLive : fromTb;
  if (first && first.length) return first;
  if (second && second.length) return second;
  return null;
}
