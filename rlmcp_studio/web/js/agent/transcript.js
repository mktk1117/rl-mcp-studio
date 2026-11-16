// How a transcript is drawn: one renderer per event kind, tool calls folded
// into groups, and the approvals a tool asks for.

import { el, dot, on, elapsed } from "../ui.js";
import { api } from "../api.js";
import { state, set, subject } from "../state.js";
import { renderMarkdown } from "../docs.js";
import { actionsOf } from "./conversations.js";
import { signIn } from "./signin.js";

// One renderer per event kind, dispatched through a table — a new kind is a
// new entry here, never a longer if-chain.
const RENDER = {
  // `shows` is what you held up while saying it — an annotated frame. The
  // agent was sent the image, so the transcript has to show it too; otherwise
  // the pane reads as a question about a picture that is not there.
  user: (e) => msg("you", el("div", { class: "m-you" },
    e.shows ? shownFrame(e.shows) : null,
    el("div", { text: e.text }))),
  // No `msg()` wrapper: the "claude" label belongs to the *turn*, and the turn
  // is a run of entries -- text, a thought, three tool calls -- not one of them.
  // `groupTurns` below puts the label on.
  //
  // Rendered, not printed. The agent writes markdown -- it is a model, that is
  // what they write -- and printed, the pane would show the source: `**bold**`
  // with its asterisks, backticks around every identifier, list markers as
  // literal dashes. The renderer is the Docs pane's, which builds nodes rather
  // than markup, so a reply cannot execute anything it happens to contain.
  text: (e) => el("div", { class: "m-bub prose" }, ...renderMarkdown(e.text)),
  thinking: (e) => el("details", { class: "m-think" },
    el("summary", { text: "thought" }), el("div", { text: e.text })),
  tool_call: (e, all) => {
    // What *happened* to this call, not what the table says about the tool. A
    // call that already ran and returned, labelled "needs you", reads as the
    // conversation being stuck on a question nobody was asked.
    const outcome = toolOutcome(e, all);
    // Folded, the way Claude Code folds one. Unfolded, a turn with six tool
    // calls is six open boxes of arguments between two sentences, and the
    // sentences are the part you are reading. The summary carries the one argument that says
    // *which* call this was -- the path, the key, the command -- so folded is
    // still informative rather than just short.
    //
    // `<details>` on purpose: `ui.js` treats `open` as user state and does not
    // strip it on repaint, so one you opened stays open while a live run
    // repaints the panel twice a second.
    // The answer goes *inside* the call it answers. As the next sibling it
    // would be two boxes per call and, when a turn interleaves them, two boxes
    // that are not even adjacent: an `ls` output can land rows below the `ls`
    // it came back from. One call, one row, and the row says how it ended.
    const answer = (all || []).find(x => x.kind === "tool_result" && x.id === e.id);
    return el("details", { class: "m-tool" },
      el("summary", { class: "m-tool-h" },
        dot(outcome.mark),
        el("span", { class: "nm", text: shortTool(e.name) }),
        el("span", { class: "sub", text: gist(e.args) }),
        el("span", { class: "st", text: outcome.label })),
      argLines(e.args),
      answer ? toolResult(answer) : null);
  },
  // Only when it is an orphan: a result whose call is not in the transcript --
  // a conversation read back after the studio restarted mid-turn, or one
  // trimmed at the front. Otherwise the call above has it.
  tool_result: (e, all) => (all || []).some(x => x.kind === "tool_call" && x.id === e.id)
    ? null : toolResult(e),
  permission: (e) => permissionCard(e),
  // "approved" on its own line, under a call whose own status column already
  // reads "you approved". Kept only when no call claims it -- a decision whose
  // call has scrolled out of the transcript is the only news it carries.
  decision: (e, all) => (all || []).some(
      x => x.kind === "tool_call" && decisionFor(x, all) === e)
    ? null
    : el("div", { class: "m-note",
        text: `${e.decision === "deny" ? "denied" : "approved"}${e.note ? ` \u2014 ${e.note}` : ""}` }),
  error: (e) => el("div", { class: "m-error" },
    el("b", { text: "That did not work. " }), e.message),
  // The studio restarted between these two messages. Said in the transcript
  // rather than left for the reader to discover by being misunderstood.
  restart: (e) => el("div", { class: "m-restart" }, el("span", {}), e.text),
  // Why the agent is suddenly talking when you did not ask it anything. The
  // CLI injects one of these when a background command it started finishes,
  // and the turn under it answers *that*, not your last message. Unlabelled,
  // it reads as the agent ignoring the question you had just typed.
  wake: (e) => el("div", { class: "m-wake" },
    el("b", { text: "woke itself" }),
    el("div", { class: "m-wake-t", text: clip(e.text, 400) })),
  // You stopped it. In the transcript, because a turn that ends because it was
  // ended looks exactly like one that finished, and the difference is the
  // whole reason the button exists.
  stopped: (e) => el("div", { class: "m-restart m-stopped" }, el("span", {}), e.text),
  // What the studio checked before it started this conversation, when the
  // answer was not "everything is fine". It is above the first question on
  // purpose: the fix for the common case -- a run that has never been launched
  // -- is a button *you* have and the agent does not. Without it an agent can
  // spend a turn reading `/proc` to work out that its own prompt was wrong
  // about its own tools.
  preflight: (e) => el("div",
    { class: "m-pre" + (e.severity === "blocking" ? " m-pre-bad" : "") },
    el("div", { class: "m-pre-h" },
      el("b", { text: e.attached === false ? "No tools" : "Attached, with a catch" }),
      el("span", { text: e.text })),
    (e.findings || []).map(f => el("div", { class: "m-pre-f" },
      el("span", { class: "why", text: f.because }),
      f.fix ? el("span", { class: "fix", text: f.fix }) : null))),
  // The credential died in the middle of this conversation. The provider says
  // so in a sentence -- "Failed to authenticate. API Error: 401 OAuth access
  // token has expired. Re-authenticate to continue." -- and a sentence alone
  // names the problem, tells you to fix it, and points at nothing. The card
  // carries the sign-in itself, under the turn it killed, because that is
  // where you are looking when it happens.
  auth_expired: (e) => el("div", { class: "m-pre m-pre-bad m-auth" },
    el("div", { class: "m-pre-h" },
      el("b", { text: "Signed out" }),
      // Not the provider's sentence again: it is the entry directly above,
      // and this card is the answer to it rather than a second copy of it.
      el("span", { text: "Claude Code's sign-in expired mid-turn" })),
    signIn({ ...(state.agent || {}), cli: e.cli !== false },
           { heading: "Sign in again to carry on",
             why: "Nothing is lost: this conversation, its run and everything "
                + "above are kept. Ask again once this is done." })),
  // The conversation is too heavy to keep sending. Every image a tool returned
  // stays in it and goes out again with each request, and the API drops a
  // request over its size cap without a reason -- ten ECONNRESETs and the turn
  // is gone. `dropped` is that having happened; otherwise it is the warning
  // before it does. Either way the answer is a fresh conversation, and the
  // button starts one with a handoff you can edit before sending.
  heavy: (e) => el("div", { class: `m-pre m-heavy${e.dropped ? " m-pre-bad" : ""}` },
    el("div", { class: "m-pre-h" },
      el("b", { text: e.dropped ? "Connection dropped" : "Getting large" }),
      el("span", { text: e.dropped
        ? "This turn failed \u2014 most likely the conversation is too large to send"
        : "This conversation is nearing what one request can carry" })),
    el("div", { class: "m-pre-f" },
      el("span", { text: `${e.images || 0} images (${mb(e.image_bytes)}) and `
        + `${Math.round((e.context_tokens || 0) / 1000)}k tokens: about ${mb(e.request_bytes)} `
        + `a request, against a cap of ${mb(e.limit_bytes)}.` }),
      el("span", { class: "fix", text: e.dropped
        ? "Resending will fail the same way. If the numbers are small, it was the network \u2014 ask again."
        : "Every image stays in the conversation and is sent again with each message." }),
      el("div", {},
        el("button", { class: "btn", text: "Start a fresh conversation",
                       onclick: () => actionsOf().freshStart() })))),
  done: () => null,
  idle: () => null,
};

/** What a tool sent back, in a shape a person can read.
 *
 * rlmcp answers in JSON, and printed as it comes the envelope is one line of
 * `{"result":{"ok":true,"session":"…","session_dir":"/path/to/…"}}`.
 * Everything the reader wants is in there and none of it is legible. The
 * envelope is unwrapped, the fields every answer carries are dropped, and what
 * is left is laid out like the arguments above it. Anything that is not JSON
 * is shown as it came.
 */
function toolResult(e) {
  const raw = String(e.content == null ? "" : e.content);
  // Some tools answer with JSON *and* a marker -- `take_screenshot` returns
  // the envelope followed by "[image]" -- which `JSON.parse` rejects outright.
  // Unstripped, the whole envelope prints raw and the picture is never shown.
  const image = /\n?\[image\]\s*$/.test(raw);
  const json = image ? raw.replace(/\n?\[image\]\s*$/, "") : raw;

  let value = null;
  try {
    value = JSON.parse(json);
  } catch {
    // Not JSON -- `Bash`, `Read`, `Grep`, which is most of the volume in a
    // real turn. These need folding most.
    return plainResult(raw, e.error === true);
  }
  // rlmcp wraps its answer, and the MCP layer wraps that.
  while (value && typeof value === "object" && !Array.isArray(value)
         && Object.keys(value).length === 1 && "result" in value) {
    value = value.result;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return plainResult(String(raw), e.error === true);
  }

  // `ok: true` is the absence of news, and the session is on screen already.
  // `ok: false` is the *whole* news, so it is never dropped silently -- a
  // failed call would otherwise render exactly like a successful one,
  // distinguished only by a row that happens to be called "error".
  const failed = e.error === true || value.ok === false;
  const NOISE = new Set(["ok", "session", "session_dir", "cmd"]);
  const rows = Object.entries(value).filter(([k]) => !NOISE.has(k));

  const shown = state.showAllFields === e.id ? rows : rows.slice(0, 10);
  const shot = image && typeof value.image_path === "string" ? value.image_path : "";

  // A failed call opens itself: it is the whole news, and folding it away is
  // how a failure reads as a success. Everything else stays shut.
  return el("details", { class: `m-tool-out${failed ? " failed" : ""}`,
                         open: failed ? "" : null },
    el("summary", { class: "m-out-h" },
      el("span", { class: failed ? "m-failed" : "m-okmark",
                   text: failed ? "failed" : "ok" }),
      el("span", { class: "m-out-gist", text: summarise(rows) })),
    rows.length
      ? el("div", { class: "m-kvs" },
          shown.map(([k, v]) => el("div", { class: "kv" },
            el("span", { text: k }),
            el("span", { text: clip(readable(v, k), 160) }))))
      : el("div", { class: "m-kvs" }, el("div", { class: "kv" },
          el("span", { text: failed ? "failed" : "ok" }), el("span", { text: "" }))),
    rows.length > shown.length
      ? el("button", { class: "m-more",
                       onclick: () => set({ showAllFields: e.id }) },
          `show the other ${rows.length - shown.length} field(s)`)
      : null,
    // The picture the tool returned, rather than the word "[image]".
    shot ? el("img", { class: "m-shot", src: state.artifactUrl(shot),
                       alt: "what the tool returned" }) : null);
}

/** One field of a tool's answer, as text. */
function readable(v, key = "") {
  if (v === null || v === undefined) return "—";
  // A couple of shapes rlmcp uses that are unreadable as bare numbers:
  // `updated_at 1787825173.211` and `heartbeat_age_s 13391.200`.
  if (typeof v === "number" && /(^|_)at$/.test(key) && v > 1e9) {
    // With the date, because a time alone cannot say "yesterday".
    return new Date(v * 1000).toLocaleString();
  }
  if (typeof v === "number" && (/_s$/.test(key) || key === "seconds")) {
    return v < 90 ? `${v.toFixed(1)} s` : elapsed(v);
  }
  if (Array.isArray(v)) {
    return v.length <= 6 ? v.map(readable).join(", ") : `${v.length} items`;
  }
  if (typeof v === "object") return JSON.stringify(v);
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(3);
  return String(v);
}

function renderEntry(entry, all) {
  const render = RENDER[entry.kind];
  return render ? render(entry, all || []) : null;
}

/** Whether a tool call was asked about, and how it ended. */
function toolOutcome(call, all) {
  const decision = decisionFor(call, all);
  if (decision) {
    return decision.decision === "deny"
      ? { label: "denied", mark: "falsified" }
      : { label: "you approved", mark: "validated" };
  }
  if (all.some(e => e.kind === "tool_result" && e.id === call.id)) {
    // A denied call still comes back with a result -- the refusal *is* the
    // result -- so the decision above has to be consulted first, and it is.
    return { label: call.policy === "ask" ? "ran" : "auto", mark: "best" };
  }
  // A call with no result, in a turn that has since ended. It ran before this
  // client was listening, or the studio restarted mid-turn: either way the
  // output is not here, and labelling it "auto" like a completed call would
  // leave the reader no way to tell the difference.
  const after = all.slice(all.indexOf(call) + 1);
  if (after.some(e => e.kind === "done" || e.kind === "idle")) {
    return { label: "no result kept", mark: "planned" };
  }
  if (all.some(e => e.kind === "permission" && e.tool === call.name)) {
    return { label: "needs you", mark: "provisional" };
  }
  if (after.some(e => e.kind === "text")) {
    return { label: "no result kept", mark: "planned" };
  }
  return call.policy === "ask"
    ? { label: "waiting on you", mark: "provisional" }
    : { label: "running", mark: "running" };
}

/** The approve/deny that answered this call, if one did.
 *
 * A permission request carries `run#thread:N:tool_name`, not the tool_use id
 * the call carries, so the two cannot be matched on identity. Matching by
 * *ordinal among calls of that tool* is wrong the moment some calls of a tool
 * are prompted and others are not: with three `Bash` calls and one denial, it
 * labels the call that ran "denied" and the refused one "ran".
 *
 * Position is the honest link. A permission arrives while its call is being
 * made, so the prompt belonging to a call is the one between that call and the
 * next call. Ids are no help even in principle: they carry a counter that
 * restarts with the process, so a transcript spanning a restart holds two
 * requests numbered `1`.
 */
function decisionFor(call, all) {
  const from = all.indexOf(call);
  if (from < 0) return null;
  for (let i = from + 1; i < all.length; i++) {
    const entry = all[i];
    if (entry.kind === "tool_call") break;           // the next call's business
    if (entry.kind !== "permission") continue;
    if (entry.tool && entry.tool !== call.name) continue;
    return all.find(e => e.kind === "decision" && e.request_id === entry.request_id) || null;
  }
  return null;
}

/** The frame a message was about, or a line saying it is not here.
 *
 * A file this studio's roots do not cover -- an artifact from another machine,
 * a cleared logs directory -- lays out as a blank box with nothing to read
 * and a 404 in the console. An image that fails to load has to say so.
 */
function shownFrame(path) {
  const img = el("img", { class: "m-shot", src: state.artifactUrl(path),
                          alt: "the frame this was asked about" });
  on(img, "error", (e) => {
    const gone = el("div", { class: "m-gone" },
      "the frame this was about is not readable from here — ",
      el("code", { text: path.split("/").slice(-1)[0] }));
    e.currentTarget.replaceWith(gone);
  });
  return img;
}

function msg(who, bubble) {
  return el("div", { class: "msg" }, el("div", { class: "m-who", text: who }), bubble);
}

/** What stands on its own rather than inside a turn.
 *
 * Your message is a turn of its own. The two dividers are the seam *between*
 * turns, so a turn cannot contain one. A permission card is the odd one, and
 * deliberately so: it is a question addressed to *you*, and burying it in the
 * fill that means "the agent has been talking" is how a card asking to be
 * answered stops looking like one.
 */
const STANDALONE = new Set(["user", "permission", "restart", "stopped", "wake",
                            // A fact about the conversation rather than a turn
                            // in it, like the two dividers above it.
                            "preflight"]);

/** The name over the agent's turns: the provider this conversation runs on.
 *
 * A conversation keeps its provider, so the label is the conversation's --
 * `agentWho`, from the transcript fetch -- and falls back to the current
 * provider for one that has not been started yet.
 */
function whoAnswers() {
  const status = state.agent || {};
  const name = state.agentWho || status.provider || "claude";
  const row = (status.providers || []).find(p => p.name === name);
  return (row && row.label) ? row.label.toLowerCase() : name;
}

/** Entries, with each run of the agent's own into one labelled block.
 *
 * The transcript arrives flat -- `text`, `tool_call`, `tool_result` are all
 * siblings -- so without this the agent's half of the conversation is a stack
 * of unrelated boxes, and the "claude" label repeats above every paragraph it
 * happens to have broken. One turn, one label, one surface.
 */
export function groupTurns(entries) {
  const out = [];
  let turn = null;
  const push = (node) => {
    if (!turn) {
      turn = el("div", { class: "m-turn" });
      out.push(el("div", { class: "msg" },
                  el("div", { class: "m-who", text: whoAnswers() }), turn));
    }
    turn.append(node);
  };

  for (let i = 0; i < entries.length; i++) {
    const entry = entries[i];
    if (STANDALONE.has(entry.kind)) {
      const node = renderEntry(entry, entries);
      if (!node) continue;
      turn = null;
      out.push(node);
      continue;
    }
    // A run of calls with nothing said between them is one thing the agent
    // did, and it reads as one thing: sixteen boxes of `ls`, `grep` and their
    // answers, between two sentences, is a transcript you scroll past rather
    // than read. Results and decisions sit inside their calls, so they do not
    // break the run; a sentence or a thought does, because that is where
    // the agent stopped looking and said something.
    if (entry.kind === "tool_call") {
      const calls = [];
      let j = i;
      for (; j < entries.length; j++) {
        const next = entries[j];
        if (next.kind === "tool_call") { calls.push(next); continue; }
        if (next.kind === "tool_result" || next.kind === "decision") continue;
        // `done` and `idle` draw nothing and break the run anyway: they mean
        // the agent stopped, and calls either side of a stop are two things
        // it did rather than one.
        break;
      }
      // `j` stopped on what broke the run, and the loop's own `i++` moves on.
      i = j - 1;
      const nodes = calls.map(c => renderEntry(c, entries)).filter(Boolean);
      if (!nodes.length) continue;
      push(nodes.length > 1 ? toolGroup(calls, nodes, entries) : nodes[0]);
      continue;
    }
    const node = renderEntry(entry, entries);
    if (node) push(node);
  }
  return out;
}

/** A continuous run of tool calls, as one box.
 *
 * Shut by default and *not* opened while it is running: a group that opens
 * itself would undo the folding for exactly the conversation you are watching,
 * and `ui.js` never takes `open` off again once it is on. The summary carries
 * the news instead -- what ran, how many times, and what is happening now --
 * so a folded group is still a live line rather than a closed door.
 *
 * A failure opens it, for the reason a failed result opens itself: folded away,
 * a failure reads exactly like a success.
 */
function toolGroup(calls, nodes, all) {
  const outcomes = calls.map(c => toolOutcome(c, all));
  const failed = outcomes.filter(o => o.mark === "falsified").length;
  const waiting = outcomes.find(o => o.mark === "provisional" || o.mark === "running");

  const counts = new Map();
  for (const call of calls) {
    const name = shortTool(call.name);
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  const named = [...counts.entries()]
    .map(([name, n]) => (n > 1 ? `${name} \u00d7${n}` : name)).join(", ");

  const last = calls[calls.length - 1];
  const note = failed ? `${failed} failed`
             : waiting ? `${waiting.label}: ${gist(last.args)}`
             : `${calls.length} calls`;

  return el("details", { class: "m-tools", open: failed ? "" : null },
    el("summary", { class: "m-tool-h" },
      dot(failed ? "falsified" : waiting ? waiting.mark : "best"),
      el("span", { class: "nm", text: clip(named, 44) }),
      el("span", { class: "st", text: note })),
    el("div", { class: "m-tools-b" }, nodes));
}

/** A result that is text rather than a shape: folded, with its first line as
 *  the summary, because the first line is what a command's output leads with. */
function plainResult(raw, failed) {
  const text = String(raw || "");
  const first = (text.split("\n").find(l => l.trim()) || "").trim();
  const lines = text.split("\n").length;
  return el("details", { class: `m-tool-out${failed ? " failed" : ""}`,
                         open: failed ? "" : null },
    el("summary", { class: "m-out-h" },
      el("span", { class: failed ? "m-failed" : "m-okmark",
                   text: failed ? "failed" : "ok" }),
      el("span", { class: "m-out-gist",
                   text: first ? clip(first, 46) + (lines > 1 ? ` +${lines - 1} lines` : "")
                               : `${lines} lines` })),
    el("div", { text: clip(text, 4000) }));
}

/** The one argument that says which call this was.
 *
 * Not the first key: the first key of `set_parameter` is the session, and the
 * answer to "which call was that" is the parameter's name. These are the
 * arguments that identify a call, in the order they identify it.
 */
const GIST_KEYS = ["key", "path", "file_path", "command", "cmd", "pattern",
                   "query", "name", "record_id", "env_id", "text"];

function gist(args) {
  const a = args || {};
  for (const k of GIST_KEYS) {
    if (a[k] !== undefined && a[k] !== null && a[k] !== "") {
      return clip(String(a[k]).replace(/\s+/g, " "), 52);
    }
  }
  const keys = Object.keys(a);
  return keys.length ? clip(`${keys.length} argument${keys.length === 1 ? "" : "s"}`, 52) : "";
}

/** What a folded result says about itself, without being opened. */
function summarise(rows) {
  if (!rows.length) return "";
  const [k, v] = rows[0];
  const rest = rows.length - 1;
  return clip(`${k} ${readable(v, k)}`, 46) + (rest > 0 ? ` +${rest}` : "");
}

function argLines(args, { full = false } = {}) {
  const keys = Object.keys(args || {});
  if (!keys.length) return null;
  return el("div", { class: "m-tool-b" },
    keys.slice(0, 8).map(k => {
      const text = String(args[k] == null ? "" : args[k]);
      // 60 characters is a fine summary of a call that already happened, and
      // no way at all to decide about one that has not: a long
      // `cd … && python3 -c "…"` clipped to its `cd` offers Approve on the
      // part you can see. A pending request shows the whole thing, in a box
      // that scrolls.
      const value = full
        ? el("span", { class: "argfull", text })
        : el("span", { title: text.length > 60 ? text : "", text: clip(text, 60) });
      return el("div", { class: "kv" }, el("span", { text: k }), value);
    }));
}

function permissionCard(e) {
  const answered = !(state.pending || []).some(p => p.request_id === e.request_id);
  // Once it is answered the call above says "denied" or "you approved", the
  // note below says who decided and why, and this card repeats the tool name
  // and the arguments a third time. It is only useful while it is a question.
  if (answered) return null;
  const card = el("div", { class: `perm${answered ? " resolved" : ""}` },
    el("div", { class: "perm-h" },
      dot(answered ? "planned" : "provisional"),
      el("span", { class: "nm", text: shortTool(e.tool) }),
      el("span", { class: "eyebrow", style: { marginLeft: "auto" },
                   text: answered ? "answered" : "needs approval" })),
    argLines(e.args, { full: true }) || el("div", { class: "m-tool-b" }),
    e.reason ? el("div", { class: "perm-why", text: e.reason }) : null);

  if (!answered) {
    card.append(el("div", { class: "perm-acts" },
      el("button", { class: "primary", onclick: () => decide(e.request_id, "allow") }, "Approve"),
      el("button", { onclick: () => decide(e.request_id, "deny") }, "Deny"),
      el("button", { class: "ghost", onclick: () => decide(e.request_id, "allow_always") },
        "Always for this run")));
  }
  return card;
}

async function decide(requestId, decision) {
  try {
    await api.agentDecide(subject(state), requestId, decision, "", state.thread);
  } catch (err) {
    set({ agentError: err.message });
  }
}

const mb = (n) => `${(Number(n || 0) / (1024 * 1024)).toFixed(1)} MB`;

function shortTool(name) { return String(name || "").replace(/^mcp__rlmcp__/, ""); }
function clip(s, n) { s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n) + "…" : s; }
