"""Loops: ``while`` with ``break`` and ``continue``, and the names the loop carries."""


def sum_to(n):
    total = 0
    while n > 0:
        total += n
        n -= 1
    return total


def sum_even(n):
    """the odds step over with continue; count walks down to 0"""
    total = 0
    count = n
    while count > 0:
        count -= 1
        if count % 2:
            continue
        total += count
    return total


def steps(x, limit):
    """a loop with no condition of its own: the breaks inside end it"""
    count = 0
    while True:
        if x >= limit:
            break
        if x < -limit:
            break
        x = x * 2 + 1
        count += 1
    return count


def gcd(a, b):
    while b != 0:
        a, b = b, a % b
    return a
