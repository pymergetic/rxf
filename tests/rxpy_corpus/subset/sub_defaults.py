"""Default parameters and keyword arguments (docs/23 4.3).

A default is the constant the def folded when it ran, so a call that leaves a parameter out
passes that word; a keyword argument binds by the parameter's name.  The same holds for a
method (its instance's cell is ``self``) and for ``__init__`` (the fields it sets are words
made at import time).  ``SPARE`` is a cell no def touches: the image still holds it."""

LIMIT = 10
SPARE = 42


def add(a, b=3):
    return a + b


def scale(x, k=2, off=LIMIT):
    return x * k + off


def short_calls(x):
    return add(x) + scale(x) * 1000 + scale(x, 5) * 100000


def keyword_calls(x):
    return add(b=x, a=1) + scale(x, off=0) * 100 + scale(k=3, x=x) * 10000


def mixed(x):
    return scale(x, 7, off=x)


class Acc:
    """Defaults in __init__ and in a method."""

    def __init__(self, start=0, step=1):
        self.n = start
        self.step = step

    def bump(self, k=1):
        self.n += self.step * k
        return self.n


fresh = Acc()
other = Acc(5)
fast = Acc(step=7)
both = Acc(2, step=3)


def run(k):
    return fresh.bump() + other.bump(k) + fast.bump(k=2) * 10 + both.bump(k=k) * 100


def snapshot():
    return fresh.n * 1000000 + other.n * 10000 + fast.n * 100 + both.n
