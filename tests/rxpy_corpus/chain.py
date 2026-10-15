"""Calls between the module's functions, and recursion (the call card's depth budget)."""


def add_one(x):
    return x + 1


def double_it(x):
    twice = add_one(x)
    return add_one(twice)


def fact(n):
    if n < 2:
        return 1
    return n * fact(n - 1)


def triangle(n):
    """recursion over two names: the call chain the budget counts"""
    if n < 1:
        return 0
    return n + triangle(n - 1)
