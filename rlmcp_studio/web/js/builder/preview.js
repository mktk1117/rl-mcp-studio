// The preview panel, and the reward terms drawn from the gates' numbers.

import { el, chip, fmt, add } from "../ui.js";
import { viserSlot } from "../components.js";
import { staleBy, staleFile, staleNote } from "./gates.js";

const PREVIEW_WORDS = {
  off: "not built",
  starting: "constructing…",
  holding: "built and stepping",
  stopped: "stopped",
  failed: "did not build",
};

// ── the preview ─────────────────────────────────────────────────────────

export function previewPanel(build, s, actions) {
  const preview = build.preview || {};
  const st = preview.state || "off";
  const busy = s.buildBusy || "";
  const view = ((s.previewLive || {}).status || {}).live_view || null;

  // Built before the last edit: the thing on screen is stepping code that is
  // no longer on disk.
  const built = st === "holding" || st === "starting";
  const stale = built ? staleBy(build, preview.started_at) : 0;

  const head = el("div", { class: "card-h" },
    el("h3", { text: "The preview" }),
    chip(PREVIEW_WORDS[st] || st, st === "holding"),
    stale
      ? el("span", { class: "chip warn", title: staleNote(build, preview.started_at),
                     text: "out of date" })
      : null,
    st === "holding" && preview.uptime_s
      ? el("span", { class: "eyebrow", text: `${fmt(preview.uptime_s, 0)} s` })
      : null,
    el("span", { class: "sp" }),
    st === "off" || st === "stopped" || st === "failed"
      ? el("button", { class: "act primary", disabled: !!busy || undefined,
                       onclick: () => actions.preview("start") },
          busy === "start" ? "building…" : "Build and hold")
      : el("button", { class: "act", disabled: !!busy || undefined,
                       title: "construct it again, which is the only way to pick "
                            + "up a file change",
                       onclick: () => actions.preview("restart") },
          busy === "restart" ? "rebuilding…" : "Rebuild"),
    st === "holding" || st === "starting"
      ? el("button", { class: "act", disabled: !!busy || undefined,
                       onclick: () => actions.preview("stop") }, "Stop")
      : null);

  const body = previewBody(preview, s, actions, view);
  if (stale) {
    // Above the picture, not below it: the picture is the thing being
    // qualified, and a note underneath is read after the wrong conclusion.
    body.prepend(el("div", { class: "stale-note" },
      el("b", { text: `You edited ${staleFile(build)} ${fmt(stale, 0)} s after this was built. ` }),
      "What is stepping here is the code as it was then — Python binds modules "
      + "at import, so a change is a restart. Rebuild constructs it again."));
  }
  return el("div", { class: "card preview-card" }, head, body);
}

function previewBody(preview, s, actions, view) {
  const st = preview.state || "off";

  if (st === "off" || st === "stopped") {
    // What the button in the header does, and not a second copy of it. Two
    // "Build and hold" buttons on screen at once -- this one and the header's
    // -- would read two different busy flags (`s.buildBusy`, which is any
    // preview request, against the header's `busy === "start"`), so they
    // could disagree about their own labels while sitting 30 px apart. The
    // preview's verbs live in the card header in every other state; one of
    // them may not move house when the preview happens to be off.
    return el("div", { class: "card-b" },
      el("div", { class: "offer" },
        el("p", { text: "Build this task with no policy and hold it open: an "
                      + "environment stepping at its own clock, with nothing "
                      + "training and no checkpoint anywhere. Editing a file "
                      + "and rebuilding is the loop." }),
        s.buildError ? el("p", { class: "m-error", text: s.buildError }) : null,
        el("p", { class: "muted",
                  text: st === "stopped"
                    ? "You stopped it. Build and hold, in this card's header, "
                      + "constructs it again."
                    : "Build and hold, in this card's header, starts it." })));
  }

  if (st === "starting") {
    return el("div", { class: "card-b" },
      el("div", { class: "empty", text: "constructing the environment…" }),
      logBlock(preview, false));
  }

  if (st === "failed") {
    return el("div", { class: "card-b" },
      el("div", { class: "m-error",
                  text: preview.error || "the preview exited" }),
      // The traceback is the whole answer while a task is being written, so it
      // is open, not folded away behind a disclosure nobody clicks.
      logBlock(preview, true));
  }

  // Holding.
  if (!view || !view.running) {
    return el("div", { class: "card-b" },
      el("div", { class: "offer" },
        el("p", { text: "It is stepping. Open the 3-D view to watch it — rlmcp "
                      + "serves the environment's own scene, and pushes only "
                      + "while this panel is open." }),
        el("button", { class: "act primary", disabled: !!s.buildBusy || undefined,
                       onclick: () => actions.previewView(true) },
          s.buildBusy === "view" ? "opening…" : "Open the 3-D view")),
      logBlock(preview, false));
  }

  return el("div", { class: "card-b viser-b" },
    viserSlot(view.url, "preview"),
    el("div", { class: "viser-foot" },
      el("span", { class: "eyebrow",
                   text: `viser on port ${view.port} · ${view.watchers || 0} watching` }),
      el("span", { class: "sp" }),
      el("button", { class: "act", onclick: () => actions.previewCommand("reset_envs", {}) },
        "Reset envs"),
      el("button", { class: "act", onclick: () => actions.previewView(false) }, "Close")),
    logBlock(preview, false));
}

function logBlock(preview, open) {
  const lines = preview.log || [];
  if (!lines.length) return null;
  const box = el("details", { class: "build-log" },
    el("summary", { text: `output · ${lines.length} line${lines.length === 1 ? "" : "s"}` }),
    el("pre", { text: lines.join("\n") }));
  if (open) box.setAttribute("open", "");
  return box;
}

// ── the terms ───────────────────────────────────────────────────────────

export function termsCard(build, s, actions) {
  const answer = (build.gates || {}).answer;
  const rows = (answer && answer.rewards) || [];
  const head = el("div", { class: "card-h" },
    el("h3", { text: "What each term pays" }),
    el("span", { class: "sp" }),
    answer && answer.policy
      ? el("span", { class: "eyebrow", text: `${answer.policy} policy` }) : null);

  if (!rows.length) {
    return el("div", { class: "card" }, head,
      el("div", { class: "card-b" },
        el("div", { class: "empty",
                    text: answer
                      ? "this task's backend does not break its reward into terms"
                      : "run the checks to see the breakdown" })));
  }

  const top = Math.max(...rows.map(r => Math.abs(r.mean))) || 1;
  const body = el("div", { class: "terms" });
  for (const row of rows) {
    const width = Math.max(1, (Math.abs(row.mean) / top) * 50);
    add(body, el("div", { class: `term${row.mean < 0 ? " neg-term" : ""}` },
      el("span", { class: "nm", title: row.term, text: row.term }),
      el("span", { class: "track" },
        el("i", { class: row.mean < 0 ? "neg" : "pos", style: { width: `${width}%` } })),
      el("span", { class: "v", text: fmt(row.mean, 3) })));
  }

  const lopsided = (answer && answer.dominance) || {};
  if (lopsided.ratio && lopsided.ratio > 8) {
    add(body, el("p", { class: "terms-note" },
      el("b", { text: `${lopsided.term} pays ${fmt(lopsided.ratio, 1)}× what ${lopsided.over} pays.` }),
      " Against a policy that is doing nothing, that is what the task is "
      + "actually asking for."));
  }

  const ends = (answer && answer.terminations) || [];
  return el("div", { class: "card" }, head, body,
    ends.length
      ? el("div", { class: "ends" },
          ends.slice(0, 4).map(e => el("span", {},
            `${e.term} `, el("b", { text: fmt(e.per_step * 100, 1) + "%" }))))
      : null);
}
