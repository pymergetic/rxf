"""A procedure of another module, called as a statement."""

import sub_procs
from sub_procs import tick


def go(k):
    sub_procs.tick(k)
    tick(1)
    return sub_procs.COUNT
