"""A module inside a package: its path in the image is ``pkgx.leaf.<name>``."""

DEPTH = 2


def inc(x):
    return x + 1


def depth():
    return DEPTH
