"""The one `rxf` entry point. A command lives next to what it operates on.

Every `cli.py` below this package defines the commands of its own package: the
public functions of `core/object/cli.py` are `rxf core object <function>`, those
of `server/cli.py` are `rxf server <function>`. The command path is the module
path, and a new `cli.py` is a new command group with no list to keep. A group
whose one command is named after the group speaks for it (`console/cli.py` is
`rxf console`). Python Fire does the parsing; results print as JSON (text as
text).

The files are loaded from their path, not imported by name: a package whose body
is the compiled core has no search path for its C++ directories.

Exit status: 0 done, 1 the image has problems, 2 the input could not be used.
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import fire

import pymergetic.rxf
from pymergetic.rxf.core.arena import ArenaError
from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.tree import TreeError
from pymergetic.rxf.live.client import LiveError
from pymergetic.rxf.live.link import LinkError

NAME = "rxf"
FILE = "cli.py"
EXIT_PROBLEMS = 1
EXIT_UNUSABLE = 2


class Problems(SystemExit):
    """A command finished and found problems; `main` prints the report and exits with 1."""

    def __init__(self, report: Any) -> None:
        super().__init__(EXIT_PROBLEMS)
        self.report = report


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _commands_of(module: ModuleType) -> dict[str, Any]:
    return {
        name: function
        for name, function in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("_") and function.__module__ == module.__name__
    }


def root() -> dict[str, Any]:
    """The command tree: one group per package that has a cli.py, nested like the packages."""
    # An editable install lists more than one directory for the package.
    found: dict[tuple[str, ...], Path] = {}
    for base in map(Path, pymergetic.rxf.__path__):
        for path in sorted(base.rglob(FILE)):
            parts = path.relative_to(base).parent.parts
            if parts:  # not this file
                found.setdefault(parts, path)
    tree: dict[str, Any] = {}
    for parts, path in sorted(found.items()):
        module = _load(path, ".".join((pymergetic.rxf.__name__, *parts, "cli")))
        commands = _commands_of(module)
        group = tree
        for part in parts[:-1]:
            group = group.setdefault(part, {})
        if len(commands) == 1 and parts[-1] in commands:
            # One command named after its group speaks for the group: `rxf console FILE` is the
            # own command of `console/cli.py`, not `rxf console console FILE`.
            group[parts[-1]] = commands[parts[-1]]
        else:
            group = group.setdefault(parts[-1], {})
            group.update(commands)
    return tree


def _is_group(result: object) -> bool:
    if not isinstance(result, dict):
        return False
    return any(
        callable(value) or isinstance(value, dict)
        for value in cast(dict[str, object], result).values()
    )


def _listing(group: dict[str, Any], prefix: str) -> list[str]:
    lines: list[str] = []
    for name, member in sorted(group.items()):
        if isinstance(member, dict):
            lines += _listing(cast(dict[str, Any], member), f"{prefix} {name}")
        else:
            summary = (inspect.getdoc(member) or "").splitlines()[:1]
            lines.append(f"{prefix} {name}".ljust(32) + (summary[0] if summary else ""))
    return lines


def _prefix(commands: dict[str, Any], arguments: Sequence[str]) -> str:
    """`rxf core object` for the arguments `core object`: the group the listing is of."""
    node: dict[str, Any] = commands
    words = [NAME]
    for word in arguments:
        child = node.get(word)
        if not isinstance(child, dict):
            break
        node = cast(dict[str, Any], child)
        words.append(word)
    return " ".join(words)


def _show(result: object, prefix: str) -> str:
    """Text as text, a command group as its command list, anything else as JSON."""
    if isinstance(result, str):
        return result
    if _is_group(result):
        return "\n".join(_listing(cast(dict[str, Any], result), prefix))
    return json.dumps(result, indent=2)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    # Fire pipes its help through `less` on a terminal; a command line tool prints and ends.
    os.environ.setdefault("PAGER", "cat")
    commands = root()
    try:
        fire.Fire(
            commands,
            command=arguments,
            name=NAME,
            serialize=lambda result: _show(result, _prefix(commands, arguments)),
        )
    except Problems as stop:
        print(json.dumps(stop.report, indent=2))
        return EXIT_PROBLEMS
    except (ArenaError, DeltaError, TreeError, LiveError, LinkError, OSError) as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        return EXIT_UNUSABLE
    return 0
