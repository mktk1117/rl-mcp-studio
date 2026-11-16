// State lives here, outside every layout.
//
// This is the rule that makes layout a preference rather than three apps:
// switching layout must not lose your place, restart a conversation, or refetch
// what is already loaded. A layout owns nothing — it is a pure arrangement of
// components over this store.

const listeners = new Set();

// The three layouts, named here as well as in `layouts/` so a value that does
// not exist -- a stale name, a hand-edited localStorage -- is never restored
// into the app, where it would crash every paint.
const LAYOUT_IDS = new Set(["console", "focus", "workbench"]);

/** The layout the studio opens in when nothing has said otherwise.
 *
 * Named here rather than left as a fallback expression, because it is a
 * *decision* -- Workbench is the arrangement this app is for -- and because it
 * has to be applied in two places: before the settings file has arrived, and
 * again when the file turns out not to mention a layout at all.
 */
export const DEFAULT_LAYOUT = "workbench";

// The views, for the same reason. An id that is not one of these leaves the
// tab strip pointing at one panel while another is on screen -- silent, and
// indistinguishable from a view that failed to render. Kept here rather than
// imported from `views.js` because this module must not depend on it.
const VIEW_IDS = new Set(["build", "overview", "control", "plots", "records",
                          "params", "source", "docs", "library"]);

/** The accents `tokens.css` defines, named here for the same reason the layout
 * and view ids are: a value that is not one of these leaves `data-accent` on
 * the root matching no rule, so the app silently keeps the default while the
 * picker shows a tick beside something else.
 *
 * `slate` is first because it is the default, in both senses -- the base
 * blocks in `tokens.css` carry its values, and this is what an unset
 * preference resolves to. */
export const ACCENTS = ["slate", "graphite", "clay", "indigo", "violet",
                        "plum", "teal", "cyan"];

import { loadWidths } from "./resize.js";

/** Who the agent is talking about right now.
 *
 * A conversation is not always about a run: a task being written in Build
 * has no record and may never have had a run, and it is the thing you most
 * want an agent for. So the subject follows the view --
 * `task:<id>` in Build, the run id everywhere else -- and the transcript,
 * the composer and the permission cards all key on it.
 *
 * **Until you are actually in a conversation.** Following the view is the
 * right default and the wrong rule: you ask an agent to start a run, the run
 * appears, you click it to watch the numbers -- and the panel swaps to that
 * run's empty conversation, taking away the agent you were talking to at the
 * moment it was answering. So a conversation you opened or spoke into is
 * *pinned*: `convPin` outranks the view, and the thread bar says which run you
 * are talking about and offers the one under the cursor. Watching a run and
 * talking to an agent are two things, and only one of them is a selection.
 */
export function subject(s = state) {
  return s.convPin || naturalSubject(s);
}

/** What the *view* is about: the run in the rail, or the task in Build.
 *
 * The subject the panel would have if nobody had chosen a conversation. Kept
 * separate from `subject` because the two answers differ exactly when the pin
 * is doing its job, and the thread bar has to be able to say so.
 */
export function naturalSubject(s = state) {
  // Build and Overview are both about the task rather than a run of it, so a
  // conversation opened from either is keyed to the task.
  if (s.view === "build" || s.view === "overview") {
    return buildable(s.task) ? `task:${s.task}` : "";
  }
  return s.runId || "";
}

/** The label records get when nobody stamped a task on them.
 *
 * rlmcp names the task in every record it writes; older records that lack it
 * are grouped under this label rather than hidden, so they stay reachable.
 * It is a *bucket*, not a task, and the difference matters as soon
 * as you try to do something to it: there is no package to edit, no env to
 * construct, and no agent that can build it. Kept next to `subject` because
 * that is the function that has to know.
 */
export const UNKNOWN_TASK = "(unknown)";

/** Whether `task` is a task you can build, preview and talk to an agent about,
 *  as opposed to the legacy bucket or nothing at all. */
export function buildable(task) {
  return !!task && task !== UNKNOWN_TASK;
}

export const state = {
  ready: false,
  connected: false,
  // Panel widths, one map, persisted per browser -- see resize.js.
  widths: loadWidths(),
  scheme: localStorage.getItem("rlmcp.scheme") || "dark",
  accent: ACCENTS.includes(localStorage.getItem("rlmcp.accent"))
            ? localStorage.getItem("rlmcp.accent") : "slate",
  // The cache, not the answer: `applySettings` replaces this with the settings
  // file's opening layout (or `DEFAULT_LAYOUT`) as soon as the file arrives.
  // It exists so the first paint is not in the wrong layout for one frame.
  layout: LAYOUT_IDS.has(localStorage.getItem("rlmcp.layout"))
            ? localStorage.getItem("rlmcp.layout") : DEFAULT_LAYOUT,

  roots: null,
  tasks: [],
  // What the task registry said when the list was built: `ok`, `error`, the
  // packages it imported and the backends it asked. Null until the first load.
  registry: null,
  task: null,

  runs: [],
  // Sessions on disk that no record names: training, or trained, and
  // invisible in a rail built from records until somebody writes one.
  unrecorded: [],
  // Whether the bar's unrecorded-sessions menu is open. In the store like
  // every other open/closed here, so a repaint mid-read does not shut it.
  unrecOpen: false,
  adopting: null, adoptSlug: "", adoptWhy: "", adoptBusy: false, adoptError: "",
  runId: null,
  runFilter: "",         // narrows the rail; the run you are on stays selected
  detail: null,          // the selected run, fully loaded
  live: null,            // its session snapshot, when it has one

  view: VIEW_IDS.has(localStorage.getItem("rlmcp.view"))
          ? localStorage.getItem("rlmcp.view") : "control",
  // The Build view: the preview's state, the gates' answer, and where both of
  // them run -- one payload, because they are one screen. `previewLive` is the
  // preview's own session snapshot, which is where its 3-D view's url lives.
  build: null, previewLive: null, buildBusy: "", buildError: "",
  // The launch form is per task and lives in the store, like every other
  // draft here: a half-typed hypothesis must not be thrown away by a repaint,
  // and the live feed repaints several times a minute.
  // The "+ New task" dialog: open, its four answers, and why it refused.
  newOpen: false, newBusy: false, newError: "",
  newTask: "", newPackage: "", newRobot: "", newWhat: "",
  launches: null, launchOpen: false, launchBusy: false, launchError: "",
  // Recipes on disk for this task, and the one being built or launched.
  recipes: null, recipeBusy: "", recipeError: "", recipeBuilt: null,
  launchSlug: "", launchWhy: "", launchEnvs: 4096, launchDevice: "cuda:0",
  launchIterations: 0, launchVideo: "",
  source: null, sourceStamp: null,
  // The Docs view: the listing, the document you opened, and whether you are
  // reading it rendered or as it was typed. `docPath` is the *path*, so an
  // agent rewriting the file under you does not change which one you are on.
  docs: null, docsStamp: null,
  // The task library: one task's card, and the index of every task. Both
  // are cached into the store like every other async view -- a view that
  // fetched into the node it filled would never show anything.
  card: null, cardStamp: null, libIndex: null, libIndexStamp: null,
  // What was typed into the shelf's filter, and the counter that says a
  // record changed under it. The nonce is how a refresh is asked for without
  // blanking what is on screen: it moves the stamp, the fetch goes out, and
  // the previous answer stays drawn until the new one lands.
  libFilter: "", libNonce: 0,
  // Which row of the shelf the keyboard is on. -1 until an arrow is pressed:
  // a highlight nobody asked for sits beside the row that is actually
  // selected, and two marks on one list is a list that says neither.
  libPicked: -1,
  docPath: "", doc: null, docStamp: null, docRaw: false,
  // The Overview view: the task's report and the facts beside it, whether it
  // is read rendered or raw, the note that goes with a request to write one,
  // and why the last request did not go. The note lives here for the reason
  // every draft in this app does -- switching task rebuilds the node it was
  // typed into, and a handler reading that node reads a detached copy.
  overview: null, overviewStamp: null, overviewRaw: false,
  overviewNote: "", overviewError: "",
  overviewBusy: false,  // the write request is in flight; the button says so
  file: null, fileStamp: null, filePath: "",
  fileFilter: "", collapsed: {},
  fileMode: "name",     // "name" (fuzzy, in the browser) or "text" (a search)
  filePicked: 0,        // which result the keyboard is on
  fileSearch: null,     // the answer to a content search
  editing: false, saveError: "",
  // Showing the open file's patch against the parent instead of the editor.
  // Not persisted: it is where you are, not how you like things.
  comparing: false,
  frames: [],            // artifacts this session produced, newest first
  clips: null,           // the run's progress clips, oldest first
  clipUrl: "",           // the one in the player
  clipPicked: false,  // true once you chose one; then new clips stop stealing it
  clipError: "",      // why the clip on screen will not play, if it will not
  running: {},           // verb -> true while its command is in flight
  commandError: "", lastFrame: null, diagnosis: null,
  verbDone: {},                    // verb -> {ok, message}, cleared after a moment
  tracePath: "", trace: null, traceStamp: null, traceChannel: "",
  clipsOpen: localStorage.getItem("rlmcp.clipsOpen") !== "0",
  robotMode: localStorage.getItem("rlmcp.robotMode") || "now",
  // The 3-D view: whether a request to open or close it is in flight, and why
  // the last one did not work. Not the view's own state -- that is rlmcp's,
  // read from `status.live_view`, never mirrored here.
  viewBusy: false, viewError: "",
  // The run's own policy, played back after the trainer has gone: the process
  // the studio started, and the snapshot of the rlmcp session it holds open.
  // `playLive` is read exactly like `live` -- same session, same `live_view`.
  play: null, playLive: null, playBusy: false, playError: "",
  // The started_at of the played process whose viewer has been asked for, so
  // one process is asked once and a restart is asked again.
  playViewAsked: "",
  // Off by default: promoting a clip copies a file into the records, and a
  // setting that writes to disk should be one you turned on.
  keepClips: localStorage.getItem("rlmcp.keepClips") === "1",
  keepingClip: "",
  // The url a click asked to play. Cleared once played, so a repaint does
  // not restart a clip you have since paused.
  playWanted: "",
  keptPlaying: "",
  // The task, because a media key starts with a record id and ids are per
  // store: with several records roots, `001/videos/...` resolves in more than
  // one of them and the first hit is another task's clip.
  recordAssetUrl: (k) => `/api/record-asset?key=${encodeURIComponent(k)}`
    + `&task=${encodeURIComponent(state.task || "")}`,
  framePicked: false,     // true once you chose a frame from the strip
  // Which envs the verbs act on: rlmcp's `where` criteria, or null for
  // all of them. The vocabulary is the run's, never ours.
  where: null, scopeOpen: false,
  artifactUrl: (p) => `/api/artifact?path=${encodeURIComponent(p)}`,
  params: null, paramsStamp: null, paramFilter: "",
  traces: null, tracesStamp: null, tracePicked: "",
  // How every parameter moved across the task's ancestry, from rl-mcp's own
  // `build_history`, and which key the chart is showing.
  history: null, historyStamp: null, historyKey: "", historyAll: false,
  pendingEdit: null,     // a parameter change awaiting confirmation
  diff: null, diffStamp: null, diffShowAll: false,
  // Whether the Code view is showing the whole comparison or just its
  // one-line summary.
  changedOpen: false,
  tb: null, tbStamp: null, tbFilter: "", tbCollapsed: {},
  // Which half of the merged views you are reading. Not persisted: they are
  // where you are, not how you like things -- the distinction the settings
  // file's own comment draws.
  // TensorBoard first. `published` is the handful a run nominates and it is
  // often empty or stale early on, where the trainer's own scalars are already
  // forty deep -- so opening on it would ask the reader to switch before they
  // could look at anything.
  plotSource: "logged",
  recordsMode: "story",
  // The ancestry fold of the selected run, and what it was read
  // for. Keyed on run *and* task, because an id alone names two runs when the
  // studio has two records roots.
  recipe: null, recipeStamp: null,
  // A chart opened large, and the smoothing every chart honours. Global
  // because the question is "how noisy is this run", not "this series".
  zoom: null,
  // A verification frame opened large, in the app rather than in a tab the
  // studio is not in: {path, policy, index}. The strip it belongs to is read
  // back out of `build` on every paint -- see `frameOverlay` in build.js --
  // so this holds which frame, never the frames.
  frameZoom: null,
  smoothing: Number(localStorage.getItem("rlmcp.smoothing") || 0),
  sourceRoot: "",
  // Closing a run out: the three sentences, the measurements, and what rl-mcp
  // said if it refused. Per run, like every other draft here -- a headline
  // half-typed about one run must not appear over another.
  closeVerdict: "", closeHeadline: "", closeOutcome: "", closeMetrics: "",
  closeBusy: false, closeError: "", closeReport: "",
  // Whether the close-out form is showing. Shut by default: the agent writes
  // the record, and this is the escape hatch for when it cannot.
  closeOpen: false,
  codeDraft: "",         // the sign-in code, kept while the tree is rebuilt
  // A box being drawn on a frame, and what is being said about it. In the
  // store because the live feed repaints the tree underneath a half-drawn box.
  annot: null,           // { path, box: {x, y, w, h} } while one is being drawn
  annotNote: "", annotKind: "observe", annotAsk: true,
  annotBusy: false, annotError: "",
  graph: null,
  graphTask: null,
  pickedNode: null,
  agent: null,          // provider status: available, reason, provider
  doctor: undefined,    // /api/doctor: what the host has; null = asked, no answer
  headline: {},         // per task: which metrics the cards show, and how many points (settings `ui.headline`)
  headlinePick: null,   // the task whose metric picker is open, or null
  transcript: [],       // this run's conversation, mirrored from the server
  agentTools: null,     // whether it has rlmcp tools, and why not — preflight.py
  agentWho: "",         // the provider this conversation runs on; "" means the current one
  pending: [],          // permission requests still awaiting an answer
  agentBusy: false,
  // A stop asked for and not yet admitted. The tail of the interrupted turn
  // keeps arriving for a second or two, and every entry that is not `idle` or
  // `stopped` otherwise means "still working" -- which would turn Send back
  // into Stop and say "working…" again, right after you pressed it.
  agentStopping: false,
  thread: "main",
  // The conversation you are in, if you chose one: a subject string, the same
  // shape `subject()` returns. Empty means "follow the view", which is what a
  // studio opens as and what the "follow" button in the thread bar restores.
  // Deliberately not persisted: a pin is about what you are doing now.
  convPin: "",
  threads: {},           // subject -> conversations started here, before the server knows
  conversations: [],
  // What the pre-scoping migration parked in `_unscoped/`, when there is any:
  // {count, path, names}. The studio says it at boot on a stdout a detached
  // process does not have, so the conversation menu says it too.
  retired: null,
  // Dismissed for this session; `localStorage` remembers it for good.
  retiredHidden: false,
  // Whether the agent panel's conversation menu is open.
  convOpen: false,
  // The composer's three menus, and what the model has spent. Only one
  // opens at a time -- they sit side by side and overlap.
  modeOpen: false, modelOpen: false, harnessOpen: false, providerOpen: false,
  usage: null,
  // A message typed over a running turn, on its way once the server has
  // ended that turn. Drawn as a faint bubble until it comes back as `user`.
  sending: "",
  // The prompt this conversation was started with, once somebody has asked to
  // read it: {live, sections, cwd, mcp, ...} from `/api/agent/…/briefing`.
  // Null while the window is shut, which is also how it is closed -- there is
  // nothing here worth keeping between two openings, and a stale copy of a
  // prompt is the one thing this window must never show.
  briefing: null,
  briefingBusy: false, briefingError: "",
  // The terminal drawer. Closed by default and *not* remembered across a
  // reload: a shell is a process, and reopening the app should not silently
  // fork one.
  //
  // One entry per open shell -- `{id, name, live, error}` -- and which of them
  // the drawer is showing. Only what the tab strip has to *draw*: the screens
  // and the sockets stay in `terminal.js`, because a repaint that owned them
  // would take the scrollback with it.
  termOpen: false, terms: [], termActive: "",
  // A screenshot on its way to the agent: the capture under review, whether
  // the box tool is on (off by default), what could not be captured, and the
  // one attached to the message being typed.
  shot: null, shotAnnotate: false, shotError: "", attached: null,
  // The machine underneath: what nvidia-smi last said, and whether the panel
  // is open. Polled only while the studio is being looked at -- see main.js.
  gpu: null, gpuOpen: false, gpuHistory: {},
  // The settings window: whether it is open, what the file says, the two
  // drafts, and what the last save reported. `setRecords`/`setPackages` are
  // null until typed in, so an untouched box renders from the file rather
  // than from an empty string that would look like "you have no roots".
  settingsOpen: false, settings: null, settingsBusy: false,
  settingsError: "", settingsRestart: null,
  setRecords: null, setPackages: null,
  attention: { needs_you: 0, working: 0, total: 0 },
  agentError: "",
  showAllFields: "",     // the tool result whose extra fields are expanded
  draft: "",            // composer text, kept across repaints
  agentOpen: localStorage.getItem("rlmcp.agent") !== "0",
  lastSeq: 0,
};

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ── when a repaint happens ────────────────────────────────────────────────
//
// One paint per animation frame. Selecting a run calls `set` four times and a
// live run streams status, metrics, events and artifacts within the same tick;
// without this each one would rebuild the tree on its own.
//
// Painting is never held back while a pointer is down, an IME composition is
// open or a `<select>` popup is showing: `ui.js` keeps the nodes across a
// repaint, so there is nothing in those moments for a paint to destroy.

let queued = false;
let frame = 0;
let timer = 0;

function flush() {
  cancelAnimationFrame(frame);
  clearTimeout(timer);
  frame = timer = 0;
  queued = false;
  for (const fn of listeners) fn(state);
}

export function set(patch) {
  Object.assign(state, patch);
  if (queued) return;
  queued = true;
  frame = requestAnimationFrame(flush);
  // A frame callback only runs while the page is compositing: a hidden tab, a
  // minimised window, a viewport the browser has stopped drawing. On rAF
  // alone the app then stops repainting *permanently* -- `queued` stays true,
  // every later change is swallowed, and the page looks alive because the
  // data is still arriving and the numbers in the DOM are the old ones. It
  // reads as a crash, and only a reload clears it. So: whichever of the two
  // fires first paints, and both are cancelled together.
  timer = setTimeout(flush, 250);
}

/** Fetch a view's payload once per stamp, and keep the answer it already has
 *  on screen while a re-read of the *same* subject is in flight.
 *
 * Clearing the payload before every fetch is right when the subject changes
 * -- the previous run's report under the new run's heading is worse than an
 * empty pane -- and wrong for a re-read, which is the ordinary case here: an
 * agent rewrites the document you are reading, and Overview polls every four
 * seconds for as long as it is being written. A cleared view spends a beat on
 * "reading the task…" each time, so a document being written would blink once
 * a poll.
 *
 * `key + "For"` is the stamp the payload on screen was fetched under, which is
 * what tells a re-read apart from a change of subject. `key + "Stamp"` stays
 * what it was: the fetch in flight, set to null by a refresh to force one.
 */
export function loadOnce(key, stamp, run) {
  if (state[key + "Stamp"] === stamp) return;
  const sameSubject = state[key + "For"] === stamp;
  set({ [key + "Stamp"]: stamp, [key + "For"]: stamp,
        ...(sameSubject ? {} : { [key]: null }) });
  run().then(value => {
    if (state[key + "Stamp"] === stamp) set({ [key]: value });
  }).catch(err => {
    if (state[key + "Stamp"] === stamp) set({ [key]: { error: err.message } });
  });
}

/** Fetch when the stamp moves, and keep what is on screen while it does --
 *  even when the stamp moved because the subject itself changed.
 *
 * The Library's stamp counts the runs it knows about, so *anything* writing to
 * the records moves it: an agent's note, a headline, a verdict, the run in
 * front of you closing. Clearing on any of those would drop the shelf and the
 * card to "reading…" and rebuild both from nothing, which with a live run is a
 * page that blinks while you try to read it and takes the scroll position of
 * both panes with it. So the old answer stays drawn until the new one lands.
 * It is a *stale* answer for as long as the request is in flight, and that is
 * the trade worth making here: the payload replacing it is nearly always
 * identical, and where it differs it differs by a line.
 */
export function revalidate(key, stamp, run) {
  if (state[key + "Stamp"] === stamp) return;
  set({ [key + "Stamp"]: stamp, [key + "For"]: stamp });
  run().then(value => {
    if (state[key + "Stamp"] === stamp) set({ [key]: value });
  }).catch(err => {
    if (state[key + "Stamp"] === stamp) set({ [key]: { error: err.message } });
  });
}

/** Paint now, skipping the frame wait. For the few callers that need it. */
export function paintNow() {
  queued = false;
  flush();
}

// ── preferences that outlive this browser ─────────────────────────────────
//
// `localStorage` is the fast half: it is read before the first paint, so the
// app opens in your theme instead of flashing the default one on the way to
// it. The studio's settings file is the durable half -- it belongs to *you*
// rather than to one browser, which is the difference between a preference and
// a cookie. Every setter writes both, and neither is enough alone.
//
// Only *appearance* is mirrored. Which view you were on and which task you had
// open are "where you were", not "how you like it", and a url already owns
// them -- a link into a named state that the settings file then overrode would
// be a link that does not work.
const MIRRORED = {
  headline: (v) => {
    if (!v || typeof v !== "object") return;
    set({ headline: v });
  },
  scheme: (v) => {
    if (typeof v !== "string") return;
    localStorage.setItem("rlmcp.scheme", v);
    document.documentElement.setAttribute("data-scheme", v);
    set({ scheme: v });
  },
  accent: (v) => {
    if (!ACCENTS.includes(v)) return;
    localStorage.setItem("rlmcp.accent", v);
    document.documentElement.setAttribute("data-accent", v);
    set({ accent: v });
  },
  layout: (v) => {
    if (!LAYOUT_IDS.has(v)) return;
    localStorage.setItem("rlmcp.layout", v);
    set({ layout: v });
  },
  panels: (v) => {
    if (!v || typeof v !== "object") return;
    localStorage.setItem("rlmcp.panels", JSON.stringify(v));
    set({ widths: loadWidths() });
  },
  robotMode: (v) => {
    if (typeof v !== "string") return;
    localStorage.setItem("rlmcp.robotMode", v);
    set({ robotMode: v });
  },
  clipsOpen: (v) => {
    localStorage.setItem("rlmcp.clipsOpen", v ? "1" : "0");
    set({ clipsOpen: !!v });
  },
  keepClips: (v) => {
    localStorage.setItem("rlmcp.keepClips", v ? "1" : "0");
    set({ keepClips: !!v });
  },
  smoothing: (v) => {
    if (typeof v !== "number" || !isFinite(v)) return;
    localStorage.setItem("rlmcp.smoothing", String(v));
    set({ smoothing: v });
  },
  agentOpen: (v) => {
    localStorage.setItem("rlmcp.agent", v ? "1" : "0");
    set({ agentOpen: !!v });
  },
};

const pendingSettings = {};
let settingsTimer = null;

/** Remember one preference in the studio's settings file.
 *
 * Batched, because a divider drag changes a width many times a second and the
 * far end is a file. One PATCH once things go quiet, carrying whatever changed
 * -- and a patch rather than the whole document, so two tabs on the same
 * studio do not each save their own idea of every other setting.
 */
export function settingChanged(key, value) {
  pendingSettings[key] = value;
  clearTimeout(settingsTimer);
  settingsTimer = setTimeout(flushSettings, 500);
}

async function flushSettings() {
  const ui = { ...pendingSettings };
  for (const key of Object.keys(pendingSettings)) delete pendingSettings[key];
  if (!Object.keys(ui).length) return;
  try {
    await fetch("/api/settings", {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ ui }),
    });
  } catch {
    // Failing to save a preference is not worth an error in front of the work.
    // It is still in `localStorage` for this browser, and the next change
    // tries again.
  }
}

/** Adopt the settings file's view of the GUI. Called once, at boot.
 *
 * The file wins over this browser, because it is the half that followed you
 * here. An *empty* file goes the other way: a studio that has never been told
 * anything adopts what this browser already had, so the first run after an
 * upgrade does not reset every panel you had sized. That migration happens
 * once, and thereafter the file is simply the answer.
 */
export function applySettings(ui) {
  const saved = ui && typeof ui === "object" ? ui : {};
  const known = Object.keys(saved).filter(k => k in MIRRORED);

  if (!known.length) {
    for (const [key, value] of Object.entries(currentPreferences())) {
      settingChanged(key, value);
    }
    return;
  }
  for (const key of known) MIRRORED[key](saved[key]);

  // A file that says nothing about the layout is answered by the *default*,
  // not by whatever this browser happens to have cached. Without this the
  // default would be unreachable: once any browser has written `rlmcp.layout`,
  // clearing the preference from the file would leave that browser opening in
  // its own last choice for good.
  if (!LAYOUT_IDS.has(saved.layout)) MIRRORED.layout(DEFAULT_LAYOUT);
}

/** What this browser currently believes, in the file's shapes. */
function currentPreferences() {
  return {
    scheme: state.scheme,
    accent: state.accent,
    layout: state.layout,
    panels: state.widths,
    robotMode: state.robotMode,
    clipsOpen: !!state.clipsOpen,
    keepClips: !!state.keepClips,
    smoothing: Number(state.smoothing) || 0,
    agentOpen: !!state.agentOpen,
  };
}

/** Switch layout.
 *
 * `remember` is the whole point of this signature. If every switch wrote the
 * settings file, the layout the studio *opened* in would be whichever one
 * anybody had clicked last, in any browser -- and with several browsers and
 * several agents pointed at one studio, "your default" would regularly be
 * somebody else's experiment. Two people looking at two layouts
 * cannot both be right about what the file should say, and the switch in the
 * bar is not a claim about the future anyway: it is "show me this one now".
 *
 * So the bar switches for this visit and writes nothing, and the picker in
 * Settings -- which says out loud that it is the opening layout -- is the one
 * that remembers. `localStorage` follows the *remembered* value only, so a
 * reload is not a flash of the temporary one before the file corrects it.
 */
export function setLayout(layout, { remember = false } = {}) {
  if (!LAYOUT_IDS.has(layout)) return;
  if (remember) {
    localStorage.setItem("rlmcp.layout", layout);
    settingChanged("layout", layout);
  }
  set({ layout });
}

export function setScheme(scheme) {
  localStorage.setItem("rlmcp.scheme", scheme);
  settingChanged("scheme", scheme);
  document.documentElement.setAttribute("data-scheme", scheme);
  set({ scheme });
}

export function setAccent(accent) {
  if (!ACCENTS.includes(accent)) return;
  localStorage.setItem("rlmcp.accent", accent);
  settingChanged("accent", accent);
  document.documentElement.setAttribute("data-accent", accent);
  set({ accent });
}

export function setView(view) {
  if (!VIEW_IDS.has(view)) return;
  localStorage.setItem("rlmcp.view", view);
  set({ view });
}

export function setAgentOpen(open) {
  localStorage.setItem("rlmcp.agent", open ? "1" : "0");
  set({ agentOpen: open });
}


// ── headline metrics, per task ───────────────────────────────────────────
//
// Which series the metric cards show and how many recent points each chart
// covers, remembered per task in the settings file (`ui.headline`), so the
// choice follows you to another browser and stays with the task rather than
// with whatever run is open. `keys` absent means "auto": the ranking in
// `components.js`, which the run's own nominations drive.

/** How many recent points a chart covers. The first is the default. */
export const WINDOWS = [240, 500, 1000, 2000, 5000];

export function headlinePrefs(task) {
  const saved = (state.headline || {})[task] || {};
  const keys = Array.isArray(saved.keys) ? saved.keys.filter(k => typeof k === "string") : [];
  const window = WINDOWS.includes(Number(saved.window)) ? Number(saved.window) : WINDOWS[0];
  return { keys: keys.length ? keys : null, window };
}

/** Remember a change for one task: `{ keys }`, `{ window }`, or `{ keys: null }` for auto.
 *
 * The file is sent a *delta* -- this task's entry, or null to clear it -- and
 * the server merges per task, so two tabs on two tasks never overwrite each
 * other's choice (settings.py). */
export function setHeadline(task, change) {
  if (!task) return;
  const current = { ...((state.headline || {})[task] || {}), ...change };
  if (!current.keys || !current.keys.length) delete current.keys;
  if (!current.window || current.window === WINDOWS[0]) delete current.window;
  const all = { ...(state.headline || {}) };
  if (Object.keys(current).length) all[task] = current; else delete all[task];
  set({ headline: all });
  settingChanged("headline", { [task]: Object.keys(current).length ? current : null });
}

/** The `tail` the session route is asked for, so the x axis is the window. */
export function tailFor(task) {
  return headlinePrefs(task).window;
}
