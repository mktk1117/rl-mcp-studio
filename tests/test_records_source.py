"""The records side: what the rail, the task switcher and the tree are built from."""

from __future__ import annotations


from rlmcp_studio.sources.records import Library, UNKNOWN_TASK, record_summary


def test_summaries_carry_every_field_the_client_paints(records_root):
  rows = Library([records_root]).summaries()
  by_id = {r["id"]: r for r in rows}

  assert set(by_id) == {"001", "002", "003"}
  child = by_id["002"]
  assert child["parent"] == "001"
  assert child["verdict"] == "running"
  assert child["task"] == "Fake-Task-G1"
  assert child["change"] == ["action_rate_l2 -0.1 -> -0.25"]
  assert child["session"], "a record with a session must say so; the live spine needs it"
  # `line` is rlmcp's own one-liner, not a sentence the studio assembled.
  assert child["line"]


def test_summaries_can_be_filtered_to_one_task(records_root):
  library = Library([records_root])
  assert [r["id"] for r in library.summaries("Fake-Task-G1")] == ["001", "002"]
  assert [r["id"] for r in library.summaries("Second-Task-G1")] == ["003"]


def test_tasks_lists_each_task_once_with_its_run_count(records_root):
  tasks = {t["task"]: t for t in Library([records_root]).tasks()}

  assert tasks["Fake-Task-G1"]["runs"] == 2
  assert tasks["Second-Task-G1"]["runs"] == 1
  assert tasks["Fake-Task-G1"]["live"] == 1, "the run with a live session is live"


def test_a_record_written_before_the_task_field_is_grouped_not_dropped(store, records_root):
  store.new_record("ancient", hypothesis="from before tasks existed")
  rows = Library([records_root]).summaries()
  assert UNKNOWN_TASK in {r["task"] for r in rows}


def test_the_graph_is_split_by_task_and_built_by_rlmcp(records_root):
  library = Library([records_root])
  nodes = library.record_graph("Fake-Task-G1")

  assert [n["id"] for n in nodes] == ["001", "002"]
  # Runs from two problems share no ancestry; drawing them together would
  # invent a relationship.
  assert [n["id"] for n in library.record_graph("Second-Task-G1")] == ["003"]
  # Coordinates and the folded recipe come from rlmcp, so they are present
  # without the studio computing anything.
  assert "row" in nodes[0] and "lane" in nodes[0]


def test_find_returns_the_record_and_the_source_that_holds_it(records_root):
  record, source = Library([records_root]).find("002")
  assert record is not None and record.id == "002"
  assert source is not None and source.store.get_record("002") is not None

  missing, nowhere = Library([records_root]).find("nope")
  assert missing is None and nowhere is None


def test_live_session_dirs_are_only_the_ones_still_answering(records_root, session_dir):
  assert Library([records_root]).live_session_dirs() == [session_dir]


def test_liveness_is_a_fact_about_the_process_not_the_verdict(records_root, dead_session):
  """The two disagree routinely, and each answer is used for its own job.

  Record 002 still claims `running` while its trainer is gone. The task
  switcher must say the task has nothing live -- that is a statement about
  processes. The watcher keeps watching the directory anyway, because an open
  record whose trainer has not published its first heartbeat looks exactly the
  same from here and must not be dropped.
  """
  library = Library([records_root])
  assert {t["task"]: t["live"] for t in library.tasks()}["Fake-Task-G1"] == 0
  assert library.live_session_dirs() == [dead_session]
  assert [r for r in library.summaries() if r["id"] == "002"][0]["verdict"] == "running"


def test_a_closed_run_whose_trainer_is_gone_stops_being_watched(store, records_root,
                                                                dead_session):
  """Nothing will ever write there again; watching it costs and buys nothing."""
  store.update_record("002", lambda r: setattr(r, "verdict", "interrupted"))
  assert Library([records_root]).live_session_dirs() == []


def test_feedback_counts_follow_rlmcps_unanswered_rule(store, records_root):
  from rlmcp.records import Feedback

  store.add_feedback("002", Feedback(text="please look at the wrist", kind="steer"))
  store.add_feedback("002", Feedback(text="nice", kind="approve"))
  store.add_feedback("002", Feedback(text="it wobbles", kind="observe"))

  row = [r for r in Library([records_root]).summaries() if r["id"] == "002"][0]
  assert row["feedback_total"] == 3
  # `observe` and `approve` are information, not instructions: only the steer
  # is owed an answer.
  assert row["feedback_open"] == 1


def test_the_palette_is_rlmcps(records_root):
  from rlmcp.records.views import VERDICT_COLORS

  assert Library([records_root]).palette() == VERDICT_COLORS


def test_record_summary_survives_a_record_with_almost_nothing_in_it(store):
  bare = store.new_record("bare")
  summary = record_summary(bare)
  assert summary["id"] == bare.id
  assert summary["task"] == UNKNOWN_TASK
  assert summary["metrics"] == []


def test_an_id_that_exists_in_two_roots_is_resolved_by_task(tmp_path, records_root):
  """Ids are per-store: two roots both number their records from 001. A `find`
  that returned whichever root came first would, on a two-root studio, make
  clicking a run in the rail show a *different* run's claim, metrics and session
  under the right id — wrong in the one way this app must never be wrong."""
  from rlmcp.records import open_store

  from rlmcp_studio.sources.records import Library

  second = tmp_path / "other-records"
  store = open_store(second)
  store.new_record("elsewhere", task="Another-Task", hypothesis="a different run entirely")
  mine = store.list_records()[0]

  library = Library([records_root, second])
  clash, _ = library.find(mine.id)
  assert clash is not None, "the id exists in both roots"

  found, source = library.find(mine.id, task="Another-Task")
  assert found.slug == "elsewhere"
  assert str(second) in str(source.root)

  # And the studio can say which ids are ambiguous, so a two-root run can warn.
  assert mine.id in library.ambiguous_ids()


def test_liveness_travels_with_the_summary_and_is_not_the_verdict(records_root,
                                                                  dead_session):
  """The rail does not light a run from `verdict == "running"`.

  A record says `running` from the moment it opens until somebody closes it
  out, which can be hours after the trainer stopped -- so a rail lit from the
  verdict would beat away beside dead runs and claim, in the one panel that has
  to be trustworthy, that something was training when nothing was. Liveness is a fact about a
  process and it travels beside the claim.
  """
  rows = {r["id"]: r for r in Library([records_root]).summaries(liveness=True)}

  running = rows["002"]
  assert running["verdict"] == "running", "the record still makes its claim"
  assert running["live"] is False, "and its trainer is gone, which is the fact"
  assert rows["001"]["live"] is False, "a closed record with no session is not live"

  # Off by default: the callers that only want the records should not pay for
  # a liveness probe per run.
  assert "live" not in Library([records_root]).summaries()[0]


def test_a_running_trainer_is_reported_live(records_root):
  rows = {r["id"]: r for r in Library([records_root]).summaries(liveness=True)}
  assert rows["002"]["live"] is True


def test_the_live_signature_changes_when_a_trainer_dies(records_root, session_dir):
  """What the heartbeat loop diffs.

  Liveness is the one thing that changes with no file changing -- a trainer
  that dies stops writing -- so the studio has to ask again on a clock, and
  this is the answer it compares.
  """
  from tests.conftest import kill_session

  library = Library([records_root])
  alive = library.live_signature()
  assert alive, "a run is training"

  kill_session(session_dir)
  assert library.live_signature() != alive
  assert library.live_signature() == ""
