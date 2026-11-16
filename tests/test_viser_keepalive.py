"""The 3-D frame survives a robot-mode switch (Live 3-D → Over training → back).

Read from the source the way the other client tests are: the frame is a
child of the card in one position whatever the mode, hidden rather than
removed, because the reconciler moves a keyed node that changed place and a
moved iframe reloads -- a reconnect and the camera you placed, each time.
"""

from __future__ import annotations

import re
from pathlib import Path
from tests.client_source import read_css, read_js

WEB = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web"


def _fn(source: str, name: str) -> str:
  return source.split(f"function {name}(", 1)[1].split("\n}", 1)[0]


def test_the_frame_is_the_cards_own_child_in_every_mode():
  components = read_js("components/robot.js")
  card = _fn(components, "robotCard")
  assert 'viserSlot(view.url, "live", { hidden: mode !== "live" })' in card
  # Before the mode ternary, not inside one branch of it.
  assert card.index("viserSlot(") < card.index('mode === "now" ? framesCard(')
  live = _fn(components, "liveViewBody")
  assert "viserSlot(" not in live, "the body no longer renders its own frame"


def test_a_hidden_host_takes_no_space_and_keeps_its_frame():
  components = read_js("components/robot.js")
  slot = _fn(components, "viserSlot")
  assert 'hidden: hidden ? "" : null' in slot
  assert '"data-keep-children": ""' in slot, "the reconciler leaves the frame alone"
  css = read_css()
  assert re.search(r"\.viser-host\[hidden\]\s*\{\s*display:\s*none", css), \
      "`display: grid` on the class would otherwise beat the attribute"
