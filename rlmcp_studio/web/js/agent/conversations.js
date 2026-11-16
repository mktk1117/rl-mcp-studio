// Which conversation you are in: the menu across runs, the bar within one, and
// the sessions other tools left on disk.

import { el, dot, on, ago, forTime } from "../ui.js";
import { api } from "../api.js";
import { state, set, subject, naturalSubject } from "../state.js";

// How a conversation's state reads. Local rather than shared: the agent panel
// is the only thing that draws this, and a shared constant with one reader is
// just a longer import.
const CONV_STATE = {
  needs_you: { dot: "provisional", label: "needs you" },
  working: { dot: "best", label: "working" },
  idle: { dot: "planned", label: "" },
  empty: { dot: "planned", label: "" },
};

/** Every conversation in the studio, and the one that wants you.
 *
 * In the panel, not the run rail: the panel that *shows* a conversation is
 * where you choose one, and the rail has nothing else to do with the
 * question. The panel owns which conversation you are in at both levels, this
 * menu across runs and `threadBar` within one.
 *
 * The attention count stays on the button rather than inside the menu, because
 * "one of these needs you" has to be visible without opening anything.
 */
// Where a conversation list went, said inside the app that lost it.
//
// Conversations are scoped per records root, so two studios over different
// task folders do not append to one `007#main.jsonl`. A file written before
// that scoping is moved into `_unscoped/` on first boot -- which from the
// outside is the entire history disappearing between one restart and the
// next. The server explains it on stdout, but a studio started detached under
// `uv tool` has no stdout anybody reads. So it is said here, and dismissed for
// good once read: it is news exactly once per machine.
const PARKED_SEEN = "rlmcp.retired.seen";

function retirementNotice() {
  const parked = state.retired;
  if (!parked || state.retiredHidden) return null;
  let seen = "";
  try { seen = localStorage.getItem(PARKED_SEEN) || ""; } catch { /* private window */ }
  if (seen === parked.path) return null;
  return el("div", { class: "conv-parked" },
    el("b", { text: `${parked.count} older conversation${parked.count === 1 ? "" : "s"} `
                  + "moved aside" }),
    el("span", { text: "Written before conversations were scoped to a records "
                     + "root, so no task can honestly claim them. Nothing was "
                     + "deleted — they are in:" }),
    el("code", { text: parked.path }),
    on(el("button", { class: "ghost", text: "got it" }), "click", (e) => {
      e.stopPropagation();
      try { localStorage.setItem(PARKED_SEEN, parked.path); } catch { /* ditto */ }
      set({ retiredHidden: true });
    }));
}

function conversations() {
  const all = state.conversations || [];
  const needs = (state.attention || {}).needs_you || 0;
  const open = !!state.convOpen;

  const working = all.filter(c => c.state === "working").length;
  // A bare count says nothing about what it counts, which leaves the other
  // agents findable only by accident. The word, the number, and the two
  // states worth interrupting for -- one of them is asking you something, one
  // of them is still thinking -- each as its own badge rather than as a
  // colour on the whole button, which cannot show both at once.
  const button = el("button", {
    class: `convbtn${needs ? " attn" : ""}${open ? " on" : ""}`,
    "aria-expanded": open ? "true" : "false",
    title: `${all.length} conversation${all.length === 1 ? "" : "s"} in this `
         + `studio${needs ? `, ${needs} waiting on you` : ""}`,
    onclick: (e) => { e.stopPropagation(); actionsOf().toggleConversations(); },
  },
    el("span", { class: "conv-n", text: String(all.length) }),
    // The word is the server's word: the menu header, the empty state and
    // every name on the server side say conversations. Counting *agents*, a
    // zero reads as "no agent is available to you", which is a different and
    // much worse fact than "you have not started one here yet".
    el("span", { class: "conv-w",
                 text: all.length === 1 ? "conversation" : "conversations" }),
    needs ? el("span", { class: "conv-badge needs", text: `${needs} need you` })
          : working ? el("span", { class: "conv-badge working" },
                        dot("best", true), `${working} working`)
          : null,
    el("span", { class: "conv-caret", text: "▾" }));
  if (!open) return el("span", { class: "menuwrap" }, button);

  const menu = el("div", { class: "menu convmenu" },
    el("div", { class: "menu-h" },
      el("span", { class: "eyebrow", text: "conversations" }),
      needs ? el("b", { class: "attn", text: `${needs} need you` }) : null),
    convFilter(all.length));
  const parked = retirementNotice();
  if (parked) menu.append(parked);
  // Typed above: a run id, a thread, a word from the first question, a task.
  const q = (state.convFilter || "").trim().toLowerCase();
  const rows = q
    ? all.filter(c => [c.run, c.thread, c.title, c.task].join(" ").toLowerCase().includes(q))
    : all;
  if (!all.length) {
    menu.append(el("div", { class: "conv-empty", text: "no conversations yet" }));
  } else if (!rows.length) {
    menu.append(el("div", { class: "conv-empty", text: `nothing matches “${state.convFilter}”` }));
  }
  // Only when the server *says* when. This file reloads with the page while
  // the process behind it runs until somebody restarts it, so a list without
  // `last_at` is an older server's: in run order, with no clock to sort by.
  // Read as "newest first", that order would put a week-old conversation at
  // the top wearing the mark and the day's conversations half-way down. Say
  // what the order is instead, and mark nothing.
  const timed = rows.some(c => c.last_at);
  const latest = timed ? rows.reduce((best, c) =>
      (!best || (c.last_at || 0) > (best.last_at || 0)) ? c : best, null) : null;
  const section = (c) => (c.state === "needs_you" || c.state === "working") ? c.state : "idle";
  const now = Date.now() / 1000;
  // The two states worth interrupting for come first, from every task: an
  // agent asking you something about another task is still asking you.
  for (const [id, heading] of SECTIONS) {
    if (id === "idle") continue;
    const these = rows.filter(c => section(c) === id);
    if (!these.length) continue;
    menu.append(el("div", { class: `conv-sec ${id}` },
      el("span", { text: heading }),
      el("span", { class: "conv-sec-n", text: String(these.length) })));
    for (const conv of these) menu.append(conversationRow(conv, now, false));
  }
  // Then the idle ones, which is most of them. As one flat list across tasks,
  // the same run id means different runs in different tasks and the day's
  // work sits among last month's. The task you are looking at comes first,
  // its rows under day headings; every other task is one folded line that
  // says how many and how recent, and opens on a click or a typed filter.
  const idle = rows.filter(c => section(c) === "idle");
  const here = state.task || "";
  const mine = idle.filter(c => taskOf(c) === here);
  const others = idle.filter(c => taskOf(c) !== here);
  if (mine.length) {
    menu.append(el("div", { class: "conv-sec idle" },
      el("span", { text: !timed ? "by run (restart the studio for times)"
                             : `this task · ${here}` }),
      el("span", { class: "conv-sec-n", text: String(mine.length) })));
    let day = null;
    for (const conv of mine) {
      const bucket = timed ? dayBucket(conv.last_at, now) : "";
      if (bucket && bucket !== day) {
        day = bucket;
        menu.append(el("div", { class: "conv-day", text: bucket }));
      }
      menu.append(conversationRow(conv, now, conv === latest));
    }
  }
  if (others.length) {
    menu.append(el("div", { class: "conv-sec idle" },
      el("span", { text: here ? "other tasks" : "by task" }),
      el("span", { class: "conv-sec-n", text: String(others.length) })));
    const byTask = new Map();
    for (const conv of others) {
      const task = taskOf(conv) || "task not known";
      if (!byTask.has(task)) byTask.set(task, []);
      byTask.get(task).push(conv);
    }
    const newest = (convs) => Math.max(...convs.map(c => c.last_at || 0));
    const groups = [...byTask.entries()].sort((a, b) => newest(b[1]) - newest(a[1]));
    for (const [task, convs] of groups) {
      const shown = !!q || !!(state.convTaskOpen || {})[task];
      menu.append(el("button", {
        class: `mrow conv-task${shown ? " on" : ""}`, key: `task:${task}`,
        "aria-expanded": shown ? "true" : "false",
        onclick: (e) => {
          e.stopPropagation();
          const opened = { ...(state.convTaskOpen || {}) };
          opened[task] = !opened[task];
          set({ convTaskOpen: opened });
        } },
        el("span", { class: "conv-task-caret", text: shown ? "▾" : "▸" }),
        el("span", {},
          el("span", { class: "mn", text: task }),
          el("span", { class: "mr", text: `${convs.length} conversation${convs.length === 1 ? "" : "s"}`
                                       + (convs[0].title ? ` · ${convs[0].title}` : "") })),
        el("span", { class: "conv-when", text: timed ? ago(newest(convs)) : "" })));
      if (shown) for (const conv of convs) menu.append(conversationRow(conv, now, conv === latest));
    }
  }
  menu.append(el("button", { class: "mrow conv-new",
    onclick: () => { actionsOf().toggleConversations(); actionsOf().newThread(); } },
    el("span", { class: "mn", text: "+ new conversation" })));
  menu.append(externalSessions());
  return el("span", { class: "menuwrap" }, button, menu);
}

/** Which task a conversation is about: what the server says, else what the
 * rail knows (its runs are the current task's), else the id's own prefix. */
function taskOf(conv) {
  if (conv.task) return conv.task;
  const run = String(conv.run || "");
  if (run.startsWith("task:")) return run.slice(5);
  if ((state.runs || []).some(r => r.id === run)) return state.task || "";
  return "";
}

/** "today", "yesterday", "this week", "earlier" -- by the local calendar,
 * because "yesterday" at 00:30 means last night, not 24 hours ago. */
function dayBucket(at, now) {
  if (!at) return "";
  const day = (t) => { const d = new Date(t * 1000); return Math.floor((d.getTime() - d.getTimezoneOffset() * 60000) / 86400000); };
  const diff = day(now) - day(at);
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  if (diff < 7) return "this week";
  if (diff < 31) return "this month";
  return "earlier";
}

/** One box that narrows the list. Only when there is a list worth narrowing:
 * three conversations do not need a search field above them. */
function convFilter(count) {
  if (count < 6 && !state.convFilter) return null;
  const input = el("input", { class: "conv-filter", type: "search",
    placeholder: "filter by run, thread, task or words…", autocomplete: "off",
    onclick: (e) => e.stopPropagation(),
    onkeydown: (e) => {
      e.stopPropagation();
      if (e.key === "Escape") { e.preventDefault(); set({ convFilter: "" }); }
    },
  });
  input.value = state.convFilter || "";
  on(input, "input", (e) => set({ convFilter: e.currentTarget.value }));
  return el("div", { class: "conv-filterwrap", onclick: (e) => e.stopPropagation() }, input);
}

/** The apps' own conversations about this subject, offered for continuing here.
 *
 * Claude Code files every session under the directory it was begun in, and
 * this studio's sessions are the same files (the SDK it hosts *is* that CLI).
 * So "the conversation I had in the terminal about this run" is a file the
 * studio can name, and continuing it here is one click: the next message
 * resumes it with its memory. Fetched when opened, not on every menu paint --
 * it reads a directory of transcripts.
 */
function externalSessions() {
  const who = subject(state);
  const open = !!state.attachOpen;
  const head = el("button", { class: "mrow conv-new conv-ext-h",
    "aria-expanded": open ? "true" : "false",
    onclick: (e) => {
      e.stopPropagation();
      const next = !open;
      set({ attachOpen: next });
      if (next && who) {
        set({ external: { loading: true } });
        api.agentSessions(who)
          .then(payload => set({ external: payload }))
          .catch(err => set({ external: { error: String(err.message || err) } }));
      }
    } },
    el("span", { class: "mn", text: `${open ? "▾" : "▸"} continue a Claude Code or Codex session here…` }));
  if (!open) return el("div", { class: "conv-ext" }, head);
  const box = el("div", { class: "conv-ext on" }, head);
  const ext = state.external || {};
  if (!who) {
    box.append(el("div", { class: "conv-empty", text: "select a run first" }));
    return box;
  }
  if (ext.loading) {
    box.append(el("div", { class: "conv-empty", text: "reading the session files…" }));
    return box;
  }
  if (ext.error) {
    box.append(el("div", { class: "m-error", text: ext.error }));
    return box;
  }
  // Both apps' sessions in one list, newest first; each row remembers which
  // app it belongs to, because only that app's provider can continue it.
  const rows = [
    ...(ext.claude || []).map(r => ({ ...r, provider: "claude" })),
    ...(ext.codex || []).map(r => ({ ...r, provider: "codex" })),
  ].sort((a, b) => (b.at || 0) - (a.at || 0));
  if (ext.cwd) {
    box.append(el("div", { class: "conv-ext-cwd" },
      el("span", { text: "sessions begun in " }), el("code", { text: ext.cwd })));
  }
  if (!rows.length) {
    box.append(el("div", { class: "conv-empty", text: "no Claude Code or Codex sessions in that directory yet" }));
  }
  for (const row of rows) {
    box.append(el("button", { class: "mrow conv-row conv-ext-row", key: `ext:${row.provider}:${row.id}`,
      title: `${row.id}\ncontinue this ${row.provider === "codex" ? "Codex" : "Claude Code"} session in the conversation that is open here`,
      onclick: async (e) => {
        e.stopPropagation();
        try {
          await api.agentAttach(who, row.id, state.thread || "main", row.provider);
          set({ attachOpen: false, external: null, agentError: "" });
          actionsOf().toggleConversations();
          actionsOf().openConversation(who, state.thread || "main");
        } catch (err) {
          set({ agentError: `could not attach: ${err.message || err}` });
        }
      } },
      el("span", { class: `conv-via ${row.via || "app"}`, text: row.via || "app" }),
      el("span", {},
        el("span", { class: "mn", text: row.title || row.id.slice(0, 8) }),
        el("span", { class: "mr", text: `${row.id.slice(0, 8)} · ${sizeOf(row.bytes)}` })),
      el("span", { class: "conv-when", text: ago(row.at) })));
  }
  return box;
}

function sizeOf(bytes) {
  const n = Number(bytes || 0);
  return n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB`
       : n >= 1024 ? `${Math.round(n / 1024)} kB` : `${n} B`;
}

const SECTIONS = [
  ["needs_you", "needs you"],
  ["working", "working now"],
  ["idle", "recent, newest first"],
];

/** One row of the menu: who, what it was about, and when or how long. */
function conversationRow(conv, now, latest) {
  const look = CONV_STATE[conv.state] || CONV_STATE.idle;
  const here = conv.run === subject(state) && conv.thread === state.thread;
  const working = conv.state === "working";
  // The right column answers a different question per section: how long it
  // has been at it, that it is waiting on you, or when it last did anything.
  let mark;
  if (working) {
    const since = conv.since || conv.last_at || now;
    mark = el("span", { class: "conv-state working", text: `working · ${forTime(now - since)}` });
  } else if (conv.state === "needs_you") {
    mark = el("span", { class: "conv-state needs_you", text: look.label });
  } else {
    mark = el("span", { class: "conv-when", text: ago(conv.last_at) });
  }
  return el("button", {
    class: `mrow conv-row ${conv.state}`, key: `${conv.run}#${conv.thread}`,
    "aria-current": here ? "true" : null,
    onclick: () => { actionsOf().toggleConversations();
                     actionsOf().openConversation(conv.run, conv.thread); },
  },
    dot(look.dot, working),
    el("span", {},
      // `main` is the default thread and naming it in every row is noise;
      // a thread that was deliberately started has a name worth showing.
      el("span", { class: "mn" },
        conv.thread && conv.thread !== "main"
          ? `${label(conv.run)} · ${conv.thread}` : label(conv.run),
        latest ? el("span", { class: "conv-latest", text: "latest" }) : null),
      el("span", { class: "mr", text: conv.title || "" }),
      // The same conversation, from a terminal: the studio's sessions are
      // Claude Code sessions, so `claude --resume <id>` in the run's directory
      // opens this one with its memory. Copied, not run: the terminal is
      // yours. One driver at a time -- send nothing here while it is open there.
      conv.session_id
        ? el("button", { class: "ghost conv-resume", title: `copy: continue this conversation in a terminal\n${conv.session_id}`,
            onclick: (e) => { e.stopPropagation(); copyResume(conv, e.currentTarget); } },
            "⧉ resume in terminal")
        : null),
    mark);
}

function copyResume(conv, button) {
  const cd = conv.session_cwd ? `cd ${shellQuote(conv.session_cwd)} && ` : "";
  // Each app's own spelling: Claude Code resumes by session file, Codex by
  // thread id (the same id its App Server gave the studio).
  const resume = conv.session_provider === "codex"
    ? `codex resume ${conv.session_id}` : `claude --resume ${conv.session_id}`;
  const cmd = `${cd}${resume}`;
  const done = () => { button.textContent = "copied"; setTimeout(() => { button.textContent = "⧉ resume in terminal"; }, 1500); };
  try {
    navigator.clipboard.writeText(cmd).then(done, () => window.prompt("continue in a terminal:", cmd));
  } catch {
    window.prompt("continue in a terminal:", cmd);
  }
}

function shellQuote(s) {
  return /^[A-Za-z0-9_\/.~-]+$/.test(s) ? s : `'${String(s).replace(/'/g, "'\\''")}'`;
}

/** A subject as a person says it: `task:my_task` is "my_task", a run is its id. */
export function label(who) {
  return String(who || "").startsWith("task:") ? String(who).slice(5) : String(who || "");
}

/** The action table, which this module is not handed. */
export function actionsOf() {
  return window.__studioActions || {};
}

// Which conversation you are in, and how to leave it. Small, because most of
// the time there is one and the bar should not argue about it.
export function threadBar() {
  const who = subject(state);
  if (!who) return null;
  // Where the rail is, as opposed to who you are talking to. They differ only
  // while a conversation is pinned somewhere else, and that is the one state
  // this bar must not leave implicit: a panel quietly addressing one run while
  // the charts on screen are another's is how a message goes to the wrong
  // agent.
  const viewing = naturalSubject(state);
  const elsewhere = !!state.convPin && !!viewing && viewing !== who;
  const server = (state.conversations || []).filter(c => c.run === who);

  // A conversation exists on the server only once something has been said in
  // it, so a thread you just started is in no list -- and a bar that drew
  // only the *other* server threads would make `main` unreachable the moment
  // you pressed "+". The bar is the union: every thread the server knows, every
  // one started here, and always `main`.
  const started = (state.threads || {})[who] || [];
  const names = new Set(["main", state.thread,
                         ...started, ...server.map(c => c.thread)]);
  const byName = Object.fromEntries(server.map(c => [c.thread, c]));
  const others = [...names].filter(n => n !== state.thread).sort();

  return el("div", { class: `threadbar${elsewhere ? " pinned" : ""}` },
    // Where you are, said in full. `main` on its own is true and tells you
    // nothing: it is the thread of *some* run, and which run is two panels
    // away in the rail.
    el("span", { class: "th-here" },
      el("b", { text: who.startsWith("task:") ? who.slice(5) : who }),
      // The thread's name, only when there is more than one to tell apart.
      // `main` is the default and it is jargon: it names a thread, a run holds
      // several so a long investigation and a quick "what is this metric" do
      // not share a context window, and with exactly one open the word answers
      // a question nobody asked.
      names.size > 1
        ? el("span", { class: "th-name", text: state.thread })
        : null),
    names.size > 1
      ? el("span", { class: "th-count", text: `${names.size} here` })
      : null,
    el("span", { style: { marginLeft: "auto" } }),
    // The way back to the view. Only drawn when there is somewhere to go back
    // to, so the ordinary case -- one conversation, about what you are looking
    // at -- gains no chrome at all.
    elsewhere
      ? el("button", {
          class: "th-follow",
          title: `you are talking to ${label(who)} while the screen shows `
               + `${label(viewing)}. Switch the conversation to ${label(viewing)}.`,
          onclick: () => actionsOf().followCurrentRun(),
        }, `\u21b5 ${label(viewing)}`)
      : null,
    conversations(),
    others.map(name => el("button", {
      class: `th-jump ${(byName[name] || {}).state || "empty"}`,
      title: byName[name] ? `${name} — ${byName[name].state}` : `${name} — nothing said yet`,
      onclick: () => window.__studioActions.openConversation(who, name),
    }, name)),
    el("button", { class: "act",
                   title: "start a second conversation about this run, with a "
                        + "context window of its own",
                   onclick: () => window.__studioActions.newThread() }, "+"));
}
