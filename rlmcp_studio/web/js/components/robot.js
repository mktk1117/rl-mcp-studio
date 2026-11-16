// The robot card: a frame now, clips over training, or the live 3-D view.

import { el, chip, fmt, add } from "../ui.js";
import { divider, fits } from "../resize.js";
import { framesCard } from "./frames.js";
import { cadenceLabel, clipsCard, recordVideo } from "./clips.js";

// Looking at the robot, in one panel.
//
// Two cards -- "Live frames" and "Progress clips" -- side by side would read
// as two piles of pictures of the same robot. They are not the same thing, but
// those names say *what* rather than *when*, which is the whole distinction:
//
//   Now            one frame, rendered on demand, of the state it is in.
//   Over training   clips recorded on a widening schedule, in order.
//
// So: one card, and the switch is the two answers to "when". Naming the axis
// is what makes the difference obvious, and it costs half the height of two
// cards.
const ROBOT_MODES = [
  { id: "now", label: "Now", blurb: "a frame rendered on demand" },
  { id: "history", label: "Over training", blurb: "clips, oldest to newest" },
  // The third answer to "when", and the only one that is not a picture: the
  // environment itself, in 3-D, as it steps. See liveViewBody below.
  { id: "live", label: "Live 3-D", blurb: "the environment itself, as it steps" },
  // The fourth, and it is a different *kind* of answer: not when, but which
  // ones survive. Clips in `logs/` go when the logs are cleaned; these were
  // promoted into the record and are part of the deliverable. A card of its
  // own beside this one would put two video panels on one screen, which is
  // one more than a screen needs.
  { id: "kept", label: "Kept", blurb: "the clips promoted into the record" },
];

/** The robot, in whichever of the three tenses you asked for.
 *
 * `withVerbs` exists for the Console, which keeps Look, Clip and Diagnose in
 * its composer bar and would otherwise show the same three twice. Everywhere
 * else they belong *here*: this card's "Now" body says "Press Look and the
 * trainer renders one", and that instruction is addressed to every layout, so
 * every layout needs a Look on screen to press.
 */
export function robotCard(state, actions, { withVerbs = true } = {}) {
  const clipCount = ((state.clips || {}).clips || []).length;
  const keptCount = ((((state.detail || {}).record || {}).assets || {}).videos || []).length;
  const view = liveViewState(state);
  const mode = availableMode(state.robotMode || "now", clipCount, view, keptCount);
  const schedule = ((state.live && state.live.status) || {}).progress_video || null;

  const head = el("div", { class: "card-h" },
    el("h3", { text: "The robot" }),
    el("span", { class: "seg" },
      ROBOT_MODES.map(m => el("button", {
        class: "seg-btn", "aria-pressed": mode === m.id ? "true" : "false",
        title: m.blurb,
        disabled: (m.id === "history" && !clipCount)
                  || (m.id === "live" && !view.offered && !view.canPlay)
                  || (m.id === "kept" && !keptCount),
        onclick: () => actions.setRobotMode(m.id),
      }, m.label))),
    mode === "now" && state.framePicked ? chip("pinned", true) : null,
    mode === "history" && schedule && schedule.enabled
      ? el("span", { class: "chip", title: schedule.cadence_prose || "",
                     text: cadenceLabel(schedule) })
      : null,
    mode === "kept" ? chip(`${keptCount} on the record`) : null,
    mode === "kept"
      ? el("button", {
          class: `act${state.keepClips ? " on" : ""}`,
          "aria-pressed": state.keepClips ? "true" : "false",
          title: state.keepClips
            ? "new clips are promoted into the record as they arrive"
            : "clips stay in logs/ and go when the logs are cleaned",
          onclick: () => actions.toggleKeepClips(),
        }, state.keepClips ? "Keeping" : "Keep latest")
      : null,
    mode === "live" && view.running ? chip(watchLabel(view), true) : null,
    // Never let a replayed checkpoint read as live training.
    mode === "live" && view.source === "play"
      ? el("span", { class: "chip", title: view.checkpoint || "",
                     text: "checkpoint, replayed" })
      : null,
    el("span", { class: "sp" }),
    el("span", { class: "eyebrow", text: robotEyebrow(mode, state, clipCount, view) }),
    // A picture of whatever tense is showing, into the message box. Without it
    // you would have to describe what you are looking at, and a description of
    // a gait is not a gait.
    el("button", {
      class: "act cam", title: "take a picture of this and add it to a message",
      onclick: (e) => {
        const card = e.currentTarget.closest(".robot-card");
        actions.screenshot(card && card.querySelector("video, img, iframe, svg"),
                           robotShotLabel(mode));
      },
    }, "\u25A3"),
    mode === "now" && state.framePicked
      ? el("button", { class: "act", title: "follow the newest frame again",
                       onclick: () => actions.liveFrames() }, "Back to live")
      : null,
    mode === "live" && view.running && view.source === "trainer"
      ? el("button", { class: "act", title: "close the viewer and give the port back",
                       disabled: state.viewBusy || undefined,
                       onclick: () => actions.setLiveView(false) }, "Turn off")
      : null,
    // A played policy is a process this studio started, so turning it off ends
    // the process rather than just the pushing.
    mode === "live" && view.source === "play"
      ? el("button", { class: "act", title: "stop the process holding this policy open",
                       disabled: state.playBusy || undefined,
                       onclick: () => actions.playPolicy(false) }, "Stop playing")
      : null);

  // The height is a preference, like every width in this app. It is the panel
  // whose content is the whole point -- a 3-D view squeezed into a strip is
  // not a view of anything -- and the only one the stage sizes from what is
  // *under* it rather than from what it holds.
  return el("div", {
    class: "card robot-card",
    // `fits`, not the raw saved height: a panel dragged tall on a large
    // monitor can be taller than a laptop's whole stage, and the video ends
    // up below the fold with nothing on screen to say why.
    style: { height: `${fits("robot", state.widths.robot)}px` },
  }, head,
    // The 3-D frame, whatever the mode. Rendered in this one position every
    // time and hidden when another mode is showing: the reconciler keeps a
    // keyed node, but it *moves* one that changed place, and a moved iframe
    // reloads -- so switching to Over training and back would cost a
    // reconnect and the camera you placed.
    view && view.alive && view.running && view.url
      ? viserSlot(view.url, "live", { hidden: mode !== "live" })
      : null,
    mode === "now" ? framesCard(state, actions, { bodyOnly: true, withVerbs })
    : mode === "history" ? clipsCard(state, actions, { bodyOnly: true })
    : mode === "kept" ? recordVideo(state, actions, { bodyOnly: true })
    : liveViewBody(state, actions, view),
    divider("robot", {
      axis: "y", side: "bottom",
      onResize: (px) => actions.setPanelWidth("robot", px),
      onReset: (px) => actions.setPanelWidth("robot", px),
    }));
}

/** The mode to actually show, which is not always the one you last chose.
 *
 * A run with no clips cannot show "over training", and one whose rlmcp has no
 * live view cannot show 3-D. Falling back is better than an empty panel under
 * a pressed button — the button is disabled and says why in its tooltip.
 */
/** What the picture is of, so the message says so without you typing it. */
function robotShotLabel(mode) {
  return { now: "the latest frame", history: "a clip, over training",
           kept: "a clip kept on the record",
           live: "the 3-D view" }[mode] || "the robot";
}

function availableMode(wanted, clipCount, view, keptCount = 0) {
  if (wanted === "history" && !clipCount) return "now";
  if (wanted === "kept" && !keptCount) return "now";
  // `canPlay` keeps the tense reachable after the trainer exits: the panel has
  // something to offer there, and falling back to "now" would hide it.
  if (wanted === "live" && !view.offered && !view.canPlay) return "now";
  return wanted;
}

function robotEyebrow(mode, state, clipCount, view) {
  if (mode === "history") return `${clipCount} clip${clipCount === 1 ? "" : "s"}`;
  if (mode === "live") {
    if (view.running) {
      const what = view.source === "play" ? "playing" : (view.mode || "live");
      return `${what}, ${fmt(view.fps || 0, 0)} fps`;
    }
    if (!view.alive && view.canPlay) return "trainer gone — the policy is not";
    return "not serving — nothing is being pushed";
  }
  return state.live && state.live.state === "running"
    ? "the state it is in" : "trainer gone — nothing new to render";
}

// ── the live 3-D view ─────────────────────────────────────────────────────
//
// The studio starts no viser server, allocates no port and holds no scene.
// rlmcp's `live_view` command owns all of that: it opens viser beside the
// trainer, mirrors the environment's own scene, pushes only while a tab is
// actually watching, and publishes where it landed in `status.live_view`.
//
// So the whole panel is three steps — turn it on, read the url, frame it —
// and the same three work on a preview session, which is what makes the env
// builder cheap.

/** What `status.live_view` says, in the shape this panel asks questions in. */
/** The 3-D view this panel should show, from whichever process is publishing one.
 *
 * Two can: the trainer, and `rlmcp play` holding a checkpoint open after the
 * trainer is gone. They are the same kind of rlmcp session answering the same
 * `live_view` command, so they are read by the same function and rendered by
 * the same body — which is the reason to play a policy *into this panel*
 * rather than build a second one beside it. Only `source` tells them apart,
 * and only because the panel has to say which one you are looking at: a
 * replayed checkpoint that looked like live training would be the most
 * misleading thing this app could draw.
 */
function liveViewState(state) {
  const trainer = viewFrom(state.live, !!state.live && state.live.state === "running");
  if (trainer.alive) return { ...trainer, source: "trainer" };

  const play = state.play || {};
  const played = viewFrom(state.playLive, play.state === "holding");
  if (played.alive) {
    return { ...played, source: "play", checkpoint: play.checkpoint || "",
             // A play process that is up but has not answered `live_view` yet
             // is still worth calling offered: the panel is where you wait.
             known: true, offered: true };
  }
  return { ...trainer, source: "trainer",
           canPlay: !!play.can_play, wouldPlay: play.would_play || "",
           playState: play.state || "off", playError: play.error || "" };
}

function viewFrom(snapshot, alive) {
  const view = ((snapshot || {}).status || {}).live_view || null;
  return {
    // Absent means this run's rlmcp predates the live view, which is a
    // different thing from "off" and has to read differently.
    known: !!view,
    offered: !!view && alive,
    alive,
    running: !!(view && view.running),
    url: (view && view.url) || "",
    hostUrl: (view && view.host_url) || "",
    mode: (view && view.mode) || "",
    fps: (view && view.fps) || 0,
    watchers: (view && view.watchers) || 0,
    pushMs: (view && view.push_ms) || 0,
    port: (view && view.port) || 0,
    error: (view && view.last_error) || (view && view.stopped_because) || "",
  };
}

/** The tail of a failed play, as the process wrote it.
 *
 * Not summarised: the failures here are a traceback from the task's own code
 * or an argparse message from rlmcp, and both say more than anything this app
 * could say about them.
 */
function playFailure(state) {
  const log = ((state.play || {}).log || []).filter(line => String(line).trim());
  if (!log.length) return null;
  return el("details", { class: "playfail" },
    el("summary", { text: "why it did not start" }),
    el("pre", { class: "log", text: log.slice(-14).join("\n") }));
}

function watchLabel(view) {
  const n = view.watchers || 0;
  const cost = view.pushMs ? ` · ${fmt(view.pushMs, 1)} ms/push` : "";
  return `${n} watching${cost}`;
}

function liveViewBody(state, actions, view) {
  if (!view.alive) {
    // The trainer being gone is when you *most* want to see the robot move.
    // `rlmcp play` rebuilds the environment from the run's own checkpoint and
    // holds it open, which is a window onto a process again — a new one,
    // saying so.
    return el("div", { class: "card-b" },
      el("div", { class: "offer" },
        el("p", { text: "The trainer is gone, and a live view is a window onto "
                      + "a process. Frames and clips are still here — they were "
                      + "written down." }),
        view.canPlay
          ? el("p", { text: "The policy it learned is not gone. Play it and rlmcp "
                          + "rebuilds the environment from this run's checkpoint "
                          + "and steps it here, under the conditions it trained in." })
          : el("p", { class: "k", style: { color: "var(--dim)" },
                      text: "This run kept no checkpoint, so there is no policy "
                          + "to play back." }),
        view.playError ? el("p", { class: "m-error", text: view.playError }) : null,
        state.playError ? el("p", { class: "m-error", text: state.playError }) : null,
        // Why it did not build, in the panel rather than in a log file. The
        // process's own last words are the useful ones -- rlmcp answers an
        // unregistered task by naming the flag to pass and listing what *is*
        // registered, and in a log file nobody would read it.
        view.playState === "failed" ? playFailure(state) : null,
        view.canPlay
          ? el("button", { class: "act primary",
                           title: view.wouldPlay || "",
                           disabled: state.playBusy || view.playState === "starting" || undefined,
                           onclick: () => actions.playPolicy(true) },
              state.playBusy || view.playState === "starting"
                ? "building the environment…" : "Play the policy")
          : null));
  }
  if (!view.known) {
    return el("div", { class: "card-b" },
      el("div", { class: "empty" },
        "this run's rlmcp does not publish a live view — it needs the "
        + "`live_view` command, which arrived with `rlmcp view`"));
  }
  if (!view.running) {
    return el("div", { class: "card-b" },
      el("div", { class: "offer" },
        el("p", { text: view.source === "play"
                    ? "The environment is built and stepping this run's policy. "
                      + "The viewer is opening — rlmcp pushes only while this "
                      + "panel is watching, so it costs nothing when nobody is."
                    : "Watch the environment itself, in 3-D, as it steps. "
                      + "rlmcp opens the viewer beside the trainer and pushes "
                      + "only while this panel is open, so it costs nothing "
                      + "when nobody is looking." }),
        state.viewError ? el("p", { class: "m-error", text: state.viewError }) : null,
        state.playError ? el("p", { class: "m-error", text: state.playError }) : null,
        // Two different sessions answer this: the trainer's, and the process
        // playing a checkpoint. Sending it to the wrong one is silent -- the
        // trainer is gone, so the command simply never lands.
        el("button", { class: "act primary", disabled: state.viewBusy || undefined,
                       onclick: () => (view.source === "play"
                                       ? actions.playView(true)
                                       : actions.setLiveView(true)) },
          state.viewBusy ? "opening…" : "Open the 3-D view")));
  }

  // The frame itself is not here: `robotCard` keeps it as its own child, in
  // the same place whatever the mode, so switching to Over training and back
  // does not reload it. This body is the foot under it.
  return el("div", { class: "card-b viser-b" },
    view.error ? el("p", { class: "m-error", text: view.error }) : null,
    // The frame's own words for this are viser's, inside the iframe, and they
    // say to turn graphics acceleration back on -- which may be the setting
    // turned off to stop the page flickering under a saturated card. The flag
    // that keeps both is the studio's to name.
    webglAvailable() ? null
      : el("p", { class: "caveat", text:
          "This browser has no WebGL, so the 3-D view cannot draw. Chrome "
          + "started with --disable-gpu-compositing keeps WebGL on the card "
          + "while the page itself is drawn on the CPU, which is what stops "
          + "the flicker under a saturated GPU. Clips and frames work without it." }),
    el("div", { class: "viser-foot" },
      el("span", { class: "eyebrow", text: `viser on port ${view.port}` }),
      el("span", { class: "sp" }),
      // The one thing the frame cannot do for you: a run being watched from
      // another machine needs the host's name, not this browser's localhost.
      view.hostUrl
        ? el("a", { class: "act", href: view.hostUrl, target: "_blank",
                    rel: "noopener", title: "the same view, named for this machine" },
            "Open in a tab")
        : null,
      el("button", { class: "act", title: "reconnect the frame",
                     onclick: () => actions.reloadLiveView() }, "Reload")));
}

// Probed once: creating a context is not free, and the answer does not change
// while the tab is open.
let webglProbe = null;
function webglAvailable() {
  if (webglProbe === null) {
    try {
      const c = document.createElement("canvas");
      webglProbe = !!(c.getContext("webgl2") || c.getContext("webgl"));
    } catch { webglProbe = false; }
  }
  return webglProbe;
}

// The frame, kept alive across repaints -- and unlike the video player, kept
// by *never being moved*.
//
// Re-parenting an <iframe> reloads its document, and `patch()` moves every
// child it renders through a detached staging node. So the frame cannot be
// rendered like anything else here: its host carries `data-keep-children`,
// which makes the reconciler morph the host and leave its contents alone, and
// later paints hand over an *empty* host that gets discarded in favour of the
// one already on screen -- the one still holding the frame, its websocket and
// the camera you placed.
const VIEWERS = new Map();

/** The frame for one viser url, kept alive across repaints.
 *
 * `key` is which panel is asking: the live run's 3-D view and the builder's
 * preview are two different environments that can be on screen in the same
 * session, and one shared frame would have them fighting over a src.
 */
export function viserSlot(url, key = "live", { hidden = false } = {}) {
  const entry = VIEWERS.get(key);
  // `hidden` is how the frame survives a mode switch: the host stays where
  // it is in the card and is simply not shown, because a host that is
  // removed takes the frame with it and a frame put back reloads.
  const host = () => el("div", { class: "viser-host", key: `viser-${key}`,
                                 "data-keep-children": "", hidden: hidden ? "" : null });

  // Detached means the panel was closed and reopened -- a view switch, another
  // robot mode. The frame cannot survive that (see above), so it is rebuilt,
  // which costs one reconnect and the camera.
  if (!entry || !entry.frame.isConnected) {
    const frame = el("iframe", { class: "viser", src: url,
                                 title: "the environment, live, over viser",
                                 allow: "fullscreen; xr-spatial-tracking" });
    VIEWERS.set(key, { frame, url });
    return add(host(), frame);
  }
  // Assigning the same src reloads the page, so only when it actually moved --
  // a restarted view can land on another port.
  if (entry.url !== url) { entry.frame.src = url; entry.url = url; }
  return host();
}

/** Reconnect a frame on purpose. Exported for the action in main.js. */
export function reloadViserFrame(key = "live") {
  const entry = VIEWERS.get(key);
  if (entry && entry.frame.isConnected) entry.frame.src = entry.url;
}
