// The terminal drawer: shells on the machine the studio runs on.
//
// Hidden until asked for, and it stays open across repaints and view switches,
// because a shell you are in the middle of using is not a panel — throwing it
// away would kill the process. *Hiding* it does not throw it away either: the
// sockets stay up while the drawer is shut, so the `tail -f` you started and
// the build you left running are still running when you open it again.
//
// **It is a terminal, not a text box.** A line editor in an `<input>` that
// sends a whole line at Enter, plus a small parser for colour, backspace and
// carriage return, is not a terminal: Up walks a history the browser keeps
// rather than the one readline keeps, Tab cannot complete, ^R searches nothing,
// and `less`, `vim`, `htop`, `git log`, `ssh`, a password prompt and anything
// else that paints a screen or takes the keyboard cannot work at all.
//
// So the browser sends **keystrokes**, and that is the whole difference: every
// character goes to the pty as it is typed, the shell's own readline does the
// editing, the history, the completion and the search, and a program that takes
// the keyboard gets it. Nothing here knows what a command is.
//
// **The emulator is vendored, like the editor.** xterm.js — what VS Code's
// terminal is built on — lives in `web/vendor/xterm` and loads from a script
// tag, for the same reason Monaco does: this is a local tool that must work
// with no network, and the client has no build step and no Node toolchain. It
// brings the screen, the scrollback, the selection, the reflow on resize and
// the parser for the sequences a real `TERM` emits; writing that by hand is how
// a drawer ends up as a text box.
//
// **Several shells, like any other terminal.** Each tab is its own socket,
// which is its own pty on the server: `LocalPty` is keyed by handle,
// and the socket owns the shell's lifetime, so a second terminal needs nothing
// from the server that a first one did not.

import { el } from "./ui.js";
import { fits, divider } from "./resize.js";
import { state, set } from "./state.js";

const VENDOR = "/static/vendor/xterm";
const INTERRUPT = "\x03";               // ^C

/** Every shell this browser has open, in tab order. Keyed by id.
 *
 * Module state rather than store state, and deliberately: a shell owns DOM
 * nodes that must survive every repaint — `patch()` would take the screen with
 * it — and a live socket that must survive a view switch. What the store holds
 * is only what the tab strip has to *draw*, mirrored by `publish()`.
 */
const shells = new Map();
let order = [];
let made = 0;

function shellOf(id) { return shells.get(id || state.termActive); }

/** What the tab strip draws. The screens themselves never go in the store. */
function publish(extra = {}) {
  set({
    terms: order.map(id => {
      const shell = shells.get(id);
      return { id, name: shell.name, where: shell.where,
               live: shell.live, error: shell.error };
    }),
    ...extra,
  });
}

// ── the emulator, loaded once ────────────────────────────────────────────

let loading = null;

/** Load the vendored xterm build and its fit addon.
 *
 * Both are UMD, so they land on `window` as `Terminal` and `FitAddon`. The
 * addon is not optional: it is what measures a cell against the pane's real
 * font and turns pixels into rows and columns. A shell told a size nobody
 * measured wraps anything that lays out to the terminal width to a width the
 * pane never had.
 */
function library() {
  if (window.Terminal && window.FitAddon) return Promise.resolve(window.Terminal);
  if (loading) return loading;
  loading = new Promise((resolve, reject) => {
    const sheet = document.createElement("link");
    sheet.rel = "stylesheet";
    sheet.href = `${VENDOR}/xterm.css`;
    document.head.append(sheet);

    // Monaco's loader leaves a global AMD `define` behind. A UMD bundle that
    // sees one registers itself as an anonymous module rather than setting
    // `window.Terminal`, and the loader rejects that -- so hide it while
    // xterm's two scripts run, and put it back either way.
    const amd = window.define;
    window.define = undefined;
    const done = (fn) => (value) => { window.define = amd; fn(value); };

    const core = document.createElement("script");
    core.src = `${VENDOR}/xterm.js`;
    core.onload = () => {
      const addon = document.createElement("script");
      addon.src = `${VENDOR}/addon-fit.js`;
      addon.onload = () => done(resolve)(window.Terminal);
      addon.onerror = () => done(reject)(new Error("could not load the terminal's fit addon"));
      document.head.append(addon);
    };
    core.onerror = () => done(reject)(new Error("could not load the terminal library"));
    document.head.append(core);
  });
  return loading;
}

/** The screen's colours, read from the app's own tokens.
 *
 * xterm ships the literal ANSI palette, which was chosen for a black
 * background: half of it is unreadable on this app's light scheme, and none of
 * it belongs to the app. These are the same tokens every other panel styles
 * through, so the shell follows the scheme like everything else.
 */
function palette() {
  const css = getComputedStyle(document.documentElement);
  const v = (name, fallback) => css.getPropertyValue(name).trim() || fallback;
  const ink = v("--text", "#E9E5DE");
  return {
    background: v("--sunken", "#131211"),
    foreground: ink,
    cursor: v("--accent", "#9FB4C6"),
    cursorAccent: v("--sunken", "#131211"),
    selectionBackground: v("--accent-soft", "#26313C"),
    selectionForeground: ink,
    black: v("--line", "#38342F"),
    red: v("--bad", "#D8615D"),
    green: v("--ok", "#4CA97D"),
    yellow: v("--warn", "#C9922F"),
    blue: v("--v-best", "#4FA3DC"),
    magenta: v("--accent", "#9FB4C6"),
    cyan: v("--v-control", "#8394BC"),
    white: v("--muted", "#A29C93"),
    brightBlack: v("--dim", "#736D65"),
    brightRed: v("--bad", "#D8615D"),
    brightGreen: v("--ok", "#4CA97D"),
    brightYellow: v("--warn", "#C9922F"),
    brightBlue: v("--v-best", "#4FA3DC"),
    brightMagenta: v("--accent", "#9FB4C6"),
    brightCyan: v("--v-control", "#8394BC"),
    brightWhite: ink,
  };
}

// The scheme is a button in the chrome and the tokens change under us when it
// is pressed. Watched here rather than called from `main.js` for the same
// reason the sockets live here: the drawer owns what only the drawer knows.
if (typeof MutationObserver !== "undefined") {
  new MutationObserver(() => {
    const colours = palette();
    for (const shell of shells.values()) {
      if (shell.term) shell.term.options.theme = colours;
    }
  }).observe(document.documentElement, { attributes: true,
                                         attributeFilter: ["data-scheme"] });
}

// ── one shell ────────────────────────────────────────────────────────────

function makeShell() {
  made += 1;
  return {
    id: `t${made}`,
    n: made,
    name: `shell ${made}`,
    where: "",
    socket: null,
    live: false,
    error: "",
    // The emulator and the node it paints into. The node is created now and
    // re-parented into every fresh render, never rebuilt: moving a DOM node
    // keeps the node, and xterm keeps its screen, its scrollback and its
    // selection with it.
    term: null,
    fitter: null,
    host: el("div", { class: "term-screen" }),
    // Output that arrived before the emulator finished loading. A socket can
    // answer faster than a 290 KB script parses, and a shell that dropped its
    // own first prompt would come up looking dead.
    waiting: [],
    cols: 80,
    rows: 24,
  };
}

/** Give a shell its emulator, once its node is on screen.
 *
 * Not before: xterm measures a character against the font of the element it is
 * opened in, and an element that is not in the document has no font and no
 * width. Everything about the size follows from that measurement.
 */
function boot(shell) {
  const term = new window.Terminal({
    fontFamily: getComputedStyle(document.documentElement)
                  .getPropertyValue("--mono").trim() || "monospace",
    fontSize: 12,
    lineHeight: 1.15,
    cursorBlink: true,
    // A shell that prints a stack trace is a shell you scroll back through.
    scrollback: 10_000,
    // Three lines a notch: the drawer is fifteen lines tall, and a wheel that
    // moves five of them is the difference between reading output and hunting
    // it.
    scrollSensitivity: 3,
    macOptionIsMeta: true,
    theme: palette(),
  });
  // `window.FitAddon` is the addon's *module* — the UMD build puts one export
  // on the global, and the export is a namespace holding the class of the same
  // name. `new window.FitAddon()` is the mistake it invites, and it fails at
  // the moment a shell is opened rather than at load.
  const fitter = new window.FitAddon.FitAddon();
  term.loadAddon(fitter);
  term.open(shell.host);
  shell.term = term;
  shell.fitter = fitter;

  // Keystrokes, not lines: this is the line the whole drawer turns on. Every
  // character goes to the pty as it is typed, so Up is readline's history and
  // not ours, Tab is readline's completion, ^R is its search, and `vim` gets
  // the keyboard.
  term.onData(data => send(shell, data));
  term.onBinary(data => send(shell, data));
  term.onResize(({ cols, rows }) => {
    shell.cols = cols;
    shell.rows = rows;
    if (shell.socket && shell.socket.readyState === 1) {
      shell.socket.send(JSON.stringify({ kind: "resize", cols, rows }));
    }
  });

  // ^C is a signal in a terminal, which leaves copying to ^⇧C — the binding
  // every other terminal uses for exactly this reason. Paste needs nothing:
  // xterm's own textarea receives the browser's paste event, brackets it if
  // the program asked for that, and sends it.
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== "keydown" || !e.ctrlKey || !e.shiftKey) return true;
    if (e.key !== "C" && e.key !== "c") return true;
    const chosen = term.getSelection();
    if (!chosen) return true;                   // nothing to copy; let ^C go
    if (navigator.clipboard) navigator.clipboard.writeText(chosen).catch(() => {});
    return false;
  });

  for (const chunk of shell.waiting) term.write(chunk);
  shell.waiting = [];
  fitNow(shell);
}

/** Measure the pane and reshape the screen to it.
 *
 * Guarded rather than trusted: `fit()` reads the host's box, and a host that
 * is display:none or not yet laid out has none — a drawer opening is exactly
 * that moment.
 */
function fitNow(shell) {
  if (!shell || !shell.term || !shell.host.isConnected) return;
  try {
    shell.fitter.fit();
  } catch {
    /* the pane has no size yet; the next paint will bring one */
  }
}

/** Name a tab after what its shell is doing.
 *
 * "shell 1, shell 2, shell 3" is a strip you have to click through to find the
 * one you meant, which is the whole reason every other terminal labels them.
 * The name is the program the shell is running if it is running one, and the
 * directory it is sitting in if it is not -- the two things that tell three
 * open shells apart. The number stays as the fallback, for a platform that
 * cannot answer and for the moment before the first output arrives.
 */
function named(shell, cwd, busy) {
  const home = "/home/";                        // only for the tilde, below
  const dir = String(cwd || "");
  const leaf = dir === "/" ? "/" : dir.split("/").filter(Boolean).pop() || "";
  // A shell sitting in your home directory should say so the way a prompt
  // does, rather than showing your login name as if it were a project.
  const tilde = dir && dir.startsWith(home) && dir.split("/").length === 3;
  shell.name = String(busy || "") || (tilde ? "~" : leaf) || `shell ${shell.n}`;
  shell.where = dir;
  publish();
}

/** Write pty output to one shell's screen. */
export function feed(chunk, id = "") {
  const shell = shellOf(id);
  if (!shell) return;
  const text = String(chunk == null ? "" : chunk);
  if (shell.term) shell.term.write(text);
  else shell.waiting.push(text);
}

// ── sockets ──────────────────────────────────────────────────────────────

/** Open a shell and attach it to a socket. Idempotent per shell. */
async function dial(shell) {
  if (shell.socket && shell.socket.readyState <= 1) return;
  let info;
  try {
    info = await fetch("/api/terminal").then(r => r.json());
  } catch {
    shell.error = "the studio did not answer";
    publish();
    return;
  }
  if (!info.available) {
    shell.error = info.reason || "no terminal on this studio";
    publish();
    return;
  }

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/ws/terminal`
            + `?token=${encodeURIComponent(info.token)}`
            + `&cwd=${encodeURIComponent(info.cwd || "")}`
            + `&cols=${shell.cols}&rows=${shell.rows}`;
  const socket = new WebSocket(url);
  shell.socket = socket;
  socket.addEventListener("open", () => {
    shell.live = true; shell.error = ""; publish();
    // The pane can have been dragged taller between measuring and connecting,
    // and the pty was forked with whatever the query string said.
    socket.send(JSON.stringify({ kind: "resize", cols: shell.cols, rows: shell.rows }));
  });
  socket.addEventListener("message", (e) => {
    let m;
    try { m = JSON.parse(e.data); } catch { return; }
    if (m.kind === "out") feed(m.data, shell.id);
    else if (m.kind === "ready") { shell.error = ""; publish(); }
    else if (m.kind === "error") { shell.error = m.text || "it would not start"; publish(); }
    else if (m.kind === "where") named(shell, m.cwd, m.busy);
    else if (m.kind === "exit") feed("\r\n\x1b[2m[the shell exited]\x1b[0m\r\n", shell.id);
  });
  socket.addEventListener("close", () => {
    shell.socket = null; shell.live = false; publish();
  });
}

/** Open the drawer's first shell, or leave the ones it already has alone.
 *
 * Alone, and that is the point: the drawer being shut is not a reason to kill
 * a shell. Closing a socket closes its pty, so closing them on the way out
 * would kill whatever is running -- the very thing you hid the panel to get
 * out of the way of.
 */
export async function connect() {
  if (!order.length) open();
  else for (const id of order) await dial(shells.get(id));
}

/** A new terminal: its own socket, its own pty, its own scrollback. */
export function open() {
  const shell = makeShell();
  shells.set(shell.id, shell);
  order = order.concat(shell.id);
  publish({ termActive: shell.id });
  // The screen is mounted by the paint that this `publish` causes; the load
  // may land after it, so mount again from here. Both ends are idempotent.
  library().then(() => {
    const body = document.querySelector(".term-body");
    if (body) mount(shell, body);
  }).catch((err) => {
    shell.error = err.message;
    publish();
  });
  return shell.id;
}

/** Close one terminal. Closing the last one closes the drawer with it. */
export function closeOne(id, actions) {
  const shell = shells.get(id);
  if (!shell) return;
  if (shell.socket) shell.socket.close();
  if (shell.term) shell.term.dispose();
  shells.delete(id);
  const was = order.indexOf(id);
  order = order.filter(other => other !== id);
  if (!order.length) {
    publish({ termActive: "" });
    if (actions && actions.toggleTerminal) actions.toggleTerminal();
    return;
  }
  const next = order[Math.min(was, order.length - 1)];
  publish({ termActive: state.termActive === id ? next : state.termActive });
  focusSoon();
}

function show(id) {
  if (!shells.has(id)) return;
  publish({ termActive: id });
  focusSoon();
}

/** Put the keyboard back in the terminal.
 *
 * After the paint, because the node that ends up on screen is not necessarily
 * the node the render built.
 */
function focusSoon() {
  requestAnimationFrame(() => {
    const shell = shellOf("");
    if (shell && shell.term) shell.term.focus();
  });
}

/** Mount the active shell into the pane that survived the paint.
 *
 * Three things, all of which have to happen after a paint rather than during
 * one: the screen is re-homed if the body it was in is not the body on screen,
 * the emulator is created the first time its node is in the document, and the
 * pty is told the size the pane actually turned out to be.
 */
function mount(shell, body) {
  if (!shell) return;
  const moved = shell.host.parentElement !== body;
  if (moved) body.replaceChildren(shell.host);
  if (!shell.term) {
    if (!window.Terminal || !window.FitAddon) return;   // still loading
    boot(shell);
    dial(shell);
  } else {
    fitNow(shell);
    // A screen that has been out of the document — the drawer was shut, or
    // another tab was in front — has been written to while nothing was
    // painting it. Redrawn rather than left to the next keystroke to reveal.
    if (moved) shell.term.refresh(0, shell.term.rows - 1);
  }
  // A repaint can leave the document with nothing focused — and then Up
  // scrolls the pane instead of walking the shell's history, and typing stops
  // working a second after you click. Only when focus has
  // actually drifted to nowhere: a person who has clicked into the agent's
  // composer keeps it.
  const away = document.activeElement;
  if (!away || away === document.body) shell.term.focus();

  // The pane is resized by its own drag handle, by the window, and by every
  // panel around it. One observer on the body covers all three; the paint it
  // would otherwise run inside is deferred, which is what keeps the browser
  // from complaining about a resize loop.
  if (!body.__termFit && typeof ResizeObserver !== "undefined") {
    body.__termFit = new ResizeObserver(() => {
      requestAnimationFrame(() => fitNow(shellOf("")));
    });
    body.__termFit.observe(body);
  }
}

function send(shell, text) {
  if (shell.socket && shell.socket.readyState === 1) {
    shell.socket.send(JSON.stringify({ kind: "in", data: text }));
  }
}

// ── the drawer ───────────────────────────────────────────────────────────

/** The drawer. Returns null when it is closed, which is most of the time. */
export function terminalDrawer(s, actions) {
  if (!s.termOpen) return null;
  const tabs = s.terms || [];
  const active = shellOf(s.termActive) || shellOf(order[0]);

  // `data-keep-children` so the reconciler morphs this host without touching
  // the screen inside it — but the screen is *not* appended here.
  //
  // `host` is one node, and appending it moves it. This function runs more
  // than once per frame (a `set` during connect, another on the first output),
  // so an earlier call's body has the node and the later call steals it — then
  // `patch()` keeps the *first* body, which is now empty, and discards the
  // second, taking the screen out of the document with it, and the drawer
  // comes up blank while the socket is plainly live.
  //
  // So: build the host empty, and mount the active screen into whichever body
  // actually survived, after the paint. `agent.js` does the same thing to
  // scroll the transcript, for the same reason — the node you built is not
  // necessarily the node on screen.
  const body = el("div", { class: "term-body", key: "term-body",
                           "data-keep-children": "" });
  queueMicrotask(() => {
    const live = document.querySelector(".term-body");
    if (live) mount(active, live);
  });

  const strip = el("div", { class: "term-tabs" },
    tabs.map(t => el("div", {
      class: `term-tab${t.id === s.termActive ? " on" : ""}`,
      onmousedown: () => show(t.id),
      title: t.error || t.where || (t.live ? "attached" : "not attached"),
    },
      el("span", { class: `term-dot${t.live ? " live" : ""}${t.error ? " bad" : ""}` }),
      el("span", { class: "term-name", text: t.name }),
      el("button", {
        class: "term-x", title: "close this shell",
        // `mousedown` and stopped here, or the tab underneath activates the
        // shell on the way to closing it.
        onmousedown: (e) => { e.stopPropagation(); e.preventDefault();
                              closeOne(t.id, actions); },
      }, "×"))),
    el("button", { class: "term-new", title: "a new shell",
                   onclick: () => open() }, "+"),
    el("span", { class: "sp" }),
    active && active.error
      ? el("span", { class: "term-note bad", text: active.error })
      : null,
    // `mousedown` prevented on the controls that act on the *shell*: a button
    // that takes the focus is a button that leaves you typing into nothing,
    // which is the same problem as the arrow keys and the same fix.
    el("button", { class: "act", title: "interrupt what is running",
                   onmousedown: (e) => e.preventDefault(),
                   onclick: () => { if (active) { send(active, INTERRUPT); focusSoon(); } } },
       "^C"),
    el("button", { class: "act", title: "clear the screen",
                   onmousedown: (e) => e.preventDefault(),
                   onclick: () => { if (active && active.term) { active.term.clear(); focusSoon(); } } },
       "Clear"),
    el("button", { class: "act", onclick: () => actions.toggleTerminal() }, "Hide"));

  return el("div", { class: "termpane", key: "terminal",
                     style: { height: `${fits("term", s.widths.term || 240)}px` } },
    strip, body,
    // Every other panel in this app can be dragged, and `resize.js` carries
    // `term: 240` with bounds of 120..700. A terminal you cannot make taller
    // is one you stop using the moment a command prints thirty lines.
    divider("term", {
      axis: "y", side: "top",
      onResize: (px) => actions.setPanelWidth("term", px),
      onReset: (px) => actions.setPanelWidth("term", px),
    }));
}
