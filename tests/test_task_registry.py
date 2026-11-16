"""A task exists before its first run — the other half of the switcher.

The rest of this app is keyed to records, which say what *has run*. These cover
the half that says what *could* run: the registry, reached through
`rlmcp tasks` in a subprocess, and the merge that puts both halves under one
task id.

Nothing here starts a simulator. The subprocess is the seam, and it is faked --
which is also the only way to cover the two states that matter most, an
rl-mcp that cannot list tasks and a listing that times out.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


from rlmcp_studio.sources import registry as source_registry


# ── the merge ────────────────────────────────────────────────────────────

def _recorded(task, runs=3, live=0):
  return {"task": task, "root": "/records", "runs": runs, "live": live,
          "latest": {"id": "007"}}


def _listed(task, package="my.tasks", experiment="exp"):
  return {"task": task, "backend": "mjlab", "package": package,
          "experiment": experiment}


def test_a_task_that_has_never_run_is_still_a_task():
  """The whole point: without the registry it would not appear in the app at all."""
  tasks, status = source_registry.merge(
      [], {"ok": True, "tasks": [_listed("Mjlab-BoxCarry-G1")]})

  assert [t["task"] for t in tasks] == ["Mjlab-BoxCarry-G1"]
  row = tasks[0]
  assert row["registered"] is True and row["recorded"] is False
  assert row["runs"] == 0 and row["live"] == 0
  assert row["package"] == "my.tasks"
  assert status["ok"] is True


def test_history_whose_package_is_not_here_keeps_everything_it_had():
  """An archive, or somebody else's task. It reads; it cannot be previewed."""
  tasks, _ = source_registry.merge([_recorded("Old-Task")], {"ok": True, "tasks": []})

  row = tasks[0]
  assert row["recorded"] is True and row["registered"] is False
  assert row["runs"] == 3 and row["latest"] == {"id": "007"}


def test_the_two_halves_meet_under_one_id():
  """`RunRecord.task` is the registered id, not a second vocabulary -- which is
  what makes this a merge rather than a mapping."""
  tasks, _ = source_registry.merge(
      [_recorded("Example-Steering-Rough-G1", runs=12, live=1)],
      {"ok": True, "tasks": [_listed("Example-Steering-Rough-G1",
                                     package="my_tasks.tasks",
                                     experiment="steering_rough_g1")]})

  assert len(tasks) == 1
  row = tasks[0]
  assert row["recorded"] and row["registered"]
  assert row["runs"] == 12 and row["live"] == 1
  assert row["experiment"] == "steering_rough_g1"


def test_a_live_run_still_sorts_first():
  """The order the switcher had: something training, then the biggest history.
  A registered task with no runs must not displace either."""
  tasks, _ = source_registry.merge(
      [_recorded("Busy", runs=2, live=1), _recorded("Big", runs=40)],
      {"ok": True, "tasks": [_listed("Brand-New")]})

  assert [t["task"] for t in tasks] == ["Busy", "Big", "Brand-New"]


def test_no_registry_leaves_the_records_exactly_as_they_were():
  """An rl-mcp without `rlmcp tasks` must cost nothing, not everything."""
  tasks, status = source_registry.merge([_recorded("Only-Recorded")], None)

  assert [t["task"] for t in tasks] == ["Only-Recorded"]
  assert tasks[0]["registered"] is False
  assert status["ok"] is False


def test_a_listing_without_a_task_id_is_skipped_rather_than_named():
  tasks, _ = source_registry.merge([], {"ok": True, "tasks": [{"backend": "mjlab"}]})

  assert tasks == []


# ── the subprocess, and its cache ────────────────────────────────────────

def test_the_answer_is_cached_and_a_refresh_asks_again():
  """Opening the switcher must not start a simulator import every time."""
  registry = source_registry.Registry()
  asked = []

  async def fake():
    asked.append(1)
    return {"ok": True, "tasks": [_listed("A")], "packages": [], "backends": []}

  registry._ask = fake

  async def run():
    await registry.tasks()
    await registry.tasks()
    await registry.tasks(refresh=True)

  asyncio.run(run())
  assert len(asked) == 2


def test_three_panels_asking_at_once_start_one_subprocess():
  """Without the lock this starts three simulator imports on a machine whose
  GPU is training."""
  registry = source_registry.Registry()
  started = []

  async def fake():
    started.append(1)
    await asyncio.sleep(0.01)
    return {"ok": True, "tasks": [], "packages": [], "backends": []}

  registry._ask = fake

  async def run():
    await asyncio.gather(registry.tasks(), registry.tasks(), registry.tasks())

  asyncio.run(run())
  assert len(started) == 1


def test_a_listing_that_hangs_is_a_message_rather_than_a_hung_studio(monkeypatch):
  monkeypatch.setattr(source_registry, "TIMEOUT", 0.05)
  monkeypatch.setattr(source_registry, "capability",
                      lambda: {"available": True, "reason": ""})

  async def never(*args, **kwargs):
    class Slow:
      async def communicate(self):
        await asyncio.sleep(5)
        return b"", b""
    return Slow()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", never)
  answer = asyncio.run(source_registry.Registry()._ask())

  assert answer["ok"] is False
  assert "no answer within" in answer["error"]
  assert answer["tasks"] == []


def test_output_that_is_not_json_at_all_says_so_rather_than_raising(monkeypatch):
  monkeypatch.setattr(source_registry, "capability",
                      lambda: {"available": True, "reason": ""})

  async def gibberish(*args, **kwargs):
    class Chatty:
      async def communicate(self):
        return b"Traceback (most recent call last):\n  ImportError: torch", b""
    return Chatty()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", gibberish)
  answer = asyncio.run(source_registry.Registry()._ask())

  assert answer["ok"] is False
  assert "not JSON" in answer["error"]


def test_a_noisy_import_does_not_hide_a_good_listing(monkeypatch):
  """A task package that prints on import lands in front of the answer, not
  instead of it."""
  monkeypatch.setattr(source_registry, "capability",
                      lambda: {"available": True, "reason": ""})

  async def noisy(*args, **kwargs):
    class Chatty:
      async def communicate(self):
        return b"loading CUDA kernels...\n{\"tasks\": [{\"task\": \"A\"}]}", b""
    return Chatty()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", noisy)
  answer = asyncio.run(source_registry.Registry()._ask())

  assert answer["ok"] is True
  assert [t["task"] for t in answer["tasks"]] == ["A"]


def test_the_capability_probe_costs_no_simulator():
  """It has to be answerable on every /api/config, so it imports nothing heavy.

  Either the installed rl-mcp has `rlmcp tasks` or it says why not -- both are
  correct answers, and which one a given machine gives depends on the rl-mcp
  installed beside the studio.
  """
  import sys

  probe = source_registry.capability()
  assert probe["available"] is not bool(probe["reason"])
  assert "torch" not in sys.modules, "the probe pulled in a simulator"


def test_the_named_packages_reach_the_subprocess(monkeypatch):
  """`--task-package` is how a task in your own repository gets listed."""
  monkeypatch.setattr(source_registry, "capability",
                      lambda: {"available": True, "reason": ""})
  seen = {}

  async def spy(*argv, **kwargs):
    seen["argv"] = argv

    class Done:
      async def communicate(self):
        return b'{"tasks": []}', b""
    return Done()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
  asyncio.run(source_registry.Registry(["my.tasks", "other"])._ask())

  assert "--task-package" in seen["argv"]
  assert "my.tasks" in seen["argv"] and "other" in seen["argv"]
  assert "tasks" in seen["argv"] and "--json" in seen["argv"]


def test_an_rlmcp_without_the_subcommand_is_not_an_empty_task_list(monkeypatch):
  """argparse exits 2 with its usage on stderr and nothing on stdout. Reading
  that as "no tasks" would leave the switcher quietly missing every task that
  has never run -- the exact failure this whole path exists to fix."""
  class Refused:
    returncode = 2

    async def communicate(self):
      return b"", b"rlmcp: error: argument command: invalid choice: 'tasks'"

  async def refused(*args, **kwargs):
    return Refused()

  monkeypatch.setattr(asyncio, "create_subprocess_exec", refused)
  answer = asyncio.run(source_registry.Registry()._ask())

  assert answer["ok"] is False
  assert "invalid choice" in answer["error"]


def test_the_task_repositorys_own_interpreter_is_preferred(tmp_path):
  """The studio's venv has rl-mcp and nothing else -- no simulator, and not
  your task package. Asked from it, the answer is honest and useless."""
  repo = tmp_path / "tasks-repo"
  (repo / ".venv" / "bin").mkdir(parents=True)
  (repo / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")

  python, cwd = source_registry.task_environment([repo])

  assert python == str(repo / ".venv" / "bin" / "python")
  assert cwd == str(repo)


def test_no_venv_next_door_falls_back_to_this_interpreter(tmp_path):
  import sys

  python, cwd = source_registry.task_environment([tmp_path])

  assert (python, cwd) == ("", "")
  assert source_registry.Registry().python == sys.executable


def test_a_simulator_saying_hello_does_not_lose_the_answer():
  """Warp prints a banner naming every CUDA device, and mujoco_warp a line per
  compiled kernel, all on stdout in front of the JSON. Refusing the answer
  because a library introduced itself is the wrong half to fail on."""
  from rlmcp_studio.sources.taskenv import _json_at_the_end

  noisy = ("Warp 1.16.0 initialized:\n"
           "   CUDA Toolkit 12.9, Driver 13.2\n"
           "Module _kernel_1 load on device 'cpu' took 0.22 ms  (cached)\n"
           '{\n  "ok": true,\n  "result": {"passed": true}\n}')

  assert _json_at_the_end(noisy) == {"ok": True, "result": {"passed": True}}
  assert _json_at_the_end('{"plain": 1}') == {"plain": 1}
  assert _json_at_the_end("no json here at all") is None
  assert _json_at_the_end("") is None


# ── tasks being written here ─────────────────────────────────────────────

def test_a_task_being_written_appears_before_anything_registers_it(tmp_path):
  """Between "I want a quadruped that walks on rough terrain" and the first
  successful import there is nothing to select and nothing to build against."""
  from rlmcp_studio.sources import starting as source_starting

  store = source_starting.Starting(tmp_path)
  store.add("Mjlab-Rough-Walk-Go1", "go1_rough_walk", str(tmp_path / "go1_rough_walk"),
            "walk on rough terrain")

  merged = source_starting.merge([], store.all())

  assert [t["task"] for t in merged] == ["Mjlab-Rough-Walk-Go1"]
  assert merged[0]["starting"] is True
  assert merged[0]["registered"] is False and merged[0]["recorded"] is False


def test_the_registry_wins_once_it_can_see_the_task(tmp_path):
  """The entry is state about a gap, and the gap closes on its own."""
  from rlmcp_studio.sources import starting as source_starting

  store = source_starting.Starting(tmp_path)
  store.add("Mjlab-Rough-Walk-Go1", "go1_rough_walk", str(tmp_path),
            "walk on rough terrain")
  real = [{"task": "Mjlab-Rough-Walk-Go1", "registered": True, "runs": 2}]

  merged = source_starting.merge(real, store.all())

  assert len(merged) == 1
  assert merged[0]["registered"] is True and "starting" not in merged[0]


def test_names_that_would_be_hard_to_change_later_are_refused_now():
  from rlmcp_studio.sources.starting import check_names

  assert check_names("Mjlab-Rough-Walk-Go1", "go1_rough_walk") == ""
  assert "task id" in check_names("mjlab rough walk!", "go1_rough_walk")
  assert "package" in check_names("Mjlab-Rough-Walk-Go1", "Go1 Rough Walk")


def test_edits_inside_the_task_are_free_and_everything_else_asks(tmp_path):
  """Scope by path, not by tool. It is the same Edit either way, and a table
  that freed the tool would have freed it everywhere."""
  from rlmcp_studio.agents import policy

  inside = tmp_path / "go1_rough_walk"
  inside.mkdir()
  free = [str(inside)]

  assert policy.verdict("Edit", {"file_path": str(inside / "rewards.py")}, free) == "allow"
  assert policy.verdict("Write", {"file_path": str(inside / "mdp" / "new.py")}, free) == "allow"
  assert policy.verdict("Edit", {"file_path": str(tmp_path / "other.py")}, free) == "ask"
  # No scope at all -- a task the registry cannot place -- and it still asks.
  assert policy.verdict("Edit", {"file_path": str(inside / "rewards.py")}) == "ask"
  # A shell command's effect is not bounded by any argument this can read.
  assert policy.verdict("Bash", {"command": f"rm -rf {inside}"}, free) == "ask"


def test_a_task_begun_here_is_a_package_this_studio_imports():
  """`+ New task` makes a directory and the agent writes a package into it,
  but the studio was launched with whatever `$RLMCP_TASK_PACKAGES` held at the
  time, and a package created a minute ago is by construction not in it. Left
  there, the first gate would say `Unknown task ... Nothing imported here
  registers it.` -- true, and a dead end for the flagship flow: create, then
  check, and the check cannot see it, with no button in the app to fix it
  short of relaunching with a longer command line.

  The package name is the one thing the dialog definitely knows, so the studio
  remembers it."""
  from rlmcp_studio.sources.registry import Registry
  from rlmcp_studio.sources.taskenv import TaskEnv

  env = TaskEnv("python", "/tmp", ["my_tasks.tasks"])
  assert env.learn("go1_rough_walk") is True
  assert "--task-package" in env.argv("check") and "go1_rough_walk" in env.argv("check")
  assert env.learn("go1_rough_walk") is False, "learning it twice does not repeat the flag"
  assert env.learn("") is False

  reg = Registry(["my_tasks.tasks"], "python", "/tmp")
  reg._answer, reg._at = {"tasks": []}, 1.0
  assert reg.learn("go1_rough_walk") is True
  assert "go1_rough_walk" in reg.packages and "go1_rough_walk" in reg.env.packages
  assert reg._answer is None, "the listing that did not know the package is dropped"


def test_the_new_task_route_teaches_both_environments():
  """The gates and the preview run under `task_env`; the switcher's listing
  runs under the registry's own. A package learned by only one of them produces
  a task that lists but will not check, or checks but never appears."""
  package = Path(__file__).resolve().parent.parent / "rlmcp_studio"
  routes_src = (package / "routes" / "build.py").read_text()
  new = routes_src.split("async def api_new_task", 1)[1].split("@app.post", 1)[0]
  assert "task_env.learn(package)" in new
  assert "registry.learn(package)" in new

  boot = (package / "context.py").read_text().split("starting = source_starting.Starting()", 1)[1][:600]
  assert "starting.all()" in boot, \
      "and a restart re-learns them, since that is what you do when one will not import"
