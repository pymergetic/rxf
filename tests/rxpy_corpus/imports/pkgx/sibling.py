"""A module that imports its sibling with dots: ``from . import leaf`` resolves against the
package the module's own name says it is in (docs/23 4.6)."""

from . import leaf
from .leaf import inc as bump_one


def via_leaf(x):
    return leaf.inc(x) * 2


def via_name(x):
    return bump_one(x) * 3


def deep():
    return leaf.DEPTH
