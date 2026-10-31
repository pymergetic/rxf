"""Simple classes (docs/23 1: a class is a Type, an instance an object cell with fields).

An instance is a named cell of the module made at import time -- ``NAME = Class(constants)`` --
whose words are the fields ``__init__`` sets, in the order it first sets them.  A method is
compiled once per instance with ``self`` bound to that cell, so ``self.n`` is a load at the
cell's address plus the field's offset and a method call is a call card of ``a__bump``.  The
state of the instances walks the way the reference's does: the differential runs the calls in
order on both sides."""

LIMIT = 50


class Counter:
    """A counter with a step: three fields."""

    def __init__(self, start, step):
        self.n = start
        self.step = step
        self.calls = 0

    def bump(self, k):
        self.calls += 1
        self.n += self.step * k
        return self.n

    def peek(self):
        return self.n

    def both(self):
        return self.n, self.calls

    def twice(self, k):
        self.bump(k)
        return self.bump(k)

    def reset(self, to):
        self.n = to
        self.calls = 0
        return self.n

    def over(self, bound):
        total = 0
        while self.n < bound:
            total += self.bump(1)
        return total


class Flag:
    """One field: a bool is a word, and its toggles are words."""

    def __init__(self, on):
        self.on = on

    def toggle(self):
        if self.on:
            self.on = 0
        else:
            self.on = 1
        return self.on

    def is_on(self):
        return self.on


class Plain:
    """No __init__: no fields, the instance's cell is one empty word and a method is a function."""

    def three(self):
        return 3

    def plus(self, x):
        return x + self.three()


a = Counter(10, 3)
b = Counter(0, 5)
f = Flag(True)
p = Plain()


def run_a(k):
    return a.bump(k)


def run_b(k):
    return b.bump(k)


def peek_both():
    return a.n * 1000 + b.n


def poke_a(v):
    a.n = v
    return a.n


def twice_a(k):
    return a.twice(k)


def pair_a():
    n, calls = a.both()
    return n * 100 + calls


def limited(k):
    if a.bump(k) > LIMIT:
        return a.reset(0)
    return a.n


def flip():
    return f.toggle()


def flag_on():
    return f.is_on()


def plain(x):
    return p.plus(x)


def climb(bound):
    return b.over(bound)


def step_of_b():
    return b.step
