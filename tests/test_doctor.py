"""`rlmcp-studio --check`: an install says what it can do before it starts.

A wheel missing `web/` or the agent's instructions would say nothing until a
page or a conversation failed. These tests pin the two halves of the check: the files the package must carry are found from
the installed package itself (not from a repository layout), and the command
exits by the *required* findings only, so a machine without a GPU or a
records root still gets a green install and an honest warning.
"""

from __future__ import annotations

from pathlib import Path

from rlmcp_studio import doctor
from rlmcp_studio.__main__ import main
from rlmcp_studio.config import Config


def test_package_files_are_found_from_the_package_itself():
  finding = doctor._package_files(Config())
  assert finding.ok, finding.detail
  assert finding.required
  # The check reads relative to the installed package, never to a repo root,
  # which is what makes it meaningful inside a wheel installed elsewhere.
  assert doctor.HERE.name == "rlmcp_studio"
  assert (doctor.HERE / "web" / "index.html").exists()
  assert (doctor.HERE / "instructions" / "00-the-work.md").exists()


def test_rlmcp_is_importable_and_versioned():
  finding = doctor._rlmcp(Config())
  assert finding.ok, finding.detail
  assert finding.required


def test_missing_records_root_is_advisory_not_fatal(tmp_path: Path):
  cfg = Config(records=[tmp_path / "nowhere"])
  findings = doctor.run(cfg)
  records = next(f for f in findings if f.name == "records")
  assert not records.ok
  assert not records.required
  assert "--records" in records.fix
  # A missing advisory item never makes the install unhealthy.
  assert doctor.healthy(findings) == all(f.ok for f in findings if f.required)


def test_a_crashing_check_is_reported_not_raised(monkeypatch):
  def boom(cfg):
    raise RuntimeError("nvidia-smi exploded")
  monkeypatch.setattr(doctor, "_gpu", boom)
  findings = doctor.run(Config())
  gpu = next(f for f in findings if f.name == "gpu")
  assert not gpu.ok and "exploded" in gpu.detail and not gpu.required


def test_render_names_the_fix_only_when_something_is_wrong():
  text = doctor.render([
      doctor.Finding("a", True, "fine", fix="never shown"),
      doctor.Finding("b", False, "broken", fix="do this", required=True),
  ])
  assert "never shown" not in text
  assert "ERR" in text and "fix: do this" in text


def test_check_flag_does_not_start_a_server(tmp_path: Path, capsys, monkeypatch):
  monkeypatch.delenv("RLMCP_RECORDS", raising=False)
  monkeypatch.delenv("RLMCP_TASK_PACKAGES", raising=False)
  code = main(["--check", "--records", str(tmp_path / "recs"),
               "--settings", str(tmp_path / "settings.json"),
               "--port", "1"])  # port 1 would fail to bind if it tried
  out = capsys.readouterr().out
  assert code == 0, out
  assert "rlmcp studio" in out
  assert "package" in out and "rl-mcp" in out
  assert "records root does not exist" not in out  # --check speaks for itself


def test_render_check_names_the_egl_library_or_the_packages_that_provide_it(monkeypatch):
  """The first thing that broke in a fresh container: `import mujoco` loads
  EGL through PyOpenGL, and without libEGL.so.1 that is an AttributeError
  before any task is listed. The check asks the loader, not mujoco."""
  import ctypes.util
  monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
  missing = doctor._render(Config())
  assert not missing.ok and not missing.required
  assert "libegl1" in missing.fix and "MUJOCO_GL" in missing.fix
  monkeypatch.setattr(ctypes.util, "find_library", lambda name: "libEGL.so.1")
  monkeypatch.setenv("MUJOCO_GL", "egl")
  found = doctor._render(Config())
  assert found.ok and "libEGL.so.1" in found.detail and "MUJOCO_GL=egl" in found.detail


def test_tasks_check_asks_the_environment_and_repeats_the_backends_reasons(monkeypatch, tmp_path):
  """`rlmcp --json tasks` answers with a document even when no backend can
  list anything, and that document names why per backend -- the line a
  person needs, in place of a truncated traceback."""
  import subprocess as sp
  answer = ('Warp 1.17 initialized\n{"tasks": [], "packages": [], "backends": '
            '[{"backend": "mjlab", "reason": "AttributeError: \'NoneType\' object has no '
            'attribute \'eglQueryString\'"}]}\n')
  monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess(a, 1, answer, ""))
  finding = doctor._task_packages(Config(records=[tmp_path / "records"]))
  assert not finding.ok
  assert "eglQueryString" in finding.detail
  assert "libEGL" in finding.fix, "the fix names the library, not the symptom"
  listed = ('{"tasks": [{"task": "Mjlab-Cartpole-Balance"}, {"task": "Mjlab-Velocity-Flat-Unitree-G1"}], '
            '"packages": [], "backends": []}\n')
  monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess(a, 0, listed, ""))
  finding = doctor._task_packages(Config(records=[tmp_path / "records"]))
  assert finding.ok and "2 registered" in finding.detail and "Cartpole" in finding.detail


def test_the_version_is_read_from_the_installed_distribution():
  """`--check` prints the version it finds under the distribution's name, which
  is `rl-mcp-studio`. Any other name finds nothing and prints "(unpackaged)"
  even from an installed wheel."""
  from rlmcp_studio.__main__ import _version
  assert _version() != "(unpackaged)"
