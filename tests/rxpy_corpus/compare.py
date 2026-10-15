"""Comparisons, the chained forms, and the short-circuit values of ``and``/``or``/``not``.

A comparison answers its bool as the 0/1 word.  ``and``/``or`` answer Python's value --
the operand itself, not a bool -- and evaluate their second operand only where the
first decides (the differential pins a side effect-free witness of that: the operand
words differ)."""


def eq(a, b):
    return a == b


def ne(a, b):
    return a != b


def lt(a, b):
    return a < b


def le(a, b):
    return a <= b


def gt(a, b):
    return a > b


def ge(a, b):
    return a >= b


def chain3(a, b, c):
    return a < b < c


def chain4(a, b, c, d):
    return a <= b < c <= d


def both(a, b):
    return a < 10 and b > 2


def either(a, b):
    return a > 10 or b > 2


def valued_and(a, b):
    return a and b


def valued_or(a, b):
    return a or b


def negate(a):
    return not a
