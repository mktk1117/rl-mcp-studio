// Docs — the markdown an agent wrote, rendered where the run is.
//
// The other views orbit what a run *measured*. This one shows what was
// *written* about it: `PLAN.md` before, `REPORT.md` after, and whatever else
// an agent — the one in this app's right-hand column, or a Claude Code sitting
// in the task repository — drops beside them. Without it the only way to read
// one is to leave the studio, which is the same failure the code pane is
// built against.
//
// Two rules shape the renderer below.
//
// **Nodes, never markup.** The text comes off disk and an agent wrote it, so
// `innerHTML` would run whatever a document happened to contain, on this
// origin, with the studio's own unauthenticated API in reach. Everything here
// builds elements and text nodes, which cannot execute.
//
// **A table is the point.** A run report is usually a results table with a
// sentence under it, so pipe tables are rendered as tables rather
// than dropped into a paragraph — a report whose numbers arrive as one long
// line of pipes is a report nobody reads twice.

import { el, add, chip, on } from "./ui.js";
import { set, loadOnce } from "./state.js";
import { api } from "./api.js";
import { placed, sideVar } from "./resize.js";

/** Re-read the listing, discarding the stamp so the next paint fetches.
 *
 * Called when a `.md` under a watched root changes. The *open* document is
 * re-read too: an agent rewriting the report you are looking at is the common
 * case, not the rare one. */
export function refreshDocs() {
  set({ docsStamp: null, docStamp: null });
}

/** Re-read the open document, and nothing else.
 *
 * The buttons below exist because the event that calls `refreshDocs` does not
 * fire for every document this view shows. The watcher's roots are the records
 * and the sessions; the *task repository* is not among them, and it is where an
 * agent's `Write NOTES.md` lands — so a note written there would appear only
 * after a reload of the whole app.
 *
 * Whether the task repo should be watched is a separate question with a cost
 * (it is a source tree, and every `.py` write would wake the watcher). A button
 * has no cost and works for the other case too: a document changed by anything
 * this app cannot see — an editor, a terminal, a Claude Code in another window.
 */
export function reloadDoc() {
  set({ docStamp: null });
}

/** Look again for documents: ones that have appeared, and ones that have gone.
 *
 * Separate from `reloadDoc` because they answer different questions. This one
 * walks three directories; that one reads one file. Pressing the wrong one and
 * getting the other's work done is worse than two buttons.
 */
export function rescanDocs() {
  refreshDocs();
}

/** What the listing is keyed on: a different run, or a different task, is a
 *  different set of documents. */
function stampOf(s) {
  return `${s.runId || ""}::${s.task || ""}`;
}

export function docsView(s, actions) {
  loadOnce("docs", stampOf(s), () => api.docs(s.runId));
  const payload = s.docs;
  if (!payload) return el("div", { class: "empty", text: "looking for documents…" });
  if (payload.error) {
    return el("div", { class: "notyet" },
      el("b", { text: "The documents could not be read" }), payload.error);
  }
  const groups = payload.groups || [];
  return el("div", { class: "docsview" },
    el("div", { class: "docs", style: { "--side": sideVar("docsNav", s.widths, { cap: "40%" }) } },
      docList(s, groups, payload), docPane(s, actions),
      placed("docsNav", { side: "left", widths: s.widths, actions, cap: "40%" })));
}

// ── the list ────────────────────────────────────────────────────────────

function docList(s, groups, payload) {
  const list = el("div", { class: "doclist" }, listHead(s, payload));
  if (!payload.count) {
    add(list, whereToWrite(payload));
    return list;
  }
  for (const group of groups) {
    add(list,
      el("div", { class: "doc-group" },
        el("span", { text: group.label }),
        el("small", { text: group.blurb })),
      ...group.docs.map(doc => docRow(s, doc)),
      group.docs.length
        ? null
        : el("div", { class: "doc-none", text: "nothing here yet" }));
  }
  add(list, whereToWrite(payload));
  return list;
}

/** How many documents there are, and the verb that looks for more.
 *
 * Above the groups rather than in the pane, because "is there a new document"
 * is a question about the list and answering it in the reader's header would
 * put two different refreshes a centimetre apart.
 */
function listHead(s, payload) {
  const count = payload.count || 0;
  return el("div", { class: "doc-head" },
    el("span", { class: "doc-count",
                 text: count ? `${count} document${count === 1 ? "" : "s"}`
                             : "no documents" }),
    el("span", { class: "sp" }),
    el("button", {
      class: "act",
      title: "look again for documents — one written by an agent in the task "
           + "repository does not reach this app on its own",
      onclick: () => rescanDocs(),
    }, "Rescan"));
}

function docRow(s, doc) {
  const chosen = s.docPath === doc.path;
  return el("button", {
    class: `doc-row${chosen ? " on" : ""}`,
    key: doc.path,
    title: doc.path,
    "aria-selected": chosen ? "true" : "false",
    onclick: () => set({ docPath: doc.path, docRaw: false }),
  },
    el("span", { class: "doc-name", text: doc.name }),
    doc.title ? el("small", { class: "doc-title", text: doc.title }) : null,
    el("small", { class: "doc-when", text: written(doc.at) }));
}

/** The one thing an agent needs to be told, said in the place you would ask it.
 *
 * A directory, not a convention: "write it in the record" is not an
 * instruction anything can follow, and a path is. */
function whereToWrite(payload) {
  const where = payload.write_to || "";
  if (!where) return null;
  return el("div", { class: "doc-hint" },
    el("b", { text: "Where these come from" }),
    el("span", {}, "Any ", el("code", { text: ".md" }),
      " written into the record's folder, the run's ",
      el("code", { text: "artifacts/" }),
      " directory, or the task repository shows up here. Ask an agent to write "
      + "one to:"),
    el("code", { class: "doc-where", text: where }));
}

/** How long ago it was written, in the unit that fits.
 *
 * Exported because the Overview tab asks the same question about the same kind
 * of file, and two answers to "how old is this document" that round differently
 * is the sort of disagreement nobody can debug from a screenshot.
 *
 * `elapsed` from ui.js is for a run's wall clock and says "51h 20m"; a
 * document is usually days old and "3 days ago" is the answer to the question
 * being asked, which is whether this is the write-up of the run on screen. */
export function written(at) {
  if (!at) return "";
  const age = Date.now() / 1000 - at;
  if (age < 90) return "just now";
  if (age < 3600) return `${Math.round(age / 60)} min ago`;
  if (age < 86400) return `${Math.round(age / 3600)} h ago`;
  const days = Math.round(age / 86400);
  return days === 1 ? "yesterday" : `${days} days ago`;
}

// ── the document ────────────────────────────────────────────────────────

function docPane(s, actions) {
  const path = s.docPath;
  if (!path) {
    return el("div", { class: "docpane" },
      el("div", { class: "empty", text: "pick a document" }));
  }
  loadOnce("doc", `${path}::${s.docsStamp}`, () => api.doc(path));
  const doc = s.doc;
  const pane = el("div", { class: "docpane" });
  if (!doc) { add(pane, el("div", { class: "empty", text: "opening…" })); return pane; }
  if (!doc.ok) {
    // A document can go while you are reading it -- an agent renames its
    // report, or you delete the file. `/api/doc → 404` is the truth and not
    // an answer; what happened is.
    add(pane, el("div", { class: "notyet" },
      el("b", { text: "That document is not there any more" }),
      "It was renamed, deleted, or moved out of the folders this view reads. "
      + "The list beside it is current.",
      el("small", { class: "muted", text: doc.reason || doc.error || "" })));
    return pane;
  }

  add(pane,
    el("div", { class: "docpane-h" },
      el("span", { class: "fp", text: doc.name }),
      doc.truncated ? chip("truncated", true) : null,
      // The document's own age, and it is here because of the reload button
      // beside it. Nothing on screen changes when you re-read a file that has
      // not changed -- the pane deliberately keeps what it has rather than
      // blinking -- so without this the button looks broken every time it
      // works. This is the thing that moves.
      el("small", { class: "doc-age", title: doc.path, text: written(doc.at) }),
      el("span", { style: { marginLeft: "auto" } }),
      // A glyph, not the word. This header already carries the file name, a
      // truncation chip, an age and two other verbs, and in the narrow pane
      // the focus layout gives it that is more than fits -- the row wraps and
      // the reader loses a line to chrome. The label moves to `aria-label`,
      // which is where a screen reader was reading it from anyway.
      el("button", {
        class: "act icon", "aria-label": "reload this document",
        title: "read this file again from disk",
        onclick: () => reloadDoc(),
      }, el("span", { class: "glyph", text: "\u21BB" })),
      el("button", {
        class: "act", title: "send this document to the agent",
        onclick: () => actions.askAboutDoc(doc.path),
      }, "Ask"),
      el("button", {
        class: s.docRaw ? "act on" : "act",
        onclick: () => set({ docRaw: !s.docRaw }),
      }, s.docRaw ? "Source" : "Rendered")),
    s.docRaw
      ? el("pre", { class: "doc-source", text: doc.text })
      // Two nodes, not one, and the reason is the scrollbar: a single element
      // that is both the scroller and the 78ch reading column puts its bar at
      // the right edge of the *text*, floating in the middle of a wide pane.
      // The scroller is full width; the column is centred inside it.
      : el("div", { class: "md" },
          el("article", { class: "md-body prose" },
             ...renderMarkdown(doc.text, doc.path, { players: docPlayers }))));
  return pane;
}

/** The open document's clips, kept across paints -- see `video`. */
const docPlayers = new Map();

// ── markdown ────────────────────────────────────────────────────────────
//
// A small renderer, not a complete one. It covers what these documents
// actually contain: headings, paragraphs, fenced code, pipe tables, lists
// (including the `- [ ]` an agent writes for a plan), quotes, rules, and the
// inline run of code, emphasis, links and images. Anything it does not know
// falls through as text, which is the only failure mode worth having — a
// construct nobody wrote is a blank screen, and a construct shown verbatim is
// a document you can still read.

const FENCE = /^\s{0,3}(```+|~~~+)\s*(\S*)/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const BULLET = /^(\s*)([-*+])\s+(.*)$/;
const NUMBER = /^(\s*)(\d{1,9})[.)]\s+(.*)$/;
const TASK = /^\[([ xX])\]\s+(.*)$/;

/** A document, as an array of block elements.
 *
 * `players` is for a view that repaints while you watch: a Map it owns, from
 * which every `<video>` in the document is reused rather than built again. See
 * `video` below for why. Leave it out for a render that is not on screen --
 * the report export, the agent transcript -- or the export would take the
 * player out of the pane you are looking at. */
export function renderMarkdown(text, path = "", { players: keep = null } = {}) {
  if (!keep) return blocksOf(text, path);
  players = keep;
  used = new Set();
  try {
    return blocksOf(text, path);
  } finally {
    // A clip the document no longer shows is let go, so opening twenty
    // documents in a row does not hold twenty documents' worth of players.
    for (const key of [...keep.keys()]) if (!used.has(key)) keep.delete(key);
    players = used = null;
  }
}

function blocksOf(text, path) {
  const lines = String(text || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;

  // YAML front matter. Without this the opening `---` renders as a horizontal
  // rule and the metadata under it as a paragraph of run-together words --
  // which is how agent memory files and many agent-written notes start.
  // Shown, folded, rather than dropped: it is what the file says.
  if (lines[0] !== undefined && lines[0].trim() === "---") {
    let end = 1;
    while (end < lines.length && lines[end].trim() !== "---") end += 1;
    if (end < lines.length) {
      blocks.push(el("details", { class: "md-front" },
        el("summary", { text: "front matter" }),
        el("pre", { text: lines.slice(1, end).join("\n") })));
      i = end + 1;
    }
  }

  while (i < lines.length) {
    const line = lines[i];

    const fence = line.match(FENCE);
    if (fence) {
      const closer = fence[1][0];
      const body = [];
      i += 1;
      while (i < lines.length && !(lines[i].trimStart().startsWith(closer.repeat(3))
                                   && lines[i].trim().replace(/[^`~]/g, "") !== "")) {
        body.push(lines[i]);
        i += 1;
      }
      i += 1;                                   // the closing fence itself
      blocks.push(el("pre", { class: "md-code" },
        el("code", { class: fence[2] ? `lang-${fence[2]}` : null,
                     text: body.join("\n") })));
      continue;
    }

    if (!line.trim()) { i += 1; continue; }

    const heading = line.match(HEADING);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      blocks.push(el(`h${level}`, { class: "md-h" }, ...inline(heading[2], path)));
      i += 1;
      continue;
    }

    if (RULE.test(line)) { blocks.push(el("hr", { class: "md-hr" })); i += 1; continue; }

    // A table is two lines before it is a table: the header, and the dashed
    // rule under it. Testing the *next* line is what stops a paragraph that
    // happens to contain a pipe becoming a one-column table.
    if (line.includes("|") && i + 1 < lines.length && isDivider(lines[i + 1])) {
      const rows = [];
      const align = alignments(lines[i + 1]);
      rows.push(cells(line));
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(cells(lines[i]));
        i += 1;
      }
      blocks.push(table(rows, align, path));
      continue;
    }

    if (QUOTE.test(line)) {
      const body = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        body.push(lines[i].match(QUOTE)[1]);
        i += 1;
      }
      blocks.push(el("blockquote", { class: "md-quote" },
        ...blocksOf(body.join("\n"), path)));
      continue;
    }

    if (BULLET.test(line) || NUMBER.test(line)) {
      const [list, next] = readList(lines, i, path);
      blocks.push(list);
      i = next;
      continue;
    }

    // A paragraph runs until a blank line or until something else starts.
    const body = [];
    while (i < lines.length && lines[i].trim() && !HEADING.test(lines[i])
           && !FENCE.test(lines[i]) && !RULE.test(lines[i]) && !QUOTE.test(lines[i])
           && !BULLET.test(lines[i]) && !NUMBER.test(lines[i])) {
      body.push(lines[i].trim());
      i += 1;
    }
    blocks.push(el("p", { class: "md-p" }, ...inline(body.join(" "), path)));
  }
  return blocks;
}

/** One list, with the nested ones under it. Returns the node and where to
 *  carry on from. */
function readList(lines, start, path) {
  const first = lines[start].match(BULLET) || lines[start].match(NUMBER);
  const ordered = !lines[start].match(BULLET);
  const indent = first[1].length;
  const node = el(ordered ? "ol" : "ul", { class: "md-list" });
  let i = start;

  while (i < lines.length) {
    const match = lines[i].match(BULLET) || lines[i].match(NUMBER);
    if (!match || match[1].length < indent) break;
    if (match[1].length > indent) {
      // Deeper: it belongs to the item just added, not to this list.
      const [nested, next] = readList(lines, i, path);
      const last = node.lastElementChild;
      if (last) { last.append(nested); } else { node.append(nested); }
      i = next;
    } else {
      const item = el("li", { class: "md-item" });
      const task = match[3].match(TASK);
      if (task) {
        const done = task[1].toLowerCase() === "x";
        item.className = `md-item task${done ? " done" : ""}`;
        add(item, el("span", { class: "md-box", text: done ? "☑" : "☐" }),
            ...inline(task[2], path));
      } else {
        add(item, ...inline(match[3], path));
      }
      node.append(item);
      i += 1;
    }
  }
  return [node, i];
}

function isDivider(line) {
  return /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(line)
      && line.includes("-");
}

/** What the divider row asked for, with "" meaning the author did not say. */
function alignments(line) {
  return cells(line).map(cell => {
    const left = cell.startsWith(":");
    const right = cell.endsWith(":");
    if (left && right) return "center";
    if (right) return "right";
    if (left) return "left";
    return "";
  });
}

// A number, as a results table writes one: `3.3`, `-0.15`, `3.3%`, `4.5x`,
// `1,024`, `2.5e-7`, `0.02 m/s`, `—`. Deliberately generous about the unit and
// strict about the front, because the question is "does this column hold
// measurements" and a cell starting with a word does not.
const NUMERIC = /^[+\-–—]?[\d.,]+(e[+\-]?\d+)?\s*[%a-zA-Z/²³°]*$/;

/** Columns the author left unaligned, but which hold nothing but numbers.
 *
 * Markdown makes you write `---:` to align a column right, and nobody writing
 * a run report at two in the morning does. The result is a results table whose
 * decimal points do not line up, which is the one thing a results table is
 * for. An explicit alignment always wins; this only fills the silence. */
function numericColumns(rows, align) {
  const body = rows.slice(1);
  return align.map((given, n) => {
    if (given) return given;
    const seen = body.map(row => (row[n] || "").trim()).filter(cell => cell && cell !== "—");
    if (!seen.length) return "left";
    return seen.every(cell => NUMERIC.test(cell)) ? "right" : "left";
  });
}

function cells(line) {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  return row.split(/(?<!\\)\|/).map(cell => cell.trim().replace(/\\\|/g, "|"));
}

/** A table, wrapped in something that can scroll.
 *
 * The wrapper is not decoration: a nine-column results table in a 300 px pane
 * either scrolls inside its own box or it makes the whole page scroll
 * sideways. */
function table(rows, given, path) {
  const head = rows[0] || [];
  const align = numericColumns(rows, given);
  return el("div", { class: "md-tablewrap" },
    el("table", { class: "md-table" },
      el("thead", {}, el("tr", {}, ...head.map((cell, n) =>
        el("th", { style: { textAlign: align[n] || "left" } }, ...inline(cell, path))))),
      el("tbody", {}, ...rows.slice(1).map(row =>
        el("tr", {}, ...row.map((cell, n) =>
          el("td", { style: { textAlign: align[n] || "left" } },
             ...inline(cell, path))))))));
}

// Inline spans, longest-delimiter first so `**bold**` is not read as two
// emphases. Code wins over everything, which is markdown's own rule: the
// asterisks inside `a*b` are literal.
const INLINE = [
  { re: /`([^`]+)`/, node: (m) => el("code", { class: "md-tick", text: m[1] }) },
  { re: /!\[([^\]]*)\]\(([^)\s]+)[^)]*\)/, node: (m, path) => image(m[1], m[2], path) },
  { re: /\[([^\]]+)\]\(([^)\s]+)[^)]*\)/, node: (m, path) => link(m[1], m[2], path) },
  { re: /\*\*([^*]+)\*\*/, node: (m, path) => el("b", {}, ...inline(m[1], path)) },
  { re: /__([^_]+)__/, node: (m, path) => el("b", {}, ...inline(m[1], path)) },
  { re: /~~([^~]+)~~/, node: (m, path) => el("s", {}, ...inline(m[1], path)) },
  { re: /\*([^*]+)\*/, node: (m, path) => el("i", {}, ...inline(m[1], path)) },
  { re: /(?<![\w\\])_([^_]+)_(?!\w)/, node: (m, path) => el("i", {}, ...inline(m[1], path)) },
];

function inline(text, path = "") {
  const source = String(text || "");
  let earliest = null;
  for (const rule of INLINE) {
    const match = source.match(rule.re);
    if (match && (earliest === null || match.index < earliest.match.index)) {
      earliest = { match, rule };
    }
  }
  if (!earliest) return source ? [document.createTextNode(source)] : [];
  const { match, rule } = earliest;
  return [
    ...(match.index ? [document.createTextNode(source.slice(0, match.index))] : []),
    rule.node(match, path),
    ...inline(source.slice(match.index + match[0].length), path),
  ];
}

/** A link. Remote ones open in a new tab; anything else is text.
 *
 * A relative link points at a local file, and following it from the studio
 * would navigate the whole app out of itself — so it is shown as what it is
 * rather than dressed up as something clickable that breaks. */
function link(label, href, path) {
  if (/^https?:\/\//i.test(href)) {
    return el("a", { class: "md-link", href, target: "_blank",
                     rel: "noopener noreferrer", text: label });
  }
  const local = beside(href, path);
  if (local && /\.(md|markdown)$/i.test(local)) {
    // Another document, so it opens in this pane. Through `/api/artifact` it
    // would *download*, which is what that route does with anything it will
    // not render -- a link that saves a file when it looks like a link.
    return el("a", { class: "md-link", href: "#", title: local, text: label,
                     onclick: (e) => { e.preventDefault();
                                       set({ docPath: local, docRaw: false }); } });
  }
  if (local) {
    return el("a", { class: "md-link", href: api.artifactUrl(local),
                     target: "_blank", rel: "noopener noreferrer", text: label });
  }
  return el("span", { class: "md-deadlink", title: href, text: label });
}

/** An image beside the document — a plot in the record folder, usually.
 *
 * Remote sources are deliberately not loaded: this page is the studio, and a
 * document that fetched an image would be a document that phones out from it.
 */
function image(alt, src, path) {
  const local = beside(src, path);
  if (!local) {
    return el("span", { class: "md-deadlink", title: src, text: alt || src });
  }
  // `![a clip](media/hold.mp4)` is how an agent writes a video into a document,
  // because markdown has no other syntax for one -- and drawn as an `<img>` it
  // is a broken picture. Muted and loopable rather than autoplaying: these are
  // ten-second clips of a robot, and a page that starts moving on its own is a
  // page you scroll past.
  if (/\.(mp4|webm)$/i.test(local)) return video(api.artifactUrl(local));
  return el("img",{ class: "md-img", src: api.artifactUrl(local),
                     alt: alt || "", loading: "lazy" });
}

// The Map the render in progress reuses players from, and the keys it has
// used. Module state rather than an argument threaded through every inline
// rule, and safe as that because a render is synchronous: set on the way into
// `renderMarkdown`, cleared on the way out.
let players = null;
let used = null;

/** A clip, as the same `<video>` node on every paint.
 *
 * A player is state the browser keeps on the node -- the frame it is on,
 * whether it is playing, the volume you set -- and the reconciler keeps a node
 * only while it can match it by position. The document's text changing above
 * the clip, the pane showing "opening…" for a frame while the file is
 * re-read, a switch of layout: each built a new `<video>`, and a new one starts
 * from zero. A document with a dozen sim-to-sim clips was a page you could not
 * watch a clip on.
 *
 * So the node is made once and handed back. Moving a playing `<video>` within
 * one task does not pause it; a longer absence does (the browser pauses a
 * player taken out of the page), so one that was playing when it was last on
 * screen is started again where it stopped. Keyed by source *and* occurrence,
 * because a document can show the same clip twice, and given the key as an
 * attribute so the reconciler never morphs a different clip into its place.
 */
function video(src) {
  const fresh = () => el("video", { class: "md-video", src, controls: true, loop: true,
                                    muted: true, playsinline: true, preload: "metadata" });
  if (!players) return fresh();
  let n = 0;
  while (used.has(`${n}|${src}`)) n += 1;
  const key = `${n}|${src}`;
  used.add(key);
  let node = players.get(key);
  if (!node) {
    node = fresh();
    node.setAttribute("key", key);
    // A pause while the node is out of the page is the browser's, not yours.
    on(node, "play", (e) => { e.currentTarget.__playing = true; });
    on(node, "pause", (e) => {
      if (e.currentTarget.isConnected) e.currentTarget.__playing = false;
    });
    players.set(key, node);
  } else if (!node.isConnected && node.__playing) {
    setTimeout(() => { if (node.isConnected && node.paused) node.play().catch(() => {}); }, 0);
  }
  return node;
}

/** Resolve a relative reference against the document's own directory. */
function beside(reference, path) {
  if (!path || /^[a-z]+:/i.test(reference) || reference.startsWith("#")) return "";
  if (reference.startsWith("/")) return reference;
  const directory = path.slice(0, path.lastIndexOf("/"));
  return directory ? `${directory}/${reference}` : "";
}
