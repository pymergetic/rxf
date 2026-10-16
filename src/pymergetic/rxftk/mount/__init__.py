"""The mount: the object world of one image as Python (docs/16).

Open an image, import its world. Types arrive as Python classes whose properties read the
payload at the field's offset, modules arrive with their exported names, and every object
carries ``id`` — its handle into the one image. The host mounts it through one import hook
(``finder.py``); the MicroPython guest boots into the same world and needs no hook at all.
One self-description, one mapping, no glue code: what the image knows about itself is what
Python sees.
"""

from .finder import ImageFinder, ImageLoader, image_open, install, mounted, uninstall

__all__ = [
    "ImageFinder",
    "ImageLoader",
    "image_open",
    "install",
    "mounted",
    "uninstall",
]
