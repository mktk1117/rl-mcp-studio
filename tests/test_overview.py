"""The task's own definition: where it lives, what it claims, and who writes it.

The failure this view exists against is not a bug in anything -- it is that
"what is this task" had no answer in the app at all. So these tests are about
the two halves staying honest about which of them they are: an agent's prose,
and the numbers the studio measured. A panel that let a written sentence and a
measured value read the same way would be worse than no panel.

Three properties carry most of it:

* a report belongs **beside the package**, and a task with no package the
  studio can see has nowhere to put one -- which is said rather than invented;
* the code moving under a report is the one way this view is confidently
  wrong, so it is detected and stated;
* asking for a report opens a conversation **of its own**, because the whole
  promise of the button is that it does not queue behind the agent watching a
  run.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rlmcp_studio.sources import overview as source_overview
from rlmcp_studio.sources import starting as source_starting
from tests.client_source import read_js

JS = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js"


def scope(directory: Path, package: str = "go1_rough_walk") -> dict:
  return {"package": package, "dir": str(directory), "free": [str(directory)]}


def an_answer(**over) -> dict:
  """A `rlmcp check` answer in the shape `sources/gates.py` stores it."""
  answer = {
      "at": time.time(), "ok": True, "error": "", "passed": True,
      "gates": [], "will_train": True,
      "env": {"num_envs": 4, "step_dt": 0.005, "actions": 29,
              "max_episode_length_s": 20.0, "joints": 37},
      "rewards": [{"term": "track_linear_velocity", "mean": 0.8},
                  {"term": "action_rate_l2", "mean": -0.04}],
      "terminations": [{"term": "fell_over", "per_step": 0.002}],
  }
  answer.update(over)
  return {"task": "Rough-Walk-Go1", "running": False, "answer": answer, "age_s": 1.0}


# ── where it lives ───────────────────────────────────────────────────────
def test_the_report_lives_beside_the_package(tmp_path):
  """Beside the code it describes, because that is what makes it go stale --
  a definition kept somewhere else is a definition nobody updates."""
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  assert source_overview.report_path(scope(package)) == package / "TASK.md"


def test_a_sibling_task_in_one_package_gets_a_document_of_its_own(tmp_path):
  """One package, two tasks -- a flat variant and a rough one -- is the ordinary
  way to write a family, and one `TASK.md` between them would show the second
  task the first one's definition, title and all."""
  package = tmp_path / "go1_walk"
  package.mkdir()
  (package / "TASK.md").write_text("# Example-Walk-Flat-Go1\n\nFlat.\n")

  # The task that wrote it keeps it.
  assert (source_overview.report_path(scope(package), "Example-Walk-Flat-Go1")
          == package / "TASK.md")
  # Its sibling is not shown it, and is given somewhere of its own to write.
  assert (source_overview.report_path(scope(package), "Example-Walk-Rough-Go1")
          == package / "TASK.Example-Walk-Rough-Go1.md")


def test_the_shared_report_belongs_to_whoever_asks_until_it_names_a_task(tmp_path):
  """A one-task package's document is that task's whether or not its heading
  spells the id out; only a heading naming *another* task hands it over."""
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  (package / "TASK.md").write_text("# The rough-terrain walking task\n")
  assert (source_overview.report_path(scope(package), "Rough-Walk-Go1")
          == package / "TASK.md")
  # Unwritten, it is free: the first task to write one takes it.
  (package / "TASK.md").unlink()
  assert (source_overview.report_path(scope(package), "Whatever-Else-G1")
          == package / "TASK.md")


def test_a_sibling_keeps_its_media_apart(tmp_path):
  """Both tasks want a picture called `scene-at-spawn.png`; in one directory
  the second overwrites the first."""
  package = tmp_path / "go1_walk"
  (package / "media" / "Example-Walk-Rough-Go1").mkdir(parents=True)
  (package / "TASK.md").write_text("# Example-Walk-Flat-Go1\n")
  (package / "media" / "scene-at-spawn.png").write_bytes(b"flat")
  (package / "media" / "Example-Walk-Rough-Go1" / "scene-at-spawn.png"
   ).write_bytes(b"rough")

  flat = source_overview.report_path(scope(package), "Example-Walk-Flat-Go1")
  rough = source_overview.report_path(scope(package), "Example-Walk-Rough-Go1")
  assert source_overview.media_dir(flat) == package / "media"
  assert (source_overview.media_dir(rough)
          == package / "media" / "Example-Walk-Rough-Go1")
  assert [m["path"] for m in source_overview.media_beside(rough)] == [
      str(package / "media" / "Example-Walk-Rough-Go1" / "scene-at-spawn.png")]
  # And the ask sends the agent to the right one, and says why it is not TASK.md.
  said = source_overview.ask("Example-Walk-Rough-Go1", rough,
                             source_overview.describe(
                                 "Example-Walk-Rough-Go1", scope=scope(package),
                                 code={}, gates={}, row={}))
  assert str(package / "media" / "Example-Walk-Rough-Go1") in said
  assert "another task is registered from this same package" in said


def test_two_tasks_sharing_one_experiment_share_a_log_directory(tmp_path):
  """`rlmcp train` writes under `<log-root>/<experiment_name>/`, so a rough
  variant that reuses the flat task's RL config drops its checkpoints and its
  progress videos in with the flat task's -- and nothing fails to say so."""
  package = tmp_path / "go1_walk"
  package.mkdir()
  listing = [
      {"task": "Example-Walk-Flat-Go1", "package": "go1_walk",
       "experiment": "go1_walk"},
      {"task": "Example-Walk-Rough-Go1", "package": "go1_walk",
       "experiment": "go1_walk"},
      {"task": "Example-Open-Door", "package": "door", "experiment": "door_open"},
  ]
  described = source_overview.describe(
      "Example-Walk-Rough-Go1", scope=scope(package), code={}, gates={},
      row=listing[1], siblings=listing)
  assert (described["facts"]["sharing"]["with"]
          == ["Example-Walk-Flat-Go1"])
  assert {"label": "experiment", "value": "go1_walk",
          "from": "registry"} in described["facts"]["rows"]

  # A task with an experiment of its own says nothing.
  alone = source_overview.describe(
      "Example-Open-Door", scope=scope(package), code={}, gates={},
      row=listing[2], siblings=listing)
  assert alone["facts"]["sharing"]["with"] == []


def test_a_task_with_no_package_has_nowhere_to_put_a_report(tmp_path):
  """Inventing a directory would file a description of a task next to code
  that does not implement it."""
  assert source_overview.report_path({"package": "", "dir": "", "free": []}) is None

  described = source_overview.describe(
      "Rough-Walk-Go1", scope={"package": "", "dir": "", "free": []},
      code={}, gates={}, row={})
  assert described["writable"] is False
  assert "nothing here registers" in described["why_not"]


def test_a_task_the_backend_ships_is_not_called_unregistered(tmp_path):
  """`Mjlab-Velocity-Rough-Unitree-G1` is registered, by a package that is not
  in this repository. "Nothing registers it" is a sentence somebody would go
  and disprove in thirty seconds, and be right."""
  described = source_overview.describe(
      "Mjlab-Velocity-Rough-Unitree-G1",
      scope={"package": "mjlab.tasks.velocity", "dir": "", "free": []},
      code={}, gates={}, row={"package": "mjlab.tasks.velocity", "backend": "mjlab"})

  assert described["writable"] is False
  assert "mjlab.tasks.velocity" in described["why_not"]
  assert "nothing here registers" not in described["why_not"]


def test_a_report_that_exists_is_read_with_its_sections(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  (package / "TASK.md").write_text(
      "# Rough-Walk-Go1\n\n## The task\n\nWalk on it.\n\n## Rewards\n\n- one\n")

  described = source_overview.describe("Rough-Walk-Go1", scope=scope(package),
                                       code={}, gates={}, row={})
  assert described["report"]["exists"] is True
  assert set(described["report"]["sections"]) == {"task", "rewards"}
  assert "Observations" in described["missing"]


def test_a_section_written_under_another_name_still_counts(tmp_path):
  """Matched on the heading's own word, not its exact spelling: a checklist
  that refused "Observations (what the policy sees)" would be teaching people
  to write headings for the checker rather than for a reader."""
  found = source_overview.sections_in(
      "## Observations (what the policy sees)\n\n## The actions, in radians\n")
  assert "observations" in found and "actions" in found


# ── the one way it is confidently wrong ──────────────────────────────────
def test_a_report_older_than_the_code_says_so(tmp_path):
  written_at = time.time() - 600
  fresh = source_overview.freshness(
      written_at, {"at": written_at + 300, "file": "/x/go1_rough_walk/rewards.py"})
  assert fresh["stale"] is True and fresh["file"] == "rewards.py"
  assert fresh["by_s"] == pytest.approx(300, abs=1)


def test_a_report_written_after_the_last_edit_is_not_stale():
  now = time.time()
  assert source_overview.freshness(now, {"at": now - 60, "file": "a.py"})["stale"] is False
  # Nothing known about the code is not evidence that the report is current,
  # and it is not evidence that it is stale either.
  assert source_overview.freshness(now, {})["stale"] is False


# ── written, versus measured ─────────────────────────────────────────────
def test_the_facts_say_which_of_them_were_measured(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  facts = source_overview.facts("Rough-Walk-Go1", scope(package), an_answer(),
                                {"backend": "mjlab"})
  by_label = {row["label"]: row for row in facts["rows"]}

  assert by_label["actions"]["from"] == "check"
  assert by_label["package"]["from"] == "registry"
  assert by_label["episode length"]["value"] == "20 s"
  assert by_label["step dt"]["value"] == "0.005 s"
  assert facts["rewards"][0]["term"] == "track_linear_velocity"


def test_a_task_with_no_time_limit_is_readable(tmp_path):
  """`max_episode_length_s` is 5e10 for a task with no cap, and eleven digits
  in a 200px rail is a number nobody reads."""
  answer = an_answer()
  answer["answer"]["env"]["max_episode_length_s"] = 50000000000.0
  facts = source_overview.facts("Rough-Walk-Go1", scope(tmp_path), answer, {})
  value = next(r["value"] for r in facts["rows"] if r["label"] == "episode length")
  assert value == "5e+10 s"


def test_nothing_here_claims_an_observation_dimension(tmp_path):
  """`rlmcp check` reports the action count and not the observation one. A
  panel that quietly omitted it would read as "not interesting", so a missing
  capability is said out loud rather than left out."""
  facts = source_overview.facts("Rough-Walk-Go1", scope(tmp_path), an_answer(), {})
  assert not [row for row in facts["rows"] if "observation" in row["label"]]
  assert any("observation" in line for line in facts["not_measured"])


def test_with_no_check_there_are_no_measured_numbers(tmp_path):
  facts = source_overview.facts("Rough-Walk-Go1", scope(tmp_path), {}, {})
  assert facts["checked"] is False
  assert not [row for row in facts["rows"] if row["from"] == "check"]


# ── what the agent is asked ──────────────────────────────────────────────
def test_the_ask_names_the_file_and_every_section(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  described = source_overview.describe("Rough-Walk-Go1", scope=scope(package),
                                       code={}, gates=an_answer(), row={})
  ask = source_overview.ask("Rough-Walk-Go1", package / "TASK.md", described)

  assert str(package / "TASK.md") in ask
  for section in source_overview.SECTIONS:
    assert section["heading"] in ask, section["heading"]
  assert source_overview.MEDIA_DIR in ask
  # The line that keeps this from becoming a second run report.
  assert "not how any run of it went" in ask


def test_the_ask_quotes_what_was_measured_rather_than_asking_for_it_twice(tmp_path):
  described = source_overview.describe("Rough-Walk-Go1", scope=scope(tmp_path),
                                       code={}, gates=an_answer(), row={})
  ask = source_overview.ask("Rough-Walk-Go1", tmp_path / "TASK.md", described)
  assert "track_linear_velocity" in ask and "29" in ask


def test_with_no_check_the_ask_says_so_instead_of_quoting_nothing(tmp_path):
  described = source_overview.describe("Rough-Walk-Go1", scope=scope(tmp_path),
                                       code={}, gates={}, row={})
  ask = source_overview.ask("Rough-Walk-Go1", tmp_path / "TASK.md", described)
  assert "No `rlmcp check` has run" in ask


def test_a_second_ask_is_a_refresh_and_names_what_is_missing(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  report = package / "TASK.md"
  report.write_text("# Rough-Walk-Go1\n\n## The task\n\nWalk on it.\n")
  code = {"at": report.stat().st_mtime + 120, "file": str(package / "rewards.py")}

  described = source_overview.describe("Rough-Walk-Go1", scope=scope(package),
                                       code=code, gates={}, row={})
  ask = source_overview.ask("Rough-Walk-Go1", report, described, note="say more about the hands")

  assert "A report is already there" in ask
  assert "rewards.py" in ask
  assert "Observations" in ask.split("Sections it does not have yet")[1]
  assert "say more about the hands" in ask


def test_the_person_s_own_words_are_carried_when_there_is_no_report(tmp_path):
  described = source_overview.describe("Rough-Walk-Go1", scope=scope(tmp_path),
                                       code={}, gates={}, row={})
  ask = source_overview.ask("Rough-Walk-Go1", tmp_path / "TASK.md", described,
                            note="the terrain matters most")
  assert "the terrain matters most" in ask


# ── complete, or not a definition ────────────────────────────────────────
def test_a_report_that_names_every_term_says_so(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  (package / "TASK.md").write_text(
      "# L\n\n## Rewards\n\n| term | weight |\n| --- | --- |\n"
      "| track_linear_velocity | 2.0 |\n| action_rate_l2 | -0.01 |\n\n"
      "## Terminations\n\nfell_over at 0.3 m.\n")

  covered = source_overview.describe("Rough-Walk-Go1", scope=scope(package), code={},
                                     gates=an_answer(), row={})["coverage"]
  assert covered["rewards"] == {"total": 2, "named": 2, "missing": []}
  assert covered["terminations"]["missing"] == []


def test_a_report_that_lists_some_of_the_terms_is_not_complete(tmp_path):
  """The failure this exists for: nine of fourteen reward terms reads exactly
  like all fourteen, and the term somebody is hunting is the dropped one."""
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  (package / "TASK.md").write_text(
      "# L\n\n## Rewards\n\nThe main one is track_linear_velocity at 2.0; "
      "there are smoothness penalties as well.\n")

  described = source_overview.describe("Rough-Walk-Go1", scope=scope(package),
                                       code={}, gates=an_answer(), row={})
  rewards = described["coverage"]["rewards"]
  assert rewards["total"] == 2 and rewards["named"] == 1
  assert rewards["missing"] == ["action_rate_l2"]
  assert described["coverage"]["terminations"]["missing"] == ["fell_over"]


def test_completeness_is_not_claimed_before_a_check_or_a_report(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  # No report: "0 of 14 named" would be a complaint about a file nobody wrote.
  assert source_overview.describe("Rough-Walk-Go1", scope=scope(package), code={},
                                  gates=an_answer(), row={})["coverage"] is None
  # A report, but nothing to check it against.
  (package / "TASK.md").write_text("# L\n")
  covered = source_overview.describe("Rough-Walk-Go1", scope=scope(package), code={},
                                     gates={}, row={})["coverage"]
  assert covered["checked"] is False and covered["rewards"]["total"] == 0


def test_the_ask_lists_every_term_rather_than_the_first_few(tmp_path):
  """A truncated list in the ask is a truncated list in the report."""
  many = an_answer(rewards=[{"term": f"term_{i:02d}", "mean": 0.1 * i}
                            for i in range(20)])
  described = source_overview.describe("Rough-Walk-Go1", scope=scope(tmp_path),
                                       code={}, gates=many, row={})
  ask = source_overview.ask("Rough-Walk-Go1", tmp_path / "TASK.md", described)

  for i in range(20):
    assert f"term_{i:02d}" in ask, f"term_{i:02d} was dropped from the ask"
  assert "20 reward term(s)" in ask
  assert "Every term, not a selection of them" in ask


def test_a_refresh_names_the_terms_the_report_left_out(tmp_path):
  package = tmp_path / "go1_rough_walk"
  package.mkdir()
  report = package / "TASK.md"
  report.write_text("# L\n\n## Rewards\n\ntrack_linear_velocity, 2.0.\n")

  described = source_overview.describe("Rough-Walk-Go1", scope=scope(package),
                                       code={}, gates=an_answer(), row={})
  ask = source_overview.ask("Rough-Walk-Go1", report, described)

  assert "names 1 of 2 reward terms" in ask
  assert "`action_rate_l2`" in ask
  assert "`fell_over`" in ask


def test_every_section_asks_for_all_of_something(tmp_path):
  """The sections are the shape of the document, so "one row per term" has to
  be in them rather than only in the prose around them."""
  for section in source_overview.SECTIONS:
    if section["id"] in ("task", "robot", "environment", "seeing", "unknown"):
      continue
    assert "every" in section["asks"] or "one row per" in section["asks"], \
        section["id"]


# ── the media beside it ──────────────────────────────────────────────────
def test_the_pictures_and_clips_beside_a_report_are_listed(tmp_path):
  package = tmp_path / "go1_rough_walk"
  (package / source_overview.MEDIA_DIR).mkdir(parents=True)
  (package / "TASK.md").write_text("# Rough-Walk-Go1\n")
  (package / source_overview.MEDIA_DIR / "scene.png").write_bytes(b"x")
  (package / source_overview.MEDIA_DIR / "hold.mp4").write_bytes(b"x")
  (package / source_overview.MEDIA_DIR / "notes.txt").write_text("no")

  media = source_overview.describe("Rough-Walk-Go1", scope=scope(package), code={},
                                   gates={}, row={})["media"]
  assert [(m["name"], m["kind"]) for m in media] == [("hold.mp4", "video"),
                                                     ("scene.png", "image")]


# ── through the app ──────────────────────────────────────────────────────
def test_the_overview_of_a_task_with_no_package_says_why(client):
  payload = client.get("/api/overview", params={"task": "Fake-Task-G1"}).json()
  assert payload["writable"] is False and payload["why_not"]
  assert payload["report"]["exists"] is False
  # The sections are offered even then: what a report would say is the answer
  # to "why would I want one".
  assert len(payload["sections"]) == len(source_overview.SECTIONS)


def test_writing_one_for_a_task_with_no_package_is_refused_with_the_reason(client):
  answer = client.post("/api/overview/write", json={"task": "Fake-Task-G1"})
  assert answer.status_code == 409
  assert "registers" in answer.json()["error"]


def test_the_report_is_asked_for_in_a_conversation_of_its_own(client, monkeypatch,
                                                              records_root):
  """The feature's whole promise: the agent you have open on a run keeps
  working while this is written. A separate thread is a separate provider
  session, so the two do not queue behind one another -- and if this ever went
  out on `main` it would land in the middle of somebody's investigation."""
  from rlmcp_studio.agents.claude import ClaudeProvider
  from rlmcp_studio.agents.host import AgentHost

  sent = {}

  async def fake_send(self, run_id, text, **kw):
    sent.update({"run_id": run_id, "text": text, **kw})

  monkeypatch.setattr(ClaudeProvider, "unavailable_reason", lambda self: "")
  monkeypatch.setattr(AgentHost, "refresh_auth",
                      lambda self, force=False: _nothing())
  monkeypatch.setattr(AgentHost, "send", fake_send)

  # A task being written here, with its package on disk -- the state
  # `/api/build/new` leaves behind, without letting this test depend on where
  # that route decides to put a directory.
  package = records_root.parent / "go1_rough_walk"
  package.mkdir()
  monkeypatch.setattr(
      source_starting.Starting, "get",
      lambda self, task: {"task": task, "package": "go1_rough_walk",
                          "dir": str(package), "what": "walk on rough terrain"})

  answer = client.post("/api/overview/write", json={"task": "Rough-Walk-Go1"})
  assert answer.status_code == 200, answer.text
  assert answer.json()["thread"] == source_overview.THREAD != "main"
  assert answer.json()["path"].endswith(f"go1_rough_walk/{source_overview.REPORT_NAME}")

  assert sent["run_id"] == "task:Rough-Walk-Go1"
  assert sent["thread"] == source_overview.THREAD
  # It is told about the task the same way the Build composer's messages are:
  # one paragraph, one place it comes from.
  assert "Rough-Walk-Go1" in sent["context"] and "go1_rough_walk" in sent["cwd"]
  assert "Observations" in sent["text"]


async def _nothing():
  return {}


def test_a_report_being_written_is_seen_from_the_conversation_s_state(tmp_path):
  """The rows `AgentHost.conversations` hands over say `state`, not `busy` --
  and a check that asked for `busy` would draw a report twenty file reads in
  as "no report yet" over a live button, with no chip, no poll and no refresh
  when the file landed: a button that seems to do nothing. Both a running turn
  and one blocked on a permission count; an idle one, or another thread, do
  not."""
  package = tmp_path / "go1_rough_walk"
  package.mkdir()

  def described(conversations):
    return source_overview.describe("Rough-Walk-Go1", scope=scope(package), code={},
                                    gates={}, row={}, conversations=conversations)

  working = {"run": "task:Rough-Walk-Go1", "thread": source_overview.THREAD,
             "state": "working"}
  assert described([working])["writing"] is True
  assert described([working])["needs_you"] is False
  assert described([{**working, "state": "needs_you"}])["needs_you"] is True
  assert described([{**working, "state": "idle"}])["writing"] is False
  assert described([{**working, "thread": "main"}])["writing"] is False
  assert described([])["writing"] is False
  # A caller that hands the flag straight in is still understood.
  assert described([{"thread": source_overview.THREAD, "busy": True}])["writing"] is True


# ── the view itself ──────────────────────────────────────────────────────
def test_overview_renders_without_a_run_selected():
  """It is about the task, so it has to draw for a task that has never trained
  -- which is the whole point of `TASK_VIEWS` and the thing every other view
  cannot do."""
  views = read_js("views.js")
  task_views = views.split("export const TASK_VIEWS = new Set(", 1)[1].split(")", 1)[0]
  assert '"overview"' in task_views


def test_the_conversation_the_panel_opens_on_overview_is_about_the_task():
  """A conversation keyed to a run would be a conversation about an experiment
  that may not exist: most tasks in this switcher have never trained."""
  state = (JS / "state.js").read_text()
  natural = state.split("export function naturalSubject", 1)[1][:400]
  assert 's.view === "overview"' in natural and "task:" in natural
