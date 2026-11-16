"""The browser libraries the studio uses but does not keep in its repository.

Monaco (the editor) and xterm.js (the terminal) are prebuilt bundles that load
from a plain `<script>` tag, so the studio needs no Node toolchain and no build
step of its own. They are still third-party code, so the repository keeps only
this manifest -- a name, a version and npm's own integrity hash for each
tarball -- and the files arrive one of two ways:

* **Building a wheel** (`uv build`, CI, the Dockerfile) runs :func:`fetch` into
  the wheel's `web/vendor/` through the hook in `setup.py`. An installed studio
  therefore works with no network at all.
* **A source checkout** has no `web/vendor/`; the server fetches the same
  tarballs once into the user cache (:func:`cache_dir`) when it starts, and
  `rlmcp-studio --check` says whether that has happened.

Either way the bytes are verified against the pinned hash before anything is
written, and nothing outside the listed members is extracted. To update a
library, change its version and hash below (`npm view <name>@<version>
dist.integrity`) and check that a Python file opens with highlighting and that
a shell opens in the terminal drawer.

Run ``python -m rlmcp_studio.vendor [DEST]`` to fetch by hand.
"""

from __future__ import annotations

import base64
import hashlib
import io
import os
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

PACKAGED = Path(__file__).resolve().parent / "web" / "vendor"


@dataclass(frozen=True)
class Package:
  name: str
  version: str
  integrity: str                      # npm's `dist.integrity`, sha512 in SRI form
  # (member prefix in the tarball, destination prefix under web/vendor/)
  mapping: Tuple[Tuple[str, str], ...]
  skip: Tuple[str, ...] = ()          # member prefixes or suffixes left out

  @property
  def tarball(self) -> str:
    base = self.name.rsplit("/", 1)[-1]
    return f"https://registry.npmjs.org/{self.name}/-/{base}-{self.version}.tgz"


PACKAGES: Tuple[Package, ...] = (
  Package(
    "monaco-editor", "0.56.0",
    "sha512-sXboRm3BeBeLm938eaiyLMe0OxzfXIlZvbv4ir/jVgQy1zDhWjgmny0WoN45fuDKhCCQsYMbBJrv/A6jd8aCUg==",
    mapping=(("package/min/vs/", "monaco/"),
             ("package/LICENSE", "monaco/LICENSE"),
             ("package/ThirdPartyNotices.txt", "monaco/ThirdPartyNotices.txt")),
    # The studio opens Python, JSON, YAML and Markdown. The TypeScript, CSS
    # and HTML language services are most of the package's size, and the UI
    # is English, so their workers and the translations stay out.
    skip=(".map", "package/min/vs/language/typescript/", "package/min/vs/language/css/",
          "package/min/vs/language/html/", "package/min/vs/assets/ts.worker-",
          "package/min/vs/assets/css.worker-", "package/min/vs/assets/html.worker-",
          "package/min/vs/nls/lang/"),
  ),
  Package(
    "@xterm/xterm", "5.5.0",
    "sha512-hqJHYaQb5OptNunnyAnkHyM8aCjZ1MEIDTQu1iIbbTD/xops91NB5yq1ZK/dC2JDbVWtF23zUtl9JE2NqwT87A==",
    mapping=(("package/lib/xterm.js", "xterm/xterm.js"),
             ("package/css/xterm.css", "xterm/xterm.css"),
             ("package/LICENSE", "xterm/LICENSE")),
  ),
  Package(
    "@xterm/addon-fit", "0.10.0",
    "sha512-UFYkDm4HUahf2lnEyHvio51TNGiLK66mqP2JoATy7hRZeXaGMRDr00JiSF7m63vR5WKATF605yEggJKsw0JpMQ==",
    mapping=(("package/lib/addon-fit.js", "xterm/addon-fit.js"),),
  ),
)

# What must exist for the client to load both libraries.
ENTRY_POINTS = ("monaco/loader.js", "xterm/xterm.js", "xterm/addon-fit.js")


def stamp() -> str:
  """One line naming every pinned package, written beside the files."""
  return " ".join(f"{p.name}@{p.version}" for p in PACKAGES)


def cache_dir() -> Path:
  """`$XDG_CACHE_HOME/rlmcp-studio/vendor/<hash of the manifest>`.

  Keyed by the manifest, so bumping a version fetches afresh instead of
  serving the old files.
  """
  base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
  key = hashlib.sha256(stamp().encode()).hexdigest()[:12]
  return Path(base) / "rlmcp-studio" / "vendor" / key


def complete(root: Path) -> bool:
  """Whether `root` holds this manifest's files."""
  try:
    return ((root / "VERSION").read_text().strip() == stamp()
            and all((root / f).is_file() for f in ENTRY_POINTS))
  except OSError:
    return False


def directory() -> Optional[Path]:
  """Where the libraries are: the wheel's own copy, else the cache, else None."""
  for root in (PACKAGED, cache_dir()):
    if complete(root):
      return root
  return None


def _verify(data: bytes, integrity: str) -> None:
  algorithm, _, expected = integrity.partition("-")
  if algorithm != "sha512":
    raise ValueError(f"unsupported integrity algorithm {algorithm!r}")
  actual = base64.b64encode(hashlib.sha512(data).digest()).decode()
  if actual != expected:
    raise ValueError("integrity mismatch: the downloaded tarball is not the pinned one")


def _destination(package: Package, member: str) -> Optional[str]:
  if any(member.startswith(s) or member.endswith(s) for s in package.skip):
    return None
  for source, target in package.mapping:
    if member == source:
      return target
    if source.endswith("/") and member.startswith(source):
      return target + member[len(source):]
  return None


def _members(package: Package, data: bytes) -> Iterable[Tuple[str, bytes]]:
  with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
    for info in tar:
      if not info.isfile():
        continue
      target = _destination(package, info.name)
      if target is None:
        continue
      parts = Path(target).parts
      if Path(target).is_absolute() or ".." in parts:
        raise ValueError(f"refusing to extract {info.name!r}")
      handle = tar.extractfile(info)
      if handle is not None:
        yield target, handle.read()


def _download(url: str, timeout: float) -> bytes:
  with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 -- pinned https
    return response.read()


def fetch(dest: Path, timeout: float = 60.0,
          download: Callable[[str, float], bytes] = _download,
          packages: Tuple[Package, ...] = PACKAGES,
          entry_points: Tuple[str, ...] = ENTRY_POINTS) -> Path:
  """Download, verify and extract every package into `dest`.

  Written into a sibling directory and renamed at the end, so a fetch that
  fails half way never leaves a directory that looks complete.
  """
  dest = Path(dest)
  staging = dest.with_name(dest.name + ".partial")
  if staging.exists():
    _remove(staging)
  staging.mkdir(parents=True)
  for package in packages:
    data = download(package.tarball, timeout)
    _verify(data, package.integrity)
    for target, content in _members(package, data):
      path = staging / target
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_bytes(content)
  (staging / "VERSION").write_text(stamp() + "\n")
  missing = [f for f in entry_points if not (staging / f).is_file()]
  if missing:
    raise ValueError(f"the packages did not contain {', '.join(missing)}")
  if dest.exists():
    _remove(dest)
  staging.rename(dest)
  return dest


def ensure(timeout: float = 60.0) -> Path:
  """The libraries' directory, fetching them into the cache if needed."""
  found = directory()
  return found if found is not None else fetch(cache_dir(), timeout=timeout)


def _remove(path: Path) -> None:
  for child in sorted(path.rglob("*"), key=lambda p: len(p.parts), reverse=True):
    child.rmdir() if child.is_dir() and not child.is_symlink() else child.unlink()
  path.rmdir()


def main(argv: Optional[List[str]] = None) -> int:
  args = sys.argv[1:] if argv is None else argv
  dest = Path(args[0]) if args else cache_dir()
  print(f"fetching {stamp()} into {dest}")
  fetch(dest)
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
