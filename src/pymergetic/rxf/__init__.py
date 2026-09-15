"""RXF — Reversible eXecutable Format.

One file that boots directly, carries its whole program state as an object graph.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("pymergetic-rxf")
except PackageNotFoundError:
    __version__ = "0.0.0"
