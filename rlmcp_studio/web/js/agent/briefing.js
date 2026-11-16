// The briefing: the prompt the agent is given, shown as it was sent.

import { el } from "../ui.js";
import { set } from "../state.js";

// ── the briefing, read rather than assembled in your head ───────────────
//
// The prompt is the text that decides how the agent behaves, and without this
// window there is no way to see it from the app that composes it: the harness
// is several markdown files, the tool list is written from the permission
// table, and the run's own context is built per message and written down
// nowhere at all. A prompt nobody can read is a prompt nobody can say is
// wrong.
//
// It shows what the *open* conversation was started with when there is one,
// and what the next one would get otherwise, and it says which -- because the
// harness is fixed at connect, so the two genuinely differ after a change.

export function briefingOverlay(s) {
  if (!s.briefing && !s.briefingBusy && !s.briefingError) return null;
  const b = s.briefing || {};
  const sections = b.sections || [];

  return el("div", {
    class: "zoom",
    onclick: (e) => { if (e.target.classList.contains("zoom")) closeBriefing(); },
  },
    el("div", { class: "z-box briefbox" },
      el("div", { class: "z-head" },
        el("span", { class: "z-name", text: "What this conversation is given" }),
        b.harness
          ? el("span", { class: "eyebrow",
                         text: `${b.label || b.harness} \u00b7 ${b.live
                           ? "as sent, above" : "as it would be sent"}` })
          : null,
        el("span", { class: "sp" }),
        el("button", { class: "act", onclick: () => closeBriefing() }, "Close")),
      el("div", { class: "brief-b" },
        s.briefingError
          ? el("div", { class: "m-error", text: s.briefingError })
          : s.briefingBusy && !s.briefing
            ? el("div", { class: "empty", text: "reading\u2026" })
            : el("div", {}, briefFacts(b), sections.map(briefSection),
                 briefFoot(b)))));
}

/** The four facts that frame the text: where it runs, what is attached, and
 *  whether Claude Code's own prompt is underneath ours. */
function briefFacts(b) {
  const row = (k, v) => v
    ? el("div", { class: "brief-fact" },
        el("span", { class: "k", text: k }), el("span", { text: v }))
    : null;
  return el("div", { class: "brief-facts" },
    row("harness", `${b.label || b.harness || "—"} — ${b.note || ""}`),
    row("system prompt", !b.preset
      ? "replaced by the sections below"
      // "with the sections below appended" over a window with no sections
      // below it, which is exactly the case `plain` is.
      : (b.sections || []).length
        ? "Claude Code's own, with the sections below appended"
        : "Claude Code's own, unchanged"),
    row("working directory", b.cwd || "wherever the studio was started"),
    row("mcp servers", (b.mcp || []).length
      ? (b.mcp || []).join(", ") + (b.tools_attached === false
          ? " — configured, but the tools will not be there" : "")
      : "none — this conversation has no rlmcp tools"),
    row("also loaded", (b.settings || []).length
      ? `CLAUDE.md, skills and commands from ${(b.settings || []).join(", ")}`
        + " settings — the CLI reads those whatever the harness says"
      : ""));
}

/** One section of the prompt, folded, with the file or table it came from.
 *
 * `<pre>` rather than the markdown renderer: this is the text *as sent*, and a
 * window whose whole purpose is showing what the agent got would be lying if
 * it showed a prettier version of it.
 */
function briefSection(section) {
  const text = section.text || "";
  return el("details", { class: "brief-s", open: section.id === "context" || undefined },
    el("summary", {},
      el("span", { class: "nm", text: section.title || section.id }),
      el("span", { class: "sub", text: section.source || "" }),
      el("span", { class: "st", text: `${text.split("\n").length} lines` })),
    el("pre", { class: "brief-text", text }));
}

function briefFoot(b) {
  if (!(b.sections || []).length) {
    return el("div", { class: "brief-foot",
                       text: b.unsupported || "Nothing from the studio: this "
                         + "conversation starts as Claude Code does in that "
                         + "directory, with the tools above." });
  }
  return el("div", { class: "brief-foot",
                     text: b.live
                       ? "This is the prompt the conversation above connected "
                         + "with. Changing the harness starts the next one "
                         + "differently; it cannot change this one."
                       : "Nothing is open on this subject yet, so this is what "
                         + "the first message would start." });
}

export function closeBriefing() {
  set({ briefing: null, briefingBusy: false, briefingError: "" });
}
