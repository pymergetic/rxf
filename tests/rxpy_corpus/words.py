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


def coerce(x):
    """the word-coercion face: ``int`` of a word is the word (the reference agrees on ints)"""
    return int(x)


def coerce_ms(t):
    """the uasyncio shape (``sleep``'s arithmetic): seconds to milliseconds, coerced"""
    return int(t * 1000)


def coerce_sum(a, b):
    return int(a) + int(b)


def coerce_twice(x):
    return int(int(x))  # noqa: RUF046 - the nested coercion is the pin: the face lowers twice


def coerce_statement(x):
    int(x)  # the coercion as a statement: the word is made and dropped, as the reference does
    return x
