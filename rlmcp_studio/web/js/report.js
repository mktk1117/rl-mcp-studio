// Report — the whole history of a task, saved as one HTML file that looks
// exactly like the studio, because it is built from the studio's own panels.
//
// Nothing here draws a run. The story grid is `renderView("records")`, the
// per-run sections are the Focus layout's panels (`recordBrief`,
// `measuredCard`, `feedbackCard`, `metricCards`, `clipsCard`), the Overview
// and the library card are their own views, and the page carries the app's
// stylesheets verbatim. Whatever the studio shows is what the file shows;
// a panel restyled in the app is restyled in the next report. The one thing
// the file adds is what a file needs and a page does not: every clip, still
// and poster fetched and written into it as a data URI, so it stands alone.
//
// The button sits in the two places a task is looked at as a whole: the
// Records toolbar and the library card. Its popover prices the three media
// choices before anything is written -- a task that filmed itself every
// hundred iterations is a few hundred megabytes of video, and that is a
// decision rather than a surprise -- then composes the page here in the
// browser and hands it to the server, which files it under the task's own
// records root and offers it back to download.
import { el, chip } from "./ui.js";
import { state, set } from "./state.js";
import { api } from "./api.js";
import { renderView } from "./views.js";
import { recordBrief, measuredCard, feedbackCard, metricCards, clipsCard,
         headlineStats, notYet } from "./components.js";
import { overviewView } from "./overview.js";
import { cardPane } from "./library.js";
import { recipeView } from "./recipe.js";
import { renderMarkdown } from "./docs.js";

const MODES = [
  { id: "all", label: "every clip", blurb: "each progress clip of each run, and every still" },
  { id: "latest", label: "the last clip of each run", blurb: "what the story card leads with, and every still" },
  { id: "none", label: "stills only", blurb: "plots and posters; clips listed by caption" },
];

/** How many metric rows a run's charts are drawn from: the whole run, in
 *  practice. The session route's `tail` is a count, and a run is thousands. */
const WHOLE_RUN = 200000;

// ── the button and its popover ───────────────────────────────────────────

/** The button, with its popover when open. `s` is the store; the task is `s.task`. */
export function reportControl(s, actions) {
  if (!s.task) return null;
  const open = !!s.reportOpen;
  return el("div", { class: "menuwrap report" },
    el("button", {
      class: open ? "act on" : "act", "aria-expanded": open ? "true" : "false",
      title: "the whole history of this task as one HTML file, media embedded",
      onclick: () => (open ? actions.closeReport() : actions.openReport()),
    }, "Report", el("small", { text: "↓" })),
    open ? popover(s, actions) : null);
}

function popover(s, actions) {
  const plan = s.reportPlan && s.reportPlan.task === s.task ? s.reportPlan : null;
  const done = s.reportDone && s.reportDone.task === s.task ? s.reportDone : null;
  const mode = MODES.some(m => m.id === s.reportMedia) ? s.reportMedia : "all";
  return el("div", { class: "menu report-pop" },
    el("div", { class: "menu-h report-h" },
      el("span", { class: "eyebrow", text: "one file, everything in it" }),
      el("span", { class: "sp" }),
      el("button", { class: "act tiny", onclick: () => actions.closeReport() }, "Close")),
    !plan && !s.reportError
      ? el("div", { class: "report-note dim", text: "reading the records…" })
      : null,
    plan ? summary(plan) : null,
    plan ? el("div", { class: "report-modes" },
      MODES.map(m => el("label", { class: "report-mode" },
        el("input", { type: "radio", name: "report-media", value: m.id,
                      checked: m.id === mode ? "checked" : null,
                      oninput: () => set({ reportMedia: m.id }) }),
        el("span", { class: "report-mode-l" },
          el("span", { text: m.label }),
          el("small", { class: "num", text: weight(plan, m.id) })),
        el("span", { class: "why-note", text: m.blurb })))) : null,
    s.reportError ? el("p", { class: "m-error", text: s.reportError }) : null,
    done ? saved(done) : null,
    el("div", { class: "launch-go" },
      el("button", {
        class: "act primary", disabled: !plan || s.reportBusy || !plan.runs ? "disabled" : null,
        onclick: () => actions.buildReport(mode),
      }, s.reportBusy ? "writing…" : done ? "Write it again" : "Write the report"),
      s.reportBusy && s.reportStep
        ? el("span", { class: "eyebrow", text: s.reportStep })
        : plan && !plan.runs ? el("span", { class: "eyebrow", text: "no runs recorded" }) : null));
}

function summary(plan) {
  return el("div", { class: "report-sum" },
    chip(`${plan.runs} run${plan.runs === 1 ? "" : "s"}`),
    chip(`${plan.clips} clip${plan.clips === 1 ? "" : "s"}`),
    plan.first ? el("span", { class: "k dim", text: `${plan.first} → ${plan.last}` }) : null);
}

function weight(plan, mode) {
  const m = (plan.modes || {})[mode];
  if (!m) return "";
  return `${human(m.bytes)} · ${m.files} file${m.files === 1 ? "" : "s"}`;
}

function saved(done) {
  return el("div", { class: "report-saved" },
    el("span", { class: "k ok", text: `written · ${human(done.size)}` }),
    el("span", { class: "fp", text: done.path, title: done.path }),
    el("a", { class: "act", href: done.url, download: done.name || "" }, "Download"));
}

export function human(n) {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${Math.round(n / 1024)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** Open the popover and ask what the file would weigh. */
export async function openReport() {
  const task = state.task;
  set({ reportOpen: true, reportError: "", reportDone: null });
  try {
    const plan = await api.reportPlan(task);
    set({ reportPlan: { ...plan, task } });
  } catch (err) {
    set({ reportError: err.message });
  }
}

export function closeReport() {
  set({ reportOpen: false });
}

/** Compose the page from the studio's own panels, then hand it to the server to file. */
export async function buildReport(media) {
  const task = state.task;
  set({ reportBusy: true, reportError: "", reportDone: null, reportStep: "reading the records…" });
  try {
    const html = await compose(task, media, (step) => set({ reportStep: step }));
    set({ reportStep: "saving…" });
    const answer = await api.saveReport(task, media, html);
    set({ reportBusy: false, reportDone: { ...answer, task }, reportStep: "" });
  } catch (err) {
    set({ reportBusy: false, reportError: err.message, reportStep: "" });
  }
}

// ── composing the page ───────────────────────────────────────────────────

/** Every action a panel might wire up, as a no-op: the file has no studio behind it. */
function inert() {
  return new Proxy({}, { get: () => () => {} });
}

/** The state a panel is handed: the real store, with the subject replaced.
 *  The store carries the person's own settings -- widths, headline choice,
 *  smoothing -- so the panels come out the way they are on screen. */
function scene(task, more) {
  return { ...state, task, runId: null, agentOpen: false, clipsOpen: true,
           clipUrl: "", clipError: "", playWanted: "", frames: [], lastFrame: null,
           pickedNode: null, ...more };
}

async function compose(task, media, progress) {
  const actions = inert();
  const graph = (await api.recordGraph(task)).nodes || [];
  const runs = (await api.runs(task)).runs || [];
  const base = scene(task, { runs, graph, graphTask: task });
  const latest = graph.length ? graph[graph.length - 1].id : null;

  // The story grid, as the Records view draws it. Its toolbar is stripped
  // below: the segmented control and this very button mean nothing in a file.
  const story = renderView("records", { ...base, recordsMode: "story" }, actions);
  const tree = renderView("records", { ...base, recordsMode: "tree", runId: latest }, actions);

  const sections = [];
  const budget = { files: 0, bytes: 0 };
  for (const node of graph) {
    progress(`reading ${node.id}…`);
    const section = await runSection(task, node, runs, base, actions);
    if (section) sections.push(section);
  }

  progress("reading the task…");
  const tabs = [
    ["history", "History", el("div", {},
      el("div", { class: "recordsview rep-story" }, story),
      ...sections)],
    ["tree", "Graph", el("div", { class: "recordsview" }, tree)],
    ["recipe", "Recipe", await tab(() => recipeTab(task, latest, base, actions))],
    ["overview", "Overview", await tab(() => overviewTab(task, base, actions))],
    ["card", "Card", await tab(() => cardTab(task, base, actions))],
    ["params", "Parameters", await tab(() => paramsTab(task, latest, base, actions))],
    ["docs", "Docs", await tab(() => docsTab(task, latest))],
  ];

  const counts = {};
  for (const r of runs) counts[r.verdict] = (counts[r.verdict] || 0) + 1;
  const root = el("div", { class: "app rep" },
    el("header", { class: "chrome" },
      el("span", { class: "brand" }, "rlmcp studio", el("span", { text: "report" })),
      el("span", { class: "act taskbtn", text: task }),
      el("span", { class: "k dim", text: `${runs.length} run${runs.length === 1 ? "" : "s"}` }),
      ...Object.entries(counts).sort((a, b) => b[1] - a[1]).map(([v, n]) => chip(`${n} ${v}`)),
      el("span", { class: "spacer" }),
      el("span", { class: "k dim", id: "rep-media" }),
      el("span", { class: "k dim", text: `written ${new Date().toISOString().slice(0, 16).replace("T", " ")}` }),
      el("button", { class: "act", id: "rep-scheme" },
        document.documentElement.getAttribute("data-scheme") === "light" ? "Dark" : "Light")),
    el("div", { class: "f-more rep-tabs" },
      el("span", { class: "lbl", text: "view" }),
      tabs.map(([id, label], i) => el("button", { "data-tab": id,
                                                  "aria-pressed": i === 0 ? "true" : "false" }, label))),
    tabs.map(([id, , body], i) => el("div", { class: "rep-pane", id: `pane-${id}`,
                                               hidden: i === 0 ? null : "hidden" }, body)));

  strip(root);
  progress("embedding the media…");
  await inlineMedia(root, media, budget, progress);
  const note = root.querySelector("#rep-media");
  if (note) note.textContent = `${human(budget.bytes)} of media · ${
    { all: "every clip embedded", latest: "the last clip of each run embedded", none: "stills only" }[media]}`;

  const sheets = await Promise.all(STYLESHEETS.map(u => fetch(u).then(r => r.text())));
  const html = document.documentElement;
  return `<!doctype html>
<html lang="en" data-scheme="${attr(html.getAttribute("data-scheme") || "dark")}" data-accent="${attr(html.getAttribute("data-accent") || "slate")}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${text(task)} — history</title>
<style>
${sheets.join("\n")}
${OVERRIDES}
</style>
</head>
<body>
${root.outerHTML}
<script>
${SCRIPT}
</script>
</body>
</html>
`;
}

async function tab(make) {
  try {
    return await make();
  } catch (err) {
    return notYet("This panel could not be drawn", err.message, "");
  }
}

/** One run, as the Focus layout shows it: the claim, what it measured, what
 *  was said, the published series, the film. */
async function runSection(task, node, runs, base, actions) {
  const detail = await api.run(node.id, task).catch(() => null);
  if (!detail || !detail.record) return null;
  const run = runs.find(r => r.id === node.id) || detail.record;
  let live = detail.live && detail.live.ok ? detail.live : null;
  const session = detail.record.session;
  if (session) {
    // The run route's snapshot is 120 rows; the charts want the whole run.
    try {
      const whole = await api.session(session, WHOLE_RUN);
      if (whole && whole.ok) live = whole;
    } catch { /* the 120-row snapshot stands */ }
  }
  const clips = await api.clips(node.id).catch(() => ({ clips: [] }));
  // The brief reads the diff against the parent from the store, and asks for
  // it when it is not there; asked here instead, so the file carries the
  // answer rather than "reading the diff…".
  const diff = await api.diff(node.id).then(d => ({ ...d, record: node.id })).catch(() => null);
  const ps = { ...base, runId: node.id, detail, live, clips, diff, view: "control" };
  const series = (live && live.series) || {};

  const section = el("section", { class: "rep-run", id: `run-${node.id}`, "data-run": node.id },
    el("div", { class: "f-head" },
      el("div", { class: "f-title" },
        el("span", { class: "id", text: run.id }),
        el("h2", { text: run.slug || "" }),
        chip(run.verdict)),
      el("div", { class: "f-sub", text: subtitle(run) })),
    el("div", { class: "rep-grid" },
      el("div", { class: "rep-main" },
        Object.keys(series).length
          ? el("div", { class: "plots-b" }, metricCards(series, 8, live))
          : el("div", { class: "empty", text: "nothing published by this run" }),
        clipsCard(ps, actions),
        await runDocs(node.id)),
      el("div", { class: "f-side" },
        headlineStats(series, live),
        recordBrief(ps, actions),
        feedbackCard(ps, actions),
        measuredCard(run, ps, actions))));

  // The clips card plays through one shared `<video>` node, moved into
  // whichever card asked last; this section keeps a copy of its own. The
  // strip's buttons remember their clip, so the file can swap the player.
  const copy = section.cloneNode(true);
  const thumbs = copy.querySelectorAll(".strip.clips .thumb");
  (clips.clips || []).forEach((clip, i) => {
    const thumb = thumbs[i];
    if (!thumb) return;
    thumb.dataset.src = clip.url || "";
    thumb.dataset.poster = clip.poster_url || "";
    thumb.dataset.label = thumb.title || "";
  });
  return copy;
}

function subtitle(run) {
  const bits = [];
  if (run.weights) bits.push(`warm started from ${run.weights}`);
  else if (run.parent) bits.push(`config from ${run.parent}`);
  else bits.push("from scratch");
  if (run.date) bits.push(run.date);
  return bits.join(" · ");
}

/** `PLAN.md`, `REPORT.md` and whatever else was written beside the record,
 *  rendered by the Docs view's own renderer, folded. */
async function runDocs(runId) {
  const listing = await api.docs(runId).catch(() => null);
  const group = ((listing && listing.groups) || []).find(g => g.id === "run");
  const docs = (group && group.docs) || [];
  if (!docs.length) return null;
  const parts = [];
  for (const doc of docs) {
    const read = await api.doc(doc.path).catch(() => null);
    if (!read || !read.ok) continue;
    parts.push(el("details", { class: "rep-doc" },
      el("summary", {},
        el("span", { class: "k", text: doc.name }),
        read.title ? el("span", { text: ` · ${read.title}` }) : null,
        el("span", { class: "k dim", text: ` · ${doc.source || ""}` })),
      el("div", { class: "md" },
        el("article", { class: "md-body prose" }, ...renderMarkdown(read.text, read.path)))));
  }
  if (!parts.length) return null;
  return el("div", { class: "card" },
    el("div", { class: "card-h" }, el("h3", { text: "Written about this run" }),
      el("span", { class: "sp" }), el("span", { class: "eyebrow", text: `${parts.length} documents` })),
    el("div", { class: "card-b" }, ...parts));
}

async function recipeTab(task, latest, base, actions) {
  if (!latest) return notYet("No recipe", "No runs, no ancestry to fold.", "");
  const recipe = await api.recipe(latest);
  return recipeView({ ...base, runId: latest, recipe }, actions);
}

async function overviewTab(task, base, actions) {
  const overview = await api.overview(task);
  return el("div", { class: "rep-view" }, overviewView({ ...base, overview, view: "overview" }, actions));
}

async function cardTab(task, base, actions) {
  const card = await api.card(task);
  return el("div", { class: "libview" }, cardPane({ ...base, card: { ...card, task } }, actions));
}

async function paramsTab(task, latest, base, actions) {
  const history = await api.paramHistory(task);
  return renderView("params", { ...base, history, runId: latest, view: "params" }, actions);
}

async function docsTab(task, latest) {
  const listing = await api.docs(latest || undefined).catch(() => null);
  const parts = [];
  for (const group of (listing && listing.groups) || []) {
    if (group.id !== "task") continue;
    for (const doc of group.docs || []) {
      const read = await api.doc(doc.path).catch(() => null);
      if (!read || !read.ok) continue;
      parts.push(el("details", { class: "rep-doc" },
        el("summary", {},
          el("span", { class: "k", text: doc.name }),
          read.title ? el("span", { text: ` · ${read.title}` }) : null,
          el("span", { class: "k dim", text: ` · ${doc.path}` })),
        el("div", { class: "md" },
          el("article", { class: "md-body prose" }, ...renderMarkdown(read.text, read.path)))));
    }
  }
  if (!parts.length) {
    return notYet("Nothing written about the task",
                  "No markdown in the task repository's top level, docs/ or notes/.", "");
  }
  return el("div", { class: "rep-docs" }, ...parts);
}

/** What a file cannot use: controls that talk to the studio, and handles.
 *
 * A card header's buttons -- Hide, Diff, Re-read -- all ask the studio for
 * something; the diff itself is read live and would say "reading…" for ever;
 * the Parameters view's live table and trace are the running trainer's, and
 * only its history section is about the records. */
function strip(root) {
  for (const node of root.querySelectorAll(
      ".plots-h, .divider, input, textarea, select, .menuwrap.report, .fb-row, .close-card, "
      + ".card-h button, .brief-diff button, .hl-config")) {
    node.remove();
  }
  // A panel still waiting on the studio says "reading …"; in a file it
  // would say it for ever.
  for (const node of root.querySelectorAll(".empty")) {
    if (/^reading\b/.test(node.textContent.trim())) node.remove();
  }
  for (const outer of root.querySelectorAll(".paramview-outer")) {
    while (outer.children.length > 1) outer.lastElementChild.remove();
  }
  // A preview shows its first frame rather than a black box until hovered:
  // the bytes are in the file already, so there is nothing to save by waiting.
  for (const video of root.querySelectorAll(".story-film video")) {
    video.setAttribute("preload", "metadata");
  }
}

// ── media ────────────────────────────────────────────────────────────────

/** Every picture and clip the page points at, fetched and written into it. */
async function inlineMedia(root, media, budget, progress) {
  const cache = new Map();
  const uri = async (url) => {
    if (!url || url.startsWith("data:")) return url;
    if (cache.has(url)) return cache.get(url);
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${response.status} for ${url}`);
    const blob = await response.blob();
    const data = await new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    budget.files += 1;
    budget.bytes += blob.size;
    cache.set(url, data);
    return data;
  };
  const swap = async (node, name) => {
    const url = node.getAttribute(name);
    if (!url || url.startsWith("data:")) return true;
    try {
      node.setAttribute(name, await uri(url));
      return true;
    } catch {
      node.removeAttribute(name);
      return false;
    }
  };

  let n = 0;
  const images = root.querySelectorAll("img[src]");
  for (const img of images) {
    progress(`embedding stills ${++n}/${images.length}…`);
    if (!await swap(img, "src")) img.remove();
  }
  for (const video of root.querySelectorAll("video[poster]")) await swap(video, "poster");

  // The story cards' previews are each run's headline clip: in for `all` and
  // `latest`, out for `none`, where the film is a still.
  const previews = root.querySelectorAll(".story-film video");
  if (media === "none") {
    for (const video of previews) video.closest(".story-film").remove();
  } else {
    n = 0;
    for (const video of previews) {
      progress(`embedding previews ${++n}/${previews.length}…`);
      if (!await swap(video, "src")) video.closest(".story-film").remove();
    }
  }

  // Each run's player and strip. The player holds the clip the card opened
  // with (the newest); the strip's buttons carry theirs under `data-src`, so
  // the file can swap them in -- when they were embedded.
  const sections = root.querySelectorAll(".rep-run");
  for (const section of sections) {
    progress(`embedding clips of ${section.dataset.run}…`);
    const player = section.querySelector("video.clip");
    const thumbs = [...section.querySelectorAll(".strip.clips .thumb")];
    if (player) {
      if (media === "none") {
        const poster = player.getAttribute("poster");
        player.replaceWith(poster
          ? el("img", { class: "clip rep-still", src: poster, alt: "" })
          : el("div", { class: "empty", text: "clips not embedded" }));
      } else if (!await swap(player, "src")) {
        player.remove();
      }
    }
    for (const thumb of thumbs) {
      const keep = media === "all" || (media === "latest" && thumb.getAttribute("aria-current") === "true");
      if (keep && thumb.dataset.src) {
        try {
          thumb.dataset.src = await uri(thumb.dataset.src);
          if (thumb.dataset.poster) thumb.dataset.poster = await uri(thumb.dataset.poster);
          continue;
        } catch { /* fall through: listed, not playable */ }
      }
      delete thumb.dataset.src;
      delete thumb.dataset.poster;
      thumb.title = `${thumb.title} · not embedded`;
      thumb.classList.add("rep-absent");
    }
  }
  // Anything else still pointing home -- a chart's frame, a doc's picture.
  for (const video of root.querySelectorAll("video[src]")) {
    if (!video.getAttribute("src").startsWith("data:")) await swap(video, "src");
  }
}

function text(value) {
  return String(value).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function attr(value) {
  return text(value).replace(/"/g, "&quot;");
}

// The studio's stylesheets, in the order index.html links them: the file
// carries them verbatim rather than a copy of some of the rules.
const STYLESHEETS = [
  "/static/css/tokens.css",
  "/static/css/app/1-base.css", "/static/css/app/2-panels.css",
  "/static/css/app/3-run.css", "/static/css/app/4-documents.css",
  "/static/css/app/5-drawers.css", "/static/css/app/6-views.css",
];

// ── what the file adds to the studio's stylesheet ────────────────────────
//
// The app is a fixed viewport with scrolling panes; a document scrolls as a
// whole. Only the rules that turn one into the other live here.
const OVERRIDES = `
html, body { height: auto; overflow: auto; }
.app.rep { height: auto; min-height: 100vh; }
.rep .chrome { position: sticky; top: 0; z-index: 30; height: auto; min-height: 44px; flex-wrap: wrap; padding: 6px 12px; }
.rep .chrome .taskbtn { color: var(--accent); }
.rep-tabs { position: sticky; top: 44px; z-index: 29; margin: 0; padding: 8px 20px; background: var(--panel); border-bottom: 1px solid var(--line); }
.rep-pane { padding: 14px 20px 40px; }
.rep-pane[hidden] { display: none; }
.rep .recordsview { display: block; }
.rep .recordsview > .stories, .rep .recordsview > .treewrap { flex: none; min-height: 0; }
.rep-story .story { cursor: pointer; }
.rep-run { margin-top: 18px; background: var(--panel); border: 1px solid var(--line); border-radius: var(--r-card); scroll-margin-top: 92px; }
.rep-run .f-head { padding: 14px 20px 0; }
.rep-grid { display: grid; grid-template-columns: minmax(0, 1fr) 320px; gap: 18px; align-items: start; padding: 14px 20px 18px; }
.rep-main { display: flex; flex-direction: column; gap: 14px; min-width: 0; }
.rep .card-b.film { display: flex; flex-direction: column; }
.rep .clip-stage { height: min(56vw, 480px); flex: none; }
.rep video.clip { cursor: default; }
.rep .thumb.rep-absent { opacity: .45; cursor: default; }
.rep .rep-still { display: block; width: 100%; border-radius: var(--r); }
.rep-doc { border: 1px solid var(--line-soft); border-radius: var(--r); background: var(--raised); margin-top: 8px; }
.rep-doc summary { padding: 7px 11px; cursor: pointer; color: var(--muted); }
.rep-doc summary:hover { color: var(--text); }
.rep-doc .md { padding: 4px 14px 14px; }
.rep .treewrap, .rep .overview, .rep .paramview, .rep .lib { --side: 320px; }
.rep .ovwrap, .rep .overview, .rep .libview, .rep .lib, .rep .libcard, .rep .ovpane { height: auto; max-height: none; overflow: visible; }
.rep .libview { display: block; }
.rep .divider { display: none; }
.rep .dot.live { animation: none; }
@media (max-width: 900px) { .rep-grid { grid-template-columns: minmax(0, 1fr); } }
`;

// The little the file needs to behave: tabs, a card that jumps to its run, a
// strip that swaps the player, previews that play under the pointer, and the
// scheme toggle. No studio behind it, so nothing here talks to a server --
// and no reconciler either, which is why these are plain `on*` properties
// rather than the app's `on()`: this script runs in the file, not here.
const SCRIPT = `
(function () {
  var all = function (sel, root) { return [].slice.call((root || document).querySelectorAll(sel)); };
  var tabs = all('.rep-tabs [data-tab]');
  function show(id) {
    tabs.forEach(function (t) { t.setAttribute('aria-pressed', t.dataset.tab === id ? 'true' : 'false'); });
    all('.rep-pane').forEach(function (p) { p.hidden = p.id !== 'pane-' + id; });
  }
  tabs.forEach(function (t) { t.onclick = function () { show(t.dataset.tab); }; });
  all('.story[data-story]').forEach(function (card) {
    card.onclick = function () {
      var run = document.getElementById('run-' + card.dataset.story);
      if (run) { show('history'); run.scrollIntoView({ block: 'start' }); }
    };
  });
  all('.story-film').forEach(function (film) {
    var v = film.querySelector('video');
    if (!v) return;
    v.muted = true;
    film.onpointerenter = function () { v.play().catch(function () {}); };
    film.onpointerleave = function () { v.pause(); v.currentTime = 0; };
  });
  all('video.clip').forEach(function (v) {
    v.controls = true; v.muted = true;
    v.onclick = function () { v.paused ? v.play().catch(function () {}) : v.pause(); };
  });
  all('.rep-run').forEach(function (section) {
    var player = section.querySelector('video.clip');
    var cap = section.querySelector('.clip-cap b');
    all('.strip.clips .thumb', section).forEach(function (thumb) {
      thumb.onclick = function () {
        if (!thumb.dataset.src || !player) return;
        all('.strip.clips .thumb', section).forEach(function (o) { o.removeAttribute('aria-current'); });
        thumb.setAttribute('aria-current', 'true');
        player.src = thumb.dataset.src;
        if (thumb.dataset.poster) player.poster = thumb.dataset.poster;
        player.play().catch(function () {});
        if (cap) cap.textContent = thumb.dataset.label || thumb.title;
      };
    });
  });
  var sw = document.getElementById('rep-scheme');
  if (sw) sw.onclick = function () {
    var root = document.documentElement;
    var next = root.getAttribute('data-scheme') === 'light' ? 'dark' : 'light';
    root.setAttribute('data-scheme', next);
    sw.textContent = next === 'light' ? 'Dark' : 'Light';
  };
})();
`;
