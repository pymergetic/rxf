"""Commands on deltas: `rxf core delta <command> SOURCE ...`."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from pymergetic.rxf import source
from pymergetic.rxf.live import harness, physical


def show(src: str, module: str | None = None) -> str:
    """The canonical delta as XML; with `module` only that module without its sub-modules."""
    return source.load(src).export(module=module)


def apply(
    src: str, *deltas: str, to: str | None = None, overwrite: bool = False
) -> dict[str, Any]:
    """Replays the DELTA files on SOURCE, in the order given, as one atomic step.

    Nothing is written unless every delta applies and the result links. With
    `--to=TARGET` the result is saved there (`.rxfd`, `.rxfb`, or a module tree).
    """
    image = source.load(src)
    image.apply_all([(name, Path(name).read_text(encoding="utf-8")) for name in deltas])
    report = source.report(image)
    if to is not None:
        report["format"] = source.save(image, to, overwrite)
        report["target"] = to
    return report


def compile(src: str, *deltas: str, to: str, check_base: bool = True) -> dict[str, Any]:
    """Compiles the XML DELTA files on SOURCE into one physical delta (the form a running image takes).

    The physical delta carries what is there afterwards, object by object, addressed by path. It
    is the difference between SOURCE and SOURCE with the deltas applied, so it is the same
    whatever tool made the change. With `--no-check-base` the delta applies to any image.
    """
    base = source.load(src)
    texts = [(name, Path(name).read_text(encoding="utf-8")) for name in deltas]
    compiled = physical.from_xml(base, *texts, check_base=check_base)
    Path(to).write_bytes(compiled.data)
    return {
        "target": to,
        "bytes": len(compiled.data),
        "digest": hashlib.sha256(compiled.data).hexdigest(),
        "creates": compiled.creates,
        "sets": compiled.sets,
        "deletes": compiled.deletes,
        "entry": compiled.entry,
        "base": base.digest() if check_base else None,
    }


def inspect(delta: str) -> list[dict[str, Any]]:
    """The operations of a physical delta file (payloads left out)."""
    out: list[dict[str, Any]] = []
    for operation in physical.decode(Path(delta).read_bytes()):
        out.append(
            {
                key: (len(value) if isinstance(value, bytes) else value)
                for key, value in operation.items()
                if key not in ("patches", "refs")
            }
            | {
                key: len(operation[key])
                for key in ("patches", "refs")
                if key in operation
            }
        )
    return out


def patch(
    src: str, delta: str, to: str | None = None, overwrite: bool = False
) -> dict[str, Any]:
    """Replays a physical DELTA on SOURCE, with the code of the live layer, and saves the result.

    The result of `compile` then `patch` is the image that `apply` of the XML gives, and it can be
    written as XML again with `--to=result.rxfd`.
    """
    image = source.load(src)
    result = harness.replay(image.to_bytes(), Path(delta).read_bytes())
    report = source.report(result)
    if to is not None:
        report["format"] = source.save(result, to, overwrite)
        report["target"] = to
    return report
