// The agent pane: transcript, tool calls, approvals, composer.
//
// One component, used by every layout — Workbench gives it a column, Focus a
// drawer, and neither owns it. State lives in the store and the transcript
// lives on the server, so switching layout or reloading the page does not lose
// the conversation.
//
// This module is the panel's frame and its transcript pane; the pieces inside
// it (the conversation menu, how entries are drawn, the composer, the briefing
// and signing in) are in `agent/`.

import { el, dot, on, ago, forTime } from "./ui.js";
import { state, set, subject, UNKNOWN_TASK } from "./state.js";
import { threadBar } from "./agent/conversations.js";
import { groupTurns } from "./agent/transcript.js";
import { composer } from "./agent/composer.js";
import { signIn, providerButton } from "./agent/signin.js";

// True while the transcript is scrolled to (or near) the bottom. A new message
// scrolls down only then, which is what every chat pane worth using does.
let following = true;

// The clocks in this panel -- "working · 4m", "12m ago" -- have to move on
// their own: a long tool call sends no event for minutes, and a menu left
// open sends none at all. An empty patch is a repaint and nothing else.
setInterval(() => {
  if (state.agentBusy || state.convOpen) set({});
}, 15000);

export function agentPanel({ onHide = null } = {}) {
  const status = state.agent || { available: false, reason: "checking…" };
  const wrap = el("div", { class: "agentwrap" },
    el("div", { class: "agent-h" },
      dot(status.available ? "running" : "planned", status.available && state.agentBusy),
      el("span", { class: "agent-t", text: "Agent" }),
      status.available ? hereState() : null,
      el("span", { style: { marginLeft: "auto" } }),
      providerButton(status),
      onHide ? el("button", { class: "act", onclick: onHide }, "Hide") : null),
    threadBar(),
    body(status),
    composer(status));
  return wrap;
}

/** The open conversation's own state, in words, beside the title.
 *
 * The pulsing dot in the header is a 7px animation, which is easy to miss
 * and says nothing about *how long*. "working · 4m" tells an agent that is
 * thinking from one that hung; "idle · 2h ago" tells a finished conversation from one that
 * has not started. The clock comes from the server's list when it has this
 * conversation, and from the transcript's own stamps until it does.
 */
function hereState() {
  const who = subject(state);
  const row = (state.conversations || [])
      .find(c => c.run === who && c.thread === state.thread);
  const now = Date.now() / 1000;
  if (state.agentStopping) return el("span", { class: "agent-state stopping", text: "stopping…" });
  if ((state.pending || []).length) return el("span", { class: "agent-state needs_you", text: "needs you" });
  if (state.agentBusy) {
    const since = (row && row.since) || lastStamp(state.transcript, "user");
    return el("span", { class: "agent-state working" },
      el("span", { class: "agent-state-dot" }),
      since ? `working · ${forTime(now - since)}` : "working");
  }
  const at = (row && row.last_at) || lastStamp(state.transcript);
  if (!at) return null;                      // nothing said yet: no state to have
  return el("span", { class: "agent-state idle", text: `idle · ${ago(at)}` });
}

/** The newest `at` stamp in a transcript, optionally of one kind. */
function lastStamp(entries, kind = null) {
  for (let i = (entries || []).length - 1; i >= 0; i--) {
    const e = entries[i];
    if (e.at && (!kind || e.kind === kind)) return e.at;
  }
  return 0;
}

/** What the agent may do here, and what it has to ask about.
 *
 * The server publishes this table; without it on screen, "what can this thing
 * do to my run" is answerable only by reading `policy.py`. Folded shut,
 * because it is reference rather than news.
 */
function policyNote(status) {
  const table = status.policy;
  if (!table) return null;
  const short = (t) => t.replace("mcp__rlmcp__", "");
  const list = (names) => el("div", { class: "pol-list" },
    names.map(n => el("code", { text: short(n) })));

  return el("details", { class: "polnote" },
    el("summary", {},
      `${table.allow.length} tools it uses freely · `,
      el("b", { text: `${table.ask.length} that ask you first` })),
    el("div", { class: "pol-h", text: "asks you first" }), list(table.ask),
    el("div", { class: "pol-h", text: "uses freely" }), list(table.allow),
    table.caveat ? el("div", { class: "pol-caveat", text: table.caveat }) : null);
}

/** The conversation itself. Exported for the Console layout, which shows the
 *  run as one stream and so shows the talking in it rather than beside it. */
export function transcriptPane(status) {
  return body(status || state.agent || { available: false, reason: "checking…" });
}

function body(status) {
  const pane = el("div", { class: "agent-b" });

  // Signed out with a conversation already on the screen: the sign-in goes
  // *above* it rather than instead of it. Replacing the pane is right on a
  // cold boot and wrong here -- a token that expires mid-run would take the
  // whole conversation off the screen with it, which reads as the studio
  // having lost it, and leave the one place the failure is explained
  // unreachable.
  const standing = (state.transcript || []).length && subject(state);
  if (!status.available && !standing) {
    pane.append(signIn(status));
    return pane;
  }
  // Once, though. The expiry card carries the same box at the point in the
  // conversation where it failed, and the pane scrolls to it -- a second copy
  // pinned above it is the same instructions twice on one screen.
  const carded = (state.transcript || []).some(e => e.kind === "auth_expired");
  if (!status.available && !carded) {
    pane.append(el("div", { class: "agent-banner" },
      signIn(status, { heading: "Signed out \u2014 the agent cannot answer",
                       why: status.reason || "not signed in" })));
  }

  if (!subject(state)) {
    // Three reasons the panel has nothing to talk *about*, and they need
    // different sentences: the third must not say "select a task" while a
    // task is plainly selected in the bar above it.
    pane.append(el("div", { class: "empty",
                            text: state.view !== "build"
                              ? "select a run to talk about it"
                              : state.task === UNKNOWN_TASK
                                ? `${UNKNOWN_TASK} groups records written before `
                                  + "rlmcp stamped a task on them. There is no "
                                  + "task package behind it to build — pick a "
                                  + "real task, or open one of its runs."
                                : "select a task to talk about it" }));
    return pane;
  }

  const entries = state.transcript || [];
  if (!entries.length) {
    // What this run's conversation would actually get, asked of the server
    // rather than assumed: a run that has never been launched has no tools
    // pinned to it, and saying otherwise would repeat the mistake an agent's
    // prompt can make about its own tools. See `agents/preflight.py`.
    const tools = state.agentTools;
    const attached = !tools || tools.attached !== false;
    pane.append(el("div", { class: "empty" },
      el("div", { text: "Nothing asked yet." }),
      el("div", { class: "k", style: { marginTop: "8px",
                                       color: attached ? "var(--dim)" : "var(--warn)" },
                  text: attached
                    ? "the rlmcp tools are attached and pinned to this run"
                    : tools.text }),
      attached ? policyNote(status) : el("div", {
        class: "k", style: { marginTop: "6px", color: "var(--dim)", maxWidth: "46ch" },
        text: (tools.findings || []).map(f => f.fix).filter(Boolean).join(" ") })));
  }
  for (const node of groupTurns(entries)) pane.append(node);
  if (state.sending) {
    // On its way: typed over a running turn, and sent by the server once that
    // turn has ended. Drawn faint until the server echoes it back as a `user`
    // entry, so pressing enter is never followed by an empty box and nothing
    // on the screen to show for it.
    pane.append(el("div", { class: "msg m-ghost" },
      el("div", { class: "m-who", text: "you" }),
      el("div", { class: "m-you" }, el("div", { text: state.sending }))));
    pane.append(el("div", { class: "typing" },
      el("i"), el("i"), el("i"), el("span", { text: "ending its turn, then sending…" })));
  }
  if (state.agentBusy) {
    pane.append(el("div", { class: "typing" },
      el("i"), el("i"), el("i"), el("span", { text: "working…" })));
  }
  // Follow the conversation only while you are *at* the conversation. Reading
  // something further up and being yanked to the bottom by an arriving tool
  // result is the same bug as a list that resets its scroll -- it just looks
  // like the app disagreeing with you about where you were.
  queueMicrotask(() => {
    // The *live* pane: `pane` is the copy `patch()` discards, so scrolling it
    // moves nothing. Scrolling the copy would open a long conversation at the
    // top, and leave a reply that arrives while you sit at the bottom below
    // the fold. `console.js` does the same.
    const live = document.querySelector(".agent-b");
    if (live && following) live.scrollTop = live.scrollHeight;
  });
  on(pane, "scroll", (e) => {
    const node = e.currentTarget;
    following = node.scrollHeight - node.scrollTop - node.clientHeight < 40;
  });
  return pane;
}
