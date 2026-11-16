// Loading what the app shows: runs, the selected run, conversations, the GPU,
// clips and tasks. Each loader writes the store, and the paint follows.

import { state, set, subject, tailFor } from "../state.js";
import { api } from "../api.js";
import { refreshBuild } from "../build.js";

export async function refreshDoctor() {
  try {
    set({ doctor: await api.doctor() });
  } catch {
    set({ doctor: null });
  }
}

/** The played policy, and the 3-D view it publishes.
 *
 * Two reads, for the same reason the builder's preview needs two: the process
 * description says whether it is up, and the *session* it holds is where its
 * `live_view` url lives. Polled while it is up and left alone when it is not,
 * so a run nobody is playing costs one request when you open it.
 */
let playTimer = null;

export async function refreshPlay() {
  const run = state.runId;
  if (!run) { set({ play: null, playLive: null }); return; }
  try {
    const answer = await api.playState(run);
    if (state.runId !== run) return;
    const play = answer.play || null;
    // Only when the answer moved. This polls every five seconds, and setting
    // the store is a full repaint of the app -- the Plots wall, the
    // transcript, the rail -- for a `play` that has not changed since the
    // last poll. On a large window that is a 50-100ms relayout every five
    // seconds, for nothing.
    if (JSON.stringify(play) !== JSON.stringify(state.play)) set({ play });

    const session = (play || {}).session;
    if (session && play.state === "holding") {
      const snapshot = await api.session(session, 1).catch(() => null);
      if (state.runId !== run) return;
      set({ playLive: snapshot });

      // Pressing "Play the policy" is a request to *see* it. The process holds
      // the environment; the viewer is a second, separate yes, and asking for
      // it in another click -- under panel text written about a trainer that
      // is not there -- would be the app being pedantic about its own seams.
      // Asked once per played process: `playViewAsked` is the process's start
      // time, so a restart asks again and a repaint does not.
      const view = ((snapshot || {}).status || {}).live_view || null;
      const stamp = String(play.started_at || "");
      if (view && !view.running && state.playViewAsked !== stamp) {
        set({ playViewAsked: stamp });
        try {
          await api.play(run, { action: "view", enabled: true });
        } catch (err) {
          if (state.runId === run) set({ playError: err.message });
        }
      }
    } else if (state.playLive) {
      set({ playLive: null });
    }
  } catch {
    // A studio with no play routes is a studio where this stays off, which is
    // what `play: null` already means. Nothing to report.
  }
  clearTimeout(playTimer);
  const busy = (state.play || {}).state === "starting";
  if (state.runId === run) playTimer = setTimeout(refreshPlay, busy ? 1200 : 5000);
}


/** Which task to open on.
 *
 * The list arrives ordered by how many runs a task has, and the biggest can be
 * a legacy `(unknown)` bucket of old records nobody is working on -- opening on
 * it shows the oldest thing in the store. In order: the task you were last
 * looking at, a task with something actually training, then whichever has the
 * newest record.
 */
export function firstTask(tasks) {
  if (!tasks.length) return null;
  const remembered = localStorage.getItem("rlmcp.task");
  if (remembered && tasks.some(t => t.task === remembered)) return remembered;
  const live = tasks.find(t => t.live > 0);
  if (live) return live.task;
  // A registered task with no runs is a real entry, and opening on one
  // would show an empty app to somebody who has fifty recorded runs.
  const withRuns = tasks.filter(t => t.runs > 0);
  if (!withRuns.length) return tasks[0].task;
  const newest = [...withRuns].sort(
      (a, b) => ((b.latest && b.latest.seq) || 0) - ((a.latest && a.latest.seq) || 0))[0];
  return newest.task;
}

export async function reloadRuns() {
  if (!state.task) { set({ runs: [] }); return; }
  const task = state.task;
  const { runs } = await api.runs(task);
  if (state.task !== task) return;     // a newer task switch already won
  const keep = runs.some(r => r.id === state.runId) ? state.runId : null;
  set({ runs });
  // The records changing is not you changing runs. Re-selecting the run you
  // are already on would throw away the frame you picked, the clip you pinned,
  // the diagnosis you ran and the box you were drawing -- every time anything
  // wrote to the ledger, including your own feedback.
  if (keep) { await refreshRun(keep); return; }
  // Land on what is *training* when something is, not on the newest record:
  // the newest can be a retry that died in seconds while the run actually on
  // the GPU sits a row below it.
  const live = [...runs].reverse().find(r => r.live === true);
  await selectRun(live ? live.id : runs.length ? runs[runs.length - 1].id : null);
}

/** Re-read the run you are already looking at, changing nothing you chose. */
export async function refreshRun(id) {
  const detail = await api.run(id, state.task);
  if (state.runId !== id) return;
  const live = detail.live || null;
  set({ detail, live, frames: framesOf(live) });
  refreshClips();
}

function framesOf(live) {
  return live && live.artifacts
    ? live.artifacts.map(a => ({ ...a, path: `${live.dir}/artifacts/${a.name}` }))
    : [];
}

export async function selectRun(id) {
  lastLive = lastClips = "";        // a different run: nothing carries over
  // A played policy belongs to the run it was played from. Carrying its viser
  // frame into the next run would show one run's robot under another's name.
  clearTimeout(playTimer);
  if (!id) {
    set({ runId: null, detail: null, live: null, play: null, playLive: null });
    return;
  }
  set({ runId: id, play: null, playLive: null, playError: "" });
  refreshPlay();
  // With more than one records root, an id alone is ambiguous -- both stores
  // number from 001. The task is what tells them apart.
  const detail = await api.run(id, state.task);
  // Ignore a response that lost the race against a newer selection.
  if (state.runId !== id) return;
  const live = detail.live || null;

  // A run reached from somewhere that is not the rail -- the record tree, a
  // conversation, an adopted session -- may belong to another task, and the
  // rail is filtered by task. Selecting one without switching task leaves the
  // app split in half: the status bar shows the run's numbers while the stage
  // says "no run selected", because the run is not in the filtered list the
  // stage looks in.
  if (detail.record && detail.record.task && detail.record.task !== state.task) {
    // Everything keyed to the old task goes with it -- the same list
    // `selectTask` clears. Without this, Story and Tree keep drawing the
    // *previous* task's records beside a rail showing the new one's.
    set({ task: detail.record.task,
          graph: null, graphTask: null, pickedNode: null,
          source: null, sourceStamp: null, filePath: "", file: null,
          diff: null, diffStamp: null, against: "" });
    await reloadRuns();
    if (state.runId !== id) return;
  }

  // A box drawn on another run's frame must not come back when you return to
  // this one: it would read as an observation about the run you are looking at
  // now, which is the one thing an annotation must never do.
  set({ detail, live, frames: framesOf(live), lastFrame: null, framePicked: false,
        diagnosis: null,
        clips: null, clipUrl: "", clipPicked: false,
        annot: null, annotNote: "", annotError: "",
        // A remark belongs to the run you wrote it about. Carried over, a
        // half-typed observation about one run, and the kind picked for it,
        // sits in the box on the next -- over a card reading "nothing said
        // about this run yet" -- and pressing Add writes it there. Same for
        // the question half-typed to the agent, and for the error from the
        // last failed write.
        fbDraft: "", fbKind: "observe", fbHint: "", draft: "",
        // The same leak, in a box that writes a *record*: the slug and the
        // hypothesis typed for one run would come up under another run's name.
        // `pendingEdit` is a parameter value and its rationale -- equally
        // wrong to carry across.
        nextSlug: "", nextWhy: "", nextError: "", nextFiles: [], nextRecord: false,
        pendingEdit: null,
        // And the close-out, which is the same leak with the highest cost: a
        // headline and a verdict typed about one run, applied to another.
        closeVerdict: "", closeHeadline: "", closeOutcome: "", closeMetrics: "",
        closeBusy: false, closeError: "", closeReport: "",
        // A command that failed belongs to the run it was sent to. Carried
        // over, an archived run in another task would show "Timed out after
        // 120s" for something that happened somewhere else.
        commandError: "", running: {}, agentError: "",
        transcript: [], pending: [], agentBusy: false, agentStopping: false });
  // The live inputs are cleared too: they are kept nodes, so a repaint alone
  // would not empty them.
  // `.close-out` is in the list for the same reason: its boxes are kept nodes
  // too, and a headline typed about one run sitting over another is the worst
  // version of this bug -- the button next to it writes the record.
  for (const box of document.querySelectorAll(
      ".fb-row .fb-input, .agent-input, .close-out .fb-input")) {
    box.value = "";
  }
  refreshClips();

  // The conversation lives on the server, so switching back to a run picks it
  // up where it was rather than showing an empty pane.
  await openHere(subject());
  refreshConversations();
}

/** The conversation to show for `who`, and show it.
 *
 * Passing `state.thread` straight through would make the thread *name*
 * follow you from run to run: start `t1127` on run A, click run B, and the
 * panel opens `B#t1127` -- a conversation that never existed -- with the real
 * one a thread away and nothing saying so. A thread belongs to a subject.
 * Here it is the one you last had open on this subject in this session, else
 * the one most recently spoken to on the server (the list is sorted that
 * way), else `main`.
 */
function threadFor(who) {
  const opened = (state.lastThread || {})[who];
  if (opened) return opened;
  const known = (state.conversations || []).find(c => c.run === who);
  return known ? known.thread : "main";
}

export async function openHere(who) {
  const thread = threadFor(who);
  set({ thread, lastThread: { ...(state.lastThread || {}), [who]: thread } });
  await loadThread(who, thread);
}

export async function loadThread(who, thread) {
  if (!who) {
    set({ transcript: [], pending: [], agentBusy: false, agentStopping: false });
    return;
  }
  try {
    const agent = await api.agentTranscript(who, thread);
    if (subject() !== who || state.thread !== thread) return;
    // Whatever the socket delivered while this fetch was in flight is *newer*
    // than the answer to it, and replacing the list with the answer throws it
    // away. A stop lands in exactly that window -- `stopAgent` refreshes as
    // its last act, and "You stopped it." is published a moment after the
    // request returns -- so the pane would stop the agent and never say so.
    // Safe to keep: this runs only for the conversation on screen, and
    // switching to another one empties the list first.
    const fetched = agent.transcript || [];
    const newest = fetched.length ? (fetched[fetched.length - 1].seq ?? -1) : -1;
    const later = (state.transcript || []).filter(e => (e.seq ?? -1) > newest);
    set({ transcript: [...fetched, ...later], pending: agent.pending || [],
          agentBusy: !!agent.busy,
          // The server is the authority on whether a turn is *running*, and
          // says so. It cannot say "stopping": it reports a stopped turn as
          // not busy from the moment the button is pressed, so a refresh --
          // and `stopAgent` ends with one -- must not close that window
          // itself, or the tail arriving a second later reopens it.
          agentStopping: agent.busy ? false : state.agentStopping,
          // What a conversation here has, or would be given if you started
          // one -- not a claim that the tools are attached to whatever run
          // you are looking at, which is false for every run that has never
          // been launched.
          agentTools: agent.tools || null,
          agentWho: agent.provider || "" });
  } catch {
    set({ transcript: [], pending: [], agentBusy: false, agentStopping: false,
          agentTools: null, agentWho: "" });
  }
}

export async function refreshUnrecorded() {
  try {
    const payload = await api.unrecorded();
    set({ unrecorded: payload.sessions || [] });
  } catch { /* the roots may not exist; the rail simply shows nothing */ }
}

/** Adopt a model or a mode, and take the server's answer as the truth.
 *
 * The reply carries the whole settings shape, so the panel redraws from what
 * was actually stored rather than from what was clicked -- a refused value
 * then shows as unchanged instead of as briefly applied.
 */
export async function chooseAgent(choice) {
  try {
    const answer = await api.agentChoose(choice);
    set({ agent: { ...state.agent, ...answer } });
  } catch (err) {
    set({ agentError: err.message });
  }
}

/** How big the answering model's context is, in tokens.
 *
 * A table here rather than from the provider, because the SDK does not report
 * it and inventing a route to ask would be a round trip for a constant. When
 * a model is not in it the button simply shows no fraction, which is the
 * honest outcome -- better than a denominator that is a guess.
 */
// The fallback only: a Claude turn reports its model's window on `done`
// (`usage.window`, from the provider's model table). A 200k fallback under a
// million-token model reads as a conversation at 360%.
const CONTEXT = { "": 1000000, "claude-opus-5-5": 1000000, "claude-opus-5": 1000000,
                  "claude-sonnet-5": 1000000, "claude-fable-5": 1000000,
                  "claude-haiku-4-5-20251001": 200000,
                  "gemini-3.8-flash": 1048576, "gemini-3.1-pro": 1048576 };

/** The composer text a fresh conversation starts with. See `freshStart`. */
export function handoff(transcript, run) {
  const last = (kind, ok = () => true) => {
    for (let i = transcript.length - 1; i >= 0; i--) {
      const e = transcript[i];
      if (e.kind === kind && (e.text || "").trim() && ok(e)) return e.text.trim();
    }
    return "";
  };
  const asked = last("user");
  const said = last("text", (e) => !/^API Error/.test(e.text.trim()));
  const quote = (t, n) => (t.length > n ? t.slice(0, n) + "\u2026" : t)
    .split("\n").map(l => "> " + l).join("\n");
  const parts = [`Carrying on from an earlier conversation on ${run || "this run"}, `
               + "which grew too large to send."];
  if (asked) parts.push("Its last request was:\n\n" + quote(asked, 800));
  if (said) parts.push("Your last word on it was:\n\n" + quote(said, 800));
  parts.push("Read the run's record and notes for the rest, then carry on.");
  return parts.join("\n\n");
}

export function contextWindow() {
  return CONTEXT[(state.agent || {}).model || ""] || 0;
}

/** What the GPUs are doing.
 *
 * On its own timer rather than on the socket: nothing publishes a GPU event,
 * and the numbers move continuously rather than at moments. Slower when the
 * panel is shut, because a closed indicator needs a figure that is roughly
 * right and each poll is a subprocess on the studio's machine.
 */
let gpuTimer = 0;

export async function refreshGpu() {
  clearTimeout(gpuTimer);
  try {
    const gpu = await api.gpu();
    set({ gpu, gpuHistory: rememberGpu(state.gpuHistory, gpu) });
    // A trainer holding the card at 100% and nearly all of its memory starves
    // the GPU compositor of a browser on the same machine: any page that
    // repaints blanks and re-rasterizes, and this one repaints every few
    // seconds by design. Measured: 3 frames a second with the pulses running,
    // 54 without, steady with GPU compositing off. The page
    // cannot free the GPU; it can stop asking it for sixty frames a second.
    // On the body, outside the reconciled tree, and read by the stylesheet.
    document.body.classList.toggle("gpu-starved", gpuStarved(gpu));
  } catch {
    // A studio with no GPU route is a studio where this stays absent, which
    // `gpu: null` already says. Nothing to report.
  }
  gpuTimer = setTimeout(refreshGpu, state.gpuOpen ? 2000 : 12000);
}

/** Whether a card on the studio's machine is too busy to also draw a browser. */
export function gpuStarved(snap) {
  if (!snap || !snap.ok) return false;
  return (snap.devices || []).some(d => (d.util || 0) >= 95 && (d.memory_frac || 0) >= 0.85);
}

/** The last minutes of each card's load, kept in the tab.
 *
 * Nothing records this -- `nvidia-smi` answers for now and only now -- so the
 * popup's sparkline is built from what this page has seen: one point per poll,
 * `[t, compute fraction, memory fraction]`, capped so an afternoon with the
 * tab open does not grow without bound. Sparse while the popup is shut (one
 * point per 12 s) and dense once it is open, which is what it is for.
 */
const GPU_HISTORY = 240;

function rememberGpu(history, snap) {
  if (!snap || !snap.ok) return history || {};
  const out = { ...(history || {}) };
  const t = snap.at || Date.now() / 1000;
  for (const d of snap.devices || []) {
    const points = (out[d.index] || []).slice(-(GPU_HISTORY - 1));
    points.push([t, d.util == null ? null : d.util / 100, d.memory_frac]);
    out[d.index] = points;
  }
  return out;
}

export async function refreshConversations() {
  try {
    const payload = await api.agentConversations();
    set({ conversations: payload.conversations || [],
          attention: payload.attention || state.attention,
          retired: payload.retired && payload.retired.count ? payload.retired : null });
  } catch { return; /* the host may not be up; the rail simply stays empty */ }
  // At boot the run is picked before this list has arrived, so the panel is
  // on `main` by default -- and if this subject's conversation is on another
  // thread, `main` is an empty pane over a full conversation. Re-pick once,
  // only while nothing has been said or chosen here.
  const who = subject();
  if (who && !state.convPin && !(state.lastThread || {})[who] && !state.transcript.length) {
    const thread = threadFor(who);
    if (thread !== state.thread) await openHere(who);
  }
}

/** The switcher's list, again. A task that appears after the page loaded --
 * an agent recording its run under a new task id -- reaches the rail
 * (records.changed reloads it), but a switcher read once at boot would never
 * show it. The registry half is cached server-side, so this costs one read of
 * the records. */
export async function refreshTasks() {
  try {
    const tasks = await api.tasks();
    set({ tasks: tasks.tasks, registry: tasks.registry || null });
  } catch { /* the list stays as it was */ }
}

export async function refreshClips() {
  const runId = state.runId;
  if (!runId) return;
  try {
    const clips = await api.clips(runId);
    if (state.runId !== runId) return;          // a newer selection won
    // Follow the newest clip until somebody picks one, then stay put.
    const clipUrl = state.clipPicked ? state.clipUrl : (clips.latest || "");
    const signature = JSON.stringify([clips.count, clips.latest, clipUrl]);
    if (signature === lastClips) return;
    lastClips = signature;
    set({ clips, clipUrl });
  } catch { /* a run with no record media simply has no strip */ }
}

// What the screen actually shows of a live session. A trainer heartbeats
// whether or not anything moved, so comparing this instead of the whole
// payload is the difference between a repaint every few seconds and a repaint
// when something happened. `status.at` is deliberately absent: it changes on
// every beat and is displayed nowhere.
function liveSignature(live, frames) {
  const status = (live && live.status) || {};
  return JSON.stringify([
    live && live.state,
    status.iteration ?? status.iter ?? null,
    status.headline_metrics || null,
    status.progress_video || null,
    status.selectors ? Object.keys(status.selectors) : null,
    (status.pending_jobs || []).length,
    (live && live.metrics || []).length,
    Object.keys((live && live.series) || {}),
    frames.map(f => `${f.name}:${f.size}`),
  ]);
}

/** The newest clip this run has produced, or "". */
export function latestClipPath() {
  const clips = ((state.clips || {}).clips) || [];
  const last = clips[clips.length - 1];
  return (last && (last.path || last.file)) || "";
}

/** The Records view's graph, re-read when a record changes.
 *
 * `ensureGraph` loads the graph once per task and keeps it, which is right
 * for a view switch and wrong for a run that starts while you are looking:
 * the rail picks the new record up on `records.changed`, and without this the
 * story and the tree would not until a reload of the whole page. Re-read only
 * when a graph for this task is on screen; a view that has not asked yet asks
 * when it does. The guard against a task switched mid-flight is the same one
 * `refreshLive` carries.
 */
export async function refreshGraph() {
  const task = state.task;
  if (!task || state.graphTask !== task || !state.graph) return;
  try {
    const payload = await api.recordGraph(task);
    if (state.task !== task) return;
    set({ graph: payload.nodes || [], graphTask: task });
  } catch { /* the store may be mid-write; the next change asks again */ }
}

export async function refreshLive() {
  const detail = state.detail;
  const runId = state.runId;
  if (!detail || !detail.record || !detail.record.session) return;
  try {
    // The window is per task (state.js `tailFor`): the x axis is exactly what
    // the route is asked for, so a longer window is a longer read, once.
    const tail = tailFor(state.task);
    const live = await api.session(detail.record.session, tail);
    // This is in flight constantly -- four socket handlers trigger it and a
    // verb awaits it. Without the guard, a run switched while it was out
    // paints the previous run's numbers under the new run's name.
    if (state.runId !== runId || state.detail !== detail) return;
    const frames = framesOf(live);
    // The window is part of what "changed": a new choice with the same beat
    // must repaint, or the cards keep the old length until the next tick.
    const signature = liveSignature(live, frames) + `|${tail}`;
    if (signature === lastLive) return;         // the beat moved, nothing else
    lastLive = signature;
    set({ live, frames });
  } catch { /* the run may have been cleaned up under us; the strip says so */ }
}

let lastLive = "";
let lastClips = "";

export async function selectTask(task) {
  // Remembered, so the next session opens where this one left off -- see
  // `firstTask`.
  localStorage.setItem("rlmcp.task", task);
  // The filter belongs to the list it was typed against. Carried across, it
  // empties the new task's rail -- and the box that would clear it is only
  // drawn for a task with more than eight runs, so on a smaller one there is
  // no way back short of a reload.
  state.runFilter = "";
  // Everything keyed to the old task goes: the record graph, the source tree,
  // the diff. `ensureGraph` only asks "is anything loaded", so a graph left
  // in place keeps Story and Tree drawing the first task's runs forever.
  set({ task, runId: null, detail: null, live: null,
        graph: null, graphTask: null, pickedNode: null,
        source: null, sourceStamp: null, filePath: "", file: null,
        diff: null, diffStamp: null, tb: null, tbStamp: null,
        // The Build panel is per task too: its gates, its preview and the
        // preview's own session all belonged to the task you just left.
        build: null, previewLive: null, buildError: "", buildBusy: "",
        commandError: "", diagnosis: null });
  await reloadRuns();
  if (state.view === "build") await refreshBuild();
}
