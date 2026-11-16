"""What the API does with a body it did not expect.

Every route has to assume the body was written by an adversary: a script, a
curl, or a page in another tab. These are the ways a hostile body does damage
when nothing checks it:

* a `kind` of ``{"x": 1}`` corrupts a record's write path permanently
* concurrent annotations race for one image name and silently overwrite each other
* a route that opens a path from the query string reads any directory on the machine
* ``confirmed: "no"`` is truthy and opens the gate on a live training run
* a NUL byte in a path produces a bare `Internal Server Error`
"""

from __future__ import annotations

import concurrent.futures
import json

import pytest

from rlmcp_studio.api import validate

BOX = {"x": 0.3, "y": 0.2, "w": 0.2, "h": 0.2}


# ── the field that broke a record ────────────────────────────────────────
def test_a_kind_that_is_not_a_kind_never_reaches_the_record(client, store):
  """Unchecked, `kind` goes to `Feedback(kind=…)`, reaches sqlite, and is
  refused as an unbindable type -- *after* the JSON half of the record has
  taken it. Every later write to that record then fails too."""
  answer = client.post("/api/run/002/feedback",
                       json={"text": "poison", "kind": {"x": 1}})

  assert answer.status_code == 400
  assert "kind" in answer.json()["error"]
  assert store.get_record("002").feedback == [], "nothing may be written"


def test_a_record_still_takes_writes_after_a_rejected_one(client, store):
  """The damage to guard against is not the refusal, it is a refusal that
  leaves the record unwritable."""
  client.post("/api/run/002/feedback", json={"text": "poison", "kind": 999})
  client.post("/api/run/002/feedback", json={"text": "poison", "kind": ["a"]})

  good = client.post("/api/run/002/feedback",
                     json={"text": "a real remark", "kind": "observe"})
  assert good.status_code == 200
  assert [f.text for f in store.get_record("002").feedback] == ["a real remark"]


@pytest.mark.parametrize("body", [
    {"text": None}, {"text": 123}, {"text": ["a"]}, {"text": {"a": 1}},
    {"text": "  "}, {},
])
def test_feedback_with_no_usable_text_is_a_400_not_a_500(client, body):
  answer = client.post("/api/run/002/feedback", json=body)
  assert answer.status_code == 400
  assert answer.json()["error"], "a refusal has to say what was wrong"


def test_a_remark_cannot_be_a_megabyte(client, store):
  """A couple of oversized remarks would grow a record's meta.json to
  megabytes, and it is read on every page load."""
  answer = client.post("/api/run/002/feedback", json={"text": "A" * 1_000_000})
  assert answer.status_code == 400
  assert "limit" in answer.json()["error"]
  assert store.get_record("002").feedback == []


def test_control_characters_do_not_survive_into_the_record(client, store):
  """A durable record is read back years later by someone who was not there.
  An escape sequence or a bidi override in it is a lie waiting to be read."""
  client.post("/api/run/002/feedback",
              json={"text": "wrist \x1b[31msnaps\x00 here ‮reversed"})

  stored = store.get_record("002").feedback[0].text
  assert "\x1b" not in stored and "\x00" not in stored and "‮" not in stored
  assert "wrist" in stored and "snaps" in stored


def test_newlines_and_tabs_are_left_alone(client, store):
  """People write those on purpose."""
  client.post("/api/run/002/feedback", json={"text": "one\ntwo\tthree"})
  assert store.get_record("002").feedback[0].text == "one\ntwo\tthree"


# ── consent is a boolean ─────────────────────────────────────────────────
@pytest.mark.parametrize("confirmed", ["no", "false", "0", "off", 1, [], {}, "x"])
def test_a_mutating_command_is_not_confirmed_by_a_truthy_string(client, trainer,
                                                                confirmed):
  """`confirmed: "no"` sails through a `not body.get("confirmed")` check and
  reaches the trainer. This is the only interlock between HTTP and a live
  run."""
  answer = client.post("/api/run/002/command",
                       json={"cmd": "set_parameter", "confirmed": confirmed,
                             "args": {"key": "reward.track_lin_vel.weight",
                                      "value": 9.0}})

  assert answer.status_code in (400, 428), answer.json()
  assert trainer.seen == [], "nothing may reach the trainer"


def test_confirmation_still_works_when_it_is_actually_true(client, trainer):
  answer = client.post("/api/run/002/command",
                       json={"cmd": "set_parameter", "confirmed": True,
                             "args": {"key": "reward.track_lin_vel.weight",
                                      "value": 3.0}})
  assert answer.status_code == 200 and answer.json()["ok"]
  assert trainer.seen[0]["cmd"] == "set_parameter"


@pytest.mark.parametrize("body", [
    {"cmd": ["status"]}, {"cmd": {"a": 1}}, {"cmd": None}, {"cmd": ""},
    {"cmd": "status", "args": [1, 2]}, {"cmd": "status", "args": "x"},
    {"cmd": "status", "timeout": "abc"}, {"cmd": "status", "timeout": -5},
    {"cmd": "status", "args": {"cmd": "stop_training"}},
    {"cmd": "status", "args": {"timeout": 5}},
])
def test_a_malformed_command_is_a_400_naming_the_field(client, trainer, body):
  """Unchecked, several of these become 500s carrying a Python signature:
  "call() got multiple values for argument 'cmd'"."""
  answer = client.post("/api/run/002/command", json=body)
  assert answer.status_code == 400, answer.text
  assert answer.json()["error"]


# ── paths ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("route", [
    "/api/source/file", "/api/artifact", "/api/session", "/api/source/tree",
])
def test_a_nul_byte_in_a_path_is_a_400_not_a_crash(client, route):
  key = "root" if route.endswith("tree") else "path"
  answer = client.get(route, params={key: "\x00"})
  assert answer.status_code in (400, 403, 404), answer.text
  assert answer.text.strip() != "Internal Server Error"


def test_the_session_routes_do_not_read_any_directory_on_the_machine(client, tmp_path):
  """These three take a path from the query string. Opening it unchecked would
  let a page in any tab read another project's telemetry."""
  elsewhere = tmp_path / "somebody-elses-run"
  elsewhere.mkdir()
  (elsewhere / "events.jsonl").write_text('{"kind":"secret"}\n')

  for route in ("/api/events", "/api/traces", "/api/session"):
    answer = client.get(route, params={"path": str(elsewhere)})
    assert answer.status_code == 400, f"{route} read outside the roots"
    assert "not a session" in answer.json()["error"]


def test_the_session_routes_still_read_a_real_session(client, session_dir):
  for route in ("/api/events", "/api/traces", "/api/session"):
    assert client.get(route, params={"path": str(session_dir)}).status_code == 200


def test_a_finished_runs_session_is_still_readable(client, store, session_dir):
  """"What may be read" is a wider set than "what is worth watching": most
  runs you look at have already stopped."""
  store.update_record("002", lambda r: setattr(r, "verdict", "falsified"))
  assert client.get("/api/session", params={"path": str(session_dir)}).status_code == 200


# ── annotation ───────────────────────────────────────────────────────────
def annotate_body(frame, **over):
  body = {"path": str(frame), "box": BOX, "note": "look here", "kind": "observe"}
  body.update(over)
  return body


def test_annotations_racing_each_other_keep_their_own_images(client, frame, store):
  """A name built by counting the annotations already on the record lets two
  concurrent writers count the same number: pictures get overwritten and the
  survivors are misattributed."""
  def send(index):
    return client.post("/api/run/002/annotate",
                       json=annotate_body(frame, note=f"parallel {index}"))

  with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
    answers = list(pool.map(send, range(12)))

  assert all(a.status_code == 200 for a in answers)
  record = store.get_record("002")
  keys = [entry[0] for entry in record.assets["annotations"]]

  assert len(record.feedback) == 12
  assert len(set(keys)) == 12, f"{12 - len(set(keys))} images were overwritten"
  for key in keys:
    assert store.media.get(key), f"{key} is listed on the record but not on disk"


def test_the_same_annotation_twice_is_one_file(client, frame, store):
  """A retried request should not double the evidence."""
  client.post("/api/run/002/annotate", json=annotate_body(frame))
  client.post("/api/run/002/annotate", json=annotate_body(frame))

  keys = {entry[0] for entry in store.get_record("002").assets["annotations"]}
  assert len(keys) == 1, "identical pictures are one picture"
  assert len(store.get_record("002").feedback) == 2, "both remarks are kept"


@pytest.mark.parametrize("body", [
    {"note": None}, {"note": 42}, {"note": ""}, {"kind": {"x": 1}},
    {"box": [1, 2, 3, 4]}, {"path": 123}, {"path": "\x00"},
    {"ask_agent": "yes"}, {"thread": {"a": 1}},
])
def test_a_malformed_annotation_is_a_400_and_writes_nothing(client, frame, store, body):
  answer = client.post("/api/run/002/annotate", json=annotate_body(frame, **body))
  assert answer.status_code in (400, 404), answer.text
  assert store.get_record("002").feedback == []
  assert not store.get_record("002").assets.get("annotations")


def test_the_refusal_for_a_foreign_frame_says_what_it_checked(client, tmp_path,
                                                              make_frame):
  """A message like "no such frame under this run" would describe a check the
  code does not do: any frame under any watched run is accepted."""
  outside = make_frame(tmp_path / "not-a-run" / "photo.png")
  answer = client.post("/api/run/002/annotate", json=annotate_body(outside))

  assert answer.status_code == 404
  assert "not under any run" in answer.json()["error"]


# ── the validators themselves ────────────────────────────────────────────
def test_the_kind_list_is_rlmcps_not_a_copy():
  """A kind the library adds is accepted the day it exists."""
  from rlmcp.records import FEEDBACK_KINDS

  for kind in FEEDBACK_KINDS:
    assert validate.kind(kind) == kind


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "5", True, None])
def test_a_number_has_to_be_a_finite_number(value):
  with pytest.raises(validate.BadRequest):
    validate.number(value, "timeout")


# ── the write that can destroy source ────────────────────────────────────
def test_a_save_without_the_size_guard_is_refused(client, records_root):
  """The guard is mandatory, so it cannot be skipped by omission: a client
  that simply left `expect_size` out would otherwise get the silent overwrite
  it exists to prevent."""
  target = records_root.parent / "task.py"
  target.write_text("original\n")

  answer = client.put("/api/source/file",
                      json={"path": str(target), "text": "replaced"})

  assert answer.status_code == 400
  assert "expect_size" in answer.json()["error"]
  assert target.read_text() == "original\n"


@pytest.mark.parametrize("size", ["abc", [1], {"a": 1}, -1, None])
def test_a_malformed_size_guard_is_refused(client, records_root, size):
  target = records_root.parent / "task.py"
  target.write_text("original\n")
  answer = client.put("/api/source/file",
                      json={"path": str(target), "text": "x", "expect_size": size})
  assert answer.status_code == 400
  assert target.read_text() == "original\n"


# ── what an artifact may be ──────────────────────────────────────────────
def test_a_page_dropped_in_the_records_root_is_not_served_as_html(client,
                                                                  records_root):
  """rlmcp writes records.html there, and a run can drop anything else. Served
  as text/html on this origin it would run with the studio's API in reach,
  able to call it the way the studio's own page does."""
  page = records_root / "records.html"
  page.write_text("<script>fetch('/api/run/002/feedback')</script>")

  answer = client.get("/api/artifact", params={"path": str(page)})
  assert answer.status_code == 200
  assert "text/html" not in answer.headers["content-type"]
  assert answer.headers.get("x-content-type-options") == "nosniff"


def test_a_frame_is_still_served_as_an_image(client, frame):
  answer = client.get("/api/artifact", params={"path": str(frame)})
  assert answer.headers["content-type"] == "image/png"


# ── the live socket ──────────────────────────────────────────────────────
@pytest.mark.parametrize("frame_text", [
    "[]", "123", '"hi"', "null", '{"kind":"resume","seq":"abc"}',
    '{"kind":"resume","seq":[1]}', '{"kind":"resume","seq":1e400}', "not json",
])
def test_a_frame_the_socket_did_not_expect_does_not_kill_it(client, frame_text):
  """Unhandled, each of these drops the connection with no close frame: the
  client's live feed stops and nothing says why."""
  with client.websocket_connect("/ws") as socket:
    socket.receive_json()                       # hello
    socket.send_text(frame_text)
    socket.send_json({"kind": "resume", "seq": 0})
    socket.send_json({"kind": "ping"})          # still alive and answering


def test_a_replay_cannot_be_asked_for_over_and_over(client):
  """If each `resume` replayed the whole backlog again, a few hundred bytes of
  `resume` frames would pull back megabytes."""
  with client.websocket_connect("/ws") as socket:
    socket.receive_json()
    for _ in range(20):
      socket.send_json({"kind": "resume", "seq": 0})
    socket.send_json({"kind": "ping"})          # the socket is still healthy


# ── answers that are true ────────────────────────────────────────────────
def test_diffing_against_a_record_that_does_not_exist_says_so(client):
  """Answering 200 "this run has no parent" would be a different statement,
  and a wrong one."""
  answer = client.get("/api/diff", params={"record_id": "002", "against": "999"})
  assert answer.status_code == 404
  assert "999" in answer.json()["error"]


def test_diffing_against_the_parent_still_works(client):
  payload = client.get("/api/diff", params={"record_id": "002"}).json()
  assert payload["against"] == "001"


def test_stopping_a_conversation_that_never_ran_is_not_an_ok(client):
  answer = client.post("/api/agent/002/stop", json={"thread": "never-existed"})
  assert answer.status_code == 200
  assert answer.json()["ok"] is False
  assert "no conversation" in answer.json()["reason"]


def test_a_refusal_does_not_echo_the_server_is_own_paths(client):
  """A 403 naming the resolved absolute path ("<home>/<checkout> is not under
  a configured root") to a caller who sent a relative path would disclose where
  the server runs, and where home is, to somebody who asked for neither."""
  answer = client.put("/api/source/file", json={"path": "relative/thing.py",
                                                "text": "x", "expect_size": 0})
  body = answer.text
  assert answer.status_code in (400, 403, 404)
  assert "/home/" not in body, body


# ── the record an edit opens ─────────────────────────────────────────────
@pytest.mark.parametrize("body", [
    {},                                              # nothing at all
    {"parent": "002"},                               # no slug, no claim
    {"parent": "002", "slug": "x"},                  # no hypothesis
    {"parent": "002", "slug": " ", "hypothesis": "y"},
    {"parent": "../../etc", "slug": "x", "hypothesis": "y"},
    {"parent": 7, "slug": "x", "hypothesis": "y"},
    {"parent": "002", "slug": ["a"], "hypothesis": "y"},
])
def test_a_malformed_next_record_is_a_400_or_404_and_writes_nothing(client, store, body):
  """This route writes to the ledger, so it gets the same treatment as the
  other two that do."""
  before = len(store.list_records())
  answer = client.post("/api/records/next", json=body)
  assert answer.status_code in (400, 404), answer.text
  assert len(store.list_records()) == before, "a refused request must write nothing"


def test_the_next_record_cannot_be_parented_on_a_run_that_does_not_exist(client, store):
  before = len(store.list_records())
  answer = client.post("/api/records/next",
                       json={"parent": "999", "slug": "orphan",
                             "hypothesis": "parented on nothing"})
  assert answer.status_code == 404
  assert len(store.list_records()) == before


# ── a parameter's shape ──────────────────────────────────────────────────
@pytest.mark.parametrize("value, why", [
    ("abc", "text into a number"),
    (True, "a boolean into a number"),
    ([1, 2], "a list into a number"),
])
def test_a_value_that_does_not_fit_the_parameter_never_reaches_the_trainer(
    client, trainer, session_dir, value, why):
  """Setting a parameter declared `range` with a value of `[-1, 1]` to the
  string "abc" would store it as a string and come back as
  `{"type": "float", "value": "abc", "min": -10, "max": 10}` -- the declared
  type changed underneath it -- and nothing in the GUI could put it back.
  The browser refuses it; so does this endpoint, which is reachable without
  the browser."""

  (session_dir / "params.json").write_text(json.dumps({
      "reward.track_lin_vel.weight": {"key": "reward.track_lin_vel.weight",
                                      "current": 2.0, "type": "float"},
  }))

  answer = client.post("/api/run/002/command",
                       json={"cmd": "set_parameter", "confirmed": True,
                             "args": {"key": "reward.track_lin_vel.weight",
                                      "value": value}})
  assert answer.status_code == 400, f"{why} was accepted"
  assert "reward.track_lin_vel.weight" in answer.json()["error"]
  assert not [c for c in trainer.seen if c["cmd"] == "set_parameter"], \
      "nothing may reach the trainer"


def test_a_list_parameter_takes_a_list_of_the_same_length(client, trainer, session_dir):
  """`command.twist.ranges.lin_vel_x` is `[-1, 1]`. A single number is not a
  range, and a three-element list is a different parameter."""

  (session_dir / "params.json").write_text(json.dumps({
      "command.twist.ranges.lin_vel_x": {"key": "command.twist.ranges.lin_vel_x",
                                         "current": [-1.0, 1.0], "type": "range"},
  }))
  send = lambda v: client.post("/api/run/002/command",
                               json={"cmd": "set_parameter", "confirmed": True,
                                     "args": {"key": "command.twist.ranges.lin_vel_x",
                                              "value": v}})

  assert send(0.5).status_code == 400
  assert send([1, 2, 3]).status_code == 400
  assert send(["a", "b"]).status_code == 400
  assert send([-2.0, 2.0]).status_code == 200, "a real range still goes through"


def test_a_value_that_can_never_be_stored_is_refused_whatever_the_parameter(
    client, trainer, session_dir):
  """`NaN` sent to the trainer would be written as `null`, and infinities
  fare no better. No parameter can hold them, so the shape check refuses them
  before it looks at the schema at all."""

  (session_dir / "params.json").write_text(json.dumps({
      "reward.track_lin_vel.weight": {"key": "reward.track_lin_vel.weight",
                                      "current": 2.0, "type": "float"},
  }))
  for body in ('{"cmd":"set_parameter","confirmed":true,'
               '"args":{"key":"reward.track_lin_vel.weight","value":NaN}}',
               '{"cmd":"set_parameter","confirmed":true,'
               '"args":{"key":"reward.track_lin_vel.weight","value":Infinity}}'):
    answer = client.post("/api/run/002/command", content=body,
                         headers={"content-type": "application/json"})
    assert answer.status_code == 400, body
  assert not [c for c in trainer.seen if c["cmd"] == "set_parameter"]


def test_the_shape_check_judges_against_what_the_browser_was_shown(client, trainer,
                                                                    session_dir):
  """Reading only the on-disk snapshot here would be worse than not checking:
  after a bad edit the live type can change while the snapshot does not, so the
  browser offers numbers, the endpoint accepts only a 2-list, and the parameter
  cannot be repaired through the GUI at all. Fetching the parameters remembers
  what the trainer said, and the check uses that."""

  # The snapshot says list-of-two; the *trainer* says float.
  (session_dir / "params.json").write_text(json.dumps({
      "reward.track_lin_vel.weight": {"key": "reward.track_lin_vel.weight",
                                      "current": [-1.0, 1.0], "type": "range"},
  }))
  live = client.get("/api/run/002/parameters").json()
  assert live["live"] is True, "the fixture's trainer answers"

  answer = client.post("/api/run/002/command",
                       json={"cmd": "set_parameter", "confirmed": True,
                             "args": {"key": "reward.track_lin_vel.weight",
                                      "value": 3.0}})
  assert answer.status_code == 200, answer.text
