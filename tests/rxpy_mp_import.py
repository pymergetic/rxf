"""The MicroPython side of the rxpy import differential: modules IMPORTED, not executed.

Run by a MicroPython unix port (see test_rxpy_imports.py): ``rxpy_mp_import.py DIR module ...``
puts DIR on ``sys.path`` and imports each module by its dotted name -- the real import machinery,
packages and relative imports included, the same bytes the RXF strand compiles -- then each case of
stdin (``module.entry word word ...``, one per line) calls the function with those words.  One
answer line per case, in the format of ``rxpy_mp_answer.py``: the answer words decimal (a ``bool``
is its 0/1, a tuple one word per column) or ``!ExceptionName`` where the call raised.

The cases run in order in one process, so a sequence of calls shares every module's state exactly
the way a sequence of runs on one image shares its cells."""

import sys


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
        module, _, entry = parts[0].rpartition(".")
        arguments = [int(word) for word in parts[1:]]
        try:
            answered = getattr(modules[module], entry)(*arguments)
        except Exception as problem:  # the reference's own refusals, by name
            stdout.write("!" + type(problem).__name__ + "\n")
            continue
        words = answered if isinstance(answered, tuple) else (answered,)
        stdout.write(" ".join(str(int(word)) for word in words) + "\n")


main(sys.argv, sys.stdin, sys.stdout)
