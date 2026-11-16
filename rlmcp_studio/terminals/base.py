"""What a terminal is to this studio, independent of where it runs.

The local implementation forks a pty on the studio's machine. A studio whose
trainer runs on another machine cannot, and the wire that carries telemetry
from that machine is what would carry a terminal. So the protocol below is the
seam, shaped for the wire from the start rather than retrofitted, because the
shape of a thing you can only run locally is not the shape of one you can also
run over a wire.

Three properties the protocol is built for, all of which matter only remotely:

* **Bytes, not lines.** A pty is a byte stream with no framing, and anything
  that assumes lines breaks on a progress bar or a prompt with no newline.
* **Backpressure is the caller's.** `read` yields what has arrived; a consumer
  that cannot keep up is not the terminal's problem to buffer forever.
* **Every operation names a session.** One studio holds several terminals, and
  a remote backend has no ambient "current" one to fall back on.
"""

from __future__ import annotations

from typing import AsyncIterator, Dict, Optional, Protocol


class TerminalBackend(Protocol):
  """Somewhere a shell can run."""

  name: str

  async def start(self, cwd: str = "", cols: int = 80, rows: int = 24) -> str:
    """Open a session and return its id."""

  async def write(self, session: str, data: str) -> None:
    """Keystrokes in."""

  async def resize(self, session: str, cols: int, rows: int) -> None:
    """The window changed shape."""

  def read(self, session: str) -> AsyncIterator[bytes]:
    """Output, as it arrives."""

  async def close(self, session: str) -> None:
    """End it. Idempotent -- a session that is already gone is closed."""

  def describe(self) -> Dict[str, object]:
    """What this backend is, for the panel to say."""


class NoTerminal:
  """The backend a studio has where no pty can be forked.

  Not an exception and not a silent no-op: a panel that offers a terminal and
  then does nothing is worse than one that says why it cannot. `unavailable`
  is the reason, in words meant for the person reading it.
  """

  name = "none"

  def __init__(self, reason: str = "no terminal backend on this studio"):
    self.reason = reason

  def unavailable(self) -> Optional[str]:
    return self.reason

  def describe(self) -> Dict[str, object]:
    return {"backend": self.name, "available": False, "reason": self.reason}
