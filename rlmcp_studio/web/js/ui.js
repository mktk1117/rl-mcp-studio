// Tiny DOM helpers. Deliberately not a framework: the client has no build step
// and no Node toolchain, so it is plain ES modules loaded as they are. The
// module boundaries below are the ones a later React port would keep.

/** Attach a handler the way `el` does -- recorded, so a kept node gets it.
 *
 * `node.addEventListener(...)` on its own is a trap here. `patch()` keeps the
 * node that is already on screen and throws away the freshly built one, so a
 * listener attached by hand rides on the copy that gets discarded. A
 * placeholder morphed into the element a handler was meant for is the usual
 * case: the surviving node has never been through the function that attaches
 * the handler, so the drag or click is dead.
 *
 * The handler itself must read the event, never the node it closed over. The
 * closure that ends up running belongs to the *discarded* copy, so
 * `() => { draft = box.value }` reads a detached input that nobody ever typed
 * into, so every draft saved that way quietly becomes the empty string. Use
 * `e.currentTarget`.
 */
export function on(node, type, fn) {
  node.__on = node.__on || {};
  node.__on[type] = fn;
  node.addEventListener(type, fn);
  return node;
}

/** Fields a writing assistant must leave alone.
 *
 * Grammarly (and the extensions built like it) mounts its own overlay inside a
 * text field and takes focus to do it. In an app that rebuilds its tree twice a
 * second that reads as the box going dead a second after you click it, and it
 * cannot be reproduced in a browser with no extensions installed.
 *
 * `data-gramm="false"` is the documented opt-out; the other two are the older
 * spellings, kept because a user's extension may be any version. Set here
 * rather than on each box, because the guarantee worth having is that *no*
 * field is missed — including the next one somebody adds.
 *
 * This is not a code editor or a document: every text field here is a command,
 * a path, a parameter name or a message to a machine. None of them wants
 * spelling suggestions, so there is nothing lost by opting out.
 */
const NO_ASSIST = { "data-gramm": "false", "data-gramm_editor": "false",
                    "data-enable-grammarly": "false" };

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  if (tag === "textarea" || tag === "input") {
    for (const [k, v] of Object.entries(NO_ASSIST)) node.setAttribute(k, v);
  }
  // Handlers and innerHTML are recorded on the node, not just applied to it.
  // `patch()` below reuses nodes across renders, and a kept node with the
  // previous render's closures would act on state that has since changed --
  // and re-setting identical innerHTML would throw away an SVG the browser is
  // mid-way through painting.
  const handlers = {};
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "html") { node.innerHTML = v; node.__html = v; }
    else if (k === "text") node.textContent = v;
    else if (k === "style" && typeof v === "object") {
      for (const [prop, val] of Object.entries(v)) {
        // A custom property (`--side`) has to go through `setProperty`:
        // assigning it as a field of `style` silently sets nothing.
        if (prop.startsWith("--")) node.style.setProperty(prop, val);
        else node.style[prop] = val;
      }
    }
    else if (k.startsWith("on") && typeof v === "function") {
      handlers[k.slice(2)] = v;
      node.addEventListener(k.slice(2), v);
    }
    else node.setAttribute(k, v === true ? "" : String(v));
  }
  node.__on = handlers;
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

// ── keeping the DOM you already have ──────────────────────────────────────
//
// The client renders by building a fresh tree and putting it on screen. Doing
// that by *replacing* the old one destroys everything the browser keeps on a
// node: the caret, the text you had selected, an open <details>, an open
// <select> popup, focus, hover, scroll offset, an in-flight IME composition,
// and a click that started on a button and had not finished. Restoring each
// of those by hand, one at a time, covers every property imperfectly.
//
// So: build the new tree as before, then *morph* the old one into it. A node
// that is still the same node stays the same node, and the browser keeps all
// of that for free.
//
// Matching is by `key` where a caller gives one, and by position otherwise.
// Give keys to lists whose items move (runs, files, transcript entries); a
// list that only ever grows at the end does not need them.

function keyOf(node) {
  return node.nodeType === 1 ? node.getAttribute("key") : null;
}

function sameKind(a, b) {
  return a.nodeType === b.nodeType
      && (a.nodeType !== 1 || a.tagName === b.tagName)
      && keyOf(a) === keyOf(b);
}

function syncAttributes(from, to) {
  for (const attr of [...from.attributes]) {
    // `open` on a <details> is *user* state, like `value` on an input: the
    // person opened it. Stripping it would snap every thinking block,
    // tool-policy list and diff hunk shut on the next repaint -- twice a second
    // with a live run, so a thinking block could not be read at all. A caller that
    // wants one open still gets its way, because setting `open` is handled by
    // the loop below.
    if (attr.name === "open" && from.tagName === "DETAILS") continue;
    if (!to.hasAttribute(attr.name)) from.removeAttribute(attr.name);
  }
  for (const attr of [...to.attributes]) {
    if (from.getAttribute(attr.name) !== attr.value) {
      from.setAttribute(attr.name, attr.value);
    }
  }
  // `style` set through the object form lands in the style attribute, which the
  // loop above already copied; nothing else to do.

  // `value` and `checked` are *properties*: once a person has typed, the
  // attribute and the property have parted ways, and copying attributes alone
  // would leave a kept input showing whatever it showed before -- including
  // after the code cleared it, which is how a composer keeps the message it
  // just sent. Only written when it differs, so the caret does not move on
  // every repaint.
  if ("value" in to && from.value !== to.value) from.value = to.value;
  if ("checked" in to && from.checked !== to.checked) from.checked = to.checked;

  const before = from.__on || {};
  const after = to.__on || {};
  for (const [type, fn] of Object.entries(before)) {
    if (after[type] !== fn) from.removeEventListener(type, fn);
  }
  for (const [type, fn] of Object.entries(after)) {
    if (before[type] !== fn) from.addEventListener(type, fn);
  }
  from.__on = after;
}

/** Morph `from` into `to`, keeping every node that can be kept. */
export function morph(from, to) {
  if (!sameKind(from, to)) {
    from.replaceWith(to);
    return to;
  }
  if (from.nodeType === 3) {                      // text
    if (from.data !== to.data) from.data = to.data;
    return from;
  }
  if (from.nodeType !== 1) return from;

  syncAttributes(from, to);

  // A node whose content came from an html string is opaque: compare the
  // string and swap wholesale only when it changed.
  if (to.__html !== undefined || from.__html !== undefined) {
    if (from.__html !== to.__html) {
      from.innerHTML = to.__html || "";
      from.__html = to.__html;
    }
    return from;
  }
  // Monaco and the <video> singleton are moved into place by their owners and
  // must never be reconciled -- their contents belong to something else.
  if (from.dataset && from.dataset.keepChildren !== undefined) return from;

  patchChildren(from, to);
  return from;
}

function patchChildren(parent, next) {
  const news = [...next.childNodes];
  const byKey = new Map();
  for (const node of parent.childNodes) {
    const key = keyOf(node);
    if (key !== null) byKey.set(key, node);
  }

  const placed = new Set();
  let cursor = parent.firstChild;

  for (const wanted of news) {
    // Skip over nodes already put in their place this pass.
    while (cursor && placed.has(cursor)) cursor = cursor.nextSibling;

    const key = keyOf(wanted);
    let match = null;
    if (key !== null) {
      match = byKey.get(key) || null;
      if (match) byKey.delete(key);
    } else if (cursor && keyOf(cursor) === null && sameKind(cursor, wanted)) {
      match = cursor;
    }

    if (match) {
      morph(match, wanted);
      if (match === cursor) cursor = cursor.nextSibling;
      else parent.insertBefore(match, cursor);
      placed.add(match);
    } else {
      parent.insertBefore(wanted, cursor);
      placed.add(wanted);
    }
  }

  for (const node of [...parent.childNodes]) {
    if (!placed.has(node)) parent.removeChild(node);
  }
}

/** Replace the children of `parent` with `children`, keeping what can be kept. */
export function patch(parent, children) {
  const staging = document.createElement(parent.tagName);
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    staging.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  patchChildren(parent, staging);
  return parent;
}

/** Append children, skipping the ones that are not there.
 *
 * `Node.append(null)` inserts the *text* "null", so a bare `null` child shows
 * up on screen as a word. `el()` filters its children; anything appended
 * afterwards has to go through this.
 */
export function add(parent, ...children) {
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return parent;
}

export function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/** Bring `row` inside the list it lives in, moving nothing else.
 *
 * Not `scrollIntoView`, which scrolls *every* scrollable ancestor -- the rail,
 * then the stage, then the document -- so bringing a row onto a rail could
 * leave the panel beside it scrolled by a hundred pixels with nothing to say
 * why. `checks.html` reports that as a DIV running past its box. A list is the only thing that should move to show a row of
 * itself.
 */
export function scrollListTo(row, listSelector, { center = false } = {}) {
  const list = row && row.closest(listSelector);
  if (!list) return;
  const a = row.getBoundingClientRect();
  const b = list.getBoundingClientRect();
  if (a.top < b.top) list.scrollTop -= b.top - a.top;
  else if (a.bottom > b.bottom) list.scrollTop += a.bottom - b.bottom;
  // A strip scrolls sideways, and the one thing playing in it belongs in the
  // middle rather than just inside the edge it happened to be past.
  if (center) list.scrollLeft += (a.left + a.width / 2) - (b.left + b.width / 2);
  else if (a.left < b.left) list.scrollLeft -= b.left - a.left;
  else if (a.right > b.right) list.scrollLeft += a.right - b.right;
}


export function verdictColor(verdict) {
  const known = ["best", "running", "validated", "provisional", "falsified",
                 "planned", "interrupted", "control", "superseded"];
  return known.includes(verdict) ? `var(--v-${verdict})` : "var(--dim)";
}

export function dot(verdict, live = false) {
  const color = verdictColor(verdict);
  return el("span", { class: `dot${live ? " live" : ""}`, style: { background: color, color } });
}

export function chip(text, on = false) {
  return el("span", { class: `chip${on ? " on" : ""}`, text });
}

/** A parameter's value, for a diff or a table: `fmt` for numbers and inside
 *  lists, a dash for nothing. `fmt`, not `String`: `reward.joint_acc_l2.weight`
 *  is -2.5e-7, and three decimals turns every digit of it into "-0.000". */
export function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return `[${v.map(x => fmt(x)).join(", ")}]`;
  return typeof v === "number" ? fmt(v) : String(v);
}

export function fmt(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString("en-US", { maximumFractionDigits: 0 });
  // A small number is not zero. `reward.joint_acc_l2.weight` is -2.5e-7 and
  // three decimals would render it "-0.000" -- every digit of information
  // gone, and that string is what the edit box would pre-fill.
  if (n !== 0 && Math.abs(n) < 0.001) return n.toExponential(1).replace("e", "e");
  return n.toFixed(Math.abs(n) < 1 && n !== 0 ? 3 : digits);
}

export function elapsed(seconds) {
  if (!seconds || seconds < 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return h ? `${h}h ${m}m` : `${m}m`;
}

/** How long ago, for a list: "just now", "4m ago", "3h ago", "2d ago".
 *
 * `at` is a server clock in seconds. Coarse on purpose -- the question a list
 * answers is "which of these was I just in", not "at what second".
 */
export function ago(at) {
  if (!at) return "";
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 45) return "just now";
  if (s < 90 * 60) return `${Math.max(1, Math.round(s / 60))}m ago`;
  if (s < 36 * 3600) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** How long something has been going: "12s", "4m", "1h 12m". */
export function forTime(seconds) {
  if (!seconds || seconds < 0) return "0s";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  return elapsed(seconds);
}

// Exponential smoothing, the same shape TensorBoard uses -- including the
// debias, which is the part that matters: without it a smoothed curve starts
// at zero and climbs out of it, so the first tenth of every run reads as a
// rise that did not happen.
export function smooth(points, weight = 0) {
  if (!weight || points.length < 2) return points;
  const w = Math.min(Math.max(weight, 0), 0.999);
  const out = [];
  let last = 0;
  let seen = 0;
  for (const [x, y] of points) {
    if (!Number.isFinite(y)) { out.push([x, y]); continue; }
    last = last * w + (1 - w) * y;
    seen += 1;
    out.push([x, last / (1 - Math.pow(w, seen))]);
  }
  return out;
}

// A line chart with an area fill and an emphasised endpoint. One renderer, used
// by every metric — nothing here knows a metric's name.
//
// `smoothing` draws the raw series faintly behind the smoothed one, because a
// smoothed curve on its own hides how noisy the thing being smoothed was, and
// that noise is usually the question.
/** A series, drawn small.
 *
 * `preserveAspectRatio="none"` is what lets the curve fill whatever width the
 * card happens to have, and it is also what distorts everything drawn in it:
 * on a very wide monitor a 420-unit viewBox is scaled several times
 * horizontally and once vertically, so a 1.5px line renders several pixels
 * thick and the end marker becomes an ellipse. Both are that one transform.
 *
 * `vector-effect="non-scaling-stroke"` takes every stroke out of the scaling,
 * so weight is in CSS pixels whatever the card's width. The marker is a
 * zero-length round-capped stroke rather than a `<circle>` for the same
 * reason -- a radius scales, a stroke does not.
 *
 * A glyph has no such opt-out, so with `axes` the numbers are not drawn in the
 * viewBox at all: they are HTML in gutters beside the plot, positioned by
 * percentage. That is the only way a label keeps its own proportions on a
 * chart whose width is whatever the card gives it.
 */
export function sparkline(points, { width = 420, height = 96, color = "var(--accent)",
                                    pad = 6, smoothing = 0, axes = false } = {}) {
  if (!points || points.length < 2) {
    return el("div", { class: "empty", text: "not enough points yet" });
  }
  const raw = points;
  points = smooth(points, smoothing);
  const xs = points.map(p => p[0]);
  const ys = raw.map(p => p[1]).concat(points.map(p => p[1]));
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const span = (hi - lo) || Math.abs(hi) || 1;
  lo -= span * 0.16; hi += span * 0.16;

  const X = v => pad + ((v - x0) / ((x1 - x0) || 1)) * (width - pad * 2);
  const Y = v => height - pad - ((v - lo) / ((hi - lo) || 1)) * (height - pad * 2);

  const path = (series) => series
      .map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`)
      .join(" ");
  const line = path(points);
  const area = `${line} L${X(x1).toFixed(1)} ${height} L${X(x0).toFixed(1)} ${height} Z`;
  const id = `g${Math.abs(hashCode(String(points.length) + color))}`;
  const last = points[points.length - 1];
  const ghost = smoothing ? path(raw) : "";

  // Ticks land on round numbers (1, 2 or 5 x a power of ten) rather than on
  // four equal slices of a padded range, because `0.163 0.288 0.413` is three
  // numbers nobody reads and `0.2 0.3 0.4` is a scale. The gridlines are drawn
  // at the ticks, so a line and its label mean the same thing.
  const gy = axes ? niceTicks(lo, hi, clampCount(height / 28, 3, 6)) : null;
  const gx = axes ? niceTicks(x0, x1, clampCount(width / 150, 2, 6)) : null;
  const grid = axes
    ? gy.ticks.map(v => `<path d="M0 ${Y(v).toFixed(1)} H${width}"/>`).join("")
      + gx.ticks.map(v => `<path d="M${X(v).toFixed(1)} 0 V${height}"/>`).join("")
    : [.25, .5, .75].map(f => `<path d="M0 ${(height * f).toFixed(0)} H${width}"/>`).join("");

  const svg =
    `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="height:${height}px">
       <defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1">
         <stop offset="0%" stop-color="${color}" stop-opacity=".26"/>
         <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
       </linearGradient></defs>
       <g stroke="var(--line-soft)" stroke-width="1" fill="none"
          vector-effect="non-scaling-stroke">${grid}</g>
       <path d="${area}" fill="url(#${id})"/>
       ${ghost ? `<path d="${ghost}" fill="none" stroke="${color}" stroke-width="1"
                        stroke-opacity=".28" stroke-linejoin="round"
                        vector-effect="non-scaling-stroke"/>` : ""}
       <path d="${line}" fill="none" stroke="${color}" stroke-width="1.5" stroke-linejoin="round"
             vector-effect="non-scaling-stroke"/>
       <path d="M${X(last[0]).toFixed(1)} ${Y(last[1]).toFixed(1)}l0 0" stroke="${color}"
             stroke-width="5.5" stroke-linecap="round" vector-effect="non-scaling-stroke"/>
     </svg>`;

  if (!axes) return el("div", { class: "chart", html: svg });

  // The labels are HTML in gutters beside the plot, not `<text>` inside it.
  // Anything drawn in the viewBox is stretched by the same transform that lets
  // the curve fill the card, and on a wide monitor that pulls every digit
  // sideways. A stroke can
  // opt out with `non-scaling-stroke`; a glyph cannot, so it moves outside.
  const yfmt = tickLabel(gy.step, Math.max(Math.abs(lo), Math.abs(hi)));
  const xfmt = tickLabel(gx.step, Math.max(Math.abs(x0), Math.abs(x1)));
  const yLabels = gy.ticks.map(v =>
    `<span style="top:${pct(Y(v) / height)}%">${yfmt(v)}</span>`).join("");
  const xLabels = gx.ticks.map(v => {
    const at = X(v) / width;
    if (at < -0.005 || at > 1.005) return "";
    // A label centred on the first or last tick hangs off the plot; pin the
    // two edges instead of letting them clip.
    const place = at <= 0.02 ? "left:0;transform:none"
                : at >= 0.98 ? "right:0;left:auto;transform:none"
                : `left:${pct(at)}%`;
    return `<span style="${place}">${xfmt(v)}</span>`;
  }).join("");

  return el("div", { class: "chart chart-axed", html:
    `<div class="ch-plot">${svg}<div class="ch-y">${yLabels}</div></div>
     <div class="ch-x">${xLabels}</div>` });
}

const pct = (f) => (f * 100).toFixed(2);
const clampCount = (n, lo, hi) => Math.max(lo, Math.min(hi, Math.round(n)));

/** Tick values on round numbers across `[lo, hi]`.
 *
 * The step is 1, 2 or 5 times a power of ten -- the spacing a person would
 * choose -- so the labels are short and the reader can add one tick to the
 * next in their head. */
function niceTicks(lo, hi, count = 4) {
  const span = hi - lo;
  if (!Number.isFinite(span) || span <= 0) return { ticks: [lo], step: 1 };
  const rough = span / count;
  const mag = Math.pow(10, Math.floor(Math.log10(rough)));
  const n = rough / mag;
  const step = (n >= 7 ? 10 : n >= 3 ? 5 : n >= 1.5 ? 2 : 1) * mag;
  const ticks = [];
  for (let i = Math.ceil(lo / step - 1e-9);
       i * step <= hi + step * 1e-9 && ticks.length < 14; i++) {
    ticks.push(+(i * step).toPrecision(12));
  }
  return { ticks: ticks.length ? ticks : [lo], step };
}

/** How to print a tick, given how far apart the ticks are.
 *
 * The step decides the decimals, so every label on one axis has the same
 * width -- `0.20 0.25 0.30`, never `0.2 0.25 0.3`. Iterations get thousands
 * separators; a weight of 2.5e-7 gets an exponent rather than `0.000`. */
function tickLabel(step, magnitude) {
  if (magnitude >= 1e6 || (step > 0 && step < 1e-4)) {
    return v => Math.abs(v) < step / 2 ? "0" : v.toExponential(1).replace("e+", "e");
  }
  const digits = Math.max(0, Math.min(6, -Math.floor(Math.log10(step))));
  return v => (Math.abs(v) < step / 2 ? 0 : v)
    .toLocaleString("en-US", { minimumFractionDigits: digits,
                               maximumFractionDigits: digits });
}

function hashCode(s) {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
  return h;
}
