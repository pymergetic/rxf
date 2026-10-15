"""The module's state: ``NAME = <constant>`` is a named cell of the import unit.

``bump`` binds the cell through ``global``; ``read`` answers it.  A sequence of runs on
one image walks the same state a sequence of MicroPython calls walks (the differential
runs the cases in order on both sides)."""

COUNT = 5


def bump(x):
    global COUNT
    COUNT = COUNT + x
    return COUNT


def read():
    return COUNT


def bump_twice(x):
    global COUNT
    COUNT = COUNT + x
    COUNT = COUNT + x
    return COUNT
