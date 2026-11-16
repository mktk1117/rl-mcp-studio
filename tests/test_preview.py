"""The preview's own state machine.

The process is a real `rlmcp play` and cannot run here, but the four words the
panel shows are decided from two fields -- is it running, and how did it end --
and getting those wrong is what makes a preview read as something it is not.
"""

from __future__ import annotations

import signal

import pytest

from rlmcp_studio.sources.preview import CLEAN_EXITS, Preview
from rlmcp_studio.sources.taskenv import TaskEnv


@pytest.fixture
def preview(tmp_path) -> Preview:
  return Preview("Some-Task", TaskEnv(cwd=str(tmp_path)), root=tmp_path / "previews")


def test_a_preview_nobody_has_built_is_off(preview):
  assert preview.state() == "off"


def test_stopping_it_yourself_is_not_a_failure(preview):
  """`Stop` asks politely, then insists with SIGTERM, and the exit code comes
  back `-15`. Only `0` counted as a clean end, so a preview the user had just
  deliberately ended was drawn as `failed` -- red, with the traceback box open
  over a log that has no traceback in it. Confirmed against the running studio
  before the fix: stopping a preview through the API answered `state: failed`.
  """
  preview.exit_code = -signal.SIGTERM
  assert preview.state() == "stopped"


def test_a_shell_reports_the_same_death_as_143(preview):
  """The same signal, one layer further out. `128 + n` is how a shell spells
  it, and a preview started through one must not be judged differently."""
  preview.exit_code = 128 + signal.SIGTERM
  assert preview.state() == "stopped"


def test_a_preview_that_actually_died_still_says_so(preview):
  """The state exists to be told apart from `stopped`; an import error, a
  missing task, a segfault -- these are the ones the traceback belongs to."""
  preview.exit_code = 1
  assert preview.state() == "failed"


def test_the_state_and_the_error_message_agree(preview):
  """The two halves of "how did it end" are decided by one list.

  With two, `_reap` could refuse to write an error for a SIGTERM death while
  `state()` called it `failed`, and the panel would show a failure with nothing
  to say about it. One rule, read by both.
  """
  for code in CLEAN_EXITS:
    preview.exit_code = code
    assert preview.state() == "stopped", code


def test_a_preview_says_when_it_was_built(preview):
  """The panel has to be able to compare that against the task's files.

  It knew `uptime_s`, which is a duration and cannot be compared with an mtime.
  Publishing the moment is what lets the Build view notice that its green gates
  and its running preview are about two different versions of the same task.
  """
  preview.started_at = 1_700_000_000.0
  assert preview.describe()["started_at"] == 1_700_000_000.0


def test_a_preview_nobody_started_has_no_start_time(tmp_path):
  from rlmcp_studio.sources.preview import Previews

  described = Previews(TaskEnv(cwd=str(tmp_path)), root=tmp_path).describe("X")
  assert described["started_at"] == 0.0, \
      "zero, not now: an absent preview must never look newer than the code"
