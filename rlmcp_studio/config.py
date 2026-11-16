"""Where the studio looks for things.

Resolution order is rlmcp's own, and deliberately so: an explicit argument
beats the environment, which beats the settings file, which beats the default.
The file sits below the environment because `$RLMCP_RECORDS` in a shell is a
statement about *this* session and a saved setting should not quietly disagree
with it -- see :mod:`rlmcp_studio.settings`. The GUI overrides by passing an
explicit value -- never by mutating ``os.environ`` -- so it sits at the top of
this order rather than fighting it, and the UI can honestly say it is
overriding ``$RLMCP_RECORDS`` instead of silently disagreeing with your shell.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from rlmcp_studio import settings as settings_file


def _split_paths(value: str) -> List[Path]:
  """``$RLMCP_LIBRARY`` is a list, so a local, a team and a public index coexist."""
  return [Path(p).expanduser() for p in value.split(os.pathsep) if p.strip()]


@dataclass
class Config:
  """Resolved roots for one studio process."""

  records: List[Path] = field(default_factory=list)
  """Roots to scan for run records. Each is passed to ``rlmcp.records.open_store``."""

  library: List[Path] = field(default_factory=list)
  """Roots holding published task cards. Empty until the library exists."""

  task_packages: List[str] = field(default_factory=list)
  """Modules whose import registers tasks, passed through to `rlmcp tasks`.

  Not a path and not discovered: it is the same `--task-package` rl-mcp takes,
  so a task in your own repository is listed here for the same reason it is
  trainable there. `$RLMCP_TASK_PACKAGES` reaches the subprocess on its own,
  through the environment, so this is only for naming one explicitly.
  """

  host: str = "127.0.0.1"
  port: int = 8730

  host_id: str = ""
  """The studio machine's name on the wire (`events.HOST`): stable across
  restarts, kept in the settings file as ``hostId``, minted on first start. Not
  the bind address above, which is where the studio listens, not who it is."""

  token: str = ""
  """The credential every request must carry (`guard.py`). Explicit
  argument or ``$RLMCP_STUDIO_TOKEN``; empty means mint one per process and
  leave it in the state directory for local tools on the same machine."""

  hosts: List[Dict[str, Any]] = field(default_factory=list)
  """Training machines this studio may launch on (`runners/remote.py`), each
  ``{name, url, token, python, cwd, log_root}``: ``hosts`` in the settings
  file, or ``$RLMCP_STUDIO_HOSTS`` as JSON. Nothing is discovered."""

  settings_path: Optional[Path] = None
  """Where the settings file is, whether or not it exists yet. The GUI shows
  it, because a setting you cannot find is a setting you cannot fix."""

  settings_error: str = ""
  """Why the settings file was ignored, if it was. Carried rather than raised:
  a studio that will not start because of a stray comma is worse than one that
  starts with defaults and says so."""

  ui: Dict[str, Any] = field(default_factory=dict)
  """The GUI's own remembered state, straight from the file. The server never
  reads it -- it stores it, so preferences follow the machine rather than one
  browser's local storage."""

  @property
  def primary_records(self) -> Optional[Path]:
    return self.records[0] if self.records else None

  def describe(self) -> dict:
    """What the UI shows in the roots control."""
    return {
        "records": [str(p) for p in self.records],
        "library": [str(p) for p in self.library],
        "task_packages": list(self.task_packages),
        "records_from_env": bool(os.environ.get("RLMCP_RECORDS")),
        "library_from_env": bool(os.environ.get("RLMCP_LIBRARY")),
        "settings_path": str(self.settings_path) if self.settings_path else "",
        "settings_error": self.settings_error,
    }


def resolve(
    records: Optional[List[str]] = None,
    library: Optional[List[str]] = None,
    host: Optional[str] = None,
    port: Optional[int] = None,
    task_packages: Optional[List[str]] = None,
    settings: Optional[str] = None,
    token: Optional[str] = None,
) -> Config:
  """Explicit argument, then environment, then the settings file, then default."""
  where = settings_file.path(settings)
  saved, problem = settings_file.load(where)

  if records:
    roots = [Path(r).expanduser() for r in records]
  elif os.environ.get("RLMCP_RECORDS"):
    roots = _split_paths(os.environ["RLMCP_RECORDS"])
  elif _paths(saved.get("records")):
    roots = _paths(saved.get("records"))
  else:
    roots = [Path("records").resolve()]

  if library:
    libs = [Path(r).expanduser() for r in library]
  elif os.environ.get("RLMCP_LIBRARY"):
    libs = _split_paths(os.environ["RLMCP_LIBRARY"])
  else:
    libs = _paths(saved.get("library"))

  # `$RLMCP_TASK_PACKAGES` is read here as well as reaching the subprocesses on
  # its own, because the studio's own registry needs the list to *list* tasks,
  # not only to import them.
  packages = ([m for m in (task_packages or []) if m]
              or _words(os.environ.get("RLMCP_TASK_PACKAGES"))
              or [m for m in (saved.get("taskPackages") or []) if m])

  return Config(
      records=[r.resolve() for r in roots],
      library=[l.resolve() for l in libs],
      task_packages=packages,
      host_id=_host_id(saved, where, problem),
      token=token or os.environ.get("RLMCP_STUDIO_TOKEN") or "",
      hosts=_hosts(saved),
      settings_path=where,
      settings_error=problem,
      ui=dict(saved.get(settings_file.UI_KEY) or {}),
      # Not the bare `$HOST`: zsh and some CI systems set it to the machine's
      # hostname, which would quietly bind a studio that can open a shell to
      # the network instead of loopback.
      host=host or os.environ.get("RLMCP_STUDIO_HOST") or saved.get("host") or "127.0.0.1",
      # `$PORT` between the flag and the default, so a supervisor that assigns
      # a port -- the editor's preview runner, a container, a PaaS -- can hand
      # one over without the command line having to know. Same order as
      # everything else here: explicit argument, environment, default.
      port=port or _env_port() or _int(saved.get("port")) or 8730,
  )


def _paths(value: Any) -> List[Path]:
  """A JSON list of path strings. Anything else is not a list of paths."""
  if not isinstance(value, list):
    return []
  return [Path(str(v)).expanduser() for v in value if str(v).strip()]


def _words(value: Optional[str]) -> List[str]:
  """`$RLMCP_TASK_PACKAGES`, which rl-mcp splits on the path separator."""
  return [m for m in (value or "").split(os.pathsep) if m.strip()]


def _host_id(saved: Dict[str, Any], where: Optional[Path], problem: str) -> str:
  """The studio machine's stable id, minted once and kept in the settings file.

  Read from ``hostId`` when the file has one. Otherwise minted and written
  back, so the next start says the same name -- unless the file could not be
  read (``problem``), in which case nothing is written over it and a derived
  id is used instead: the machine's own id when the OS has one, the hostname
  when it does not. Derived is stable too; it is only less private, which is
  why the minted form is preferred.
  """
  import hashlib
  import socket
  import uuid

  current = saved.get("hostId")
  if isinstance(current, str) and current.strip():
    return current.strip()
  if where is not None and not problem:
    minted = uuid.uuid4().hex[:12]
    _, failed = settings_file.patch({"hostId": minted}, where)
    if not failed:
      return minted
  seed = ""
  for candidate in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
    try:
      seed = Path(candidate).read_text().strip()
      break
    except OSError:
      continue
  seed = seed or socket.gethostname()
  return "h-" + hashlib.sha256(seed.encode()).hexdigest()[:12]


def _hosts(saved: Dict[str, Any]) -> List[Dict[str, Any]]:
  """Training machines: `$RLMCP_STUDIO_HOSTS` (JSON) over the file's `hosts`."""
  import json
  raw = os.environ.get("RLMCP_STUDIO_HOSTS")
  if raw:
    try:
      parsed = json.loads(raw)
    except ValueError:
      return []
    return [e for e in parsed if isinstance(e, dict)] if isinstance(parsed, list) else []
  found = saved.get("hosts")
  return [e for e in found if isinstance(e, dict)] if isinstance(found, list) else []


def _int(value: Any) -> Optional[int]:
  return value if isinstance(value, int) and 0 < value < 65536 else None


def _env_port() -> Optional[int]:
  """``$PORT``, when it is a number. A malformed one is ignored rather than
  crashing the server: falling back to the default starts, and a traceback
  about an environment variable nobody set by hand does not."""
  raw = os.environ.get("PORT", "").strip()
  if not raw.isdigit():
    return None
  value = int(raw)
  return value if 0 < value < 65536 else None
