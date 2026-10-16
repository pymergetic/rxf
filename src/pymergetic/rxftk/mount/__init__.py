"""The host mount: one image, one import hook.

The object world of one image as Python. ``image_open`` opens an ``.rxfb`` (or a ``.rxfd``
tree), and from then on ``import`` answers from the image for every module name it has: the
same trichotomy the guest's import walk reads (``Image.resolve_module``, docs/23 §4.6), the
same faces ``core.mount`` synthesizes (docs/16). The finder sits **last** in ``sys.meta_path``:
where the host environment already has the name, the host wins; what nothing else provides
comes from the image. A name under a package the image serves and the image does not have is
refused by name (``ImportError: image has no ...``), never guessed.

The MicroPython guest needs no finder at all: it boots into the object world (docs/16), and
the names it imports are exactly the names this mount serves — one card, two seats.
"""

from __future__ import annotations

import importlib.abc
import importlib.util
import sys
import weakref
from pathlib import Path

from pymergetic.rxftk.core import mount as core_mount
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.core.tree import TreeError

__all__ = [
    "image_open",
    "install",
    "mounted",
    "uninstall",
]


class ImageLoader(importlib.abc.Loader):
    """One module of the image: the synthesized module object of its cell.

    The synthesizer is held weakly: the loader is the transport of one import (a module's
    ``__spec__.loader`` points at it), the finder is what owns the mount — a module must not
    keep an image alive."""

    def __init__(self, synthesizer: core_mount.Synthesizer) -> None:
        self._synthesizer = weakref.ref(synthesizer)

    @property
    def synthesizer(self) -> core_mount.Synthesizer:
        synth = self._synthesizer()
        if synth is None:
            raise RuntimeError("the mount this loader belonged to is gone")
        return synth

    def create_module(self, spec):
        return self.synthesizer.module(spec.name)

    def exec_module(self, module) -> None:
        """The module is complete when it is made: the face reads the image on demand."""


class ImageFinder(importlib.abc.MetaPathFinder):
    """Answers ``import`` for the module names of one image."""

    def __init__(self, image: Image) -> None:
        self.image = image
        self.synthesizer = core_mount.Synthesizer(image)
        self._loader = ImageLoader(self.synthesizer)  # one loader per image

    def find_spec(self, fullname, path=None, target=None):
        try:
            stat = self.image.resolve_module(fullname)
        except TreeError:  # no root of the image is spelled here: not ours
            return None
        if stat == "none":
            parent = fullname.rpartition(".")[0]
            served = sys.modules.get(parent)
            if served is not None and getattr(served, "__rxf_mount__", False):
                raise ImportError(f"image has no {fullname!r}", name=fullname)
            return None
        return importlib.util.spec_from_loader(
            fullname, self._loader, is_package=stat == "package"
        )


_finder: ImageFinder | None = None  # one image at a time


def install(image: Image) -> ImageFinder:
    """Mount one image (one at a time). The finder goes to the end of ``sys.meta_path``:
    where the host environment has the name, the host wins."""
    global _finder
    if _finder is not None:
        raise ImportError(
            "an RXF image is already mounted; call rxftk.mount.uninstall() first"
        )
    _finder = ImageFinder(image)
    sys.meta_path.append(_finder)
    return _finder


def uninstall() -> None:
    """Take the mount down: the finder out of ``sys.meta_path`` and every module it served
    out of ``sys.modules`` (and out of its parent's attributes)."""
    global _finder
    if _finder is not None and _finder in sys.meta_path:
        sys.meta_path.remove(_finder)
    _finder = None
    for name in [
        n for n, m in list(sys.modules.items()) if getattr(m, "__rxf_mount__", False)
    ]:
        module = sys.modules.pop(name)
        parent_name, _, leaf = name.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and getattr(parent, leaf, None) is module:
            delattr(parent, leaf)


def mounted() -> Image:
    """The image the mount serves, or ImportError("no RXF image loaded")."""
    if _finder is None:
        raise ImportError("no RXF image loaded")
    return _finder.image


def image_open(path) -> Image:
    """Open an ``.rxfb`` file (or a ``.rxfd`` tree directory) and mount it."""
    at = Path(path)
    image = tree.load(at) if at.is_dir() else Image.from_bytes(at.read_bytes())
    install(image)
    return image
