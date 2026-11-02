"""Imports of a module of the image: every form the subset binds (docs/23 4.6).

``import m`` and ``import m as n`` bind the module (its functions are call cards, its word
cells are read and written live); ``from m import f`` binds a function; ``from m import NAME``
binds this module's own cell, a snapshot of the word the cell had when the import ran."""

import imp_base
import imp_base as base
from imp_base import COUNT as FIRST_COUNT
from imp_base import LIMIT, twice
from imp_base import bump as bumped


def via_module(x):
    return imp_base.twice(x) + 1


def via_alias(x):
    return base.twice(x) + 2


def via_from(x):
    return twice(x) + 3


def live_count():
    return imp_base.COUNT


def snapshot_limit():
    return LIMIT


def snapshot_count():
    return FIRST_COUNT


def push(k):
    return bumped(k)


def push_module(k):
    return imp_base.bump(k)


def write_through(v):
    imp_base.COUNT = v
    return imp_base.COUNT


def add_through(v):
    imp_base.COUNT += v
    return imp_base.COUNT


def pair_use(a, b):
    q, r = imp_base.divide(a, b)
    return q * 100 + r


def shadowed(imp_base):
    return imp_base + 1


def loop_through(n):
    total = 0
    while n > 0:
        total = total + imp_base.twice(n) + imp_base.COUNT
        n = n - 1
    return total
