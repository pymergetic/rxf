"""Helpers the tests share."""

from pymergetic.rxftk.core.object import Image


def resolved(image: Image, path: str) -> int:
    """The handle of a path that must exist in the image."""
    found = image.resolve(path)
    assert found is not None, f"{path} does not resolve"
    return found
