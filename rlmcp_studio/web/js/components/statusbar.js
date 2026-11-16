// The status bar along the bottom, and the GPU cells in it.

import { el, dot, fmt, verdictColor, add, elapsed } from "../ui.js";
import { pickHeadline, short } from "./metrics.js";

/** The machine underneath: one cell per GPU, opening onto the detail.
 *
 * In the bar rather than as a view of its own, because it is a *condition*
 * rather than a subject -- the same category as "connected" and "trainer
 * running", and you look at it while doing something else. Written as a
 * list: one card is a single workstation, four is the ordinary case for the
 * work this app is for, and a studio watching a trainer on another machine
 * fills the same shape from what that machine reports.
 *
 * The chip is the glance; the popup is `nvidia-smi` for someone who does not
 * want to open a terminal to read it: every gauge the card reports, why its
 * clocks are being held down, the last minutes of load, and each process by
 * a name a person recognises -- the run where it is one of this studio's,
 * the command where it is not.
 */
export function gpuCells(state, actions) {
  const snap = state.gpu;
  if (!snap || !snap.ok || !(snap.devices || []).length) return null;
  const devices = snap.devices;
  const open = !!state.gpuOpen;

  // One cell each while there are few; a single summary once there are many,
  // because a bar of eight identical chips is a bar nobody reads.
  const summary = devices.length > 3;
  const button = el("button", {
    class: `st gpu-chip${open ? " on" : ""}`,
    "aria-expanded": open ? "true" : "false",
    title: "what the GPUs are doing, and which run is doing it — click for the detail",
    onclick: (e) => { e.stopPropagation(); actions.toggleGpu(); },
  }, summary
       ? el("span", {}, `${devices.length} GPUs `,
            el("b", { text: `${Math.round(Math.max(...devices.map(d => d.util || 0)))}%` }))
       : devices.map(d => el("span", { class: "gpu-one" },
           el("span", { class: "gpu-i", text: `gpu${d.index}` }),
           el("b", { text: `${Math.round(d.util || 0)}%` }),
           el("span", { class: "gpu-mem", text: memLabel(d) }))));

  // Said beside the number, because the symptom lands on the person as "the
  // page flickers" and nothing else on screen connects it to the card being
  // full. The remedy is the browser's, not the studio's.
  const starved = devices.some(d => (d.util || 0) >= 95 && (d.memory_frac || 0) >= 0.85);
  const warn = starved
    ? el("span", { class: "st gpu-starve",
                   title: "The training run holds this GPU at capacity, and Chrome "
                        + "composites with the same GPU. If this page flickers or "
                        + "blanks, start Chrome with --disable-gpu-compositing: the "
                        + "page is drawn on the CPU and the 3-D view keeps WebGL on "
                        + "the card. Turning graphics acceleration off in settings "
                        + "also works but takes WebGL, and the 3-D view, with it. "
                        + "The studio pauses its own animations while the card is "
                        + "this busy." },
         "gpu saturated \u00b7 page may flicker")
    : null;
  if (!open) return el("span", { class: "menuwrap" }, button, warn);
  const history = state.gpuHistory || {};
  const where = [snap.host, snap.driver ? `driver ${snap.driver}` : ""]
    .filter(Boolean).join(" · ");
  return el("span", { class: "menuwrap" }, button,
    // A click inside is a click on the popup, not on the page behind it --
    // the page-level handler that closes menus must not see it.
    el("div", { class: "menu gpumenu", onclick: (e) => e.stopPropagation() },
      el("div", { class: "menu-h gpu-menu-h" },
        el("span", { class: "eyebrow", text: "graphics processors" }),
        el("span", { class: "sp" }),
        el("span", { class: "gpu-asof", title: "read from nvidia-smi on the studio's machine",
                     text: `${where}${where ? " · " : ""}as of ${clockOf(snap.at)}` })),
      devices.map(d => gpuRow(d, history[d.index] || [])),
      el("div", { class: "gpu-foot",
                  text: "refreshed every 2 s while this is open. a process this studio "
                      + "cannot name is still listed — something else holding the card "
                      + "is what you want to know before launching." })));
}

function memLabel(d) {
  if (d.memory_used_mb == null || !d.memory_total_mb) return "";
  const gb = (v) => (v / 1024).toFixed(v / 1024 >= 10 ? 0 : 1);
  return `${gb(d.memory_used_mb)}/${gb(d.memory_total_mb)}G`;
}

function clockOf(epoch) {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleTimeString("en-GB");
}

const gib = (mb) => `${(mb / 1024).toFixed(1)} GiB`;

/** One measured quantity: a name, the figure, and the fraction as a bar. A
 * bar rather than a number because every one of these is a comparison --
 * used against total, draw against limit, clock against its ceiling -- and a
 * comparison is a picture. `warn` is the fraction past which the bar turns
 * the warning colour; null means it never does (a fast clock is not a
 * problem). */
function gauge(label, text, frac, warn = 0.9, note = "") {
  if (text == null) return null;
  const f = frac == null ? null : Math.max(0, Math.min(1, frac));
  return el("div", { class: "gpu-gauge" },
    el("div", { class: "gpu-gauge-l" },
      el("span", { class: "gpu-gauge-k", text: label }),
      el("span", { class: "sp" }),
      el("span", { class: "gpu-gauge-v", text }),
      note ? el("span", { class: "gpu-gauge-n", text: note }) : null),
    el("div", { class: "gpu-bar" },
      f == null ? null
        : el("i", { class: warn != null && f > warn ? "full" : "",
                    style: { width: `${Math.round(f * 100)}%` } })));
}

function gpuRow(d, hist) {
  const n = (v, digits = 0) => (v == null ? null : Number(v).toFixed(digits));
  const pair = (a, b, unit, digits = 0) =>
    a == null ? null : `${n(a, digits)}${b != null ? ` / ${n(b, digits)}` : ""} ${unit}`;
  const facts = [
    d.temperature_c != null ? `${Math.round(d.temperature_c)}°C` : "",
    d.fan_pct != null ? `fan ${Math.round(d.fan_pct)}%` : "",
    d.pstate ? d.pstate : "",
    d.pcie_gen != null
      ? `PCIe gen${n(d.pcie_gen)} x${n(d.pcie_width)}`
        + (d.pcie_gen_max != null && (d.pcie_gen_max !== d.pcie_gen || d.pcie_width_max !== d.pcie_width)
           ? ` (of gen${n(d.pcie_gen_max)} x${n(d.pcie_width_max)})` : "")
      : "",
    d.uuid ? d.uuid.replace(/^GPU-/, "").slice(0, 8) : "",
  ].filter(Boolean);
  const throttle = d.throttle || [];
  const procs = d.processes || [];
  const shown = procs.slice(0, 12);

  return el("div", { class: "gpu-row" },
    el("div", { class: "gpu-head" },
      el("b", { text: `gpu${d.index}` }),
      el("span", { class: "gpu-name", text: d.name || "" }),
      el("span", { class: "sp" }),
      d.temperature_c != null
        ? el("span", { class: `gpu-t${d.temperature_c >= 83 ? " hot" : ""}`,
                       text: `${Math.round(d.temperature_c)}°C` })
        : null,
      el("span", { class: "gpu-t", text: `${Math.round(d.util || 0)}% busy` })),

    el("div", { class: "gpu-gauges" },
      gauge("compute", d.util != null ? `${Math.round(d.util)}%` : null,
            d.util != null ? d.util / 100 : null, null),
      gauge("memory",
            d.memory_used_mb != null && d.memory_total_mb
              ? `${gib(d.memory_used_mb)} / ${gib(d.memory_total_mb)}` : null,
            d.memory_frac, 0.8,
            d.memory_reserved_mb ? `${Math.round(d.memory_reserved_mb)} MiB reserved` : ""),
      gauge("memory bus", d.memory_util != null ? `${Math.round(d.memory_util)}%` : null,
            d.memory_util != null ? d.memory_util / 100 : null, null),
      gauge("power", pair(d.power_w, d.power_limit_w, "W"),
            d.power_w != null && d.power_limit_w ? d.power_w / d.power_limit_w : null, 0.95),
      gauge("sm clock", pair(d.clock_sm_mhz, d.clock_sm_max_mhz, "MHz"),
            d.clock_sm_mhz != null && d.clock_sm_max_mhz ? d.clock_sm_mhz / d.clock_sm_max_mhz : null, null),
      gauge("memory clock", pair(d.clock_mem_mhz, d.clock_mem_max_mhz, "MHz"),
            d.clock_mem_mhz != null && d.clock_mem_max_mhz ? d.clock_mem_mhz / d.clock_mem_max_mhz : null, null)),

    facts.length ? el("div", { class: "gpu-facts", text: facts.join(" · ") }) : null,
    // Why the clocks are below their ceiling, when the driver says so. The
    // power cap is ordinary under a full load; a thermal slowdown is the one
    // to act on, and it is named for what it is rather than as a bit.
    throttle.length
      ? el("div", { class: `gpu-throttle${throttle.some(t => /thermal|brake|hardware/.test(t)) ? " bad" : ""}`,
                    text: `clocks held down: ${throttle.join(", ")}` })
      : null,

    hist.length > 2 ? gpuSpark(hist) : null,

    procs.length
      ? el("div", { class: "gpu-procs" },
          el("div", { class: "gpu-proc gpu-proc-h" },
            el("span", { class: "gpu-who", text: "process" }),
            el("span", { class: "gpu-user", text: "user" }),
            el("span", { class: "gpu-pid", text: "pid" }),
            el("span", { class: "gpu-for", text: "for" }),
            el("span", { class: "gpu-pm", text: "memory" })),
          shown.map(p =>
            el("div", { class: `gpu-proc${p.mine ? " mine" : ""}`, title: p.command || "" },
              el("span", { class: "gpu-who",
                           text: p.mine ? `${p.run} · ${p.task || ""}`.trim()
                                        : (p.name || `pid ${p.pid}`) }),
              el("span", { class: "gpu-user", text: p.user || "" }),
              el("span", { class: "gpu-pid", text: String(p.pid) }),
              el("span", { class: "gpu-for", text: p.for_seconds ? elapsed(p.for_seconds) : "" }),
              el("span", { class: "gpu-pm",
                           text: p.memory_mb != null ? `${(p.memory_mb / 1024).toFixed(1)}G` : "" }))),
          procs.length > shown.length
            ? el("div", { class: "gpu-idle", text: `and ${procs.length - shown.length} more` })
            : null)
      : el("div", { class: "gpu-idle", text: "nothing is using this card" }));
}

/** The last minutes of load, as two lines: compute in the accent, memory
 * dimmer behind it. Kept in the browser, because nothing records it -- it
 * starts when the page opens and only says what this tab has seen. */
function gpuSpark(hist) {
  const w = 300, h = 34;
  const t0 = hist[0][0], t1 = hist[hist.length - 1][0];
  const span = Math.max(1, t1 - t0);
  const path = (pick) => hist.map(([t, ...v], i) => {
    const y = pick(v);
    const x = ((t - t0) / span) * w;
    return `${i ? "L" : "M"}${x.toFixed(1)},${(h - 1 - (y == null ? 0 : y) * (h - 2)).toFixed(1)}`;
  }).join(" ");
  const secs = Math.round(span);
  const label = secs >= 90 ? `last ${Math.round(secs / 60)} min` : `last ${secs} s`;
  return el("div", { class: "gpu-spark" },
    el("div", { class: "gpu-spark-l" },
      el("span", { text: label }),
      el("span", { class: "sp" }),
      el("span", { class: "gpu-spark-k util", text: "compute" }),
      el("span", { class: "gpu-spark-k mem", text: "memory" })),
    el("div", { html:
      `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" width="100%" height="${h}">
         <path d="${path(v => v[1])}" fill="none" stroke="var(--dim)" stroke-width="1"
               vector-effect="non-scaling-stroke" opacity=".7"/>
         <path d="${path(v => v[0])}" fill="none" stroke="var(--accent)" stroke-width="1.4"
               vector-effect="non-scaling-stroke"/>
       </svg>` }));
}

/** Keep a bar menu inside the window.
 *
 * A bar menu is `position: fixed` (see the stylesheet: the bar scrolls, and a
 * scrolling box clips what is positioned inside it) and hangs from its chip's
 * left edge. The GPU chip sits near the right of the bar, so in a narrow
 * window a 540 px popup would run off the right-hand side. Measured after
 * paint, the way `clampMenu` does for the chrome bar's menus; found through
 * the DOM rather than a closure, because the reconciler may keep a different
 * node than the one this render built.
 *
 * Pinned by `right` rather than slid by `left`: a fixed box with only `left`
 * set is as wide as the room to its right, so every measurement would see a
 * different width and the menu would creep eight pixels per repaint.
 */
const BAR_MENU_PLACE = {};

function placeBarMenus() {
  const edge = 8;
  for (const m of document.querySelectorAll(".statusbar .menu")) {
    m.style.left = "";
    m.style.right = "";
    const box = m.getBoundingClientRect();
    if (box.right > window.innerWidth - edge) m.style.right = `${edge}px`;
    else if (box.left < edge) m.style.left = `${edge}px`;
    // Remembered so the next render can build the menu already in place: the
    // reconciler drops an inline style the new tree does not carry, and a
    // popup that repaints every two seconds would otherwise jump each time.
    BAR_MENU_PLACE[m.className] = m.getAttribute("style") || "";
  }
}

export function statusbar(run, live, state = null, actions = null) {
  const bar = el("div", { class: "statusbar", key: "statusbar" });
  const status = (live && live.status) || {};
  const iteration = status.iteration ?? status.iter ?? null;

  bar.append(el("div", { class: "st" }, "iter ",
    el("b", { text: iteration === null ? "—" : Number(iteration).toLocaleString("en-US") })));

  // How big, how fast. `num_envs` is published; the other two are not, and
  // are derived here rather than asked for upstream because they are a
  // *presentation* of what the metrics rows already carry -- each row has its
  // own wall-clock `t`, so the gap between two of them is what an iteration
  // costs, and the rest is arithmetic on numbers the run states itself.
  if (status.num_envs) {
    bar.append(el("div", { class: "st" }, "envs ",
      el("b", { text: Number(status.num_envs).toLocaleString("en-US") })));
  }
  // Only while a trainer is actually running. A rate is a claim about now, and
  // this app's rule is that liveness is a fact about a process rather than
  // about a verdict -- an fps left on screen beside a run that stopped an hour
  // ago is the confidently-wrong kind of number.
  const pace = live && live.ok && live.state === "running" ? iterationPace(live) : null;
  if (pace) {
    bar.append(el("div", { class: "st" }, "s/iter ",
      el("b", { title: "median over the last iterations", text: secs(pace.seconds) })));
    if (pace.fps) {
      bar.append(el("div", { class: "st" }, "fps ",
        el("b", { title: `${Math.round(pace.fps).toLocaleString("en-US")} env steps per second`,
                  text: rate(pace.fps) })));
    }
  }

  if (run && run.stage && run.stage !== "default") {
    bar.append(el("div", { class: "st" }, "stage ", el("b", { text: run.stage })));
  }

  const series = (live && live.series) || {};
  const headline = pickHeadline(series, live);
  // The bar scrolls sideways when it does not fit, and what falls off the end
  // is the right-hand group -- the verdict and whether the trainer is alive,
  // which is the part you glance down for. So the metrics past the first are
  // marked as the ones a narrow window drops.
  headline.forEach(([name, points], index) => {
    const last = points[points.length - 1][1];
    bar.append(el("div", { class: `st${index ? " sst" : ""}` },
                  `${short(name)} `, el("b", { text: fmt(last) })));
  });

  if (run) {
    bar.append(el("div", { class: "st" }, "verdict ",
      el("b", { style: { color: verdictColor(run.verdict) }, text: run.verdict })));
  }
  if (run && run.feedback_open) {
    bar.append(el("div", { class: "st attn" }, "feedback ",
      el("b", { text: `${run.feedback_open} unanswered` })));
  }
  // The one place that answers "is this run alive right now". A verdict is a
  // claim about an experiment; this is a fact about a process, and when they
  // disagree -- which is most of the time an hour after a run stops -- this is
  // the one to believe.
  // `state` is the store; the trainer's own state needs its own name because
  // the bar is handed both.
  const trainer = live && live.ok ? live.state : "no session";
  if (state && actions) {
    add(bar, gpuCells(state, actions), unrecordedCell(state, actions));
    for (const m of bar.querySelectorAll(".menu")) {
      if (BAR_MENU_PLACE[m.className]) m.setAttribute("style", BAR_MENU_PLACE[m.className]);
    }
    if (state.gpuOpen || state.unrecOpen) requestAnimationFrame(placeBarMenus);
  }
  bar.append(el("div", { class: `st right ${trainer === "running" ? "" : "stale"}` },
    dot(trainer === "running" ? "running" : "interrupted", trainer === "running"),
    el("b", { text: trainer === "running" ? "trainer running"
                  : trainer === "stalled" ? "trainer stalled"
                  : trainer === "dead" ? "trainer gone" : "no session" })));
  return bar;
}

/** What one iteration costs, and how many env steps a second that is.
 *
 * The **median** of the recent gaps, not the mean and not the last one: a
 * progress video renders inside an iteration, a checkpoint is written inside
 * another, and either makes one gap several times the rest. A mean carries
 * that spike for the whole window and the last-gap reading flickers between
 * two numbers -- neither answers "how fast is this run going", which is a
 * question about the typical iteration.
 *
 * `fps` is env steps per second: envs x the rollout length x iterations per
 * second. The rollout length is not published either, but `total_env_steps`
 * counts steps *per env*, so dividing it by the iteration recovers it.
 */
function iterationPace(live) {
  const rows = (live && live.metrics) || [];
  const status = (live && live.status) || {};
  const gaps = [];
  for (let i = rows.length - 1; i > 0 && gaps.length < 15; i -= 1) {
    const dt = Number(rows[i].t) - Number(rows[i - 1].t);
    const di = Number(rows[i].iteration) - Number(rows[i - 1].iteration);
    // A row with no clock, a repeated iteration, or a clock that went
    // backwards across a restart. None of them is a rate.
    if (isFinite(dt) && isFinite(di) && dt > 0 && di > 0) gaps.push(dt / di);
  }
  if (!gaps.length) return null;
  gaps.sort((a, b) => a - b);
  const seconds = gaps[Math.floor(gaps.length / 2)];
  if (!(seconds > 0)) return null;

  const envs = Number(status.num_envs) || 0;
  const iteration = Number(status.iteration) || 0;
  const steps = Number(status.total_env_steps) || 0;
  const perIteration = iteration > 0 ? steps / iteration : 0;
  const fps = envs && perIteration ? (envs * perIteration) / seconds : 0;
  return { seconds, fps };
}

/** Seconds, at the precision the number deserves. */
function secs(v) {
  if (v >= 100) return `${Math.round(v)}`;
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

/** A rate, shortened -- the bar is 10.5px mono and "41,383" is five characters
 *  of noise where "41k" is the answer. The exact figure is in the tooltip. */
function rate(v) {
  if (v >= 10000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
  return String(Math.round(v));
}

// Sessions on disk that no record names.
//
// The rail is built from records, which is the right spine and the wrong
// silence: a run with no record is invisible here even while it is training,
// including one an agent launched from this app's own panel. So they are
// listed, apart from the records and clearly not one, with the
// single thing that would fix them.
/** Training sessions on disk that no record names.
 *
 * In the bar rather than the rail. The capability has to live somewhere --
 * without it a run that trains with no record is invisible in an app whose
 * spine is records. But it does not belong *among* the records: a row
 * reading "no record" in a list of records reads as a record, and the rail is
 * the one panel that has to be trustworthy about what exists.
 *
 * So the bar states the fact and the menu offers the fix.
 */
function unrecordedCell(state, actions) {
  const rows = state.unrecorded || [];
  if (!rows.length) return null;
  const open = !!state.unrecOpen;
  // Not named `chip`: that is an import from ui.js, and shadowing it inside a
  // function that may later want it is a trap for whoever edits this next.
  const button = el("button", {
    class: "st unrec-chip", "aria-expanded": open ? "true" : "false",
    title: "training sessions on disk that no record names",
    onclick: (e) => { e.stopPropagation(); actions.toggleUnrecorded(); },
  }, `${rows.length} unrecorded`);
  if (!open) return el("span", { class: "menuwrap" }, button);

  const menu = el("div", { class: "menu unrecmenu" },
    el("div", { class: "menu-h" },
      el("span", { class: "eyebrow", text: "sessions with no record" }),
      el("span", { class: "menu-note",
                   text: "writing one changes nothing about the run — it gives "
                       + "the studio something to hang its frames and metrics on" })));
  for (const row of rows) {
    menu.append(el("button", {
      class: "mrow", key: row.session,
      title: `${row.session}\n${row.state} · iteration ${row.iteration ?? "—"}`,
      onclick: () => actions.startAdopting(row),
    },
      dot(row.state === "running" ? "running" : "planned", row.state === "running"),
      el("span", {},
        el("span", { class: "mn", text: row.name.slice(0, 30) }),
        el("span", { class: "mr", text: `${row.task || "unknown task"} · iteration `
                                      + `${row.iteration ?? "—"}` })),
      el("span", { class: "mc" })));
  }
  return el("span", { class: "menuwrap" }, button, menu);
}
