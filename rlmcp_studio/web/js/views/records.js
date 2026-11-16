// Records: every run of the task, as story cards or as the lineage tree.

import { el, dot, verdictColor, on } from "../ui.js";
import { placed, sideVar } from "../resize.js";
import { set } from "../state.js";
import { reportControl } from "../report.js";
import { recipeView } from "../recipe.js";
import { nodes, picked, ctx, segmented, pick, escape } from "./shared.js";

// ── records ─────────────────────────────────────────────────────────────
//
// Cards and the graph are two readings of one set. Which you want changes
// minute to minute -- "what happened in this run" and "what did it come from"
// are asked in the same breath -- so it is a button rather than a trip through
// the tab strip.
const RECORD_MODES = new Set(["story", "tree", "recipe"]);

export function recordsView() {
  const mode = RECORD_MODES.has(ctx.state.recordsMode) ? ctx.state.recordsMode : "story";
  return el("div", { class: "recordsview" },
    el("div", { class: "plots-h" },
      segmented([
        { id: "story", label: "cards", count: nodes.length || undefined,
          blurb: "one card per run, newest first" },
        { id: "tree", label: "graph",
          blurb: "config and warm-start edges, as rlmcp draws them" },
        // The third answer to "what came from what": not the shape of the
        // tree but what walking it *accumulated*. It belongs here rather than
        // in a tab of its own because it is a reading of the same records,
        // and because you ask it about the run you have just clicked on.
        { id: "recipe", label: "recipe",
          blurb: "code, config and interventions folded from the root" },
      ], mode, (id) => set({ recordsMode: id })),
      // The fourth reading of the same records: all of them, in one file you
      // can send. It sits with the other three because it is asked about the
      // task on screen, not about a run.
      el("span", { style: { marginLeft: "auto" } }),
      reportControl(ctx.state, ctx.actions)),
    mode === "recipe" ? recipeView(ctx.state, ctx.actions)
    : mode === "tree" ? treeView() : storyView());
}

// ── story ───────────────────────────────────────────────────────────────
// Which card the pointer is over. Module-level rather than state: setting
// state on hover would repaint the grid, and repainting the grid is what
// interrupts the video you are hovering.
let hovering = "";

function storyView() {
  const grid = el("div", { class: "stories" });
  for (const node of nodes) {
    grid.append(storyCard(node));
  }
  // A repaint rebuilds the cards, so the one under the pointer has to be told
  // to carry on. Without this a card stops playing roughly once a second.
  if (hovering) {
    const again = grid.querySelector(`[data-story="${hovering}"] video`);
    if (again) again.play().catch(() => {});
  }
  // And the open one, for the same reason: a rebuild pauses it, and a card
  // that stops playing a second after you click it reads as broken.
  if (picked) {
    const open = grid.querySelector(`[data-story="${picked.id}"] video`);
    if (open) open.play().catch(() => {});
  }
  return grid;
}

/** Whether this run's trainer is answering, for a node that only has a claim.
 *
 * The graph is built from records and a record does not know what a process is
 * doing; the rail's payload does, and both are about the task on screen, so
 * the id is enough to join them.
 */
function isLive(id) {
  return (ctx.state.runs || []).some(r => r.id === id && r.live === true);
}

function storyCard(node) {
  const clip = node.clip && node.clip.src ? node.clip : null;
  // The one you clicked opens: its clip plays without being hovered, and what
  // the run claimed is on the card rather than a panel away. A grid of cards
  // where clicking only *selects* makes you look somewhere else to find out
  // what you selected, which is the trip this saves.
  const open = !!(picked && node.id === picked.id);
  const card = el("button", {
    class: `story${open ? " story-open" : ""}`,
    "data-story": node.id,
    "aria-current": open ? "true" : null,
    "aria-expanded": open ? "true" : "false",
    onclick: () => { pick(node.id); ctx.actions.selectRun(node.id); },
  },
    el("span", { class: "stripe", style: { background: verdictColor(node.verdict) } }),
    clip ? clipPreview(node, clip, open) : null,
    el("span", { class: "story-b" },
      el("span", { class: "story-top" },
        // Lit by the trainer, not by the word. The graph nodes carry the
        // record's claim; whether anything is training is on the runs payload
        // beside them, and `running` outlives the process by hours.
        dot(node.verdict, isLive(node.id)),
        el("span", { class: "story-id", text: node.id }),
        el("span", { class: "story-slug", text: node.slug })),
      el("span", { class: "story-line", text: node.summary || "—" }),
      node.warm_run
        ? el("span", { class: "story-meta", text: `warm start ← ${node.warm_run}` })
        : el("span", { class: "story-meta", text: node.scratch ? "from scratch" : "" }),
      open ? storyMore(node) : null));
  return card;
}

/** What the open card says beyond its headline.
 *
 * The same facts `detailPanel` draws for the graph, in the card, because the
 * question "what was this run and did it work" is asked *at* the card.
 * Rendered as spans rather than a `<dl>`: this is inside a `<button>`, and a
 * definition list in a button is invalid markup that browsers flatten
 * unpredictably.
 */
function storyMore(node) {
  const rows = [
    ["claimed", node.hypothesis],
    ["predicted", node.prediction],
    ["falsifier", node.falsifier],
    ["outcome", node.outcome],
  ].filter(([, v]) => v);
  const measured = (node.metrics || []).slice(0, 4);

  return el("span", { class: "story-more" },
    rows.map(([k, v]) => el("span", { class: "sm-row" },
      el("span", { class: "sm-k", text: k }),
      el("span", { class: "sm-v", text: v }))),
    measured.length
      ? el("span", { class: "sm-metrics" },
          measured.map(([k, v]) => el("span", { class: "sm-metric" },
            el("b", { text: String(v) }), el("span", { text: String(k) }))))
      : null);
}

/** The run's clip, playing while the pointer is on the card.
 *
 * Muted and `preload="none"`: muted because a wall of cards that makes noise on
 * hover is intolerable and because browsers refuse unmuted autoplay anyway, and
 * unloaded because a grid of cards each fetching a video to sit still is a
 * megabyte each for nothing. It loads when you point at it.
 */
function clipPreview(node, clip, open = false) {
  const src = ctx.state.recordAssetUrl(clip.src);
  const video = el("video", {
    class: "story-clip", src, muted: true, loop: true,
    playsinline: true, preload: "none",
    title: clip.caption || "",
  });
  video.muted = true;                 // the attribute alone is not enough in all browsers

  const wrap = el("span", { class: "story-film" }, video,
    el("span", { class: "story-play", text: "▶" }));

  // Someone who asked for less motion gets the still and the caption, not a
  // grid of moving pictures.
  const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  // The open card plays on its own. `preload` has to change with it: `none`
  // is right for a grid of twenty stills and means the open one would sit
  // black until something asked for a frame.
  if (open && !still) {
    video.setAttribute("preload", "auto");
    video.autoplay = true;
    queueMicrotask(() => { video.play().catch(() => {}); });
  }
  if (!still) {
    on(wrap, "pointerenter", () => { hovering = node.id; video.play().catch(() => {}); });
    on(wrap, "pointerleave", () => {
      if (hovering === node.id) hovering = "";
      video.pause();
      video.currentTime = 0;
    });
  }
  return wrap;
}


// ── tree ────────────────────────────────────────────────────────────────
function treeView() {
  const X0 = 34, LANE = 62, Y0 = 26, ROW = 44;
  const at = {};
  for (const n of nodes) at[n.id] = { x: X0 + (n.lane || 0) * LANE, y: Y0 + (n.row || 0) * ROW };

  const width = Math.max(560, X0 + (Math.max(...nodes.map(n => n.lane || 0)) + 1) * LANE + 300);
  const height = Y0 + nodes.length * ROW + 10;

  const edges = nodes.filter(n => n.parent && at[n.parent]).map(n => {
    const a = at[n.parent], b = at[n.id];
    const d = a.x === b.x
      ? `M${a.x} ${a.y} L${b.x} ${b.y}`
      : `M${a.x} ${a.y} C${a.x} ${a.y + ROW * .6} ${b.x} ${b.y - ROW * .6} ${b.x} ${b.y}`;
    return `<path d="${d}" fill="none" stroke="var(--line)" stroke-width="1.6"/>`;
  }).join("");

  // Warm-start edges are drawn separately because they are a different claim,
  // and they often disagree with the config parent.
  const warm = nodes.filter(n => n.warm_run && at[n.warm_run]).map(n => {
    const a = at[n.warm_run], b = at[n.id];
    const bow = 120;
    return `<path d="M${a.x} ${a.y} C${a.x + bow} ${a.y} ${b.x + bow} ${b.y} ${b.x} ${b.y}"
             fill="none" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="4 3" opacity=".8"/>`;
  }).join("");

  const marks = nodes.map(n => {
    const p = at[n.id], c = verdictColor(n.verdict);
    const on = picked && n.id === picked.id;
    return `<g>
      ${on ? `<circle cx="${p.x}" cy="${p.y}" r="11" fill="none" stroke="var(--accent)" stroke-width="1.4"/>` : ""}
      <circle cx="${p.x}" cy="${p.y}" r="6" fill="${c}" stroke="var(--ground)" stroke-width="2"/>
      <text x="${p.x + 15}" y="${p.y - 1}" fill="var(--text)" font-family="ui-monospace,monospace" font-size="11.5" font-weight="600">${n.id}</text>
      <text x="${p.x + 15 + Math.round(n.id.length * 7.2) + 8}" y="${p.y - 1}" fill="var(--muted)" font-family="ui-monospace,monospace" font-size="10.5">${escape(n.slug)}</text>
      <text x="${p.x + 15}" y="${p.y + 12}" fill="var(--dim)" font-family="ui-monospace,monospace" font-size="9.5">${n.verdict}</text>
    </g>`;
  }).join("");

  const svg = el("div", { class: "treesvg", html:
    `<svg viewBox="0 0 ${width} ${height}" style="max-width:${width}px">${edges}${warm}${marks}</svg>` });

  // Click anywhere in the svg: nearest node by y, which is how the rows read.
  on(svg, "click", (e) => {
    // `e.currentTarget`, not `svg`: this closure belongs to the copy `patch()`
    // discarded, a detached node measures 0x0, and the arithmetic below then
    // divides by zero and picks nothing: the tree stops responding to clicks,
    // with no error anywhere. See `ui.js`.
    const box = e.currentTarget.querySelector("svg").getBoundingClientRect();
    const y = ((e.clientY - box.top) / box.height) * height;
    let best = null, bestDist = Infinity;
    for (const n of nodes) {
      const d = Math.abs(at[n.id].y - y);
      if (d < bestDist) { bestDist = d; best = n; }
    }
    if (best) { pick(best.id); ctx.actions.selectRun(best.id); }
  });

  const { widths } = ctx.state;
  return el("div", { class: "treewrap", style: { "--side": sideVar("treeDetail", widths) } },
    el("div", {},
      svg,
      // A legend for edges a one-node graph does not have is furniture.
      nodes.length > 1 ? el("div", { class: "legend2" },
        el("span", { html: `<svg width="26" height="8"><path d="M1 4 H25" stroke="var(--muted)" stroke-width="1.4"/></svg> config parent` }),
        el("span", { html: `<svg width="26" height="8"><path d="M1 4 H25" stroke="var(--accent)" stroke-width="1.4" stroke-dasharray="4 3"/></svg> warm start — where the weights came from` })) : null),
    detailPanel(),
    // Half the 18px gap: the seam between the tree and the claims.
    placed("treeDetail", { side: "right", widths, actions: ctx.actions, offset: 9 }));
}

function detailPanel() {
  if (!picked) return el("div", { class: "detail" }, el("div", { class: "empty", text: "pick a run" }));
  const rows = [
    ["claims", picked.hypothesis],
    ["predicted", picked.prediction],
    ["falsifier", picked.falsifier],
    ["outcome", picked.outcome],
    ["config", picked.parent ? `from ${picked.parent}` : "root"],
    ["weights", picked.warm_run ? `from ${picked.warm_run}` : "from scratch"],
  ].filter(([, v]) => v);

  const dl = el("dl", {});
  for (const [k, v] of rows) {
    dl.append(el("dt", { text: k }), el("dd", { text: v }));
  }

  const recipe = picked.recipe || [];
  return el("div", { class: "detail" },
    el("div", { class: "eyebrow", style: { marginBottom: "9px" }, text: `selected · ${picked.id}` }),
    dl,
    recipe.length
      ? el("div", { style: { marginTop: "12px" } },
          el("div", { class: "eyebrow", style: { marginBottom: "6px" }, text: "recipe · folded from the root" }),
          recipe.map(step => el("div", { class: "recipe-step" },
            el("span", { class: "rs-id", text: step.id }),
            el("span", { class: "rs-c", text: (step.change || []).join("; ") || "—" }))),
          el("div", { class: "k", style: { color: "var(--dim)", marginTop: "7px" },
                      text: "computed, never stored, so it cannot drift" }))
      : null);
}
