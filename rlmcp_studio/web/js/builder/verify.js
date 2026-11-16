// The verification strip and card, and a frame from it opened large.

import { el, chip, fmt, add } from "../ui.js";
import { state, set } from "../state.js";
import { api } from "../api.js";

// ── the verification ────────────────────────────────────────────────────
//
// The gate strip answers one question -- does this environment survive a zero
// policy -- and a task can pass all six of its gates while most of its reward
// terms pay exactly nothing, nothing ever terminates, and an episode can run
// for 5e10 seconds. None of that is a failed gate. All of it is in numbers
// `rlmcp check` already returns, and the gates do not read them against each
// other.
//
// So the suite is five questions rather than one: the task survives doing
// nothing, it survives being shaken, the scene it built is the right shape,
// every term it declares does something, and it looks right. The last one is
// pictures, because a robot spawned inside the floor passes every gate there
// is and is one glance to see.
//
// Two surfaces, for the same reason the gates have two: a strip, which is the
// state of the whole view and has to be readable from whichever pane you were
// in, and a card, which is the evidence.

const VERIFY_ORDER = ["zero", "random", "scene", "components", "look"];

const VERIFY_LABEL = {
  zero: "does nothing",
  random: "gets shaken",
  scene: "scene",
  components: "terms",
  look: "looks right",
};

const VERDICT_WORD = {
  pass: "verified",
  warn: "worth a look",
  fail: "not ready",
  running: "verifying…",
  skip: "not asked",
};

const TICK = { pass: "✓", warn: "!", fail: "✗", running: "·", skip: "·" };

/** The state of the whole suite, in one line, above the panes. */
export function verifyStrip(build, s, actions) {
  const verify = build.verify || {};
  const report = verify.report || null;
  const byId = {};
  for (const step of (report && report.steps) || []) byId[step.id] = step;

  const strip = el("div", { class: "gates verify-strip" },
    el("span", { class: "verify-tag", text: "verify" }));

  for (const id of VERIFY_ORDER) {
    const step = byId[id];
    // The same three-state rule the gates follow, and for the same reason: a
    // question nobody asked is not a question that passed.
    const state = step ? step.state : "wait";
    add(strip, el("span", { class: `gate ${state === "wait" ? "wait" : state}`,
                            title: step ? [step.says, step.detail].filter(Boolean).join(" — ")
                                        : "not asked yet" },
      el("span", { class: "tick", text: step ? (TICK[state] || "·") : "·" }),
      VERIFY_LABEL[id] || id));
  }

  add(strip,
    report
      ? el("span", { class: `stamp${report.verdict === "pass" ? "" : " warn"}`,
                     title: report.error || "",
                     text: verify.running
                       ? `${report.stage || "working"}…`
                       : `${VERDICT_WORD[report.verdict] || report.verdict} · `
                         + `${fmt(report.seconds, 0)} s · ${fmt(verify.age_s, 0)} s ago` })
      : el("span", { class: "stamp", text: "not verified yet" }),
    el("span", { class: "sp" }),
    verify.running
      ? el("button", { class: "act", onclick: () => actions.stopVerify() }, "Stop")
      : el("button", { class: "act",
                       title: "check it doing nothing, check it being shaken, read "
                            + "every term, and take frames of both — a few minutes",
                       onclick: () => actions.runVerify() },
          report ? "Verify again" : "Verify the build"));

  return el("div", { class: "gates-wrap" }, strip);
}

/** The evidence: what each step found, the frames, and the dead terms. */
export function verifyCard(build, s, actions) {
  const verify = build.verify || {};
  const report = verify.report || null;

  const head = el("div", { class: "card-h" },
    el("h3", { text: "Verification" }),
    report ? chip(VERDICT_WORD[report.verdict] || report.verdict,
                  report.verdict === "pass") : null,
    el("span", { class: "sp" }),
    report && report.how
      ? el("span", { class: "eyebrow",
                     text: `${report.how.steps} steps · ${report.how.num_envs} envs` })
      : null);

  if (!report) {
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b" },
        el("p", { class: "muted",
                  text: "Nothing has verified this task. The gates above answer "
                      + "whether it survives doing nothing; this answers whether "
                      + "it also survives being shaken, whether every term it "
                      + "declares actually pays, and what it looks like — with "
                      + "frames, under both policies." })));
  }

  const body = el("div", { class: "card-b verify-b" });
  if (report.error) add(body, el("div", { class: "m-error", text: report.error }));

  for (const step of report.steps || []) {
    add(body, el("div", { class: `v-step ${step.state}` },
      el("div", { class: "v-head" },
        el("span", { class: "tick", text: TICK[step.state] || "·" }),
        el("b", { text: step.label }),
        el("span", { class: "sp" }),
        el("span", { class: "v-says", text: step.says || "" })),
      step.detail ? el("p", { class: "v-detail", text: step.detail }) : null));
  }

  const shots = report.shots || [];
  if (shots.length) add(body, filmstrip(shots));
  const dead = (report.components || []).filter(c => c.state !== "pass");
  if (dead.length) add(body, componentList(dead, report.components.length));
  return el("div", { class: "card" }, head, body);
}

/** The frames, under each policy, in the order they were taken.
 *
 * They size themselves to the column, which is half the view. A thumbnail is a
 * prompt to look rather than the looking, so each one opens the full render —
 * *here*, in the app, the way an enlarged chart does. A link with
 * `target="_blank"` would answer the same question by throwing you out of the
 * studio: the new tab has the picture and none of the run it belongs to, and
 * coming back means finding the studio's tab again. The overlay keeps the
 * full-size link in its header, for the times the browser's own zoom is what
 * you actually want.
 */
function filmstrip(shots) {
  const wrap = el("div", { class: "filmstrip" });
  const policies = [];
  for (const shot of shots) if (!policies.includes(shot.policy)) policies.push(shot.policy);

  for (const policy of policies) {
    const mine = shots.filter(sh => sh.policy === policy);
    add(wrap,
      el("div", { class: "film-row" },
        el("span", { class: "film-label",
                     text: policy === "zero" ? "zero actions" : "random actions" }),
        el("div", { class: "film-frames" },
          mine.map(shot => el("button", {
            class: "film-frame",
            title: `${policy}, frame ${shot.index + 1} of ${mine.length} — click to enlarge`,
            onclick: () => set({ frameZoom: { path: shot.path, policy: shot.policy,
                                              index: shot.index } }),
          }, el("img", { src: api.artifactUrl(shot.path), loading: "lazy",
                         alt: `${policy} policy, frame ${shot.index + 1}` }))))));
  }
  return wrap;
}

// ── a frame, opened large ───────────────────────────────────────────────
//
// The same window an enlarged chart gets, for the same reason: the detail is
// the whole point of a frame, and a 110 px thumbnail is too small to see the
// foot that is through the floor. It reads the strip out of the store on every
// paint rather than the copy taken when it was clicked, so a re-verify under an
// open frame moves it along instead of leaving a picture that no longer exists.

/** The frames the open one belongs to, and where in them it is. */
function frameStrip(s) {
  const open = s.frameZoom;
  if (!open) return null;
  const shots = (((s.build || {}).verify || {}).report || {}).shots || [];
  const at = shots.findIndex(sh => sh.path === open.path);
  // A verify that has since replaced this frame leaves it on screen alone,
  // rather than shutting the window under whoever is looking at it.
  if (at < 0) return { shots: [open], at: 0 };
  return { shots, at };
}

/** Close the opened frame. */
export function closeFrame() { set({ frameZoom: null }); }

/** Move `delta` frames along the strip, across both policies, and stop at the
 *  ends rather than wrapping — the order is the order they were taken in, and
 *  wrapping from the last random frame back to the first zero one reads as a
 *  reset. */
export function stepFrame(delta) {
  const strip = frameStrip(state);
  if (!strip) return;
  const next = strip.shots[strip.at + delta];
  if (!next) return;
  set({ frameZoom: { path: next.path, policy: next.policy, index: next.index } });
}

export function frameOverlay(s, actions) {
  const strip = frameStrip(s);
  if (!strip) return null;
  const { shots, at } = strip;
  const shot = shots[at];
  const url = api.artifactUrl(shot.path);
  const label = shot.policy === "zero" ? "zero actions" : "random actions";

  const step = (delta, text, title) => el("button", {
    class: "act", title, disabled: !shots[at + delta],
    onclick: () => stepFrame(delta),
  }, text);

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) closeFrame(); },
  },
    el("div", { class: "z-box framebox" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: label }),
        el("span", { class: "eyebrow", text: `frame ${at + 1} of ${shots.length}` }),
        el("span", { class: "sp" }),
        // The way out of the app, kept as a choice rather than the default:
        // the browser's own zoom and "save image as" live out there.
        el("a", { class: "act", href: url, target: "_blank", rel: "noopener",
                  title: "open the full-size render in a browser tab" }, "Full size \u2197"),
        el("button", { class: "act", onclick: () => closeFrame() }, "Close")),
      el("div", { class: "frame-plot" },
        el("img", { class: "frame-img", src: url, key: url,
                    alt: `${shot.policy} policy, frame ${shot.index + 1}` })),
      el("div", { class: "frame-nav" },
        step(-1, "\u2039 Previous", "the frame before this one"),
        step(1, "Next \u203A", "the frame after this one"),
        el("span", { class: "sp" }),
        el("span", { class: "z-hint", text: "\u2190 \u2192 to step, Esc to close" }))));
}

/** The terms that are not fine, named. See `sources/verify.py`.
 *
 * Only those: a list of rows most of which say "ok" is a list nobody reads,
 * and the few that matter are the reason this stage exists.
 */
function componentList(rows, total) {
  const box = el("div", { class: "v-terms" },
    el("div", { class: "v-terms-h",
                text: `${rows.length} of ${total} terms want looking at` }));
  for (const row of rows) {
    add(box, el("div", { class: `v-term ${row.state}` },
      el("span", { class: "nm", title: `${row.kind} term`, text: row.term }),
      el("span", { class: "v-why", text: row.says }),
      el("span", { class: "v", title: "zero policy · random policy",
                   text: `${num(row.zero)} / ${num(row.random)}` })));
  }
  return box;
}

/** A term's payment, short enough for a column this wide. */
function num(value) {
  if (value === null || value === undefined) return "—";
  if (value === 0) return "0";
  return Math.abs(value) < 0.001 ? value.toExponential(0) : fmt(value, 3);
}
