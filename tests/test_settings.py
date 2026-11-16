"""The settings file: the third kind of memory this studio has.

Without it, where the records are would live on a command line, and how the
studio looks would live in one browser's `localStorage`. Neither is a file you
can open, and neither follows you anywhere. These hold the rules that make the file safe to
have: it never outranks the shell, a broken one never stops the app, and a
crash mid-write never costs you the settings you had.
"""

from __future__ import annotations

import json

import pytest

from rlmcp_studio import settings
from rlmcp_studio.config import resolve


@pytest.fixture
def file(tmp_path):
  return tmp_path / "settings.json"


# ── reading ───────────────────────────────────────────────────────────────
def test_no_file_yet_is_not_a_problem(file):
  """The normal state on a fresh machine, and not worth a word to anybody."""
  values, problem = settings.load(file)

  assert values == {}
  assert problem == ""


def test_a_broken_file_is_reported_and_ignored(file):
  """A studio that will not start because of a stray comma is worse than one
  that starts with defaults and says why."""
  file.write_text('{"records": ["/tmp"],,}')

  values, problem = settings.load(file)

  assert values == {}
  assert "not valid JSON" in problem


def test_a_file_that_is_not_an_object_is_refused(file):
  file.write_text('["/tmp/records"]')

  values, problem = settings.load(file)

  assert values == {}
  assert "JSON object" in problem


# ── writing ───────────────────────────────────────────────────────────────
def test_a_patch_changes_one_setting_and_keeps_the_rest(file):
  """The GUI sends the setting it changed, not the document it believes in:
  two tabs on one studio would otherwise each save their own idea of every
  other setting, and the last to touch anything would win everything."""
  settings.save({"records": ["/a"], "ui": {"scheme": "dark", "layout": "focus"}},
                file)

  merged, problem = settings.patch({"ui": {"scheme": "light"}}, file)

  assert problem == ""
  assert merged["records"] == ["/a"], "an untouched setting survives"
  assert merged["ui"] == {"scheme": "light", "layout": "focus"}


def test_a_setting_this_version_never_heard_of_survives_a_save(file):
  """Dropping it would be a downgrade nobody asked for: it belongs to a
  version that does know about it."""
  file.write_text(json.dumps({"somethingNewer": {"a": 1}}))

  merged, _ = settings.patch({"ui": {"scheme": "light"}}, file)

  assert merged["somethingNewer"] == {"a": 1}


def test_a_patch_refuses_to_overwrite_a_file_it_could_not_read(file):
  """Merging one changed setting into "nothing" would delete whatever the
  unreadable file actually said."""
  file.write_text("{ broken")

  _, problem = settings.patch({"ui": {"scheme": "light"}}, file)

  assert "not valid JSON" in problem
  assert file.read_text() == "{ broken", "the file is left exactly as it was"


def test_writing_leaves_no_temporary_behind(file):
  settings.save({"ui": {"scheme": "dark"}}, file)

  assert file.exists()
  assert not list(file.parent.glob("*.tmp")), "the write is a rename, not a copy"


def test_a_directory_that_does_not_exist_yet_is_made(tmp_path):
  target = tmp_path / "nested" / "deeper" / "settings.json"

  assert settings.save({"ui": {}}, target) == ""
  assert target.exists()


# ── precedence ────────────────────────────────────────────────────────────
def test_the_file_is_used_when_nothing_more_immediate_says_otherwise(file, monkeypatch):
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  monkeypatch.delenv("RLMCP_TASK_PACKAGES", raising=False)
  settings.save({"records": ["/from/the/file"], "taskPackages": ["pkg"],
                 "port": 8799}, file)

  cfg = resolve(settings=str(file))

  assert [str(p) for p in cfg.records] == ["/from/the/file"]
  assert cfg.task_packages == ["pkg"]
  assert cfg.port == 8799


def test_the_environment_still_beats_the_file(file, monkeypatch):
  """`$RLMCP_RECORDS` in a shell is a statement about *this* session, and a
  saved setting must not quietly disagree with it."""
  settings.save({"records": ["/from/the/file"]}, file)
  monkeypatch.setenv("RLMCP_RECORDS", "/from/the/shell")

  cfg = resolve(settings=str(file))

  assert [str(p) for p in cfg.records] == ["/from/the/shell"]


def test_an_explicit_argument_beats_both(file, monkeypatch):
  settings.save({"records": ["/from/the/file"]}, file)
  monkeypatch.setenv("RLMCP_RECORDS", "/from/the/shell")

  cfg = resolve(records=["/from/the/flag"], settings=str(file))

  assert [str(p) for p in cfg.records] == ["/from/the/flag"]


def test_a_broken_file_leaves_the_studio_startable(file, monkeypatch):
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  file.write_text("{ not json")

  cfg = resolve(settings=str(file))

  assert cfg.records, "it falls back to the default root"
  assert "not valid JSON" in cfg.settings_error
  assert cfg.describe()["settings_error"] == cfg.settings_error, "and says so"


def test_the_ui_half_reaches_the_client(file, monkeypatch):
  """The server never reads these -- it keeps them, so a preference belongs to
  you rather than to one browser."""
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  settings.save({"ui": {"scheme": "light", "panels": {"robot": 640}}}, file)

  cfg = resolve(settings=str(file))

  assert cfg.ui == {"scheme": "light", "panels": {"robot": 640}}


def test_a_shell_s_hostname_does_not_move_the_bind_address(file, monkeypatch):
  """zsh and some CI systems set `$HOST` to the machine's hostname. Reading
  it would bind a studio that can open a shell to the network."""
  monkeypatch.setenv("HOST", "workstation.example.com")
  monkeypatch.delenv("RLMCP_STUDIO_HOST", raising=False)
  assert resolve(settings=str(file)).host == "127.0.0.1"
  monkeypatch.setenv("RLMCP_STUDIO_HOST", "0.0.0.0")
  assert resolve(settings=str(file)).host == "0.0.0.0"
