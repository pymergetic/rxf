"""Procedures: a def that answers None (docs/23 4.3).

``None`` has no word of its own -- a word cannot be told from the int 0 -- but the ABSENCE of an
answer is representable: a def whose returns are all bare (or ``return None``), or that falls off
its end, answers nothing.  Its Signature has no Result (the image says so) and its callers call it
as a statement.  A call used as a value is refused by name."""

COUNT = 0
LOG = 0


def tick(k):
    global COUNT
    COUNT += k


def reset():
    global COUNT
    COUNT = 0
    return


def note(x):
    global LOG
    if x > 3:
        LOG = x
        return None
    LOG = 0


def drive(n):
    tick(n)
    tick(2)
    note(n)
    return COUNT * 100 + LOG


def read():
    return COUNT


class Gauge:
    """Methods that answer nothing."""

    def __init__(self, level):
        self.level = level

    def raise_by(self, k):
        self.level += k

    def lower(self, k):
        if k > self.level:
            return
        self.level -= k

    def read(self):
        return self.level


g = Gauge(10)


def gauge(k):
    g.raise_by(k)
    g.lower(3)
    g.lower(100)
    return g.read()
