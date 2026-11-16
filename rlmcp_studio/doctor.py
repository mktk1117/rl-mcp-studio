"""``rlmcp-studio --check`` -- is this install able to do the job, and if not, why.

Two audiences, one list. CI installs the wheel into an empty environment and
runs this from outside the source tree, which is the only place a file missing
from the wheel shows up. A person who just installed the studio runs it to
learn, before opening a browser, what the machine it runs on still lacks -- a
GPU, a signed-in agent, a records root -- and what to do about each.

Every check is a :class:`Finding` with three fields a person can act on: what
was looked at, whether it is fine, and the fix when it is not. A check is
either ``required`` (the studio cannot start without it: its own files,
rl-mcp) or advisory (it starts, but the thing the user came for will not
work). The exit code follows the required ones only, so a laptop without a
GPU still gets a green install and an honest warning.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp_studio import vendor
from rlmcp_studio.config import Config

HERE = Path(__file__).resolve().parent


@dataclass
class Finding:
  name: str
  ok: bool
  detail: str
  fix: str = ""
  required: bool = False

  def as_dict(self) -> Dict[str, Any]:
    return {"name": self.name, "ok": self.ok, "detail": self.detail,
            "fix": self.fix, "required": self.required}


def run(cfg: Config) -> List[Finding]:
  """Every check, in the order a person should read them."""
  checks = (("package", "_package_files"), ("rl-mcp", "_rlmcp"),
            ("editor libs", "_vendor"), ("rlmcp cli", "_rlmcp_cli"), ("records", "_records"),
            ("tasks", "_task_packages"), ("render", "_render"), ("gpu", "_gpu"),
            ("agent", "_agent"),
            ("menagerie", "_menagerie"))
  out: List[Finding] = []
  for name, attr in checks:
    check = globals()[attr]  # looked up late, so a test can stand one in
    try:
      out.append(check(cfg))
    except Exception as exc:  # a check that crashes is itself a finding
      out.append(Finding(name, False, f"check crashed: {exc!r}", required=False))
  return out


def healthy(findings: List[Finding]) -> bool:
  return all(f.ok for f in findings if f.required)


def render(findings: List[Finding]) -> str:
  lines = []
  for f in findings:
    mark = "ok " if f.ok else ("ERR" if f.required else "-- ")
    lines.append(f"  {mark}  {f.name:<14} {f.detail}")
    if not f.ok and f.fix:
      lines.append(f"      fix: {f.fix}")
  return "\n".join(lines)


# -- the checks -------------------------------------------------------------


def _package_files(cfg: Config) -> Finding:
  """The non-Python files the package must carry.

  `web/index.html` is what the browser loads; `instructions/*.md` is what the
  agent reads. A wheel that left them out starts, and then fails the first
  page or the first conversation, so this is where it shows up instead.
  """
  need = [HERE / "web" / "index.html", HERE / "web" / "js" / "main.js",
          HERE / "instructions" / "00-the-work.md"]
  missing = [str(p.relative_to(HERE)) for p in need if not p.exists()]
  if missing:
    return Finding("package", False, f"missing from this install: {', '.join(missing)}",
                   "reinstall rl-mcp-studio from a freshly built wheel",
                   required=True)
  return Finding("package", True, f"files present under {HERE}", required=True)


def _vendor(cfg: Config) -> Finding:
  """Monaco and xterm.js: in the wheel, or fetched into the cache (vendor.py)."""
  found = vendor.directory()
  if found is None:
    return Finding("editor libs", False,
                   f"{vendor.stamp()} not found; the code editor and terminal will not open",
                   "python -m rlmcp_studio.vendor  (or start the studio once with network)")
  where = "packaged" if found == vendor.PACKAGED else f"cached at {found}"
  return Finding("editor libs", True, f"{vendor.stamp()} ({where})")


def _rlmcp(cfg: Config) -> Finding:
  try:
    rlmcp = importlib.import_module("rlmcp")
    importlib.import_module("rlmcp.session")
    importlib.import_module("rlmcp.records")
  except Exception as exc:
    return Finding("rl-mcp", False, f"cannot import rlmcp: {exc}",
                   'pip install "rl-mcp[server]"',
                   required=True)
  version = getattr(rlmcp, "__version__", None) or _dist_version("rl-mcp") or "?"
  return Finding("rl-mcp", True, f"{version} at {Path(rlmcp.__file__).parent}", required=True)


def _rlmcp_cli(cfg: Config) -> Finding:
  """The `rlmcp` command a person will type.

  The studio itself never needs it on PATH: it runs rl-mcp through the task
  repository's own interpreter (`sources/taskenv.py`), because that is the
  environment with the simulator in it. But the tutorial, the agent's
  instructions and every docs page say `rlmcp ...`, so a shell where that word
  means nothing is a support question waiting to happen. Beside the studio's
  interpreter counts: `uv tool install` puts both scripts in one bin dir.
  """
  on_path = shutil.which("rlmcp")
  if on_path:
    return Finding("rlmcp cli", True, on_path)
  beside = Path(sys.executable).parent / "rlmcp"
  if beside.exists():
    # `uv tool install` exposes only the studio's own scripts; rl-mcp's sit in
    # the same venv but not on PATH. Fine for the studio, awkward at a shell.
    return Finding("rlmcp cli", True,
                   f"{beside} (not on PATH; `uv tool install rl-mcp` puts it there)")
  return Finding("rlmcp cli", False, "`rlmcp` is not on PATH",
                 "install rl-mcp into the same environment as the studio, or as its "
                 "own tool: uv tool install \"rl-mcp[server]\"")


def _records(cfg: Config) -> Finding:
  present = [r for r in cfg.records if r.exists()]
  if not present:
    roots = ", ".join(str(r) for r in cfg.records) or "(none)"
    return Finding("records", False, f"no records root exists yet: {roots}",
                   "pass --records DIR (it is created on first run), or set $RLMCP_RECORDS")
  return Finding("records", True, ", ".join(str(r) for r in present))


def _task_packages(cfg: Config) -> Finding:
  """What the switcher will list: asked of the environment, not guessed.

  The studio lists tasks by running `rlmcp tasks` in the task repository's
  interpreter when one sits beside the records, and in its own otherwise
  (`sources/taskenv.py`). So the honest question is not "is a package
  configured" but "does that interpreter register anything" -- an install
  with rl-mcp's `mjlab` extra lists a dozen built-in tasks with no package
  configured at all, and an install with a package that fails to import
  lists none however it is configured. This is the slowest check here (it
  imports the simulator) and the one a person most needs the answer to.
  """
  from rlmcp_studio.sources import taskenv
  from rlmcp_studio.sources.code import source_roots
  # The same derivation app.py makes: the task repository is the records
  # root's parent, and its .venv is the interpreter, when there is one.
  env = taskenv.from_roots(source_roots(cfg.records), cfg.task_packages)
  where = env.describe()
  try:
    done = subprocess.run(env.argv("--json", "tasks"), capture_output=True, text=True,
                          timeout=180, env=env.environ(), cwd=env.cwd or None)
  except subprocess.TimeoutExpired:
    return Finding("tasks", False, f"`rlmcp tasks` gave no answer in 180 s ({where['python']})",
                   "run it by hand in that interpreter; a task package that hangs at "
                   "import is usually waiting on a GPU or a download")
  # The JSON document is read whatever the exit code: `rlmcp --json tasks`
  # answers with one even when no backend could list anything, and that
  # answer names *why* per backend, which is the line a person needs.
  answer = taskenv._json_at_the_end(done.stdout)
  if not isinstance(answer, dict):
    tail = (done.stderr or done.stdout).strip().splitlines()[-1:] or ["no output"]
    return Finding("tasks", False, f"`rlmcp tasks` failed in {where['python']}: {tail[0][:300]}",
                   "install a backend into that environment (`rl-mcp[mjlab]`) or fix "
                   "the task package it imports")
  tasks = [t.get("id") or t.get("task") for t in answer.get("tasks", []) if isinstance(t, dict)]
  packages = cfg.task_packages or [p for p in answer.get("packages", []) if isinstance(p, str)]
  if not tasks:
    reasons = [f"{b.get('backend')}: {b.get('reason')}"
               for b in answer.get("backends", []) if isinstance(b, dict) and b.get("reason")]
    why = "; ".join(reasons)[:400] or "no backend listed anything"
    fix = ("install a backend with tasks (`rl-mcp[mjlab]` lists mjlab's built-in ones), "
           "pass --task-package MODULE or set $RLMCP_TASK_PACKAGES, or build one from "
           "the GUI with + New task")
    if "eglQueryString" in why or "libEGL" in why:
      fix = ("mujoco's headless renderer cannot find libEGL.so.1: install it (Debian/Ubuntu: "
             "apt install libegl1 libgl1 libglvnd0 libegl-mesa0) and set MUJOCO_GL=egl")
    return Finding("tasks", False, f"no task registered in {where['python']} ({why})", fix)
  shown = ", ".join(str(t) for t in tasks[:4]) + (f" (+{len(tasks) - 4} more)" if len(tasks) > 4 else "")
  own = "the studio's own interpreter" if where.get("own") else where["python"]
  via = f"; packages: {', '.join(packages)}" if packages else ""
  return Finding("tasks", True, f"{len(tasks)} registered in {own}: {shown}{via}")


def _render(cfg: Config) -> Finding:
  """The library mujoco's headless renderer dlopens.

  `import mujoco` loads its renderer, which loads EGL through PyOpenGL, and on
  a machine without libEGL.so.1 that is an AttributeError deep inside
  PyOpenGL before a single task is listed -- typically the first thing to
  break in a fresh container. Cheap to ask up front: the loader's own search, no import
  of mujoco needed. Advisory, because a task package that never renders is
  fine without it; the fix names the packages a Debian-family machine needs.
  """
  import ctypes.util
  found = ctypes.util.find_library("EGL")
  if not found:
    return Finding("render", False, "libEGL.so.1 not found: importing mujoco (and so every "
                   "mjlab task) will fail before listing anything",
                   "Debian/Ubuntu: apt install libegl1 libgl1 libglvnd0 libegl-mesa0; then "
                   "MUJOCO_GL=egl for headless rendering")
  gl = os.environ.get("MUJOCO_GL", "")
  return Finding("render", True, f"{found}" + (f", MUJOCO_GL={gl}" if gl else ""))


def _gpu(cfg: Config) -> Finding:
  from rlmcp_studio.sources import gpu
  if not gpu.present():
    return Finding("gpu", False, "no NVIDIA GPU visible (nvidia-smi missing or empty)",
                   "training locally needs a CUDA GPU (a remote training host "
                   "works too); the GUI, records and agent work without one")
  return Finding("gpu", True, "nvidia-smi answers")


def _agent(cfg: Config) -> Finding:
  """A way for an agent to sign in. Either provider is enough."""
  from rlmcp_studio.agents import auth
  ways = []
  if os.environ.get("ANTHROPIC_API_KEY"):
    ways.append("ANTHROPIC_API_KEY")
  if auth.cli_present():
    ways.append("claude CLI")
  if os.environ.get("GEMINI_API_KEY"):
    ways.append("GEMINI_API_KEY")
  if not ways:
    return Finding("agent", False, "no agent credential found",
                   "set ANTHROPIC_API_KEY or GEMINI_API_KEY, or sign in from the GUI's "
                   "agent panel (`claude auth login` also works)")
  return Finding("agent", True, ", ".join(ways))


def _menagerie(cfg: Config) -> Finding:
  """The robot models most task packages import at construction.

  Not the studio's dependency, but the commonest reason `rlmcp check` dies at
  `constructs` on a fresh machine, and it reads as a studio bug when it does.
  """
  env = os.environ.get("MUJOCO_MENAGERIE")
  if env and Path(env).expanduser().exists():
    return Finding("menagerie", True, f"$MUJOCO_MENAGERIE = {env}")
  return Finding("menagerie", False, "$MUJOCO_MENAGERIE is not set",
                 "clone https://github.com/google-deepmind/mujoco_menagerie and export "
                 "MUJOCO_MENAGERIE=/path/to/it (only needed by tasks that load a "
                 "menagerie robot)")


def _dist_version(name: str) -> Optional[str]:
  try:
    from importlib.metadata import version
    return version(name)
  except Exception:
    return None
