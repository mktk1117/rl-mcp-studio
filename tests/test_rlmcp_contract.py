"""What the studio borrows from rl-mcp, pinned name by name.

This is the file that catches a module rename in rl-mcp (say
`rlmcp.records.graph` moving) before the app breaks: the studio imports rlmcp
and reads its shapes, so every one of those is a promise that can be broken
from another repository by a commit nobody here reviewed.

Two kinds of check live here, and both are cheap:

* **The borrowed surface exists** — every module, function and field the studio
  reaches for. A failure names the exact thing that moved.
* **Nothing was re-implemented** — the studio's own source is grepped for the
  handful of things it must never do (open `metrics.jsonl`, write to `inbox/`,
  invent verdict colours). That rule is the whole design and it decays quietly
  otherwise.

Run this against a new rl-mcp before upgrading. If it passes, the studio runs.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from tests.client_source import read_js


SOURCE = Path(__file__).resolve().parent.parent / "rlmcp_studio"


# ── the borrowed surface ─────────────────────────────────────────────────
def test_records_package_exports_what_the_studio_reads():
  from rlmcp.records import (Feedback, RunRecord, OWED_A_RESPONSE, open_store)

  assert callable(open_store)
  for field in ("id", "slug", "seq", "task", "stage", "date", "verdict",
                "parent", "session", "change", "metrics", "feedback",
                "hypothesis", "prediction", "outcome", "falsifier", "links",
                "assets", "weights"):
    assert field in RunRecord.__dataclass_fields__, f"RunRecord lost {field}"

  # The annotation writes these three by keyword; a rename would be silent.
  signature = inspect.signature(Feedback)
  for field in ("text", "kind", "author", "iteration", "artifacts", "response",
                "changed"):
    assert field in signature.parameters, f"Feedback lost {field}"
  assert OWED_A_RESPONSE, "the unanswered-feedback rule vanished"


def test_the_record_graph_is_built_by_rlmcp():
  """`rlmcp.records.graph` -- a rename of it empties the whole Records view."""
  from rlmcp.records import graph

  for name in ("build", "to_payload", "summarize", "headline_video", "Graph"):
    assert hasattr(graph, name), f"rlmcp.records.graph lost {name}"


def test_verdict_colours_come_from_rlmcp():
  from rlmcp.records.views import VERDICT_COLORS

  # The client asks for `var(--v-<verdict>)`, so the studio's CSS and rlmcp's
  # vocabulary have to agree on the set of verdicts, not just on the colours.
  assert {"best", "running", "validated", "falsified"} <= set(VERDICT_COLORS)


def test_media_store_can_hold_an_annotated_frame(store, tmp_path, make_frame):
  frame = make_frame(tmp_path / "shot.png")
  key = store.media.put("001", str(frame), "a caption", "annotations")
  assert store.media.get(key), "media.put returned a key media.get cannot resolve"
  assert store.media.exists(key)


def test_session_exposes_the_client_half(session_dir):
  from rlmcp.session import Session

  session = Session.open(session_dir)
  for name in ("call", "submit", "poll", "wait", "liveness_info", "status",
               "info", "metrics", "params", "artifact_path"):
    assert hasattr(session, name), f"Session lost {name}"
  assert session.liveness_info()["state"] in ("running", "stalled", "dead")
  assert session.status()["iteration"] == 40
  assert len(session.metrics()) == 5


def test_clips_and_posters_are_rlmcps_idea_of_a_clip():
  """The clip strip reads these; a signature change empties the panel."""
  from rlmcp.records import clips
  from rlmcp.records.poster import record_posters

  assert callable(record_posters)
  for name in ("from_record", "order", "iteration_of", "is_progress_clip",
               "caption_for"):
    assert hasattr(clips, name), f"rlmcp.records.clips lost {name}"


def test_progress_video_schedule_is_published_in_status(session_dir):
  """The clips card labels the *current* gap, which rlmcp publishes."""
  from rlmcp.session import Session

  status = Session.open(session_dir).status()
  assert "progress_video" in status


def test_the_live_3d_view_is_rlmcps_and_says_where_it_is():
  """The studio frames a viewer it does not own.

  The 3-D panel starts no viser server, picks no port and holds no scene: it
  calls `live_view` and reads the url out of `status.live_view`. Both halves
  are rl-mcp's, so both are pinned here. If either moves, the panel degrades
  to "this run's rlmcp does not publish a live view" -- honest, and completely
  silent about the fact that it is an rl-mcp change that broke it.
  """
  from rlmcp.core import live_view

  described = inspect.signature(live_view.LiveView.describe)
  assert described is not None
  fields = inspect.getsource(live_view.LiveView.describe)
  for key in ("running", "url", "host_url", "port", "watchers", "fps",
              "push_ms", "last_error"):
    assert f'"{key}"' in fields, f"status.live_view lost {key}"

  # The command itself, and the two arguments the panel sends.
  from rlmcp.core.controller import RlMcp

  assert hasattr(RlMcp, "cmd_live_view"), "rlmcp lost the live_view command"
  params = inspect.signature(RlMcp.cmd_live_view).parameters
  for arg in ("enabled", "port", "host"):
    assert arg in params, f"live_view lost {arg}"
  # The panel frames `url`, which has to be a browser-usable origin rather than
  # the 0.0.0.0 the server binds.
  assert "localhost" in inspect.getsource(live_view.LiveView.url.fget)


# ── the rule: never re-implement rlmcp ───────────────────────────────────
def _studio_sources() -> list[Path]:
  return sorted(SOURCE.rglob("*.py"))


def _lines_of_code(path: Path):
  """Source lines with comments and docstring bodies left out.

  The prose in this repository talks about `metrics.jsonl` constantly -- to say
  that it is never opened. A rule that cannot tell a sentence from a call would
  fire on its own explanation.
  """
  in_doc = False
  for number, line in enumerate(path.read_text().splitlines(), 1):
    ticks = line.count('"""')
    if in_doc:
      in_doc = ticks % 2 == 0
      continue
    if ticks:
      in_doc = ticks % 2 == 1
      continue
    if line.strip().startswith("#"):
      continue
    yield number, line


#: The watcher names rlmcp's files because it watches them for changes. It
#: never opens one -- that is the distinction this rule is about.
WATCHES_BY_NAME = {"watcher.py"}


def test_metrics_are_read_through_the_session():
  offenders = []
  for path in _studio_sources():
    if path.name in WATCHES_BY_NAME:
      continue
    for number, line in _lines_of_code(path):
      if "metrics.jsonl" in line or "status.json" in line:
        offenders.append(f"{path.name}:{number}: {line.strip()}")
  assert not offenders, (
      "read a run's numbers through rlmcp.session.Session, never off disk:\n"
      + "\n".join(offenders))


def test_commands_go_through_session_call():
  """The protocol is rlmcp's. Writing an inbox request by hand races it."""
  offenders = []
  for path in _studio_sources():
    for number, line in _lines_of_code(path):
      if ("inbox" in line or "outbox" in line) and "Session" not in line:
        offenders.append(f"{path.name}:{number}: {line.strip()}")
  assert not offenders, "steer with Session.call:\n" + "\n".join(offenders)

  control = (SOURCE / "sources/control.py").read_text()
  assert "session.call(" in control, \
      "sources/control.py stopped using Session.call -- what replaced it?"


def test_the_studio_does_not_keep_its_own_verdict_palette():
  """One palette, rlmcp's. A second one drifts and then lies."""
  from rlmcp.records.views import VERDICT_COLORS

  colours = {c.lower() for c in VERDICT_COLORS.values() if isinstance(c, str)}
  offenders = []
  for path in _studio_sources():
    for number, line in enumerate(path.read_text().splitlines(), 1):
      for colour in colours:
        if colour in line.lower():
          offenders.append(f"{path.name}:{number}")
  assert not offenders, (
      "a verdict colour is hard-coded in the studio; read VERDICT_COLORS "
      f"instead: {offenders}")


def test_the_studio_reports_what_the_installed_rlmcp_can_do():
  """A panel that explains an absence has to name the real reason.

  A fixed "Needs Extension.selectors" is misleading on a machine whose rl-mcp
  already has it: there the missing piece is the *task's* extension, not the
  library. Asked of the library, never assumed from a version, because rl-mcp
  is often installed from a working checkout that may be on any branch.
  """
  from rlmcp_studio.app import _rlmcp_capabilities

  caps = _rlmcp_capabilities()
  assert set(caps) == {"selectors", "task_registry", "task_registry_reason"}
  assert isinstance(caps["selectors"], bool)

  from rlmcp.core.extensions import Extension
  assert caps["selectors"] == hasattr(Extension, "selectors")

  # Whether a task can exist here before its first run, which is `rlmcp tasks`.
  # An rl-mcp without it leaves the switcher records-only, and the
  # reason is carried so the panel can say which of the two it is.
  assert isinstance(caps["task_registry"], bool)
  assert caps["task_registry"] is not bool(caps["task_registry_reason"])


def test_the_close_out_control_takes_its_verdicts_from_rlmcp():
  """The same rule as the palette, for the words themselves.

  The close-out offers a list of verdicts to press. A list written into the
  client drifts from the store's, and each difference is either a verdict the
  GUI cannot reach or one that can only ever be refused -- so the words travel
  with `/api/config`, from `rlmcp.records`.

  Not a ban on the strings: `ui.js` names them to build a CSS variable and
  `agent.js` borrows them as marks on a tool call. What must not exist is a
  *vocabulary* -- the set, written here, offered as choices.
  """
  from rlmcp.records import TERMINAL_VERDICTS

  components = read_js("components/close.js")
  card = components.split("export function closeCard(", 1)[1].split("\n}", 1)[0]
  assert "config" in card and "verdicts" in card, \
      "the control reads the vocabulary the server sent"
  spelled = {v for v in TERMINAL_VERDICTS if f'"{v}"' in card}
  assert spelled <= {"interrupted"}, (
      "these verdicts are written into the close-out control rather than read "
      f"from rl-mcp: {sorted(spelled)}")


def test_closing_a_run_is_rlmcps_own_close_out():
  """The studio must not decide what a verdict means.

  `rlmcp record close` gathers the run's evidence, folds the feedback said to
  the live trainer into the record, evaluates the pre-registered falsifier and
  refuses claims the record has not earned. Re-implementing any of that here
  would be the second opinion rule zero exists to prevent -- so the close-out
  is rl-mcp's command, driven the way `sources/gates.py` drives `rlmcp check`.
  """
  closeout = (SOURCE / "sources/closeout.py").read_text()
  assert '"record", "--records-root"' in closeout and '"close"' in closeout, \
      "the close-out is `rlmcp record close`, not a write from here"
  for forbidden in ("gather_evidence", "check_verdict_change", "write_report",
                    "falsifier.check", "verdict ="):
    assert forbidden not in closeout, (
        f"{forbidden} is part of what closing a run *means*, and that belongs "
        "to rl-mcp")
