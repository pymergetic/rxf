"""The MicroPython side of the rxpy differential: the same corpus source, the same words.

Run by a MicroPython unix port (see test_rxpy.py's MPY): the corpus files of
``tests/rxpy_corpus`` are executed as they stand -- the same bytes the RXF strand
compiles -- and each case of stdin (``entry word word ...``, one per line) calls the
named function with those words.  One answer line per case: the answer words decimal
(``bool`` is its 0/1, a tuple one word per column), or ``!ExceptionName`` where the
call raised -- the refusal kinds the reference really raises (ZeroDivisionError and
friends), pinned one-sidedly beside the run's own statuses.

The cases run in order in one namespace, so a sequence of calls shares the module's
state exactly the way a sequence of runs on one image shares its cells."""

import sys


def main(argv, stdin, stdout):
    namespace: dict = {"__name__": "rxpy_corpus"}
    for path in argv[1:]:
        with open(path) as source:
            text = source.read()
        exec(text, namespace)
    for line in stdin:
        parts = line.split()
        if not parts:
            continue
        entry = parts[0]
        arguments = [int(word) for word in parts[1:]]
        try:
            answered = namespace[entry](*arguments)
        except Exception as problem:  # the reference's own refusals, by name
            stdout.write("!" + type(problem).__name__ + "\n")
            continue
        words = answered if isinstance(answered, tuple) else (answered,)
        stdout.write(" ".join(str(int(word)) for word in words) + "\n")


main(sys.argv, sys.stdin, sys.stdout)
