"""The reference executor of the asyncio corpus: MicroPython's real ``asyncio``, run as it stands.

Run by a MicroPython unix port (``tests/test_smp_asyncio.py``): ``reference.py programs.py [name
...]`` executes the corpus data of ``programs.py`` as real coroutines (``create_task``, ``await``
on a task, ``asyncio.Event``, ``asyncio.sleep``, ``Task.cancel``) and prints one report per
program, in the format of ``tests/host/smp_asyncio_prove.c``::

    program NAME
    task 0 D 123456        (D done, C cancelled, P still pending when main returned)
    trace 1:10 2:20 ...    (the shared trace: task:value, in the order the loop ran them)

It never imports the model and the model never imports it: the two meet in the report only
(docs/23 decision 10, MicroPython is the reference). It runs under CPython too, which is only a
convenience (CPython's asyncio is not the reference).
"""

import asyncio
import sys
import time

# one tick of the corpus is this much real time: far apart enough that wall-clock jitter never
# reorders two deadlines or moves a rounded clock reading
UNIT_MS = 20
START = 7
CANCEL_WORD = 3148

# MicroPython's time functions are not in CPython's typeshed
ticks = getattr(time, "ticks_ms")
ticks_diff = getattr(time, "ticks_diff")


def fold(result, word):
    return (result * 31 + word + 1) & 0xFFFFFFFF


class Context:
    """What the tasks of one program share: the task objects, the events, the answers."""

    def __init__(self, program):
        self.program = program
        self.tasks = {}
        self.state = {}
        self.result = {}
        self.events = [asyncio.Event() for _ in range(program["events"])]
        self.trace = []
        self.t0 = 0


async def body(ctx, index):
    acc = START
    try:
        for op in ctx.program["tasks"][index]:
            kind = op[0]
            arg = op[1] if len(op) > 1 else 0
            if kind == "mark":
                acc = fold(acc, arg)
            elif kind == "emit":
                ctx.trace.append((index, arg))
            elif kind == "sleep":
                await asyncio.sleep(arg * UNIT_MS / 1000)
                if arg:
                    acc = fold(acc, arg)
            elif kind == "spawn":
                ctx.tasks[arg] = asyncio.create_task(body(ctx, arg))
            elif kind == "join":
                try:
                    acc = fold(acc, await ctx.tasks[arg])
                except asyncio.CancelledError:
                    # a join raises the awaited task's cancellation into the awaiter
                    if ctx.state.get(arg) != "C":
                        raise
                    acc = fold(acc, CANCEL_WORD)
            elif kind == "wait":
                await ctx.events[arg].wait()
                acc = fold(acc, 1)
            elif kind == "set":
                ctx.events[arg].set()
            elif kind == "clear":
                ctx.events[arg].clear()
            elif kind == "cancel":
                ctx.tasks[arg].cancel()
            elif kind == "now":
                elapsed = ticks_diff(ticks(), ctx.t0)
                acc = fold(acc, (elapsed + UNIT_MS // 2) // UNIT_MS)
            else:
                raise ValueError("unknown op " + str(kind))
    except asyncio.CancelledError:
        ctx.state[index] = "C"
        raise
    ctx.state[index] = "D"
    ctx.result[index] = acc
    return acc


def run_program(program):
    ctx = Context(program)
    asyncio.new_event_loop()  # MicroPython: a fresh task queue, nothing left of the last program

    async def top():
        ctx.t0 = ticks()
        await body(ctx, 0)

    asyncio.run(top())
    return ctx


def report(name, ctx):
    out = ["program " + name]
    for index in range(len(ctx.program["tasks"])):
        state = ctx.state.get(index, "P")
        answer = ctx.result.get(index, 0) if state == "D" else 0
        out.append(f"task {index} {state} {answer}")
    out.append("trace" + "".join(f" {task}:{value}" for task, value in ctx.trace))
    return "\n".join(out)


def main(argv):
    namespace: dict = {"__name__": "asyncio_corpus"}
    with open(argv[1]) as source:
        exec(source.read(), namespace)
    programs = namespace["PROGRAMS"]
    for name in argv[2:] or sorted(programs):  # MicroPython dicts keep no order
        print(report(name, run_program(programs[name])))


main(sys.argv)
