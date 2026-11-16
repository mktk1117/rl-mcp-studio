"""The client's source, read as text by the tests that check it.

The client is plain ES modules and stylesheets with no build step, so these
tests read what the browser loads. A module is read by its path under
``web/js``; an area kept in several modules is read as all of them.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "rlmcp_studio" / "web"
JS = WEB / "js"


def read_js(*patterns: str) -> str:
  """The text of these modules, in the order named. A pattern may be a glob."""
  parts = []
  for pattern in patterns:
    matched = sorted(JS.glob(pattern))
    assert matched, f"no client module matches {pattern}"
    parts.extend(path.read_text() for path in matched)
  return "\n".join(parts)


def stylesheets() -> list[Path]:
  """The app's own stylesheets, in the order ``index.html`` links them.

  The design tokens are left out: they are a file of their own, read by the
  tests that are about them."""
  page = (WEB / "index.html").read_text()
  hrefs = re.findall(r'<link rel="stylesheet" href="/static/([^"]+)"', page)
  return [WEB / href for href in hrefs if not href.endswith("tokens.css")]


def read_css() -> str:
  """The app's stylesheet as the browser applies it: every part, in link order."""
  return "\n".join(path.read_text() for path in stylesheets())
