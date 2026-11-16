"""A shell on the studio's machine, behind a pty.

A pty rather than a pipe, and the difference is not cosmetic: without a
terminal on the other end a shell disables its prompt, tools drop their colour,
`less` refuses to page and anything that asks `isatty()` behaves differently
from how it behaves for you. A "terminal" made of pipes is a command runner
wearing the wrong name.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional
from uuid import uuid4

#: How much output one session keeps for a client that connects late or
#: reconnects. A terminal you reload and find empty is a terminal that has lost
#: what you were reading; this is what makes the drawer survive a repaint.
SCROLLBACK = 200_000

#: The most that is read from the pty in one go.
CHUNK = 65_536


class _Session:
  def __init__(self, pid: int, fd: int, cwd: str):
    self.pid = pid
    self.fd = fd
    self.cwd = cwd
    self.buffer = bytearray()
    self.queues: List[asyncio.Queue] = []
    self.closed = False

  def emit(self, data: bytes) -> None:
    self.buffer += data
    if len(self.buffer) > SCROLLBACK:
      del self.buffer[:len(self.buffer) - SCROLLBACK]
    for queue in list(self.queues):
      queue.put_nowait(data)


class LocalPty:
  """Shells forked on the machine the studio runs on."""

  name = "local"

  def __init__(self, shell: str = "", env: Optional[Dict[str, str]] = None):
    self.shell = shell or os.environ.get("SHELL") or "/bin/bash"
    self.env = dict(env or {})
    self.sessions: Dict[str, _Session] = {}

  def unavailable(self) -> Optional[str]:
    if not hasattr(os, "forkpty"):
      return "this platform has no pty support"
    if not Path(self.shell).exists():
      return f"no shell at {self.shell}"
    return None

  def describe(self) -> Dict[str, object]:
    reason = self.unavailable()
    return {"backend": self.name, "available": not reason, "reason": reason or "",
            "shell": self.shell, "sessions": sorted(self.sessions)}

  async def start(self, cwd: str = "", cols: int = 80, rows: int = 24) -> str:
    reason = self.unavailable()
    if reason:
      raise RuntimeError(reason)
    where = cwd if cwd and Path(cwd).is_dir() else str(Path.home())

    pid, fd = pty.fork()
    if pid == 0:                                  # the child; never returns
      try:
        os.chdir(where)
        environ = {**os.environ, **self.env,
                   # Say what we are, honestly: the drawer renders with
                   # xterm.js, which is what VS Code's terminal is. Claiming
                   # more than the renderer handles makes every tool emit
                   # sequences that arrive as rubbish, and claiming less
                   # throws away colour the renderer can draw.
                   "TERM": "xterm-256color",
                   "COLUMNS": str(cols), "LINES": str(rows)}
        os.execvpe(self.shell, [self.shell, "-i"], environ)
      except Exception:                           # noqa: BLE001
        os._exit(1)

    os.set_blocking(fd, False)
    session = _Session(pid, fd, where)
    handle = uuid4().hex[:12]
    self.sessions[handle] = session
    await self.resize(handle, cols, rows)

    loop = asyncio.get_running_loop()

    def pump() -> None:
      try:
        data = os.read(fd, CHUNK)
      except (BlockingIOError, InterruptedError):
        return
      except OSError:
        data = b""
      if not data:                                # the shell exited
        loop.remove_reader(fd)
        session.closed = True
        for queue in list(session.queues):
          queue.put_nowait(None)
        return
      session.emit(data)

    loop.add_reader(fd, pump)
    return handle

  async def write(self, session: str, data: str) -> None:
    held = self.sessions.get(session)
    if not held or held.closed:
      return
    try:
      os.write(held.fd, data.encode("utf-8", errors="replace"))
    except OSError:
      await self.close(session)

  async def resize(self, session: str, cols: int, rows: int) -> None:
    held = self.sessions.get(session)
    if not held or held.closed:
      return
    cols = max(20, min(500, int(cols or 80)))
    rows = max(5, min(200, int(rows or 24)))
    try:
      fcntl.ioctl(held.fd, termios.TIOCSWINSZ,
                  struct.pack("HHHH", rows, cols, 0, 0))
    except OSError:
      pass

  def where(self, session: str) -> Dict[str, str]:
    """What this shell is doing, for the tab that stands for it.

    Two facts, both read from `/proc` and both cheap: the directory the shell
    is in, and the name of the program it is running if it is running one. A
    strip of tabs reading "shell 1, shell 2, shell 3" is a strip you have to
    click through to find the one you meant -- which is the whole reason every
    other terminal labels them.

    `/proc` is Linux, and this is the same module that already assumes a pty
    and `termios`; where it is missing both facts come back empty and the tab
    falls back to its number rather than to a lie.
    """
    held = self.sessions.get(session)
    if not held or held.closed:
      return {"cwd": "", "busy": ""}
    cwd = ""
    busy = ""
    try:
      cwd = os.readlink(f"/proc/{held.pid}/cwd")
    except OSError:
      pass
    try:
      # The shell's children, if the kernel is built to answer: whatever it
      # forked and is waiting on is the thing worth naming. `comm` is the
      # short name -- `python3`, `sleep` -- which is what a tab has room for.
      kids = Path(f"/proc/{held.pid}/task/{held.pid}/children").read_text().split()
      if kids:
        busy = Path(f"/proc/{kids[-1]}/comm").read_text().strip()
    except OSError:
      pass
    return {"cwd": cwd, "busy": busy}

  # No "is it echoing?" query for the client to mask input with: the browser
  # sends raw keystrokes, so the pty echoes -- or does not -- and a password
  # typed at `sudo` is invisible for the same reason it is in any other
  # terminal. Nothing has to be told.

  def scrollback(self, session: str) -> bytes:
    held = self.sessions.get(session)
    return bytes(held.buffer) if held else b""

  async def read(self, session: str) -> AsyncIterator[bytes]:
    """Output from here on. The caller replays `scrollback` first if it wants
    what came before -- keeping the two separate is what lets a reconnecting
    client catch up without the stream having to remember who has seen what."""
    held = self.sessions.get(session)
    if not held:
      return
    queue: asyncio.Queue = asyncio.Queue()
    held.queues.append(queue)
    try:
      while True:
        chunk = await queue.get()
        if chunk is None:
          return
        yield chunk
    finally:
      if queue in held.queues:
        held.queues.remove(queue)

  async def close(self, session: str) -> None:
    held = self.sessions.pop(session, None)
    if not held:
      return
    held.closed = True
    try:
      asyncio.get_running_loop().remove_reader(held.fd)
    except Exception:                             # noqa: BLE001
      pass
    for queue in list(held.queues):
      queue.put_nowait(None)
    try:
      os.kill(held.pid, signal.SIGHUP)
    except OSError:
      pass
    try:
      os.close(held.fd)
    except OSError:
      pass
    # Reap, so a studio left open all day does not accumulate zombies.
    try:
      os.waitpid(held.pid, os.WNOHANG)
    except OSError:
      pass

  async def close_all(self) -> None:
    for handle in list(self.sessions):
      await self.close(handle)
