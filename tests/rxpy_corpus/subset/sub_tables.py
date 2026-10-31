"""Tables: tuples, lists and bytes as data cells (docs/23 4.3).

A table is one named data cell -- a FixedArray whose payload is the elements, a word each for a
tuple or a list and a byte each for bytes.  An index is normalized the way Python does (a negative
one counts from the end) and checked against the length: outside it the run refuses, where the
reference raises IndexError.  ``len`` of a table is a constant of the module.  A list may be stored
through an index; its length is fixed."""

PRIMES = (2, 3, 5, 7, 11, 13)
SIGNED = (-5, 0, 5)
MASKS = b"\x01\x02\x04\x08\x10\x20\x40\x80"
HIGH = b"\xff\xfe\x80"
EMPTY = b""
SLOTS = [10, 20, 30, 40]


def prime(i):
    return PRIMES[i]


def signed(i):
    return SIGNED[i]


def mask(i):
    return MASKS[i]


def high(i):
    return HIGH[i]


def sizes():
    return len(PRIMES) * 1000 + len(MASKS) * 100 + len(SLOTS) * 10 + len(EMPTY)


def total(n):
    i = 0
    s = 0
    while i < n:
        s += PRIMES[i]
        i += 1
    return s


def tail(i):
    return MASKS[-i]


def slot(i):
    return SLOTS[i]


def put(i, v):
    SLOTS[i] = v
    return SLOTS[i]


def grow(i, v):
    SLOTS[i] += v
    return SLOTS[i]


def sum_slots():
    return SLOTS[0] + SLOTS[1] + SLOTS[2] + SLOTS[3]
