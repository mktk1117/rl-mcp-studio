// Actions on the selected run: the verbs, frames and clips, the live 3-D view,
// and playing its policy.

import { state, set, settingChanged } from "../state.js";
import { api } from "../api.js";
import { reloadViserFrame } from "../components.js";
import { actions } from "./actions-app.js";
import { refreshPlay, selectRun, latestClipPath, refreshLive } from "./load.js";

export const runActions = {
  runVerb: async (key, cmd, args) => {
    if (!state.runId) return;
    set({ running: { ...state.running, [key]: true }, commandError: "",
          verbDone: { ...state.verbDone, [key]: null } });
    let outcome = { ok: true, message: "" };
    try {
      const answer = await api.command(state.runId, cmd, { ...args, where: state.where || undefined });
      if (!answer.ok) throw new Error(answer.error || "the command failed");
      // A picture goes to the frame; a clip goes to the player. One shared
      // slot would point the hero `<img>` at an mp4, and the frame would be a
      // broken-image icon until you pressed Look again.
      // A rendered frame is the newest frame, so it releases the pin: the
      // header would otherwise keep saying "pinned" over a picture the pin no
      // longer points at.
      if (answer.image_path) set({ lastFrame: answer.image_path, framePicked: false });
      if (answer.video_path) set({ clipUrl: state.artifactUrl(answer.video_path),
                                   clipPicked: true });
      if (cmd === "diagnose") {
        // The diagnosis brings its own plot as a PNG *and* the signals behind
        // it. Take the signals: drawn here they match every other curve and can
        // be smoothed and zoomed, which a picture cannot.
        set({ diagnosis: answer, trace: null, traceStamp: null });
        if (answer.trace_path) set({ tracePath: answer.trace_path });
      }
      await refreshLive();
    } catch (err) {
      outcome = { ok: false, message: err.message };
      set({ commandError: err.message });
    } finally {
      const running = { ...state.running };
      delete running[key];
      set({ running, verbDone: { ...state.verbDone, [key]: outcome } });
      // The mark says what happened and then stops saying it. Long enough to
      // read on a glance back, short enough not to become furniture.
      setTimeout(() => {
        if ((state.verbDone || {})[key] === outcome) {
          set({ verbDone: { ...state.verbDone, [key]: null } });
        }
      }, outcome.ok ? 2600 : 7000);
    }
  },
  // Picking a frame from the strip *pins* it -- otherwise the next arriving
  // artifact would yank you off the one you are looking at. The panel says so
  // and offers the way back; without that it just looked frozen.
  showFrame: (path) => set({ lastFrame: path, framePicked: true }),
  keepClip: async (path, caption) => {
    if (!state.runId || !path) return;
    set({ keepingClip: path });
    try {
      await api.addAsset(state.runId, path, "videos", caption || "");
      await selectRun(state.runId);            // re-read so the record is authoritative
    } catch (err) {
      set({ commandError: err.message });
    } finally {
      set({ keepingClip: "" });
    }
  },
  toggleKeepClips: () => {
    const on = !state.keepClips;
    localStorage.setItem("rlmcp.keepClips", on ? "1" : "0");
    settingChanged("keepClips", !!on);
    set({ keepClips: on });
    // Turning it on is also the instruction to keep what is on screen now,
    // otherwise the first clip it applies to is the next one -- which is not
    // what "keep the latest" means to anyone who just pressed it.
    if (on) {
      const latest = latestClipPath();
      if (latest) actions.keepClip(latest, "latest at the time it was kept");
    }
  },
  playKept: (key, url) => set({ keptPlaying: key, playWanted: url || "" }),
  setRobotMode: (mode) => {
    localStorage.setItem("rlmcp.robotMode", mode);
    settingChanged("robotMode", mode);
    set({ robotMode: mode });
  },
  // The 3-D view is rlmcp's, not ours: this asks the trainer to open or close
  // it and then re-reads status, which is where the url comes from. The
  // studio allocates no port and serves no scene.
  //
  // `confirmed` because the endpoint refuses anything outside its read-only
  // set without it, and this one is a person pressing a button that binds a
  // port beside their training run. That is exactly what confirmation means.
  setLiveView: async (on) => {
    if (!state.runId) return;
    set({ viewBusy: true, viewError: "" });
    try {
      const answer = await api.command(state.runId, "live_view", { enabled: !!on },
                                       { confirmed: true });
      if (!answer.ok) throw new Error(answer.error || "the trainer refused");
      if (on) set({ robotMode: "live" });
      await refreshLive();
    } catch (err) {
      set({ viewError: err.message });
    } finally {
      set({ viewBusy: false });
    }
  },
  reloadLiveView: () => reloadViserFrame(),

  /** Play this run's own policy, in the panel the live view owns in training.
   *
   * Not a command to the trainer — the trainer is gone, which is the whole
   * reason this exists. It is a process of the studio's, so it is started and
   * stopped here and polled like the builder's preview: the reply describes
   * the moment it returned, and an environment that is "starting" then is
   * holding a minute later.
   */
  /** Open or close the viewer on the *played* session. */
  playView: async (on) => {
    if (!state.runId) return;
    set({ viewBusy: true, playError: "" });
    try {
      const answer = await api.play(state.runId, { action: "view", enabled: !!on });
      if (answer.error) throw new Error(answer.error);
      await refreshPlay();
    } catch (err) {
      set({ playError: err.message });
    } finally {
      set({ viewBusy: false });
    }
  },

  playPolicy: async (on) => {
    if (!state.runId) return;
    set({ playBusy: true, playError: "" });
    try {
      const answer = await api.play(state.runId, { action: on ? "start" : "stop" });
      if (answer.error) throw new Error(answer.error);
      set({ play: answer.play || null, robotMode: on ? "live" : state.robotMode });
      if (!on) set({ playLive: null });
      await refreshPlay();
    } catch (err) {
      set({ playError: err.message });
    } finally {
      set({ playBusy: false });
    }
  },

};
