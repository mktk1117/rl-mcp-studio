// What every view of a run shares: the state of the paint in progress, the
// record node it is about, and a few small controls.

import { el } from "../ui.js";
import { state, set } from "../state.js";

// Rebuilding the whole tree on every keystroke into a filter is slow, and
// fatal to an IME: a composition lives in the element and dies with it, so
// Japanese could not be typed into these boxes at all. The draft is kept
// immediately and the repaint is deferred until you pause.
let typingTimer = 0;

export function typed(field, value) {
  state[field] = value;
  clearTimeout(typingTimer);
  typingTimer = setTimeout(() => set({ [field]: value }), 180);
}

export let nodes = [];
export let picked = null;
export let ctx = null;

/** Point every view at this paint: its state and actions, the record graph,
 *  and the node the selected run is. Assigned here and only read elsewhere. */
export function enter(ctxState, actions) {
  ctx = { state: ctxState, actions };
  nodes = ctxState.graph || [];
  picked = nodes.find(n => n.id === ctxState.runId) || nodes[nodes.length - 1] || null;
}

/** A segmented control: one question, two answers to switch between.
 *
 * `seg` / `seg-btn` are the app's own, already worn by the robot card's
 * Now / Over training / Live 3-D control. A second one styled slightly
 * differently would be two controls that mean the same thing, and CSS for it
 * would redefine `.seg` for everybody, turning the robot card's pill into a
 * rectangle. */
export function segmented(options, current, onPick) {
  return el("div", { class: "seg" }, options.map(o => el("button", {
    class: "seg-btn", "aria-pressed": o.id === current ? "true" : "false",
    title: o.blurb || "",
    onclick: () => onPick(o.id),
  }, o.label, o.count === undefined ? null
     : el("small", { text: ` ${o.count}` }))));
}

export function pick(id) {
  picked = nodes.find(n => n.id === id) || picked;
  set({ pickedNode: picked ? picked.id : null });
}

export function escape(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
