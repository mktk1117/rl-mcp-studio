// Plots: the curves the trainer published, and the TensorBoard event files.

import { el, fmt, sparkline, on } from "../ui.js";
import { api } from "../api.js";
import { state, set, loadOnce } from "../state.js";
import { notYet, headlineConfig, openable } from "../components.js";
import { typed, ctx, segmented } from "./shared.js";

// ── plots ───────────────────────────────────────────────────────────────
//
// Two sources, one view. `published` is what rlmcp's controller nominates --
// the handful a run states are its headline -- and `logged` is everything the
// trainer's own logger wrote, which on a real run is dozens of series against
// that handful. Separate tabs would make the reader choose a *source* before
// they could ask a question about a curve.
export function plotsView() {
  const s = ctx.state;
  const series = (s.live && s.live.series) || {};
  const published = Object.keys(series).length;
  const source = s.plotSource === "published" ? "published" : "logged";

  const head = el("div", { class: "plots-h" },
    segmented([
      { id: "published", label: "published", count: published || undefined,
        blurb: "the series the run itself publishes" },
      { id: "logged", label: "tensorboard",
        blurb: "every scalar the trainer's own logger wrote" },
    ], source, (id) => set({ plotSource: id })),
    source === "published"
      ? el("span", { class: "plots-note",
                     text: "what the run nominates as its own" })
      : null);

  if (source === "logged") {
    return el("div", { class: "plotsview" }, head, tensorboardView());
  }
  return el("div", { class: "plotsview" }, head,
    published
      ? el("div", { class: "plots-b" }, publishedView(series))
      : notYet("No series yet",
               " Nothing has been published by this run. The trainer's own "
               + "scalars may still be there — try tensorboard above.", ""));
}

/** Every series the run publishes, the way the tensorboard half draws its own.
 *
 * The tab promises every curve, so this is not a few headline cards: same
 * grid, same groups, same controls as the other source -- one view, two
 * sources, as the header above it says -- plus the headline picker, which is
 * where the cards the Run view shows are chosen and windowed.
 */
function publishedView(series) {
  const s = ctx.state;
  const wrap = el("div", { class: "tbview" });
  const filter = (s.tbFilter || "").toLowerCase();
  const tags = Object.keys(series).sort();
  const matching = tags.filter(t => !filter || t.toLowerCase().includes(filter));
  const points = tags.reduce((n, t) => n + (series[t] || []).length, 0);
  const shownPoints = matching.reduce((n, t) => n + (series[t] || []).length, 0);
  wrap.append(seriesHead(s, matching.length, tags.length, filter ? shownPoints : points, filter));
  wrap.append(headlineConfig(series, s.live));
  if (!matching.length) {
    wrap.append(el("div", { class: "empty", text: `no series matches “${s.tbFilter}”` }));
    return wrap;
  }
  seriesGroups(wrap, matching, series, s, "published by the run");
  return wrap;
}

/** The row over a wall of charts: what is on screen, smoothing, a filter. */
function seriesHead(s, shown, total, points, filter) {
  const search = el("input", {
    class: "ftree-filter", type: "text", placeholder: "filter tags…",
    value: s.tbFilter || "",
    oninput: (e) => typed("tbFilter", e.target.value),
  });
  return el("div", { class: "tb-head" },
    el("span", { class: "eyebrow",
                 // Both halves count what is on screen. A filtered series
                 // count beside an unfiltered point count reads "2 of 7 series
                 // · 2,100 points" over two charts holding 600 between them.
                 text: (filter ? `${shown} of ${total} series · ` : `${total} series · `)
                     + `${points.toLocaleString("en-US")} points` }),
    el("span", { class: "sp" }),
    smoothingControl(s, ctx.actions),
    search);
}

/** Cards for `tags`, grouped by namespace, each group collapsible. Shared by
 *  both sources, so a curve reads the same whoever wrote it. */
function seriesGroups(wrap, tags, series, s, source) {
  const groups = new Map();
  for (const tag of tags) {
    const group = namespaceOf(tag);
    if (!groups.has(group)) groups.set(group, []);
    groups.get(group).push(tag);
  }
  const collapsed = s.tbCollapsed || {};
  for (const [group, names] of [...groups].sort()) {
    const shut = !!collapsed[group];
    wrap.append(el("button", {
      class: "tb-group", "aria-expanded": shut ? "false" : "true",
      onclick: () => set({ tbCollapsed: { ...collapsed, [group]: !shut } }),
    }, el("span", { class: "tb-caret", text: shut ? "▸" : "▾" }),
       el("span", { class: "tb-gname", text: group }),
       el("span", { class: "tb-gcount", text: String(names.length) })));
    if (shut) continue;

    const grid = el("div", { class: "tb-grid" });
    for (const tag of names) {
      const points = series[tag] || [];
      if (!points.length) continue;
      const last = points[points.length - 1];
      grid.append(el("div", { class: "card tb-card" },
        el("div", { class: "card-b" },
          el("div", { class: "chart-h" },
            el("span", { class: "k", text: tag.slice(group.length + 1) || tag }),
            el("span", { class: "now", text: fmt(last[1]) })),
          openable(tag, points,
                   sparkline(points, { color: "var(--v-best)", height: 74,
                                       smoothing: s.smoothing || 0 }),
                   { source }),
          el("div", { class: "tb-foot" },
            el("span", { text: `${points.length} pts` }),
            el("span", { class: "sp" }),
            el("span", { text: `to iteration ${Number(last[0]).toLocaleString("en-US")}` })))));
    }
    wrap.append(grid);
  }
}

// The event files are read when the view opens and cached until Reload. On a
// run that is training, that alone leaves the curves standing still while the
// status bar under them counts iterations. The server reads the files
// incrementally and caches per file, so asking again costs it nothing; this
// asks again every `TB_REFRESH_S` while the run is live and the wall is on
// screen.
const TB_REFRESH_S = 30;
setInterval(() => {
  const s = state;
  const payload = s.tb;
  const live = s.live && s.live.ok && s.live.state === "running";
  if (s.view !== "plots" || s.plotSource === "published" || !live || !payload || !payload.read_at) return;
  if (Date.now() / 1000 - payload.read_at >= TB_REFRESH_S) set({ tbStamp: null });
}, 5000);

// ── tensorboard ─────────────────────────────────────────────────────────
//
// The event files the trainer writes beside the session, drawn with the same
// renderer as every other chart here rather than in an embedded TensorBoard.
// Two reasons that is the right call and not just the cheaper one: the curves
// match the rest of the app, and an iframe cannot be filtered, grouped, or
// compared against what the record claims.
//
// Grouping is by namespace, which is the structure the tags already carry:
// `Curriculum/terrain_levels/level/flat` belongs with its siblings, and
// showing dozens of ungrouped charts is a wall nobody reads.
/** Why these curves might not be current, in words, or "".
 *
 * Deliberately about *when this was read*, not about iteration numbers: the
 * trainer's global step and rlmcp's iteration are not always the same
 * quantity, and a banner comparing them would be confidently wrong on any run
 * where they diverge. When it was read is a fact either way.
 */
function tbStaleness(s, payload) {
  const live = s.live && s.live.ok && s.live.state === "running";
  if (!live || !payload.read_at) return "";
  const age = Math.round(Date.now() / 1000 - payload.read_at);
  if (age < 60) return "";
  const minutes = Math.round(age / 60);
  return `This run is still training. These files were read ${minutes} minute`
       + `${minutes === 1 ? "" : "s"} ago.`;
}

function namespaceOf(tag) {
  const cut = tag.lastIndexOf("/");
  return cut === -1 ? "(ungrouped)" : tag.slice(0, cut);
}

// The same control as the enlarged view carries, because the question comes up
// while looking at the wall of small charts, not only after opening one.
function smoothingControl(s, actions) {
  const weight = s.smoothing || 0;
  const slider = el("input", {
    type: "range", min: "0", max: "0.97", step: "0.01", class: "z-slider small",
    value: String(weight),
    title: "exponential smoothing, as TensorBoard does it",
  });
  on(slider, "input", (e) => actions.setSmoothing(+e.target.value));
  return el("span", { class: "tb-smooth" },
    el("span", { class: "eyebrow", text: "smooth" }),
    slider,
    el("span", { class: "z-weight", text: weight ? weight.toFixed(2) : "off" }));
}

function tensorboardView() {
  const { state: s, actions } = ctx;
  if (!s.runId) return el("div", { class: "empty", text: "select a run" });
  loadOnce("tb", s.runId, () => api.tensorboard(s.runId));

  const payload = s.tb;
  const wrap = el("div", { class: "tbview" });

  if (!payload) return el("div", { class: "empty", text: "reading event files…" });
  if (payload.error) return el("div", { class: "m-error", text: payload.error });
  if (!payload.ok) {
    return notYet("No TensorBoard files for this run", payload.reason || "",
                  "events.out.tfevents.*");
  }

  const filter = (s.tbFilter || "").toLowerCase();
  const matching = payload.tags.filter(t => !filter || t.toLowerCase().includes(filter));
  const shownPoints = matching.reduce((n, tag) => n + ((payload.series[tag] || []).length), 0);
  const head = seriesHead(s, matching.length, payload.tags.length,
                          filter ? shownPoints : payload.points, filter);
  head.append(el("button", { class: "act", title: "read the files again",
                             onclick: () => set({ tbStamp: null }) }, "Reload"));
  wrap.append(head);

  // Older than the refresh above would allow only when the run is live and
  // the re-read has not landed; said so rather than left to the 9px foot.
  const behind = tbStaleness(s, payload);
  if (behind) {
    wrap.append(el("div", { class: "caveat" },
      behind,
      el("button", { class: "act", style: { marginLeft: "8px" },
                     onclick: () => set({ tbStamp: null }) }, "Read again")));
  }

  if (!matching.length) {
    wrap.append(el("div", { class: "empty",
      text: `no series matches “${s.tbFilter}”` }));
    return wrap;
  }
  seriesGroups(wrap, matching, payload.series, s, "from the trainer's event files");
  return wrap;
}
