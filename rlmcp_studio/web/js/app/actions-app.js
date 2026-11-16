// The action table: every name a layout, view or component calls through
// `actions`. The app-wide ones are here; the rest come from a table per area.

import { state, set, setAgentOpen, setView, subject } from "../state.js";
import { api } from "../api.js";
import { openReport, closeReport, buildReport } from "../report.js";
import { refreshBuild } from "../build.js";
import { connect as termConnect } from "../terminal.js";
import { closeBriefing } from "../agent/briefing.js";
import { shoot, flatten } from "../capture.js";
import { runActions } from "./actions-run.js";
import { buildActions } from "./actions-build.js";
import { workspaceActions } from "./actions-workspace.js";
import { refreshDoctor, selectRun, openHere, loadThread, chooseAgent, handoff,
         refreshGpu, selectTask } from "./load.js";

const appActions = {
  selectRun: (id) => selectRun(id),
  setView: (id) => {
    setView(id);
    // Build reads from a different place than every other view, so switching
    // to it is a fetch. The poller keeps it current while it is open.
    if (id === "build") refreshBuild();
    // And the agent's subject follows the view, so the pane has to as well --
    // otherwise Build shows the last run's conversation under a panel that
    // says it is about the task.
    openHere(subject());
  },
  // The library's shelf picks a task, and it must be the same act as the
  // switcher's -- the rail, the graph and the source tree all follow it.
  openTask: (task) => selectTask(task),
  // The task's whole history as one file (report.js). Opening asks what it
  // would weigh; building writes it under the task's records root.
  openReport: () => openReport(),
  closeReport: () => closeReport(),
  buildReport: (media) => buildReport(media),
  toggleAgent: () => setAgentOpen(!state.agentOpen),
  toggleUnrecorded: () => set({ unrecOpen: !state.unrecOpen }),
  toggleConversations: () => set({ convOpen: !state.convOpen }),
  // Opening it polls at once: the pending timer was set for the closed
  // cadence, and a popup that showed twelve-second-old numbers for its first
  // twelve seconds would look like it had frozen.
  toggleGpu: () => {
    const open = !state.gpuOpen;
    set({ gpuOpen: open });
    if (open) refreshGpu();
  },
  openSettings: async () => {
    set({ settingsOpen: true, settingsError: "", settingsRestart: null,
          setRecords: null, setPackages: null });
    // Re-asked on open: the person came here after doing something about a
    // missing item, and the card should agree with them.
    refreshDoctor();
    try {
      set({ settings: await api.settings() });
    } catch (err) {
      set({ settingsError: err.message });
    }
  },
  closeSettings: () => set({ settingsOpen: false }),
  // Take a picture of a panel and open it for review.
  screenshot: (node, label) => shoot(node, label),
  closeShot: () => set({ shot: null, shotAnnotate: false }),
  // Into the message box rather than straight to the agent: the point of the
  // picture is what you say about it, and sending it alone would skip that.
  attachShot: async () => {
    const url = await flatten(state.shot);
    set({ attached: { url, label: (state.shot || {}).label || "" },
          shot: null, shotAnnotate: false });
    const box = document.querySelector(".agent-input");
    if (box) box.focus();
  },
  dropAttachment: () => set({ attached: null }),
  saveRoots: async (body) => {
    set({ settingsBusy: true, settingsError: "" });
    try {
      const answer = await api.saveSettings(body);
      // The server's own answer about what it cannot adopt while running --
      // not a list kept here, because two copies of that rule is one too many.
      set({ settings: await api.settings(),
            settingsRestart: answer.restart_required || [],
            setRecords: null, setPackages: null });
    } catch (err) {
      set({ settingsError: err.message });
    } finally {
      set({ settingsBusy: false });
    }
  },
  // Opening attaches a shell; *closing* attaches nothing and detaches nothing.
  // A closed socket is a closed pty by the server's design, so closing sockets
  // here would kill whatever is running in the drawer. Hiding a terminal is
  // not quitting it.
  toggleTerminal: () => {
    const open = !state.termOpen;
    set({ termOpen: open });
    if (open) termConnect();
  },
  setAgentMode: (mode) => chooseAgent({ mode }),
  setAgentModel: (model) => chooseAgent({ model }),
  // Who drives the *next* conversation. The open one keeps its provider: its
  // context lives inside that provider's session, and there is no honest way
  // to hand it to another model.
  setAgentProvider: (provider) => chooseAgent({ provider }),
  // How the provider signs in, for a provider that has a choice (the list
  // comes from the provider).
  setAgentAuth: (signin) => chooseAgent({ signin }),
  // The one choice that cannot reach the conversation already open: a system
  // prompt is handed to the CLI at connect. `chooseAgent` stores it and the
  // menu says so; the next conversation starts with it.
  setAgentHarness: (harness) => chooseAgent({ harness }),
  openBriefing: async () => {
    const who = subject(state);
    if (!who) return;
    set({ briefing: null, briefingBusy: true, briefingError: "" });
    try {
      set({ briefing: await api.agentBriefing(who, state.thread) });
    } catch (err) {
      set({ briefingError: err.message });
    } finally {
      set({ briefingBusy: false });
    }
  },
  closeBriefing: () => closeBriefing(),
  // Open a conversation *without* moving the rail. Going through `selectRun`
  // would also change which run you are looking at, and you could not read
  // one run's charts while its agent explained itself.
  openConversation: async (run, thread) => {
    setAgentOpen(true);
    set({ convPin: run, thread, lastThread: { ...(state.lastThread || {}), [run]: thread } });
    await loadThread(run, thread);
  },
  // Give the panel back to the view: the conversation about whatever the rail
  // is showing. The way out of a pin, and the only one -- switching runs does
  // not do it by accident.
  followCurrentRun: async () => {
    set({ convPin: "" });
    await openHere(subject());
  },
  // A fresh conversation on the same run, with a handoff already in the
  // composer: what was last asked and the agent's last word on it. Written
  // for you to edit and send, not sent -- the old conversation is too large
  // to ask for a summary, and what matters from it is yours to pick.
  freshStart: async () => {
    const brief = handoff(state.transcript || [], subject());
    await actions.newThread();
    set({ draft: brief });
  },
  newThread: async () => {
    // Named by when it started: a conversation renames itself from its first
    // question as soon as there is one.
    const stamp = new Date().toTimeString().slice(0, 5).replace(":", "");
    const thread = `t${stamp}`;
    // Remembered here as well as on the server, because the server does not
    // know about a conversation until something is said in it -- and until
    // then it is in no list and cannot be left or returned to. Kept *per
    // run*, or a thread started on one run is offered on an unrelated run in
    // another task, under a panel that says it is pinned to this run.
    // Keyed by the *subject*, which is the pinned conversation when there is
    // one: a second thread started while pinned to a run belongs to that run,
    // not to whichever run the rail happens to be showing.
    const who = subject();
    const mine = (state.threads || {})[who] || [];
    const threads = { ...(state.threads || {}),
                      [who]: [...new Set([...mine, thread])] };
    set({ thread, threads, convPin: who,
          lastThread: { ...(state.lastThread || {}), [who]: thread },
          transcript: [], pending: [], agentBusy: false, agentStopping: false,
          agentTools: null, agentWho: "" });
    setAgentOpen(true);
  },
  // Stop what the agent is doing, keeping the conversation. Without it a
  // runaway turn can go on for hundreds of entries with nothing on the screen
  // able to reach it.
  stopAgent: async () => {
    const who = subject();
    if (!who) return;
    // Optimistic, and safe: `agent.stopped` and `agent.idle` both come back
    // over the socket and are the authority. Without it the panel keeps
    // saying "working…" for as long as the request takes, which is the exact
    // stuck feeling this button exists to end.
    set({ agentBusy: false, agentStopping: true, agentError: "" });
    try {
      const answer = await api.agentStop(who, state.thread);
      if (!answer.ok) set({ agentError: answer.reason || "there was nothing to stop" });
    } catch (err) {
      set({ agentError: err.message });
    }
    await loadThread(who, state.thread);
  },
};

/** The action table every layout, view and component is handed.
 *
 * One object, assembled from a table per area so that no file has to hold all
 * of it. Each name is defined in exactly one table.
 *
 * The tables call each other through this object, so they import it back; that
 * cycle is safe only while this module is the way into them. A table imported
 * before this module loads first, and the spread below meets it uninitialised. */
export const actions = { ...appActions, ...runActions, ...buildActions, ...workspaceActions };
