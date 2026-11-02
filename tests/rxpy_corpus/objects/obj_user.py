"""Classes and imports together: a module drives another module's instances through the
functions that module exports (an instance itself is a cell of its own module; the importer
reaches it through a function, or reads the word cells it exports)."""

import obj_counter
from obj_counter import LIMIT, run_b


def drive(k):
    return obj_counter.run_a(k) + obj_counter.peek_both()


def through_name(k):
    return run_b(k) * 2


def bound():
    return LIMIT + obj_counter.LIMIT
