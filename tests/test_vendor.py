"""The pinned browser libraries: verified, extracted selectively, never partial.

Every test here runs offline against a tarball built in memory; the real
download is exercised by building a wheel (CI's `package` job).
"""

import base64
import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from rlmcp_studio import vendor

WEB = Path(vendor.__file__).resolve().parent / "web"


def _tarball(files):
  buffer = io.BytesIO()
  with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
    for name, content in files.items():
      info = tarfile.TarInfo(name)
      info.size = len(content)
      tar.addfile(info, io.BytesIO(content))
  return buffer.getvalue()


def _integrity(data):
  return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode()


def _package(data, **kw):
  return vendor.Package("lib", "1.0.0", _integrity(data),
                        mapping=kw.get("mapping", (("package/dist/", "lib/"),
                                                   ("package/LICENSE", "lib/LICENSE"))),
                        skip=kw.get("skip", (".map",)))


def test_members_are_mapped_and_the_rest_left_out(tmp_path):
  data = _tarball({"package/dist/lib.js": b"js", "package/dist/lib.js.map": b"map",
                   "package/LICENSE": b"MIT", "package/src/lib.ts": b"ts"})
  out = vendor.fetch(tmp_path / "v", download=lambda url, t: data,
                     packages=(_package(data),), entry_points=("lib/lib.js",))
  got = sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file())
  assert got == ["VERSION", "lib/LICENSE", "lib/lib.js"]


def test_a_tarball_that_is_not_the_pinned_one_is_refused(tmp_path):
  data = _tarball({"package/dist/lib.js": b"js"})
  pinned = _package(data)
  with pytest.raises(ValueError, match="integrity"):
    vendor.fetch(tmp_path / "v", download=lambda url, t: data + b"x",
                 packages=(pinned,), entry_points=("lib/lib.js",))
  assert not (tmp_path / "v").exists()


def test_a_member_cannot_climb_out_of_the_destination(tmp_path):
  data = _tarball({"package/dist/../../evil.js": b"x"})
  with pytest.raises(ValueError, match="refusing"):
    vendor.fetch(tmp_path / "v", download=lambda url, t: data,
                 packages=(_package(data),), entry_points=())


def test_a_fetch_missing_an_entry_point_leaves_nothing_that_looks_complete(tmp_path):
  data = _tarball({"package/LICENSE": b"MIT"})
  with pytest.raises(ValueError, match="did not contain"):
    vendor.fetch(tmp_path / "v", download=lambda url, t: data,
                 packages=(_package(data),), entry_points=("lib/lib.js",))
  assert not vendor.complete(tmp_path / "v")


def test_the_cache_is_keyed_by_the_manifest(monkeypatch, tmp_path):
  monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
  first = vendor.cache_dir()
  monkeypatch.setattr(vendor, "PACKAGES", vendor.PACKAGES[:1])
  assert vendor.cache_dir() != first
  assert first.is_relative_to(tmp_path / "rlmcp-studio" / "vendor")


def test_every_file_the_client_loads_is_an_entry_point():
  """editor.js and terminal.js name files under /static/vendor; each must be
  one the manifest guarantees, or a fetch can succeed and the page still 404."""
  js = (WEB / "js" / "editor.js").read_text() + (WEB / "js" / "terminal.js").read_text()
  for name in vendor.ENTRY_POINTS:
    assert name.split("/", 1)[1] in js, name


def test_the_libraries_are_not_committed():
  ignore = (WEB.parent.parent / ".gitignore").read_text()
  assert "rlmcp_studio/web/vendor/" in ignore
