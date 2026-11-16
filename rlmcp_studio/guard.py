"""Who may use this studio at all -- the app-wide guard.

Without a guard, every route in this app would be reachable, without
authentication, by any page in any tab the user has open. The terminal has its
own guard (`terminals/guard.py`) because it *executes*, but the studio also
edits code, launches training and stops runs, so "any page in any tab" is too
many people for every route. This is the same two checks as the terminal's,
widened to the whole app, and shaped so it survives more than one user: today the token is
per process and stands for the one implicit user; with real sign-in it
becomes the signed-in user's, and the check does not change shape.

**How a browser gets in without anybody typing anything.** `GET /` sets the
token as an HttpOnly, SameSite=Strict cookie. Every same-origin fetch and
the WebSocket handshake carry it from then on, so the client's JavaScript
knows nothing about it -- a page in another tab cannot read it, and a
cross-site page cannot send it. State-changing requests must also carry an
`Origin` that is this studio, which is what the terminal already required.

**How everything else gets in.** A bearer token: `Authorization: Bearer …`.
The token is `$RLMCP_STUDIO_TOKEN` when set (a container, a test), else the
one already in `$XDG_STATE_HOME/rlmcp-studio/token` (`token-<port>` off the
default port), minted and written there (mode 0600) only when none is. Kept
across restarts so an open tab survives one, and written down so the studio's
own local tools -- a second `rlmcp-studio` asking whether one is already up, the fleet MCP server the
agent runs, `scripts/fresh-install.sh` -- can present it without being told.

A refused request gets a 401 with one sentence and no hint about which of
the two checks it failed.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Optional

from rlmcp_studio.paths import state_dir
from rlmcp_studio.terminals.guard import Guard

COOKIE = "rlmcp_studio"
"""The cookie's name *prefix*; the port is appended (`cookie_name`).

Browsers scope cookies by host and ignore the port, so two studios on
127.0.0.1 -- say one on 8730 and a scratch one on 8731 -- would share one
cookie under a bare name and overwrite each other's token on every visit.
The tab that lost would get 401 on its next fetch, and the client's answer to
a 401 is to reload the page: over and over, mid-sentence, for as long as both
studios were in use."""
ENV = "RLMCP_STUDIO_TOKEN"
DEFAULT_PORT = 8730
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def cookie_name(port: int = DEFAULT_PORT) -> str:
  """This studio's cookie, told apart from another studio's on the same host."""
  return f"{COOKIE}_{int(port)}"


def token_file(port: int = DEFAULT_PORT) -> Path:
  """Where this studio leaves its token. The default port keeps the bare
  name every tool already reads; another port gets its own file, because
  two studios writing one file would leave the tools holding whichever wrote
  last."""
  return state_dir("token" if int(port) == DEFAULT_PORT else f"token-{int(port)}")


def read_token_file(port: int = DEFAULT_PORT) -> str:
  """The running studio's token, for a tool on the same machine. "" if none."""
  try:
    return token_file(port).read_text().strip()
  except OSError:
    return ""


class AppGuard:
  """One studio's credential, and the two checks every request passes."""

  def __init__(self, host: str = "127.0.0.1", port: int = 8730,
               token: Optional[str] = None, write_file: bool = True):
    # Reused across restarts, from the file the last process of this port
    # left. A token minted per process would invalidate every open tab on
    # every restart -- the tab's cookie would be the old token, every fetch
    # would answer 401, and the page would reload itself to sign in again,
    # losing whatever was being typed. The file is 0600 in the user's own state directory, so
    # reusing it widens nothing: whoever can read it could already read the
    # next one. `write_file=False` (tests) still mints fresh.
    kept = read_token_file(port) if write_file and not (token or os.environ.get(ENV)) else ""
    self.token = token or os.environ.get(ENV) or kept or secrets.token_urlsafe(32)
    self.from_env = bool(token or os.environ.get(ENV))
    self.reused = bool(kept) and self.token == kept
    """Whether the token is the previous process's, so its cookies still work."""
    self.port = int(port)
    self.cookie_name = cookie_name(port)
    self._origin = Guard(host=host, port=port, token=self.token)
    self.file: Optional[Path] = None
    if write_file:
      self.file = self._write()

  def _write(self) -> Optional[Path]:
    """Leave the token where the studio's own local tools can find it, and nobody else."""
    path = token_file(self.port)
    try:
      path.parent.mkdir(parents=True, exist_ok=True)
      fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
      with os.fdopen(fd, "w") as handle:
        handle.write(self.token + "\n")
      os.chmod(path, 0o600)
      return path
    except OSError:
      return None

  # -- the checks --

  def presented(self, headers: Any, cookies: Any) -> Optional[str]:
    """The token a request carries, from the header or the cookie, or None."""
    auth = headers.get("authorization") or ""
    if auth.startswith("Bearer "):
      return auth[7:].strip()
    try:
      return cookies.get(self.cookie_name)
    except Exception:
      return None

  def allows(self, method: str, headers: Any, cookies: Any) -> bool:
    if not self._origin.allows_token(self.presented(headers, cookies)):
      return False
    # A browser sends `Origin` on every state-changing request and on a
    # WebSocket handshake, and a page cannot forge it. A non-browser client
    # (curl, the fleet server) sends none, and the bearer token is what it
    # must know instead -- so an absent Origin is fine with a bearer token
    # and not with a cookie, which is the header a hostile page would ride.
    origin = headers.get("origin")
    auth = (headers.get("authorization") or "").startswith("Bearer ")
    if method.upper() in SAFE_METHODS and not origin:
      return True
    if origin:
      return self._origin.allows_origin(origin)
    return auth

  def refuse_socket(self, headers: Any, cookies: Any) -> Optional[str]:
    """Why a WebSocket handshake is not allowed, or None. Logged, never sent."""
    if not self._origin.allows_token(self.presented(headers, cookies)):
      return "token missing or wrong"
    origin = headers.get("origin")
    if origin and not self._origin.allows_origin(origin):
      return f"origin {origin!r} is not this studio"
    if not origin and not (headers.get("authorization") or "").startswith("Bearer "):
      return "no origin and no bearer token"
    return None

  def cookie(self) -> dict:
    """The Set-Cookie arguments for the page that lets a browser in."""
    return {"key": self.cookie_name, "value": self.token, "httponly": True, "samesite": "strict",
            "path": "/", "max_age": 60 * 60 * 24 * 30}
