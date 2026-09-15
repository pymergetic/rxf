"""face.py — CLI entry point primitives.

Provides: mint, inspect, replay, certify, dump, layout — the operations
on state binaries.  Each takes a path and returns a result dict.

Canonical pack/unpack goes through output engine (BinaryLayout).
"""

import hashlib
import json
from pathlib import Path

from pymergetic.rxf.bridge import container_to_layout, layout_to_container
from pymergetic.rxf.canon import canon
from pymergetic.rxf.checker import check
from pymergetic.rxf.dump import dump as hexdump
from pymergetic.rxf.execution.binder import candidate_details
from pymergetic.rxf.execution.binder import preflight as build_preflight
from pymergetic.rxf.execution.decode import decode_code
from pymergetic.rxf.expand import Template
from pymergetic.rxf.layout import cell_map as _cell_map
from pymergetic.rxf.layout import relations as _relations
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.output.engine import pack_layout, unpack_layout


def _blob_digest(blob: bytes) -> bytes:
    return hashlib.sha256(blob).digest()


def mint(input_path: str, output_path: str | None = None) -> dict:
    """Mint a .rxf file from JSON-as-code input."""
    try:
        with open(input_path) as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return {"ok": False, "errors": [str(e)]}

    norm = canon(raw)
    container = Container.from_dict(norm)

    errors = check(container)
    if errors:
        return {"ok": False, "errors": errors}

    layout = container_to_layout(container)
    blob = pack_layout(layout)
    dgst = _blob_digest(blob)

    if output_path:
        Path(output_path).write_bytes(blob)

    return {
        "ok": True,
        "path": output_path,
        "size": len(blob),
        "digest": dgst.hex(),
        "nodes": len(container.nodes),
        "heap": unpack_layout(blob).heap.model_dump(exclude={"cells"}),
    }


def inspect(path: str) -> dict:
    """Inspect a .rxf file — unpack through output engine, bridge to Container."""
    blob = Path(path).read_bytes()
    layout = unpack_layout(blob)
    container = layout_to_container(layout)
    d = container.to_dict()
    d["size"] = len(blob)
    return {"ok": True, "container": d, "size": len(blob)}


def replay(path: str) -> dict:
    """Replay a .rxf file — unpack and re-pack through output engine."""
    blob = Path(path).read_bytes()
    layout = unpack_layout(blob)
    blob2 = pack_layout(layout)
    same = blob == blob2
    return {
        "ok": same,
        "size_original": len(blob),
        "size_replayed": len(blob2),
        "identical": same,
    }


def certify(path: str) -> dict:
    """Certify a .rxf file — unpack, check, report."""
    blob = Path(path).read_bytes()
    layout = unpack_layout(blob)
    container = layout_to_container(layout)
    errors = check(container)
    dgst = _blob_digest(blob)
    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "digest": dgst.hex(),
        "nodes": len(container.nodes),
        "heap": layout.heap.model_dump(exclude={"cells"}),
    }


def dump_cmd(path: str) -> dict:
    """Annotated hex dump of a .rxf file."""
    blob = Path(path).read_bytes()
    return {"ok": True, "dump": hexdump(blob)}


def layout_cmd(path: str) -> dict:
    """Memory layout analysis of a .rxf file."""
    blob = Path(path).read_bytes()
    layout = unpack_layout(blob)
    container = layout_to_container(layout)
    return {
        "ok": True,
        "cell_map": _cell_map(container),
        "relations": _relations(container),
    }


def mint_from_template(template: Template, output_path: str | None = None) -> dict:
    """Mint a .rxf file from a Template object (class-based, no JSON)."""
    container = template.build()
    errors = check(container)
    if errors:
        return {"ok": False, "errors": errors}
    layout = container_to_layout(container)
    blob = pack_layout(layout)
    dgst = _blob_digest(blob)
    if output_path:
        Path(output_path).write_bytes(blob)
    return {
        "ok": True,
        "path": output_path,
        "size": len(blob),
        "digest": dgst.hex(),
        "nodes": len(container.nodes),
        "heap": unpack_layout(blob).heap.model_dump(exclude={"cells"}),
    }


def preflight(path: str, entry_function: int, target_id: int) -> dict:
    """Build an atomic transient native BindingPlan; never execute Code."""
    blob = Path(path).read_bytes()
    container = layout_to_container(unpack_layout(blob))
    plan = build_preflight(container, entry_function, target_id)
    return {
        "ok": plan.ok,
        "entry_function": str(plan.entry_function),
        "active_target": str(plan.target_id),
        "selected": [
            {
                "function_id": str(item.function_id),
                "code_id": str(item.code_id),
                "rank": list(item.rank),
            }
            for item in plan.bindings
        ],
        "refusals": [
            {
                "code": item.code.value,
                "object_id": str(item.object_id),
                "message": item.message,
            }
            for item in plan.diagnostics
        ],
        "candidates": [
            {
                "code_id": str(item.code_id),
                "target_id": str(item.target_id),
                "compatible": item.compatible,
                "rank": None if item.rank is None else list(item.rank),
                "reason": item.reason,
            }
            for item in plan.candidates
        ],
        "health": "ready" if plan.ok else "refused",
    }


def targets(path: str, active_target: int | None = None) -> dict:
    """Inspect runtime targets and native candidate health without binding entry."""
    blob = Path(path).read_bytes()
    container = layout_to_container(unpack_layout(blob))
    from pymergetic.rxf.ty.builtins import CODE_TYPE, RUNTIME_TARGET_TYPE, TARGET_TYPE

    target_ids = sorted(
        node.id
        for node in container.nodes
        if node.type_id in (RUNTIME_TARGET_TYPE, TARGET_TYPE)
    )
    functions = sorted(
        {
            decode_code(node).owner_function
            for node in container.nodes
            if node.type_id == CODE_TYPE
        }
    )
    details = []
    if active_target is not None:
        for function_id in functions:
            ranked = candidate_details(container, function_id, active_target)
            details.append(
                {
                    "function_id": str(function_id),
                    "candidates": [
                        {
                            "code_id": str(item.code_id),
                            "target_id": str(item.target_id),
                            "compatible": item.compatible,
                            "rank": None if item.rank is None else list(item.rank),
                            "reason": item.reason,
                        }
                        for item in ranked
                    ],
                }
            )
    return {
        "ok": True,
        "active_target": None if active_target is None else str(active_target),
        "targets": [str(value) for value in target_ids],
        "candidates": details,
    }


def compile(path: str, entry_function: int) -> dict:
    """Compile a composed Function to deterministic target-ready native artifacts."""
    from pymergetic.rxf.compiler import CompileError, compile_targets

    blob = Path(path).read_bytes()
    container = layout_to_container(unpack_layout(blob))
    try:
        artifact = compile_targets(container, entry_function)
    except (CompileError, ValueError) as error:
        return {"ok": False, "errors": [str(error)]}
    return {"ok": True, "artifact": artifact.inspect()}
