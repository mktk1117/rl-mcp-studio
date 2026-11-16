"""Conversations that exist outside this studio: the apps' own session files.

Claude Code and Codex each keep every conversation as a JSONL file the moment
it happens -- Claude Code under ``~/.claude/projects/<cwd-slug>/<id>.jsonl``,
Codex under ``~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl``. The
studio does not own those, and does not need to: listing them is how the
agent panel offers to *continue* a conversation begun in a terminal or the
desktop app (`AgentHost.attach`), and the studio's own conversations are the
same kind of file, because the SDK it hosts is that CLI.

Read-only. Nothing here writes into an app's session directory, and nothing
here parses more of a file than its first user message, so listing a
directory of thousand-line transcripts costs one small read each.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

#: How many sessions a listing returns, newest first. A task directory that
#: has been worked in for a month has hundreds; the menu wants the recent few.
LIMIT = 30
#: How much of a file is read to find its first user message and its cwd.
HEAD_BYTES = 64 * 1024


def claude_home() -> Path:
  return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def codex_home() -> Path:
  return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def project_slug(cwd: str) -> str:
  """The directory name Claude Code files a cwd's sessions under.

  Observed, not documented: every character that is not a letter, digit or
  hyphen becomes a hyphen, so ``/srv/u/.codex/x_y`` is ``-srv-u--codex-x-y``.
  """
  return re.sub(r"[^A-Za-z0-9-]", "-", str(Path(cwd).resolve() if cwd else ""))


def _head_lines(path: Path):
  try:
    with path.open("rb") as handle:
      chunk = handle.read(HEAD_BYTES)
  except OSError:
    return
  for raw in chunk.split(b"\n"):
    if not raw.strip():
      continue
    try:
      yield json.loads(raw)
    except ValueError:
      continue


def _first_text(content: Any) -> str:
  if isinstance(content, str):
    return content
  if isinstance(content, list):
    for block in content:
      if isinstance(block, dict) and block.get("type") == "text":
        return str(block.get("text") or "")
  return ""


def _clip(text: str, n: int = 80) -> str:
  text = " ".join(str(text or "").split())
  return text if len(text) <= n else text[: n - 1] + "…"


def claude_sessions(cwd: str, limit: int = LIMIT) -> List[Dict[str, Any]]:
  """Claude Code sessions begun in ``cwd``, newest first.

  Each row: ``id``, ``at`` (last write, epoch seconds), ``title`` (the first
  thing the user said), ``via`` (``"terminal"``, ``"studio"`` for the SDK
  entrypoint this studio uses, or whatever the file says), ``bytes``.
  """
  if not cwd:
    return []
  folder = claude_home() / "projects" / project_slug(cwd)
  if not folder.is_dir():
    return []
  rows: List[Dict[str, Any]] = []
  for path in folder.glob("*.jsonl"):
    try:
      stat = path.stat()
    except OSError:
      continue
    if stat.st_size == 0:
      continue
    title, via, session_id = "", "", path.stem
    for line in _head_lines(path):
      if not isinstance(line, dict):
        continue
      if line.get("sessionId") and not session_id:
        session_id = str(line["sessionId"])
      if line.get("type") == "user" and not title:
        message = line.get("message") or {}
        # A tool result rides in a user message too; a person's words are text.
        text = _first_text(message.get("content"))
        if text and not text.startswith("<"):
          title = _clip(text)
          entry = str(line.get("entrypoint") or "")
          via = "studio" if entry.startswith("sdk") else ("terminal" if entry else "")
      if title:
        break
    rows.append({"id": session_id, "at": stat.st_mtime, "title": title,
                 "via": via, "bytes": stat.st_size, "provider": "claude",
                 "path": str(path)})
  rows.sort(key=lambda r: -r["at"])
  return rows[:limit]


def codex_sessions(cwd: str, limit: int = LIMIT) -> List[Dict[str, Any]]:
  """Codex sessions (rollouts) whose ``session_meta`` names ``cwd``, newest first.

  Every rollout on the machine is under one dated tree, so the match is by
  the cwd inside each file's first line; the tree is walked newest-day first
  and stops once ``limit`` rows are found.
  """
  if not cwd:
    return []
  root = codex_home() / "sessions"
  if not root.is_dir():
    return []
  want = str(Path(cwd).resolve())
  rows: List[Dict[str, Any]] = []
  files = sorted(root.glob("*/*/*/rollout-*.jsonl"), reverse=True)
  for path in files:
    if len(rows) >= limit:
      break
    meta: Optional[Dict[str, Any]] = None
    title = ""
    for line in _head_lines(path):
      if not isinstance(line, dict):
        continue
      if line.get("type") == "session_meta" and meta is None:
        meta = line.get("payload") or {}
        if str(meta.get("cwd") or "") != want:
          break
      elif meta is not None and line.get("type") == "event_msg":
        payload = line.get("payload") or {}
        if payload.get("type") == "user_message":
          title = _clip(payload.get("message") or "")
          break
    if meta is None or str(meta.get("cwd") or "") != want:
      continue
    try:
      stat = path.stat()
    except OSError:
      continue
    rows.append({"id": str(meta.get("id") or meta.get("session_id") or path.stem),
                 "at": stat.st_mtime, "title": title,
                 "via": str(meta.get("originator") or "codex"),
                 "bytes": stat.st_size, "provider": "codex", "path": str(path)})
  rows.sort(key=lambda r: -r["at"])
  return rows[:limit]


def sessions(cwd: str, limit: int = LIMIT) -> Dict[str, Any]:
  """Both apps' conversations about one working directory."""
  return {"cwd": cwd, "slug": project_slug(cwd) if cwd else "",
          "claude": claude_sessions(cwd, limit), "codex": codex_sessions(cwd, limit)}
