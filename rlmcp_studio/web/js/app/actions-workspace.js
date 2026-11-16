// Actions on the rest of the workspace: panels, files and filters, parameters,
// the next record, asking the agent, annotations and closing a run out.

import { state, set, setAgentOpen, setView, subject, settingChanged } from "../state.js";
import { api } from "../api.js";
import { saveWidth } from "../resize.js";
import * as editorHost from "../editor.js";
import { actions } from "./actions-app.js";
import { reloadRuns, refreshRun, selectRun, loadThread, refreshUnrecorded } from "./load.js";

export const workspaceActions = {
  toggleClips: () => {
    const open = !state.clipsOpen;
    localStorage.setItem("rlmcp.clipsOpen", open ? "1" : "0");
    settingChanged("clipsOpen", !!open);
    set({ clipsOpen: open });
  },
  // One action for every resizable panel. Bounds and persistence live in
  // resize.js, so adding a panel is an entry in its table rather than another
  // copy of this.
  setPanelWidth: (name, px) => {
    set({ widths: saveWidth(state.widths, name, px) });
  },
  liveFrames: () => set({ lastFrame: null, framePicked: false }),
  closeZoom: () => set({ zoom: null }),
  startAdopting: (row) => set({
    adopting: row, adoptError: "",
    // A name is offered rather than demanded: the directory the trainer chose
    // already says what the run was, and retyping it is busywork.
    adoptSlug: (row.name || "").replace(/^\d{4}-\d{2}-\d{2}_[\d-]+_/, "") || "run",
    adoptWhy: "",
  }),
  cancelAdopt: () => set({ adopting: null, adoptError: "" }),
  adopt: async (slug, why) => {
    const row = state.adopting;
    if (!row) return;
    if (!String(slug || "").trim()) {
      set({ adoptError: "give the run a short name first" });
      return;
    }
    set({ adoptBusy: true, adoptError: "" });
    try {
      const answer = await api.adopt(row.session, slug, why || "");
      set({ adopting: null, adoptBusy: false });
      await refreshUnrecorded();
      await reloadRuns();
      await selectRun(answer.record);
    } catch (err) {
      set({ adoptBusy: false, adoptError: err.message });
    }
  },
  setSmoothing: (weight) => {
    localStorage.setItem("rlmcp.smoothing", String(weight));
    settingChanged("smoothing", Number(weight) || 0);
    set({ smoothing: weight });
  },
  // Opening a file, optionally at a line: a content-search hit is a line, and
  // landing in the right file at the wrong place is barely better than not
  // landing at all.
  openFile: (path, line = 0) => {
    set({ filePath: path, fileStamp: null, jumpTo: line });
    // Handed to the editor rather than fired on a timer: the file has not been
    // fetched yet, and a reveal that arrives before the content does nothing.
    if (line) editorHost.revealWhenLoaded(path, line);
  },
  // One box, two searches. Names are ranked here from the tree already loaded;
  // text has to ask the server, so it waits for a pause in the typing.
  typeFilter: (value) => {
    state.fileFilter = value;
    clearTimeout(searchTimer);
    if ((state.fileMode || "name") === "name") {
      searchTimer = setTimeout(() => set({ fileFilter: value, filePicked: 0 }), 60);
      return;
    }
    set({ fileSearch: null });
    searchTimer = setTimeout(async () => {
      set({ fileFilter: value });
      if (value.trim().length < 2) return;
      try {
        const found = await api.sourceSearch(value);
        if (state.fileFilter === value) set({ fileSearch: found });
      } catch (err) {
        if (state.fileFilter === value) set({ fileSearch: { error: err.message } });
      }
    }, 220);
  },
  // Kept out of the store until the typing pauses, like the other filters --
  // the rail is rebuilt on every keystroke otherwise, and with fifty rows that
  // is work nobody asked for.
  typeRunFilter: (value) => {
    state.runFilter = value;
    clearTimeout(railFilterTimer);
    railFilterTimer = setTimeout(() => set({ runFilter: value }), 80);
  },
  // The library's shelf, filtered the same way and for the same reason: dozens
  // of tasks is a list you scan, not one you read.
  typeLibFilter: (value) => {
    state.libFilter = value;
    clearTimeout(libFilterTimer);
    libFilterTimer = setTimeout(() => set({ libFilter: value }), 80);
  },
  toggleScope: () => set({ scopeOpen: !state.scopeOpen }),
  // Narrowing is a studio-side setting, not a command: it changes what the
  // *next* verb asks for and touches nothing in the run until you press one.
  setWhere: (where) => {
    // `undefined` means "stop narrowing by this", and an empty object means
    // "all envs" -- the verbs take no `where` at all then.
    const kept = Object.fromEntries(
      Object.entries(where || {}).filter(([, v]) => v !== undefined && v !== null));
    set({ where: Object.keys(kept).length ? kept : null, scopeOpen: false });
  },
  // Picking a clip pins it: later clips landing mid-run must not yank the
  // player away from the iteration you are looking at.
  showClip: (url) => set({ clipUrl: url, clipPicked: true, playWanted: url }),
  setParameter: async (key, value, why) => {
    if (!state.runId) return;
    try {
      const answer = await api.command(state.runId, "set_parameter",
        { key, value, rationale: why || "" }, { confirmed: true });
      if (!answer.ok) throw new Error(answer.error || "not applied");
      set({ pendingEdit: null, paramsStamp: null, commandError: "" });
    } catch (err) {
      set({ commandError: err.message });
    }
  },
  proposeEdit: (edit) => set({ pendingEdit: edit }),

  // You edit code, and the app offers you the record it belongs to. Opening
  // the dialog reads the working tree so the file list is the real one rather
  // than a promise.
  startNextRecord: async () => {
    set({ nextRecord: true, nextError: "", nextFiles: null });
    try {
      const patch = await api.diff(state.runId);
      const all = (patch.worktree && patch.worktree.files) || [];
      // The relevant ones, marked server-side -- the same list the record will
      // carry, so the dialog is not showing something else.
      const relevant = all.filter(f => f.relevant).map(f => f.path);
      set({ nextFiles: relevant.length ? relevant : all.map(f => f.path),
            nextOffTopic: relevant.length === 0 && all.length > 0 });
    } catch (err) {
      set({ nextError: err.message });
    }
  },
  openNextRecord: async () => {
    if (state.nextBusy) return;
    set({ nextBusy: true, nextError: "" });
    try {
      const answer = await api.nextRecord({
        parent: state.runId, slug: state.nextSlug || "", hypothesis: state.nextWhy || "",
      });
      set({ nextBusy: false, nextRecord: false, nextSlug: "", nextWhy: "" });
      await reloadRuns();
      await selectRun(answer.record);
    } catch (err) {
      set({ nextBusy: false, nextError: err.message });
    }
  },
  askAboutSelection: async (given) => {
    const sel = given || editorHost.selection();
    if (!sel) return;
    const where = `${sel.path.split("/").slice(-2).join("/")}:${sel.from}`
      + (sel.to > sel.from ? `-${sel.to}` : "");
    await actions.askAgent([
      `What is this doing, and is it right?`, "",
      `From ${where}:`, "```", sel.text, "```",
    ].join("\n"));
  },
  askAboutDoc: async (path) => {
    if (!path) return;
    const name = path.split("/").slice(-1)[0];
    await actions.askAgent(
        `Read ${path} and tell me what it says about this run — and whether `
        + `${name} still matches what the numbers show.`);
  },
  saveFile: async () => {
    const path = editorHost.openPath();
    if (!path || !editorHost.isDirty()) return;
    try {
      // Required: the server refuses a save that does not say what size
      // the file was when it was opened, because a save that skips that check
      // is how another writer's work disappears.
      const size = state.file && state.file.size;
      if (size === undefined || size === null) {
        set({ saveError: "reopen the file before saving — its size is unknown" });
        return;
      }
      await api.saveFile(path, editorHost.contents(), size);
      editorHost.markSaved();
      set({ saveError: "", fileStamp: null });     // re-read so size is current
    } catch (err) {
      set({ saveError: err.message });
    }
  },
  askAgent: async (text) => {
    if (!state.agent || !state.agent.available) {
      set({ agentError: "signed out — the agent panel has the sign-in, "
                        + "with the steps" });
      return;
    }
    setAgentOpen(true);
    // Two doors into one host: a run's conversation carries the record's
    // context and the run's session; a task's carries the package, the
    // preview and the last check -- and lets the agent edit inside the task
    // without asking. Which one you get follows the view you are in.
    const who = subject();
    try {
      if (who.startsWith("task:")) {
        await api.buildAgent(state.task, text, state.thread);
      } else {
        // `who`, not `state.runId`: a pinned conversation is the one you are
        // typing into, and sending to the selected run instead would put the
        // message in a transcript you are not looking at.
        await api.agentSend(who, text, state.thread);
      }
      // Speaking into a conversation is how you end up in it. Without this,
      // asking the agent to launch a run and then clicking the run it made
      // would still take the conversation away.
      set({ convPin: who });
      await loadThread(who, state.thread);
    } catch (err) { set({ agentError: err.message }); }
  },
  // One box, two destinations — the ledger always, the agent when it is asked
  // for. The record write is what must not be lost, so a failure to reach the
  // agent is reported beside the composer rather than thrown away or, worse,
  // treated as a failure to record the observation.
  annotate: async ({ path, box, note, kind, ask }) => {
    if (!state.runId) return;
    set({ annotBusy: true, annotError: "" });
    try {
      const answer = await api.annotate(state.runId, {
        path, box, note, kind, ask_agent: !!ask, thread: state.thread,
      });
      if (ask) setAgentOpen(true);
      set({ annot: null, annotNote: "", annotBusy: false,
            annotError: answer.agent_error || "" });
      // The ledger is the authority, and it is also the confirmation: the
      // entry appears with the boxed frame on it. Re-*read* the run rather
      // than re-selecting it -- selecting resets the frame, the pinned clip
      // and the transcript, which is a lot to lose for writing a note.
      await refreshRun(state.runId);
    } catch (err) {
      set({ annotBusy: false, annotError: err.message });
    }
  },
  // ── closing a run out ──────────────────────────────────────────────
  //
  // The studio collects three sentences and hands them to rl-mcp, which
  // decides whether the record has earned the verdict. Its refusal is shown
  // as it was written -- see `closeCard` in components.js.
  pickVerdict: (verdict) => set({
    closeVerdict: state.closeVerdict === verdict ? "" : verdict,
    // The refusal was about the last verdict you picked, so it is not about
    // this one. Leaving it up reads as the new choice having been refused too.
    closeError: "",
  }),
  closeRun: async () => {
    if (!state.runId || !state.closeVerdict || state.closeBusy) return;
    set({ closeBusy: true, closeError: "", closeReport: "" });
    try {
      const answer = await api.closeRun(state.runId, {
        verdict: state.closeVerdict,
        headline: (state.closeHeadline || "").trim(),
        outcome: (state.closeOutcome || "").trim(),
        // One line, the way `--metric NAME=VALUE` takes them. Split here and
        // checked again on the endpoint, because the CLI answers a malformed
        // pair by exiting rather than by saying anything a panel can show.
        metrics: (state.closeMetrics || "").split(/[,\n]/)
          .map(s => s.trim()).filter(Boolean),
      });
      // The written record is the confirmation, so the form empties only once
      // rl-mcp has said yes -- and the report path stays on screen, because it
      // is a file somebody may want to open.
      set({ closeBusy: false, closeVerdict: "", closeHeadline: "",
            closeOutcome: "", closeMetrics: "",
            closeReport: answer.report || "" });
      for (const box of document.querySelectorAll(".close-out .fb-input")) {
        box.value = "";
      }
      await reloadRuns();
      await refreshRun(state.runId);
    } catch (err) {
      // Verbatim: this is rl-mcp explaining what the record has not earned,
      // and rewording it would be the studio having an opinion about verdicts.
      set({ closeBusy: false, closeError: err.message });
    }
  },
  // Console has no rail, so its "Runs" button goes where the runs are: the
  // Story view, not a menu no layout renders.
  openRuns: () => setView("story"),
  openRecords: (view) => setView(typeof view === "string" ? view : "story"),
};

let searchTimer = 0;
let railFilterTimer = 0;
let libFilterTimer = 0;
