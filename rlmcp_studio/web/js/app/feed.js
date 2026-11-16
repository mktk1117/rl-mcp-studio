// The event feed: what each kind arriving on the socket re-reads, coalesced so
// that a burst of events is one request.

import { state, set, subject } from "../state.js";
import { refreshDocs } from "../docs.js";
import { refreshOverview } from "../overview.js";
import { refreshLibrary } from "../library.js";
import { refreshRecipe } from "../recipe.js";
import { reloadRuns, refreshUnrecorded, contextWindow, refreshConversations,
         refreshTasks, refreshClips, refreshGraph, refreshLive } from "./load.js";

// Event kinds dispatch through a registry for the same reason: a new kind is a
// new entry, never an edit to a growing switch statement.
export const HANDLERS = {
  "hello": () => {},
  // Four kinds, one fetch. A live run emits status, metrics, events and
  // artifacts within the same tick; a request per event would send the same
  // `/api/session` four times a second.
  "status.tick": () => soon(refreshLive),
  "metrics.append": () => soon(refreshLive),
  "events.append": () => soon(refreshLive),
  "artifacts.changed": () => { soon(refreshLive); soon(refreshClips); },
  // A `.md` was written under a watched root. The listing and the document on
  // screen are both re-read: an agent rewriting the report you have open is
  // the ordinary case here, not the unusual one.
  "docs.changed": () => { soon(refreshDocs); soon(refreshOverview); },
  // The card is built from the records, so a record written while you are
  // reading one is a card that has gone stale on screen -- and the recipe
  // folds the whole ancestry, so a write *anywhere* in the store can change
  // it, not only a write to the run in front of you.
  "records.changed": () => { soon(reloadRuns); soon(refreshClips); soon(refreshUnrecorded);
                             soon(refreshLibrary); soon(refreshRecipe); soon(refreshGraph);
                             soon(refreshTasks); },
  "sessions.rescanned": () => soon(refreshUnrecorded),
  // A trainer started or stopped. Nothing else announces a *stop*: the process
  // dies, its files stop changing, and every handler above is driven by a file
  // changing -- so without this the page keeps saying `training` beside numbers
  // that had stopped moving. The run on screen and the rail both re-read,
  // because either can be the one that died.
  "liveness.changed": () => { soon(refreshLive); soon(reloadRuns); soon(refreshTasks); },

  // The agent's events arrive on the same socket and the same envelope as a
  // metric row. It is not a special transport.
  "agent.user": (e) => appendAgent(e),
  // A turn the studio did not start: the CLI woke the agent by itself, which
  // it does when a background shell it launched finishes. It arrives on the
  // same socket as everything else and is a turn like any other -- the only
  // reason it has its own kind is that the pane has to say why the agent
  // started talking, or the answer reads as a reply to whatever you asked last.
  "agent.wake": (e) => appendAgent(e),
  "agent.text": (e) => appendAgent(e),
  "agent.thinking": (e) => appendAgent(e),
  "agent.tool_call": (e) => appendAgent(e),
  "agent.tool_result": (e) => appendAgent(e),
  "agent.decision": (e) => appendAgent(e),
  "agent.error": (e) => appendAgent(e),
  // The credential expired mid-turn. A transcript entry like any other, so it
  // survives a reload and sits under the turn it ended -- and it is the entry
  // that carries the sign-in controls. See `RENDER.auth_expired`.
  "agent.auth_expired": (e) => appendAgent(e),
  // The conversation is getting too large to send, or a turn just died of it.
  // See `RENDER.heavy`.
  "agent.heavy": (e) => appendAgent(e),
  "agent.permission": (e) => appendAgent(e, { addPending: true }),
  "agent.stopped": (e) => appendAgent(e),
  "agent.auth": (e) => set({ agent: { ...state.agent, ...e.data } }),
  // What the last turn cost, for the composer's model button. The context
  // window is the provider's business and is not reported per-turn, so the
  // client keeps the running total and the *window* comes from the model
  // table -- a number with nothing to compare it against answers nothing.
  "agent.done": (e) => {
    const usage = (e.data || {}).usage;
    if (usage && (usage.used || usage.context_tokens)) {
      set({ usage: { ...usage, context: usage.window || contextWindow() } });
    }
  },
  "agent.idle": (e) => {
    refreshConversations();
    // The report thread going quiet is the file landing (or the agent giving
    // up): the Overview re-reads either way, whether or not its poll is
    // running -- it only runs while the tab is on screen.
    if (/^task:.*#report$/.test(e.scope || "")) soon(refreshOverview);
    // `subject()`, not `state.runId` -- the same distinction the transcript
    // handler below spells out. A Build conversation is scoped
    // `task:<name>#main`, which never equals `007#main`, so keyed on the run
    // the flag would never clear: the panel would stay "working" for the rest
    // of the session and refuse every later message with "still working on
    // the last message".
    // And only for an `idle` that is not older than what is already on screen.
    // The socket replays its backlog when it connects, so reloading the page
    // in the middle of a turn delivers the *previous* turn's `idle` after the
    // transcript has been fetched: the panel would go quiet while the agent is
    // still working, and Stop -- the one control that turn wants -- would not
    // be drawn at all. The transcript is sorted by `seq`, so the last entry's
    // is the newest thing known about this conversation.
    const seq = (e.data || {}).seq;
    const newest = state.transcript.length
      ? (state.transcript[state.transcript.length - 1].seq ?? -1) : -1;
    const stale = seq !== undefined && seq < newest;
    if (e.scope === `${subject()}#${state.thread}` && !stale) {
      set({ agentBusy: false });
    }
  },
};

// Coalesce repeated calls to the same function into one, on the next tick.
// The socket delivers a burst per training iteration; the answer to all of it
// is the same single request.
const soonest = new Map();

function soon(fn, ms = 120) {
  if (soonest.has(fn)) return;
  soonest.set(fn, setTimeout(() => { soonest.delete(fn); fn(); }, ms));
}

function appendAgent(envelope, { addPending = false } = {}) {
  soon(refreshConversations, 400);
  // The scope is `subject#thread`, so an event for a conversation you are not
  // looking at updates the rail's indicator without touching the transcript.
  // It must be `subject()` and not `state.runId`: in Build the subject is
  // `task:<id>` and there is no run at all, so keying on the run id drops
  // every entry the task's own agent produces -- its text, its tool calls and,
  // worst, its permission cards, which leaves the agent waiting on an approval
  // the screen never offers.
  if (envelope.scope !== `${subject()}#${state.thread}`) return;
  const entry = envelope.data;
  // The socket replays its backlog when it connects, and the transcript was
  // also just fetched over HTTP -- so without this, after a reload every
  // message, tool call and answer appears twice. `seq` is the conversation's
  // own index: one entry, one seq, whichever way it arrived first.
  if (entry.seq !== undefined
      && state.transcript.some(e => e.seq === entry.seq && e.kind === entry.kind)) {
    return;
  }
  const transcript = [...state.transcript, entry]
      .sort((a, b) => (a.seq ?? 0) - (b.seq ?? 0));
  const pending = addPending ? [...state.pending, entry] : state.pending;
  const answered = entry.kind === "decision"
    ? pending.filter(p => p.request_id !== entry.request_id)
    : pending
      // A stop abandons the turn, and with it anything it was waiting to be
      // allowed to do. A card still asking about a tool call nobody will make
      // is a question with no answer that changes anything.
      .filter(() => entry.kind !== "stopped");
  // The tail of a turn you stopped is not evidence of work in progress: it is
  // what the model had already produced when the interrupt reached it, and it
  // arrives after the button was pressed. `agentStopping` holds until the turn
  // admits it ended.
  const ended = DONE_WORKING.has(entry.kind);
  // A new message starts a turn, so it closes any stop still in flight too --
  // otherwise a correction typed straight after the button would look idle
  // while the agent answered it.
  const stopping = state.agentStopping && !ended && entry.kind !== "user";
  set({ transcript, pending: answered,
        agentStopping: stopping,
        agentBusy: !ended && !stopping });
}

// The kinds that mean the agent is not working any more. `idle` ends a turn
// that ran to the end; `stopped` ends one you ended. Anything else arriving is
// evidence of work in progress.
const DONE_WORKING = new Set(["idle", "stopped"]);
