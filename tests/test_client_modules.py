"""The client, checked without a JavaScript engine.

The client has no build step and the suite needs no Node toolchain, so the
browser modules are not unit-tested. What *can* be checked from here is the structure that breaks
silently: a module importing a name another module stopped exporting, a path
that no longer exists, an event kind the server renamed. All three show up in a
browser as a blank panel and nothing in the log worth reading, which is the
worst kind of failure to own.

It has a second job. The reconciler (`patch()` in `ui.js`) makes three things
fail *silently* -- a listener on a discarded node, a handler reading a detached
one, a paint that throws and stops every later paint -- so the rules that
prevent them are held here too, with a test each.

This is a static reader over the source. It is not a parser and does not try to
be one -- it reads import and export lines, which are one-per-line by
convention in this client, and matches the shapes of the traps rather than
parsing JavaScript.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.client_source import read_css, read_js

WEB = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web"
JS = WEB / "js"
SERVER = Path(__file__).resolve().parent.parent / "rlmcp_studio"

IMPORT = re.compile(r'^import\s+(?:(\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?["\']([^"\']+)["\']',
                    re.M)
EXPORT_NAMED = re.compile(r'^export\s+(?:async\s+)?(?:function|const|let|class)\s+(\w+)', re.M)
EXPORT_LIST = re.compile(r'^export\s*\{([^}]*)\}', re.M)


def modules() -> list[Path]:
  return sorted(p for p in JS.rglob("*.js") if "vendor" not in p.parts)


def exports_of(path: Path) -> set[str]:
  text = path.read_text()
  names = set(EXPORT_NAMED.findall(text))
  for group in EXPORT_LIST.findall(text):
    for piece in group.split(","):
      piece = piece.strip()
      if not piece:
        continue
      # `export { agentPanel } from "./agent.js"` and `as` aliases.
      names.add(piece.split(" as ")[-1].strip())
  return names


def test_there_are_modules_to_check():
  assert len(modules()) >= 8, "the client lost its modules, or WEB moved"


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_every_import_resolves_to_a_file(module):
  for _, target in IMPORT.findall(module.read_text()):
    if not target.startswith("."):
      continue                       # bare specifiers are the vendored bundles
    resolved = (module.parent / target).resolve()
    assert resolved.is_file(), f"{module.name} imports {target}, which does not exist"


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_every_named_import_is_actually_exported(module):
  """The failure this catches: a module renames an export and its importers
  get `undefined` at call time, deep inside a repaint."""
  for names, target in IMPORT.findall(module.read_text()):
    if not target.startswith(".") or not names.startswith("{"):
      continue
    available = exports_of((module.parent / target).resolve())
    for piece in names.strip("{}").split(","):
      wanted = piece.strip().split(" as ")[0].strip()
      if not wanted:
        continue
      assert wanted in available, (
          f"{module.name} imports {{{wanted}}} from {target}, which exports "
          f"{sorted(available)}")


def test_the_page_loads_the_modules_that_exist():
  page = (WEB / "index.html").read_text()
  for reference in re.findall(r'(?:src|href)="([^"]+)"', page):
    if reference.startswith(("http", "//", "data:")):
      continue          # inline, so there is no file to find
    assert (WEB / reference.lstrip("/").replace("static/", "")).is_file(), \
        f"index.html references {reference}, which does not exist"


def test_the_layout_check_covers_every_view():
  """The other hand-written list, and the same failure: a view missing from it
  is a view whose layout faults nobody walks. The count in its summary is
  computed, so it cannot go stale on its own."""
  page = (WEB / "dev" / "checks.html").read_text()
  listed = re.search(r"const VIEWS = \[(.*?)\];", page, re.S)
  assert listed, "checks.html no longer declares a VIEWS list"
  walked = set(re.findall(r'"([a-z-]+)"', listed.group(1)))

  registry = re.search(r"export const VIEWS = \[(.*?)\n\];", read_js("views.js", "views/*.js"), re.S)
  real = set(re.findall(r'id:\s*"([a-z-]+)"', registry.group(1)))

  assert walked == real, (
      f"checks.html walks {sorted(walked)} but the app has {sorted(real)}")


def test_the_module_check_covers_every_module():
  """The list on that page is hand-written, and it is the only syntax gate
  when the suite runs without a JavaScript engine. A module missing from it is a module
  whose syntax error first appears as a blank app."""
  page = (WEB / "dev" / "modules.html").read_text()
  named = set(re.findall(r'"\.\./js/([^"]+)"', page))
  on_disk = {str(p.relative_to(JS)) for p in modules()}

  assert on_disk - named == set(), (
      "these modules are not in dev/modules.html, so nothing checks that they "
      f"parse: {sorted(on_disk - named)}")
  assert named - on_disk == set(), (
      f"dev/modules.html names modules that no longer exist: {sorted(named - on_disk)}")


def test_every_event_the_client_listens_for_is_one_the_server_sends():
  """The live spine's contract, and it spans two languages.

  A renamed event kind is invisible: the server publishes into the void and the
  client waits forever, with a socket that is connected and a UI that has
  simply stopped updating.
  """
  from rlmcp_studio.events import KINDS

  published = set(KINDS)
  # `agent.<kind>` is namespaced rather than enumerated: the provider decides
  # that vocabulary, so the client is allowed to handle any of them.
  main = read_js("main.js", "app/*.js")
  block = main[main.index("const HANDLERS"):main.index("function appendAgent")]
  handled = set(re.findall(r'^\s*"([a-z_.]+)"\s*:', block, re.M))

  orphans = {k for k in handled - published if not k.startswith("agent.")}
  assert not orphans, (
      f"the client handles events nothing publishes: {sorted(orphans)}")


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_python_docstrings_leaked_into_the_client(module):
  """`\"\"\"a note\"\"\"` is a comment in the server's language and a syntax error
  in this one — three string literals with no operator between them. It is an
  easy slip when switching between the two languages, and the browser's only
  complaint is that the module silently fails to load."""
  text = module.read_text()
  assert '"""' not in text, (
      f"{module.name} contains a Python-style docstring; JavaScript comments "
      "are // or /* */")


def strip_comments(text: str) -> str:
  """Source with `//` lines and `/* … */` blocks removed.

  A name inside a comment is not a call, and the prose in this client is
  dense enough that scanning it raw finds several.
  """
  import re as _re
  text = _re.sub(r"/\*.*?\*/", "", text, flags=_re.S)
  return "\n".join(line for line in text.splitlines()
                   if not line.lstrip().startswith("//"))


def _lines_of_code(path: Path):
  """Source lines with `//` comments and `/* … */` blocks left out."""
  in_block = False
  for number, line in enumerate(path.read_text().splitlines(), 1):
    if in_block:
      in_block = "*/" not in line
      continue
    if line.strip().startswith("//"):
      continue
    if "/*" in line and "*/" not in line:
      in_block = True
      continue
    yield number, line


def _balanced(text: str, start: int) -> str:
  """The attribute object starting at ``start``, nested braces included.

  A regex that stops at the first `}` stops inside `style: { width: … }`, and
  then reports a field that is perfectly fine.
  """
  depth = 0
  for i in range(start, min(len(text), start + 2000)):
    if text[i] == "{":
      depth += 1
    elif text[i] == "}":
      depth -= 1
      if depth == 0:
        return text[start:i + 1]
  return text[start:start + 2000]


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_every_text_field_keeps_its_text_in_the_store(module):
  """A field's text has to be recoverable from the store.

  `ui.js` keeps the node itself across a repaint, so ordinary typing survives
  without help — but a node is only kept while the structure around it holds,
  and switching view, run or task rebuilds that structure. The draft in the
  store is what carries the text across *those*; without it a one-time
  sign-in code can vanish between the paste and the button.

  Either shape counts: a `value:` rendered from the store, or an `input`
  listener that writes back to it.
  """
  text = module.read_text()
  offenders = []
  for match in re.finditer(r'el\("(input|textarea)"\s*,\s*\{', text):
    kind = match.group(1)
    attrs = _balanced(text, match.end() - 1)
    if 'type: "checkbox"' in attrs or 'type: "range"' in attrs:
      continue                       # no text to lose
    line = text[:match.start()].count("\n") + 1
    tail = text[match.start():match.start() + 1200]
    # Rendered from somewhere, and typing captured somewhere. Which helper
    # does the capturing is not this test's business -- `typed()`, `set()` and
    # a write into a pending edit are all fine.
    has_value = "value:" in attrs or ".value =" in tail
    captures = ("oninput" in attrs or 'addEventListener("input"' in tail
                or '"input"' in tail and "on(" in tail)
    if not (has_value and captures):
      offenders.append(f"{module.name}:{line} <{kind}>")
  assert not offenders, (
      "these fields would lose their text when the view around them is rebuilt: "
      + ", ".join(offenders))


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_loop_keywords_inside_a_callback(module):
  """`continue` inside `.map(name => { … })` is a syntax error, and a syntax
  error in one module is a blank page — the browser stops loading it and
  nothing says why.

  The suite runs without a JavaScript engine, and the pure-Python parser
  available rejects `?.` and `catch {`, so this checks the specific shape that
  is easy to write by mistake rather than pretending to be a parser. `web/dev/modules.html` is the real check: it imports every module in
  a browser and reports what failed.
  """
  offenders = []
  for number, line in _lines_of_code(module):
    stripped = line.strip()
    if not (stripped.startswith("continue") or stripped.startswith("break")):
      continue
    # Walk back to the nearest enclosing block opener that is a loop or a
    # switch; an arrow function in between means the keyword has no loop.
    head = module.read_text().splitlines()[:number - 1]
    depth = 0
    for previous in reversed(head):
      depth += previous.count("}") - previous.count("{")
      if depth < 0:
        if re.search(r"\b(for|while|switch)\b\s*\(", previous):
          break
        if "=>" in previous or re.search(r"\bfunction\b", previous):
          offenders.append(f"{module.name}:{number}: {stripped}")
          break
        depth = 0
  assert not offenders, (
      "`continue`/`break` with no enclosing loop — this is a syntax error: "
      + ", ".join(offenders))


# ── the renderer's own failure modes ─────────────────────────────────────
#
# These are read off the source rather than run, for the reason at the top of
# this file. They pin two things that each, on their own, would make the whole
# app stop responding while still looking alive: a click that sometimes does
# nothing.

def test_a_paint_that_throws_does_not_stop_every_later_paint():
  """Unguarded, one exception in one panel freezes the app for the session:
  data keeps arriving, the DOM stops changing, and nothing clicked does
  anything. A stale layout id in localStorage is enough to cause it."""
  text = read_js("main.js")
  assert "function paintOnce" in text, "the render body must be callable inside a guard"
  guarded = text.split("function paint(s) {", 1)[1].split("function paintOnce", 1)[0]
  assert "try {" in guarded and "catch" in guarded
  assert "paintOnce(s)" in guarded
  assert "showCrash" in guarded, "a frozen display has to say so, not go quiet"
  # And it has to take the notice away again when paints start working, or the
  # bar outlives the fault and becomes the lie it was meant to prevent.
  assert "crashed = false" in guarded and "remove()" in guarded


def test_an_unknown_layout_cannot_be_restored_or_stored():
  """`byId[layout].meta` throws on every paint when the id is not one of the
  three -- so the id is validated on the way in *and* on the way out."""
  state = (JS / "state.js").read_text()
  assert "LAYOUT_IDS" in state
  assert "LAYOUT_IDS.has(localStorage.getItem" in state, "restore is guarded"
  setter = state.split("export function setLayout", 1)[1][:200]
  assert "LAYOUT_IDS.has(layout)" in setter, "the setter refuses an unknown id"

  main = read_js("main.js", "app/*.js")
  assert "byId[s.layout].meta" not in main, "every lookup needs the fallback"


def test_no_listener_is_attached_where_the_reconciler_cannot_see_it():
  """`patch()` keeps the node already on screen and discards the freshly built
  one, so a handler attached with a bare `addEventListener` rides on the copy
  that is thrown away. A concrete case: a "no frame yet" placeholder morphed
  into the frame stage keeps the placeholder's node, which has never been
  through `frameStage`, so the annotation drag on it would be dead. Handlers go through
  `el`'s `on*` props or `on()`, both of which record them for the morph."""
  allowed = ("window.addEventListener", "document.addEventListener",
             "socket.addEventListener", "node.addEventListener")
  offenders = []
  for path in modules():
    if path.name == "ui.js":
      continue                       # where `on()` and `el()` legitimately do it
    for number, line in enumerate(path.read_text().splitlines(), 1):
      if ".addEventListener(" in line and not any(a in line for a in allowed):
        offenders.append(f"{path.name}:{number}: {line.strip()}")
  assert not offenders, "use on(node, type, fn) so a kept node keeps the handler:\n" + "\n".join(offenders)


def test_an_iframe_is_never_re_parented_by_a_repaint():
  """Moving an <iframe> in the DOM reloads the document inside it.

  That makes the trick the video player uses -- keep one node, append it into
  each fresh render -- exactly wrong here: `patch()` builds its children in a
  *detached* staging node, so appending the frame takes it out of the document
  and putting it back reloads it. For the 3-D view that means a dropped
  websocket, a rebuilt scene and the camera you just placed, several times a
  minute, on a panel that otherwise looks fine.

  The shape that works: a host marked `data-keep-children`, which the
  reconciler morphs without touching its contents, and later paints handing
  over an empty host that is discarded in favour of the one on screen. So
  every <iframe> this client builds has to be built beside that attribute.
  """
  offenders = []
  for path in modules():
    text = path.read_text()
    for number, line in enumerate(text.splitlines(), 1):
      if 'el("iframe"' not in line:
        continue
      # The host is built in the same function; a hundred lines is far more
      # than any of them, and far less than the file.
      window = "\n".join(text.splitlines()[max(0, number - 30):number + 30])
      if "keep-children" not in window:
        offenders.append(f"{path.name}:{number}")
  assert not offenders, (
      "an <iframe> must sit inside a data-keep-children host, or every repaint "
      f"reloads it: {offenders}")


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_no_handler_reads_the_node_it_closed_over(module):
  """The closure that ends up running belongs to the copy `patch()` discarded.

  `on(box, "input", () => { draft = box.value })` therefore reads a detached
  input nobody typed into, and the draft is the empty string forever. Every
  draft in the app breaks this way at once, silently -- and so does a read
  like `svg.querySelector(...).getBoundingClientRect()`, which is why this
  looks at *any* read of the captured node rather than a list of property
  names.
  Read `e.currentTarget`, or read the store.
  """
  text = module.read_text()
  offenders = []
  for match in re.finditer(r'on\((\w+),\s*"\w+",\s*(?:\([^)]*\)|\w+)\s*=>\s*', text):
    node = match.group(1)
    body = _handler_body(text, match.end())
    if re.search(rf'\b{re.escape(node)}\.\w+', body):
      line = text[:match.start()].count(chr(10)) + 1
      offenders.append(f"{module.name}:{line}")
  assert not offenders, ("these read the discarded copy of their own node: "
                         + ", ".join(offenders))


def _handler_body(text: str, start: int) -> str:
  """The arrow function's body: braced, or the single expression after `=>`."""
  if text[start] == "{":
    depth = 0
    for i in range(start, len(text)):
      if text[i] == "{":
        depth += 1
      elif text[i] == "}":
        depth -= 1
        if depth == 0:
          return text[start:i + 1]
    return text[start:]
  return text[start:text.find("\n", start)]


def test_the_view_ids_the_store_accepts_are_the_views_that_exist():
  """`state.js` guards the id, `views.js` defines it, and the two modules
  deliberately do not import each other -- so this is what keeps them together.
  An id outside the set would leave the tab strip pointing at one panel while
  another is on screen, and report success."""
  registry = read_js("views.js").split("export const VIEWS = [", 1)[1]
  registry = registry.split("\n];", 1)[0]
  ids = set(re.findall(r'\{\s*id:\s*"(\w+)"', registry))
  guarded = (JS / "state.js").read_text().split("VIEW_IDS = new Set(", 1)[1].split(")", 1)[0]

  assert ids, "the view registry should not be empty"
  assert ids == set(re.findall(r'"(\w+)"', guarded))


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_nothing_appends_a_value_that_might_not_be_there(module):
  """`Node.append(null)` inserts the *text* "null". A bare `null` in the
  sidebar or above a banner reads as a data bug rather than a DOM one. `el()` filters its children;
  anything appended afterwards goes through `add()` from `ui.js`.

  Only the shape that can be seen statically is checked -- an argument that is
  literally `null`, or a ternary with a `null` branch. A helper that *returns*
  null is invisible from here, which is why `add()` exists at all.
  """
  text = module.read_text()
  if module.name == "ui.js":
    return                          # where `add()` and `el()` legitimately do it
  offenders = []
  for match in re.finditer(r'\.append\(', text):
    for arg in _arguments(text, match.end() - 1):
      if arg.strip() == "null" or (":" in arg and re.search(r':\s*null\s*$', arg.strip())):
        offenders.append(f"{module.name}:{text[:match.start()].count(chr(10)) + 1}")
        break
  assert not offenders, ("append() stringifies null -- use add() from ui.js: "
                         + ", ".join(offenders))


def _arguments(text: str, open_paren: int) -> list[str]:
  """The top-level arguments of a call, given the index of its `(`."""
  depth = 0
  args, current = [], []
  for i in range(open_paren, len(text)):
    char = text[i]
    if char in "([{":
      depth += 1
      if depth == 1:
        continue
    elif char in ")]}":
      depth -= 1
      if depth == 0:
        args.append("".join(current))
        return args
    if depth == 1 and char == ",":
      args.append("".join(current))
      current = []
    else:
      current.append(char)
  return args


def test_a_slash_command_switches_the_view_rather_than_asking_the_agent():
  """The Console prints `/metrics`, `/tb`, `/diff` under the composer, which
  reads as an invitation to type them -- so typing one must not send it to the
  agent as a question. Only a bare slash-word that names one of the nine views
  is intercepted; anything else, including a message that merely starts with a
  slash, still goes to the agent."""
  text = read_js("agent/composer.js")
  send = text.split("async function send(", 1)[1].split("\n}", 1)[0]
  assert "setView(" in send, "the composer has to be able to switch views"
  assert "state.view !== before" in send, "and only claim the message when it did"


def test_a_failed_write_keeps_what_was_typed():
  """A composer that clears the box before the write, and reports the failure
  somewhere nothing on screen renders, makes a remark that was never recorded
  look exactly like one that was, with the words gone. Clearing belongs after
  the write, in the action that knows it succeeded."""
  # The Feedback panel's own box is gone -- a remark is said to the agent now,
  # and the agent writes the ledger. The lesson outlived it: the *annotation*
  # composer still writes feedback, and it must not clear what was typed until
  # the write has actually been taken.
  main = read_js("main.js", "app/*.js")
  annotate = main.split("annotate: async", 1)[1].split("\n  },", 1)[0]
  before, after = annotate.split("await api.annotate", 1)
  assert 'annotNote: ""' not in before, \
      "the note must not be cleared before the write"
  assert 'annotNote: ""' in after, "and must be cleared once it succeeded"
  assert "catch" in annotate and "annotError" in annotate, \
      "and a failure has to say so where it was written"


def test_the_agent_panel_renders_the_errors_it_records():
  """Failures are written to `agentError`, so something has to display it."""
  agent = read_js("agent/composer.js")
  assert agent.count("agentError") >= 4
  assert "state.agentError" in agent.split("export function composer", 1)[1], \
      "shown where the message was sent from"


def test_the_sign_in_says_how_and_not_only_that():
  """Telling the user their sign-in has expired is no help without a way to
  fix it, and one button with one line of note is not much of a way. Three
  things: numbered steps, the command, and noticing a sign-in done
  elsewhere."""
  agent = read_js("agent/transcript.js", "agent/signin.js")

  box = agent.split("function signIn(status", 1)[1]
  assert "si-steps" in box and box.count("el(\"li\"") >= 5, \
      "numbered steps in both halves of the flow, not a bare button"
  assert "claude auth login" in box, "and the command, for anyone who wants it"
  assert "Check again" in box, "a sign-in done in a terminal has to be noticed"

  card = agent.split("auth_expired: (e) =>", 1)[1].split("done: () => null", 1)[0]
  assert "signIn(" in card, \
      "the card carries the sign-in itself; naming the problem is not enough"


def test_a_parameter_edit_is_checked_before_it_reaches_the_trainer():
  """Unchecked, `abc` would be accepted for a parameter declared `float` and
  sent to the run, and `999999` would go with `-10.00 … 10.00` printed beside
  it: the declared type and range would be decoration. And the check has to
  run *as you type*, which means the row repaints on input — a value mutated
  in place with no repaint leaves the button and the note unchanged."""
  views = read_js("views/params.js")
  assert "function checkValue" in views
  assert "checkValue(spec," in views, "the editing row consults it"

  row = views.split("const input = el(\"input\", { class: \"pedit\"", 1)[1][:400]
  assert "set({ pendingEdit" in row, "typing has to repaint the row that judges it"
  assert "disabled: !!complaint" in views, "and Apply is off while it is wrong"


def test_a_small_number_is_not_rendered_as_zero():
  """A weight of -2.5e-7 at three decimals is "-0.000" — every digit gone, and
  that string would pre-fill the edit box. The rule lives in `fmt`, which
  every panel uses."""
  ui = (JS / "ui.js").read_text()
  body = ui.split("export function fmt(", 1)[1].split("\n}", 1)[0]
  assert "toExponential" in body, "small magnitudes keep their digits"
  assert "0.001" in body


def test_every_action_the_client_calls_is_one_the_app_publishes():
  """`actions.foo()` on an action that does not exist is a TypeError inside a
  click handler — the button looks live and throws. There is no type checker
  here, so this is the check: every name called through `actions`,
  `panelActions` or `window.__studioActions` has to be in the one object
  `app/actions-app.js` builds, from the tables it spreads into it."""
  source = read_js("app/actions*.js")
  spread = re.search(r"export const actions = \{([^}]*)\};", source)
  assert spread, "app/actions-app.js should build `actions` from its tables"
  published = set()
  for table in re.findall(r"\.\.\.(\w+)", spread.group(1)):
    block = source.split(f"const {table} = {{", 1)[1]
    depth, i = 1, 0
    while depth and i < len(block):
      if block[i] == "{":
        depth += 1
      elif block[i] == "}":
        depth -= 1
      i += 1
    published |= set(re.findall(r"^\s{2}(\w+):", block[:i], re.M))
  assert len(published) > 20, "the action table should not be nearly empty"

  called = set()
  for module in modules():
    text = module.read_text()
    called |= set(re.findall(r"\bactions\.(\w+)", text))
    called |= set(re.findall(r"panelActions\.(\w+)", text))
    called |= set(re.findall(r"__studioActions\.(\w+)", text))

  assert not (called - published), \
      f"called but never published: {sorted(called - published)}"


def test_every_path_the_client_fetches_is_a_route_the_app_serves():
  """A client calling a path the server does not have gets a 404 and a panel
  that quietly stays empty. The two lists live in different languages, so
  nothing but a test keeps them together."""
  server = "\n".join(path.read_text() for path in
                     [SERVER / "app.py", *sorted((SERVER / "routes").glob("*.py"))])
  routes = set()
  for method, path in re.findall(r'@app\.(get|post|put|delete)\("([^"]+)"', server):
    # `/api/run/{record_id}/clips` -> a shape the client's template matches
    routes.add(re.sub(r"\{[^}]+\}", "*", path))

  client = (JS / "api.js").read_text()
  called = set()
  for raw in re.findall(r'["\'`](/api/[^"\'`?]*)', client):
    called.add(re.sub(r"\$\{[^}]+\}", "*", raw).rstrip("/"))

  missing = {p for p in called if p not in routes}
  assert not missing, f"the client fetches paths the app does not serve: {sorted(missing)}"


def test_the_run_rail_can_be_narrowed():
  """Dozens of records in one task, in id order, with no way to ask for one by
  name, means reading the whole rail to find one. The filter
  matches the id, the slug and the headline — the three things anyone
  remembers about a run — and appears only when the list is long enough to
  need it."""
  components = read_js("components/rail.js")
  rail = components.split("export function runRail", 1)[1].split("\n}", 1)[0]
  assert "state.runFilter" in rail
  assert "r.line" in rail, "the headline is searchable, not just the slug"
  assert "runs.length > 8" in rail, "a short rail does not need a filter"

  main = read_js("app/actions-workspace.js")
  assert "typeRunFilter" in main and "setTimeout" in main.split("typeRunFilter", 1)[1][:300], \
      "debounced, like the other filters"


def test_the_dev_pages_exist_and_are_reachable():
  """Two pages carry the checks a test file cannot: `modules.html` imports
  every module the way the browser does (the only syntax gate, since the
  suite runs without a JavaScript engine), and `checks.html` loads the app at
  a laptop size and walks every view/layout combination looking for the
  faults that are invisible from a tall window. They are worth nothing if
  nobody finds them, so they link to each other."""
  dev = WEB / "dev"
  assert (dev / "modules.html").is_file()
  assert (dev / "checks.html").is_file()
  assert "checks.html" in (dev / "modules.html").read_text()
  assert "modules.html" in (dev / "checks.html").read_text()


def test_the_layout_tree_names_the_files_that_exist():
  """The tree in AGENTS.md is read as a map, and a map drifts as files move.
  Every path it draws has to be real — the reverse (every real file listed) is not the rule, since
  a map is allowed to leave out the streets nobody asks for."""
  import re as _re

  guide = (SERVER.parent / "AGENTS.md").read_text()
  tree = guide.split("## Layout", 1)[1].split("```", 2)[1]
  paths = _re.findall(r"^\s*([\w./*-]+/[\w./*-]+|\w+\.\w+|\w+/)\s{2,}", tree, _re.M)
  assert paths, "the tree should list something"

  # Indentation in the tree implies a parent, and reconstructing that would be
  # a parser. The question worth asking is simpler: does a file by this name
  # exist anywhere in the repo?
  root = SERVER.parent
  missing = []
  for path in paths:
    if "*" in path:
      continue                                  # a glob, not a file
    if (root / path).exists():
      continue
    name = path.rstrip("/").split("/")[-1]
    found = any(p.name == name for p in root.rglob(name)
                if ".git" not in p.parts and "vendor" not in p.parts)
    if not found:
      missing.append(path)
  assert not missing, f"the tree in AGENTS.md names paths that do not exist: {missing}"


def test_a_draft_does_not_follow_you_to_another_run():
  """A half-typed remark about run 048, and the kind picked for it, must not
  sit in the box on run 047 — over a card reading "nothing said about this run
  yet" — where pressing Add would write it there. Same for a question
  half-typed to the agent and for the error from the last failed write. Selecting a run clears all of
  them, in the store *and* in the kept input nodes, which a repaint alone
  would not empty."""
  main = read_js("app/load.js")
  select = main.split("async function selectRun(id)", 1)[1].split("\n}", 1)[0]
  for field in ("fbDraft", "fbKind", "fbHint", "draft", "agentError"):
    assert f"{field}:" in select, f"{field} follows you to the next run"
  assert ".fb-input, .agent-input" in select, "the live nodes are cleared too"


def test_a_permission_prompt_shows_the_whole_of_what_it_is_asking_about():
  """60 characters is a fine summary of a call that already happened, and no
  way at all to decide about one that has not: a long
  `cd … && python3 -c "…"` cut down to `cd /tmp/…` would offer Approve, Deny
  and "Always for this run" on the visible `cd` alone."""
  agent = read_js("agent/transcript.js")
  card = agent.split("function permissionCard", 1)[1].split("\n}", 1)[0]
  assert "argLines(e.args, { full: true })" in card

  args = agent.split("function argLines", 1)[1].split("\n}", 1)[0]
  assert "full" in args and "argfull" in args
  assert 'title: text.length > 60' in args, "and the short form keeps the rest in a tooltip"


def test_the_console_shows_what_its_own_verbs_produce():
  """The verb bar sits under every Console view, so its results have to appear
  in them: `Look` advances `state.lastFrame` and `Diagnose` fills
  `state.diagnosis`, and a stream that rendered neither would leave you
  pressing a button, getting a 200 back and seeing nothing at all -- the worst
  thing this app can do.

  The Console places `robotCard`, the one panel that holds the frames and the
  clips, so the check looks for them there. The second half of the rule -- no
  second copy of the verbs already in the composer bar -- is a flag, and
  deliberately: the body-only card carries the verbs, because in Workbench and
  Focus that body is the only thing on screen telling you to press Look. This
  is the one layout that has them elsewhere, so it is the one layout that says
  no.
  """
  console = (JS / "layouts" / "console.js").read_text()
  assert "robotCard(state, actions" in console, \
      "the frame and clips the verbs produce have to be in the stream"
  assert "withVerbs: false" in console, \
      "the Console's verbs are in its composer bar; a second set in the card "\
      "is two buttons for one act"

  components = read_js("components/robot.js")
  card = components[components.index("export function robotCard"):]
  card = card[:card.index("\n}")]
  for produced in ("framesCard(state, actions", "clipsCard(state, actions"):
    assert produced in card, f"the robot card stopped showing {produced}"
    assert "bodyOnly: true" in card, "a body-only card: this one owns the header"


def test_the_headline_card_is_filled_from_what_the_run_nominated():
  """A card titled "Headline metrics" that showed `Train/mean_episode_length`,
  which the run did not declare, while dropping `rlmcp/terrain_level_mean`,
  which it did, would be the one panel claiming to be the headline and not
  being it.
  Roles give the order; the run's own nomination decides which series fills
  each one."""
  components = read_js("components/metrics.js")
  rank = components.split("export function rankMetrics", 1)[1].split("\n}", 1)[0]
  assert "const declared = names.filter(n => nominated.has(n))" in rank
  assert "declared.length ? prefer(declared, test) : null" in rank, \
      "a declared series wins its role"
  assert "|| prefer(names, test)" in rank, "and a role with nothing declared still fills"


def test_a_menu_never_opens_past_the_bottom_of_the_window():
  """A tall menu opened from a bar at the bottom of a short window puts its
  last options *below the viewport*, with no page scroll to reach them — they
  cannot be clicked at all. Menus in a bottom bar open
  upward, and no menu is taller than the room it has."""
  css = read_css()
  rule = css.split(".menu {", 1)[1].split("}", 1)[0]
  assert "max-height" in rule and "vh" in rule, "clamped to the viewport"
  assert ".c-composer .menu" in css and "bottom: calc(100% + 7px)" in css, \
      "a menu in a bottom bar opens upward"


def test_a_frame_you_picked_says_it_is_pinned():
  """Clicking a thumbnail pins that frame — otherwise the next artifact would
  yank you off the one you are studying. But a header reading "Live frames"
  over a picture from thousands of iterations ago, with no caption and no way
  back, makes the panel look frozen rather than held."""
  components = read_js("components/frames.js")
  header = components.split('el("h3", { text: "Live frames" })', 1)[1][:700]
  assert "framePicked" in header and "pinned" in header
  assert "Back to live" in header

  main = read_js("app/actions-workspace.js")
  assert "liveFrames:" in main, "and an action that unpins it"


def test_the_console_shows_what_you_asked_for_rather_than_the_conversation():
  """Choosing `/params` from the hint row must not leave the table far above
  the scroll position while the stream follows the conversation at the bottom
  — that is "press a button, get a 200, see nothing" by another door. A view you chose, and a verb's
  answer, both win over what is arriving."""
  console = (JS / "layouts" / "console.js").read_text()
  box = console.split("function streamBox", 1)[1].split("\n}", 1)[0]
  assert "lastShown" in box and "scrollTop = 0" in box, "a new view starts at its top"
  assert "scrollIntoView" in box, "a verb's answer is scrolled to"
  assert "if (following)" in box, "and the conversation still follows otherwise"


def test_the_rail_filter_belongs_to_the_list_it_was_typed_against():
  """Carried across a task switch it would empty the new task's rail — and the
  box that clears it is only drawn for a task with more than eight runs, so on
  a smaller one there would be no way back short of a reload."""
  main = read_js("app/load.js")
  select = main.split("async function selectTask(task)", 1)[1].split("\n}", 1)[0]
  assert 'state.runFilter = ""' in select

  components = read_js("components/rail.js")
  rail = components.split("export function runRail", 1)[1].split("\n}", 1)[0]
  assert "runs.length > 8 || query" in rail, "a filter in force always has a box"


def test_the_scope_criteria_combine():
  """The menu offers terrain, level and holding as separate groups, each with
  its own selection dot, and the chip joins them with " · ". Both promise
  combination — so picking a second criterion must not silently drop the
  first by replacing the whole object."""
  components = read_js("components/frames.js")
  # The first `setWhere` in the file is the "all envs" row, which clears; the
  # criterion rows are the ones that have to *combine*.
  rows = components.split("const chosen = state.where && state.where[key] === value;", 1)[1][:700]
  assert "...(state.where || {})" in rows, "a criterion joins the others"
  assert "[key]: undefined" in rows, "and clicking it again clears just that one"

  main = read_js("app/actions-workspace.js")
  setter = main.split("setWhere: (where)", 1)[1][:500]
  assert "undefined" in setter and "null" in setter, "an emptied set means all envs"


def test_a_live_parameter_table_follows_the_run():
  """Read once per run and never again, the table would show the old value of
  a parameter changed by the agent, the CLI or another window — under a chip
  reading "live"."""
  views = read_js("views/params.js")
  load = views.split('loadOnce("params"', 1)[0][-600:]
  assert "state.live.state === \"running\"" in load, "the key follows a live run"
  assert "Re-read" in views, "and there is a way to ask again"


def test_an_agent_event_reaches_the_conversation_it_belongs_to():
  """`appendAgent` drops an envelope whose scope is not the conversation on
  screen. In Build there is no run — the subject is `task:<id>`, which is what
  `subject()` returns and what the server stamps on the envelope — so a scope
  built from `state.runId` would discard every entry of the Build view's
  agent as belonging to somebody else: its text, tool calls and permission
  cards would never reach the screen. The panel would sit on "working…" while
  the agent sat on an approval the screen never offered: a deadlock with no
  visible cause on either side.

  `state.js` states the rule ("the transcript, the composer and the permission
  cards all key on it"), and this comparison has to agree with it."""
  main = read_js("main.js", "app/*.js")
  append = main.split("function appendAgent(", 1)[1].split("\n}", 1)[0]
  assert "envelope.scope" in append, "the guard is still what routes an entry"
  assert "${subject()}#${state.thread}" in append, \
      "an agent event is routed by the subject, so Build's task conversation gets its own"
  assert "${state.runId}#" not in append, \
      "keying on the run id silently drops every entry of a task that has no run"


def test_a_message_the_agent_would_not_take_is_not_thrown_away():
  """The same rule as `test_a_failed_write_keeps_what_was_typed`, for the
  agent's box.

  The agent composer empties itself before the POST, so a send the server
  refuses loses the message. The refusal that matters is the ordinary one:
  `/api/agent/{run}/message` answers 409 "still working on the last message"
  whenever the agent is mid-turn — which is exactly when somebody types, because
  watching an agent go down a wrong path is what makes you reach for the box. A
  long correction must not vanish on Enter, leaving an error note and an empty
  box.

  Optimistic clearing is right; it just has to be undone when the write did
  not happen."""
  agent = read_js("agent/composer.js")
  send = agent.split("async function send(input)", 1)[1].split("\n}", 1)[0]
  catch = send.split("} catch", 1)[1]
  assert "state.draft = text" in catch, "a refused message goes back into the store"
  assert ".agent-input" in catch, "and into the box on screen, which a repaint alone would not refill"


def test_the_preview_offers_to_build_itself_once():
  """Two "Build and hold" buttons at once while the preview is off — one in the
  card header, one in the body — would read two different busy flags: the
  header's `busy === "start"`, the body's `s.buildBusy`, which is true for
  *any* preview request. So the pair could show different labels while
  sitting side by side, and pressing either one would do the same thing.

  A duplicate control is not cosmetic. Two buttons for one act mean two states
  to keep in agreement and two places for the next fix to miss, and the reader
  reasonably assumes that two buttons with the same words do two things.
  """
  code = "\n".join(line for path in (JS / "build.js", *sorted((JS / "builder").glob("*.js")))
                   for _, line in _lines_of_code(path))
  assert code.count('actions.preview("start")') == 1, (
      "the preview's build verb is wired in exactly one place — the card "
      "header, where Rebuild and Stop live in every other state. The body may "
      "name the button; it may not be a second one")


def test_press_look_is_written_where_there_is_a_look_to_press():
  """The empty-frame card reads "Press Look and the trainer renders one", so
  there has to be a Look on the screen in every layout.

  The robot card renders `framesCard` headless, so verbs kept only in its
  *header* would leave Workbench and Focus drawing the instruction with
  nothing to follow it with. Only the Console would have them, in its composer
  bar — the one layout where the sentence would be true.

  So the body carries them, and a layout that switches them off has to offer
  its own.
  """
  components = read_js("components.js", "components/*.js")
  frames = components.split("export function framesCard", 1)[1].split("\nexport ", 1)[0]
  assert "Press Look" in frames, "the sentence being checked is still here"
  body = frames.split("const body = el(", 1)[1]
  assert "verbs({" in body, (
      "the headless body — what the robot card draws in every layout — has to "
      "carry the verbs; the header it kept them in is not rendered there")

  for name in ("console.js", "focus.js", "workbench.js"):
    text = (JS / "layouts" / name).read_text()
    if "robotCard(" not in text:
      continue
    if "withVerbs: false" in text:
      assert re.search(r"\bverbs\(\s*\{", text), (
          f"{name} turns the robot card's verbs off and offers none of its own")


def test_launching_a_run_selects_it():
  """After launching 002, an app still holding 001 — the previous, failed run —
  in the rail, in the status bar and in Control leaves finding the new record
  to the reader.

  Pressing "Open a record and train" is the clearest statement of intent this
  app takes, and the one act after which what you want to look at is not in
  doubt. The record is opened *before* the trainer starts, so there is
  something to select the moment the call returns, and the launch selects the
  id it is handed back.
  """
  main = read_js("app/actions-build.js")
  launch = main.split("  launch: async () => {", 1)[1].split("\n  cancelLaunch:", 1)[0]
  assert "answer.record" in launch and "openRun" in launch, (
      "a launch that succeeded has to leave the app on the run it started")


def test_the_launch_card_says_what_code_the_run_executed():
  """Every launch is stamped with a git tree, and the runner runs from a copy
  of it -- or, told `in_place` or when the copy cannot be made, from a
  checkout that may have moved since. The job says which (`tree`,
  `materialized`, `workdir`, `note`). A launch card showing none of it would
  make two runs of "the same" task, one from the stamp and one from whatever
  the working tree held by then, look identical, and the runner's sentence
  about an rl-mcp too old to keep the stamp would reach nobody.

  So the card shown for a launched run draws them, and the note is shown
  whether or not the run was stamped (a run this studio did not start has
  only a note).
  """
  build = read_js("builder/launch.js")
  card = build.split("function launchCard(", 1)[1].split("\n}\n", 1)[0]
  live = card.split("if (live || (last && !s.launchOpen))", 1)[1].split("\n  }\n", 1)[0]
  assert "launchedFrom(job)" in live, "the card for a launched run says what it ran"
  shown = build.split("function launchedFrom(", 1)[1].split("\n}\n", 1)[0]
  for field in ("job.tree", "job.materialized", "job.workdir", "job.note"):
    assert field in shown, f"the launch card drops {field}"
  before_stamp = shown.split('if (!("tree" in job))', 1)[1].split("\n", 1)[0]
  assert "note" in before_stamp, "a job with no stamp at all still shows its note"


def test_a_working_agent_can_be_stopped_from_the_panel():
  """`POST /api/agent/{id}/stop` has `stop()` and `interrupt()` behind it, and
  the panel has to call it: otherwise a runaway agent that writes no files can
  only be ended with `curl` — from the terminal this whole app exists to
  remove.

  The control belongs in the composer row: that is where you are when you are
  watching an agent go the wrong way, and it is next to the Send that will not
  take your correction until the turn ends.
  """
  agent = read_js("agent/composer.js")
  composer = agent.split("export function composer(", 1)[1].split("\nasync function send", 1)[0]
  assert "stopAgent" in composer, "the composer offers a stop"
  assert "working" in composer, "and offers it only while there is something to stop"

  main = read_js("app/actions-app.js")
  stop = main.split("  stopAgent: async () => {", 1)[1].split("\n  },", 1)[0]
  assert "agentStop" in stop and "subject()" in stop, (
      "it stops the conversation on screen, which in Build is `task:<id>` and "
      "not a run id at all")

  api = (JS / "api.js").read_text()
  assert "/stop" in api, "and there is a call for it to make"


def test_the_tail_of_a_stopped_turn_does_not_look_like_work():
  """The client keeps its own copy of "is it working": every transcript entry
  that is not `idle` or `stopped` means the agent is busy. That is right for a
  live turn and wrong for the one you just ended — what the model had already
  produced arrives a second *after* the button, so without a window Send
  would turn back into Stop and the panel would say "working…" again, right
  after you stopped it.

  The server holds the same window (`AgentSession.stopping`); this is the half
  of it the browser owns, and it has to survive the refresh `stopAgent` ends
  with — a stopped turn reads as "not busy" from the moment it is asked for,
  so the server's answer cannot be what closes the window.
  """
  main = read_js("app/feed.js", "app/actions-app.js", "app/load.js")
  stop = main.split("  stopAgent: async () => {", 1)[1].split("\n  },", 1)[0]
  assert "agentStopping: true" in stop, "pressing stop opens the window"

  entry = main.split("const transcript = [...state.transcript, entry]", 1)[1]
  entry = entry.split("\n}", 1)[0]
  assert "agentStopping" in entry and "state.agentStopping" in entry, (
      "and an entry arriving inside it does not put the panel back to work")

  load = main.split("async function loadThread(", 1)[1].split("\n}", 1)[0]
  assert "agentStopping: agent.busy ? false : state.agentStopping" in load, (
      "a refresh mid-stop must not close the window the refresh cannot see")


def test_a_refresh_does_not_throw_away_what_the_socket_just_delivered():
  """The socket keeps delivering while `loadThread`'s fetch is in flight.
  `stopAgent` refreshes as its last act and the server publishes "You stopped
  it." a moment after the request returns — so a `loadThread` that replaced
  the transcript with the answer to its own fetch would fetch over the
  entries that say the stop happened: the pane would stop the agent and say
  nothing about it.
  """
  main = read_js("app/load.js")
  load = main.split("async function loadThread(", 1)[1].split("\n}", 1)[0]
  assert "state.transcript" in load and "filter" in load, (
      "entries newer than the fetch survive it")
  assert "seq" in load, "and newer means by `seq`, the conversation's own index"


def test_a_message_typed_while_the_agent_works_interrupts_it():
  """The other half of stopping one: you type *because* it is going wrong.

  Refusing Enter while a turn runs -- keeping the message and saying so -- is
  honest and still the wrong answer: what you typed is the correction, and
  "press Stop, then send" is two gestures and a wait for it. The composer
  sends with `interrupt`: the server ends the
  turn, waits for it to admit it ended, and sends the message into the
  conversation *after* the tail and the "You stopped it." -- the only order a
  reader can follow. Still not a queue: nothing waits for the turn to finish
  on its own.

  The panel mirrors `stopAgent` while that happens -- not busy, stopping --
  and shows the message as a ghost until the server echoes it, so pressing
  enter is never followed by an empty box and nothing on screen.
  """
  agent = read_js("agent/composer.js")
  send = agent.split("async function send(input)", 1)[1].split("\n}", 1)[0]
  assert "const interrupting = !!state.agentBusy" in send, \
      "a running turn changes how the message is sent, not whether"
  assert "agentSend(who, text, state.thread" in send and "interrupting)" in send, \
      "and the flag reaches the server"
  assert "agentStopping: interrupting ? true" in send, "the stop window opens, as Stop's does"
  assert "sending: interrupting ? text" in send, "the message is shown while it is on its way"
  assert "finally" in send and 'sending: ""' in send, "and the ghost goes when the request ends"
  composer = agent.split("export function composer(", 1)[1].split("\nasync function send", 1)[0]
  assert "agent-send interrupt" in composer, "the corner offers interrupt-and-send while working"
  assert "agent-send stop" in composer, "beside Stop, which still only stops"
  hint = agent.split('class: "hint-note"', 1)[1][:400]
  assert "interrupts" in hint, "the row says what enter does"
  api = (JS / "api.js").read_text()
  assert "interrupt" in api.split("agentSend:", 1)[1].split("\n\n", 1)[0]
  assert "interrupt" in api.split("buildAgent:", 1)[1].split("\n\n", 1)[0]


def test_the_provider_menu_is_the_servers_list():
  """There is more than one provider. The panel's chip is a menu over
  whatever the server lists, so a third provider needs no change in the browser -- and
  a provider without a sign-in flow gets a card that says how it authenticates
  rather than Claude's `claude auth login` steps."""
  agent = read_js("agent.js", "agent/*.js")
  assert "providerButton(status)" in agent.split("export function agentPanel", 1)[1][:1200]
  menu = agent.split("function providerButton(status)", 1)[1].split("\n}", 1)[0]
  assert "status.providers" in menu and "setAgentProvider" in menu
  card = agent.split("function signIn(status", 1)[1][:400]
  assert "providerCard" in card, "the Claude sign-in is not shown for another provider"
  # Neither provider's absence is a dead end: every not-ready card offers the
  # ones that are ready. "Install Claude Code and reload" alone, with another
  # provider a click away in the same menu, would be a dead end.
  signin = agent.split("function signIn(status", 1)[1].split("\nfunction codeInput", 1)[0]
  assert signin.count("switchTo(status)") >= 2, \
      "the CLI-missing card and the signed-out card both offer the other provider"
  provider_card = agent.split("function providerCard(status)", 1)[1].split("\n}", 1)[0]
  assert "switchTo(status)" in provider_card
  assert "--with google-antigravity" not in agent, \
      "the package is a dependency, not an install flag the card has to teach"
  main = read_js("main.js", "app/*.js")
  assert "setAgentProvider" in main
  settings = (JS / "settings.js").read_text()
  assert "agent.providers" in settings and "setAgentProvider" in settings

def test_every_layout_can_close_a_run():
  """Without a headline field and a verdict control, every run launched from
  the studio would stay `verdict: running` for ever — next to `trainer gone`
  in the status bar — and the only advice left would be to open a terminal.

  Every layout, not one: a run finishes in whichever layout you happened to be
  in, and "you cannot close it from here" is the same dead end three times.
  """
  for name in ("console.js", "focus.js", "workbench.js"):
    text = (JS / "layouts" / name).read_text()
    assert "closeCard(" in text, f"{name} cannot close a run"
  assert "rlmcp record close" not in (JS / "layouts" / "workbench.js").read_text(), \
      "the Record view does not send you to the CLI for this"


def test_a_refusal_to_close_is_shown_as_rl_mcp_wrote_it():
  """"'validated' is not available to a run warm-started from 041" teaches the
  rule the store is enforcing. Any sentence this client wrote instead would be
  the studio having an opinion about what a verdict means."""
  main = read_js("app/actions-workspace.js")
  close = main.split("  closeRun: async () => {", 1)[1].split("\n  },", 1)[0]
  assert "closeError: err.message" in close, "the refusal reaches the panel intact"
  components = read_js("components/rail.js", "components/close.js")
  card = components.split("export function closeCard(", 1)[1].split("\n}", 1)[0]
  assert "state.closeError" in card, "and the panel renders it"


def test_a_close_out_draft_belongs_to_the_run_it_was_typed_about():
  """The stale-draft trap, in the box where it would cost the most: a headline
  and a verdict typed about 002 must not be left sitting over 001, above a
  button that writes the record."""
  main = read_js("main.js", "app/load.js")
  select = main.split("async function selectRun(id)", 1)[1].split("\nasync function", 1)[0]
  for field in ("closeVerdict", "closeHeadline", "closeOutcome", "closeMetrics"):
    assert f'{field}: ""' in select, f"{field} follows you to the next run"
  assert ".close-out .fb-input" in select, \
      "the kept nodes are emptied too; a repaint alone would not do it"


def test_the_build_view_says_when_its_two_panels_disagree_about_the_code():
  """The gate strip and the preview both hold a moment, and an edit
  invalidates both without either noticing. After an edit the gates come back
  green on the new code while the preview is still stepping the old one — on
  the same screen, with nothing to say they are about different versions of
  the task. Only `Rebuild` reconciles them, and nothing asked for it.

  Three clocks: the preview knows when it started, a check knows when it ran,
  and the package's newest mtime arrives as `build.code.at`.
  """
  build = read_js("builder/gates.js", "builder/preview.js")
  assert "code" in build and "started_at" in build, "the preview is compared to the files"
  strip = build.split("function gateStrip(", 1)[1].split("\n}", 1)[0]
  assert "answer.at" in strip, "and so is the check's own answer"
  panel = build.split("function previewPanel(", 1)[1].split("\n}", 1)[0]
  assert "stale" in panel, "the preview panel says so where the picture is"


def test_a_failing_gate_can_be_handed_to_the_agent():
  """The traceback is on screen and the agent is in the next panel; copying
  text between the two by hand is the chore this removes."""
  build = read_js("builder/gates.js")

  assert "gateBrief(" in build, "no way to send a failing gate to the agent"
  brief = build[build.index("function gateBrief"):]
  brief = brief[:brief.index("\n}")]
  # What is sent is what is on screen: the gate's own words, not a summary.
  for carried in ("failed.detail", "failed.fix", "traceback"):
    assert carried in brief, f"the brief drops {carried}"


def test_an_agent_scope_is_never_compared_to_the_run_id():
  """A conversation's scope is `${subject()}#${thread}`, and `subject()` is the
  run id in nine views and `task:<name>` in Build. Comparing a scope to
  `state.runId` therefore works everywhere except the one view the comparison
  was written for, and fails silently: an `agent.idle` handler written that
  way would stop clearing `agentBusy` for build conversations, so the panel
  would stay "working" and refuse every message after the first."""
  source = read_js("main.js", "app/*.js")
  offenders = [line.strip() for line in source.splitlines()
               if "e.scope ===" in line and "subject()" not in line]
  assert not offenders, ("compare an agent scope to `subject()`, not to a run "
                         f"id: {offenders}")


def test_the_robot_panel_height_is_a_stored_preference():
  """Every width in this app is remembered per browser, and the panel whose
  *content* is the point has to be resizable too. A 3-D view
  squeezed into a strip is not a view of anything."""
  resize = (JS / "resize.js").read_text()
  components = read_js("components/robot.js")

  assert "robot:" in resize, "the robot panel needs a default and a range"
  assert 'divider("robot"' in components, "and a handle to drag"
  assert 'axis: "y"' in components, "which resizes a height, not a width"


def test_the_records_rail_holds_only_records():
  """A row reading "no record" inside a list *of* records reads as a record,
  and the rail is the one panel that has to be trustworthy about what exists.
  The sessions themselves are not dropped -- a run training with no record
  would be invisible -- they live in the bar, which is where this app states
  facts about the studio rather than about the experiment."""
  components = read_js("components.js", "components/*.js")
  # Just the function body: `runRail` is followed by helpers that are not
  # exported, so slicing to the next `export` swallows the very definition
  # this is checking is not *called* here.
  rail = components.split("export function runRail", 1)[1].split("\n}\n", 1)[0]

  assert "unrecordedList" not in rail and "unrecordedCell" not in rail, \
      "unrecorded sessions are back in the records rail"
  assert "conversationList" not in rail and "convs" not in rail, \
      "conversations are back in the records rail"

  # The panel that *shows* a conversation is the one that picks it.
  agent = read_js("agent.js", "agent/*.js")
  assert "function conversations()" in agent, \
      "the agent panel has to offer the conversations, since the rail does not"
  assert "openConversation" in agent, "and switching to one has to still work"

  bar = components.split("export function statusbar", 1)[1].split("\n}", 1)[0]
  assert "unrecordedCell" in bar, "then they are reachable from nowhere at all"
  assert "startAdopting" in components, "and adopting one has to still be possible"


def test_the_run_tab_says_run():
  """"Control" says *that you can act here* rather than what the tab is
  about. The id stays `control` — three layouts, the store and the
  checks page branch on it, and renaming that is churn nobody sees."""
  views = read_js("views.js")
  registry = views.split("export const VIEWS = [", 1)[1].split("\n];", 1)[0]
  row = [line for line in registry.splitlines() if 'id: "control"' in line]
  assert row and 'label: "Run"' in row[0], "the Control tab should read Run"


def test_the_settings_window_does_not_keep_its_own_list_of_what_needs_a_restart():
  """The server keeps one (`settings.SERVER_KEYS`) and its reply carries
  `restart_required`. A second copy in the browser is a rule that can disagree
  with itself, and this window's whole job is to be honest about when a
  setting takes effect."""
  settings = (JS / "settings.js").read_text()
  assert "restart_required" in read_js("main.js", "app/*.js"), \
      "the server's answer is what the window reports"
  # A *declaration*, not the word: the file names the server's constant in a
  # comment explaining why it does not copy it, which is the opposite of the
  # problem and should not fail this.
  import re as _re
  code = "\n".join(line for line in settings.splitlines()
                   if not line.lstrip().startswith(("//", "*", "/*")))
  assert not _re.search(r"(const|let|var)\s+\w*(RESTART|SERVER_KEYS)\w*\s*=", code), \
      "the browser should not hold a second copy of that rule"


def test_the_settings_textareas_set_value_as_a_property():
  """`el()` puts unknown keys on the node with `setAttribute`, and a
  `<textarea>` has no value attribute -- its content is its child text. Set
  that way, both boxes would come up empty over a file that plainly has roots
  in it."""
  settings = (JS / "settings.js").read_text()
  for name in ("recordsBox", "packagesBox"):
    assert f"{name}.value =" in settings, f"{name} has to be filled as a property"
  block = settings.split("function roots(", 1)[1].split("\n}", 1)[0]
  assert 'value:' not in block.split("el(\"textarea\"", 1)[1].split("});", 1)[0], \
      "a textarea's value attribute does nothing"


def test_code_says_which_file_count_it_can_actually_show():
  """`code.changed` is counted from the two trees; `code.files` is what
  survived the patch text being clipped. They disagree on a big diff, and a
  summary counting every changed file promises Compare buttons that are not
  there."""
  views = read_js("views/source.js")
  strip = views.split("function changedStrip(", 1)[1].split("\n}", 1)[0]
  assert "code.files" in strip and "clipped" in strip, \
      "the summary has to distinguish counted from shown"


def test_the_compare_button_appears_only_for_a_file_that_changed():
  """A Compare button that answers "identical" on every file in the package is
  a button nobody presses twice."""
  views = read_js("views/source.js")
  assert "function patchFor(" in views, "the pane asks whether this file changed"
  pane = views.split("function filePane(", 1)[1].split("\nfunction ", 1)[0]
  assert "patch\n        ? el(\"button\"" in pane or "patch\n      ? el(\"button\"" in pane, \
      "the button is conditional on there being a patch"
  assert "comparing ? null : editor.mount()" in pane, \
      "the editor is not mounted underneath the patch"


def test_the_default_thread_name_is_not_shown_when_it_is_the_only_one():
  """`main` names a thread -- a run holds several so a long investigation and
  a quick "what is this metric" do not share a context window -- and with
  exactly one open the word raises a question rather than answering one."""
  agent = read_js("agent/conversations.js")
  bar = agent.split("function threadBar(", 1)[1].split("\n}", 1)[0]
  assert 'names.size > 1' in bar.split('class: "th-name"')[0], \
      "the thread name should be conditional on there being more than one"

  row = agent.split("function conversationRow(", 1)[1].split("\n}", 1)[0]
  assert '!== "main"' in row, \
      "and a row for the default thread should not spell it out either"


def test_the_conversation_menu_says_when_and_how_long():
  """Rows told apart only by a small dot, in run order, make the latest
  conversation hard to find. The menu is sectioned by state, every idle row says
  how long ago it was last spoken to, a working row says how long it has been
  at it, and the newest is marked -- and the panel header says the same about
  the conversation that is open."""
  agent = read_js("agent.js", "agent/conversations.js")
  menu = agent.split("function conversations(", 1)[1].split("\n}", 1)[0]
  assert "SECTIONS" in menu and "conv-sec" in menu, "one heading per state"
  assert "latest" in menu, "the newest row is marked"
  row = agent.split("function conversationRow(", 1)[1].split("\n}", 1)[0]
  assert "ago(conv.last_at)" in row, "an idle row says when"
  assert "conv.since" in row and "forTime(" in row, "a working row says how long"
  head = agent.split("function hereState(", 1)[1].split("\n}", 1)[0]
  assert "agent-state" in head and "forTime(" in head and "ago(" in head, \
      "the header says it about the open conversation too"
  assert "setInterval(" in agent, "and the clocks move without an event"


def test_the_menu_claims_newest_first_only_when_the_server_says_when():
  """The page reloads this file while the process behind it runs until it is
  restarted, so the list can arrive from an older server: in run order, with
  no `last_at`. Read as "newest first", that would put a week-old 002 at the
  top wearing the `latest` mark and the day's conversations half-way down."""
  agent = read_js("agent/conversations.js")
  menu = agent.split("function conversations(", 1)[1].split("\n}", 1)[0]
  assert "const timed = rows.some(c => c.last_at)" in menu, \
      "whether any row carries a clock decides what the menu may claim"
  assert "const latest = timed ?" in menu, "no clock, no `latest` mark"
  assert '!timed ? "by run' in menu, \
      "and the idle section is named for the order it is actually in"


def test_a_tab_open_across_a_restart_signs_itself_back_in():
  """The token is minted per process and `/` hands it out as a cookie, so a
  tab open across a restart gets 401 on every fetch, a refused feed, and an
  empty pane for whatever it opens next -- with nothing saying why. A 401
  reloads the page, which is the sign-in; once per half-minute, because a
  401 that survives a reload is not a stale cookie."""
  api = (JS / "api.js").read_text()
  assert "function refused(status)" in api
  assert "status !== 401" in api, "only a refused token, not every error"
  assert "window.location.reload()" in api
  assert "sessionStorage" in api and "30000" in api, "and not in a loop"
  for fn in ("async function get(", "async function send("):
    body = api.split(fn, 1)[1].split("\n}", 1)[0]
    assert "refused(response.status)" in body, f"{fn} asks before it throws"
  close = api.split('addEventListener("close"', 1)[1].split("});", 1)[0]
  assert 'get("/api/config")' in close, \
      "a refused handshake looks like a dead server; the probe tells them apart"


def test_a_record_asset_is_asked_for_within_a_task():
  """A media key begins with the record id -- `002/videos/....mp4` -- and ids
  are per *store*. With several records roots, two stores can both hold that
  exact key, pointing at different files, and a route that returned whichever
  source came first would show another task's robot under this run's name."""
  text = (JS / "state.js").read_text()
  builder = text.split("recordAssetUrl", 1)[1].split("\n\n", 1)[0]
  assert "task=" in builder, "state.js builds an asset url with no task"

  server = (SERVER / "routes" / "run.py").read_text()
  route = server.split('def api_record_asset(', 1)[1].split("\n  @app", 1)[0]
  assert "library.find(record_id, task)" in route, \
      "the record has to be resolved inside the task before its store is used"


def test_there_is_one_video_panel():
  """A card of its own for kept clips beside the robot panel would be two video
  players on one screen, where which one you want depends on a distinction
  (`logs/` against the record) that neither header makes."""
  components = read_js("components.js", "components/*.js")
  assert '{ id: "kept"' in components, "kept is one of the robot panel's tenses"
  for layout in ("workbench.js", "focus.js"):
    text = (JS / "layouts" / layout).read_text()
    assert "recordVideo(" not in text, f"{layout} still draws a second video card"


def test_a_saved_panel_size_is_capped_by_the_window_it_lands_in():
  """`clamp` bounds a size against what is *sensible*; that is a different
  question from what will *fit*. A robot panel dragged tall on a big monitor
  is legal, and on a laptop it can be taller than the stage that holds it --
  so the video sits below the fold with nothing to say why.
  Applied at render and never written back: the size you chose is still yours
  and returns when the window is big enough for it."""
  resize = (JS / "resize.js").read_text()
  assert "export function fits(" in resize, "there has to be a viewport-aware cap"
  body = resize.split("export function fits(", 1)[1].split("\n}", 1)[0]
  assert "innerHeight" in body and "innerWidth" in body, \
      "the cap has to read the actual window"
  assert "saveWidth" not in body, "capping must not overwrite what was chosen"

  for module, name in (("components/robot.js", "robot"), ("terminal.js", "term")):
    text = (JS / module).read_text()
    assert f'fits("{name}"' in text, f"{module} applies a height without the cap"


def test_the_agent_column_default_scales_with_the_window():
  """A fixed width that is right on a laptop is a strip on a very wide
  monitor."""
  resize = (JS / "resize.js").read_text()
  assert "function agentDefault()" in resize
  body = resize.split("function agentDefault()", 1)[1].split("\n", 1)[0]
  assert "share(" in body, "a share of the window, bounded at both ends"
  share = resize.split("function share(", 1)[1].split("\n}", 1)[0]
  assert "innerWidth" in share and "Math.min" in share and "Math.max" in share, \
      "a share of the window, bounded at both ends"
  # The claims panel on Records > graph scales the same way.
  defaults = resize.split("export const DEFAULTS = {", 1)[1].split("\n};", 1)[0]
  assert "treeDetail: share(" in defaults


def test_a_sparkline_does_not_thicken_when_it_is_stretched():
  """`preserveAspectRatio="none"` is what lets a curve fill any card width and
  is also what distorts what is drawn in it: on a wide monitor a 420-unit
  viewBox scales about three times horizontally and once vertically, so a
  1.5px line renders at four and a circular marker becomes an ellipse."""
  ui = (JS / "ui.js").read_text()
  spark = ui.split("export function sparkline(", 1)[1].split("\n}", 1)[0]
  assert spark.count("non-scaling-stroke") >= 4, \
      "every stroked element has to opt out of the scaling"
  assert "<circle" not in spark, \
      "a radius scales with the transform; the marker has to be a stroke"


def test_axis_numbers_are_not_drawn_inside_the_stretched_viewbox():
  """A stroke can opt out of the scaling; a glyph cannot. Axis labels drawn as
  `<text>` would be pulled sideways with the curve, so they are HTML in
  gutters beside the plot instead."""
  ui = (JS / "ui.js").read_text()
  spark = ui.split("export function sparkline(", 1)[1].split("\nconst pct", 1)[0]
  svg = spark.split("const svg =", 1)[1].split("</svg>", 1)[0]
  assert "<text" not in svg, "a label inside the viewBox is stretched with it"
  assert 'class="ch-y"' in spark and 'class="ch-x"' in spark, \
      "the labels live in gutters outside the SVG"
  assert "function niceTicks(" in ui, \
      "ticks land on round numbers, not on slices of a padded range"
  css = read_css()
  assert ".chart-axed .ch-y" in css and ".chart-axed .ch-x" in css, \
      "the gutters need the CSS that positions them"


def test_the_parameter_history_opens_on_every_key_at_once():
  """"What moved" is a question about the set. One chart at a time means a
  click per parameter and no way to compare two, so the grid of small
  multiples is what the view opens with; a key zooms in."""
  views = read_js("views/params.js")
  assert "function historyGrid(" in views
  section = views.split("function historySection() {", 1)[1].split("\n}\n", 1)[0]
  assert "historyGrid(" in section, "the grid is what the section draws by default"
  assert 'shown.find(e => e.key === s.historyKey) ? s.historyKey : ""' in section, \
      "an unpicked key is the grid, not the first key in the index"
  state = (JS / "state.js").read_text()
  assert 'historyKey: ""' in state, "so a fresh visit lands on the grid"


def test_the_chrome_says_whether_it_is_training():
  """Whether the run is training is the first thing you want to know, so it
  belongs in the chrome rather than at the end of a row of metrics in the
  status bar. A fact about the process,
  never the record's verdict: those disagree routinely."""
  main = read_js("main.js", "app/*.js")
  assert "function trainingChip(" in main
  chip = main.split("function trainingChip(", 1)[1].split("\n}\n", 1)[0]
  assert "live.state" in chip or "live && live.ok" in chip, \
      "liveness comes from the session, not from the verdict"
  assert "verdict" not in chip, "the verdict is the one that lies about this"


def test_a_screenshot_goes_into_the_message_rather_than_straight_out():
  """The point of the picture is what you say about it. Sending it on capture
  would skip that, so it lands in the composer as an attachment and goes with
  the next message -- and the annotation tool is off by default, because most
  of the time the picture is the point and a drawing tool in the way is
  friction."""
  capture = (JS / "capture.js").read_text()
  agent = read_js("agent/composer.js")
  main = read_js("app/actions-app.js")

  assert "shotAnnotate: false" in main or "shotAnnotate: !annotating" in capture
  state = (JS / "state.js").read_text()
  assert "shotAnnotate: false" in state, "the box tool starts off"

  assert "attachShot" in main and "attached:" in main, \
      "attaching puts it in the composer"
  assert 'class: "attached"' in agent, "the composer shows what will be sent"

  send = agent.split("async function send(", 1)[1].split("\n}", 1)[0]
  assert "attached ? attached.url" in send, "and Send carries it"
  assert "attached })" in send, \
      "a failed send must put the picture back -- it cannot be retyped"


def test_a_cross_origin_frame_is_refused_rather_than_captured_blank():
  """The 3-D view is another document and a page cannot read its pixels. A
  blank rectangle with no explanation is the worst of the three outcomes."""
  capture = (JS / "capture.js").read_text()
  assert 'tag === "IFRAME"' in capture and "reason" in capture


@pytest.mark.parametrize("module", modules(), ids=lambda p: p.name)
def test_every_ui_helper_a_module_calls_is_one_it_imported(module):
  """A `ui.js` helper called without importing it throws on every paint. The
  guard catches it, so the app stays alive showing the *previous* tree -- a
  panel that will not open, with nothing on screen to say why. That is the
  quietest failure this client has, and a name check is cheap.

  Only `ui.js`'s exports are checked: they are the ones called bare, and a
  module-local helper of the same name would be found by the import scan
  below anyway."""
  import re as _re
  helpers = set(_re.findall(r"^export function (\w+)", (JS / "ui.js").read_text(), _re.M))
  text = module.read_text()
  if module.name == "ui.js":
    return

  imported = set()
  for names in _re.findall(r"import\s*\{([^}]*)\}\s*from", text):
    for piece in names.split(","):
      imported.add(piece.split(" as ")[0].strip())
  defined = set(_re.findall(r"^(?:export )?function (\w+)", text, _re.M))
  defined |= set(_re.findall(r"^(?:export )?const (\w+)\s*=", text, _re.M))

  called = set(_re.findall(r"(?<![.\w])(\w+)\s*\(", strip_comments(text)))
  missing = sorted((called & helpers) - imported - defined)
  assert not missing, (
      f"{module.name} calls {missing} from ui.js without importing them — "
      "this throws on paint and the guard hides it")


def test_a_record_card_opens_where_you_clicked_it():
  """A grid where clicking only *selects* makes you look somewhere else to
  find out what you selected. The open card plays its clip without being
  hovered and carries what the run claimed."""
  views = read_js("views.js", "views/*.js")
  card = views.split("function storyCard(", 1)[1].split("\nfunction ", 1)[0]
  assert "story-open" in card and "storyMore(node)" in card

  more = views.split("function storyMore(", 1)[1].split("\n}", 1)[0]
  for field in ("hypothesis", "prediction", "falsifier", "outcome"):
    assert field in more, f"an open card should say what it {field}"
  assert "<dl" not in more and 'el("dl"' not in more, \
      "a definition list inside a <button> is invalid markup"

  # A rebuild pauses a video, and a card that stops playing a second after you
  # click it reads as broken -- the hovered card has the same need.
  story = views.split("function storyView(", 1)[1].split("\n}", 1)[0]
  assert "picked" in story and "play()" in story, \
      "the open card has to be told to carry on after a repaint"


def test_no_text_field_is_left_open_to_a_writing_assistant():
  """Grammarly mounts its own overlay inside a text field and takes focus to do
  it. In an app that rebuilds its tree twice a second that reads as the box
  going dead a second after you click it -- and it cannot be reproduced in a
  browser without extensions.

  Set in `el()` rather than on each box, so the guarantee is that no field is
  missed, including the next one somebody adds."""
  ui = (JS / "ui.js").read_text()
  assert 'data-gramm' in ui, "the opt-out has to be somewhere"
  create = ui.split("export function el(", 1)[1].split("\n}", 1)[0]
  assert '"textarea"' in create and '"input"' in create, \
      "both kinds of field, and applied where every one of them is built"


def test_feedback_is_a_ledger_and_not_a_form():
  """A remark is said to the agent, which writes the record. Two places to
  write the same thing make two habits, and a box in this panel would leave
  the agent not knowing what had been said."""
  components = read_js("components.js", "components/*.js")
  card = components.split("export function feedbackCard(", 1)[1].split("\nexport function ", 1)[0]
  assert "ledger" in card, "the entries stay"
  for gone in ("fb-input", "fb-kind", "function submit"):
    assert gone not in card, f"the write path is still here: {gone}"
  assert "addFeedback" not in read_js("main.js", "app/*.js"), \
      "the action is dead once the box is gone"


def test_closing_a_run_is_still_possible_without_the_agent():
  """Read-only by default, because the agent writes the record. Not read-only
  *only*: closing is a decision rather than a comment, rl-mcp judges the
  verdict and can refuse it, and a signed-out agent would otherwise leave a run
  that cannot be closed at all."""
  components = read_js("components/close.js")
  card = components.split("export function closeCard(", 1)[1].split("\nexport function ", 1)[0]
  assert "Close it myself" in card, "the escape hatch has to exist"
  assert "closeOpen" in card, "and be shut by default"
  assert "Never mind" in card, "and have a way back out of it"


def test_a_run_of_tool_calls_is_one_box():
  """A card per call and a *second* card per result, not even adjacent once a
  turn interleaves them, buries the sentences between them. A continuous run
  of calls is one thing the agent did and renders as one folded box; a sentence or a thought breaks the run,
  because that is where the agent stopped and said something."""
  agent = read_js("agent/transcript.js")
  group = agent.split("function groupTurns(", 1)[1].split("\n}", 1)[0]
  assert "toolGroup(" in group, "a run of calls folds into one"
  assert "nodes.length > 1 ? toolGroup" in group, "one call is not a group"
  # A result or a decision between two calls is not a break: they are drawn
  # inside the call they belong to, so the run is still continuous.
  assert '"tool_result"' in group and '"decision"' in group

  fold = agent.split("function toolGroup(", 1)[1].split("\n}", 1)[0]
  assert 'open: failed ? "" : null' in fold, \
      "a failure opens it; folded away, a failure reads like a success"
  assert "waiting.label" in fold, \
      "and a folded group still says what is happening now"


def test_a_folded_group_does_not_open_itself_while_it_runs():
  """`ui.js` never takes `open` off a <details> again -- it is user state. A
  group that opened itself while running would therefore stay open for good,
  which is the folding undone for exactly the conversation being watched."""
  agent = read_js("agent.js", "agent/*.js")
  fold = agent.split("function toolGroup(", 1)[1].split("\n}", 1)[0]
  assert 'open: waiting' not in fold and 'waiting ? ""' not in fold


def test_a_tool_result_is_drawn_inside_the_call_it_answers():
  """A result drawn as a box of its own can land rows below the call that
  returned it. The result is matched by `tool_use_id` and drawn in
  its call; only an orphan -- a transcript trimmed at the front, or read back
  across a restart -- still stands on its own."""
  agent = read_js("agent/transcript.js")
  call = agent.split("  tool_call: (e, all) => {", 1)[1].split("\n  },", 1)[0]
  assert 'x.kind === "tool_result" && x.id === e.id' in call
  assert "answer ? toolResult(answer) : null" in call

  result = agent.split("  tool_result: (e, all) =>", 1)[1].split("\n  permission:", 1)[0]
  assert 'x.kind === "tool_call" && x.id === e.id' in result
  assert "? null" in result and "toolResult(e)" in result


def test_the_mode_menu_no_longer_promises_the_next_conversation():
  """The handler reads the mode per call and the open sessions are told as
  well, so a note saying it "applies to conversations started from now on"
  would be a promise in the wrong direction."""
  agent = read_js("agent.js", "agent/*.js")
  mode = agent.split("function modeButton(", 1)[1].split("\n}", 1)[0]
  assert "applies to conversations started from now on" not in mode
  assert "applies now" in mode


def test_switching_layout_in_the_bar_does_not_change_what_the_studio_opens_in():
  """If every switch wrote the settings file, the layout the studio *opens* in
  would be whichever one anybody clicked last — in any browser, including the
  ones agents drive. The bar switches for this visit; the picker in Settings, which says out
  loud that it is the opening layout, is the one that writes it down."""
  state = (JS / "state.js").read_text()
  setter = state.split("export function setLayout(", 1)[1].split("\n}", 1)[0]
  assert "remember = false" in setter, "a switch does not remember by default"
  body = setter.split("if (remember)", 1)
  assert len(body) == 2, "the write is behind the flag"
  assert "settingChanged" not in body[0], "nothing is written before the flag"
  assert "settingChanged" in body[1] and "localStorage.setItem" in body[1]

  settings = (JS / "settings.js").read_text()
  assert "setLayout(id, { remember: true })" in settings, \
      "the Settings picker is the control that remembers"

  main = read_js("main.js", "app/*.js")
  bar = main.split('menu("layout"', 1)[1].split("\n    el(\"button\"", 1)[0]
  assert "setLayout(l.meta.id)" in bar and "remember" not in bar, \
      "the bar switch stays a switch"


def test_a_settings_file_that_names_no_layout_opens_workbench():
  """An `applySettings` that walked only the keys the file *has* would make the
  default unreachable: once any browser had written `rlmcp.layout` into its
  own storage, removing the preference from the file would leave that browser
  opening in its last choice for ever — with no way back to the default short
  of clearing site data."""
  state = (JS / "state.js").read_text()
  assert 'export const DEFAULT_LAYOUT = "workbench"' in state, \
      "the default is a decision, named once"

  apply_fn = state.split("export function applySettings(", 1)[1].split("\n}", 1)[0]
  assert "LAYOUT_IDS.has(saved.layout)" in apply_fn and "DEFAULT_LAYOUT" in apply_fn, \
      "a file with no layout is answered by the default, not by this browser"


def test_choosing_a_conversation_does_not_move_the_run_rail():
  """If the panel followed the selection, watching a run and talking to an
  agent would be the same act: ask the agent to launch 049, click 049 to see
  the numbers, and the conversation you were in would be replaced by 049's
  empty one -- mid-answer. A conversation you opened or spoke into is pinned
  instead."""
  state = (JS / "state.js").read_text()
  subject = state.split("export function subject(", 1)[1].split("\n}", 1)[0]
  assert "convPin" in subject, "the pin outranks the view"
  assert "export function naturalSubject(" in state, \
      "and the view's own answer is still reachable, for the bar to compare"

  main = read_js("main.js", "app/*.js")
  opener = main.split("openConversation: async (run, thread) => {", 1)[1] \
               .split("\n  },", 1)[0]
  assert "selectRun" not in opener, "choosing who you talk to is not a selection"
  assert "convPin: run" in opener
  assert "followCurrentRun:" in main, "and there is a way back to the view"


def test_a_message_goes_to_the_conversation_on_screen():
  """Addressing by `state.runId` is the same trap from the sending end: with a
  conversation pinned to 046 while the rail shows 048, a typed message would
  go to 048's agent and appear in neither panel."""
  for name, text in (("main.js", read_js("main.js", "app/*.js")),
                     ("agent.js", read_js("agent.js", "agent/*.js"))):
    assert "api.agentSend(state.runId" not in text, \
        f"{name} still addresses the selected run"
    assert "api.agentSend(who" in text, f"{name} should send to the subject"


def test_no_panel_reads_liveness_off_the_verdict():
  """`verdict === "running"` is not "this run is training".

  A record claims `running` from the moment it opens until somebody closes it
  out, which is hours after the trainer stopped. A panel lighting its live dot
  from the word would pulse beside dead runs -- the failure the `live` flag on
  `/api/runs` and the heartbeat event exist to end.
  """
  guilty = {}
  for module in modules():
    for line in module.read_text().splitlines():
      # The dot and the chip are the two shapes that *say* live, and their
      # second argument is the one that lights them. Comparing the claim
      # against the process -- the `trainer` lines -- is the honest use and
      # the whole point of keeping both.
      if not re.search(r'\b(dot|chip)\(', line):
        continue
      if re.search(r'verdict\s*===\s*"running"', line):
        guilty.setdefault(str(module.relative_to(JS)), []).append(line.strip())

  assert not guilty, (
      "these panels decide liveness from the record's claim; use the `live` "
      f"flag the runs payload carries: {guilty}")


def test_the_run_rail_is_keyed_by_the_run():
  """The rail is drawn newest first, so a record landing is an insert at the
  *top*. With nothing to match rows by, `patch()` falls back to position:
  every node is rewritten to move the list down by one, and the row under the
  pointer becomes a different run between deciding to click it and clicking
  it.
  An agent writing the next record mid-session is the ordinary case here."""
  components = read_js("components/rail.js")
  rail = components.split("export function runRail", 1)[1].split("\n}\n", 1)[0]
  assert "key: run.id" in rail


def test_the_selected_run_is_brought_back_onto_the_rail():
  """A run is chosen in more places than the rail -- the record graph, a
  citation on a library card, a task switch selecting the newest for you -- and
  in a task with dozens of records the mark on the rail is the only thing saying which
  run the app is about. Same rules as the library's shelf: `nearest` so a
  visible row does not move, once per run so scrolling away sticks, and on a
  timeout because a hidden tab is handed no frames."""
  components = read_js("components.js", "components/*.js")
  body = components.split("function keepRunInView(", 1)[1].split("\n}", 1)[0]
  assert "scrollListTo(" in body
  assert "runId === inView" in body, "scrolled once per run, not once per paint"
  assert "requestAnimationFrame" not in body and "setTimeout(" in body


def test_a_list_scrolls_itself_and_nothing_else():
  """`scrollIntoView` scrolls every scrollable ancestor -- the rail, then the
  stage, then the document -- so bringing a row onto a rail would leave the
  panel beside it scrolled with nothing to say why, the kind of fault
  `checks.html` reports as a DIV running past its box. The helper moves one
  list, and the two lists that keep a selection in view both use it."""
  ui = (JS / "ui.js").read_text()
  body = ui.split("export function scrollListTo(", 1)[1].split("\n}", 1)[0]
  assert "scrollIntoView" not in body and "closest(listSelector)" in body
  for module, text in (("components", read_js("components.js", "components/*.js")),
                       ("library.js", read_js("library.js"))):
    assert "scrollListTo(" in text
    assert "scrollIntoView" not in text, f"{module} still moves every ancestor"


def test_one_module_owns_fetch_once_and_it_is_not_a_view():
  """A copy of `loadOnce` per view, to dodge an import cycle with views.js, is
  one more place per view to fix a paint bug, and copies drift by exactly the
  line that matters. `state.js` is where it lives: it is what the views read
  and write, every view already imports it, and it imports none of them, so
  there is no cycle to dodge."""
  assert "export function loadOnce(" in (JS / "state.js").read_text()
  assert "export function revalidate(" in (JS / "state.js").read_text()
  offenders = [p.name for p in modules()
               if p.name != "state.js"
               and re.search(r"^function (loadOnce|revalidate)\(", p.read_text(), re.M)]
  assert not offenders, f"import it from state.js instead: {offenders}"


def test_a_re_read_of_the_same_subject_keeps_what_is_on_screen():
  """Clearing the payload before every fetch is right when the subject changes
  and wrong for a re-read -- and a re-read is the ordinary case in Docs and
  Overview, where an agent rewrites the document you are reading and Overview
  polls every few seconds for as long as it is being written. The view would
  drop to its empty state once a poll. `key + "For"` is what tells
  the two apart: the stamp the payload on screen was fetched under."""
  body = (JS / "state.js").read_text().split("export function loadOnce(", 1)[1]
  body = body.split("\n}", 1)[0]
  assert 'state[key + "For"] === stamp' in body, "a re-read is recognised"
  assert "sameSubject ? {} : { [key]: null }" in body, "and keeps its payload"


def test_the_conversation_count_says_what_the_server_calls_them():
  """The menu header, the empty state and every name on the server say
  *conversations*, so the chip does too. "0 agents" reads as "no agent is
  available to you" rather than "you have not started one here yet", and
  sends somebody hunting a broken panel that is working."""
  agent = read_js("agent/conversations.js")
  button = agent.split("const button = el(", 1)[1].split("if (!open)", 1)[0]
  assert '"agents"' not in button, "the chip is not counting agents"
  assert '"conversation"' in button and '"conversations"' in button


def test_the_menu_says_where_a_vanished_conversation_list_went():
  """A notice about parked conversations printed to stdout reaches nobody in a
  detached studio, so the list would disappear with nothing in the app to
  explain it. The menu that lost the list is where it is said, and it
  is dismissed for good rather than every boot -- it is news once."""
  main = read_js("app/load.js")
  assert "retired: payload.retired" in main, "the payload's field is stored"
  agent = read_js("agent/conversations.js")
  body = agent.split("function retirementNotice(", 1)[1].split("\nfunction ", 1)[0]
  assert "state.retired" in body and "parked.path" in body, "the path is shown"
  assert "localStorage.setItem(PARKED_SEEN" in body, "dismissed for good"
  assert "catch" in body, "and a private window cannot break the menu"


def test_an_open_cards_clip_is_bounded_on_both_axes():
  """The open card spans the whole grid, and a clip taking that width at
  `height: auto` is drawn wider and taller than the pane, with the run's own
  claim pushed off the bottom of it. Both caps matter: a wide card is bounded by the column and a
  tall clip by its own height, and either one alone lets the other axis run."""
  css = read_css()
  clip = css.split(".story-open .story-clip {", 1)[1].split("}", 1)[0]
  assert "max-width" in clip and "max-height" in clip, "bounded both ways"
  assert "width: auto" in clip and "height: auto" in clip, "and keeps its ratio"


def test_plots_opens_on_the_trainers_own_scalars():
  """`published` is the handful a run nominates and it is often empty or stale
  early on, where the trainer's own log is already dozens of series deep -- so
  opening on it would ask the reader to switch before they could look at
  anything.
  Two places have to agree, or the segmented control lights the tab the view is
  not showing."""
  state = (JS / "state.js").read_text()
  assert re.search(r"plotSource:\s*\"logged\"", state), "the stored default"
  views = read_js("views/plots.js")
  body = views.split("function plotsView(", 1)[1].split("\n}", 1)[0]
  chosen = re.search(r"const source = ([^;]+);", body).group(1)
  assert '"published" ? "published" : "logged"' in chosen, \
      "and what an unset or unknown value falls back to"


def test_every_sized_panel_can_be_dragged():
  """A panel with a fixed width and no handle is a size nobody can fix. Every
  width `resize.js` knows about
  has to reach a divider somewhere in the client, or the size is a constant
  with a save file: it is written, persisted and never draggable."""
  resize = (JS / "resize.js").read_text()
  defaults = resize.split("export const DEFAULTS = {", 1)[1].split("\n};", 1)[0]
  names = re.findall(r"^\s*([a-zA-Z]+):", defaults, re.M)
  assert len(names) >= 14, names
  client = "\n".join(p.read_text() for p in JS.rglob("*.js"))
  undraggable = [n for n in names
                 if f'divider("{n}"' not in client and f'placed("{n}"' not in client]
  assert not undraggable, f"sized but never draggable: {undraggable}"
  # And a stylesheet rule stacking the columns still wins on a narrow window:
  # the sizes reach the grid through a custom property, never an inline
  # `grid-template-columns` that would override every media query.
  css = read_css()
  for cls in ("treewrap", "paramview", "f-body", "files", "docs", "lib", "overview", "build-body"):
    assert re.search(rf"\.{re.escape(cls)}\b[^}}]*var\(--side", css), f".{cls} does not read --side"


def test_a_thread_does_not_follow_you_to_another_run():
  """Start `t1127` on 002, click 041, and a panel carrying the thread over
  would open `041#t1127` -- a conversation that never existed -- with the
  real one a thread away and nothing saying so. A thread belongs to a
  subject: every place the subject changes asks `threadFor` rather than
  passing `state.thread` through."""
  main = read_js("main.js", "app/*.js")
  assert "function threadFor(" in main and "function openHere(" in main
  tail = re.split(r"\n(?:export )?async function ",
                  main.split("async function selectRun(", 1)[1], maxsplit=1)[0]
  assert "openHere(subject())" in tail and "state.thread)" not in tail
  follow = main.split("followCurrentRun: async", 1)[1].split("\n  },", 1)[0]
  assert "openHere(" in follow
  view = main.split("setView(id);", 1)[1].split("\n  },", 1)[0]
  assert "openHere(" in view, "Build's subject is the task"


def test_a_task_opens_on_the_run_that_is_training():
  """The newest record can be a retry that died in seconds while the run
  actually on the GPU sits a row below it with a pulse."""
  main = read_js("app/load.js")
  reload = main.split("async function reloadRuns(", 1)[1].split("\n}", 1)[0]
  assert "r.live === true" in reload, "the live one, not the newest"


def test_the_switcher_learns_a_new_task_without_a_reload():
  """A task that appears after the page loads has to reach the switcher as
  well as the rail; a switcher read once at boot would never show it."""
  main = read_js("app/feed.js", "app/load.js")
  assert "async function refreshTasks(" in main
  for event in ("records.changed", "liveness.changed"):
    handler = main.split(f'"{event}": ', 1)[1].split("\n  \"", 1)[0]
    assert "refreshTasks" in handler, event


def test_a_run_training_out_of_view_is_one_click_away():
  """A run recorded under another task -- a child of 041 written under a new
  task id, say -- would be invisible from where its parent is."""
  main = read_js("app/chrome.js")
  assert "function trainingElsewhere(" in main
  chrome = main.split("function chrome(s)", 1)[1].split("\n}", 1)[0]
  assert "trainingElsewhere(s)" in chrome, "drawn in the chrome, on every view"
  badge = main.split("function trainingElsewhere(", 1)[1].split("\n}", 1)[0]
  assert "t.task !== s.task" in badge and "r.id !== s.runId" in badge


def test_a_dragged_side_panel_leaves_the_stage_room():
  """An agent column dragged wide can take most of the window and leave the
  Plots view a narrow strip: header one word per line, filter box cut off,
  one chart per row."""
  resize = (JS / "resize.js").read_text()
  fits = resize.split("export function fits(", 1)[1].split("\n}", 1)[0]
  assert "STAGE_MIN" in fits and "window.innerWidth - RAIL_ALLOWANCE - STAGE_MIN" in fits
  assert "const STAGE_MIN = " in resize


def test_the_plots_page_reads_again_while_the_run_trains():
  """Event files read once and cached until Reload would leave the curves of a
  live run standing still under a status bar counting iterations."""
  views = read_js("views/plots.js")
  assert "TB_REFRESH_S" in views and "set({ tbStamp: null })" in views
  poll = views.split("const TB_REFRESH_S", 1)[1].split("}, 5000);", 1)[0]
  assert 's.live.state === "running"' in poll, "only while it is training"
  assert 's.view !== "plots"' in poll, "and only while the wall is on screen"


def test_the_published_tab_shows_every_series_it_counts():
  """A tab counting every published series over eight full-width cards, with
  no smoothing, no filter and no way to the rest, hides most of what it
  counts."""
  views = read_js("views.js", "views/*.js")
  plots = views.split("function plotsView(", 1)[1].split("\n}", 1)[0]
  assert "metricCards(series, 8" not in plots and "publishedView(series)" in plots
  pub = views.split("function publishedView(", 1)[1].split("\n}", 1)[0]
  assert "seriesHead(" in pub and "seriesGroups(" in pub and "headlineConfig(" in pub
  tb = views.split("function tensorboardView(", 1)[1].split("\n}", 1)[0]
  assert "seriesHead(" in tb and "seriesGroups(" in tb, "one renderer for both sources"
  css = read_css()
  assert "flex-wrap: wrap" in css.split(".tb-head {", 1)[1].split("}", 1)[0]


def test_a_saturated_gpu_pauses_the_pulses_and_says_so():
  """A trainer saturating the GPU starves the browser's compositor on the same
  machine: the Plots page blanks and re-rasterizes every few seconds, and the
  animated pulses make it far worse. The page cannot free the card; it stops asking it for sixty frames a
  second, and the chip says what to do."""
  main = read_js("app/load.js")
  assert "export function gpuStarved(" in main
  assert 'classList.toggle("gpu-starved"' in main.split("async function refreshGpu(", 1)[1].split("\n}", 1)[0]
  css = read_css()
  assert "body.gpu-starved .dot.live" in css and "animation: none" in css.split("body.gpu-starved", 1)[1].split("\n", 1)[0]
  comp = read_js("components/robot.js", "components/statusbar.js")
  assert "gpu-starve" in comp and "--disable-gpu-compositing" in comp, \
      "the remedy that keeps WebGL: acceleration off in settings takes the 3-D view with it"
  foot = comp.split("class: \"card-b viser-b\"", 1)[1].split("viser-foot", 1)[0]
  assert "webglAvailable()" in foot, "the 3-D card says so itself when WebGL is gone"


def test_the_provider_card_names_only_what_it_declares():
  """A module is strict, so reading a name nothing declared throws, and the
  card for a provider that is not ready fails to draw. `account` is the name
  a removed sign-in path left behind."""
  src = read_js("agent/signin.js")
  body = src.split("function providerCard(", 1)[1].split("\n}\n", 1)[0]
  body = re.sub(r"//[^\n]*", "", body)                  # comments
  body = re.sub(r"\"(?:[^\"\\\n]|\\.)*\"", '""', body)       # strings
  if re.search(r"\baccount\b(?!\s*:)", body):
    assert re.search(r"\b(const|let|var)\s+account\b|\baccount\s*[,)=]", body), \
        "providerCard reads `account` without declaring it"
