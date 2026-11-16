"""``rlmcp-studio`` -- start the server and print where it is."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

import uvicorn

from rlmcp_studio.app import create_app
from rlmcp_studio import vendor
from rlmcp_studio.config import resolve


def main(argv: list[str] | None = None) -> int:
  parser = argparse.ArgumentParser(prog="rlmcp-studio", description=__doc__)
  parser.add_argument("--records", action="append",
                      help="a records root; repeat for several. Overrides $RLMCP_RECORDS.")
  parser.add_argument("--library", action="append",
                      help="a task-card root; repeat for several. Overrides $RLMCP_LIBRARY.")
  parser.add_argument("--task-package", action="append", metavar="MODULE",
                      help="a module whose import registers tasks, so tasks that "
                           "have never run are listed too. Repeatable. "
                           "$RLMCP_TASK_PACKAGES works as well.")
  parser.add_argument("--host", default=None)
  parser.add_argument("--port", type=int, default=None)
  parser.add_argument("--settings", default=None, metavar="PATH",
                      help="the settings file to read and write. Defaults to "
                           "$XDG_CONFIG_HOME/rlmcp-studio/settings.json; "
                           "$RLMCP_STUDIO_SETTINGS works as well.")
  parser.add_argument("--token", default=None,
                      help="the credential every request must carry. Defaults to "
                           "$RLMCP_STUDIO_TOKEN, else one minted per process and "
                           "left in $XDG_STATE_HOME/rlmcp-studio/token for local "
                           "tools on the same machine. A browser gets it from the "
                           "page.")
  parser.add_argument("--reload", action="store_true")
  parser.add_argument("--open", action="store_true",
                      help="open the studio in a browser once it is up. What "
                           "the desktop launcher passes.")
  parser.add_argument("--check", action="store_true",
                      help="do not start; say whether this install can do the "
                           "job (its own files, rl-mcp, a GPU, an agent "
                           "credential, a records root) and how to fix what is "
                           "missing. Exit 1 only when the studio could not "
                           "start at all.")
  args = parser.parse_args(argv)

  cfg = resolve(records=args.records, library=args.library,
                host=args.host, port=args.port,
                task_packages=args.task_package, settings=args.settings,
                token=args.token)

  if args.check:
    from rlmcp_studio import doctor
    findings = doctor.run(cfg)
    print(f"rlmcp studio {_version()}  ({doctor.HERE})", flush=True)
    print(doctor.render(findings), flush=True)
    return 0 if doctor.healthy(findings) else 1

  # Starting a second one is the commonest thing to do by accident -- clicking
  # the launcher twice, or forgetting the tab already open -- and the honest
  # answer is not a traceback about a bound port. If a studio already holds
  # this one, that *is* the studio: say where, open it if asked, and stop.
  # Anything else on the port still gets uvicorn's own error, because "some
  # other program is using 8730" is a different problem with a different fix.
  serving = already_serving(cfg.host, cfg.port)
  if serving:
    print(f"rlmcp studio is already running  {serving}", flush=True)
    if args.open:
      webbrowser.open(serving)
    return 0

  missing = [str(p) for p in cfg.records if not p.exists()]
  if missing:
    print(f"warning: records root does not exist: {', '.join(missing)}",
          file=sys.stderr, flush=True)

  # `flush=True` because this is as often redirected as watched: started under
  # nohup, piped into a log, run from a script. Python buffers a pipe, so
  # without it the log stays empty until the buffer fills or the process
  # exits -- and the one thing you want from that log is the line saying the
  # server came up.
  print(f"rlmcp studio  http://{cfg.host}:{cfg.port}", flush=True)
  for root in cfg.records:
    print(f"  records  {root}", flush=True)
  if cfg.settings_path:
    print(f"  settings {cfg.settings_path}", flush=True)
  if cfg.settings_error:
    print(f"warning: {cfg.settings_error}", file=sys.stderr, flush=True)

  # Ids are per-store, so two roots both numbering from 001 make an id
  # ambiguous. The app disambiguates by task, which covers the rail and every
  # view reached from it; anything that asks by id alone still gets the first
  # match, so it is worth knowing when that case exists at all.
  if len(cfg.records) > 1:
    from rlmcp_studio.sources.records import Library
    clashes = Library(list(cfg.records)).ambiguous_ids()
    if clashes:
      ids = ", ".join(sorted(clashes)[:8])
      more = "" if len(clashes) <= 8 else f" (+{len(clashes) - 8} more)"
      print(f"  note     {len(clashes)} record id(s) exist in more than one "
            f"root: {ids}{more}", file=sys.stderr, flush=True)

  # A wheel carries the editor and terminal libraries; a source checkout
  # fetches them once into the user cache. Without them the studio still
  # runs -- only the code editor and the terminal drawer cannot open.
  if vendor.directory() is None:
    print(f"  fetching {vendor.stamp()} into {vendor.cache_dir()}", flush=True)
    try:
      vendor.ensure(timeout=30)
    except Exception as exc:
      print(f"warning: could not fetch the editor and terminal libraries ({exc}); "
            "the code editor and terminal will not open. Retry with "
            "`python -m rlmcp_studio.vendor`.", file=sys.stderr, flush=True)

  if args.open:
    _open_when_up(f"http://{cfg.host}:{cfg.port}")

  uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port, log_level="warning")
  return 0


def _version() -> str:
  try:
    from importlib.metadata import version
    return version("rl-mcp-studio")
  except Exception:
    return "(unpackaged)"


def already_serving(host: str, port: int, timeout: float = 1.0) -> str:
  """The url of a studio already on this address, or "".

  Asks `/api/config` rather than just opening a socket: something is listening
  is not the same as *this* is listening, and opening a browser at another
  program's port would be worse than the error it replaced.
  """
  url = f"http://{host}:{port}"
  # The running studio guards /api; its token is in the environment or left
  # in the state directory for exactly this kind of caller (guard.py).
  from rlmcp_studio import guard as app_guard
  token = os.environ.get(app_guard.ENV) or app_guard.read_token_file(port)
  request = urllib.request.Request(
      f"{url}/api/config",
      headers={"authorization": f"Bearer {token}"} if token else {})
  try:
    with urllib.request.urlopen(request, timeout=timeout) as answer:
      payload = json.load(answer)
  except urllib.error.HTTPError as exc:
    # 401 is still an answer: a studio *is* there, with a token we lack.
    return url if exc.code == 401 else ""
  except Exception:
    return ""
  return url if isinstance(payload, dict) and "roots" in payload else ""


def _open_when_up(url: str, within: float = 20.0) -> None:
  """Open a browser once the server answers, in the background.

  Polled rather than opened straight away: uvicorn binds after this returns,
  and a tab that loads before the server is up shows a connection error the
  user then has to reload past.
  """
  def wait() -> None:
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
      if already_serving(*_split(url), timeout=0.4):
        webbrowser.open(url)
        return
      time.sleep(0.25)

  threading.Thread(target=wait, daemon=True).start()


def _split(url: str) -> tuple:
  host, _, port = url.rsplit("//", 1)[1].partition(":")
  return host, int(port)


if __name__ == "__main__":
  raise SystemExit(main())
