"""Branching: ``if``/``elif``/``else``, and a return from inside a branch."""


def classify(x):
    if x < 0:
        return -1
    elif x == 0:
        return 0
    else:
        return 1


def abs_of(x):
    if x < 0:
        return -x
    return x


def early(x, n):
    """returns from inside the loop once the total passes 100"""
    total = 0
    while n > 0:
        total += x
        n -= 1
        if total > 100:
            return total
    return total


def signed_area(w, h):
    if w > 0 and h > 0:
        area = w * h
    elif w < 0 or h < 0:
        area = 0 - w * h
    else:
        area = 0
    return area
