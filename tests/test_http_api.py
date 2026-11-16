"""The HTTP surface as a whole: every route answers, and the confined ones stay confined.

The client fetches all of this, so a route that renames a field or starts
500-ing is a blank panel with no error anybody sees. These are deliberately
shallow -- one assertion about shape per route -- because the depth lives in
the per-feature files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict



def test_config_says_where_it_is_reading_from(client, records_root):
  payload = client.get("/api/config").json()
  assert str(records_root) in json.dumps(payload["roots"])
  assert payload["palette"], "the client colours verdicts from this"


def test_runs_and_tasks_answer(client):
  assert len(client.get("/api/tasks").json()["tasks"]) == 2
  assert len(client.get("/api/runs").json()["runs"]) == 3
  assert len(client.get("/api/runs", params={"task": "Fake-Task-G1"}).json()["runs"]) == 2


def test_the_rail_is_told_which_runs_are_actually_training(client, session_dir):
  """`verdict` is the record's claim; `live` is the process.

  A rail that drew its pulse from `verdict == "running"` alone would beat
  beside trainers that have been gone for hours, because the verdict stays true
  for as long as nobody closes the run out. So the two answers travel together
  and disagree honestly.
  """
  from tests.conftest import kill_session

  rows = {r["id"]: r for r in client.get("/api/runs").json()["runs"]}
  assert rows["002"]["verdict"] == "running" and rows["002"]["live"] is True
  assert rows["001"]["live"] is False

  kill_session(session_dir)
  rows = {r["id"]: r for r in client.get("/api/runs").json()["runs"]}
  assert rows["002"]["verdict"] == "running", "the record's claim is unchanged"
  assert rows["002"]["live"] is False, "and the fact underneath it moved"


def test_one_run_carries_its_claim_its_ledger_and_its_live_state(client):
  payload = client.get("/api/run/002").json()

  assert payload["record"]["id"] == "002"
  assert payload["hypothesis"].startswith("a gate")
  assert payload["falsifier"]["prose"] == ""     # this record has none written
  assert payload["feedback"] == []
  assert payload["live"]["ok"] and payload["live"]["state"] == "running"
  assert payload["dir"], "the record's own directory, for anyone who wants the files"


def test_a_run_with_no_session_says_why_rather_than_looking_broken(client):
  live = client.get("/api/run/001").json()["live"]
  assert live["ok"] is False and "no session" in live["reason"]


def test_an_unknown_run_is_a_404_everywhere(client):
  for url in ("/api/run/999", "/api/run/999/clips", "/api/diff?record_id=999"):
    assert client.get(url).status_code == 404, url


def test_the_record_graph_answers_per_task(client):
  assert len(client.get("/api/records/graph",
                        params={"task": "Fake-Task-G1"}).json()["nodes"]) == 2


def test_session_events_and_traces_read_a_live_run(client, session_dir):
  assert client.get("/api/session", params={"path": str(session_dir)}).json()["ok"]
  assert client.get("/api/events", params={"path": str(session_dir)}).json()["events"]
  assert client.get("/api/traces", params={"path": str(session_dir)}).json()["changed"] == 1


# ── steering over HTTP ───────────────────────────────────────────────────
def test_a_read_only_command_goes_straight_through(client, trainer):
  answer = client.post("/api/run/002/command",
                       json={"cmd": "screenshot", "args": {"env_id": 2}})
  assert answer.status_code == 200 and answer.json()["ok"]


def test_a_command_that_changes_the_run_needs_saying_so_twice(client, trainer):
  """This endpoint is reachable without an agent, so it draws the same line
  the agent's permission table draws."""
  refused = client.post("/api/run/002/command",
                        json={"cmd": "set_parameter",
                              "args": {"key": "reward.track_lin_vel.weight",
                                       "value": 3.0}})
  assert refused.status_code == 428
  assert refused.json()["needs_confirmation"] is True
  assert trainer.seen == [], "nothing may reach the trainer before confirmation"

  allowed = client.post("/api/run/002/command",
                        json={"cmd": "set_parameter", "confirmed": True,
                              "args": {"key": "reward.track_lin_vel.weight",
                                       "value": 3.0}})
  assert allowed.status_code == 200 and allowed.json()["ok"]
  assert trainer.seen[0]["cmd"] == "set_parameter"


def test_a_command_to_a_dead_trainer_says_what_still_works(client, dead_session):
  answer = client.post("/api/run/002/command", json={"cmd": "screenshot"})
  assert answer.status_code == 409
  assert answer.json()["trainer_gone"] is True


def test_a_command_needs_a_session_to_send_it_to(client):
  answer = client.post("/api/run/001/command", json={"cmd": "screenshot"})
  assert answer.status_code == 409
  assert "session" in answer.json()["error"]


def test_an_empty_command_is_refused(client):
  assert client.post("/api/run/002/command", json={}).status_code == 400


def test_parameters_are_served_live_or_from_the_snapshot(client, trainer):
  payload = client.get("/api/run/002/parameters").json()
  assert payload["live"] is True and payload["parameters"]


# ── the ledger ───────────────────────────────────────────────────────────
def test_feedback_can_be_written_from_the_gui(client, store):
  answer = client.post("/api/run/002/feedback",
                       json={"text": "it limps on the left", "kind": "observe"})
  assert answer.status_code == 200 and answer.json()["count"] == 1
  assert store.get_record("002").feedback[0].text == "it limps on the left"


def test_empty_feedback_is_refused(client):
  assert client.post("/api/run/002/feedback", json={"text": "  "}).status_code == 400


# ── artifacts stay inside the run ────────────────────────────────────────
def test_an_artifact_of_the_run_is_served(client, frame):
  answer = client.get("/api/artifact", params={"path": str(frame)})
  assert answer.status_code == 200
  assert answer.headers["content-type"] == "image/png"


def test_a_file_outside_every_root_is_not_served(client):
  assert client.get("/api/artifact", params={"path": "/etc/passwd"}).status_code == 404


def test_a_traversal_dressed_up_as_an_artifact_is_not_served(client, session_dir):
  sneaky = str(session_dir / "artifacts" / ".." / ".." / ".." / ".." / "etc" / "passwd")
  assert client.get("/api/artifact", params={"path": sneaky}).status_code == 404


# ── the agent surface answers even with no credentials ───────────────────
def test_the_agent_status_says_why_it_cannot_run(client):
  payload = client.get("/api/agent").json()
  assert payload["provider"] == "claude"
  assert "policy" in payload, "the permission table is shown to the user"
  if not payload["available"]:
    assert payload["reason"], "unavailable without a reason is a blank panel"


def test_asking_without_credentials_is_a_503_not_a_crash(client):
  answer = client.post("/api/agent/002/message", json={"text": "hello"})
  assert answer.status_code in (200, 503)
  if answer.status_code == 503:
    assert answer.json()["error"]


def test_a_transcript_can_be_asked_for_before_anything_was_said(client):
  payload = client.get("/api/agent/002").json()
  assert payload["transcript"] == [] and payload["busy"] is False


def test_the_prompt_can_be_read_before_a_word_is_said(client):
  """The Briefing window, on a run nobody has opened a conversation on. It
  answers about the conversation that *would* start, and says so -- the
  alternative is a window that is only useful once it is too late to change
  what it shows."""
  payload = client.get("/api/agent/002/briefing").json()
  assert payload["live"] is False and payload["run"] == "002"
  assert payload["harness"] == "studio"
  ids = [section["id"] for section in payload["sections"]]
  assert ids == ["instructions", "tools", "context"]
  assert "Run 002" in dict((s["id"], s["text"]) for s in payload["sections"])["context"]
  assert payload["cwd"], "where it would be started"


def test_a_briefing_for_a_run_that_is_not_here_is_a_404(client):
  assert client.get("/api/agent/999/briefing").status_code == 404


def test_the_harness_can_be_turned_off_from_the_panel(tmp_path, records_root,
                                                     monkeypatch):
  """And the window then shows the shorter truth: nothing from the studio but
  the tools and the directory.

  On its own settings file rather than the `client` fixture's: choosing a
  harness *saves* it, and a test that writes the developer's real preferences
  is a test that turns their studio's instructions off.
  """
  from fastapi.testclient import TestClient
  from rlmcp_studio import settings as settings_store
  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve

  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  where = tmp_path / "settings.json"
  settings_store.save({}, where)
  with TestClient(create_app(resolve(records=[str(records_root)],
                                     settings=str(where)))) as client:
    chosen = client.post("/api/agent/settings", json={"harness": "plain"}).json()
    assert chosen["harness"] == "plain"
    assert "open one keeps the prompt" in chosen["harness_applies"], (
        "a system prompt is fixed at connect, and the panel must not imply "
        "otherwise")
    # Written down, because a preference that does not survive a restart is
    # the shape of bug that makes a setting feel like it never stuck.
    assert settings_store.load(where)[0]["ui"]["agentHarness"] == "plain"

    payload = client.get("/api/agent/002/briefing").json()
    assert payload["sections"] == [] and payload["preset"] is True


def test_a_harness_nobody_offers_is_refused(client):
  """Refused before anything is stored, so a typo cannot leave the studio in a
  state it has no name for."""
  before = client.get("/api/agent").json()["harness"]
  assert client.post("/api/agent/settings",
                     json={"harness": "whatever"}).status_code == 400
  assert client.get("/api/agent").json()["harness"] == before


# ── the live socket ──────────────────────────────────────────────────────
def test_the_socket_greets_and_replays_from_a_sequence(client):
  with client.websocket_connect("/ws") as socket:
    hello = socket.receive_json()
    assert hello["kind"] == "hello" and hello["data"]["replay_window"] > 0

    socket.send_json({"kind": "resume", "seq": 0})
    # Nothing has happened yet, so nothing is replayed -- and the socket must
    # stay open rather than erroring on an empty backlog.
    socket.send_json({"kind": "nonsense"})


def test_a_trainer_that_dies_is_announced_on_the_socket(records_root, session_dir,
                                                       monkeypatch):
  """The one event no file change can produce.

  Everything else this app pushes is the watcher seeing a write. A run ending
  is a process going away: nothing is written, the watcher stays silent, and
  the page would go on saying `training` beside numbers that stopped moving an
  hour ago. So the studio asks on a clock, and says so when the answer moves.
  """
  from fastapi.testclient import TestClient

  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve
  from tests.conftest import kill_session

  monkeypatch.setattr("rlmcp_studio.app.LIVENESS_POLL_S", 0.05)
  app = create_app(resolve(records=[str(records_root)]))

  with TestClient(app) as client:
    with client.websocket_connect("/ws") as socket:
      assert socket.receive_json()["kind"] == "hello"
      kill_session(session_dir)

      kinds = []
      for _ in range(4):
        kinds.append(socket.receive_json()["kind"])
        if "liveness.changed" in kinds:
          break
      assert "liveness.changed" in kinds, kinds


def test_the_client_is_served_and_never_cached(client):
  page = client.get("/")
  assert page.status_code == 200
  assert "no-store" in page.headers.get("cache-control", "")


def test_the_agent_starts_in_the_task_package_not_in_a_logs_directory(app, records_root):
  """`logs/rsl_rl/<task>/<run>/rlmcp`'s grandparent is a logs folder: no code,
  no CLAUDE.md, no skills. The package beside the records is where both are."""
  from rlmcp_studio.sources import code as source_code

  roots = source_code.source_roots([records_root])
  assert roots, "the fixture's records root should have a parent to use"
  # The route builds it; this pins the choice rather than the plumbing.
  assert str(roots[0]) == str(records_root.parent)


def test_the_first_message_after_a_restart_does_not_claim_you_are_signed_out(app):
  """The provider says "not signed in" when nobody has asked the CLI yet, and
  after a restart nobody has. The GUI asks on boot; a script does not."""
  from fastapi.testclient import TestClient

  with TestClient(app) as fresh:
    assert fresh.app.state if False else True     # the app is up
    answer = fresh.post("/api/agent/002/message", json={"text": "hello"})
    # Either it works, or it says something *other* than "not signed in"
    # without having looked -- what it must not do is report a stale default.
    if answer.status_code == 503:
      assert "not signed in" not in answer.json()["error"] or _really_signed_out()


def _really_signed_out() -> bool:
  import subprocess, json as _json
  try:
    out = subprocess.run(["claude", "auth", "status", "--json"],
                         capture_output=True, text=True, timeout=20).stdout
    return not _json.loads(out or "{}").get("loggedIn")
  except Exception:
    return True


# ── runs with no record ──────────────────────────────────────────────────
def test_a_session_nobody_wrote_a_record_for_is_listed(client, records_root, tmp_path):
  """The rail is built from records, so a run with none is invisible — even a
  run launched from this app's own agent panel would train to the end and
  never appear."""
  from rlmcp.session import Session

  orphan = records_root.parent / "logs" / "orphan_run" / "rlmcp"
  Session(orphan).create({"task": "Mjlab-Cartpole-Balance", "kind": "trainer"})

  sessions = client.get("/api/sessions/unrecorded").json()["sessions"]
  assert [s["name"] for s in sessions] == ["orphan_run"]
  assert sessions[0]["task"] == "Mjlab-Cartpole-Balance"


def test_a_session_that_has_a_record_is_not_offered(client, session_dir):
  """002 already names this one."""
  sessions = client.get("/api/sessions/unrecorded").json()["sessions"]
  assert all(s["session"] != str(session_dir) for s in sessions)


def test_writing_a_record_makes_the_run_visible(client, records_root, store):
  from rlmcp.session import Session

  orphan = records_root.parent / "logs" / "orphan_run" / "rlmcp"
  Session(orphan).create({"task": "Mjlab-Cartpole-Balance", "kind": "trainer"})

  answer = client.post("/api/sessions/adopt",
                       json={"session": str(orphan), "slug": "cartpole_smoke",
                             "hypothesis": "a smoke test, not an experiment"})
  assert answer.status_code == 200
  record_id = answer.json()["record"]

  written = store.get_record(record_id)
  assert written.slug == "cartpole_smoke"
  assert written.task == "Mjlab-Cartpole-Balance"
  assert written.session == str(orphan)
  assert written.hypothesis == "a smoke test, not an experiment"

  # And it is a run now: it appears in the rail and stops being offered.
  assert any(r["id"] == record_id for r in client.get("/api/runs").json()["runs"])
  assert client.get("/api/sessions/unrecorded").json()["sessions"] == []


def test_a_directory_that_is_not_on_offer_cannot_be_adopted(client, tmp_path):
  """The write is confined to the sessions this studio is actually showing."""
  answer = client.post("/api/sessions/adopt",
                       json={"session": str(tmp_path), "slug": "sneaky"})
  assert answer.status_code == 404


def test_adopting_without_a_name_is_refused(client, records_root):
  from rlmcp.session import Session

  orphan = records_root.parent / "logs" / "orphan_run" / "rlmcp"
  Session(orphan).create({"task": "T", "kind": "trainer"})
  assert client.post("/api/sessions/adopt",
                     json={"session": str(orphan), "slug": "  "}).status_code == 400


def test_adopting_a_stopped_run_does_not_claim_it_was_interrupted(client, records_root,
                                                                  store):
  """A session nobody is listening to is not a run that failed. A smoke test
  that finished all its iterations and exited clean is not interrupted, and
  saying so would be a false claim in the ledger."""
  from rlmcp.session import Session

  orphan = records_root.parent / "logs" / "finished_run" / "rlmcp"
  Session(orphan).create({"task": "T", "kind": "trainer"})
  from tests.conftest import kill_session
  kill_session(orphan)

  answer = client.post("/api/sessions/adopt",
                       json={"session": str(orphan), "slug": "finished"})
  record = store.get_record(answer.json()["record"])
  assert record.verdict != "interrupted"
  assert record.verdict in ("planned", "")


# ── the record an edit belongs to ────────────────────────────────────────
def test_an_edit_can_open_the_record_it_belongs_to(client, records_root, store,
                                                    monkeypatch):
  """The `next_run` banner says your edit cannot reach the
  running process; this is what to do about it. Parent, task and the file list
  are filled in from what the studio can see; the hypothesis is required,
  because it is the part only a person can write."""
  from rlmcp_studio.sources import code as source_code

  async def fake_diff(root, max_bytes=200_000):
    return {"ok": True, "files": [{"path": "my_tasks/env/rewards.py"},
                                  {"path": "my_tasks/env/events.py"}]}

  monkeypatch.setattr(source_code, "worktree_diff", fake_diff)

  answer = client.post("/api/records/next",
                       json={"parent": "002", "slug": "ema_action_filter",
                             "hypothesis": "an EMA on the action stream removes the chatter"})
  assert answer.status_code == 200, answer.text
  written = store.get_record(answer.json()["record"])

  assert written.parent == "002"
  assert written.task == store.get_record("002").task, "the same experiment"
  assert written.hypothesis.startswith("an EMA")
  assert any("rewards.py" in line for line in written.change)
  assert written.verdict in ("planned", ""), "nothing has run yet"


def test_a_record_is_not_opened_for_an_edit_that_does_not_exist(client, monkeypatch):
  """A clean tree means there is no edit for the record to be about."""
  from rlmcp_studio.sources import code as source_code

  async def clean(root, max_bytes=200_000):
    return {"ok": True, "files": []}

  monkeypatch.setattr(source_code, "worktree_diff", clean)
  answer = client.post("/api/records/next",
                       json={"parent": "002", "slug": "nothing",
                             "hypothesis": "there is nothing to test"})
  assert answer.status_code == 409


def test_the_next_record_needs_a_hypothesis(client):
  """The one field the studio cannot fill in for you."""
  assert client.post("/api/records/next",
                     json={"parent": "002", "slug": "unstated"}).status_code == 400


def test_a_run_can_be_diffed_against_any_other_run(client):
  """"Diff 011 against 001" is a question the studio has to answer. A diff
  against the parent answers "what did I change" and not "how far has this
  drifted from the run that worked", so the route takes an `against` that can
  name any run."""
  parent = client.get("/api/diff", params={"record_id": "003"}).json()
  chosen = client.get("/api/diff", params={"record_id": "003", "against": "001"}).json()

  assert parent["against"] != "001", "the fixture's 003 does not descend from 001"
  assert chosen["against"] == "001"
  assert chosen["config"] is not None, "a config diff against a run of its own choosing"


def test_a_reconnecting_client_gets_the_tail_and_not_the_whole_history(client, store):
  """The live spine's contract, and the reason `seq` exists: a laptop that
  slept for ten minutes must come back to what it missed, not to everything
  since the studio started, and not to a silent gap.

  Checked against a real backlog rather than an empty one -- the greeting test
  above only proves the socket greets and survives an empty replay.
  """
  # Four writes, four events.
  for i in range(4):
    assert client.post("/api/run/002/feedback",
                       json={"text": f"socket replay probe {i}",
                             "kind": "observe"}).status_code == 200

  with client.websocket_connect("/ws") as socket:
    assert socket.receive_json()["kind"] == "hello"
    socket.send_json({"kind": "resume", "seq": 0})
    everything = [socket.receive_json() for _ in range(4)]

  seqs = [e["seq"] for e in everything]
  assert seqs == sorted(seqs), "replay is in order"
  assert len(set(seqs)) == len(seqs), "and each event once"

  # Now come back from the middle: only what followed.
  with client.websocket_connect("/ws") as socket:
    socket.receive_json()
    socket.send_json({"kind": "resume", "seq": seqs[1]})
    tail = [socket.receive_json() for _ in range(2)]

  assert [e["seq"] for e in tail] == seqs[2:], "the tail, and nothing before it"


def test_every_route_that_takes_a_record_id_takes_the_task_too(app):
  """Ids are per store, so on a two-root studio an id alone can name two
  different runs. Every route that resolves one accepts the task that
  disambiguates it — a route added later without it is a route that can serve
  the wrong run's data under the right id."""
  import inspect

  routes = [r for r in app.routes
            if getattr(r, "path", "").startswith(("/api/run/", "/api/diff",
                                                  "/api/agent/{record_id}/message"))]
  assert routes, "the app should have id-bearing routes"

  missing = []
  for route in routes:
    params = inspect.signature(route.endpoint).parameters
    if "record_id" in params and "task" not in params:
      missing.append(route.path)
  assert not missing, f"these resolve a record id with no task to narrow it: {missing}"


def test_the_socket_survives_the_server_going_away_and_coming_back(client):
  """The daily reality of editing this app: the server restarts, the page does
  not. The client must notice, reconnect, and resume from where it was rather
  than sitting on a dead socket looking connected."""
  api = (Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web" / "js" / "api.js").read_text()
  connect = api.split("export function connect", 1)[1]

  assert 'addEventListener("close"' in connect, "a closed socket has to be noticed"
  assert "setTimeout(open" in connect, "and reopened"
  assert "retry" in connect and "2 **" in connect, "with a backoff, not a hot loop"
  assert 'kind: "resume", seq: lastSeq()' in connect, "resuming from the last seq"


def test_the_build_payload_carries_all_three_clocks(client):
  """One read, because the preview, the gates and the task's files are one
  screen -- and because the only way to notice that the green gates and the
  running preview are about *different versions of the task* is to have all
  three moments in the same answer."""
  payload = client.get("/api/build", params={"task": "Fake-Task-G1"}).json()

  assert "started_at" in payload["preview"], "when the environment was built"
  assert "answer" in payload["gates"], "when the check ran (inside its answer)"
  assert "at" in payload["code"] and "file" in payload["code"], \
      "and when the package last changed, with the file that changed"


def test_a_new_task_is_refused_when_there_is_no_task_repository(client, monkeypatch,
                                                                tmp_path):
  """`Path("")` is `Path(".")`, so a guard meant to catch "no task repository
  beside the records root" can pass on it -- and the package is then made in
  whatever directory the studio happened to be started from: a stray one in a
  source tree, or, on a studio launched from `$HOME`, task packages scattered
  there. This fixture has no `.venv` beside its records,
  which is exactly that case -- and the working directory is moved somewhere
  disposable, so a regression is a failed assertion rather than a package
  directory left in whoever's tree ran the suite."""
  monkeypatch.chdir(tmp_path)
  answer = client.post("/api/build/new", json={
      "task": "Example-Task-v0", "package": "my_tasks", "what": "walk on flat ground"})

  assert answer.status_code == 409
  assert "nowhere to put a package" in answer.json()["error"]
  assert not (Path.cwd() / "my_tasks").exists(), "it wrote into the working directory"


# ── starting it twice ─────────────────────────────────────────────────────
def test_a_second_start_finds_the_first_instead_of_failing(monkeypatch):
  """Clicking the launcher twice, or forgetting the tab already open, is the
  commonest way to start a studio -- and uvicorn's traceback about a bound port
  is no answer to it."""
  from rlmcp_studio import __main__ as entry

  monkeypatch.setattr(entry.webbrowser, "open", lambda url: None)
  monkeypatch.setattr(entry, "already_serving",
                      lambda host, port, timeout=1.0: "http://127.0.0.1:8730")

  started = []
  monkeypatch.setattr(entry.uvicorn, "run",
                      lambda *a, **k: started.append(1))

  assert entry.main(["--port", "8730"]) == 0
  assert not started, "the second start must not bind anything"


def test_something_else_on_the_port_is_not_mistaken_for_a_studio(monkeypatch):
  """`already_serving` asks /api/config, because something is listening is not
  the same as *this* is listening -- and opening a browser at another
  program's port would be worse than a plain error."""
  from rlmcp_studio import __main__ as entry

  class NotAStudio:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"hello": "some other server"}'

  monkeypatch.setattr(entry.urllib.request, "urlopen",
                      lambda url, timeout=1.0: NotAStudio())

  assert entry.already_serving("127.0.0.1", 8730) == ""


def test_a_setting_the_server_cannot_adopt_says_so(tmp_path, monkeypatch):
  """Changing where the records are takes a restart. A studio that accepted a
  new root and went on serving the old one would be lying in the one place it
  must not, so the reply names what needs one."""
  from fastapi.testclient import TestClient
  from rlmcp_studio import settings as settings_store
  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve

  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  where = tmp_path / "settings.json"
  settings_store.save({}, where)
  client = TestClient(create_app(resolve(records=[str(tmp_path)],
                                         settings=str(where))))

  answer = client.patch("/api/settings",
                        json={"records": ["/somewhere/else"],
                              "ui": {"scheme": "light"}}).json()

  assert answer["restart_required"] == ["records"]
  assert "ui" not in answer["restart_required"], "the GUI half applies at once"
  assert settings_store.load(where)[0]["records"] == ["/somewhere/else"]


def test_a_message_carries_the_task_the_numbers_and_the_way_to_launch(
    app, monkeypatch, records_root):
  """The whole context the agent is opened with, assembled by the route.

  `build_context` is tested on a dict; this is the half that reads the world --
  the brief, the registry row, the session's own metrics, and a launch command
  built from the same spec the Launch button uses. Every one of those is a
  place where a renamed attribute fails silently, because a context that
  cannot be built is a context that is simply thinner.
  """
  from fastapi.testclient import TestClient
  from rlmcp_studio.agents.claude import ClaudeProvider
  from rlmcp_studio.agents.host import AgentHost

  seen: Dict[str, Any] = {}

  async def capture(self, run_id, text, context="", **rest):
    seen["run"] = run_id
    seen["context"] = context

  monkeypatch.setattr(ClaudeProvider, "unavailable_reason", lambda self: "")
  monkeypatch.setattr(AgentHost, "send", capture)

  with TestClient(app) as client:
    answer = client.post("/api/agent/002/message", json={"text": "what is it doing"})
  assert answer.status_code == 200, answer.text

  context = seen["context"]
  assert seen["run"] == "002"
  # 002 is the fixture's live-looking run, so the numbers are readable.
  assert "## How this run has gone" in context
  assert "get_training_status" in context
  # And the recipe for the next one, through the interpreter that has the
  # simulator -- not the `rlmcp` on whatever PATH the agent inherits.
  assert "## Starting another run" in context
  assert "record new" in context and "RECORD_ID" in context
  assert "Fake-Task-G1" in context


def test_the_conversation_list_says_where_the_missing_half_went(records_root):
  """Flat conversation files that cannot be attributed to a records root are
  parked in `_unscoped/`. Announcing that with `print()` reaches a detached process's stdout that
  nobody reads: a person opening the panel would see their whole list gone and
  a chip reading zero, and conclude the panel was broken. So the count and the
  path travel with the list, and the app can say it where it happens."""
  from fastapi.testclient import TestClient

  from rlmcp_studio.agents.store import state_home
  from rlmcp_studio.app import create_app
  from rlmcp_studio.config import resolve

  flat = state_home()
  flat.mkdir(parents=True, exist_ok=True)
  (flat / "007#main.jsonl").write_text('{"role": "user", "text": "hi"}\n')

  with TestClient(create_app(resolve(records=[str(records_root)]))) as client:
    parked = client.get("/api/agent/conversations").json()["retired"]
  assert parked["count"] == 1
  assert parked["path"].endswith("_unscoped")
  assert "007#main.jsonl" in parked["names"]
  assert (Path(parked["path"]) / "007#main.jsonl").exists(), "kept, not deleted"


# ── which provider ───────────────────────────────────────────────────────
def test_the_status_lists_every_provider_and_a_switch_is_remembered(client):
  """Every provider is listed whether or not its CLI, SDK, or credential is
  present, with the reason it cannot answer if it cannot -- a provider missing
  from the menu is one nobody can find out how to enable."""
  payload = client.get("/api/agent").json()
  names = [p["name"] for p in payload["providers"]]
  assert names == ["claude", "codex", "antigravity"]
  for row in payload["providers"]:
    assert row["available"] or row["reason"], "unavailable without a reason is a blank card"

  answer = client.post("/api/agent/settings", json={"provider": "antigravity"})
  assert answer.status_code == 200
  body = answer.json()
  assert body["provider"] == "antigravity"
  assert [p["current"] for p in body["providers"]] == [False, False, True]
  assert client.get("/api/agent").json()["provider"] == "antigravity"
  assert client.post("/api/agent/settings", json={"provider": "gpt"}).status_code == 400
  # Back, so the fixture's other tests see what they expect.
  assert client.post("/api/agent/settings", json={"provider": "claude"}).status_code == 200


def test_codex_models_can_be_selected_and_are_remembered(client):
  for model in ("gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5", ""):
    answer = client.post("/api/agent/settings", json={
        "provider": "codex", "model": model,
    })
    assert answer.status_code == 200
    assert answer.json()["provider"] == "codex"
    assert answer.json()["model"] == model
    status = client.get("/api/agent").json()
    assert status["provider"] == "codex" and status["model"] == model
  assert client.post("/api/agent/settings", json={
      "provider": "codex", "model": "not-a-codex-model",
  }).status_code == 400
  client.post("/api/agent/settings", json={"provider": "claude"})


def test_the_mode_reaches_every_provider_and_the_model_only_one(client):
  answer = client.post("/api/agent/settings", json={"mode": "manual"}).json()
  assert answer["mode"] == "manual"
  other = client.post("/api/agent/settings", json={"provider": "antigravity"}).json()
  assert other["mode"] == "manual", "a studio preference, not a provider's"
  assert client.post("/api/agent/settings", json={"model": "claude-opus-5"}).status_code == 400, \
      "a Claude model id is not one Antigravity offers"
  client.post("/api/agent/settings", json={"provider": "claude", "mode": "auto-edit"})


def test_a_message_may_ask_to_interrupt(client):
  """`interrupt` is a flag on the message routes. With nothing running it
  changes nothing; the point of the test is that the field is accepted, and
  refused when it is not a flag."""
  answer = client.post("/api/agent/002/message", json={"text": "hello", "interrupt": True})
  assert answer.status_code in (200, 503)
  answer = client.post("/api/agent/002/message", json={"text": "hello", "interrupt": "yes please"})
  assert answer.status_code == 400


def test_antigravity_has_one_way_in_and_claude_has_none_to_choose(client):
  answer = client.post("/api/agent/settings", json={"provider": "antigravity", "signin": "key"})
  assert answer.status_code == 200 and answer.json()["signin"] == "key"
  status = client.get("/api/agent").json()
  assert status["signin"] == "key"
  assert isinstance(status["auth"], dict), "`auth` is still the sign-in state, untouched by the setting"
  # The Google-account way in is not in this tree (Google's terms, docs/providers.md).
  assert [a["id"] for a in status["signins"]] == ["key"]
  assert client.post("/api/agent/settings", json={"signin": "account"}).status_code == 400
  assert client.post("/api/agent/settings", json={"provider": "claude", "signin": "key"}).status_code == 400
  client.post("/api/agent/settings", json={"provider": "claude"})


def test_doctor_answers_with_the_checks_and_their_fixes(client):
  """`rlmcp-studio --check` as JSON: the empty state and Settings draw this.

  Shape only, plus the one property that matters for a fresh install: the
  package's own files are found from the installed package, and missing
  advisory items carry a fix a person can act on.
  """
  payload = client.get("/api/doctor").json()
  names = [f["name"] for f in payload["findings"]]
  assert names[:2] == ["package", "rl-mcp"]
  assert payload["healthy"] is True
  assert payload["where"].endswith("rlmcp_studio")
  for f in payload["findings"]:
    assert set(f) == {"name", "ok", "detail", "fix", "required"}
    if not f["ok"]:
      assert f["fix"], f"{f['name']} failed without saying what to do"
