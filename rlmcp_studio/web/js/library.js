// Library — a task as a thing with a history, and the shelf it stands on.
//
// Every other view is about a *run*. This one is about the task: what it is,
// which package registers it, how its runs ended, what was learned, what
// wasted time, and what to read before touching it.
//
// Two panes, for two readers. The rail is the index — every
// task the studio can see, with the counts that say whether its card is worth
// opening — and it answers "has anyone done this on this robot". The card is
// what an agent should read before its first run on a task like this one.
//
// Everything on the card cites a run, because that is the whole difference
// between a library and a pile of opinions. Nothing here is generated prose:
// the published card is written by rl-mcp's own skill and promoted by
// a person, and until one exists this view shows only what the records, the
// registry and the notes already say — and says that is what it is doing.

import { el, add, chip, dot, on, scrollListTo } from "./ui.js";
import { state, set, revalidate } from "./state.js";
import { api } from "./api.js";
import { placed, sideVar } from "./resize.js";
import { reportControl } from "./report.js";

/** What the card is keyed on.
 *
 * The task, and how many runs of it the app knows about — so a run recorded
 * while you are reading brings the card with it. There is no event for "a
 * record changed" that this view could listen to without the run list also
 * changing, and the run list is refreshed by the live feed already. */
function stampOf(s) {
  return `${s.task || ""}::${(s.runs || []).length}::${s.libNonce || 0}`;
}

/** Re-read both halves, without taking them off the screen first.
 *
 * A counter rather than a clear: it moves both stamps, `revalidate` asks
 * again, and what is already drawn stays drawn until the answer lands. The
 * rail's counts come from the same records, so both halves go together.
 */
export function refreshLibrary() {
  set({ libNonce: (state.libNonce || 0) + 1 });
}

export function libraryView(s, actions) {
  revalidate("libIndex", `index::${(s.tasks || []).length}::${s.libNonce || 0}`,
             () => api.library());
  // Tagged with the task it was asked for, success or failure alike. Keeping
  // the previous answer on screen means there is now a moment where `card`
  // belongs to the task you just left, and a card headed with another task's
  // name is worse than the word "reading".
  const task = s.task;
  revalidate("card", stampOf(s), () =>
    api.card(task).then(c => ({ ...c, task: c.task || task }),
                        err => ({ error: err.message, task })));
  return el("div", { class: "libview" },
    el("div", { class: "lib", style: { "--side": sideVar("libNav", s.widths, { cap: "40%" }) } },
      shelf(s, actions), cardPane(s, actions),
      placed("libNav", { side: "left", widths: s.widths, actions, cap: "40%" })));
}

// ── the shelf ───────────────────────────────────────────────────────────

/** One term of a query, aligned onto `text` as tightly as it will go.
 *
 * Returns `null` when the term is not a subsequence of the text at all, and
 * otherwise the score and the indices it matched.
 *
 * Two passes, which is fzf's own trick and the difference between a ranking
 * that is useful and one that is noise. Greedy forward matching takes the
 * *first* character that will do: for `g1` against
 * `Mjlab-Velocity-Rough-Unitree-G1` that is the `g` in the middle of "Rough",
 * scored as a letter buried in a word, which would rank the exact task below
 * every other name ending the same way. So the forward pass is used only to
 * find where a match can end, and the real alignment is read backwards from
 * there -- which lands on the `G` that starts `G1`, where anybody typing `g1`
 * was pointing.
 *
 * The scoring is fzf's shape, not its arithmetic: a character that starts a
 * word counts for much more than one inside one, a run of adjacent characters
 * counts for more than the same characters scattered, and a gap between two
 * matched characters costs a little.
 */
function alignOne(needle, text) {
  const hay = text.toLowerCase();

  let at = 0;
  for (const want of needle) {
    const found = hay.indexOf(want, at);
    if (found < 0) return null;
    at = found + 1;
  }

  // Backwards from the earliest end the forward pass found.
  const hits = [];
  let cursor = at - 1;
  for (let k = needle.length - 1; k >= 0; k--) {
    const found = hay.lastIndexOf(needle[k], cursor);
    hits.unshift(found);
    cursor = found - 1;
  }

  let score = 0;
  let previous = -1;
  for (const found of hits) {
    // A boundary is where a word starts: the front of the string, just after a
    // separator, or a capital inside a word. Task ids look like
    // `Example-OpenLid-v0`, so both spellings occur in one name.
    const before = found ? text[found - 1] : "";
    const boundary = !found || /[-_. /]/.test(before)
                     || (before === before.toLowerCase() && text[found] !== hay[found]);
    if (boundary) score += 8;
    if (found === previous + 1) score += 6;
    else if (previous >= 0) score -= Math.min(found - previous - 1, 6);
    previous = found;
  }
  // Where the match starts is a tie-break and not a penalty: a name that
  // carries the answer late is still the answer.
  return { score: score - hits[0] / 40, hits };
}

/** Match `query` against `text` the way fzf does: as a subsequence, and as
 *  space-separated terms that must all match.
 *
 * Not `includes`. Task names are often four or five hyphenated words each
 * -- `Mjlab-Velocity-Rough-Unitree-G1` -- and the thing you remember about
 * one is rarely a contiguous run of it. `vrg1` should find
 * `Mjlab-Velocity-Rough-Unitree-G1`, and `open lid` should find
 * `Example-Open-Lid-v0`; a substring filter can do neither.
 *
 * Returns `null` when the query does not match, and otherwise the score and
 * every index that matched, so a row can show *why* it is in the list.
 */
export function fuzzy(query, text) {
  const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return { score: 0, hits: [] };
  let score = 0;
  const hits = [];
  for (const term of terms) {
    const aligned = alignOne(term, text);
    if (!aligned) return null;
    score += aligned.score;
    hits.push(...aligned.hits);
  }
  // Shorter names are better answers to the same query:
  // `Mjlab-Velocity-Flat-Unitree-G1` over
  // `Mjlab-Velocity-Flat-Unitree-G1-Play` when both match.
  return { score: score - text.length / 20, hits };
}

/** The rows this query is asking for, best first.
 *
 * The name is what people type at, so a name match wins outright. The package
 * and the backend are the second question -- "what else is in `my_tasks`" --
 * and match at a discount rather than not at all, because the alternative is a
 * filter that answers "nothing matches" about a row the reader can see.
 */
function narrow(rows, query) {
  if (!query) return rows.map(row => ({ row, hits: [] }));
  const out = [];
  for (const row of rows) {
    const name = fuzzy(query, row.task || "");
    if (name) { out.push({ row, hits: name.hits, score: name.score }); continue; }
    const other = fuzzy(query, `${row.package || ""} ${row.backend || ""}`);
    if (other) out.push({ row, hits: [], score: other.score - 40 });
  }
  // Stable within a score, so equally-good matches keep the shelf's own order
  // -- which is the last-worked-on order, and the right tie-break.
  return out.sort((a, b) => b.score - a.score);
}

/** Which row the keyboard is on, against the list that is on the shelf now.
 *
 * `-1` means nobody has pressed an arrow, and it is the honest default: the
 * shelf already marks the task you have open, and a second mark that appeared
 * on its own would leave two rows claiming to be the answer. Clamped rather
 * than trusted, because the list it indexes into shrinks as you type -- the
 * pick was on row nine and the query narrowed the list to four.
 */
function pickOf(s, shown) {
  const picked = Number.isInteger(s.libPicked) ? s.libPicked : -1;
  if (picked < 0 || !shown.length) return -1;
  return Math.min(picked, shown.length - 1);
}

function shelf(s, actions) {
  const rail = el("div", { class: "liblist" });
  const payload = s.libIndex;
  if (!payload) return add(rail, el("div", { class: "empty", text: "reading the library…" }));
  if (payload.error) {
    return add(rail, el("div", { class: "notyet" },
      el("b", { text: "The library could not be listed" }), payload.error));
  }
  const rows = payload.tasks || [];
  const query = (s.libFilter || "").trim();
  const shown = narrow(rows, query);
  const picked = pickOf(s, shown);
  add(rail, el("div", { class: "doc-group" },
    el("span", { text: query ? `tasks · ${shown.length} of ${rows.length}`
                             : `tasks · ${rows.length}` }),
    el("small", { text: query ? "best match first"
                              : "most recently worked on first" })));
  // The whole list, not the narrowed one: the box re-narrows from what is
  // *in* it, and the two disagree for as long as the debounce lasts.
  add(rail, filterBox(s, rows, actions));
  if (!shown.length) {
    add(rail, el("div", { class: "empty", text: `nothing matches “${query}”` }));
  }
  shown.forEach((hit, at) => add(rail, shelfRow(s, hit, at === picked, actions)));
  keepInView(shown, picked, s.task);
  return rail;
}

/** What the shelf was last scrolled to, so it is scrolled once and not on
 *  every repaint -- which would fight the scroll wheel with a live run. */
let inView = "";

/** Bring the row you are on back onto the shelf, when it is the app that
 *  moved and not you.
 *
 * A task is picked in four places -- this shelf, the switcher, Build, and a
 * citation on a card -- and in three of them the shelf is left marking a row
 * that may be twenty rows above the scrollport. The list can be dozens of
 * rows and the mark is the only thing that says which of them you have open,
 * so a selection you cannot see reads as no selection at all.
 *
 * `scrollListTo` rather than anything centring: a row already on screen must
 * not move, or every keystroke through a narrowed list would slide the whole
 * shelf under the reader -- and the shelf is the only thing allowed to scroll,
 * never the panel beside it. And only when the *target* changes, so scrolling
 * away from your own selection is allowed to stick.
 */
function keepInView(shown, picked, task) {
  const want = picked >= 0 ? (shown[picked] && shown[picked].row.task) : task;
  if (!want || want === inView) return;
  inView = want;
  // After the paint this render is part of: the node to scroll to does not
  // exist until `patch()` has put it there. A timeout rather than a frame --
  // a hidden tab is handed no frames at all, and coming back to one that had
  // quietly skipped every scroll would show a shelf marking a row that is not
  // on it.
  setTimeout(() => {
    scrollListTo(document.querySelector(`.liblist .lib-row[key="${CSS.escape(want)}"]`),
                 ".liblist");
  }, 0);
}

/** Typing narrows the shelf; the arrows walk it; Enter opens where you are;
 *  Escape clears it.
 *
 * Drawn always rather than past some length, unlike the run rail's: the run
 * rail is one task's records and is often four rows, and this is every task
 * the studio can see, which is never short.
 *
 * The arrows are what makes the box a way *through* the shelf rather than a
 * sieve with one exit. With Enter alone, the second-best match -- which for a
 * query like `g1` is most of them -- would only be reachable by letting go of
 * the keyboard and pointing at it.
 */
function filterBox(s, rows, actions) {
  const box = el("input", { class: "railfilter libfilter", type: "text",
                            placeholder: "filter tasks…", value: s.libFilter || "" });
  // `e.currentTarget`, never the node closed over: `patch()` keeps the input
  // that is already on screen and throws this copy away, so a handler reading
  // `box.value` reads a detached element nobody ever typed into.
  on(box, "input", (e) => {
    actions.typeLibFilter(e.currentTarget.value);
    // A new query is a new list, and a pick held over from the old one points
    // at whatever happens to be in that position now.
    set({ libPicked: -1 });
  });
  // The pick means "where Enter would take you", and Enter only takes you
  // anywhere while the box has the focus. Left behind, it is an accent outline
  // on a row that no key can reach -- a second mark on the shelf claiming to
  // be the answer. Clicking a row blurs the box first and the click still
  // lands, because `patch()` keeps the button rather than rebuilding it.
  on(box, "blur", () => set({ libPicked: -1 }));
  on(box, "keydown", (e) => {
    // Narrowed here, from the text that is in the box *now*.
    //
    // `typeLibFilter` holds the query out of the store for 80ms so the shelf
    // is not rebuilt on every keystroke, and Enter inside that window would
    // read a `shown` from before the last letters landed: typing a name fast
    // and pressing Enter would open the top of the *unfiltered* shelf -- the
    // most recently worked-on task, which is the one thing nobody typing a
    // name could have meant.
    const query = e.currentTarget.value.trim();
    const shown = narrow(rows, query);
    // From the store, not from the render that built this handler. Two arrows
    // inside one repaint would read the same stale index and compute the same
    // next row, so a held-down arrow key would walk half as far as pressed.
    const at = pickOf(state, shown);
    if (e.key === "Escape") {
      actions.typeLibFilter("");
      e.currentTarget.value = "";
      set({ libPicked: -1 });
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!shown.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      // Wrapping, the way every list you type into wraps. Off the bottom of
      // the shelf is the top of it, not a key that does nothing.
      const next = at < 0 ? (step > 0 ? 0 : shown.length - 1)
                          : (at + step + shown.length) % shown.length;
      set({ libPicked: next });
      // Or the caret jumps to the end of the query on every arrow.
      e.preventDefault();
      return;
    }
    // The point of a filter you type into: narrow to it and open it without
    // reaching for the pointer. The query goes with it, because the search is
    // over -- leaving it would hand you a shelf of one row and no hint that
    // the rest are behind a box you have to find and empty.
    if (e.key === "Enter" && shown.length) {
      actions.openTask(shown[Math.max(at, 0)].row.task);
      actions.typeLibFilter("");
      e.currentTarget.value = "";
      set({ libPicked: -1 });
    }
  });
  return box;
}

/** The name, with the characters the query matched picked out.
 *
 * A fuzzy filter that does not show *why* a row survived reads as a list that
 * ignored what you typed -- `vrg1` matching `Mjlab-Velocity-Rough-Unitree-G1`
 * is obvious once the four letters are marked and cryptic until they are.
 */
function marked(text, hits) {
  if (!hits || !hits.length) return el("b", { text });
  const name = el("b", {});
  const wanted = new Set(hits);
  let run = "";
  let inHit = false;
  const flush = () => {
    if (!run) return;
    name.append(inHit ? el("i", { class: "lib-hit", text: run })
                      : document.createTextNode(run));
    run = "";
  };
  for (let i = 0; i < text.length; i++) {
    const hit = wanted.has(i);
    if (hit !== inHit) { flush(); inHit = hit; }
    run += text[i];
  }
  flush();
  return name;
}

function shelfRow(s, { row, hits }, picked, actions) {
  const chosen = row.task === s.task;
  return el("button", {
    // Two marks, because they answer two questions: `on` is the task you have
    // open, `picked` is where Enter would take you. They are the same row
    // often enough that one style for both would read as correct until the
    // day it mattered.
    class: `lib-row${chosen ? " on" : ""}${picked ? " picked" : ""}`,
    key: row.task,
    title: row.line || row.task,
    "aria-selected": chosen ? "true" : "false",
    // Opening a task from here is the same act as picking it in the switcher:
    // the rail, the graph and the source tree all follow it, so this must not
    // be a second, quieter way of changing the same thing.
    onclick: () => actions.openTask(row.task),
  },
    el("span", { class: "lib-name" },
      dot(row.live ? "running" : row.runs ? "planned" : "superseded", !!row.live),
      marked(row.task, hits)),
    el("span", { class: "lib-counts" },
      // "no runs yet" is a state, not a blank: it is what a task being built
      // says.
      el("small", { text: row.runs ? `${row.runs} run${row.runs === 1 ? "" : "s"}` : "no runs yet" }),
      // What put a task with no runs high on a shelf headed "most recently
      // worked on": its files, which are the only clock it has.
      row.edited ? el("small", { class: "lib-e", text: `edited ${row.edited}` }) : null,
      row.findings ? el("small", { class: "lib-f", text: `${row.findings} found` }) : null,
      row.pitfalls ? el("small", { class: "lib-p", text: `${row.pitfalls} pitfall${row.pitfalls === 1 ? "" : "s"}` }) : null));
}

// ── the card ────────────────────────────────────────────────────────────

/** The card itself, without the shelf. Exported for the report, which is
 *  the studio's own panels saved to a file. */
export function cardPane(s, actions) {
  const pane = el("div", { class: "libcard" });
  if (!s.task) {
    return add(pane, el("div", { class: "empty" },
      "Pick a task from the shelf. Its card is what somebody should read "
      + "before their first run on a task like it."));
  }
  const card = s.card && s.card.task === s.task ? s.card : null;
  // Switching task is the one moment this pane cannot keep what it had: the
  // previous card belongs to another task, and a card headed with the wrong
  // name is worse than the word "reading". So it keeps the *frame* instead --
  // the name you just asked for, in the place the name goes -- rather than
  // collapsing the whole pane to one grey line, which reads as a panel that
  // broke.
  if (!card) {
    return add(pane,
      el("div", { class: "libcard-h" },
        el("h2", { text: s.task }),
        chip("reading its runs…")),
      el("div", { class: "lib-empty",
                  text: "What it is, what its runs concluded, and what wasted "
                      + "time — assembled from the records." }));
  }
  if (card.error) {
    return add(pane, el("div", { class: "notyet" },
      el("b", { text: "That card could not be read" }), card.error));
  }

  const history = card.history || {};
  add(pane,
    el("div", { class: "libcard-h" },
      el("h2", { text: card.task }),
      card.registered ? chip("registered", true) : chip("not registered here"),
      card.recorded ? chip(`${history.runs} recorded`) : chip("never trained"),
      history.live ? chip(`${history.live} live`, true) : null,
      el("span", { style: { marginLeft: "auto" } }),
      reportControl(s, actions)),
    card.blurb ? el("p", { class: "lib-blurb", text: card.blurb }) : null,
    identity(card),
    verdicts(history),
    section("Findings", card.findings,
            "what its runs concluded — and what they ruled out",
            "Nothing has been concluded yet. A run closed as validated, best, "
            + "provisional or falsified is what puts a line here.",
            entry => finding(entry, actions)),
    section("Pitfalls", card.pitfalls,
            "what wasted time, and what somebody said not to do again",
            "Nothing has been written down as a pitfall. An interrupted run, "
            + "or a remark of kind correct, reject or constrain, lands here.",
            entry => pitfall(entry, actions)),
    section("Not settled", card.open_questions,
            "what this task has not answered",
            "Nothing is outstanding: no run is still open and every "
            + "instruction on the ledger has an answer.",
            entry => pitfall(entry, actions)),
    reading(card, actions),
    media(s, card),
    published(card));
  return pane;
}

/** Which package registers it, and where its history is kept.
 *
 * A card that named neither would leave the reader unable to answer the first
 * question anybody has about a task they did not write: where is it. */
function identity(card) {
  const rows = [
    ["package", card.package],
    ["backend", card.backend],
    ["experiment", card.experiment],
    ["records", card.root],
  ].filter(([, value]) => value);
  if (!rows.length) return null;
  return el("dl", { class: "lib-id" },
    rows.map(([label, value]) => el("div", {},
      // The whole value in the tooltip: the cell clips rather than
      // breaking a path mid-word, and a records root is worth reading in full.
      el("dt", { text: label }),
      el("dd", { class: "fp", title: value, text: value }))));
}

/** How the runs ended, in rl-mcp's own colours.
 *
 * The palette is `records/views.py`'s, reached through `verdictColor` — the
 * studio must not invent a second vocabulary for run state. */
function verdicts(history) {
  const tally = Object.entries(history.verdicts || {});
  if (!tally.length) return null;
  return el("div", { class: "lib-verdicts" },
    tally.sort((a, b) => b[1] - a[1]).map(([verdict, count]) =>
      el("span", { class: "lib-v", title: `${count} ${verdict}` },
        dot(verdict), el("small", { text: `${count} ${verdict}` }))));
}

function section(title, entries, blurb, empty, render) {
  const list = entries || [];
  return el("section", { class: "lib-sec" },
    el("div", { class: "lib-sec-h" },
      el("h3", { text: title }),
      el("small", { text: blurb }),
      el("span", { class: "sp" }),
      chip(String(list.length))),
    list.length
      ? el("div", { class: "lib-entries" }, list.map(render))
      // An absence says what would fill it. "No findings" alone reads
      // as a broken panel; this reads as a task nobody has finished a run on.
      : el("div", { class: "lib-empty", text: empty }));
}

function finding(entry, actions) {
  return el("div", { class: "lib-entry", key: `${entry.run}:${entry.text}` },
    el("div", { class: "lib-text" },
      el("span", { class: `lib-stance ${entry.stance === "ruled out" ? "out" : "in"}`,
                   text: entry.stance }),
      entry.text),
    // What was changed to get it. The recipe says what to do; a finding with
    // the intervention beside it says why anyone did. Lines rather than chips:
    // a `change` on a real record is often a paragraph of several hundred
    // characters, and a chip that long is a chip-shaped wall of text.
    entry.change && entry.change.length
      ? el("div", { class: "lib-change" },
          entry.change.map(c => el("small", { class: "lib-chg", text: c })))
      : null,
    cite(entry, actions));
}

function pitfall(entry, actions) {
  return el("div", { class: "lib-entry", key: `${entry.run}:${entry.index ?? ""}:${entry.text}` },
    el("div", { class: "lib-text" },
      entry.kind ? el("span", { class: "lib-kind", text: entry.kind }) : null,
      entry.text),
    entry.source === "feedback" && entry.answered === false
      ? el("small", { class: "muted", text: "nobody has answered this" })
      : null,
    cite(entry, actions));
}

/** The run behind a claim, and a way to go and read it. */
function cite(entry, actions) {
  return el("button", {
    class: "lib-cite", title: `open ${entry.run}`,
    onclick: () => { actions.selectRun(entry.run); actions.setView("records"); },
  },
    dot(entry.verdict),
    el("small", { text: `${entry.run} · ${entry.slug || ""}` }),
    el("small", { class: "muted", text: entry.date || "" }));
}

/** What to read first, handed to the view that renders markdown. */
function reading(card, actions) {
  const docs = card.read_first || [];
  return el("section", { class: "lib-sec" },
    el("div", { class: "lib-sec-h" },
      el("h3", { text: "Read first" }),
      el("small", { text: "the notes about this task, then the run write-ups" }),
      el("span", { class: "sp" }), chip(String(docs.length))),
    docs.length
      ? el("div", { class: "lib-docs" }, docs.map(doc => el("button", {
          class: "lib-doc", key: doc.path, title: doc.path,
          // Opened in Docs rather than rendered here: that view already has
          // the markdown renderer, and two renderers would drift.
          onclick: () => { set({ docPath: doc.path, docRaw: false }); actions.setView("docs"); },
        },
          el("b", { text: doc.name }),
          doc.title ? el("small", { text: doc.title }) : null,
          el("small", { class: "muted", text: doc.group || "" }))))
      : el("div", { class: "lib-empty",
                    text: "Nothing written yet. A .md in the record's folder, the "
                        + "run's artifacts, or the task repository shows up here." }));
}

/** The clips the records kept. */
function media(s, card) {
  const clips = card.media || [];
  if (!clips.length) return null;
  const showing = clips[0];
  return el("section", { class: "lib-sec" },
    el("div", { class: "lib-sec-h" },
      el("h3", { text: "What it looks like" }),
      el("small", { text: `kept on run ${showing.run}` })),
    el("video", { class: "lib-clip", src: s.recordAssetUrl(showing.key),
                  controls: true, loop: true, muted: true, playsInline: true }),
    showing.caption ? el("small", { class: "muted", text: showing.caption }) : null);
}

/** Why this is not the published card, said on the card itself.
 *
 * A missing capability degrades visibly. Leaving it out would let this
 * assembled view read as the immutable, agent-written published card. */
function published(card) {
  const where = card.published || {};
  if (where.exists) return null;
  const roots = where.roots || [];
  return el("div", { class: "lib-note" },
    el("b", { text: "Not published" }), where.reason || " ",
    // Where one would go, when the studio was told: `$RLMCP_LIBRARY`.
    // Without a root the honest sentence is that there is nowhere to put one.
    roots.length
      ? el("div", {}, "Library root: ", el("code", { text: roots.join(", ") }))
      : el("div", { text: "No library root is configured, so there is nowhere "
                        + "a published card could be read from yet." }));
}
