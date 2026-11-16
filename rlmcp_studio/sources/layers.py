"""The three layers of a comparison, kept apart and then joined on purpose.

A run differs from another in three ways -- the code it ran, the config it
launched with, and what somebody did to it while it trained -- and the reason
to keep them apart is that one number over all three is a number about nothing.

The failure this module exists to stop is the screen saying **"0 of 60 differ"
directly above "reward_scale 3.0 → 8.0"**. Both halves can be true: the weight
was equal in the two *launch* snapshots, and it was raised by hand mid-run.
Together they read as the app contradicting itself, because the count does not
say what it counted and nothing on screen connects the two.

So nothing here computes a new diff -- config comes from
:func:`rlmcp_studio.sources.code.config_diff`, code from the snapshots rl-mcp
stamps, interventions from ``rlmcp.records.interventions``. This is the small
amount of arithmetic that lets each layer state its own scope, plus the one
fact that lets a reader put them together: which of the fields the config
comparison covered did not stay at their launch value.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def annotate_config(config: Optional[Dict[str, Any]],
                    mine: Dict[str, Any],
                    theirs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
  """``config`` plus the facts its header needs to say what it compared.

  ``fields_here`` / ``fields_there``
    how many fields each side actually snapshotted. ``total`` is not a usable
    denominator: it is the *other* run's field count, so a run whose parent
    published no config at all would render as "64 of 0 differ".

  ``compared``
    the fields the two snapshots cover between them -- the honest denominator,
    and zero exactly when neither side recorded anything.

  ``moved_after_launch``
    the compared fields that a runtime edit moved, in either run. Non-empty is
    precisely the case where "identical" would be read as a statement about
    the runs rather than about their launch snapshots.

  ``runtime_known``
    whether both event logs could be read. A layer nobody looked at must not
    be reported as a layer where nothing happened.
  """
  if not config:
    return config

  keys = set(config.get("keys") or [])
  moved: List[str] = []
  for side in (mine, theirs):
    for key in side.get("parameters") or []:
      # Only fields the config comparison actually covered: a knob that exists
      # only at runtime is a fact about the run, not a caveat on this card.
      if key in keys and key not in moved:
        moved.append(key)

  return {**config,
          "compared": len(keys),
          "fields_here": len(keys) - len(config.get("removed") or []),
          "fields_there": len(keys) - len(config.get("added") or []),
          "moved_after_launch": sorted(moved),
          "runtime_known": bool(mine.get("ok")) and bool(theirs.get("ok"))}
