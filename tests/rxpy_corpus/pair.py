"""A function that answers a tuple: the out cells of docs/23 4.1, in Python.

``pair`` answers two words (its return is a tuple); its caller unpacks them
(``lo, hi = pair(x)``).  The callee writes each answer through the Object its
caller passes -- one cell per callee and out position -- and ``main`` reads the
words back and adds them."""


def pair(x):
    return x + 1, x + 2


def main(x):
    lo, hi = pair(x)
    return lo + hi
