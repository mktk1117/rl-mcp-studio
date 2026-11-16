// Progress clips: the run filming itself over training, and a record's video.

import { el, chip, on, scrollListTo } from "../ui.js";
import { state, set } from "../state.js";
import { notYet } from "./rail.js";

// ── progress clips ──────────────────────────────────────────────────────
//
// The run films itself at iteration 0 and every N iterations after that, and
// this is where those clips become a *trajectory*: the strip is in iteration
// order, so scrubbing it left to right is watching the policy learn. A metric
// curve says the number moved; this says what the robot did while it moved.

// The player is kept across repaints rather than rebuilt with the tree. The
// live feed repaints several times a minute, and a <video> recreated each time
// restarts from zero -- so the element is a module-level singleton and only its
// src changes. Appending an existing node moves it, which is all a repaint
// needs.
const PLAYERS = new Map();

/** Why a clip will not play, in words rather than a numeric code. */
function describeMediaError(video) {
  const code = (video.error && video.error.code) || 0;
  return {
    1: "loading the clip was aborted",
    2: "the clip could not be read from disk",
    3: "the clip is corrupt — most likely written while the trainer was killed",
    // Chrome reports code 4 for "I could not open this", which is far more
    // often a truncated or empty file than a codec this browser lacks -- a
    // few-kilobyte mp4 with an ftyp box and no media reads exactly like an
    // unsupported format. Name both, in that order.
    4: "the clip could not be decoded — usually a file that was never finished "
       + "writing, occasionally a format this browser lacks",
  }[code] || "the clip could not be played";
}

/** Somebody who asked for less motion gets the poster frame, not a loop. */
function prefersStill() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/** The picture, and everything else lying on top of it.
 *
 * One rectangle, the same in every tense and at every height the panel is
 * dragged to. As a stack -- picture, then a caption line, then a row of other
 * clips -- the rows under it are not the same height in the two tenses, so
 * the picture would move and resize every time you switched between them,
 * which is the one thing a picture of a robot must not do while you are
 * comparing two of them. So nothing below it can push it: the caption sits on
 * the clip, and the row of other clips comes up over it when you point at it.
 */
function clipStage(video, { caption = null, rail = null, acts = null, error = "" } = {}) {
  return el("div", { class: "clip-stage" }, video,
    error ? el("div", { class: "m-error", text: error }) : null,
    acts, caption, rail);
}

/** The clip on the card: an animation, not a video player.
 *
 * A scrub bar, a timecode and a volume slider are the furniture of a viewer,
 * and a viewer is not what this panel is -- it is the robot, moving, next to
 * the numbers that say what it was doing. Twelve seconds of gait on a loop
 * answers "what is it doing" without anybody pressing anything, and the
 * controls would be the tallest thing competing with the picture for a panel
 * that may be 250 px high on a laptop. Click toggles it, which is the one
 * control that matters.
 */
function player(id, url, poster, { play = false } = {}) {
  let entry = PLAYERS.get(id);
  if (!entry) {
    const node = el("video", { class: "clip", preload: "auto", loop: true,
                               muted: true, playsinline: true });
    node.muted = true;               // the attribute alone is not enough in all browsers
    entry = { node, url: "", held: false };
    // A clip the browser cannot decode -- truncated because the trainer was
    // killed mid-write, or an encoding this browser lacks -- otherwise shows a
    // spinner and 0:00 forever, indistinguishable from one still loading.
    on(node, "error", () => { set({ clipError: describeMediaError(node) }); });
    on(node, "loadeddata", () => { if (state.clipError) set({ clipError: "" }); });
    // It loops on its own, so the only thing left to ask of it is to stop --
    // to hold one moment of the gait still and look at it. `held` is that
    // request, and it is what the restart below checks: without it, the next
    // repaint would start a clip you had deliberately paused.
    // `e.currentTarget`, not the `node` this closed over: the reconciler hands
    // handlers a discarded copy, and this player is only ever the same element
    // by a rule the rest of the app does not follow.
    on(node, "click", (e) => {
      const v = e.currentTarget;
      entry.held = !v.paused;
      entry.held ? v.pause() : v.play().catch(() => {});
    });
    PLAYERS.set(id, entry);
  }
  if (entry.url !== url) {
    entry.node.src = url;
    entry.url = url;
    entry.held = false;              // a clip you picked is one you want to see move
    if (state.clipError) state.clipError = "";
    // Consumed. Left set, the next repaint would restart a clip you paused.
    if (play && state.playWanted === url) state.playWanted = "";
  }
  if (poster) entry.node.poster = poster;
  else entry.node.removeAttribute("poster");
  // An animation runs unless you stopped it, and `paused` on its own is not
  // that test: autoplay can be refused before the first frame is decodable,
  // and a clip quietly sitting still is indistinguishable from one that failed
  // to load. So every paint puts it back in motion unless `held` says
  // otherwise. Muted autoplay needs no user gesture; reduced motion is the one
  // answer that leaves it on the poster frame.
  if (!entry.held && !prefersStill()) {
    queueMicrotask(() => {
      if (!entry.held && entry.node.paused) entry.node.play().catch(() => {});
    });
  }
  return entry.node;
}

/** What a frame tile says under its thumbnail.
 *
 * Not `name.slice(-16)`, which cuts from the *left*: every caption would read
 * "ot_env0_it022565" for `shot_env0_it022565.png`. Truncating from the left
 * removes exactly the part that says what kind of artifact it is, so this
 * keeps the iteration -- the part that differs between tiles -- and drops the
 * prefix only when there is no room, with the whole name in the tooltip.
 */
export function frameCaption(name) {
  const stem = name.replace(/\.[^.]+$/, "");
  const iteration = /_it0*(\d+)/.exec(stem);
  const env = /_env(\d+)/.exec(stem);
  if (iteration) {
    return `it ${Number(iteration[1]).toLocaleString("en-US")}`
         + (env ? ` · env ${env[1]}` : "");
  }
  return stem.length <= 18 ? stem : stem.slice(0, 17) + "…";
}

/** How often the run films itself, in words that are true of the schedule. */
export function cadenceLabel(schedule) {
  const prose = schedule.cadence_prose || "";
  if (/double/i.test(schedule.cadence || "") || /twice the last/i.test(prose)) {
    return "each gap doubles";
  }
  const every = Number(schedule.every);
  // "set to every N" rather than "every N": the clips underneath are whatever
  // the run managed to film, which need not be evenly spaced, and the chip is
  // the setting, not a description of the list.
  return Number.isFinite(every) && every > 0
    ? `set to every ${every.toLocaleString("en-US")}`
    : "on a schedule";
}

function clipTitle(clip) {
  const when = clip.iteration !== null && clip.iteration !== undefined
    ? `iteration ${Number(clip.iteration).toLocaleString("en-US")}` : "";
  // The caption is usually the iteration again, and "clip · iteration 0 ·
  // iteration 0" is what a screen reader then reads out.
  const caption = (clip.caption || "").trim();
  const extra = caption && caption !== when ? caption : "";
  return ["clip", when, extra].filter(Boolean).join(" · ") || "clip";
}

function clipLabel(clip) {
  if (clip.iteration !== null && clip.iteration !== undefined) {
    return `it ${Number(clip.iteration).toLocaleString("en-US")}`;
  }
  return (clip.caption || "clip").slice(0, 14);
}

export function clipsCard(state, actions, { bodyOnly = false } = {}) {
  const payload = state.clips;
  const clips = (payload && payload.clips) || [];
  const schedule = ((state.live && state.live.status) || {}).progress_video || null;
  const current = clips.find(c => c.url === state.clipUrl)
    || clips[clips.length - 1] || null;

  const card = el("div", { class: bodyOnly ? "card-headless" : "card" },
    bodyOnly ? null : el("div", { class: "card-h" },
      el("h3", { text: "Progress clips" }),
      // "every 400" says nothing about *what* 400 is, and on a doubling
      // cadence it is not even true. rlmcp writes the cadence in prose; use it.
      schedule && schedule.enabled
        ? el("span", { class: "chip", title: schedule.cadence_prose || "",
                       text: cadenceLabel(schedule) })
        : (schedule ? chip("off") : null),
      el("span", { class: "sp" }),
      clips.length ? el("span", { class: "eyebrow", text: `${clips.length} clips` }) : null,
      // A video player is the tallest thing on Control and not always what you
      // are here for. Collapsed it keeps its header, so the cadence and the
      // count still read at a glance.
      el("button", { class: "act", title: state.clipsOpen ? "collapse" : "expand",
                     onclick: () => actions.toggleClips() },
         state.clipsOpen ? "Hide" : "Show")));

  if (!bodyOnly && !state.clipsOpen) return card;

  const body = el("div", { class: "card-b" });

  if (!clips.length) {
    body.append(notYet(
      "No clips yet",
      schedule && schedule.enabled
        ? `The run films itself: ${schedule.cadence_prose || "on a schedule"}. The first one is on its way.`
        : "A supervised run films itself at iteration 0 and at gaps that double. Turn it on with `rlmcp video --every double`.",
      (schedule && schedule.last_error) || ""));
    card.append(body);
    return card;
  }

  const due = schedule && schedule.next_iteration !== null && schedule.next_iteration !== undefined
    ? `next at ${Number(schedule.next_iteration).toLocaleString("en-US")}`
    : "";
  const spent = schedule && schedule.megabytes ? `${schedule.megabytes} MB` : "";
  const cap = el("div", { class: "clip-cap" },
    el("b", { text: clipLabel(current) }),
    el("span", { text: current.source === "session" ? "from the live session" : "in the record" }),
    // The cadence in full as a tooltip, because "every 200" is only the gap
    // *here* — the default doubles, so the number moves as the run goes on.
    el("span", { class: "muted", title: (schedule && schedule.cadence_prose) || "",
                 text: [due, spent].filter(Boolean).join(" · ") }));

  const strip = el("div", { class: "strip clips clip-rail" });
  for (const clip of clips) {
    strip.append(el("button", {
      class: "thumb", "aria-current": clip.url === current.url ? "true" : null,
      // Read out as the button's name. `clip.key` is an absolute path, which
      // is no name for a screen reader to announce, so it says what the clip
      // *is* and leaves the path to the caption line under the player.
      title: clipTitle(clip),
      "aria-label": clipTitle(clip),
      onclick: () => actions.showClip(clip.url),
    },
      clip.poster_url
        // The label is beside the image already; repeating it in `alt` would
        // make the name "it 200 it 200".
        ? el("img", { src: clip.poster_url, alt: "" })
        : el("span", { class: "thumb-vid", text: "▶" }),
      el("span", { class: "cap", text: clipLabel(clip) })));
  }
  // Why the strip stopped growing is the one thing a viewer cannot infer from
  // it, so it is said in the card rather than left in the event log.
  const trouble = (schedule && schedule.stopped_because)
    || (schedule && schedule.last_error ? `last clip: ${schedule.last_error}` : "");
  body.classList.add("film");
  body.append(clipStage(
    player("clips", current.url, current.poster_url,
           { play: state.playWanted === current.url }),
    { caption: cap, rail: clips.length > 1 ? strip : null,
      error: state.clipError || trouble }));
  // The strip is wider than the card and the clip playing can be anywhere in
  // it: left alone, it can sit at the far left showing an early iteration
  // while the player shows a late one.
  queueMicrotask(() => {
    scrollListTo(document.querySelector(".strip.clips .thumb[aria-current]"),
                 ".strip.clips", { center: true });
  });
  card.append(body);
  return card;
}

// The video the record kept.
//
// Distinct from the clip player on Control: that shows what is in `logs/`,
// which goes when the logs are cleaned. This shows what was promoted into the
// records' own media store -- what survives, and therefore what the record
// actually claims to have shown. `rlmcp record asset` is the same operation.
/** The clips promoted into the record.
 *
 * `bodyOnly` for the robot panel, which owns the tense switch and the header.
 * A card of its own beside that panel would put two video players on one
 * screen, and the one you wanted would depend on a distinction (`logs/`
 * against the record) that two headers do not make.
 */
export function recordVideo(state, actions, { bodyOnly = false } = {}) {
  const assets = ((state.detail || {}).record || {}).assets || {};
  const videos = assets.videos || [];
  const latest = videos[videos.length - 1];
  // What is on screen: the one picked, or the newest.
  const showing = videos.find(v => v[0] === state.keptPlaying) || latest;
  const clipPath = latestSessionClip(state);
  const keeping = state.keepingClip;

  const head = el("div", { class: "card-h" },
    el("h3", { text: "Kept video" }),
    videos.length ? chip(`${videos.length} on the record`) : chip("none kept"),
    el("span", { class: "sp" }),
    // Off by default, because turning it on copies files into the records.
    el("button", {
      class: `act${state.keepClips ? " on" : ""}`,
      "aria-pressed": state.keepClips ? "true" : "false",
      title: state.keepClips
        ? "new clips are promoted into the record as they arrive"
        : "clips stay in logs/ and go when the logs are cleaned",
      onclick: () => actions.toggleKeepClips(),
    }, state.keepClips ? "Keeping" : "Keep latest"),
    clipPath && !keeping
      ? el("button", { class: "act", title: clipPath,
                       onclick: () => actions.keepClip(clipPath, "kept from the studio") },
          "Keep this one")
      : null,
    keeping ? el("span", { class: "verb-spin" }) : null);

  const body = el("div", { class: bodyOnly ? "card-b robot-body" : "card-b" });
  if (showing) {
    const url = state.recordAssetUrl(showing[0]);
    const node = player("kept", url, "", { play: state.playWanted === url });
    const rail = videos.length > 1
      // Newest first, and the one playing is marked -- a row of buttons with
      // no current state leaves you guessing which one you are watching.
      ? el("div", { class: "kept-strip clip-rail" },
          videos.slice().reverse().slice(0, 10).map(([key, caption]) =>
            el("button", {
              class: "act", title: caption || key,
              "aria-pressed": key === showing[0] ? "true" : "false",
              onclick: () => actions.playKept(key, state.recordAssetUrl(key)),
            }, keptLabel(key))))
      : null;
    // The "keep this one" button acts on the *session's* newest clip, so it
    // belongs beside the answer to "is there one to keep". On the stage rather
    // than under it: a button that comes and goes under the picture moves the
    // picture, which is exactly what the stage exists to stop.
    const acts = bodyOnly && ((clipPath && !keeping) || keeping)
      ? el("div", { class: "clip-acts" },
          clipPath && !keeping
            ? el("button", { class: "act", title: clipPath,
                             onclick: () => actions.keepClip(clipPath, "kept from the studio") },
                "Keep the newest clip")
            : null,
          keeping ? el("span", { class: "verb-spin" }) : null)
      : null;
    body.classList.add("film");
    body.append(clipStage(node, {
      caption: showing[1] ? el("div", { class: "clip-cap" },
                               el("b", { text: keptLabel(showing[0]) }),
                               el("span", { text: showing[1] })) : null,
      rail, acts, error: state.clipError,
    }));
    if (bodyOnly) return body;
    return el("div", { class: "card" }, head, body);
  }

  body.append(notYet("No video on this record",
    "Clips in the run's logs are not part of the record — they go when the "
    + "logs do. Keep one and it is copied into the records' media store.",
    "record asset"));
  if (bodyOnly) {
    if (clipPath && !keeping) {
      body.append(el("div", { class: "kept-go" },
        el("button", { class: "act", title: clipPath,
                       onclick: () => actions.keepClip(clipPath, "kept from the studio") },
          "Keep the newest clip")));
    }
    if (keeping) body.append(el("span", { class: "verb-spin" }));
    return body;
  }
  return el("div", { class: "card" }, head, body);
}

/** A strip label that keeps the part that differs between clips. */
function keptLabel(key) {
  const stem = key.split("/").pop().replace(/\.[^.]+$/, "");
  const iteration = /_it0*(\d+)/.exec(stem);
  if (iteration) return `it ${Number(iteration[1]).toLocaleString("en-US")}`;
  return stem.length > 18 ? stem.slice(0, 17) + "…" : stem;
}

/** The newest clip sitting in the live session, which is what "keep" promotes. */
function latestSessionClip(state) {
  const clips = ((state.clips || {}).clips) || [];
  const fromSession = clips.filter(c => c.source !== "record");
  const last = fromSession[fromSession.length - 1] || clips[clips.length - 1];
  return (last && (last.path || last.file)) || "";
}
