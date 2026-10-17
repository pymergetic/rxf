"""The arch proxy of the starter: one function, the code of every machine, one place to ask.

A native function is one identity (``x.y.z``) with a ``Code`` object per ``RuntimeTarget``:
the bytes of that machine, the ABI that says how to call it, and the recipe that built it.
A function of only a bytecode program is never part of this: its one program runs on every
machine. This tool is where a person or a build asks the proxy::

    python tools/native/arch_proxy.py targets                    the target map
    python tools/native/arch_proxy.py resolve x.y.z [--target T]  the code, per target or one
    python tools/native/arch_proxy.py gaps [--target T]          what a target still lacks
    python tools/native/arch_proxy.py build [--target T] [--check]

``resolve`` names the code a seat of that machine runs -- or the refusal ``no code for
<T>``, the same words the checker of the binary uses. ``gaps`` is the ``(function, target)``
work list of a target that is not built yet; it exits 1 while the set is not empty.
``build`` runs the pinned recipes of ``native_build.py`` and the derivations of ``sync.py``
(there is no incremental build: the sources compile to the whole set) and prints the gaps
before and after, so the gap that was the reason to build is seen closing.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
import graph_sync

ROOT = HERE.parents[1]
RXF = "{urn:pymergetic:rxf}"
NO_CODE = "no code for"


def word(value: str | None) -> str:
    return (value or "").split(".")[-1]


def targets(starter: Path) -> dict[str, dict[str, str]]:
    """``{target: {"architecture", "abi", "environment"}}`` as the kinds say them."""
    document = ET.parse(starter / "pymergetic" / "rxf" / "targets.rxfd")
    kinds: dict[str, str] = {}
    for tag in ("Architecture", "ABI", "Environment"):
        for element in document.getroot().iter(tag):
            kinds[word(element.get("name"))] = word(element.get("kind"))
    found: dict[str, dict[str, str]] = {}
    for element in document.getroot().iter("RuntimeTarget"):
        found[word(element.get("name"))] = {
            "architecture": kinds[word(element.get("architecture"))],
            "abi": kinds[word(element.get("abi"))],
            "environment": kinds[word(element.get("environment"))],
        }
    return dict(sorted(found.items()))


def functions(starter: Path) -> dict[str, dict[str, dict[str, str]]]:
    """``{function: {target: code facts}}`` of the functions that are machine code.

    A function whose only ``Code`` has no ``runtime_target`` is a program of the
    interpreter; it runs everywhere and is left out of the proxy entirely.
    """
    found: dict[str, dict[str, dict[str, str]]] = {}
    for path in sorted(starter.rglob("*.rxfd")):
        parts = path.relative_to(starter).with_suffix("").parts
        for label, function in graph_sync.functions_in(
            ET.parse(path).getroot(), graph_sync.module_prefix(parts)
        ):
            codes: dict[str, dict[str, str]] = {}
            for code in function:
                if code.tag != "Code":
                    continue
                target = code.get("runtime_target")
                if target is None:
                    continue
                refs = {
                    word(r.get("role")): word(r.get("target"))
                    for r in code.findall(f"{RXF}ref")
                }
                codes[word(target)] = {
                    "name": word(code.get("name")),
                    "format": word(code.get("format")),
                    "byte_count": code.get("byte_count") or "0",
                    "provenance": refs.get("Provenance", ""),
                    "abi": refs.get("AbiSignature", ""),
                }
            if codes:
                found[label] = codes
    return dict(sorted(found.items()))


def resolve(
    starter: Path, label: str, target: str | None = None
) -> dict[str, dict[str, str]]:
    """The code of one function: for a chosen target, or for every target.

    A target without code answers the refusal ``no code for <target>`` -- the same
    words the checker of the binary names, so a build that misses a target is told
    the same thing a seat is.
    """
    codes = functions(starter).get(label)
    if codes is None:
        raise SystemExit(f"{label}: not a function of the starter")
    wanted = [target] if target is not None else sorted(codes)
    answer: dict[str, dict[str, str]] = {}
    for name in sorted(wanted):
        code = codes.get(name)
        if code is None:
            answer[name] = {"refusal": f"{NO_CODE} {name}"}
        else:
            answer[name] = code
    return answer


def gaps(starter: Path, target: str | None = None) -> list[tuple[str, str]]:
    """The ``(function, target)`` pairs that have no code: a target's work list."""
    known = targets(starter)
    if target is not None and target not in known:
        raise SystemExit(f"{target}: not a target of the starter")
    wanted = set(known) if target is None else {target}
    missing: list[tuple[str, str]] = []
    for label, codes in functions(starter).items():
        missing.extend((label, name) for name in sorted(wanted - set(codes)))
    return missing


def build(
    starter: Path, target: str | None = None, check: bool = False
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Run the pinned recipes and the derivations; answer the gaps before and after."""
    import sync  # the toolchain is only needed here

    before = gaps(starter, target)
    sync.sync(starter, check=check)
    return before, gaps(starter, target)


# ---- the command line ---------------------------------------------------------------------------

USAGE = "targets | resolve FUNCTION [--target T] | gaps [--target T] | build [--target T] [--check]"


def line(label: str, target: str, code: dict[str, str]) -> str:
    if "refusal" in code:
        return f"{label} {target}: {code['refusal']}"
    facts = " ".join(f"{k}={v}" for k, v in code.items() if v)
    return f"{label} {target}: {facts}"


def main() -> None:
    arguments = sys.argv[1:]
    verb = arguments[0] if arguments else ""
    rest = arguments[1:]
    target = None
    check = "--check" in rest
    if "--target" in rest:
        at = rest.index("--target")
        target = rest[at + 1]
        rest = rest[:at] + rest[at + 2 :]
    positional = [a for a in rest if not a.startswith("--")]
    starter = ROOT / "starter"
    if positional and Path(positional[-1]).is_dir():
        starter = Path(positional.pop())
    if verb == "targets":
        for name, info in targets(starter).items():
            print(name, " ".join(f"{k}={v}" for k, v in info.items()))
        return
    if verb == "resolve" and len(positional) == 1:
        for name, code in resolve(starter, positional[0], target).items():
            print(line(positional[0], name, code))
        return
    if verb == "gaps":
        missing = gaps(starter, target)
        for label, name in missing:
            print(f"{label}: {NO_CODE} {name}")
        raise SystemExit(1 if missing else 0)
    if verb == "build":
        before, after = build(starter, target, check)
        for label, name in before:
            if (label, name) not in after:
                print(f"closed {label}: {NO_CODE} {name}")
        for label, name in after:
            print(f"{label}: {NO_CODE} {name}")
        raise SystemExit(1 if after else 0)
    raise SystemExit(f"usage: {USAGE}")


if __name__ == "__main__":
    main()
