"""A module other modules import: a function, a pair of answers, two word cells.

Its state walks the way a MicroPython module's does: ``bump`` writes the cell through
``global``, and a module that imports it reads the live word through ``imp_base.COUNT`` or
holds the snapshot ``from imp_base import COUNT`` took when the import ran (docs/23 4.6).  A
cell is in the image only where some def reads or writes it, so ``limit`` reads LIMIT."""

COUNT = 7
LIMIT = 100


def twice(x):
    return x * 2


def bump(k):
    global COUNT
    COUNT = COUNT + k
    return COUNT


def limit():
    return LIMIT


def divide(a, b):
    return a // b, a % b
