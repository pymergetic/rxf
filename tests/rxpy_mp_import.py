"""The MicroPython side of the rxpy import differential: modules IMPORTED, not executed.

Run by a MicroPython unix port (see test_rxpy_imports.py): ``rxpy_mp_import.py DIR module ...``
puts DIR on ``sys.path`` and imports each module by its dotted name -- the real import machinery,
packages and relative imports included, the same bytes the RXF strand compiles -- then each case of
stdin (``module.entry word word ...``, one per line) calls the function with those words; an
entry may reach into an instance (``module.a.bump``: the method bound to ``a``).  One
answer line per case, in the format of ``rxpy_mp_answer.py``: the answer words decimal (a ``bool``
is its 0/1, a tuple one word per column) or ``!ExceptionName`` where the call raised.

The cases run in order in one process, so a sequence of calls shares every module's state exactly
the way a sequence of runs on one image shares its cells."""

import sys


def resolve(modules, dotted):
    """The callable a dotted name reaches: the longest module prefix, then attributes -- so
    ``m.f`` is a function and ``m.a.bump`` is a method bound to the instance ``m.a``."""
    for name in sorted(modules, key=len, reverse=True):
        if dotted.startswith(name + "."):
            target = modules[name]
            for attribute in dotted[len(name) + 1 :].split("."):
                target = getattr(target, attribute)
            return target
    raise ValueError("no module of " + dotted)


def main(argv, stdin, stdout):
    sys.path.insert(0, argv[1])
    modules = {}
    for name in argv[2:]:
        __import__(name)
        modules[name] = sys.modules[name]
    for line in stdin:
        parts = line.split()
        if not parts:
            continue
        arguments = [int(word) for word in parts[1:]]
        try:
            answered = resolve(modules, parts[0])(*arguments)
        except Exception as problem:  # the reference's own refusals, by name
            stdout.write("!" + type(problem).__name__ + "\n")
            continue
        words = answered if isinstance(answered, tuple) else (answered,)
        stdout.write(" ".join(str(int(word)) for word in words) + "\n")


main(sys.argv, sys.stdin, sys.stdout)
