// Take a picture of what is on screen, and hand it to the agent.
//
// The loop this closes: you are watching a clip or a curve, something is
// wrong with it, and without this the only way to say so is to describe it in
// words. A frame is worth more than the description — the agent can look at it
// — and a *rendered frame* can already go through the annotation flow, which
// writes to the feedback ledger. This is the other half: anything on
// screen, into the message box, with a comment you type.
//
// Three sources, and each needs its own answer:
//
//   a <video>   drawn to a canvas at its natural size — a clip's current frame
//   an <svg>    serialised and rasterised — a chart is not a bitmap until asked
//   an <img>    drawn directly, which also covers a rendered frame
//
// A viser <iframe> is deliberately absent: it is a different origin's
// document and the browser will not let this page read its pixels. The button
// says so rather than producing a blank rectangle.

import { el, on } from "./ui.js";
import { state, set } from "./state.js";

/** The largest edge of a capture. A 4K frame is not more useful to a model
 *  than a 1600px one and costs four times the message. */
const MAX_EDGE = 1600;

function scaled(w, h) {
  const factor = Math.min(1, MAX_EDGE / Math.max(w || 1, h || 1));
  return [Math.max(1, Math.round(w * factor)), Math.max(1, Math.round(h * factor))];
}

/** A PNG data url of whatever `node` is showing, or "" with a reason. */
export async function capture(node) {
  if (!node) return { url: "", reason: "there is nothing to capture" };
  const tag = node.tagName;

  if (tag === "IFRAME") {
    return { url: "", reason: "the 3-D view is another document, and a page "
                            + "cannot read another document's pixels. Use the "
                            + "viewer's own screenshot, or Look for a frame." };
  }

  try {
    if (tag === "VIDEO") {
      const [w, h] = scaled(node.videoWidth, node.videoHeight);
      if (!node.videoWidth) {
        return { url: "", reason: "the clip has not loaded a frame yet" };
      }
      return { url: draw(node, w, h) };
    }
    if (tag === "IMG") {
      const [w, h] = scaled(node.naturalWidth || node.width, node.naturalHeight || node.height);
      return { url: draw(node, w, h) };
    }
    if (tag === "svg" || tag === "SVG" || node.namespaceURI === "http://www.w3.org/2000/svg") {
      return { url: await fromSvg(node) };
    }
    // A card, a panel: capture the picture inside it rather than refusing.
    const inner = node.querySelector("video, img, svg");
    if (inner) return capture(inner);
    return { url: "", reason: "there is no picture in that panel to capture" };
  } catch (err) {
    // A cross-origin image taints the canvas and `toDataURL` throws. Saying so
    // beats a broken thumbnail with no explanation.
    return { url: "", reason: `that could not be captured (${err.name})` };
  }
}

function draw(source, w, h) {
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  // A chart on a dark ground is transparent where the page shows through, and
  // a transparent PNG reaches the model as black on black.
  ctx.fillStyle = getComputedStyle(document.body).backgroundColor || "#fff";
  ctx.fillRect(0, 0, w, h);
  ctx.drawImage(source, 0, 0, w, h);
  return canvas.toDataURL("image/png");
}

/** An SVG chart, rasterised.
 *
 * Serialised with its computed colours resolved: the app draws in CSS custom
 * properties, and a detached SVG has no `:root` to resolve them against — so
 * a chart rasterised naively comes out black on black.
 */
async function fromSvg(svg) {
  const clone = svg.cloneNode(true);
  const box = svg.getBoundingClientRect();
  const [w, h] = scaled(box.width || 420, box.height || 96);
  clone.setAttribute("width", w);
  clone.setAttribute("height", h);
  resolveColours(svg, clone);

  const blob = new Blob([new XMLSerializer().serializeToString(clone)],
                        { type: "image/svg+xml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  try {
    const image = await load(url);
    return draw(image, w, h);
  } finally {
    URL.revokeObjectURL(url);
  }
}

/** Copy the *computed* stroke and fill onto the clone, node for node. */
function resolveColours(live, clone) {
  const from = [live, ...live.querySelectorAll("*")];
  const to = [clone, ...clone.querySelectorAll("*")];
  for (let i = 0; i < from.length && i < to.length; i += 1) {
    const style = getComputedStyle(from[i]);
    for (const property of ["fill", "stroke", "stop-color", "color"]) {
      const value = style.getPropertyValue(property);
      if (value && value !== "none" && value.indexOf("var(") === -1) {
        to[i].setAttribute(property, value);
      }
    }
  }
}

function load(src) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("SecurityError"));
    image.src = src;
  });
}

// ── the window it pops up in ─────────────────────────────────────────────

/** Take a picture of `node` and open it for review. */
export async function shoot(node, label = "") {
  const { url, reason } = await capture(node);
  if (!url) { set({ shotError: reason || "that could not be captured" }); return; }
  set({ shot: { url, label, box: null }, shotAnnotate: false, shotError: "" });
}

/** The review window: the picture, an Annotate toggle that is off, and the
 *  button that puts it in the message box. */
export function shotOverlay(s, actions) {
  if (!s.shot) return null;
  const annotating = !!s.shotAnnotate;
  const box = s.shot.box;

  const image = el("img", { class: "shot-img", src: s.shot.url, alt: s.shot.label || "capture" });
  const frame = el("div", { class: `shot-frame${annotating ? " drawing" : ""}` }, image,
    box ? el("div", { class: "shot-box", style: {
      left: `${box.x * 100}%`, top: `${box.y * 100}%`,
      width: `${box.w * 100}%`, height: `${box.h * 100}%`,
    } }) : null);

  if (annotating) attachDrag(frame);

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) actions.closeShot(); },
  },
    el("div", { class: "z-box shotbox" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: s.shot.label || "Screenshot" }),
        el("span", { class: "sp" }),
        // Off by default: most of the time the
        // picture is the point and a drawing tool in the way is friction.
        el("button", {
          class: annotating ? "act on" : "act",
          title: "draw a box on it, to point at the thing you mean",
          onclick: () => set({ shotAnnotate: !annotating,
                               shot: { ...s.shot, box: annotating ? null : s.shot.box } }),
        }, annotating ? "Annotating" : "Annotate"),
        el("button", { class: "act", onclick: () => actions.closeShot() }, "Close")),
      frame,
      el("div", { class: "shot-go" },
        el("span", { class: "eyebrow",
                     text: annotating && !box ? "drag a box on the picture"
                         : annotating ? "drag again to move the box"
                         : "it goes into the message box, where you can say what to look at" }),
        el("span", { class: "sp" }),
        el("button", { class: "primary",
                       onclick: () => actions.attachShot() },
          "Add to the message"))));
}

/** Drag a box on the picture. In the store, because the live feed repaints
 *  the tree underneath a half-drawn box -- the same reason `annotate.js`
 *  keeps its own there. */
function attachDrag(frame) {
  let from = null;
  on(frame, "pointerdown", (e) => {
    const node = e.currentTarget;
    const rect = node.getBoundingClientRect();
    from = { x: (e.clientX - rect.left) / rect.width,
             y: (e.clientY - rect.top) / rect.height };
    node.setPointerCapture(e.pointerId);
  });
  on(frame, "pointermove", (e) => {
    if (!from) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const to = { x: (e.clientX - rect.left) / rect.width,
                 y: (e.clientY - rect.top) / rect.height };
    set({ shot: { ...state.shot, box: {
      x: Math.min(from.x, to.x), y: Math.min(from.y, to.y),
      w: Math.abs(to.x - from.x), h: Math.abs(to.y - from.y),
    } } });
  });
  on(frame, "pointerup", () => { from = null; });
}

/** The picture with its box burned in, ready to send.
 *
 * Burned in rather than sent alongside: the agent receives one image, and a
 * box described in coordinates beside a picture is a puzzle it has to solve
 * before it can answer.
 */
export async function flatten(shot) {
  if (!shot || !shot.box || !shot.box.w) return shot ? shot.url : "";
  const image = await load(shot.url);
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
  const ctx = canvas.getContext("2d");
  ctx.drawImage(image, 0, 0);
  const { x, y, w, h } = shot.box;
  ctx.strokeStyle = "#ff3b30";
  ctx.lineWidth = Math.max(2, Math.round(canvas.width / 320));
  ctx.strokeRect(x * canvas.width, y * canvas.height,
                 w * canvas.width, h * canvas.height);
  return canvas.toDataURL("image/png");
}
