"""The terminal of `rxf console`: what the user types, and what the image answers.

A program is read until a blank line (or a line of one ``.``). The whole thing goes to the
running image as one object of the arena, the in-image front end compiles it, and the words it
writes are installed as a Function. The outcome is the Function the image installed, or the
line its front end stopped at.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from pathlib import Path
from typing import Any, TextIO

from pymergetic.rxftk.core.delta import DeltaError
from pymergetic.rxftk.honors import Honors, HonorsError
from pymergetic.rxftk.live.front import Front, Installed, Verdict

PROGRAM = re.compile(r"(?m)^[ \t]*program[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(")
TERMINATOR = "."
REFUSED = "RXF_REFUSE_"
NO_NAME = "the source names no program (`program NAME(...)`); give --name="


def name_of(source: str) -> str | None:
    """The name a program gives itself: what its Function is called."""
    found = PROGRAM.findall(source)
    return found[0] if found else None


def beside(path: Path) -> dict[str, bytes]:
    """The table of the files an `include` can name: the sources beside the file (docs/19)."""
    return {p.name: p.read_bytes() for p in sorted(path.parent.glob("*.rxfs"))}


def report(installed: Installed) -> dict[str, Any]:
    """What the terminal prints when the image installed a Function."""
    return {
        "ok": True,
        "path": installed.path,
        "identity": f"0x{installed.identity:016x}",
        "code_identity": f"0x{installed.code_identity:016x}",
        "bytes": installed.byte_count,
        "digest": installed.digest,
    }


def refusal(verdict: Verdict, table: Mapping[str, int]) -> dict[str, Any]:
    """The line the front end stopped at, and the refusal it stopped with, by name."""
    named = [
        name
        for name, value in table.items()
        if value == verdict.status and name.startswith(REFUSED)
    ]
    name = named[0][len(REFUSED) :] if named else f"status {verdict.status}"
    return {
        "ok": False,
        "line": verdict.at,
        "status": verdict.status,
        "refusal": name,
        "message": f"compile error at line {verdict.at}: refused {name}",
    }


class Console:
    """The terminal on one running image: type `.rxfs`, the image compiles and installs it.

    Every operation of the terminal runs inside the gate of the channel it speaks on — the
    transaction that travels with its front (`console/world.py` `World.front`) — so an editor
    install, a feed read or a second console never lands in the middle of one: not between the
    compile and the install, not inside a request. A front that shares its channel with nobody
    has no gate and needs none.
    """

    def __init__(
        self,
        front: Front,
        out: TextIO,
        files: Mapping[str, bytes] | None = None,
        honors: Honors | None = None,
    ) -> None:
        self._front = front
        self._out = out
        self._files = dict(files or {})
        self._honors = honors
        self._transaction = getattr(front, "transaction", nullcontext)

    def submit(
        self,
        source: str,
        name: str | None = None,
        files: Mapping[str, bytes] | None = None,
    ) -> dict[str, Any]:
        """One program through the image: compiled by its front end and installed as a Function,
        or refused with the line it stopped at. FILES is what an `include` of the program can
        name. The whole compile-then-install is one operation on the channel: it holds the gate
        (`World.transaction`) around both, so nothing lands between them."""
        wanted = name or name_of(source)
        if wanted is None:
            return {"ok": False, "message": NO_NAME}
        data = source.encode()
        with self._transaction():
            verdict = self._front.compile(data, self._files if files is None else files)
            if not verdict.ok:
                return refusal(verdict, self._front.numbers())
            return report(self._front.install(wanted, verdict.words, data))

    def one_shot(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        """One source file through the same path the loop takes. The `.rxfs` sources beside the
        file travel with it: they are what an `include` of the program can name."""
        file = Path(path)
        return self.submit(
            file.read_text(encoding="utf-8"), name=name, files=beside(file)
        )

    def listed(self) -> dict[str, Any]:
        """What the image has installed (read-only facts)."""
        with self._transaction():
            entries = self._front.installed()
        return {
            "installed": [
                {"path": entry.path, "identity": f"0x{entry.identity:016x}"}
                for entry in entries
            ]
        }

    def repl(self, lines: Iterable[str], prompt: bool = True) -> dict[str, Any]:
        """The loop: read a program, have the image compile and install it, print the outcome.

        A blank line (or a line of one `.`) sends what was typed. `:list` shows what the image
        has installed, `:help` the words of the terminal, `:quit` leaves.
        """
        stream = iter(lines)
        buffer: list[str] = []
        summary: dict[str, Any] = {"submitted": 0, "installed": [], "refused": 0}
        while True:
            if prompt:
                self._out.write("rxf> " if not buffer else "... ")
                self._out.flush()
            try:
                line = next(stream)
            except StopIteration:
                break
            text = line.rstrip("\n")
            if not buffer:
                if text.strip() == TERMINATOR:
                    continue
                if text.strip().startswith(":"):
                    if self._meta(text.strip()):
                        break
                    continue
                if not text.strip():
                    continue
            elif not text.strip() or text.strip() == TERMINATOR:
                self._outcome(self._safely("\n".join(buffer) + "\n"), summary)
                buffer = []
                continue
            buffer.append(text)
        if buffer:
            self._outcome(self._safely("\n".join(buffer) + "\n"), summary)
        return summary

    def _safely(self, source: str) -> dict[str, Any]:
        """A program that cannot even be sent answers like a refused one: the loop goes on."""
        try:
            return self.submit(source)
        except (DeltaError, ValueError) as problem:
            return {"ok": False, "message": str(problem)}

    def _meta(self, word: str) -> bool:
        """One word of the terminal (`:quit` answers True: leave)."""
        parts = word.split(" ", 1)
        head, rest = parts[0], parts[1] if len(parts) > 1 else ""
        if head in (":quit", ":q"):
            return True
        if head == ":list":
            with self._transaction():
                entries = self._front.installed()
            for entry in entries:
                self._out.write(f"{entry.path} (id 0x{entry.identity:016x})\n")
            return False
        if head == ":honors":
            self._honors_word(rest)
            return False
        if head == ":about":
            self._about_word(rest)
            return False
        if head == ":honor":
            self._honor_word(rest)
            return False
        if head == ":help":
            self._out.write(
                "a program of .rxfs ends at a blank line (or a line of one `.`)\n"
                ":list   what the image has installed\n"
                ":honors  the founding core and the ledger (docs/36)\n"
                ":about NAME  one person or one work, with its refs\n"
                ":honor NAME ROLE TEXT  one entry onto the ledger about NAME\n"
                ":quit   leave\n"
            )
            return False
        self._out.write(f"no such word: {word} (:help shows the words)\n")
        return False

    def _honors_of(self, word: str) -> Honors:
        """The honors of the image this terminal talks to, or the truth about a v6
        image: it carries none (docs/36 4 - the honors live in the image)."""
        if self._honors is None:
            raise HonorsError(f"this image carries no honors ({word} reads them)")
        return self._honors

    def _honors_word(self, rest: str) -> None:
        if rest.strip():
            self._out.write(":honors takes no name (about NAME does)\n")
            return
        try:
            report = self._honors_of(":honors").who()
        except HonorsError as problem:
            self._out.write(f"{problem}\n")
            return
        for group in ("roles", "people", "works", "ledger"):
            self._out.write(f"{group}\n")
            for member in report[group]:
                self._out.write(f"  {member['name']}  {member['text']}\n")

    def _about_word(self, rest: str) -> None:
        name = rest.strip()
        if not name:
            self._out.write(":about NAME - one person or one work\n")
            return
        try:
            found = self._honors_of(f":about {name}").about(name)
        except HonorsError as problem:
            self._out.write(f"{problem}\n")
            return
        self._out.write(f"{found['name']} ({found['kind']})\n")
        self._out.write(f"  {found['text']}\n")
        for ref in found["refs"]:
            self._out.write(
                f"  {ref['role']} -> {ref['target']} ({ref['index']})\n"
            )
        for entry in found["vouched"]:
            self._out.write(f"  vouched by {entry}\n")

    def _honor_word(self, rest: str) -> None:
        words = rest.split(" ", 2)
        if len(words) < 3 or not all(words):
            self._out.write(":honor NAME ROLE TEXT - one entry onto the ledger\n")
            return
        name, role, text = words
        try:
            entry = self._honors_of(f":honor {name}").append(
                text, about=name, role=role, by="console"
            )
        except HonorsError as problem:
            self._out.write(f"{problem}\n")
            return
        self._out.write(f"appended {entry} about {name} with {role}\n")

    def _outcome(self, outcome: Mapping[str, Any], summary: dict[str, Any]) -> None:
        summary["submitted"] += 1
        if outcome.get("ok"):
            summary["installed"].append(outcome["path"])
            self._out.write(
                f"installed {outcome['path']} "
                f"(id {outcome['identity']}, {outcome['bytes']} bytes)\n"
            )
        else:
            summary["refused"] += 1
            self._out.write(f"{outcome['message']}\n")
