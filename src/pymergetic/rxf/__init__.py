"""RXF: an object image with a C++ core, described by deltas in XML.

`pymergetic.rxf.core` is the compiled core: one submodule per C++ module (arena,
object, hash, meta, delta, tree). `rxf` on the command line has one command group
per module. Python is orchestration only.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("pymergetic-rxf")
except PackageNotFoundError:
    __version__ = "0.0.0"
