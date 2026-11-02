"""The asyncio program corpus (docs/23 4.5): programs as DATA, run by two executors.

One executor is MicroPython's real ``asyncio`` (``reference.py``, the reference of docs/23
decision 10); the other is the model of the S3 ready-ring binding (``tests/host/smp_asyncio.c``).
Neither parses code: a program is tasks of ops, and the two reports are compared line by line.

A program is ``{"events": N, "tasks": [[op, ...], ...]}``; task 0 is the main task
(``run_until_complete``). An op is a tuple ``(name, argument)``:

    mark v     fold v into the task's result          spawn t   create_task(task t)
    emit v     append (task, v) to the shared trace   join t    await task t
    sleep n    sleep n ticks (0 is a yield)           wait e    await event e
    now        fold the clock (ticks) into the result  set e    event e .set()
    cancel t   task t .cancel()                       clear e   event e .clear()

The trace is a function of the loop's FIFO order, so at ONE carrier it must equal the reference
exactly; at N carriers the order is the interleaving and only the multiset and each task's own
sequence are results. Every program here spawns each task exactly once; main joins all
of them except where a program says it abandons one (the abandoned task is parked on a timer, so
its state is the same at any carrier count).

The folds and the tick (``UNIT_MS`` of real time in the reference) are documented in
``smp_asyncio.h``; a program's times are small integers so a sleep of the reference is a few
units of 20 ms, far apart enough that wall-clock jitter never reorders two deadlines.
"""

def spawn_join() -> dict:
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("mark", 1), ("join", 1), ("join", 2), ("emit", 90)],
            [("mark", 10), ("emit", 11), ("sleep", 0), ("emit", 12), ("mark", 13)],
            [("mark", 20), ("emit", 21), ("sleep", 0), ("emit", 22), ("sleep", 0), ("emit", 23)],
        ],
    }


def round_robin() -> dict:
    """Three tasks yield in turn: the ring's FIFO order is the whole trace."""
    spin = [("emit", 1), ("sleep", 0), ("emit", 2), ("sleep", 0), ("emit", 3), ("sleep", 0)]
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("spawn", 3), ("join", 1), ("join", 2), ("join", 3)],
            list(spin),
            list(spin),
            list(spin),
        ],
    }


def timers_in_due_order() -> dict:
    """Sleeps of 3, 1, 2 and a tie of 2: deadlines order the wakes, equal deadlines keep the
    order the sleeps were made in."""
    def napper(ticks: int, tag: int) -> list:
        return [("sleep", ticks), ("emit", tag), ("now",)]

    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("spawn", 3), ("spawn", 4),
             ("join", 1), ("join", 2), ("join", 3), ("join", 4), ("now",)],
            napper(3, 31),
            napper(1, 11),
            napper(2, 21),
            napper(2, 22),
        ],
    }


def event_fanout() -> dict:
    """Three waiters on one event, woken in arrival order by one set."""
    def waiter(tag: int) -> list:
        return [("emit", tag), ("wait", 0), ("emit", tag + 100), ("mark", tag)]

    return {
        "events": 1,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("spawn", 3), ("sleep", 2), ("emit", 0), ("set", 0),
             ("join", 1), ("join", 2), ("join", 3)],
            waiter(1),
            waiter(2),
            waiter(3),
        ],
    }


def set_then_wait() -> dict:
    """An event that is already set returns at once; clear makes the next wait park again."""
    return {
        "events": 1,
        "tasks": [
            [("spawn", 1), ("set", 0), ("sleep", 2), ("set", 0), ("join", 1)],
            [("wait", 0), ("emit", 1), ("clear", 0), ("sleep", 1), ("wait", 0), ("emit", 2)],
        ],
    }


def chain_join() -> dict:
    """Results travel up a chain of awaits: 1 awaits 2 awaits 3."""
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("join", 1), ("emit", 1)],
            [("spawn", 2), ("mark", 5), ("join", 2), ("mark", 6)],
            [("spawn", 3), ("mark", 7), ("join", 3), ("mark", 8)],
            [("sleep", 1), ("mark", 9)],
        ],
    }


def late_join() -> dict:
    """A join of a task that has already finished does not park."""
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("sleep", 3), ("join", 1), ("emit", 7)],
            [("mark", 4), ("emit", 5)],
        ],
    }


def cancel_sleeper() -> dict:
    """A cancel of a task parked on a timer: it ends cancelled and its joiner folds the marker."""
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("sleep", 2), ("cancel", 1), ("join", 1), ("emit", 1)],
            [("emit", 10), ("sleep", 9), ("emit", 99)],
        ],
    }


def cancel_waiter() -> dict:
    """A cancel of a task parked on an event that is never set."""
    return {
        "events": 1,
        "tasks": [
            [("spawn", 1), ("sleep", 1), ("cancel", 1), ("join", 1), ("emit", 2)],
            [("emit", 10), ("wait", 0), ("emit", 99)],
        ],
    }


def cancel_forwarded() -> dict:
    """uasyncio forwards a cancel to the task the target awaits: task 1 awaits task 2, a cancel of
    task 1 cancels task 2, and task 1 resumes with the marker of its join."""
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("sleep", 2), ("cancel", 1), ("join", 1), ("emit", 3)],
            [("spawn", 2), ("join", 2), ("emit", 4), ("mark", 5)],
            [("emit", 10), ("sleep", 9), ("emit", 99)],
        ],
    }


def abandoned() -> dict:
    """run_until_complete returns when main does: the worker parked on a timer stays pending."""
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("sleep", 1), ("emit", 1)],
            [("emit", 10), ("sleep", 50), ("emit", 99)],
        ],
    }


def ping_pong() -> dict:
    """Two tasks hand a turn back and forth through two events."""
    return {
        "events": 2,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("join", 1), ("join", 2)],
            [("emit", 1), ("set", 0), ("wait", 1), ("clear", 1), ("emit", 3),
             ("set", 0), ("wait", 1), ("clear", 1), ("emit", 5)],
            [("wait", 0), ("clear", 0), ("emit", 2), ("set", 1), ("wait", 0), ("clear", 0),
             ("emit", 4), ("set", 1)],
        ],
    }


def fan_in_out() -> dict:
    """Eight workers sleep for different times and signal their own event; main awaits every
    event, then joins every worker. Many runs park and wake across the carriers."""
    workers = 8
    tasks: list = [[("spawn", i + 1) for i in range(workers)]]
    for i in range(workers):
        tasks.append([("emit", i + 1), ("sleep", (i * 3) % 4 + 1), ("mark", i), ("now",), ("set", i)])
    tasks[0] += [("wait", i) for i in range(workers)]
    tasks[0] += [("join", i + 1) for i in range(workers)]
    tasks[0].append(("now",))
    return {"events": workers, "tasks": tasks}


def pipeline() -> dict:
    """A producer, two stages and a sink handing results on through joins and events."""
    return {
        "events": 3,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("spawn", 3), ("spawn", 4), ("wait", 2), ("join", 4),
             ("join", 3), ("join", 2), ("join", 1)],
            [("mark", 1), ("sleep", 1), ("set", 0), ("mark", 2)],
            [("wait", 0), ("sleep", 2), ("emit", 20), ("set", 1)],
            [("wait", 1), ("sleep", 1), ("emit", 30), ("set", 2)],
            [("wait", 2), ("emit", 40), ("mark", 4)],
        ],
    }


def churn_yields() -> dict:
    """Many yields: lots of ring pushes and pops, runs migrating across carriers."""
    body = [("mark", 1)] + [("sleep", 0), ("mark", 2)] * 150
    return {
        "events": 0,
        "tasks": [
            [("spawn", 1), ("spawn", 2), ("spawn", 3), ("spawn", 4), ("spawn", 5),
             ("join", 1), ("join", 2), ("join", 3), ("join", 4), ("join", 5)],
            list(body),
            list(body),
            list(body),
            list(body),
            list(body),
        ],
    }


PROGRAMS = {
    "spawn_join": spawn_join(),
    "round_robin": round_robin(),
    "timers_in_due_order": timers_in_due_order(),
    "event_fanout": event_fanout(),
    "set_then_wait": set_then_wait(),
    "chain_join": chain_join(),
    "late_join": late_join(),
    "cancel_sleeper": cancel_sleeper(),
    "cancel_waiter": cancel_waiter(),
    "cancel_forwarded": cancel_forwarded(),
    "abandoned": abandoned(),
    "ping_pong": ping_pong(),
    "fan_in_out": fan_in_out(),
    "pipeline": pipeline(),
    "churn_yields": churn_yields(),
}
