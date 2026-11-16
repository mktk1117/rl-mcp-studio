"""Where the studio keeps what it writes for itself, and how a name becomes a file.

One place for both, because either rule spelled out again wherever it is
needed -- the XDG lookup, the filename rule -- leaves copies, and a copy is
where the next change to one of them is forgotten.
"""

from __future__ import annotations

import os
from pathlib import Path


def state_dir(*parts: str) -> Path:
  """`$XDG_STATE_HOME/rlmcp-studio/...`: state that is not configuration or cache."""
  base = os.environ.get("XDG_STATE_HOME") or (Path.home() / ".local" / "state")
  return Path(base, "rlmcp-studio", *parts)


def safe_name(name: str) -> str:
  """A task or handle, as one path segment that cannot climb or collide with a separator."""
  return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)[:120]
