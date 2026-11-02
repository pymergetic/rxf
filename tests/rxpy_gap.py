"""The measured gap between the Python strand and upstream uasyncio (docs/23 4.3, 4.5 and 7).

``measure()`` compiles the six pinned ``extmod/asyncio`` sources the way the strand would if their
imports were served and the file's own defs were bound where the file binds them (the file stands
as it is: a call of a sibling def names the function the file defines), and reports, per file, what
it takes and what it refuses, by name:

* ``imports`` -- the import statements of the file, and the first refusal of the file against the
  image as it is (the starter: none of ``time``, ``sys``, ``select``, ``io``, the package
  ``asyncio`` itself is in the module tree);
* ``statements`` -- every top-level statement and what became of it with its imports served and
  the file's own defs bound: a ``def`` compiles or is refused (the first reason), a ``class`` is a
  layout the strand accepts or refuses (its methods are checked per instance, and no instance of
  these exists, so each method is also tried as a def over a parameter ``self``, which is what a
  method of an instance held in a name is), anything else at module level is dropped by the
  strand's own rule;
* ``defs`` -- the contract of docs/23 section 7: each top-level def by name, and the first
  construct of the def that refuses it (``"compiles"`` where every construct of the def lowers --
  a callee's own refusal is the callee's row);
* ``reasons`` -- the histogram of the first refusals.

``tests/rxpy_asyncio_gap.json`` is the pinned answer; ``tests/test_rxpy_asyncio_gap.py`` compares
the measurement with it, and goes red the day the strand learns a construct. The regeneration path
is this module's command line (the JSON and the rows of the docs/23 table, from one measurement):

    PYTHONPATH=tools/native:. python -m tests.rxpy_gap --write

``--raw`` prints the verbatim refusal message per statement instead (the inspection behind the
stable reason names of the pin).
"""

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

import rxpy

from pymergetic.rxftk.core import tree

from .mp_reference import packages_dir
from .snippets import STARTER

PINNED = Path(__file__).parent / "rxpy_asyncio_gap.json"
FILES = ("core", "task", "event", "funcs", "lock", "stream")
# the modules the six files import; each is a module of the tree in the measurement that serves
# imports, and the sibling files are the modules of the package
KINDS = ("def", "class", "method")  # the order the table reads them in
SERVED = {"core", "task", "event", "funcs", "lock", "stream", "time", "sys", "select", "io", "errno"}

# the phrases of the strand's refusals, as the reason a histogram counts them under
REASONS = (
    ("an attribute of something that is not an imported module or an instance",
     "an object held in a name or a parameter (no cell the graph can resolve)"),
    ("a call of an attribute of something that is not an imported module or an instance",
     "a method call on an object held in a name or a parameter"),
    ("a call of class", "a class instantiated inside a def (a run-time allocation)"),
    ("an assignment to an attribute that is not a field", "an attribute store on an object held in a name"),
    ("a try/except", "try/except (the nlr seam of S11)"),
    ("a raise", "raise (the exception seam)"),
    ("an assert", "assert (the panic seam)"),
    ("a for loop", "for (the subset loops with while)"),
    ("a yield", "yield (the coroutine frame of 4.5)"),
    ("an await", "await (the coroutine frame of 4.5)"),
    ("an async def", "async def (the coroutine frame of 4.5)"),
    ("a subscript", "a subscript (a sequence cell)"),
    ("a lambda", "a lambda (a closure)"),
    ("a comprehension", "a comprehension (a closure)"),
    ("a nested def", "a nested def (a closure)"),
    ("a nonlocal", "nonlocal (a closure)"),
    ("a del", "del"),
    ("a string", "a string as a value"),
    ("a call of", "a call of a name the strand does not compile (a builtin or a face)"),
    ("takes a default", "a default parameter"),
    ("'s default is not an integer constant", "a default that folds to no word (None or a call)"),
    ("a decorator", "a decorator"),
    ("a conditional expression", "a conditional expression"),
    ("a list", "a list or dict or set value"),
    ("a dict", "a list or dict or set value"),
    ("the special method", "a special method (only __init__ is of the subset)"),
    ("does more than set", "an __init__ that does more than set fields"),
    ("no inheritance", "inheritance"),
    ("a class attribute", "a class attribute or a nested class"),
    ("None as a value", "None as a value (no word of its own)"),
    ("takes *args, **kwargs", "*args, **kwargs or a keyword-only parameter"),
    ("is a name the subset cannot bind", "a name the subset cannot bind (a class or a global of no cell)"),
    ("is a function, not a word", "a function as a value (a callee is not a word)"),
    ("a bare return", "a bare return (None is not a word)"),
    ("falls off the end", "a function that falls off the end (None is not a word)"),
    ("a NoneType as a value", "None as a value (a value is a word)"),
    ("names no module-level binding", "global of a name that is not an integer cell"),
    ("a module-level assignment that is not", "a module-level binding that is not NAME = constant"),
    ("a comparison the subset does not take", "is / is not / in / not in (identity and membership)"),
    ("as the callee of a call", "a callee that is not a name or an attribute"),
    ("at module level", "an expression or a control statement at module level"),
    ("not an integer constant", "a module-level binding that is not an integer constant"),
    ("an f-string", "an f-string"),
    ("a float", "a float"),
)


class Served(rxpy.Resolver):
    """A resolver that serves every import: the question of the measurement is what the strand
    refuses AFTER its imports, so every module exists and every other name is a function."""

    def __init__(self) -> None:
        super().__init__(None)

    def module_kind(self, path: str) -> str:
        return "module" if path.split(".")[-1] in SERVED | {"asyncio"} else "none"

    def member(self, parent: rxpy.Binding, name: str) -> rxpy.Binding | None:
        path = f"{parent.path}.{name}"
        if name in SERVED:
            return rxpy.Binding("module", path, loaded=frozenset({path}))
        return rxpy.Binding("function", path, rxpy.Sig(name, -1, 0))


def upstream() -> Path | None:
    root = packages_dir()
    found = root / "micropython" / "extmod" / "asyncio" if root else None
    return found if found is not None and found.is_dir() else None


def reason_of(message: str) -> str:
    """The stable name of a refusal: the first phrase of the table the message holds."""
    for phrase, name in REASONS:
        if phrase in message:
            return name
    text = re.sub(r"line \d+: ", "", message.split(": ", 1)[-1])
    # A message is "headline (explanation)": the headline is the group's name. A length cut
    # would stop mid-word and merge two messages that merely begin alike.
    return "other: " + text.split(" (", 1)[0]


def tried(
    text: str, module: str, only: set[str] | None = None, known: dict[str, str] | None = None
) -> str | None:
    """None when the text compiles with its imports served, else the refusal's message."""
    try:
        rxpy.graphs_of(text, module, only=only, known=known, resolver=Served())
    except rxpy.Refused as problem:
        return str(problem)
    return None


def source_of(text: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(text, node, padded=True) or ""
    lines = segment.splitlines()
    indent = len(lines[0]) - len(lines[0].lstrip()) if lines else 0
    return "\n".join(line[indent:] for line in lines)


def survey(path: Path, name: str, raws: dict[str, str] | None = None) -> dict:
    """What the strand does with one source: its imports against the image as it is, then every
    top-level statement with its imports served and the file's own defs bound where the file
    binds them (the file stands as it is: a call of a sibling def names the function the file
    defines, so a def's refusal is a construct OF THE DEF).  ``raws``, when given, collects the
    verbatim refusal message per statement (the inspection the ``--raw`` mode prints; the pin
    stores the stable reason name instead)."""
    text = path.read_text()
    module = ast.parse(text)
    imports = [n for n in module.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    header = "\n".join(ast.unparse(n) for n in imports)
    package = f"asyncio.{name}"
    known = {
        node.name: f"{package}.{node.name}"
        for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    image = tree.load(STARTER)
    first = None
    try:
        rxpy.graphs_of(text, package, resolver=rxpy.Resolver(image))
    except rxpy.Refused as problem:
        first = str(problem).split(": ", 1)[-1]
    reasons: Counter[str] = Counter()
    kinds = {"def": [0, 0], "class": [0, 0], "method": [0, 0], "dropped": 0}
    defs: list[list[str]] = []  # pairs in the file's order: the pin's keys sort, the table does not
    for node in module.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        segment = f"{header}\n{source_of(text, node)}\n"
        if isinstance(node, ast.FunctionDef):
            bad = tried(segment, package, known=known)
            kinds["def"][1 if bad else 0] += 1
            defs.append([node.name, reason_of(bad) if bad else "compiles"])
            key = node.name
        elif isinstance(node, ast.ClassDef):
            bad = tried(segment, package, known=known)
            kinds["class"][1 if bad else 0] += 1
            key = f"class {node.name}"
        else:
            bad = tried(segment, package, known=known)
            if bad:
                kinds["dropped"] += 1
            key = f"line {node.lineno}"
        if bad:
            reasons[reason_of(bad)] += 1
            if raws is not None:
                raws[f"{name}.{key}"] = bad
        if isinstance(node, ast.ClassDef):
            for method in (m for m in node.body if isinstance(m, ast.FunctionDef)):
                if method.name.startswith("__") and method.name.endswith("__"):
                    continue
                bad = tried(f"{header}\n{source_of(text, method)}\n", package, known=known)
                kinds["method"][1 if bad else 0] += 1
                if bad:
                    reasons[reason_of(bad)] += 1
                    if raws is not None:
                        raws[f"{name}.{node.name}.{method.name}"] = bad
    return {
        "imports": {"statements": len(imports), "first_refusal": first},
        "compiles": {k: v for k, v in kinds.items() if k != "dropped"},
        "defs": defs,
        "dropped": kinds["dropped"],
        "reasons": dict(sorted(reasons.items(), key=lambda item: (-item[1], item[0]))),
    }


def measure() -> dict[str, dict]:
    root = upstream()
    assert root is not None, "no upstream MicroPython checkout in the workspace"
    return {name: survey(root / f"{name}.py", name) for name in FILES}


def rows(measured: dict[str, dict]) -> list[str]:
    """The measurement as the rows of the docs/23 section 7 table (one per file) and the
    histogram of first refusals over all six."""
    out = ["| file | statements of the file served | compiles (ok / refused) | first refusal against the image |", "|---|---|---|---|"]
    for name in FILES:
        found = measured[name]
        compiles = found["compiles"]
        cells = ", ".join(f"{kind} {compiles[kind][0]}/{compiles[kind][1]}" for kind in KINDS)
        out.append(
            f"| `{name}.py` | {found['imports']['statements']} imports, {found['dropped']} dropped | "
            f"{cells} | {found['imports']['first_refusal']} |"
        )
    total: Counter[str] = Counter()
    for name in FILES:
        total.update(measured[name]["reasons"])
    out += ["", "| def | the first construct that refuses it |", "|---|---|"]
    for name in FILES:
        for fn, reason in measured[name]["defs"]:
            out.append(f"| `{fn}` (`{name}.py`) | {reason} |")
    out += ["", "| reason (first refusal of a def, a method, a class or a statement) | count |", "|---|---|"]
    out += [
        f"| {reason} | {count} |"
        for reason, count in sorted(total.items(), key=lambda item: (-item[1], item[0]))
    ]
    return out


def main(argv: list[str]) -> int:
    if "--raw" in argv:
        root = upstream()
        assert root is not None, "no upstream MicroPython checkout in the workspace"
        for name in FILES:
            raws: dict[str, str] = {}
            survey(root / f"{name}.py", name, raws)
            for key, message in raws.items():
                sys.stdout.write(f"{key}: {message}\n")
        return 0
    measured = measure()
    if "--write" in argv:
        PINNED.write_text(json.dumps(measured, indent=2, sort_keys=True) + "\n")
        docs = Path(__file__).parents[1] / "docs" / "23-MICROPYTHON-EXPANSION.md"
        text = docs.read_text()
        begin = text.index("<!-- rxpy_gap:begin")
        made = "\n".join(rows(measured)) + "\n"
        head = text[: text.index("\n", begin) + 1]
        tail = text[text.index("<!-- rxpy_gap:end -->") :]
        docs.write_text(head + made + tail)
    sys.stdout.write("\n".join(rows(measured)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
