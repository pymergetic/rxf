"""FastAPI and Jinja2 application for browsing mounted RXF binaries."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

from pymergetic.rxf.checker import check
from pymergetic.rxf.execution.binder import preflight
from pymergetic.rxf.execution.decode import (
    decode_code,
    decode_function,
    decode_import,
    decode_relocation,
    decode_signature,
    decode_target,
)
from pymergetic.rxf.model.execution import CallRole, Effect
from pymergetic.rxf.model.generics import (
    GenericArgument,
    GenericParameter,
    Specialization,
    Template,
)
from pymergetic.rxf.model.module import derived_fqns
from pymergetic.rxf.model.target import (
    ABIObject,
    ArchitectureObject,
    EnvironmentObject,
    FeatureObject,
    FeatureSetObject,
    RuntimeTargetObject,
)
from pymergetic.rxf.model.traits import (
    AssociatedType,
    Conformance,
    ImplementationBinding,
    Trait,
    TraitRequirement,
)
from pymergetic.rxf.output.header import BinaryHeader
from pymergetic.rxf.output.node import NodeEntry, RefBinding, RefKind
from pymergetic.rxf.schema import NODE_INVALID, NodeKind
from pymergetic.rxf.server.config import ServerConfig
from pymergetic.rxf.server.state import RXFLibrary, RXFSnapshot
from pymergetic.rxf.ty.builtins import (
    ABI_SIGNATURE_TYPE,
    ABI_TYPE,
    ARCHITECTURE_TYPE,
    ARGUMENT_TYPE,
    ASSOCIATED_TYPE_TYPE,
    CALL_TYPE,
    CODE_TYPE,
    CONFORMANCE_TYPE,
    ENVIRONMENT_TYPE,
    FEATURE_SET_TYPE,
    FEATURE_TYPE,
    FUNCTION_TYPE,
    GENERIC_ARGUMENT_TYPE,
    GENERIC_PARAMETER_TYPE,
    IMPLEMENTATION_BINDING_TYPE,
    IMPORT_TYPE,
    NUMERIC_CONTRACT_TYPE,
    PARAMETER_TYPE,
    REFUSAL_SET_TYPE,
    REFUSAL_VARIANT_TYPE,
    RELOCATION_TYPE,
    RESULT_TYPE,
    RUNTIME_TARGET_TYPE,
    SIGNATURE_TYPE,
    SPECIALIZATION_TYPE,
    TARGET_TYPE,
    TEMPLATE_TYPE,
    TRAIT_REQUIREMENT_TYPE,
    TRAIT_TYPE,
    VALUE_TYPE,
)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_TEMPLATES = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape(("html", "jinja2")),
    auto_reload=True,
)


def _object_id(value: int) -> str:
    """Serialize a uint64 object identity without losing precision in JavaScript."""
    return str(value)


def _node_view(node: NodeEntry, fqn: str | None = None) -> dict[str, Any]:
    return {
        "id": _object_id(node.id),
        "name": node.name,
        "fqn": node.name if fqn is None else fqn,
        "kind": node.kind.name,
        "parent": None if node.parent == NODE_INVALID else _object_id(node.parent),
        "type_id": _object_id(node.type_id),
        "flags": node.state.pack(),
        "state": node.state.to_dict(),
        "owner_kind": node.owner_kind.name,
        "owner": None if node.owner == NODE_INVALID else _object_id(node.owner),
        "ref_count": len(node.refs),
        "section_count": len(node.sections),
        "size": node.size,
    }


def _heap_view(loaded: RXFSnapshot) -> dict[str, Any]:
    heap = loaded.layout.heap
    return {
        "base": loaded.layout.header.heap_off,
        "image_size": heap.image_size,
        "committed_size": heap.committed_size,
        "frontier": heap.frontier,
        "limit": heap.limit,
        "align": heap.align,
        "page_size": heap.page_size,
        "stored_size": len(heap.to_wire()),
    }


def _header_view(header: BinaryHeader) -> dict[str, Any]:
    return {
        "magic": header.magic.decode("ascii", errors="replace"),
        "version": header.version,
        "heap_off": header.heap_off,
        "image_size": header.image_size,
        "committed_size": header.committed_size,
        "frontier": header.frontier,
        "node_count": header.node_count,
        "node_table_off": header.node_table_off,
        "ref_table_off": header.ref_table_off,
        "ref_table_count": header.ref_table_count,
        "section_table_off": header.section_table_off,
        "section_table_count": header.section_table_count,
        "string_table_off": header.string_table_off,
        "string_table_size": header.string_table_size,
        "entry_node": header.entry_node,
        "flags": header.flags,
        "type_table_off": header.type_table_off,
        "type_table_count": header.type_table_count,
        "code_table_off": header.code_table_off,
        "code_table_count": header.code_table_count,
    }


def _render(template_name: str, context: Mapping[str, Any]) -> HTMLResponse:
    return HTMLResponse(_TEMPLATES.get_template(template_name).render(**context))


def _encoded(entry_id: str) -> str:
    return (
        base64.urlsafe_b64encode(entry_id.encode("utf-8")).decode("ascii").rstrip("=")
    )


def _decoded(token: str) -> str:
    try:
        padding = "=" * (-len(token) % 4)
        return base64.b64decode(token + padding, altchars=b"-_", validate=True).decode(
            "utf-8"
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise HTTPException(404, "Invalid RXF identifier") from error


def _tree_children(loaded: RXFSnapshot, parent: int | None) -> list[dict[str, Any]]:
    nodes = loaded.layout.nodes
    fqns = derived_fqns(nodes)
    known = {node.id for node in nodes}
    children_by_parent: dict[int, list[NodeEntry]] = {}
    for node in nodes:
        children_by_parent.setdefault(node.parent, []).append(node)
    if parent is None:
        selected = [
            node for node in nodes if node.kind == NodeKind.ROOT and node.id == 0
        ]
    else:
        selected = children_by_parent.get(parent, []) if parent in known else []
    return [
        {
            **_node_view(node, fqns[node.id]),
            "has_children": bool(children_by_parent.get(node.id)),
            "child_count": len(children_by_parent.get(node.id, [])),
        }
        for node in sorted(selected, key=lambda item: (item.name, item.id))
    ]


def _memory_bins(
    loaded: RXFSnapshot, start: int, end: int, bins: int
) -> dict[str, Any]:
    heap = loaded.layout.heap
    limit = max(int(heap.committed_size), heap.frontier, 1)
    start = max(0, min(start, limit - 1))
    end = max(start + 1, min(end or limit, limit))
    bins = max(16, min(bins, 4096))
    span = end - start
    result = [
        {
            "used": 0,
            "count": 0,
            "types": {},
            "owners": {},
            "dispositions": {},
            "node_id": None,
        }
        for _ in range(bins)
    ]
    nodes = {n.id: n for n in loaded.layout.nodes}
    for cell in heap.cells:
        if cell.end <= start or cell.offset >= end:
            continue
        first = max(0, (max(cell.offset, start) - start) * bins // span)
        last = min(bins - 1, (max(cell.end - 1, start) - start) * bins // span)
        node = nodes[cell.header.id]
        for index in range(first, last + 1):
            bs = start + index * span // bins
            be = start + (index + 1) * span // bins
            used = max(0, min(cell.end, be) - max(cell.offset, bs))
            item = result[index]
            item["used"] += used
            item["count"] += 1
            item["types"][str(cell.header.type_id)] = (
                item["types"].get(str(cell.header.type_id), 0) + used
            )
            item["owners"][node.owner_kind.name] = (
                item["owners"].get(node.owner_kind.name, 0) + used
            )
            item["dispositions"][node.state.disposition.name] = (
                item["dispositions"].get(node.state.disposition.name, 0) + used
            )
            cell_id = _object_id(cell.header.id)
            item["node_id"] = cell_id if item["node_id"] in (None, cell_id) else None
    for i, item in enumerate(result):
        width = max(1, start + (i + 1) * span // bins - (start + i * span // bins))
        item["occupancy"] = min(1.0, item["used"] / width)
        item["dominant_type"] = (
            max(item["types"], key=item["types"].get) if item["types"] else None
        )
        item["owner_kind"] = (
            max(item["owners"], key=item["owners"].get) if item["owners"] else None
        )
        item["disposition"] = (
            max(item["dispositions"], key=item["dispositions"].get)
            if item["dispositions"]
            else None
        )
        del item["types"]
        del item["owners"]
        del item["dispositions"]
    return {"heap": _heap_view(loaded), "start": start, "end": end, "bins": result}


def _parse_object_id(value: str, label: str) -> int:
    if not value or not value.isdecimal():
        raise HTTPException(422, f"{label} must be a decimal uint64 object ID")
    result = int(value)
    if result > 0xFFFF_FFFF_FFFF_FFFF:
        raise HTTPException(422, f"{label} is outside uint64 range")
    return result


def _link(nodes: dict[int, Any], value: int) -> dict[str, str]:
    return {
        "id": _object_id(value),
        "name": nodes[value].name if value in nodes else "unknown",
    }


def _target_view(
    container: Any, node: Any, active_target: int | None
) -> dict[str, Any]:
    nodes = {item.id: item for item in container.nodes}
    if node.type_id == TARGET_TYPE:
        record = decode_target(node)
        return {
            "id": _object_id(node.id),
            "name": node.name,
            "legacy": True,
            "active": node.id == active_target,
            "architecture": {
                "id": str(record.architecture),
                "name": f"legacy:{record.architecture}",
            },
            "abi": {"id": str(record.abi), "name": f"legacy:{record.abi}"},
            "environment": {
                "id": str(record.environment),
                "name": f"legacy:{record.environment}",
            },
            "feature_set": {"id": "0", "name": f"legacy-mask:0x{record.features:x}"},
            "word_bits": record.word_bits,
            "endianness": record.endianness.name,
            "features": [],
        }
    target = RuntimeTargetObject.from_node(node)
    architecture = ArchitectureObject.from_node(nodes[target.architecture_id])
    abi = ABIObject.from_node(nodes[target.abi_id])
    environment = EnvironmentObject.from_node(nodes[target.environment_id])
    feature_set = FeatureSetObject.from_node(nodes[target.feature_set_id])
    features = [
        FeatureObject.from_node(nodes[value]) for value in feature_set.feature_ids
    ]
    return {
        "id": _object_id(node.id),
        "name": node.name,
        "legacy": False,
        "active": node.id == active_target,
        "architecture": {
            **_link(nodes, architecture.id),
            "kind": architecture.kind.name,
            "word_bits": architecture.word_bits,
            "endianness": architecture.endianness.name,
        },
        "abi": {
            **_link(nodes, abi.id),
            "kind": abi.kind.name,
            "version": abi.version,
            "calling_convention": abi.calling_convention.name,
        },
        "environment": {
            **_link(nodes, environment.id),
            "kind": environment.kind.name,
            "version": environment.version,
        },
        "feature_set": _link(nodes, feature_set.id),
        "features": [
            {
                **_link(nodes, feature.id),
                "kind": feature.kind.name,
                "version": feature.version,
            }
            for feature in features
        ],
    }


_CODE_DISPLAY_LIMIT = 64 * 1024


def _effect_names(effects: Effect) -> list[str]:
    return [str(effect.name) for effect in Effect if effect.value and effect in effects]


def _hexdump(
    raw: bytes, entry_offset: int, limit: int = _CODE_DISPLAY_LIMIT
) -> dict[str, Any]:
    displayed = raw[: max(0, limit)]
    rows = []
    for offset in range(0, len(displayed), 16):
        chunk = displayed[offset : offset + 16]
        contains_entry = offset <= entry_offset < offset + len(chunk)
        rows.append(
            {
                "offset": offset,
                "offset_hex": f"{offset:08x}",
                "hex": " ".join(f"{value:02x}" for value in chunk),
                "cells": [f"{value:02x}" for value in chunk]
                + [None] * (16 - len(chunk)),
                "ascii": "".join(
                    chr(value) if 32 <= value < 127 else "." for value in chunk
                ),
                "contains_entry": contains_entry,
                "entry_index": entry_offset - offset if contains_entry else None,
            }
        )
    return {
        "rows": rows,
        "displayed_byte_count": len(displayed),
        "truncated": len(displayed) < len(raw),
    }


def _code_view(container: Any, node: Any, nodes: dict[int, Any]) -> dict[str, Any]:
    record = decode_code(node)
    owner = nodes.get(record.owner_function)
    owner_record = (
        decode_function(owner)
        if owner is not None and owner.type_id == FUNCTION_TYPE
        else None
    )
    target_node = nodes.get(record.target_id)
    target = (
        _target_view(container, target_node, None)
        if target_node is not None
        and target_node.type_id in (TARGET_TYPE, RUNTIME_TARGET_TYPE)
        else None
    )
    import_ids = [ref.target for ref in node.refs if ref.to_off == int(CallRole.IMPORT)]
    relocation_ids = [
        ref.target for ref in node.refs if ref.to_off == int(CallRole.RELOCATION)
    ]
    imports = []
    for import_id in import_ids:
        imported = nodes.get(import_id)
        if imported is None or imported.type_id != IMPORT_TYPE:
            continue
        value = decode_import(imported)
        imports.append(
            {
                **_link(nodes, import_id),
                "function": _link(nodes, value.function_id),
                "optional": value.optional,
            }
        )
    relocations = []
    for relocation_id in relocation_ids:
        relocation = nodes.get(relocation_id)
        if relocation is None or relocation.type_id != RELOCATION_TYPE:
            continue
        value = decode_relocation(relocation)
        relocations.append(
            {
                **_link(nodes, relocation_id),
                "offset": value.offset,
                "patch_width": value.patch_width,
                "kind": value.kind.name,
                "target": _link(nodes, value.target_id),
                "addend": value.addend,
                "in_bounds": value.offset + value.patch_width <= record.byte_count,
            }
        )
    agreement = {
        "signature": owner_record is not None
        and record.signature_id == owner_record.signature_id,
        "effects": owner_record is not None and record.effects == owner_record.effects,
        "semantic_version": owner_record is not None
        and record.semantic_version == owner_record.semantic_version,
        "semantic_digest": owner_record is not None
        and record.semantic_digest == owner_record.semantic_digest,
    }
    return {
        "category": "Code",
        "owner_function": _link(nodes, record.owner_function),
        "target": target,
        "signature": _link(nodes, record.signature_id),
        "format": record.format.name,
        "effects": {
            "flags": int(record.effects),
            "names": _effect_names(record.effects),
        },
        "flags": record.flags,
        "semantic_version": record.semantic_version,
        "semantic_digest": record.semantic_digest.hex(),
        "entry_offset": record.entry_offset,
        "byte_count": record.byte_count,
        "raw_hex": record.raw_bytes.hex()
        if record.byte_count <= _CODE_DISPLAY_LIMIT
        else None,
        "hexdump": _hexdump(record.raw_bytes, record.entry_offset),
        "imports": imports,
        "relocations": relocations,
        "contract": {
            "agrees": owner_record is not None and all(agreement.values()),
            **agreement,
        },
        "validation_warnings": [
            warning
            for warning in check(container)
            if f"Code {node.id}" in warning or f"code {node.id}" in warning
        ],
        "object_links": [
            {"role": "Owner Function", **_link(nodes, record.owner_function)},
            {"role": "Target", **_link(nodes, record.target_id)},
            {"role": "Signature", **_link(nodes, record.signature_id)},
        ],
    }


def _plan_view(plan: Any) -> dict[str, Any]:
    return {
        "ok": plan.ok,
        "health": "ready" if plan.ok else "refused",
        "entry_function": _object_id(plan.entry_function),
        "active_target": _object_id(plan.target_id),
        "selected": [
            {
                "function_id": _object_id(value.function_id),
                "target_id": _object_id(value.target_id),
                "code_id": _object_id(value.code_id),
                "rank": list(value.rank),
            }
            for value in plan.functions
        ],
        "imports": [
            {
                "import_id": _object_id(value.import_id),
                "function_id": _object_id(value.function_id),
            }
            for value in plan.imports
        ],
        "patches": [
            {
                "relocation_id": _object_id(value.relocation_id),
                "code_id": _object_id(value.code_id),
                "offset": value.offset,
                "width": value.width,
                "target_id": _object_id(value.target_id),
                "addend": value.addend,
            }
            for value in plan.patches
        ],
        "candidates": [
            {
                "function_id": _object_id(value.function_id),
                "code_id": _object_id(value.code_id),
                "target_id": _object_id(value.target_id),
                "compatible": value.compatible,
                "rank": None if value.rank is None else list(value.rank),
                "reason": value.reason,
            }
            for value in plan.candidates
        ],
        "diagnostics": [
            {
                "code": value.code.value,
                "object_id": _object_id(value.object_id),
                "message": value.message,
                "related_ids": [_object_id(item) for item in value.related_ids],
            }
            for value in plan.diagnostics
        ],
    }


def create_app(library: RXFLibrary | None = None) -> FastAPI:
    """Create an inspector over a lazily loaded mounted RXF library."""
    if library is None:
        library = RXFLibrary(ServerConfig.from_toml(Path("rxf-server.toml")))
    app = FastAPI(title="RXF Inspector", version="0.3.0")

    def snapshot(entry_id: str) -> RXFSnapshot:
        logical_id = _decoded(entry_id)
        try:
            return library.load(logical_id)
        except KeyError as error:
            raise HTTPException(404, f"RXF {logical_id!r} was not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(422, f"Could not load RXF: {error}") from error

    def catalog_groups() -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for entry in library.list_entries():
            groups.setdefault(entry.mount, []).append(
                {
                    "id": entry.id,
                    "encoded_id": _encoded(entry.id),
                    "relative_path": entry.relative_path,
                    "size": entry.size,
                }
            )
        return [{"name": name, "entries": entries} for name, entries in groups.items()]

    @app.get("/", response_class=HTMLResponse)
    def index() -> Response:
        entries = library.list_entries()
        if len(entries) == 1:
            return RedirectResponse(f"/rxf/{_encoded(entries[0].id)}", status_code=307)
        return _render("base.jinja2", {"mounts": catalog_groups(), "selected": None})

    @app.post("/rescan", response_class=HTMLResponse)
    def rescan() -> Response:
        library.scan()
        return index()

    @app.get("/rxf/{entry_id}", response_class=HTMLResponse)
    def document(
        entry_id: str, object: str | None = Query(default=None)
    ) -> HTMLResponse:
        loaded = snapshot(entry_id)
        initial_object = (
            object
            if object is not None
            else _object_id(loaded.container.header.entry_node)
        )
        functions = []
        layer_counts: dict[str, int] = {}
        implementation_counts: dict[str, int] = {}
        for node in loaded.container.nodes:
            if node.type_id != FUNCTION_TYPE:
                continue
            record = decode_function(node)
            layer_counts[record.layer.name] = layer_counts.get(record.layer.name, 0) + 1
            implementation_counts[record.implementation.name] = (
                implementation_counts.get(record.implementation.name, 0) + 1
            )
            functions.append(
                {
                    "id": node.id,
                    "name": node.name,
                    "layer": record.layer.name,
                    "implementation": record.implementation.name,
                    "intrinsic": record.intrinsic.name,
                }
            )
        functions.sort(key=lambda item: (item["layer"], item["name"]))
        fqns = derived_fqns(loaded.layout.nodes)
        active_targets = sorted(
            node.id
            for node in loaded.container.nodes
            if node.type_id == RUNTIME_TARGET_TYPE
        )
        binding_health = None
        entry = loaded.container.node_by_id(loaded.container.header.entry_node)
        if active_targets and entry is not None and entry.type_id == FUNCTION_TYPE:
            binding_health = preflight(loaded.container, entry.id, active_targets[0])
        selected = {
            "active_targets": [_object_id(value) for value in active_targets],
            "binding_health": None
            if binding_health is None
            else {
                "ok": binding_health.ok,
                "active_target": _object_id(binding_health.target_id),
                "selected": [
                    _object_id(value.code_id) for value in binding_health.functions
                ],
                "refusals": [
                    {
                        "code": value.code.value,
                        "message": value.message,
                        "object_id": _object_id(value.object_id),
                    }
                    for value in binding_health.diagnostics
                ],
            },
            "id": loaded.entry.id,
            "encoded_id": _encoded(loaded.entry.id),
            "mount": loaded.entry.mount,
            "relative_path": loaded.entry.relative_path,
            "blob_size": len(loaded.blob),
            "nodes": [_node_view(node, fqns[node.id]) for node in loaded.layout.nodes],
            "heap": _heap_view(loaded),
            "header": _header_view(loaded.layout.header),
            "functions": functions,
            "layer_counts": layer_counts,
            "implementation_counts": implementation_counts,
            "initial_object": initial_object,
            "entry_node": _object_id(loaded.container.header.entry_node),
            "navigation": {
                "application": next(
                    (
                        _object_id(node.id)
                        for node in loaded.container.nodes
                        if node.kind == NodeKind.MODULE and node.name == "application"
                    ),
                    None,
                ),
                "library": next(
                    (
                        _object_id(node.id)
                        for node in loaded.container.nodes
                        if node.kind == NodeKind.MODULE and node.name == "library"
                    ),
                    None,
                ),
                "basic": next(
                    (
                        _object_id(node.id)
                        for node in loaded.container.nodes
                        if node.kind == NodeKind.MODULE and node.name == "basic"
                    ),
                    None,
                ),
                "foundation": next(
                    (
                        _object_id(node.id)
                        for node in loaded.container.nodes
                        if node.kind == NodeKind.MODULE and node.name == "foundation"
                    ),
                    None,
                ),
                "code": [
                    _object_id(node.id)
                    for node in loaded.container.nodes
                    if node.type_id == CODE_TYPE
                ],
            },
            "function_count": len(functions),
            "module_count": sum(
                node.kind == NodeKind.MODULE for node in loaded.layout.nodes
            ),
            "code_count": sum(
                node.type_id == CODE_TYPE for node in loaded.layout.nodes
            ),
            "target_count": sum(
                node.type_id in (TARGET_TYPE, RUNTIME_TARGET_TYPE)
                for node in loaded.layout.nodes
            ),
            "call_count": sum(
                node.type_id == CALL_TYPE for node in loaded.layout.nodes
            ),
        }
        return _render(
            "inspector.jinja2",
            {
                "mounts": catalog_groups(),
                "selected": selected,
            },
        )

    @app.get("/rxf/{entry_id}/nodes/{node_id}")
    def node_detail(entry_id: str, node_id: int) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}?object={node_id}", status_code=307)

    @app.get("/rxf/{entry_id}/heap")
    def heap_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/graph")
    def graph_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/explore")
    def explore_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/types")
    def types_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/functions")
    def functions_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/semantic")
    def semantic_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/rxf/{entry_id}/lowered")
    def lowered_compatibility(entry_id: str) -> RedirectResponse:
        return RedirectResponse(f"/rxf/{entry_id}", status_code=307)

    @app.get("/api/rxfs/{entry_id}/tree")
    def api_tree(entry_id: str, parent: int | None = None) -> list[dict[str, Any]]:
        return _tree_children(snapshot(entry_id), parent)

    @app.get("/api/rxfs/{entry_id}/memory")
    def api_memory(
        entry_id: str,
        start: int = 0,
        end: int = 0,
        bins: int = 1024,
    ) -> dict[str, Any]:
        return _memory_bins(snapshot(entry_id), start, end, bins)

    @app.get("/api/rxfs")
    def api_rxfs() -> list[dict[str, Any]]:
        return [
            {
                "id": entry.id,
                "mount": entry.mount,
                "path": entry.relative_path,
                "size": entry.size,
                "mtime_ns": entry.mtime_ns,
            }
            for entry in library.list_entries()
        ]

    @app.post("/api/rxfs/rescan")
    def api_rescan() -> list[dict[str, Any]]:
        library.scan()
        return api_rxfs()

    @app.get("/api/rxfs/{entry_id}")
    def api_document(entry_id: str) -> dict[str, Any]:
        loaded = snapshot(entry_id)
        return {
            "id": loaded.entry.id,
            "mount": loaded.entry.mount,
            "path": loaded.entry.relative_path,
            "size": len(loaded.blob),
            "header": _header_view(loaded.layout.header),
        }

    @app.get("/api/rxfs/{entry_id}/nodes")
    def api_nodes(entry_id: str) -> list[dict[str, Any]]:
        loaded = snapshot(entry_id)
        fqns = derived_fqns(loaded.layout.nodes)
        return [_node_view(node, fqns[node.id]) for node in loaded.layout.nodes]

    @app.get("/api/rxfs/{entry_id}/nodes/{node_id}")
    def api_node(entry_id: str, node_id: int) -> dict[str, Any]:
        loaded = snapshot(entry_id)
        node = next((item for item in loaded.layout.nodes if item.id == node_id), None)
        if node is None:
            raise HTTPException(404, f"Node {node_id} was not found")
        nodes = {item.id: item for item in loaded.layout.nodes}
        fqns = derived_fqns(loaded.layout.nodes)
        children = [
            {
                "id": _object_id(item.id),
                "name": item.name,
                "fqn": fqns[item.id],
                "kind": item.kind.name,
            }
            for item in loaded.layout.nodes
            if item.parent == node.id
        ]
        references = [
            {
                "kind": RefKind(ref.kind).name,
                "target": _object_id(ref.target),
                "target_name": nodes[ref.target].name
                if ref.target in nodes
                else "unknown",
                "role": ref.to_off,
                "binding": RefBinding(ref.binding).name,
            }
            for ref in node.refs
        ]
        execution: dict[str, Any] | None = None
        semantic_node = loaded.container.node_by_id(node.id)
        if node.type_id == FUNCTION_TYPE and semantic_node is not None:
            record = decode_function(semantic_node)
            execution = {
                "category": "Function",
                "signature_id": _object_id(record.signature_id),
                "body_id": _object_id(record.body_id) if record.body_id else None,
                "implementation": record.implementation.name,
                "layer": record.layer.name,
                "intrinsic": record.intrinsic.name,
                "object_links": [
                    {
                        "role": "Signature",
                        "id": _object_id(record.signature_id),
                        "name": nodes[record.signature_id].name
                        if record.signature_id in nodes
                        else "unknown",
                    },
                    *(
                        [
                            {
                                "role": "Body",
                                "id": _object_id(record.body_id),
                                "name": nodes[record.body_id].name
                                if record.body_id in nodes
                                else "unknown",
                            }
                        ]
                        if record.body_id
                        else []
                    ),
                    *(
                        {
                            "role": "Implementation Code",
                            "id": _object_id(code_id),
                            "name": nodes[code_id].name
                            if code_id in nodes
                            else "unknown",
                        }
                        for code_id in (
                            ref.target
                            for ref in semantic_node.refs
                            if ref.to_off == int(CallRole.IMPLEMENTATION)
                        )
                    ),
                ],
            }
        elif semantic_node is not None and node.type_id in {
            ARCHITECTURE_TYPE,
            ABI_TYPE,
            ENVIRONMENT_TYPE,
            FEATURE_TYPE,
            FEATURE_SET_TYPE,
            RUNTIME_TARGET_TYPE,
        }:
            decoders = {
                ARCHITECTURE_TYPE: ArchitectureObject.from_node,
                ABI_TYPE: ABIObject.from_node,
                ENVIRONMENT_TYPE: EnvironmentObject.from_node,
                FEATURE_TYPE: FeatureObject.from_node,
                FEATURE_SET_TYPE: FeatureSetObject.from_node,
                RUNTIME_TARGET_TYPE: RuntimeTargetObject.from_node,
            }
            record = decoders[node.type_id](semantic_node)
            facts = {}
            for key, value in record.__dict__.items():
                if key in {"id", "name", "parent"} or key.endswith("_ids"):
                    continue
                if hasattr(value, "name"):
                    facts[key] = value.name
                elif isinstance(value, tuple):
                    facts[key] = [_object_id(item) for item in value]
                else:
                    facts[key] = value
            execution = {
                "category": type(record).__name__,
                **facts,
                "object_links": [
                    {
                        "role": str(ref.to_off),
                        "id": _object_id(ref.target),
                        "name": nodes[ref.target].name
                        if ref.target in nodes
                        else "unknown",
                    }
                    for ref in node.refs
                ],
            }
        elif semantic_node is not None and node.type_id in {
            GENERIC_PARAMETER_TYPE,
            GENERIC_ARGUMENT_TYPE,
            TEMPLATE_TYPE,
            TRAIT_TYPE,
            TRAIT_REQUIREMENT_TYPE,
            ABI_TYPE,
            ARCHITECTURE_TYPE,
            ASSOCIATED_TYPE_TYPE,
            CONFORMANCE_TYPE,
            IMPLEMENTATION_BINDING_TYPE,
            SPECIALIZATION_TYPE,
        }:
            decoders = {
                GENERIC_PARAMETER_TYPE: GenericParameter.from_node,
                GENERIC_ARGUMENT_TYPE: GenericArgument.from_node,
                TEMPLATE_TYPE: Template.from_node,
                TRAIT_TYPE: Trait.from_node,
                TRAIT_REQUIREMENT_TYPE: TraitRequirement.from_node,
                ASSOCIATED_TYPE_TYPE: AssociatedType.from_node,
                CONFORMANCE_TYPE: Conformance.from_node,
                IMPLEMENTATION_BINDING_TYPE: ImplementationBinding.from_node,
                SPECIALIZATION_TYPE: Specialization.from_node,
            }
            record = decoders[node.type_id](semantic_node)
            links = []
            for ref in node.refs:
                if ref.target in nodes:
                    links.append(
                        {
                            "role": str(ref.to_off),
                            "id": _object_id(ref.target),
                            "name": nodes[ref.target].name,
                        }
                    )
            facts = {
                key: value
                for key, value in record.__dict__.items()
                if not key.endswith("_ids") and key != "digest"
            }
            if hasattr(record, "digest"):
                facts["digest"] = record.digest.hex()
            execution = {
                "category": type(record).__name__,
                **facts,
                "object_links": links,
            }
        elif semantic_node is not None and node.type_id in {
            SIGNATURE_TYPE,
            PARAMETER_TYPE,
            CALL_TYPE,
            ARGUMENT_TYPE,
            VALUE_TYPE,
            RESULT_TYPE,
            REFUSAL_SET_TYPE,
            REFUSAL_VARIANT_TYPE,
            NUMERIC_CONTRACT_TYPE,
            ABI_SIGNATURE_TYPE,
        }:
            import struct

            labels = {
                SIGNATURE_TYPE: "Signature",
                PARAMETER_TYPE: "Parameter",
                CALL_TYPE: "Call",
                ARGUMENT_TYPE: "Argument",
                VALUE_TYPE: "Value",
                RESULT_TYPE: "Result",
                REFUSAL_SET_TYPE: "RefusalSet",
                REFUSAL_VARIANT_TYPE: "RefusalVariant",
                NUMERIC_CONTRACT_TYPE: "NumericContract",
                ABI_SIGNATURE_TYPE: "ABISignature",
            }
            facts: dict[str, Any] = {"payload_hex": semantic_node.data.hex()}
            if node.type_id == SIGNATURE_TYPE:
                r = decode_signature(semantic_node)
                facts.update(
                    return_type=_object_id(r.return_type),
                    parameter_count=r.parameter_count,
                    result_count=r.result_count,
                    refusal_set_id=_object_id(r.refusal_set_id)
                    if r.refusal_set_id
                    else None,
                )
            elif node.type_id == CALL_TYPE and len(semantic_node.data) == 24:
                callee, count, result = struct.unpack("<3Q", semantic_node.data)
                facts.update(
                    callee=_link(nodes, callee),
                    argument_count=count,
                    result_type=_link(nodes, result),
                )
            execution = {
                "category": labels[node.type_id],
                **facts,
                "object_links": [
                    {"role": str(ref.to_off), **_link(nodes, ref.target)}
                    for ref in semantic_node.refs
                ],
            }
        elif node.type_id == CODE_TYPE and semantic_node is not None:
            semantic_nodes = {item.id: item for item in loaded.container.nodes}
            execution = _code_view(loaded.container, semantic_node, semantic_nodes)
        return {
            **_node_view(node, fqns[node.id]),
            "attrs": node.attrs,
            "parent_object": (
                {
                    "id": _object_id(nodes[node.parent].id),
                    "name": nodes[node.parent].name,
                    "fqn": fqns[node.parent],
                }
                if node.parent in nodes
                else None
            ),
            "children": children,
            "references": references,
            "execution": execution,
        }

    @app.get("/api/rxfs/{entry_id}/targets")
    def api_targets(
        entry_id: str, active_target: str | None = Query(default=None)
    ) -> dict[str, Any]:
        loaded = snapshot(entry_id)
        active = (
            None
            if active_target is None
            else _parse_object_id(active_target, "active_target")
        )
        if active is not None:
            active_node = loaded.container.node_by_id(active)
            if active_node is None or active_node.type_id not in (
                RUNTIME_TARGET_TYPE,
                TARGET_TYPE,
            ):
                raise HTTPException(404, f"Target {active} was not found")
        target_nodes = sorted(
            (
                node
                for node in loaded.container.nodes
                if node.type_id in (RUNTIME_TARGET_TYPE, TARGET_TYPE)
            ),
            key=lambda node: node.id,
        )
        return {
            "active_target": None if active is None else _object_id(active),
            "targets": [
                _target_view(loaded.container, node, active) for node in target_nodes
            ],
        }

    @app.get("/api/rxfs/{entry_id}/preflight")
    def api_preflight(
        entry_id: str,
        entry_function: str = Query(...),
        active_target: str = Query(...),
    ) -> dict[str, Any]:
        loaded = snapshot(entry_id)
        entry = _parse_object_id(entry_function, "entry_function")
        active = _parse_object_id(active_target, "active_target")
        entry_node = loaded.container.node_by_id(entry)
        if entry_node is None:
            raise HTTPException(404, f"Entry object {entry} was not found")
        if entry_node.type_id != FUNCTION_TYPE:
            raise HTTPException(422, f"Entry object {entry} is not a Function")
        target_node = loaded.container.node_by_id(active)
        if target_node is None or target_node.type_id not in (
            RUNTIME_TARGET_TYPE,
            TARGET_TYPE,
        ):
            raise HTTPException(404, f"Target {active} was not found")
        return _plan_view(preflight(loaded.container, entry, active))

    @app.get("/api/rxfs/{entry_id}/heap")
    def api_heap(entry_id: str) -> dict[str, Any]:
        return _heap_view(snapshot(entry_id))

    @app.get("/api/rxfs/{entry_id}/graph")
    def api_graph(entry_id: str) -> list[dict[str, Any]]:
        loaded = snapshot(entry_id)
        return [
            {
                "source": _object_id(node.id),
                "target": _object_id(ref.target),
                "kind": RefKind(ref.kind).name,
            }
            for node in loaded.layout.nodes
            for ref in node.refs
        ]

    return app
