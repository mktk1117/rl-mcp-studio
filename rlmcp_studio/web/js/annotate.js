// Drawing on a frame, and saying what you see there.
//
// This is a feature that only becomes real in a GUI: nobody types `rlmcp
// feedback` mid-run to reach rl-mcp's feedback ledger, but everybody will drag
// a box around a wrist that snaps and type four words about it.
//
// Two things this module is careful about:
//
// **The tree repaints several times a minute** from the live feed. A half-drawn
// box, the note you are typing and the kind you picked all live in the store,
// so a repaint re-renders them instead of destroying them. The drag itself
// mutates `state.annot.box` in place and moves the rectangle by hand — one
// `set()` per drag rather than one per mouse move.
//
// **The pointer listeners are on the window**, not on the image. The node under
// the cursor can be replaced mid-drag by a repaint; the window cannot.

import { el, chip, on } from "./ui.js";
import { state, set, paintNow } from "./state.js";

// rl-mcp's six kinds, in the order a person reaches for them. Exported so the
// ledger and the annotation composer cannot drift into two different lists.
export const KINDS = ["observe", "steer", "correct", "constrain", "approve", "reject"];

// A drag shorter than this in both directions was a click, not a box.
const MIN_EDGE = 0.012;

function normalized(event, rect) {
  return {
    x: Math.min(Math.max((event.clientX - rect.left) / rect.width, 0), 1),
    y: Math.min(Math.max((event.clientY - rect.top) / rect.height, 0), 1),
  };
}

function place(node, box) {
  if (!node) return;
  node.style.left = `${box.x * 100}%`;
  node.style.top = `${box.y * 100}%`;
  node.style.width = `${box.w * 100}%`;
  node.style.height = `${box.h * 100}%`;
}

function beginDrag(event, path) {
  if (event.button !== 0) return;
  // From the event, not from a captured node: the closure that runs belongs to
  // the copy `patch()` discarded, and a detached node measures 0x0 -- so every
  // drag would start at the bottom-right corner and cover nothing.
  const rect = event.currentTarget.getBoundingClientRect();
  const start = normalized(event, rect);
  const box = { x: start.x, y: start.y, w: 0, h: 0 };

  // Mutated, not `set()` — a repaint per mouse move would fight the drag and
  // hammer the layout. The store still holds it, so a repaint that does happen
  // draws the box where it currently is.
  state.annot = { path, box };
  state.annotError = "";
  set({ annot: state.annot });
  // The one paint this drag needs happens now rather than a frame later, so
  // the box is on screen before the first move looks for it. After it, the
  // rectangle is moved directly and no repaint is wanted at all.
  paintNow();

  const move = (e) => {
    const here = normalized(e, rect);
    box.x = Math.min(start.x, here.x);
    box.y = Math.min(start.y, here.y);
    box.w = Math.abs(here.x - start.x);
    box.h = Math.abs(here.y - start.y);
    place(document.querySelector("[data-annot-box]"), box);
  };

  const up = () => {
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", up);
    if (box.w < MIN_EDGE && box.h < MIN_EDGE) set({ annot: null });
    else set({ annot: { path, box } });          // now the composer appears
  };

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
  event.preventDefault();
}

/** The frame, with whatever box is being drawn on it. */
export function frameStage(state, actions, path) {
  const drawing = state.annot && state.annot.path === path ? state.annot : null;

  const wrap = el("div", { class: `frame-wrap annot-able${drawing ? " drawing" : ""}` },
    el("img", { class: "frame", src: state.artifactUrl(path), alt: "latest frame",
                draggable: "false" }),
    drawing ? el("div", { class: "annot-box", "data-annot-box": "" }) : null);

  on(wrap, "pointerdown", (e) => beginDrag(e, path));
  if (drawing) place(wrap.querySelector("[data-annot-box]"), drawing.box);
  return wrap;
}

/** What to say about the box, and where to send it. */
export function composer(state, actions, path) {
  const drawing = state.annot && state.annot.path === path ? state.annot : null;
  if (!drawing) {
    return el("div", { class: "annot-hint" },
      "Drag a box on the frame to ask about one region — it lands on the record.");
  }

  const box = drawing.box;
  const agentReady = !!(state.agent && state.agent.available);

  const note = el("input", {
    class: "fb-input", type: "text",
    placeholder: "What is wrong here? (kept on the record)",
    onkeydown: (e) => {
      if (e.isComposing || e.keyCode === 229) return;   // the IME is still deciding
      if (e.key === "Enter") submit();
      if (e.key === "Escape") cancel();
    },
  });
  note.value = state.annotNote || "";
  on(note, "input", (e) => { state.annotNote = e.currentTarget.value; });

  const kind = el("select", { class: "fb-kind" },
    KINDS.map(k => el("option", { value: k, selected: (state.annotKind || "observe") === k }, k)));
  on(kind, "change", (e) => { state.annotKind = e.currentTarget.value; });

  const ask = el("input", { type: "checkbox", id: "annot-ask", disabled: !agentReady });
  ask.checked = agentReady && state.annotAsk !== false;
  on(ask, "change", (e) => { state.annotAsk = e.currentTarget.checked; });

  function cancel() { set({ annot: null, annotError: "" }); }

  function submit() {
    // From the store, not from `note`: see `ui.js`. This closure's `note` is
    // the copy `patch()` discarded.
    const text = (state.annotNote || "").trim();
    if (state.annotBusy) return;
    if (!text) {
      const live = document.querySelector(".annot .fb-input");
      (live || note).focus();
      set({ annotError: "say what you see in the box first" });
      return;
    }
    actions.annotate({ path, box, note: text,
                       kind: state.annotKind || "observe",
                       ask: state.annotAsk !== false });
  }

  return el("div", { class: "annot" },
    el("div", { class: "annot-where" },
      chip(`${(box.w * 100).toFixed(0)}×${(box.h * 100).toFixed(0)}% of the frame`),
      el("span", { class: "eyebrow",
        // Both halves in the same units: a size in percent beside a position
        // in 0–1 on one line reads as two different scales.
        text: `top-left at ${(box.x * 100).toFixed(0)}%, ${(box.y * 100).toFixed(0)}%` }),
      el("span", { class: "sp" }),
      el("button", { class: "act", onclick: cancel }, "Cancel")),
    el("div", { class: "fb-row" }, kind, note,
      el("button", { class: "primary", disabled: !!state.annotBusy, onclick: submit },
        state.annotBusy ? "sending…" : "Annotate")),
    el("label", { class: "annot-ask", for: "annot-ask", title: agentReady
        ? "the same box and note, with the image, go to this run's agent"
        : "sign in to the agent to ask about a region" },
      ask, agentReady ? "and ask the agent about it" : "agent not signed in"),
    state.annotError ? el("div", { class: "m-error", text: state.annotError }) : null);
}
