"""The settings file: what this studio remembers between runs.

Without a file, the studio's memory would be split in two places neither of
which you can open. Where the records are would live on the command line, to be
retyped or baked into a desktop entry nobody would think to look in. How the
studio looks -- theme, layout, panel sizes -- would live in `localStorage`, so
it would belong to one *browser*: the same machine, a different browser, and
every panel back to its default.

So there is a third thing, the one an editor has: a JSON file, read at startup,
rewritten when you change a setting in the GUI.

**Precedence**, the same order `config.resolve` follows::

    explicit argument  >  environment  >  settings file  >  default

The file sits below the environment on purpose. `$RLMCP_RECORDS` in a shell is
a statement about *this* session, and a saved setting should not quietly
disagree with it -- the same reason `code .` opens the folder you named rather
than the one in your settings. What the GUI writes is therefore what you get on
the next start *unless* something more immediate says otherwise, and the app
can say which, because `describe()` reports where each answer came from.

**Writes are atomic.** A settings file half-written by a crash is worse than no
settings file: the next start would fail to parse it and fall back to defaults
with no explanation. Write a sibling temporary file and rename, which is atomic
on every filesystem this runs on.

**A broken file is not fatal.** Unparseable settings are reported and ignored
rather than raised: the studio starting with default panel sizes and a warning
is recoverable, and a studio that will not start because of a stray comma is
somebody editing JSON by hand at the worst possible moment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

#: Keys the server acts on. Anything else in the file is carried through
#: untouched -- a setting this version does not know about belongs to a version
#: that does, and dropping it on the first save would be a downgrade nobody
#: asked for.
SERVER_KEYS = ("records", "library", "taskPackages", "host", "port")

#: The GUI's own state, under one key so it is obvious which half of the file
#: the browser owns. Free-form on purpose: the client adds a preference far
#: more often than the server grows a root, and each one should not need a
#: change here to be remembered.
UI_KEY = "ui"


def path(explicit: Optional[str] = None) -> Path:
  """Where the settings file is.

  `$XDG_CONFIG_HOME` before `~/.config`, because a machine that sets it means
  it -- and this is exactly the kind of file it is for.
  """
  if explicit:
    return Path(explicit).expanduser()
  if os.environ.get("RLMCP_STUDIO_SETTINGS"):
    return Path(os.environ["RLMCP_STUDIO_SETTINGS"]).expanduser()
  base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
  return Path(base).expanduser() / "rlmcp-studio" / "settings.json"


def load(file: Optional[Path] = None) -> Tuple[Dict[str, Any], str]:
  """The settings, and why they are empty if they are.

  Returns ``({}, "")`` when there is simply no file yet, which is the normal
  state on a fresh machine and not worth a word to anybody. A file that exists
  and cannot be read comes back with the reason, for whoever can act on it.
  """
  target = file or path()
  try:
    raw = target.read_text()
  except FileNotFoundError:
    return {}, ""
  except OSError as exc:
    return {}, f"could not read {target}: {exc}"

  try:
    loaded = json.loads(raw or "{}")
  except json.JSONDecodeError as exc:
    return {}, f"{target} is not valid JSON: {exc}"

  if not isinstance(loaded, dict):
    return {}, f"{target} should hold a JSON object, not {type(loaded).__name__}"
  return loaded, ""


def save(values: Dict[str, Any], file: Optional[Path] = None) -> str:
  """Write the whole settings document. Returns "" or why it did not.

  Atomic: a temporary sibling and a rename, so a crash mid-write leaves the
  previous settings rather than half of the new ones.
  """
  target = file or path()
  try:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    # `sort_keys` so a file a human edits does not have its lines shuffled by
    # the next save, and a trailing newline so it ends like every other text
    # file on the machine.
    temporary.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
    temporary.replace(target)
  except OSError as exc:
    return f"could not write {target}: {exc}"
  return ""


def patch(changes: Dict[str, Any], file: Optional[Path] = None
          ) -> Tuple[Dict[str, Any], str]:
  """Merge `changes` into the file and save it. Returns the new settings.

  Shallow at the top level and one deep into ``ui``, which is exactly as deep
  as the document goes. The GUI sends the setting it changed, not the document
  it believes in -- two tabs open on the same studio would otherwise each save
  their own idea of every other setting, and the last one to touch anything
  would win everything.
  """
  current, problem = load(file)
  if problem:
    # Refuse rather than overwrite: the file says something this process could
    # not read, and replacing it with a merge of "nothing" and one changed
    # setting would delete whatever the user meant by it.
    return current, problem

  merged = dict(current)
  for key, value in changes.items():
    if key == UI_KEY and isinstance(value, dict):
      ui = dict(merged.get(UI_KEY) or {})
      for ui_key, ui_value in value.items():
        # `headline` is a map by task, and the client sends one task's entry
        # at a time (null clears it), so two tabs on two tasks never overwrite
        # each other's choice. One level deeper than the rest, on purpose.
        if ui_key == "headline" and isinstance(ui_value, dict):
          per_task = dict(ui.get("headline") or {})
          for task, prefs in ui_value.items():
            if prefs is None:
              per_task.pop(task, None)
            elif isinstance(prefs, dict):
              per_task[task] = prefs
          ui["headline"] = per_task
        else:
          ui[ui_key] = ui_value
      merged[UI_KEY] = ui
    else:
      merged[key] = value

  return merged, save(merged, file)
