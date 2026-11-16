"""The TensorBoard scalar reader, against event files built here byte by byte.

The reader was written against the real `tensorboard` package and can be
checked against it over the event files of real training runs, where the two
should agree exactly except on a run still being written (it grows between the
two reads). That check needs a 50 MB dependency and real training logs, so it
is not this suite's job.

What is this suite's job is the *shape* of the format, and the cases the real
files happened not to contain: a tensor-valued scalar, a half-written record at
the end of a growing file, a file that was replaced under us. Those are written
here with a miniature writer, which is also the clearest statement of what the
reader believes the format to be.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rlmcp_studio.sources import tb


# ── a miniature event-file writer ────────────────────────────────────────
def varint(value: int) -> bytes:
  out = b""
  while True:
    byte = value & 0x7F
    value >>= 7
    out += bytes([byte | (0x80 if value else 0)])
    if not value:
      return out


def key(field: int, wire: int) -> bytes:
  return varint(field << 3 | wire)


def delimited(field: int, payload: bytes) -> bytes:
  return key(field, 2) + varint(len(payload)) + payload


def f32(field: int, value: float) -> bytes:
  return key(field, 5) + struct.pack("<f", value)


def scalar_value(tag: str, value: float) -> bytes:
  """One ``Summary.Value``: a tag and a `simple_value`."""
  return delimited(1, tag.encode()) + f32(2, value)


def tensor_value(tag: str, value: float) -> bytes:
  """The shape newer writers use: a DT_FLOAT tensor of rank 0."""
  tensor = key(1, 0) + varint(1)                     # dtype = DT_FLOAT
  tensor += delimited(2, b"")                        # empty tensor_shape
  tensor += delimited(4, struct.pack("<f", value))   # tensor_content
  return delimited(1, tag.encode()) + delimited(8, tensor)


def summary(*values: bytes) -> bytes:
  """A ``Summary``: repeated Value in field 1. Forgetting this wrapper is how
  a hand-written encoder produces a file no reader can make sense of."""
  return b"".join(delimited(1, value) for value in values)


def event(step: int, values: bytes) -> bytes:
  """One Event carrying a Summary. Step 0 is written as *absent*, which is
  what protobuf does with a field holding its type's default -- and what the
  reader has to understand."""
  body = b""
  if step:
    body += key(2, 0) + varint(step)
  body += delimited(5, values)          # `values` is a whole Summary message
  return body


def frame(payload: bytes) -> bytes:
  """TFRecord framing. The CRCs are zeros: the reader does not check them."""
  return (struct.pack("<Q", len(payload)) + b"\x00" * 4 + payload + b"\x00" * 4)


def write_events(path: Path, points, tag: str = "Train/mean_reward",
                 shape=scalar_value) -> Path:
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("ab") as handle:
    for step, value in points:
      handle.write(frame(event(step, summary(shape(tag, value)))))
  return path


@pytest.fixture
def run_dir(tmp_path) -> Path:
  directory = tmp_path / "logs" / "a_run"
  directory.mkdir(parents=True)
  return directory


# ── reading ──────────────────────────────────────────────────────────────
def test_scalars_come_back_as_step_value_pairs(run_dir):
  write_events(run_dir / "events.out.tfevents.1.host.1.0",
               [(0, 1.5), (1, 2.5), (2, 3.5)])

  answer = tb.Reader().read(run_dir)
  assert answer["ok"] and answer["tags"] == ["Train/mean_reward"]
  assert answer["series"]["Train/mean_reward"] == [[0.0, 1.5], [1.0, 2.5], [2.0, 3.5]]


def test_step_zero_is_a_step_not_a_missing_field(run_dir):
  """Protobuf omits a zero field entirely. Reading "absent" as "unknown" drops
  the first point of every run that starts at zero, and only a cross-check
  against the real reader would notice."""
  write_events(run_dir / "events.out.tfevents.1.host.1.0", [(0, 0.25)])

  series = tb.Reader().read(run_dir)["series"]["Train/mean_reward"]
  assert series == [[0.0, 0.25]]


def test_a_tensor_valued_scalar_is_read_too(run_dir):
  """`add_scalar` writes `simple_value` on some versions and a rank-0 tensor
  on others. Both are one number."""
  write_events(run_dir / "events.out.tfevents.1.host.1.0",
               [(3, 7.5)], tag="Loss/value", shape=tensor_value)

  assert tb.Reader().read(run_dir)["series"]["Loss/value"] == [[3.0, 7.5]]


def test_many_tags_in_one_file(run_dir):
  path = run_dir / "events.out.tfevents.1.host.1.0"
  with path.open("ab") as handle:
    for step in range(4):
      handle.write(frame(event(step, summary(
          scalar_value("Train/mean_reward", step * 1.0),
          scalar_value("Loss/value", step * -0.5)))))

  answer = tb.Reader().read(run_dir)
  assert answer["tags"] == ["Loss/value", "Train/mean_reward"]
  assert answer["series"]["Loss/value"][-1] == [3.0, -1.5]


def test_files_in_a_subdirectory_are_found(run_dir):
  """rsl_rl writes into the run directory; other loggers use a subdirectory."""
  write_events(run_dir / "summaries" / "events.out.tfevents.9.host.9.0", [(1, 4.0)])

  assert tb.Reader().read(run_dir)["series"]["Train/mean_reward"] == [[1.0, 4.0]]


# ── a file that is still being written ───────────────────────────────────
def test_a_half_written_record_is_waited_for_not_guessed_at(run_dir):
  """The normal state of a live run's file. The reader stops at the last
  complete record and picks the rest up next time."""
  path = write_events(run_dir / "events.out.tfevents.1.host.1.0", [(1, 1.0)])
  whole = frame(event(2, summary(scalar_value("Train/mean_reward", 2.0))))
  with path.open("ab") as handle:
    handle.write(whole[:len(whole) // 2])            # the writer is mid-record

  reader = tb.Reader()
  assert reader.read(run_dir)["series"]["Train/mean_reward"] == [[1.0, 1.0]]

  with path.open("r+b") as handle:                   # the write completes
    handle.seek(0, 2)
    handle.truncate(handle.tell() - len(whole) // 2)
  with path.open("ab") as handle:
    handle.write(whole)

  assert reader.read(run_dir)["series"]["Train/mean_reward"] == [[1.0, 1.0], [2.0, 2.0]]


def test_a_growing_file_is_read_from_where_it_left_off(run_dir):
  """Re-reading from the top would get slower the longer a run goes on, which
  is exactly backwards."""
  path = run_dir / "events.out.tfevents.1.host.1.0"
  write_events(path, [(step, float(step)) for step in range(50)])

  reader = tb.Reader()
  reader.read(run_dir)
  after_first = reader._files[str(path)].offset
  assert after_first == path.stat().st_size

  write_events(path, [(step, float(step)) for step in range(50, 60)])
  answer = reader.read(run_dir)

  assert len(answer["series"]["Train/mean_reward"]) == 60
  assert reader._files[str(path)].offset == path.stat().st_size


def test_a_file_replaced_by_a_shorter_one_is_read_again(run_dir):
  """A rerun into the same directory. Continuing from the old offset would
  read the middle of a record and produce nonsense."""
  path = run_dir / "events.out.tfevents.1.host.1.0"
  write_events(path, [(step, float(step)) for step in range(30)])
  reader = tb.Reader()
  reader.read(run_dir)

  path.unlink()
  write_events(path, [(0, 99.0)])

  assert reader.read(run_dir)["series"]["Train/mean_reward"] == [[0.0, 99.0]]


# ── what is deliberately not read ────────────────────────────────────────
def test_an_event_that_is_not_a_scalar_is_skipped_quietly(run_dir):
  """A file_version event opens every file; a graph or a histogram is not a
  curve. None of them is an error."""
  path = run_dir / "events.out.tfevents.1.host.1.0"
  with path.open("ab") as handle:
    handle.write(frame(delimited(3, b"brain.Event:2")))       # file_version
    handle.write(frame(event(1, summary(scalar_value("Train/mean_reward", 1.0)))))

  assert tb.Reader().read(run_dir)["series"] == {"Train/mean_reward": [[1.0, 1.0]]}


def test_a_shaped_tensor_is_not_mistaken_for_a_number(run_dir):
  """A histogram is a tensor too. Reading its first float as "the value" would
  draw a confident line through nothing."""
  shaped = (key(1, 0) + varint(1)
            + delimited(2, delimited(2, key(1, 0) + varint(3)))   # shape [3]
            + delimited(4, struct.pack("<3f", 1.0, 2.0, 3.0)))
  path = run_dir / "events.out.tfevents.1.host.1.0"
  path.write_bytes(frame(event(1, summary(delimited(1, b"H") + delimited(8, shaped)))))

  assert tb.Reader().read(run_dir)["series"] == {}


def test_rubbish_does_not_take_the_reader_down(run_dir):
  (run_dir / "events.out.tfevents.1.host.1.0").write_bytes(b"\x00" * 64)
  (run_dir / "events.out.tfevents.2.host.2.0").write_bytes(bytes(range(256)))

  answer = tb.Reader().read(run_dir)
  assert answer["ok"] and answer["series"] == {}


# ── the wire ─────────────────────────────────────────────────────────────
def test_long_series_are_thinned_but_keep_their_last_point(run_dir):
  """The last point is the one being watched on a live run."""
  write_events(run_dir / "events.out.tfevents.1.host.1.0",
               [(step, float(step)) for step in range(5000)])

  series = tb.Reader().read(run_dir, limit=100)["series"]["Train/mean_reward"]
  assert len(series) <= 101
  assert series[-1] == [4999.0, 4999.0], "the newest point must survive thinning"
  assert series[0] == [0.0, 0.0]


def test_a_run_with_no_event_files_says_so(tmp_path):
  answer = tb.Reader().read(tmp_path)
  assert answer["ok"] is False
  assert "no TensorBoard event files" in answer["reason"]
  assert answer["series"] == {}


def test_the_run_directory_is_the_session_dirs_parent(tmp_path):
  session = tmp_path / "a_run" / "rlmcp"
  session.mkdir(parents=True)
  assert tb.run_directory(str(session)) == tmp_path / "a_run"
  assert tb.run_directory("") is None


# ── over HTTP ────────────────────────────────────────────────────────────
def test_the_endpoint_serves_a_runs_scalars(client, session_dir):
  """The event files sit beside the session, in the run directory rsl_rl
  points its writer at."""
  write_events(session_dir.parent / "events.out.tfevents.1.host.1.0",
               [(step, float(step) / 10) for step in range(120)])

  payload = client.get("/api/run/002/tensorboard").json()
  assert payload["ok"] and payload["tags"] == ["Train/mean_reward"]
  assert len(payload["series"]["Train/mean_reward"]) == 120


def test_a_run_with_no_event_files_is_answered_not_refused(client):
  payload = client.get("/api/run/002/tensorboard").json()
  assert payload["ok"] is False and payload["series"] == {}
  assert "event files" in payload["reason"]


def test_a_record_with_no_session_says_why(client):
  payload = client.get("/api/run/001/tensorboard").json()
  assert payload["ok"] is False
  assert "does not name a session" in payload["reason"]


def test_an_unknown_run_is_a_404(client):
  assert client.get("/api/run/999/tensorboard").status_code == 404


def test_the_wire_limit_is_honoured_and_bounded(client, session_dir):
  write_events(session_dir.parent / "events.out.tfevents.1.host.1.0",
               [(step, 1.0) for step in range(3000)])

  assert len(client.get("/api/run/002/tensorboard",
                        params={"limit": 50}).json()["series"]["Train/mean_reward"]) <= 51
  assert client.get("/api/run/002/tensorboard",
                    params={"limit": 10 ** 9}).status_code == 400


def test_the_reader_says_how_far_the_files_go(tmp_path):
  """A trainer flushes its event file in batches, so on a live run these
  curves can sit thousands of iterations behind the status bar. The client
  cannot say so without knowing where the files stop."""
  from rlmcp_studio.sources import tb

  directory = tmp_path / "run"
  directory.mkdir()
  write_events(directory / "events.out.tfevents.1", [(10, 1.0), (20, 2.0)])
  write_events(directory / "events.out.tfevents.2", [(15, 0.5)], tag="Loss/value")

  payload = tb.Reader().read(directory)
  assert payload["ok"]
  assert payload["last_iteration"] == 20, "the furthest step any series reached"
