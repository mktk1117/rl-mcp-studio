"""Who may open a terminal.

Every other route in this studio reads. This one *executes*, and a studio on
localhost is reachable from any page in any tab. Those two facts together
would be remote code execution: without a guard, such a page could open a
WebSocket to `localhost:8730` and run commands as you, and you would never
know. The app-wide guard (`rlmcp_studio/guard.py`) covers `/api` and the live
feed; the terminal's socket is checked here instead, because the stakes are a
shell.

Two checks, and they cover different attackers:

* **Origin.** A browser sends `Origin` on a WebSocket handshake and a page
  cannot forge it. Refusing anything that is not this studio stops the
  drive-by case entirely -- a random site cannot connect at all.
* **A token.** Origin is a browser's promise, and a non-browser client makes no
  such promise: `curl` simply omits the header, and a native program can send
  whatever it likes. The token is what a caller must *know*, and it is only
  learnable from a same-origin response, which the browser's own rules keep
  from a cross-origin reader.

This is also the seam that survives real sign-in: the token then stops
being per-process and becomes the signed-in user's, and the Origin check stops
being "is this me" and becomes "is this the app" -- the shape of the check
stays the same.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Optional
from urllib.parse import urlsplit


class Guard:
  """One studio's terminal credential."""

  def __init__(self, host: str = "127.0.0.1", port: int = 8730,
               token: Optional[str] = None):
    self.token = token or secrets.token_urlsafe(32)
    """Per process. A studio you restarted is a studio whose old token is
    dead, which is the behaviour you want from something that grants a shell."""
    self.host = host
    self.port = port

  def allows_origin(self, origin: Optional[str]) -> bool:
    """Whether a handshake's `Origin` is this studio.

    A *missing* origin is refused rather than allowed. It is tempting to let it
    through for the sake of non-browser clients, and that is exactly the hole:
    the header's absence is what an attacker controls most easily.
    """
    if not origin:
      return False
    try:
      parsed = urlsplit(origin)
    except ValueError:
      return False
    if parsed.scheme not in ("http", "https"):
      return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    # The host is whatever this studio is reachable as. `0.0.0.0` binds
    # everything, so it cannot be compared against and only the port is
    # meaningful; a studio bound that wide has bigger questions than this one.
    if self.host not in ("0.0.0.0", "::", ""):
      if parsed.hostname not in (self.host, "localhost", "127.0.0.1", "::1"):
        return False
    return port == self.port

  def allows_token(self, given: Optional[str]) -> bool:
    """Constant-time, because this is a secret and the comparison is remote."""
    if not given:
      return False
    return hmac.compare_digest(str(given), self.token)

  def refuse(self, origin: Optional[str], token: Optional[str]) -> Optional[str]:
    """Why this handshake is not allowed, or None if it is.

    A reason, for the studio's own log. It is deliberately *not* sent to the
    caller: telling an unauthenticated client which of the two checks it failed
    is telling it how to pass.
    """
    if not self.allows_origin(origin):
      return f"origin {origin!r} is not this studio"
    if not self.allows_token(token):
      return "token missing or wrong"
    return None
