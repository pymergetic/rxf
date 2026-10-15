"""A face of another import: ``double_it`` is not a function of this module -- the call
is a call card naming the path a prior import mapped the name to (``known``), the same
card shape a call of an in-module def takes."""


def borrow(x):
    return double_it(x) + 1
