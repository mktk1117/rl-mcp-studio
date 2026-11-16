"""Build hook: put the pinned browser libraries into the wheel.

Everything else about the package is declared in pyproject.toml. This file
exists only because setuptools takes a custom build step from here: after the
normal `build_py`, it fetches Monaco and xterm.js (see rlmcp_studio/vendor.py)
into the build directory, so the repository never carries them and an
installed wheel still works offline. An editable install skips the fetch; the
server fetches into the user cache on first start instead.
"""

import importlib.util
import sys
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py


def _vendor_module():
  # Loaded by path: importing the package would import its dependencies,
  # which the isolated build environment does not have.
  path = Path(__file__).resolve().parent / "rlmcp_studio" / "vendor.py"
  spec = importlib.util.spec_from_file_location("_rlmcp_studio_vendor", path)
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module  # dataclasses look the module up by name
  spec.loader.exec_module(module)
  return module


class build_py_with_vendor(build_py):
  def run(self):
    super().run()
    if getattr(self, "editable_mode", False):
      return
    vendor = _vendor_module()
    dest = Path(self.build_lib) / "rlmcp_studio" / "web" / "vendor"
    if vendor.complete(dest):
      return  # copied from a source tree that already had them
    vendor.fetch(dest)


setup(cmdclass={"build_py": build_py_with_vendor})
