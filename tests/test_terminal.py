"""The terminal, and the guard in front of it.

Every other route in this studio reads. This one executes, on a port with no
authentication, so most of what is worth testing here is what it *refuses*.
"""

from __future__ import annotations

import pytest

from rlmcp_studio.terminals.guard import Guard
from rlmcp_studio.terminals.local import LocalPty


# ── who may open one ─────────────────────────────────────────────────────
@pytest.fixture
def guard():
  return Guard(host="127.0.0.1", port=8730, token="the-right-token")


def test_this_studio_is_allowed(guard):
  assert guard.refuse("http://127.0.0.1:8730", "the-right-token") is None
  assert guard.refuse("http://localhost:8730", "the-right-token") is None


def test_another_page_is_refused_even_with_the_token(guard):
  """The drive-by: any page in any tab can open a WebSocket to localhost. It
  cannot forge `Origin`, and that is the check that stops it dead."""
  assert guard.refuse("https://evil.example", "the-right-token")
  assert guard.refuse("http://127.0.0.1:9999", "the-right-token")


def test_a_missing_origin_is_refused(guard):
  """Tempting to allow, for the sake of non-browser clients -- and exactly the
  hole, because the header's absence is what an attacker controls most easily.
  `curl` omits it."""
  assert guard.refuse(None, "the-right-token")
  assert guard.refuse("", "the-right-token")


def test_the_token_still_has_to_be_right(guard):
  """Origin is a browser's promise and a native program makes no such promise.
  The token is the half a caller must *know*."""
  assert guard.refuse("http://127.0.0.1:8730", "guessed")
  assert guard.refuse("http://127.0.0.1:8730", None)


def test_the_reason_is_for_the_log_and_not_for_the_caller(guard):
  """Telling an unauthenticated client which of the two checks it failed is
  telling it how to pass. The route logs this; it does not send it."""
  assert "origin" in guard.refuse("https://evil.example", "the-right-token")
  assert "token" in guard.refuse("http://127.0.0.1:8730", "wrong")


def test_a_token_is_generated_when_none_is_given():
  first, second = Guard(), Guard()
  assert first.token and len(first.token) > 20
  assert first.token != second.token, "a restart is a new credential"


# ── the route ────────────────────────────────────────────────────────────
def test_the_studio_says_whether_a_terminal_is_available(client):
  answer = client.get("/api/terminal")
  assert answer.status_code == 200
  body = answer.json()
  assert "available" in body and "token" in body and body["backend"]


def test_a_socket_without_a_token_is_refused(client):
  """`TestClient` sends no Origin, which is itself a refusal -- so this covers
  both halves at once and proves the route checks before accepting."""
  from starlette.websockets import WebSocketDisconnect
  with pytest.raises(WebSocketDisconnect):
    with client.websocket_connect("/ws/terminal") as socket:
      socket.receive_text()


# ── the shell ────────────────────────────────────────────────────────────
def test_a_shell_runs_and_answers(tmp_path):
  """A pty rather than a pipe: without a terminal on the other end a shell
  disables its prompt and tools drop their colour, and a "terminal" made of
  pipes is a command runner wearing the wrong name.

  `asyncio.run` rather than a plugin -- one async test is not worth a test
  dependency, and the loop is the only thing being borrowed.
  """
  import asyncio

  pty = LocalPty()
  if pty.unavailable():
    pytest.skip(pty.unavailable())

  async def go():
    handle = await pty.start(cwd=str(tmp_path), cols=80, rows=24)
    seen = bytearray()

    async def drain():
      async for chunk in pty.read(handle):
        seen.extend(chunk)

    task = asyncio.create_task(drain())
    await asyncio.sleep(0.4)
    await pty.write(handle, "echo studio-terminal-ok\n")
    for _ in range(50):
      await asyncio.sleep(0.1)
      if b"studio-terminal-ok" in seen:
        break
    await pty.close(handle)
    task.cancel()
    return bytes(seen), handle

  seen, handle = asyncio.run(go())
  assert b"studio-terminal-ok" in seen
  assert handle not in pty.sessions, "closing removes the session"


def test_the_scrollback_is_bounded(tmp_path):
  """A `find /` left running is otherwise unbounded memory in a process the
  user cannot see."""
  from rlmcp_studio.terminals import local

  session = local._Session(pid=0, fd=-1, cwd=str(tmp_path))
  session.emit(b"x" * (local.SCROLLBACK + 5_000))
  assert len(session.buffer) == local.SCROLLBACK


# ── the drawer, as a piece of client source ──────────────────────────────
#
# Read as text, like the rest of the client checks, so the suite needs no
# JavaScript engine.

from pathlib import Path as _Path
from tests.client_source import read_css, read_js

WEB = _Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web"


def test_the_drawer_does_not_wear_the_reward_bars_class_name():
  """`.term` belongs to the reward-term bars in Build, whose rule sets
  `align-items: center`. A drawer wearing it too shrinks every child to its
  content and floats it in the middle of the strip: a header island, a thin
  body, and an input box hanging in space. Nothing about that reads as a
  terminal, and no amount of styling the children would fix it."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert 'class: "termpane"' in js
  assert 'class: "term"' not in js, "the bars own that name"

  css = read_css()
  bars = css.split("\n.term {", 1)
  assert len(bars) == 2, "the bars rule is still there, and still the only .term"
  assert "align-items: center" in bars[1].split("}", 1)[0], \
      "which is exactly what leaked into the drawer"
  assert ".termpane {" in css


def test_the_drawer_has_one_ground_and_it_is_the_app_s_own():
  """Three stacked surfaces — a white header bar, a `--sunken` body, a white
  input bar — read as three panels rather than as one terminal."""
  css = read_css()
  pane = css.split(".termpane {", 1)[1].split("}", 1)[0]
  assert "var(--ground)" in pane, "the same ground as the rest of the app"
  for gone in (".term-h {", ".term-c {", ".term-input {", ".term-in {"):
    assert gone not in css, f"{gone} would be a band or a box"


def test_what_you_type_goes_to_the_pty_a_keystroke_at_a_time():
  """A line editor in the browser — an `<input>` that collects a line and sends
  it at Enter — fails to be a terminal in every way that follows from that. Up
  walks a history the browser keeps rather than the one readline keeps — and
  scrolls the pane whenever focus has drifted out of the box. Tab cannot
  complete, ^R searches nothing, and `less`, `vim`, `htop` and a password
  prompt cannot run at all.

  So: keystrokes, straight through. Nothing in this module knows what a command
  is, which is why there is nothing left here to get wrong."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert "term.onData(data => send(shell, data))" in js, \
      "every character goes to the pty as it is typed"
  for gone in ("makeInput", "shell.history", "shell.at", "shell.input",
               '"ArrowUp"', 'class: "term-in"'):
    assert gone not in js, f"{gone} is a line editor that would make this a text box"


def test_the_shell_is_the_one_keeping_the_history():
  """The proof that the history is readline's and not ours: Up is not a key
  this module has an opinion about. It leaves as `\\x1b[A` because xterm sends
  it, and comes back as whatever the shell decides that means — which is the
  same in `bash`, in `python`, in `psql` and in anything else run in here.

  The one key the drawer does intercept is ^-shift-C, and only because ^C is a
  signal in a terminal and cannot also be copy."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert "attachCustomKeyEventHandler" in js
  handler = js.split("term.attachCustomKeyEventHandler((e) => {", 1)[1]
  handler = handler.split("\n  });", 1)[0]
  assert handler.count("return true") >= 2, "everything else is the shell's"
  assert "getSelection" in handler and "clipboard" in handler


def test_each_shell_owns_its_own_emulator_and_its_own_socket():
  """One `Terminal` per tab, each with its own screen, scrollback and socket.
  Two tabs sharing any of that is two shells writing into one screen."""
  js = (WEB / "js" / "terminal.js").read_text()
  made = js.split("function makeShell() {", 1)[1].split("\n}", 1)[0]
  for field in ("socket:", "term:", "fitter:", "host:", "waiting:"):
    assert field in made, f"{field} belongs to the shell, not to the module"

  assert "const shells = new Map()" in js
  # The store learns only what the tab strip draws. A screen in the store is a
  # DOM node a repaint would take with it, which is the bug this app is built
  # against.
  publish = js.split("function publish(", 1)[1].split("\n}", 1)[0]
  assert "screen" not in publish and "socket" not in publish


def test_closing_the_last_shell_closes_the_drawer():
  """A drawer holding no shells is a strip of empty ground with a `+` in it."""
  js = (WEB / "js" / "terminal.js").read_text()
  close = js.split("export function closeOne(", 1)[1].split("\n}", 1)[0]
  assert "if (!order.length)" in close and "toggleTerminal()" in close


def test_the_shell_is_told_how_big_the_pane_actually_is():
  """Guessing the width from `pane.clientWidth` at the moment the socket is
  dialled does not work: the pane does not exist yet then, so the guess falls
  back to a fixed default and `echo $COLUMNS` says the same number at any
  window size. Anything that lays out to the terminal width — `ls` in columns,
  a wrapped error, a progress bar — would wrap to a width the pane never had.

  The fit addon measures a cell against the pane's real font, which is why it
  is pinned alongside the emulator rather than left out as an extra."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert "new window.FitAddon.FitAddon()" in js, \
      "the global is the addon's module; the class is inside it"
  assert "term.loadAddon(fitter)" in js

  fit = js.split("function fitNow(shell) {", 1)[1].split("\n}", 1)[0]
  assert "host.isConnected" in fit, \
      "a pane that is not laid out has no size to measure"
  assert "fitter.fit()" in fit

  # One path to the server, off the emulator's own resize event: whatever
  # changed the size -- the drag handle, the window, another panel -- ends up
  # there rather than each of them having to remember to say so.
  resize = js.split("term.onResize(({ cols, rows }) => {", 1)[1].split("\n  });", 1)[0]
  assert '"resize"' in resize and "shell.socket.send" in resize

  assert "new ResizeObserver(" in js, "the pane is watched, not the window"
  assert "fitNow(shellOf(\"\"))" in js


def test_the_pane_is_told_to_leave_the_scrolling_alone():
  """Two scrollbars over one screen: xterm draws its own viewport and keeps its
  own scrollback, so a pane that also scrolled would put the shell's output
  inside a second scroller — and the wheel would move whichever one the pointer
  happened to be over."""
  css = read_css()
  body = css.split(".term-body {", 1)[1].split("}", 1)[0]
  assert "overflow: hidden" in body
  assert "font-family" not in body, "the font is the emulator's to measure"
  assert ".term-body .xterm { height: 100%; }" in css, \
      "the emulator fills the pane, or it lays out to nothing"


def test_a_password_is_hidden_by_the_tty_and_nothing_else(tmp_path):
  """An `echoing()` hook with an `echo` message on the socket and a
  `<input type=password>` at the other end would only matter with a line
  editor in the browser: a pty that stops echoing for `sudo` cannot hide
  characters that never reached it.

  The browser sends keystrokes, so a password is invisible for exactly the
  reason it is in any other terminal — the tty does not echo it and nothing
  comes back to draw. No other apparatus is needed, and this is the test that
  says it must not be added as belt and braces."""
  pty = LocalPty()
  assert not hasattr(pty, "echoing"), "the pty answers this by not echoing"

  server = (_Path(__file__).resolve().parent.parent
            / "rlmcp_studio" / "routes" / "machine.py").read_text()
  route = server.split("async def ws_terminal(", 1)[1].split("\n  @app.", 1)[0]
  assert '"kind": "echo"' not in route

  js = (WEB / "js" / "terminal.js").read_text()
  for gone in ('m.kind === "echo"', '"password"', "hidden-in"):
    assert gone not in js, f"{gone} belongs to a line editor in the browser"


def test_a_paste_is_the_emulator_s_to_make_sense_of():
  """An `<input>` silently joins the lines of a multi-line paste into one, so a
  five-line snippet would arrive as a single unrunnable command — the box
  quietly rewriting what was pasted. There is no box: the paste event lands on xterm's
  own textarea, which brackets it when the program has asked for that and sends
  it as it was written."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert "clipboardData" not in js and "lastIndexOf" not in js, \
      "the client does not rewrite what was pasted"


def test_the_drawer_can_be_opened_from_a_url():
  """There is no other way to photograph it. A headless browser cannot click
  the Terminal button, and this repository's rule is that a layout is verified
  by *looking* — a layout bug such as a class-name collision that leaves the
  drawer's contents floating in the middle of the strip passes every DOM
  assertion and shows only in a screenshot.

  Only from the URL, and only when asked: opening the drawer forks a shell, so
  it is deliberately not something the app remembers across a reload.
  """
  main = read_js("main.js", "app/*.js")
  assert 'get("term") === "1"' in main
  assert "!state.termOpen" in main, "asking twice does not fork two shells"
  # *In `boot`*, and this is not pedantry: the same block in another function
  # (a patch applied at the wrong offset, say) is syntactically fine, never
  # runs, and passes a test that only asks whether the file contains the line.
  # A check that cannot tell those apart is not a check.
  boot = main.split("async function boot() {", 1)[1].split("\n}", 1)[0]
  assert 'get("term") === "1"' in boot, "the query is read at boot"
  assert main.count('get("term") === "1"') == 1, "and nowhere else"

  goto = (WEB / "dev" / "goto.html").read_text()
  assert 'want.get("term")' in goto and "?term=1" in goto, \
      "passed through rather than stored, for the same reason"


def test_the_strips_controls_sit_at_the_far_end():
  """`.sp` is `margin-left: auto` scoped to each header that uses it, not a
  global rule — without its own rule the strip packs `^C` and `Hide` against
  the `+`, three controls in a row with empty ground to their right."""
  css = read_css()
  assert ".term-tabs .sp { margin-left: auto; }" in css


def test_the_drawer_can_be_resized():
  """`resize.js` carries `term: 240` with bounds of 120..700, but without a
  rendered handle the height is whatever the settings file happens to say,
  with no way to change it from the app. A terminal you cannot make taller is one you stop using the
  moment a command prints thirty lines."""
  js = (WEB / "js" / "terminal.js").read_text()
  assert 'divider("term"' in js
  handle = js.split('divider("term", {', 1)[1].split("}", 1)[0]
  assert 'axis: "y"' in handle and 'side: "top"' in handle, \
      "a bottom drawer grows upward"
  assert 'setPanelWidth("term"' in handle, "and it is stored like every other panel"

  resize = (WEB / "js" / "resize.js").read_text()
  assert "term: 240" in resize and "term: [120, 700]" in resize

  css = read_css()
  pane = css.split(".termpane {", 1)[1].split("}", 1)[0]
  assert "position: relative" in pane, "the handle is positioned against it"


def test_a_shell_says_where_it_is_and_what_it_is_running(tmp_path):
  """"shell 1, shell 2, shell 3" is a strip you have to click through to find
  the one you meant, which is the whole reason every other terminal labels its
  tabs. Two facts are enough to tell three shells apart: the directory it sits
  in, and the program it is running if it is running one."""
  import asyncio

  pty = LocalPty()
  if pty.unavailable():
    pytest.skip(pty.unavailable())

  async def go():
    handle = await pty.start(cwd=str(tmp_path))

    async def drain():
      async for _ in pty.read(handle):
        pass

    task = asyncio.create_task(drain())

    async def settle(check, tries=40):
      for _ in range(tries):
        if check(pty.where(handle)):
          return pty.where(handle)
        await asyncio.sleep(0.05)
      return pty.where(handle)

    # Not "any cwd": for the first instant after `pty.fork()` the child is
    # still standing where its parent was, and reading that is how a test
    # passes while telling you nothing.
    at_start = await settle(lambda w: w["cwd"] == str(tmp_path))
    await pty.write(handle, "cd /\n")
    moved = await settle(lambda w: w["cwd"] == "/")
    await pty.write(handle, "sleep 4\n")
    busy = await settle(lambda w: bool(w["busy"]))
    task.cancel()
    await pty.close(handle)
    return at_start, moved, busy, pty.where(handle)

  at_start, moved, busy, closed = asyncio.run(go())
  assert at_start["cwd"] == str(tmp_path), "where it was started"
  assert moved["cwd"] == "/", "and where it has walked to since"
  assert busy["busy"] == "sleep", "and what it is busy with"
  assert closed == {"cwd": "", "busy": ""}, \
      "a shell that is gone says nothing rather than something stale"


def test_a_command_that_prints_nothing_still_names_its_tab():
  """Output is the natural moment to look, and it covers `cd` (which prints a
  prompt) and a command finishing (which prints one too). It does not cover a
  command *starting*: `sleep 6` produces nothing at all, and at the instant its
  own echo arrives the shell has not forked it yet. So a line sent is a reason
  to look again in a moment — without that the tab never says `sleep`."""
  server = (_Path(__file__).resolve().parent.parent
            / "rlmcp_studio" / "routes" / "machine.py").read_text()
  route = server.split("async def ws_terminal(", 1)[1].split("\n  @app.", 1)[0]
  assert "async def look_after(" in route
  assert "look_after(0.4)" in route and 'kind == "in"' in route
  assert "len(looks) < 4" in route, "a bound, not a queue"
  # Two tasks write to one socket, so the sends have to be serialised.
  assert "sending = asyncio.Lock()" in route and "async with sending" in route


def test_the_tab_is_named_after_the_work_not_the_number():
  """The program if there is one, the directory if not, the number only when
  the platform cannot answer — and a home directory reads as `~`, the way a
  prompt writes it, rather than showing your login name as a project."""
  js = (WEB / "js" / "terminal.js").read_text()
  named = js.split("function named(shell, cwd, busy) {", 1)[1].split("\n}", 1)[0]
  assert "busy" in named and "leaf" in named and "shell ${shell.n}" in named
  assert 'tilde ? "~"' in named
  assert 'm.kind === "where"' in js

  css = read_css()
  name = css.split(".term-name {", 1)[1].split("}", 1)[0]
  assert "text-overflow: ellipsis" in name and "max-width" in name, \
      "a directory is as long as it likes; the strip is not"


# ── it is an emulator now, and that has consequences ─────────────────────
def test_the_emulator_loads_from_the_pinned_libraries():
  """xterm.js is not in the repository: vendor.py pins it, a wheel carries it,
  and a checkout fetches it into the cache. The client loads it from the
  studio's own server, never from a CDN, so an installed studio works offline."""
  from rlmcp_studio import vendor
  assert "xterm/xterm.js" in vendor.ENTRY_POINTS
  assert "xterm/addon-fit.js" in vendor.ENTRY_POINTS

  js = (WEB / "js" / "terminal.js").read_text()
  assert 'const VENDOR = "/static/vendor/xterm"' in js
  assert "${VENDOR}/xterm.js" in js and "${VENDOR}/xterm.css" in js \
      and "${VENDOR}/addon-fit.js" in js
  assert "cdn" not in js.lower() and "https://" not in js, \
      "nothing here may reach the network"

  # Loaded when a shell is asked for, not at boot: 300 KB on every page load
  # for a drawer most sessions never open is a page that starts slower for
  # everybody.
  assert "/static/vendor/xterm" not in (WEB / "index.html").read_text()


def test_the_shell_is_told_it_is_talking_to_a_real_terminal():
  """Claiming more than the renderer handles makes every tool emit sequences
  that arrive as rubbish; claiming less is as bad. xterm.js handles 256
  colours, and a shell told it has a lesser terminal than it does is one where
  `vim` picks the wrong palette and `less` picks the wrong keys."""
  local = (_Path(__file__).resolve().parent.parent
           / "rlmcp_studio" / "terminals" / "local.py").read_text()
  assert '"TERM": "xterm-256color"' in local


def test_hiding_the_drawer_does_not_kill_what_is_running_in_it():
  """A closed socket is a closed pty by the server's design, so a drawer that
  closed its sockets on hide would kill the `tail -f` you hid it to get out of
  the way of. Hiding a terminal is not quitting it."""
  main = read_js("main.js", "app/*.js")
  toggle = main.split("toggleTerminal: () => {", 1)[1].split("\n  },", 1)[0]
  assert "termConnect()" in toggle
  assert "termDisconnect" not in main, "there is nothing left to disconnect"

  js = (WEB / "js" / "terminal.js").read_text()
  assert "export function disconnect" not in js


def test_the_keyboard_goes_back_to_the_shell_after_a_repaint():
  """Focus has to survive working in the drawer. This app rebuilds its tree several times a minute, and
  a repaint that leaves the document focused on nothing leaves you typing into
  nothing — with Up scrolling the pane, because the pane was what had the
  keyboard.

  Restored only when focus has drifted to *nowhere*: somebody who has clicked
  into the agent's composer keeps it, or the drawer would steal the keyboard
  twice a second."""
  js = (WEB / "js" / "terminal.js").read_text()
  mount = js.split("function mount(shell, body) {", 1)[1].split("\n}", 1)[0]
  assert "document.activeElement" in mount and "document.body" in mount
  assert "shell.term.focus()" in mount

  # And the controls in the strip that act on the shell do not take it away in
  # the first place.
  strip = js.split("const strip = el(", 1)[1].split("\n  return el(", 1)[0]
  assert strip.count("onmousedown: (e) => e.preventDefault()") >= 2
  assert "focusSoon()" in strip


def test_the_emulator_loads_after_the_editor_has():
  """Monaco's loader leaves a global AMD `define` on the page. A UMD bundle
  that finds one registers itself as an anonymous module instead of setting
  `window.Terminal`, and the loader rejects it -- so the terminal opened after
  the code editor had loaded never appeared. The emulator's scripts have to
  run with `define` out of the way, and it has to come back afterwards."""
  js = read_js("terminal.js")
  loader = js.split("function library() {", 1)[1].split("\n}\n", 1)[0]
  assert "window.define" in loader, "xterm's UMD bundle must not see Monaco's define"
  hide = loader.index("window.define = undefined")
  assert hide < loader.index("document.head.append(core)"), "hidden before the core runs"
  assert loader.count("window.define = amd") >= 1, "and restored afterwards"
