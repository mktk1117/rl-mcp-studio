"""The preview: one task, built and stepping, with nothing being trained.

The env builder's whole argument is that the interval between changing a task
and seeing the change should be as short as constructing the env. This is the
thing that gets constructed.

**It is a session, which is why this file is short.** ``rlmcp play --policy
zero --mode hold`` builds the task, wraps it exactly as a training run is
wrapped, steps it at the environment's own clock and serves nothing. So a
preview answers every command a run answers: the parameter table works, Look
and Clip work, the 3-D panel works because ``live_view`` is a controller
command, and the agent's whole MCP surface applies. The studio adds a process
and a directory; everything else it already had.

**A change is a restart, not a reload.** Python binds modules at import, so
editing `rewards.py` does not change a constructed env; `importlib.reload`
half-works and leaves stale references, which is worse than not working
because the bug it produces looks like a bad reward rather than a stale
module. One env on CPU constructs in seconds, and a restart is *correct* where
a reload is merely fast.

**The log is the feature when the env will not build.** A preview that fails
is the common case while a task is being written, and the traceback is the
whole answer, so stdout and stderr go to a file next to the session and the
panel reads its tail. A dead viewer with no message is the thing this exists
to prevent.
"""

from __future__ import annotations

import asyncio
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp_studio.paths import safe_name, state_dir
from rlmcp_studio.sources.taskenv import TaskEnv

STOP_GRACE = 8.0
"""Seconds a preview is given to end itself after `rlmcp stop`, before it is
terminated. A held env's loop notices between steps, which is milliseconds --
but an env still constructing has no loop to notice with, and that is the case
this number is sized for."""

LOG_TAIL = 60
"""Lines of the preview's output the panel shows. Enough for a traceback."""

DEFAULT_ENVS = 1
"""One robot. A preview is for looking at a task, not for measuring it, and
one env constructs fastest -- which is the number this feature is about."""

CLEAN_EXITS = (0, -signal.SIGTERM, 128 + signal.SIGTERM)
"""Exit codes that mean *somebody stopped it*, not *it crashed*.

`stop()` asks politely and then insists, and insisting is SIGTERM -- which
comes back as ``-15`` from `Process.wait`, or as ``143`` through a shell.
Counting only ``0`` as a clean end would classify a preview the user just
stopped as `failed`, with the red state and the traceback box. Both `_reap`
(which decides whether to write an error) and :meth:`Preview.state` (which
decides the colour) read this one rule, so the two halves cannot disagree and
leave a preview reading "did not build" with nothing to say about why.
"""


def state_home() -> Path:
  return state_dir("previews")


class Preview:
  """One held environment, and the process holding it."""

  #: Whether this install's `play` accepts `--policy`, asked once. Class-level
  #: because it is a property of the rl-mcp on disk, not of one preview.
  _policy_ok: Optional[bool] = None

  def __init__(self, task: str, env: TaskEnv, root: Optional[Path] = None,
               handle: str = "") -> None:
    self.task = task
    self.env = env
    self.handle = handle or task
    # Named for the key, not the task: playing a run and previewing its task
    # are two environments, and one directory between them would have the
    # second wiping the first's session on start.
    self.root = Path(root or state_home()) / safe_name(self.handle)
    self.session_dir = self.root / "rlmcp"
    self.log_path = self.root / "preview.log"
    self.proc: Optional[asyncio.subprocess.Process] = None
    self.started_at = 0.0
    self.stopped_at = 0.0
    self.exit_code: Optional[int] = None
    self.error = ""
    self.policy = "zero"
    self.checkpoint = ""
    self.mode = "viser"
    self.num_envs = DEFAULT_ENVS
    self.device = "cpu"
    self._lock = asyncio.Lock()

  # ── state ───────────────────────────────────────────────────────────────

  @property
  def running(self) -> bool:
    return self.proc is not None and self.proc.returncode is None

  def published(self) -> bool:
    """Whether the held environment is answering yet.

    Asked of rlmcp rather than of the filesystem: "is there a status file"
    would be the studio deciding what a live session looks like, and rl-mcp
    already weighs a pid and a heartbeat to answer exactly this. It is also
    the more useful question -- a session directory appears the moment the
    process starts, and the environment is not built for several seconds
    after that.
    """
    from rlmcp_studio.sources.session import LiveSession

    try:
      snapshot = LiveSession(self.session_dir).snapshot(metrics_tail=0)
    except Exception:
      return False
    return snapshot.get("state") == "running"

  def state(self) -> str:
    """One word for the panel, and they are not interchangeable.

    ``starting`` and ``holding`` look the same from the outside and are not:
    constructing a G1 on rough terrain takes seconds, and a panel that says
    "holding" while nothing has been built yet is the flicker this whole
    feature exists to replace with a state.

    ``stopped`` and ``failed`` are the same distinction at the other end, and
    it is the user's own action that tells them apart: a preview you ended is
    not a preview that died. See :data:`CLEAN_EXITS`.
    """
    if self.running:
      return "holding" if self.published() else "starting"
    if self.exit_code is None:
      return "off"
    return "stopped" if self.exit_code in CLEAN_EXITS else "failed"

  def tail(self, lines: int = LOG_TAIL) -> List[str]:
    try:
      text = self.log_path.read_text(errors="replace")
    except OSError:
      return []
    return text.splitlines()[-lines:]

  def describe(self) -> Dict[str, Any]:
    state = self.state()
    return {
        "task": self.task,
        "state": state,
        "session": str(self.session_dir) if state != "off" else "",
        "pid": self.proc.pid if self.proc else 0,
        # *When* it was built, not just for how long. The environment a preview
        # holds is the one that existed at this moment; an edit after it is a
        # file the panel is not showing you. See `code.newest_change`.
        "started_at": self.started_at if self.started_at else 0.0,
        "uptime_s": round(time.time() - self.started_at, 1) if self.running else 0.0,
        "exit_code": self.exit_code,
        "error": self.error,
        "policy": self.policy,
        "mode": self.mode,
        "checkpoint": self.checkpoint,
        "num_envs": self.num_envs,
        "device": self.device,
        "log": self.tail(),
        "where": self.env.describe(),
    }

  # ── lifecycle ───────────────────────────────────────────────────────────

  async def _policy_unsupported(self) -> str:
    """Empty if this install's `rlmcp play` can run without a checkpoint.

    Holding an *untrained* environment open needs `rlmcp play --policy`, which
    older rl-mcp releases lack, and the studio talks to whichever rl-mcp the
    task repo has. Asked of `--help` once per process and remembered: the
    alternative is spawning a run that dies on an unknown flag, whose only
    trace is a usage dump in a log nobody opens. A capability that is missing
    says so, in words about what to do.
    """
    if Preview._policy_ok is None:
      try:
        proc = await asyncio.create_subprocess_exec(
            *self.env.spawn_argv("play", "--help"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            env=self.env.environ())
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        Preview._policy_ok = b"--policy" in out
      except Exception:
        # Could not ask. Let the spawn be the answer rather than refusing on
        # the strength of a probe that failed for its own reasons.
        return ""
    if Preview._policy_ok:
      return ""
    return ("this rl-mcp's `play` needs a checkpoint: holding an untrained "
            "environment open needs `rlmcp play --policy`, which the rl-mcp in "
            f"{self.env.describe().get('python', 'this environment')} does not "
            "have. Upgrade rl-mcp in the task repository to preview an "
            "untrained task; a finished run's policy still plays.")

  async def start(self, policy: str = "zero", num_envs: int = DEFAULT_ENVS,
                  device: str = "cpu", checkpoint: str = "",
                  mode: str = "viser") -> Dict[str, Any]:
    """Build the task and hold it. Starting one that runs is not an error.

    ``mode`` is `play`'s: ``viser`` for the preview, which is a thing somebody
    is looking at, and ``hold`` for an environment that only has to *exist* --
    the verification suite's frames, where a viewer would allocate a port and a
    scene for a picture nobody is watching live. Both step the environment at
    its own clock and both answer every rlmcp command, which is why one class
    covers them.

    Two people pressing the same button, or a paint that fires twice, should
    not produce two environments on one GPU -- so an already-running preview
    is described rather than replaced. Changing *how* it runs is a restart,
    which is an explicit act.
    """
    async with self._lock:
      if self.running:
        return self.describe()

      # A checkpoint is the third policy: the same session, the same viewer,
      # weights instead of zeros. `play` does this in every rl-mcp -- it is
      # what the command was written for -- so watching a finished run and
      # watching a task being written are one mechanism, not two.
      self.checkpoint = checkpoint or ""
      self.policy = ("checkpoint" if self.checkpoint
                     else policy if policy in ("zero", "random") else "zero")
      # Zero means "do not say", and that is the right answer when playing a
      # trained policy: the task's own play configuration sized its contact
      # budget, and forcing one env under it can overflow `nconmax` on rough
      # terrain -- a task that trained fine refusing to be looked at. The
      # builder still asks for one, because there it is a task being *built*
      # and one robot constructs fastest.
      self.num_envs = max(0, int(num_envs))
      self.device = device or "cpu"
      self.mode = mode if mode in ("viser", "hold", "native") else "viser"
      self.error = ""
      self.exit_code = None

      # A fresh directory every start: a session is a record of one run of one
      # environment, and reusing the last one would leave the previous
      # traceback sitting under a preview that has since built.
      try:
        if self.root.exists():
          _wipe(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
      except OSError as exc:
        self.error = f"could not prepare {self.root}: {exc}"
        return self.describe()

      # How `rlmcp play` is actually spelled: the checkpoint is positional, and
      # the viewer mode is `viser`. An untrained preview is a different matter
      # -- `--policy` is missing from older rl-mcp releases -- so that path
      # is checked before spawning rather than handed to argparse, which
      # answers an unknown flag with a usage dump and exit code 2.
      if self.checkpoint:
        how = [self.checkpoint]
      else:
        missing = await self._policy_unsupported()
        if missing:
          self.error = missing
          return self.describe()
        how = ["--policy", self.policy]
      # `--task` stays even with a checkpoint: play can read the task from the
      # checkpoint's own session, but a checkpoint whose session has been
      # cleaned would otherwise fail with "could not tell which task", and we
      # know the answer.
      argv = self.env.spawn_argv(
          "play", *how, "--task", self.task, "--mode", self.mode,
          *(("--num-envs", str(self.num_envs)) if self.num_envs else ()),
          "--device", self.device,
          "--session-dir", str(self.session_dir),
      )
      try:
        handle = open(self.log_path, "wb")
      except OSError as exc:
        self.error = f"could not open {self.log_path}: {exc}"
        return self.describe()

      try:
        self.proc = await asyncio.create_subprocess_exec(
            *argv, stdout=handle, stderr=asyncio.subprocess.STDOUT,
            cwd=self.env.cwd, env=self.env.environ(),
            # Its own group, so stopping the preview cannot signal the studio.
            start_new_session=True)
      except Exception as exc:                    # noqa: BLE001 - reported, not raised
        self.error = f"could not start it: {exc}"
        self.proc = None
        return self.describe()
      finally:
        handle.close()

      self.started_at = time.time()
      asyncio.ensure_future(self._reap())
      return self.describe()

  async def _reap(self) -> None:
    """Remember how it ended, so `failed` can be told from `stopped`."""
    proc = self.proc
    if proc is None:
      return
    code = await proc.wait()
    if proc is self.proc:
      self.exit_code = code
      self.stopped_at = time.time()
      if code not in CLEAN_EXITS:
        self.error = self.error or f"the preview exited with code {code}"

  async def stop(self) -> Dict[str, Any]:
    """Ask it to stop the way a person would, then insist.

    `rlmcp stop` is the clean path: the session records its end and the
    environment closes itself. An env that never finished constructing has no
    loop to hear that, which is exactly when a preview needs stopping most --
    so the polite request has a deadline.
    """
    async with self._lock:
      if not self.running or self.proc is None:
        return self.describe()

      if self.published():
        try:
          from rlmcp_studio.sources import control

          await asyncio.wait_for(
              control.call(self.session_dir, "stop", timeout=STOP_GRACE / 2),
              timeout=STOP_GRACE / 2)
        except Exception:
          pass                                    # the deadline below covers it

      try:
        await asyncio.wait_for(self.proc.wait(), timeout=STOP_GRACE)
      except asyncio.TimeoutError:
        self.proc.terminate()
        try:
          await asyncio.wait_for(self.proc.wait(), timeout=4.0)
        except asyncio.TimeoutError:
          self.proc.kill()
      return self.describe()

  async def restart(self, **how: Any) -> Dict[str, Any]:
    """Stop and build again -- the only honest way to pick up a file change."""
    await self.stop()
    return await self.start(
        policy=how.get("policy", self.policy),
        num_envs=how.get("num_envs", self.num_envs),
        device=how.get("device", self.device),
        mode=how.get("mode", self.mode))


def _wipe(root: Path) -> None:
  import shutil

  shutil.rmtree(root, ignore_errors=True)


def latest_checkpoint(record: Any) -> str:
  """The checkpoint `rlmcp play` would choose for this record, or "".

  rlmcp already knows how to pick one -- latest by iteration, not by mtime, so
  a run that had an older checkpoint copied back in still plays the one it
  ended on. Asking it is the difference between agreeing with the CLI and
  inventing a second rule that disagrees on exactly the interesting cases.
  """
  session = getattr(record, "session", "") or ""
  if not session:
    return ""
  try:
    from rlmcp.play import find_checkpoint
    return str(find_checkpoint(Path(session)))
  except Exception:
    return ""


class Previews:
  """One preview per task, and the promise that they all end with the studio."""

  def __init__(self, env: TaskEnv, root: Optional[Path] = None) -> None:
    self.env = env
    self.root = root
    self._by_task: Dict[str, Preview] = {}

  def of(self, task: str, key: str = "") -> Preview:
    """One preview per key, defaulting to one per task.

    A run being played and a task being previewed are two environments that can
    want to be open at once -- and if they shared a key, opening one would
    silently describe the other as already running. The key is the run id when
    a run is being played and the task name otherwise.
    """
    handle = key or task
    if handle not in self._by_task:
      self._by_task[handle] = Preview(task, self.env, self.root, handle=handle)
    return self._by_task[handle]

  def describe_key(self, handle: str) -> Dict[str, Any]:
    """One preview by its key, without creating one that is off."""
    found = self._by_task.get(handle)
    return found.describe() if found else {
        "task": "", "state": "off", "session": "", "pid": 0,
        "started_at": 0.0, "policy": "", "checkpoint": "", "log": "",
    }

  def describe(self, task: str) -> Dict[str, Any]:
    """What this task's preview is doing, without creating one that is off."""
    found = self._by_task.get(task)
    if found is None:
      return {"task": task, "state": "off", "session": "", "pid": 0,
              "started_at": 0.0,
              "uptime_s": 0.0, "exit_code": None, "error": "", "log": [],
              "policy": "zero", "num_envs": DEFAULT_ENVS, "device": "cpu",
              "where": self.env.describe()}
    return found.describe()

  def live(self) -> List[Dict[str, Any]]:
    return [p.describe() for p in self._by_task.values() if p.running]

  def session_dirs(self) -> List[Path]:
    """Sessions this studio started itself.

    The path guard on the session route allows only the directories records
    name, which is right for a path arriving from a browser -- and leaves out
    the one kind of session this app creates rather than finds. These are not
    client-supplied: the studio chose them.
    """
    return [p.session_dir for p in self._by_task.values()]

  async def stop_all(self) -> None:
    """A preview that outlives the studio is a simulator nobody can see."""
    await asyncio.gather(*(p.stop() for p in self._by_task.values()),
                         return_exceptions=True)


__all__ = ["CLEAN_EXITS", "DEFAULT_ENVS", "Preview", "Previews", "state_home"]
