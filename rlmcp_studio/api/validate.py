"""Checking what came in off the wire, before it can reach a record.

Every route here sits behind the app guard's token, but whoever holds it can
still send anything. So this assumes the body is hostile, because the cost of
trusting it is concrete:

* a `kind` sent as ``{"x": 1}`` would reach ``Feedback(kind=…)``, and then
  sqlite, which refuses to bind a dict — after the JSON half of the record has
  already taken it. The caller is told the write failed; the record keeps it;
  and **every later write to that record fails too**, because the index can no
  longer be rebuilt from a record it cannot bind. One malformed field would
  permanently break a run's ledger.
* one-line requests — a NUL byte in a path, a `text` of ``null``, a `cmd` of
  ``["status"]`` — would produce a bare ``Internal Server Error``, which tells
  a user nothing and a developer nothing either.

The rule this module exists to enforce: **nothing reaches a durable record
without being checked, and a bad field is a 400 that names it.**
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

from rlmcp.records import FEEDBACK_KINDS

MAX_TEXT = 20_000
"""How long a remark may be.

Not a guess about what a person types: it is a bound on what one HTTP request
can add to a file that is read on every page load. Without it, a couple of
requests carrying a megabyte of text each would grow a record's `meta.json` to
megabytes, and nothing anywhere would say no.
"""

MAX_ID = 128


class BadRequest(ValueError):
  """A request that cannot be honoured, with a message meant for a person.

  Carried as an exception so a route reads as its happy path; the app turns it
  into a 400 with this text.
  """


def text(value: Any, field: str = "text", *, required: bool = True,
         limit: int = MAX_TEXT) -> str:
  """A string, stripped. Anything else is a 400 naming the field."""
  if value is None and not required:
    return ""
  if not isinstance(value, str):
    raise BadRequest(f"`{field}` must be a string, not {_name(value)}")
  cleaned = value.strip()
  if required and not cleaned:
    raise BadRequest(f"`{field}` is empty")
  if len(cleaned) > limit:
    raise BadRequest(
        f"`{field}` is {len(cleaned):,} characters; the limit is {limit:,}")
  return _printable(cleaned)


def kind(value: Any, field: str = "kind", default: str = "observe") -> str:
  """One of rl-mcp's feedback kinds, and nothing else.

  The list is rlmcp's, imported rather than copied: a kind the library adds is
  accepted here the day it exists, and one the library does not know never
  reaches the record.
  """
  if value is None:
    return default
  if not isinstance(value, str):
    raise BadRequest(f"`{field}` must be one of {', '.join(FEEDBACK_KINDS)}, "
                     f"not {_name(value)}")
  if value not in FEEDBACK_KINDS:
    raise BadRequest(f"`{field}` must be one of {', '.join(FEEDBACK_KINDS)}; "
                     f"got '{value[:40]}'")
  return value


def identifier(value: Any, field: str = "id") -> str:
  """A record or thread id: a short string with nothing exotic in it."""
  if not isinstance(value, str):
    raise BadRequest(f"`{field}` must be a string, not {_name(value)}")
  if not value or len(value) > MAX_ID:
    raise BadRequest(f"`{field}` must be 1 to {MAX_ID} characters")
  if "\x00" in value or "/" in value or "\\" in value:
    raise BadRequest(f"`{field}` may not contain slashes or NUL bytes")
  return value


def number(value: Any, field: str, *, default: Optional[float] = None,
           low: Optional[float] = None, high: Optional[float] = None) -> float:
  """A finite number in range. Rejects NaN, infinity and numeric strings.

  A numeric string is refused on purpose: the same leniency in a boolean
  check is what would let `confirmed: "no"` through (see :func:`flag`).
  """
  if value is None and default is not None:
    return float(default)
  if isinstance(value, bool) or not isinstance(value, (int, float)):
    raise BadRequest(f"`{field}` must be a number, not {_name(value)}")
  number_value = float(value)
  if number_value != number_value or number_value in (float("inf"), float("-inf")):
    raise BadRequest(f"`{field}` must be a finite number")
  if low is not None and number_value < low:
    raise BadRequest(f"`{field}` must be at least {low}")
  if high is not None and number_value > high:
    raise BadRequest(f"`{field}` must be at most {high}")
  return number_value


def flag(value: Any, field: str) -> bool:
  """A boolean, meaning ``true``. Not "truthy".

  A truthiness check would let ``confirmed: "no"`` open the gate on a live
  training run, because every non-empty string is truthy in Python. Consent is a boolean or it is not
  consent: a client that cannot send one is a client that has not asked.
  """
  if value is None or value is False:
    return False
  if value is True:
    return True
  raise BadRequest(f"`{field}` must be true or false, not {_name(value)}")


def mapping(value: Any, field: str, *, reserved: Iterable[str] = ()) -> Dict[str, Any]:
  """An object, with the caller kept out of the wrapper's own parameters.

  ``args`` is splatted into a function that has its own ``cmd``, ``timeout``
  and ``session_dir``; a caller sending one of those would get a Python
  ``TypeError`` reported as a 500 about "multiple values for argument".
  """
  if value is None:
    return {}
  if not isinstance(value, dict):
    raise BadRequest(f"`{field}` must be an object, not {_name(value)}")
  clashes = sorted(set(value) & set(reserved))
  if clashes:
    raise BadRequest(f"`{field}` may not contain {', '.join(clashes)}")
  bad_keys = [k for k in value if not isinstance(k, str)]
  if bad_keys:
    raise BadRequest(f"`{field}` keys must be strings")
  return dict(value)


def path_like(value: Any, field: str = "path") -> str:
  """A filesystem path from a client: a string with no NUL byte.

  A NUL is rejected here rather than at ``Path.resolve``, which raises an
  ``OSError`` a route may not catch -- one character would otherwise be
  enough for a bare 500.
  """
  if not isinstance(value, str):
    raise BadRequest(f"`{field}` must be a string, not {_name(value)}")
  if not value:
    raise BadRequest(f"`{field}` is empty")
  if "\x00" in value:
    raise BadRequest(f"`{field}` contains a NUL byte")
  return value


def _printable(value: str) -> str:
  """Strip the control characters that make a record unreadable or lie.

  Newlines and tabs stay -- people write those on purpose. What goes is the
  rest of C0, plus the bidirectional overrides that can make stored text render
  as something other than what it says. This is a *durable* record; text in it
  is read back years later by someone who was not there.
  """
  drop = {*range(0, 9), *range(11, 32), 127, 0x202A, 0x202B, 0x202D, 0x202E,
          0x2066, 0x2067, 0x2068, 0x2069}
  return "".join(ch for ch in value if ord(ch) not in drop)


def _name(value: Any) -> str:
  if value is None:
    return "null"
  return {dict: "an object", list: "a list", bool: "a boolean",
          int: "a number", float: "a number", str: "a string"}.get(
              type(value), type(value).__name__)
