"""Signing the agent in, without asking anyone to open a terminal.

The studio never handles a credential itself. It runs `claude auth login`,
carries the authorize URL out to the browser, and carries the one-time code the
user gets back in to that same local process. The code goes to the CLI on the
machine the studio runs on and nowhere else -- not to the studio's storage, not
to any service the studio chose.

`claude auth login` behaves well without a TTY: it prints the URL, then waits
on stdin for the code. That is the whole reason this can be a button.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import time
from typing import Any, Dict, Optional

URL_PATTERN = re.compile(r"https://\S+")

URL_TIMEOUT_S = 25.0
LOGIN_TIMEOUT_S = 600.0
"""How long a half-finished sign-in may sit before it is abandoned.

Long, because the user is off in a browser and may be interrupted -- but not
unbounded, because a stalled subprocess holding a pipe is a leak.
"""


def cli_present() -> bool:
  return bool(shutil.which("claude"))


EXPIRY_PATTERN = re.compile(
    r"oauth[^.\n]{0,40}token[^.\n]{0,24}expired"
    r"|re-?authenticate to continue"
    r"|failed to authenticate"
    r"|401 +(unauthorized|oauth)"
    r"|please run\s+`?/login`?"
    r"|invalid bearer token",
    re.I)
"""The sentences that mean *this session's credential is no longer good*.

Written against the text the CLI sends -- `Failed to authenticate. API Error:
401 OAuth access token has expired. Re-authenticate to continue.` -- which
arrives as an ordinary assistant message. Drawn as one, it would tell the user
the agent has to be signed in again and give no way to do it, in an app that has
a whole sign-in flow one panel away.
"""

EXPIRY_LIMIT = 300
"""Longest message this is asked about.

The failure is a one-line replacement for a whole turn. An agent that *quotes*
one of these sentences -- reading `auth.py`, say, or explaining this feature -- is
writing a paragraph, and a paragraph must not put a sign-in card in the middle
of a working conversation.
"""


def looks_expired(text: str) -> bool:
  """Is this the provider saying the credential is gone, rather than talking?"""
  text = (text or "").strip()
  if not text or len(text) > EXPIRY_LIMIT:
    return False
  return bool(EXPIRY_PATTERN.search(text))


async def status() -> Dict[str, Any]:
  """Ask the CLI, rather than guessing from a file on disk.

  A credentials file exists long after its session has expired, so its presence
  proves nothing. `claude auth status --json` is the real answer.

  ``answered`` says whether the CLI actually replied. **"I could not ask" is not
  "you are signed out"**: if a subprocess that timed out were read as
  `loggedIn: False`, the panel would show signed out and the composer would
  disable itself -- mid-sentence, throwing away focus. Callers keep their last
  real answer when this is false.
  """
  if not cli_present():
    return {"loggedIn": False, "authMethod": "none", "answered": True,
            "reason": "the `claude` CLI is not on PATH"}
  try:
    proc = await asyncio.create_subprocess_exec(
        "claude", "auth", "status", "--json",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=20)
    payload = json.loads(out.decode() or "{}")
    payload.setdefault("loggedIn", False)
    payload["answered"] = True
    return payload
  except asyncio.TimeoutError:
    return {"loggedIn": False, "authMethod": "none", "answered": False,
            "reason": "`claude auth status` timed out"}
  except Exception as exc:
    return {"loggedIn": False, "authMethod": "none", "answered": False,
            "reason": str(exc)}


class LoginFlow:
  """One sign-in attempt, driven from the GUI.

  Deliberately single-flight: two overlapping OAuth flows would race for the
  same credential store, and the second URL would silently invalidate the
  first.
  """

  def __init__(self) -> None:
    self.proc: Optional[asyncio.subprocess.Process] = None
    self.url: str = ""
    self.mode: str = ""
    self.started_at: float = 0.0
    self.error: str = ""
    self.output: str = ""

  @property
  def running(self) -> bool:
    return self.proc is not None and self.proc.returncode is None

  def describe(self) -> Dict[str, Any]:
    return {
        "running": self.running,
        "url": self.url,
        "mode": self.mode,
        "error": self.error,
        "waiting_for_code": bool(self.running and self.url),
        "age_s": round(time.time() - self.started_at, 1) if self.started_at else 0,
    }

  async def start(self, mode: str = "claudeai") -> Dict[str, Any]:
    if self.running:
      return self.describe()
    if not cli_present():
      self.error = "the `claude` CLI is not on PATH"
      return self.describe()

    flag = "--console" if mode == "console" else "--claudeai"
    self.mode, self.url, self.error, self.output = mode, "", "", ""
    self.started_at = time.time()

    self.proc = await asyncio.create_subprocess_exec(
        "claude", "auth", "login", flag,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT)

    # Read until the URL appears. It is not newline-terminated -- the CLI
    # follows it with a prompt and then blocks -- so read chunks, not lines.
    deadline = time.time() + URL_TIMEOUT_S
    while time.time() < deadline and not self.url:
      try:
        chunk = await asyncio.wait_for(self.proc.stdout.read(4096), timeout=2.0)
      except asyncio.TimeoutError:
        continue
      if not chunk:
        break
      self.output += chunk.decode(errors="replace")
      match = URL_PATTERN.search(self.output)
      if match:
        self.url = match.group(0).rstrip(".,)")

    if not self.url:
      self.error = "the CLI did not print a sign-in URL"
      await self.cancel()
    return self.describe()

  async def submit_code(self, code: str) -> Dict[str, Any]:
    """Hand the user's one-time code to the CLI process, and only to it."""
    if not self.running or self.proc is None or self.proc.stdin is None:
      self.error = "no sign-in is in progress"
      return self.describe()
    code = (code or "").strip()
    if not code:
      self.error = "no code given"
      return self.describe()
    try:
      self.proc.stdin.write((code + "\n").encode())
      await self.proc.stdin.drain()
      rest = await asyncio.wait_for(self.proc.stdout.read(), timeout=60)
      self.output += rest.decode(errors="replace")
      await asyncio.wait_for(self.proc.wait(), timeout=30)
    except asyncio.TimeoutError:
      self.error = "the CLI did not finish signing in"
    except Exception as exc:
      self.error = str(exc)
    finally:
      returncode = self.proc.returncode if self.proc else None
      if returncode not in (0, None) and not self.error:
        # Show the CLI's own words: it explains a bad code better than we can.
        self.error = self.output.strip().splitlines()[-1] if self.output.strip() else \
            f"sign-in failed (exit {returncode})"
      self.proc = None
      self.url = ""
    return self.describe()

  async def cancel(self) -> Dict[str, Any]:
    if self.proc is not None and self.proc.returncode is None:
      try:
        self.proc.terminate()
        await asyncio.wait_for(self.proc.wait(), timeout=5)
      except Exception:
        pass
    self.proc = None
    self.url = ""
    return self.describe()

  async def reap_if_stale(self) -> None:
    if self.running and time.time() - self.started_at > LOGIN_TIMEOUT_S:
      self.error = "sign-in timed out"
      await self.cancel()
