"""The word arithmetic of the subset: the operators lower to the starter's leaves.

Every answer is one word (the two's complement reading of the run's 64-bit answer);
where the domain is left -- a sum or product past the signed word, a shift past 63, a
zero divisor -- the compiled program refuses and MicroPython computes a bignum or
raises.  The differential pins both sides (tests/test_rxpy.py)."""


def add(a, b):
    return a + b


def sub(a, b):
    return a - b


def mul(a, b):
    return a * b


def floordiv(a, b):
    return a // b


def mod(a, b):
    return a % b


def band(a, b):
    return a & b


def bor(a, b):
    return a | b


def bxor(a, b):
    return a ^ b


def shl(a, n):
    return a << n


def shr(a, n):
    return a >> n


def neg(a):
    return -a


def invert(a):
    return ~a


def calc(a, b):
    """a mix of the operators over names: nothing folds at compile time here"""
    mixed = a * 3 + b // 2 - a % 5
    return (mixed ^ b) + (a & 15) - (b >> 1)
