// Draggable dividers between panels.
//
// Three things this has to get right.
//
// **It is visible.** A thin strip whose line only appears on hover gives no
// way to know it is there -- it reads as missing rather than subtle. A divider that can be dragged should look like a
// divider first and reveal that it drags on approach.
//
// **Every panel, one mechanism.** Widths live in one map under one key, so
// adding a resizable panel is an entry rather than another copy of the drag
// handling, the clamping and the persistence.
//
// **It survives the repaint.** Pointer capture rather than window listeners: a
// drag that leaves the element still belongs to it, and there is nothing to
// unbind if the tree is rebuilt mid-drag -- which it will be, because the live
// feed repaints several times a minute.

import { el, on } from "./ui.js";

const KEY = "rlmcp.panels";

/** A share of the window, bounded: a default that suits a laptop and a very
 * wide monitor alike. Only a default -- a width you drag is saved and wins. */
function share(fraction, min, max) {
  const width = typeof window === "undefined" ? 1280 : window.innerWidth;
  return Math.max(min, Math.min(max, Math.round(width * fraction)));
}

function agentDefault() { return share(0.24, 300, 560); }

export const DEFAULTS = {
  rail: 198,        // records and conversations
  // The transcript. A fixed 312px is right on a laptop and thin on a very
  // wide monitor, so the default is a share of the window, bounded so it is neither a strip nor half the app.
  // Only a *default*: a width you drag is saved and wins from then on.
  agent: agentDefault(),
  focusRail: 208,
  focusAgent: 320,
  tree: 288,        // the file tree in Source
  // Control's two columns, as a percentage of the stage rather than pixels:
  // a pixel split chosen on a wide window leaves nothing for the right column
  // on a narrow one, and this panel is the one people resize the window on.
  controlSplit: 56,
  // The robot panel's height. The one panel whose *content* is the point --
  // a 3-D view, a rendered frame, a clip -- and the one the stage gives the
  // least room to, because everything under it is text that wants its own.
  robot: 360,
  // The terminal drawer's height.
  term: 240,
  // Records › graph: the claims panel beside the tree. It holds a run's whole
  // hypothesis, prediction and falsifier -- paragraphs, not a caption -- and
  // at 300px a claim is a column of forty short lines.
  treeDetail: share(0.36, 420, 820),
  // Parameters: the list of traced keys beside the plot.
  paramList: 268,
  // Focus › Control: the side column that carries the record and its verbs.
  focusSide: 216,
  // The document list in Docs and the shelf in Library.
  docsNav: 288,
  libNav: 288,
  // Overview: the facts rail beside the report.
  overviewFacts: 320,
  // Build: the side column (verification, terms, launch), as a percentage of
  // the body -- the same reasoning as `controlSplit`.
  buildSide: 50,
};

/** What the stage keeps whatever a side panel was dragged to, and the rail
 *  beside it: the widest rail default plus its handle. */
const STAGE_MIN = 520;
const RAIL_ALLOWANCE = 220;

/** The panels stored as a percentage of their container, not in pixels. */
export const PERCENT = new Set(["controlSplit", "buildSide"]);

const LIMITS = {
  rail: [140, 420],
  agent: [240, 900],
  focusRail: [140, 420],
  focusAgent: [240, 900],
  tree: [180, 520],
  controlSplit: [28, 78],
  robot: [220, 1100],
  term: [120, 700],
  treeDetail: [280, 1400],
  paramList: [180, 640],
  focusSide: [160, 700],
  docsNav: [150, 640],
  libNav: [150, 640],
  overviewFacts: [180, 760],
  buildSide: [25, 75],
};

/** Told when widths change, so they reach the settings file too.
 *
 * A callback rather than an import: `state.js` already imports this module for
 * `loadWidths`, and importing it back would be a cycle. `main.js` wires the
 * two together at boot, which is where the app's other seams are joined.
 */
let onSaved = () => {};

export function reportWidths(fn) { onSaved = fn; }

/** Widths for this browser, defaults filled in. */
export function loadWidths() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(KEY) || "{}") || {};
  } catch {
    saved = {};                       // a corrupt entry is not worth an error
  }
  const out = { ...DEFAULTS };
  for (const [name, value] of Object.entries(saved)) {
    if (typeof value === "number" && Number.isFinite(value)) out[name] = clamp(name, value);
  }
  return out;
}

export function saveWidth(widths, name, px) {
  const next = { ...widths, [name]: clamp(name, px) };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* private mode, or a full quota: the drag still works for this session */
  }
  // And in the settings file, so panel sizes follow you to another browser.
  // Batched at the far end -- a drag calls this many times a second.
  onSaved(next);
  return next;
}

export function clamp(name, px) {
  const [min, max] = LIMITS[name] || [120, 900];
  return Math.max(min, Math.min(max, Math.round(px)));
}

/** A saved size, capped by the window it is being applied in.
 *
 * `clamp` bounds a size against what is *sensible*; this bounds it against
 * what will *fit*, and the two are different questions. A robot panel dragged
 * to 903px on a tall monitor is a legal size, and on an 800px laptop it is a
 * card taller than the stage that holds it -- the video ends up below the fold
 * with nothing to say why.
 *
 * Applied at render, never written back: the number you chose is still yours,
 * and it comes back the moment the window is big enough for it again. Saving
 * the capped value would quietly shrink a panel every time you opened the
 * studio on a laptop.
 */
export function fits(name, px) {
  if (PERCENT.has(name)) return clamp(name, px);   // a share always fits
  const [min] = LIMITS[name] || [120, 900];
  const vertical = name === "robot" || name === "term";
  const room = vertical
    // What the stage can give a single panel and still show something under
    // it: the chrome, the tab strip, the status bar and one more card.
    ? Math.max(min, window.innerHeight - 300)
    // Sideways: 60% of the window, and never so much that the stage between
    // the rail and this panel drops under `STAGE_MIN`. Without that floor an
    // agent column dragged to 900px on a wide monitor would take 60% of a
    // 1500px window and leave the Plots view a 378px strip -- the header
    // wrapping one word per line, the filter box cut off, one chart per row.
    : Math.max(min, Math.min(Math.round(window.innerWidth * 0.6),
                             window.innerWidth - RAIL_ALLOWANCE - STAGE_MIN));
  return Math.min(clamp(name, px), room);
}

/**
 * A divider that resizes the panel on one side of it.
 *
 * `side` says which panel the handle belongs to, because that decides which
 * way the drag reads: dragging the left edge of a right-hand panel *right*
 * makes it narrower, and the arithmetic is the opposite for a left-hand one.
 */
export function divider(name, {
  side = "right", onResize, onReset,
  // Which way the handle drags. `x` resizes widths and sits on a panel's left
  // or right edge; `y` resizes heights and sits on its top or bottom. The
  // arithmetic is the same either way -- only the coordinate read and the
  // sense of "grows" change -- so one divider serves both rather than a second
  // near-copy that would drift from this one.
  axis = "x",
  // What the panel is *now*, in the units this name is stored in, and how a
  // pixel drag translates into them. Defaults suit a panel measured in pixels.
  //
  // Measuring the element instead is wrong for a panel stored as a
  // percentage: css grid divides what is left after padding and the gap, so
  // the measured share never equals the stored one and every key press
  // re-derives from a slightly different basis -- the split walks downwards
  // a percent at a time whichever arrow you press.
  current = (handle) => {
    const box = handle.parentElement.getBoundingClientRect();
    return axis === "y" ? box.height : box.width;
  },
  perPixel = () => 1,
} = {}) {
  // Which end of the drag makes the panel bigger. A handle on a panel's
  // trailing edge (its right, or its bottom) grows it as the pointer moves
  // away from the panel; one on the leading edge grows it as the pointer
  // moves toward the panel's own side.
  const grows = side === "left" || side === "bottom" ? 1 : -1;
  const handle = el("div", {
    class: `divider ${side}${axis === "y" ? " vert" : ""}`,
    role: "separator",
    "aria-orientation": axis === "y" ? "horizontal" : "vertical",
    tabindex: "0",
    title: "drag to resize · double-click to reset",
  });

  // Two rules, and this handful of lines is where both of them bite.
  //
  // **Handlers go through `on()`**, so the node the reconciler *keeps* carries
  // them. A bare `addEventListener` happens to work here -- the surviving node
  // keeps the listeners it was built with -- but only by accident, and the
  // rule that guards every other panel cannot have an exception it does not
  // understand.
  //
  // **The handler reads `e.currentTarget`, never `handle`.** Every repaint
  // builds a fresh divider whose closures point at a node that was discarded;
  // reading it would capture nothing and drag nothing.
  //
  // **The drag's origin lives on the node**, not in this scope. A repaint
  // between pointerdown and pointermove -- and with a live run there is one
  // every few seconds -- swaps in handlers from a scope where no drag ever
  // started, so a `startX` held here would read 0 mid-drag and the panel would
  // jump to its minimum. The node is the one thing still there.
  const panelOf = (node) => node.parentElement;

  on(handle, "pointerdown", (e) => {
    const node = e.currentTarget;
    if (!panelOf(node)) return;
    node.__drag = { at: axis === "y" ? e.clientY : e.clientX, width: current(node) };
    node.setPointerCapture(e.pointerId);
    node.classList.add("dragging");
    document.body.classList.add("resizing");
    if (axis === "y") document.body.classList.add("rows");
    e.preventDefault();
  });

  on(handle, "pointermove", (e) => {
    const node = e.currentTarget;
    const drag = node.__drag;
    if (!drag || !node.hasPointerCapture(e.pointerId)) return;
    const at = axis === "y" ? e.clientY : e.clientX;
    const delta = (at - drag.at) * perPixel(node);
    onResize(clamp(name, drag.width + grows * delta));
  });

  const stop = (e) => {
    const node = e.currentTarget;
    if (node.hasPointerCapture(e.pointerId)) node.releasePointerCapture(e.pointerId);
    node.__drag = null;
    node.classList.remove("dragging");
    document.body.classList.remove("resizing");
    document.body.classList.remove("rows");
  };
  on(handle, "pointerup", stop);
  on(handle, "pointercancel", stop);
  on(handle, "dblclick", () => onReset(DEFAULTS[name]));

  // Keyboard, because a divider that only answers to a mouse is unusable for
  // anyone who does not use one, and it costs four lines.
  on(handle, "keydown", (e) => {
    // Proportional to the panel's own range, because the units differ: 12 is a
    // reasonable nudge in pixels and a third of the way across a panel measured
    // in percent, where three presses hit the stop.
    const [min, max] = LIMITS[name] || [120, 900];
    const step = Math.max(1, ((max - min) / 40)) * (e.shiftKey ? 4 : 1);
    const node = e.currentTarget;
    if (!panelOf(node)) return;
    const width = current(node);
    const [less, more] = axis === "y"
      ? ["ArrowUp", "ArrowDown"] : ["ArrowLeft", "ArrowRight"];
    if (e.key === less) {
      onResize(clamp(name, width - grows * step));
    } else if (e.key === more) {
      onResize(clamp(name, width + grows * step));
    } else if (e.key === "Home") {
      onReset(DEFAULTS[name]);
    } else {
      return;
    }
    e.preventDefault();
  });

  return handle;
}

/**
 * The size a container gives a panel, as a css value for its `--side`.
 *
 * The container's grid reads `var(--side, <the old fixed width>)`, so a
 * stylesheet rule that stacks the columns on a narrow window still wins: an
 * inline `grid-template-columns` would override every media and container
 * query, and the rail would stay a column on a phone. `cap` bounds the panel
 * to a share of its container as well ("40%"), for the lists whose stylesheet
 * refuses to let them eat a narrow window.
 */
export function sideVar(name, widths, { cap = null } = {}) {
  if (PERCENT.has(name)) return `${clamp(name, widths[name])}%`;
  const px = fits(name, widths[name]);
  return cap ? `min(${px}px, ${cap})` : `${px}px`;
}

/**
 * A divider placed by the *container* rather than by the panel it resizes.
 *
 * The dividers on the rail and the agent column sit inside the panel, on its
 * edge. That does not work for a panel that scrolls: `overflow: auto` clips
 * the half of the handle that hangs outside, adds a horizontal scrollbar for
 * the half that does not, and scrolls the handle away with the content. So
 * these sit in the grid container instead, at the column boundary the
 * container's `--side` puts them on -- `offset` is the container's padding
 * plus half its gap, which is where the seam between the columns actually is.
 * Absolutely positioned, so the grid does not give them a cell.
 */
export function placed(name, {
  side, widths, actions, offset = 0, cap = null,
} = {}) {
  const percent = PERCENT.has(name);
  const size = () => percent ? clamp(name, widths[name]) : fits(name, widths[name]);
  const handle = divider(name, {
    side,
    current: size,
    perPixel: percent
      ? (node) => {
          const box = node.parentElement.getBoundingClientRect();
          return box.width ? 100 / box.width : 0.1;
        }
      : () => 1,
    onResize: (v) => actions.setPanelWidth(name, v),
    onReset: (v) => actions.setPanelWidth(name, v),
  });
  handle.classList.add("at");
  const at = percent ? `${size()}%` : cap ? `min(${size()}px, ${cap})` : `${size()}px`;
  handle.style.setProperty("--at", offset ? `calc(${at} + ${offset}px)` : at);
  return handle;
}
