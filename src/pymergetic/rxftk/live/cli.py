"""Commands on a running image: `rxf live <command> LINK ...`.

LINK is what `live.link.open_link` takes: `pty:/dev/pts/3[:115200]`, `tty:/dev/ttyS0:115200`,
`fd:5` or `shm:NAME`. The image on the other end is served by whatever drains its channel (the
leaf `uart.pump` plus the live pump on a machine, a host that runs `core.live.Area`).

This is the **frames face** of a link: `rxf live console` is its console. It reads and changes
the image over the channel whatever wire carries it and needs no machine of its own. The
**world face** (`rxf console`) compiles and installs, and that runs the image — a run is not a
frame (docs/19, "who runs when") — so it attaches the world of whoever runs the image through
the one seam (`console/world`). When a runner is registered at LINK in this process, both faces
speak on its channel; when nothing runs here (a rig or firmware on the far end of a line), a
command opens the wire. The two faces are one link, not one API: the frames face cannot run,
the world face cannot reach an image no runner here drives.
"""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, TextIO

from pymergetic.rxftk import source
from pymergetic.rxftk.console import world
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import link, physical
from pymergetic.rxftk.live.client import Client, LiveError


def _client(spec: str, timeout: float) -> Client:
    """The channel of the image at SPEC: the world of the runner in this process when one runs
    here (`console/world`, the seam `rxf console` attaches through), else the wire (`live.link`)."""
    if world.registered(spec):
        return world.attach(spec, timeout=float(timeout)).client
    return link.connect(spec, timeout=float(timeout))


def _held(client: Client) -> AbstractContextManager[None]:
    """The world's transaction for one whole operation when the channel is a world's gated
    view; a no-op on the raw wire, where a far end's serialization is the far end's own."""
    transaction = getattr(client, "transaction", None)
    return transaction() if transaction is not None else nullcontext()


def info(spec: str, timeout: float = 30.0) -> dict[str, Any]:
    """What the image says about itself: its sizes, how many deltas it took, its digest."""
    client = _client(spec, timeout)
    hello = client.hello()
    return {
        "digest": client.digest(),
        "version": hello.version,
        "handles": hello.handles,
        "body_size": hello.body_size,
        "arena_capacity": hello.arena_capacity,
        "commits": hello.commits,
        "rejects": hello.rejects,
        "committed": hello.committed,
    }


def digest(spec: str, timeout: float = 30.0) -> str:
    """The digest of the running image."""
    return _client(spec, timeout).digest()


def emit(
    spec: str, to: str, overwrite: bool = False, timeout: float = 30.0
) -> dict[str, Any]:
    """Dumps the running image to TO (`.rxfd` a delta, `.rxfb` image bytes, anything else a module tree)."""
    client = _client(spec, timeout)
    raw, dumped = client.emit()
    image = Image.from_bytes(raw)
    report = source.report(image)
    report["format"] = source.save(image, to, overwrite)
    report["target"] = to
    report["emitted"] = dumped
    return report


def apply(
    spec: str, *deltas: str, to: str | None = None, timeout: float = 30.0
) -> dict[str, Any]:
    """Applies the XML DELTA files to the running image as one atomic step.

    The image is dumped, the deltas are compiled against that dump into a physical delta, and the
    image takes it. The dump-compile-take composite runs inside the world's transaction when the
    channel is a world's (the gate travels with the view), so no mutation lands between the dump
    and the take. The report carries the digest the image answered with. With `--to=TARGET` the
    result is dumped there afterwards.
    """
    client = _client(spec, timeout)
    with _held(client):
        raw, _ = client.emit()
        base = Image.from_bytes(raw)
        texts = [(name, Path(name).read_text(encoding="utf-8")) for name in deltas]
        compiled = physical.from_xml(base, *texts)
        answered = client.apply(compiled.data)
        report: dict[str, Any] = {
            "digest": answered,
            "creates": compiled.creates,
            "sets": compiled.sets,
            "deletes": compiled.deletes,
            "entry": compiled.entry,
        }
        if to is not None:
            after, _ = client.emit()
            report["format"] = source.save(Image.from_bytes(after), to)
            report["target"] = to
    return report


def patch(spec: str, delta: str, timeout: float = 30.0) -> dict[str, Any]:
    """Applies a physical DELTA file (from `rxf core delta compile`) to the running image."""
    client = _client(spec, timeout)
    return {"digest": client.apply(Path(delta).read_bytes())}


def halt(spec: str, timeout: float = 30.0) -> dict[str, Any]:
    """Stops the program of the image at its next safe point; the channel keeps answering."""
    stopped = _client(spec, timeout).break_(True)
    return {"stopped": stopped}


def go(spec: str, timeout: float = 30.0) -> dict[str, Any]:
    """Lets the program go on."""
    _client(spec, timeout).break_(False)
    return {"stopped": False}


def ping(spec: str, text: str = "ping", timeout: float = 30.0) -> str:
    """Sends TEXT through the channel and prints what comes back."""
    return _client(spec, timeout).ping(str(text).encode()).decode()


HELP = """ping TEXT | hello | digest | resolve PATH | path HANDLE | get PATH | children PATH | halt | go | emit FILE | apply DELTA.rxfd... | help | quit"""


def _handle(client: Client, word: str) -> int:
    if word.isdigit():
        return int(word)
    found = client.resolve(word)
    if found is None:
        raise KeyError(f"no object at {word!r}")
    return found


def _line(client: Client, line: str) -> str | None:
    words = line.split()
    if not words:
        return ""
    command, args = words[0], words[1:]
    if command in ("quit", "exit"):
        return None
    if command == "help":
        return HELP
    if command == "ping":
        return client.ping(" ".join(args).encode() or b"ping").decode()
    if command == "hello":
        return repr(client.hello())
    if command == "digest":
        return client.digest()
    if command == "resolve":
        return str(client.resolve(args[0]))
    if command == "path":
        return client.path(int(args[0]))
    if command == "get":
        return repr(client.get(_handle(client, args[0])))
    if command == "children":
        return "\n".join(
            client.path(h) for h in client.children(_handle(client, args[0]))
        )
    if command == "halt":
        return "stopped" if client.break_(True) else "not stopped"
    if command == "go":
        client.break_(False)
        return "going"
    if command == "emit":
        raw, dumped = client.emit()
        source.save(Image.from_bytes(raw), args[0], True)
        return f"{dumped} -> {args[0]}"
    if command == "apply":
        with _held(client):
            raw, _ = client.emit()
            texts = [(name, Path(name).read_text(encoding="utf-8")) for name in args]
            compiled = physical.from_xml(Image.from_bytes(raw), *texts)
            return client.apply(compiled.data)
    raise ValueError(f"no command {command!r}; {HELP}")


def _serve(client: Client, lines: TextIO, out: TextIO, prompt: str) -> int:
    served = 0
    for line in lines:
        out.write(prompt)
        out.flush()
        try:
            answer = _line(client, line)
        except (LiveError, KeyError, ValueError, IndexError, OSError) as problem:
            out.write(f"error: {problem}\n")
            served += 1
            continue
        if answer is None:
            break
        served += 1
        if answer:
            out.write(answer + "\n")
    return served


def console(
    spec: str, script: str | None = None, timeout: float = 30.0
) -> dict[str, int]:
    """A console on the running image: one command a line (`help` lists them), from stdin or SCRIPT."""
    client = _client(spec, timeout)
    if script is None:
        return {
            "commands": _serve(
                client, sys.stdin, sys.stdout, "rxf> " if sys.stdin.isatty() else ""
            )
        }
    with open(script, encoding="utf-8") as lines:
        return {"commands": _serve(client, lines, sys.stdout, "")}
