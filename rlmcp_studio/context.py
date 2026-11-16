"""The objects every route shares, built once per app.

`create_app` builds one :class:`Studio` and hands it to each routes module.
Nothing here registers a route: this is the state, and the few helpers more
than one area of the API needs to agree on -- where a task lives, which paths
a route may read, which remote sessions exist.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from rlmcp_studio.agents.antigravity import AntigravityProvider
from rlmcp_studio.agents.claude import ClaudeProvider
from rlmcp_studio.agents.codex import CodexProvider
from rlmcp_studio.agents.host import AgentHost
from rlmcp_studio.config import Config
from rlmcp_studio.events import set_host
from rlmcp_studio.guard import AppGuard
from rlmcp_studio.hub import Hub
from rlmcp_studio.runners import local as local_runner
from rlmcp_studio.runners import remote as remote_runner
from rlmcp_studio.sources import code as source_code
from rlmcp_studio.sources import gates as source_gates
from rlmcp_studio.sources import preview as source_preview
from rlmcp_studio.sources import registry as source_registry
from rlmcp_studio.sources import starting as source_starting
from rlmcp_studio.sources import taskenv as source_taskenv
from rlmcp_studio.sources import tb as source_tb
from rlmcp_studio.sources import verify as source_verify
from rlmcp_studio.sources.records import Library
from rlmcp_studio.terminals.guard import Guard
from rlmcp_studio.terminals.local import LocalPty


@dataclass
class Studio:
  """One studio process's shared state."""

  cfg: Config
  library: Library
  code_roots: List[Path]
  task_env: Any
  registry: Any
  previews: Any
  gates: Any
  verification: Any
  starting: Any
  runner: Any
  hub: Hub
  hosts: Any
  terminals: LocalPty
  guard: Guard
  app_guard: AppGuard
  agents: AgentHost
  tensorboard: Any
  """One reader for the process: it remembers where it got to in each file."""

  def known_addresses(self) -> Set[str]:
    """The remote sessions a route may touch: those a record names, and our own jobs'."""
    known = {str(row["session"]) for row in self.library.summaries() if row.get("session")}
    known |= {str(job.session_dir)
              for job in [*self.runner.jobs(), *self.hosts.jobs()] if job.session_dir}
    return known

  def watch_roots(self) -> List[Path]:
    """Records roots, plus the session directory of every run still open.

    Two different places, on purpose: records say what was claimed, sessions say
    what is happening. Watching only the first means the heartbeat never
    arrives, which looks exactly like a stalled UI.
    """
    return list(self.cfg.records) + self.library.live_session_dirs()

  def code_clock(self, scope: Dict[str, Any]) -> Dict[str, Any]:
    """When the task's files last changed, for the panels that hold a moment."""
    found = (source_code.newest_change(Path(scope["dir"])) if scope["dir"]
             else {"at": 0.0, "file": "", "files": 0})
    return {**found, "dir": scope["dir"], "package": scope["package"]}

  def task_scope(self, task: str) -> Dict[str, Any]:
    """Where a task lives, as far as anything here can tell.

    From the registry's own answer: the package whose import registered this
    task. `my_tasks.tasks` means the directory `<repo>/my_tasks`,
    which is the scope edits are free inside. A task the registry cannot place
    -- one that has not been written yet, or one the backend ships -- gets no
    free scope at all, and every edit asks. That is the safe direction to be
    wrong in.
    """
    begun = self.starting.get(task)
    if begun and begun.get("dir"):
      # Being written here: the directory is the one this studio made for it,
      # and nothing has registered it yet for the registry to name.
      where = Path(begun["dir"])
      return {"package": begun.get("package", ""),
              "dir": str(where) if where.is_dir() else "",
              "free": [str(where)] if where.is_dir() else [],
              "what": begun.get("what", "")}

    listing = self.registry.cached() or {}
    package = ""
    for row in listing.get("tasks", []):
      if row.get("task") == task:
        package = row.get("package") or ""
        break
    # Same reason as `api_new_task`: with no task repository a package name
    # would resolve against the studio's own working directory, and a
    # directory that happened to share the name would become a task's free
    # scope.
    root = Path(self.task_env.cwd) if self.task_env.cwd else None
    if not package or root is None:
      return {"package": package, "dir": "", "free": []}
    top = package.split(".")[0]
    where = root / top
    return {"package": package, "dir": str(where) if where.is_dir() else "",
            "free": [str(where)] if where.is_dir() else []}

  def artifact_path(self, path: str,
                    extra_roots: Iterable[Path] = ()) -> Optional[Path]:
    """One artifact path, confined to somewhere a run actually wrote.

    Resolved before the containment check, and shared by everything that reads
    a produced file -- serving it, and annotating it -- so there is one answer
    to "may this be read" rather than two that can drift apart.

    ``extra_roots`` is how the *serving* route reaches a picture that is not a
    run's output at all: a task report's `media/`, which lives beside the code
    it describes. Annotation deliberately does not pass it. An annotation says
    "this frame, from this run, is wrong", and a frame the run did not produce
    is a different kind of thing however readable it is -- widening the shared
    answer instead would turn that refusal into the wrong sentence, which
    `test_hostile_input.py` checks.
    """
    roots = [Path(p) for p in self.library.live_session_dirs()]
    roots += [Path(r) for r in self.cfg.records]
    roots += [Path(r) for r in extra_roots]
    # And where a verification keeps its frames. Not a session and not a
    # record: the studio made that directory itself, so no run names it and
    # the roots above cannot reach it. It is still the studio's own path and
    # never the caller's -- what arrives from the browser is checked against
    # it, exactly as everything else here is.
    roots += self.verification.roots()
    try:
      target = source_code.resolve_under(path, roots) if roots else None
    except source_code.OutsideRoot:
      target = None
    if target is None:
      # Fall back to any session named by a record: a finished run's artifacts
      # are still worth showing.
      for record in self.library.summaries():
        if record.get("session") and Path(path).resolve().is_relative_to(
            Path(record["session"]).resolve()):
          target = Path(path).resolve()
          break
    return target if target is not None and target.is_file() else None


def build(cfg: Config) -> Studio:
  """Every shared object, wired together, before any route exists."""
  library = Library(cfg.records)
  # One answer to "which environment can construct this task", shared by the
  # listing, the gates and the preview -- see sources/taskenv.py.
  code_roots = source_code.source_roots(cfg.records)
  task_env = source_taskenv.from_roots(code_roots, cfg.task_packages)
  registry = source_registry.Registry(cfg.task_packages, task_env.python,
                                      task_env.cwd or "")
  previews = source_preview.Previews(task_env)
  gates = source_gates.Gates(task_env)
  # The gates go in, deliberately: the suite's zero pass *is* the gate strip's
  # answer, and two panels on one screen disagreeing about whether a task
  # imports is the confusion the strip's own clock exists to prevent.
  verification = source_verify.Verification(task_env, gates)
  starting = source_starting.Starting()
  # A task being written outlives this process -- that is the whole point of
  # `starting` being on disk -- so the packages it names are re-learned at boot.
  # Otherwise restarting the studio, which is the thing you do when a task
  # package will not import, is also what makes the studio forget it exists.
  for begun in starting.all():
    task_env.learn(begun.get("package", ""))
    registry.learn(begun.get("package", ""))
  runner = local_runner.LocalRunner(
      task_env, records_root=str(cfg.primary_records or ""))
  hub = Hub()

  def _session_arrived(job: Any, address: str) -> None:
    """A remote run's session appeared on its host: write the address on the record.

    The trainer there was given no record id (runners/remote.py), so this is
    where `session` and the move to `running` come from for a remote run --
    the two facts the trainer writes itself when it is local.
    """
    if not job.spec.record_id:
      return
    record, source = library.find(job.spec.record_id, job.spec.task)
    if record is None or source is None:
      return

    def mutate(rec: Any) -> None:
      rec.session = address
      if rec.verdict == "planned":
        rec.verdict = "running"

    try:
      source.store.update_record(record.id, mutate)
    except Exception:  # noqa: BLE001 - a record that will not write is not the run's fault
      return
    try:
      loop = asyncio.get_running_loop()
      loop.create_task(hub.publish("records.changed", {"record": record.id}, record.id))
    except RuntimeError:
      pass

  def _config_arrived(job: Any, config: Dict[str, Any]) -> None:
    """A remote run's parameters are readable: the config snapshot, from the wire.

    What a local trainer writes on its record at its first iteration; a
    remote one has no record to write, so the studio reads the same values
    off the session's published parameters and writes them here, once."""
    if not job.spec.record_id:
      return
    record, source = library.find(job.spec.record_id, job.spec.task)
    if record is None or source is None or record.config:
      return

    def mutate(rec: Any) -> None:
      if not rec.config:
        rec.config = dict(config)

    try:
      source.store.update_record(record.id, mutate)
    except Exception:  # noqa: BLE001
      return

  # Training machines this studio may launch on, from the settings (the
  # studio knows its hosts; nothing is discovered). Empty means the studio's
  # own machine only; the local runner stays what the launch button uses unless a host
  # is named.
  hosts = remote_runner.Hosts(cfg.hosts, on_session=_session_arrived,
                              on_config=_config_arrived)

  # Every event carries `host` and `run`.
  # The host is the stable id of the machine the studio runs on; the run is looked up from whatever
  # directory a publisher named, by the library that knows whose it is.
  set_host(cfg.host_id)
  hub.resolve_run = library.record_for_path
  # A shell, and the credential that guards it. Every other route here reads;
  # this one executes, so its socket is guarded by a check of its own rather
  # than the app guard's -- see terminals/guard.
  terminals = LocalPty(env={"RLMCP_RECORDS": ":".join(str(r) for r in cfg.records)})
  guard = Guard(host=cfg.host, port=cfg.port)
  # And the credential that guards everything else (guard.py):
  # a cookie the page sets for a browser, a bearer token for everything that
  # is not one. Per process unless the environment or --token says otherwise.
  app_guard = AppGuard(host=cfg.host, port=cfg.port, token=cfg.token or None)
  # The saved model and mode, adopted at boot. Writing a preference to the
  # settings file and then ignoring it on the next start is the shape of bug
  # that makes a setting feel like it did not stick.
  _ui = dict(cfg.ui or {})
  # `auto` when nothing is saved: this studio is a place to watch an agent
  # drive a run and interrupt it, not a place to approve its shell commands
  # one at a time. A studio that opens in `manual` teaches a first-time user
  # that the agent is slow. The studio's own instructions unless somebody has
  # turned them off; `resolve_harness` is what a stale id lands on. Both are
  # studio preferences, so every provider gets the same ones.
  _mode = str(_ui.get("agentMode") or "auto-edit")
  _harness = str(_ui.get("agentHarness") or "studio")
  agents = AgentHost(
      hub.publish,
      # Conversations are filed under the records root: a run id counts from
      # 001 inside one task folder, so two studios would both have a run 007
      # and both read back the same transcript. See `agents.store.workspace_key`.
      records_root=str(cfg.primary_records or ""),
      # The fleet server (`agents/fleet_mcp.py`) calls this studio back over
      # HTTP for every tool, so the agent and the GUI can never disagree
      # about what is training. Where "this studio" is, and who it is.
      studio_url=f"http://{cfg.host}:{cfg.port}",
      studio_token=app_guard.token,
      host_id=cfg.host_id,
      providers={
          "claude": ClaudeProvider(
              model=str(_ui.get("agentModel") or ""),
              mode=_mode, harness=_harness),
          "codex": CodexProvider(
              model=str(_ui.get("codexModel") or ""),
              mode=_mode, harness=_harness),
          # Optional in two senses: the package is an extra, and the panel
          # says what is missing rather than the studio failing to start.
          "antigravity": AntigravityProvider(
              model=str(_ui.get("antigravityModel") or ""),
              mode=_mode, harness=_harness,
              # The SDK with a key is the only way in; a settings file from
              # an older studio may still say `account`, and the provider
              # falls back to `key` rather than refusing to start.
              auth=str(_ui.get("antigravityAuth") or "key")),
      },
      current=str(_ui.get("agentProvider") or "claude"))

  return Studio(
      cfg=cfg, library=library, code_roots=code_roots, task_env=task_env,
      registry=registry, previews=previews, gates=gates,
      verification=verification, starting=starting, runner=runner, hub=hub,
      hosts=hosts, terminals=terminals, guard=guard, app_guard=app_guard,
      agents=agents, tensorboard=source_tb.Reader())
