"""Dotted imports: ``import a.b`` binds ``a`` and loads ``a.b``; ``from a import b`` binds the
submodule; ``from a.b import f`` binds the function (docs/23 4.6)."""

import pkgx.leaf
import pkgx.sibling
from pkgx import leaf as lf
from pkgx.sibling import via_leaf


def dotted(x):
    return pkgx.leaf.inc(x)


def through_sibling(x):
    return pkgx.sibling.via_name(x)


def from_package(x):
    return lf.inc(x) + 10


def imported_fn(x):
    return via_leaf(x)


def depth_of():
    return pkgx.leaf.DEPTH + lf.DEPTH
