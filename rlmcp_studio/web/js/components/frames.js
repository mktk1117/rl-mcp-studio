// The verbs (look, clip, diagnose), the frames they produce, and what a
// diagnosis says.

import { el, chip, fmt } from "../ui.js";
import { state, set } from "../state.js";
import { api } from "../api.js";
import { frameStage, composer } from "../annotate.js";
import { notYet } from "./rail.js";
import { frameCaption } from "./clips.js";

// The verbs. Same three everywhere; only their weight changes with the space
// around them, which is why the caller picks the variant.
const ICONS = {
  look: `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.2" y="3.6" width="13.6" height="10" rx="2"/><path d="M5.4 3.6 L6.6 1.6 h2.8 l1.2 2"/><circle cx="8" cy="8.7" r="2.6"/></svg>`,
  clip: `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="1.2" y="3.2" width="13.6" height="10.4" rx="2"/><path d="M1.2 6.2 H14.8 M1.2 10.6 H14.8" opacity=".4"/><path d="M6.6 6.9 L10 8.9 L6.6 10.9 Z" fill="currentColor" stroke="none"/></svg>`,
  diagnose: `<svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1 8.4 H3.4 L4.9 4.2 L7 12.2 L9.1 6.2 L10.5 8.4 H15"/></svg>`,
};

// The three verbs, and the rlmcp commands they are. Names are the studio's
// (a person "looks"), commands are rlmcp's.
const VERBS = [
  { key: "look", label: "Look", hint: "", cmd: "screenshot", args: {} },
  { key: "clip", label: "Clip", hint: "5s", cmd: "record_video", args: { seconds: 5 } },
  { key: "diagnose", label: "Diagnose", hint: "4s", cmd: "diagnose", args: { seconds: 4 } },
];

export function verbs({ variant = "chip", live = false, state = null, actions = null } = {}) {
  const busy = (state && state.running) || {};
  const done = (state && state.verbDone) || {};
  const title = live ? null : "needs a live trainer — this run has exited";
  const fire = (v) => actions && actions.runVerb(v.key, v.cmd, v.args);

  // A button that does nothing visible for eight seconds reads as broken, and
  // one that fails silently reads as done. So each carries its own state: a
  // turning ring while the trainer works on it, then a tick or a cross that
  // fades. Nothing moves position -- the indicator lives inside the button.
  const mark = (v) => {
    if (busy[v.key]) return el("span", { class: "verb-spin", title: "working…" });
    const outcome = done[v.key];
    if (!outcome) return null;
    return el("span", {
      class: `verb-mark ${outcome.ok ? "ok" : "bad"}`,
      title: outcome.message || (outcome.ok ? "done" : "failed"),
      text: outcome.ok ? "✓" : "✕",
    });
  };

  if (variant === "block") {
    return el("div", { class: "f-acts" },
      el("span", { class: "eyebrow", text: "do now" }),
      VERBS.map(v => el("button", {
        class: `verb${busy[v.key] ? " working" : ""}`,
        disabled: !live || !!busy[v.key], title, onclick: () => fire(v),
      },
        el("span", { html: ICONS[v.key] }),
        el("span", { text: v.label }),
        v.hint ? el("small", { text: v.hint }) : null,
        mark(v))),
      scopeChip(state, actions));
  }
  return el("span", { class: "acts" },
    VERBS.map(v => el("button", {
      class: `act verb-btn${busy[v.key] ? " working" : ""}`,
      disabled: !live || !!busy[v.key], title, onclick: () => fire(v),
    }, v.label,
       v.hint ? el("small", { text: v.hint }) : null,
       mark(v))),
    el("span", { class: "div" }),
    scopeChip(state, actions));
}

// Which environment the verbs act on.
//
// Nothing here is a list of criteria: the run publishes what `--where` accepts
// on *this* task under `status.selectors` (rl-mcp's Extension.selectors), and
// this renders whatever arrives. A task with a `holding=true` selector gets a
// menu entry for it with no commit to the studio, which is the test every
// panel should pass.
function whereLabel(where) {
  if (!where) return "all envs";
  return Object.entries(where).map(([k, v]) => `${k} ${v}`).join(" · ");
}

function scopeChip(state, actions) {
  // A caller with no state is a caller that cannot narrow anything -- and a
  // thrown TypeError here takes the *whole paint* down, leaving a header and
  // an empty page, because paint() has already cleared the tree by the time a
  // layout renders. Nothing in a render path may assume its arguments.
  if (!state) return null;
  const selectors = ((state.live && state.live.status) || {}).selectors || null;
  const open = !!state.scopeOpen;
  const chip = el("button", {
    class: "scope", "aria-expanded": open ? "true" : "false",
    title: selectors
      ? "which envs the verbs act on — the criteria this run publishes"
      : "this run publishes no env selectors; the verbs act on the env rlmcp picks",
    onclick: (e) => { e.stopPropagation(); actions && actions.toggleScope(); },
  }, "on ", el("b", { text: whereLabel(state.where) }), " ▾");

  if (!open) return el("span", { class: "menuwrap" }, chip);

  const menu = el("div", { class: "menu scopemenu" },
    el("div", { class: "menu-h" },
      el("span", { class: "eyebrow", text: "act on" })),
    el("button", {
      class: "mrow", "aria-current": state.where ? null : "true",
      onclick: () => actions.setWhere(null),
    }, el("span", { class: "dot", style: { background: "var(--dim)" } }),
       el("span", {}, el("span", { class: "mn", text: "all envs" }),
         el("span", { class: "mr", text: "whatever rlmcp picks" })),
       el("span", { class: "mc" })));

  if (!selectors) {
    // Missing capability, said out loud rather than hidden. The run may still
    // answer a criterion; it just cannot offer one. *Why* it cannot is two
    // different sentences, and the server knows which: rl-mcp#30
    // is merged upstream, so "needs rl-mcp#30" would be wrong here whenever
    // the installed copy already has it.
    const has = ((state.config && state.config.rlmcp) || {}).selectors;
    menu.append(has === false
      ? notYet("No selectors published",
          "The rl-mcp installed here predates `Extension.selectors`, so no "
          + "run can publish them yet. Upstream that is ",
          "rl-mcp#30")
      : notYet("No selectors published",
          "This run's extensions do not implement `Extension.selectors`, so "
          + "they do not advertise what `--where` accepts. The verbs still "
          + "act on the env rlmcp picks.", ""));
    return el("span", { class: "menuwrap" }, chip, menu);
  }

  for (const [key, spec] of Object.entries(selectors)) {
    const values = (spec && spec.values) || [];
    if (!values.length) continue;
    menu.append(el("div", { class: "menu-h" },
      el("span", { class: "eyebrow", text: spec.label || key }),
      spec.extension
        ? el("span", { class: "mr", text: ` from ${spec.extension}` }) : null));
    for (const value of values) {
      const chosen = state.where && state.where[key] === value;
      menu.append(el("button", {
        class: "mrow", "aria-current": chosen ? "true" : null,
        // The menu offers several independent criteria -- terrain, level,
        // holding -- each with its own selection dot, and `whereLabel` joins
        // them with " · ". Both promise combination, and replacing the whole
        // object would quietly drop whatever you picked before. Clicking a
        // chosen row clears just that criterion.
        onclick: () => actions.setWhere(
          chosen
            ? { ...(state.where || {}), [key]: undefined }
            : { ...(state.where || {}), [key]: value }),
      },
        el("span", { class: "dot",
                     style: { background: chosen ? "var(--accent)" : "var(--dim)" } }),
        el("span", {}, el("span", { class: "mn", text: String(value) })),
        el("span", { class: "mc", text: key })));
    }
  }
  return el("span", { class: "menuwrap" }, chip, menu);
}

// Whatever the run has produced, newest first. Frames are the reason to have a
// GUI at all: a metric says the number moved, a frame says what the robot did.
export function framesCard(state, actions, { withVerbs = true, bodyOnly = false } = {}) {
  const frames = state.frames || [];
  const shots = frames.filter(f => ["png", "jpg", "jpeg", "gif"].includes(f.kind));
  const clips = frames.filter(f => ["mp4", "webm"].includes(f.kind));
  const latest = state.lastFrame || (shots[0] && shots[0].path);

  const card = el("div", { class: bodyOnly ? "card-headless" : "card" },
    bodyOnly ? null : el("div", { class: "card-h" },
      el("h3", { text: "Live frames" }),
      // A frame you picked stays until you say otherwise, and a header that
      // still says "Live" over a frame from 2,000 iterations ago is a lie the
      // panel tells by omission.
      state.framePicked ? chip("pinned", true) : null,
      state.commandError ? chip("failed") : null,
      el("span", { class: "sp" }),
      state.framePicked
        ? el("button", { class: "act", title: "follow the newest frame again",
                         onclick: () => actions.liveFrames() }, "Back to live")
        : null,
      // The Console keeps its verbs in the composer bar, so this header would
      // be a second copy of the same three buttons -- and, because the scope
      // menu is driven by one flag, clicking either chip would open both.
      withVerbs
        ? verbs({ variant: "chip",
                  live: !!(state.live && state.live.ok && state.live.state === "running"),
                  state, actions })
        : null));

  const body = el("div", { class: "card-b" });
  const alive = !!(state.live && state.live.ok && state.live.state === "running");
  if (state.commandError) {
    body.append(el("div", { class: "m-error", text: state.commandError }));
  }
  // Headless -- inside the robot card -- the verbs come with the body, because
  // the header they would otherwise use belongs to that card and already
  // carries a three-way switch. This body says "Press Look" in every layout,
  // so every layout needs a Look on screen to press.
  if (bodyOnly && withVerbs) {
    body.append(verbs({ variant: "chip", live: alive, state, actions }));
  }
  if (latest) {
    // The frame is not a picture of the run, it is a surface you can point at:
    // drag a box and the region, the remark and the image go to the record and
    // to the agent together. See annotate.js.
    body.append(frameStage(state, actions, latest));
    body.append(composer(state, actions, latest));
  } else if (alive) {
    body.append(notYet("No frame yet",
      "Press Look and the trainer renders one between rollout batches — it never pauses to do it.",
      "screenshot"));
  } else {
    // Telling somebody to press a button that is greyed out is worse than
    // saying nothing: the instruction is the only thing on the panel and it
    // cannot be followed.
    body.append(notYet("No frames on this run",
      "Nothing is training, so none can be rendered now. Frames a run made "
      + "while it was alive stay in its record; this one kept none.", ""));
  }

  // Frames only. Clips have their own panel with a player, an order and a
  // poster; listing them here as well would show the same clip twice and,
  // after a few of them, leave the frame strip mostly video tiles.
  if (shots.length) {
    const strip = el("div", { class: "strip" });
    for (const f of shots.slice(0, 12)) {
      strip.append(el("button", {
        class: "thumb", "aria-current": f.path === latest ? "true" : null,
        title: f.name, onclick: () => actions.showFrame(f.path),
      },
        // `alt` is the fallback when the file is not there -- an artifact
        // from another machine, a cleared logs directory -- and a blank one
        // lays the tile out as an empty box with nothing to read.
        el("img", { src: state.artifactUrl(f.path), alt: f.name, loading: "lazy" }),
        el("span", { class: "cap", text: frameCaption(f.name) })));
    }
    body.append(strip);
  }

  if (state.diagnosis) {
    body.append(diagnosisBlock(state.diagnosis));
  }
  card.append(body);
  return card;
}

// What the numbers rlmcp reports are *in*. Only what is unambiguous: a unit
// guessed wrong is worse than none, so anything not named here is shown bare.
const DIAGNOSIS_UNITS = {
  seconds: "s", duration: "s", median_air_s: "s", window_s: "s",
  jerk_rms: "rad/s³", effort_rms: "N·m", joint_vel_rms: "rad/s",
  joint_acc_rms: "rad/s²", tilt_deg_mean: "°", transitions_per_min: "/min",
};

// A fraction is a fraction: `hf_share 0.171` reads as a count until it says so.
const FRACTION = /(_frac|_share|_ratio)$/;

function diagnosisBlock(d) {
  const rows = Object.entries(d).filter(([, v]) => typeof v === "number");
  const verdict = d.verdict || d.summary || d.advice || "";
  // Anything that is not a number and not the verdict -- `worst_joint`, say --
  // is shown too: it is often the most useful line in the answer.
  const named = Object.entries(d).filter(([k, v]) =>
      typeof v === "string" && v && !["verdict", "summary", "advice"].includes(k));

  return el("div", { class: "diagnosis" },
    el("div", { class: "measures" },
      rows.slice(0, 8).map(([k, v]) => el("div", { class: "measure" },
        el("div", { class: "l", text: k.replace(/_/g, " ") }),
        el("div", { class: "v" },
          FRACTION.test(k) ? `${(v * 100).toFixed(1)}%` : fmt(v),
          DIAGNOSIS_UNITS[k]
            ? el("span", { class: "unit", text: ` ${DIAGNOSIS_UNITS[k]}` })
            : null))),
      named.slice(0, 4).map(([k, v]) => el("div", { class: "measure" },
        el("div", { class: "l", text: k.replace(/_/g, " ") }),
        el("div", { class: "v s", text: v })))),
    verdict ? el("div", { class: "verdict-line" }, el("p", { text: String(verdict) })) : null,
    traceCharts(d));
}

// The signals the diagnosis was computed from, drawn like every other curve
// here rather than shown as matplotlib's picture. Which channel matters
// depends on the question, so the channel is a choice and the default is the
// one the verdict is usually about.
const TRACE_PREFERRED = ["joint_vel", "action", "joint_torque", "joint_acc", "reward"];

function traceCharts(d) {
  const path = d.trace_path;
  if (!path) return null;

  const wrap = el("div", { class: "tracewrap" });
  const trace = state.trace;

  if (!trace) {
    if (state.traceStamp !== path) {
      set({ traceStamp: path });
      api.trace(path)
        .then(payload => { if (state.traceStamp === path) set({ trace: payload }); })
        .catch(err => { if (state.traceStamp === path) set({ trace: { ok: false, reason: err.message } }); });
    }
    wrap.append(el("div", { class: "empty", text: "reading the trace…" }));
    return wrap;
  }
  if (!trace.ok) {
    // The PNG is still there, so say what is missing rather than nothing.
    wrap.append(el("div", { class: "empty", text: trace.reason || "no trace" }));
    return wrap;
  }

  const names = Object.keys(trace.channels || {});
  if (!names.length) return null;
  const chosen = state.traceChannel && names.includes(state.traceChannel)
    ? state.traceChannel
    : (TRACE_PREFERRED.find(n => names.includes(n)) || names[0]);
  const components = trace.channels[chosen] || {};
  const labels = Object.keys(components);

  wrap.append(el("div", { class: "trace-head" },
    el("span", { class: "eyebrow", text: `trace · ${trace.duration_s}s · ${trace.samples} samples` }),
    el("span", { class: "sp" }),
    el("select", { class: "fb-kind",
                   onchange: (e) => set({ traceChannel: e.target.value }) },
      names.map(n => el("option", { value: n, selected: n === chosen }, n)))));

  // One chart per component would be 29 charts for a humanoid's joints. They
  // go on one axis instead, which is also how you see one joint diverging.
  const merged = labels.map(label => components[label]);
  wrap.append(multiLine(merged, labels, chosen));
  return wrap;
}

function multiLine(seriesList, labels, name) {
  const flat = seriesList.flat();
  if (flat.length < 2) return el("div", { class: "empty", text: "not enough samples" });
  const width = 900, height = 240, pad = 30;
  const xs = flat.map(p => p[0]), ys = flat.map(p => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  const span = (hi - lo) || Math.abs(hi) || 1;
  lo -= span * 0.1; hi += span * 0.1;
  const X = v => pad + ((v - x0) / ((x1 - x0) || 1)) * (width - pad * 2);
  const Y = v => height - pad - ((v - lo) / ((hi - lo) || 1)) * (height - pad * 2);

  // Enough hues to tell lines apart, taken round the wheel from the accent so
  // they belong to the same palette rather than being a second one.
  const paths = seriesList.map((series, i) => {
    const hue = (i * 47) % 360;
    const d = series.map((p, j) => `${j ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="hsl(${hue} 70% 62%)" stroke-width="1.1"
                  stroke-linejoin="round" opacity=".85"><title>${labels[i]}</title></path>`;
  }).join("");

  const grid = [0.25, 0.5, 0.75].map(f =>
    `<path d="M${pad} ${(height * f).toFixed(0)} H${width - pad}" stroke="var(--line-soft)" stroke-width="1"/>`
  ).join("");

  return el("div", { class: "chart trace-chart", html:
    `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" style="height:${height}px">
       ${grid}${paths}
       <text x="${pad}" y="12" fill="var(--dim)" font-size="9"
             font-family="ui-monospace, monospace">${name} — ${labels.length} signal${labels.length === 1 ? "" : "s"}</text>
       <text x="${pad}" y="${height - 8}" fill="var(--dim)" font-size="9"
             font-family="ui-monospace, monospace">${x0.toFixed(2)}s</text>
       <text x="${width - pad}" y="${height - 8}" fill="var(--dim)" font-size="9"
             text-anchor="end" font-family="ui-monospace, monospace">${x1.toFixed(2)}s</text>
     </svg>` });
}
