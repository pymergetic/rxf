"""Deterministic executable closure planning and layout."""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, replace

from pymergetic.rxf.compiler.compile import compile_function
from pymergetic.rxf.compiler.native import Architecture, NativeImage, emit
from pymergetic.rxf.executable.graph import CODE_HEADER_SIZE, encode_graph
from pymergetic.rxf.executable.models import (
    ExecutableImage,
    ExecutablePlatform,
    ExecutableProvenance,
    ExecutableSegment,
    ExecutableSymbol,
    ExecutableTarget,
    RuntimeTable,
    RuntimeTableKind,
    SegmentKind,
    SegmentPermissions,
    SymbolKind,
)
from pymergetic.rxf.execution.binder import BindingPlan, preflight
from pymergetic.rxf.execution.decode import (
    decode_code,
    decode_function,
    decode_relocation,
)
from pymergetic.rxf.model.capabilities import CapabilityManifest
from pymergetic.rxf.model.container import Container
from pymergetic.rxf.model.contracts import (
    ABIClass,
    ABIKind,
    ABIPassingMode,
    ABIRegisterBank,
    ABIRole,
    ABISignature,
    ABIValueLocation,
    CompilerProvenance,
)
from pymergetic.rxf.model.execution import (
    CallRole,
    CodeFormat,
    CodeObject,
    FunctionImplementation,
    RelocationKind,
)
from pymergetic.rxf.model.node import NodeDef
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.target import AARCH64_ID, X86_64_ID
from pymergetic.rxf.schema import RefKind
from pymergetic.rxf.ty.builtins import FUNCTION_TYPE, REF_TYPE, U32_TYPE

FUNCTION_ENTRY_SIZE = 24
OBJECT_ENTRY_SIZE = 80
CAPABILITY_ENTRY_SIZE = 24
TRANSACTION_ENTRY_SIZE = 48
RUNTIME_CONTEXT_SIZE = 144
LIMIT = 1 << 64


class ExecutableBuildError(ValueError):
    pass


@dataclass(frozen=True)
class ExecutableLayout:
    base_address: int = 0x400000
    page_size: int = 0x1000
    text_alignment: int = 16
    heap_size: int = 0x10000
    journal_entries: int = 64
    cleanup_entries: int = 64
    stack_size: int = 0x10000

    def __post_init__(self) -> None:
        for name in (
            "base_address",
            "page_size",
            "text_alignment",
            "heap_size",
            "stack_size",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be positive")
        if self.page_size & (self.page_size - 1) or self.text_alignment & (
            self.text_alignment - 1
        ):
            raise ValueError("layout alignments must be powers of two")
        if self.base_address % self.page_size:
            raise ValueError("base_address must be page aligned")
        if self.journal_entries < 0 or self.cleanup_entries < 0:
            raise ValueError("runtime capacities must not be negative")


@dataclass(frozen=True)
class PlannedFunction:
    function_id: int
    name: str
    code_id: int | None
    entry_offset: int
    text: bytes
    native: NativeImage | None = None
    source_code_id: int | None = None


@dataclass(frozen=True)
class ExecutablePlan:
    container: Container
    target: ExecutableTarget
    entry_function_id: int
    binding: BindingPlan
    functions: tuple[PlannedFunction, ...]
    object_ids: tuple[int, ...]
    capability_ids: tuple[int, ...]
    layout: ExecutableLayout
    source_digest: bytes


def _align(value: int, alignment: int) -> int:
    result = (value + alignment - 1) & -alignment
    if result >= LIMIT:
        raise ExecutableBuildError("address overflow")
    return result


def _add(value: int, amount: int) -> int:
    result = value + amount
    if value < 0 or amount < 0 or result >= LIMIT:
        raise ExecutableBuildError("address or size overflow")
    return result


def _entry_id(entry: int | NodeDef) -> int:
    if isinstance(entry, NodeDef):
        if entry.type_id != FUNCTION_TYPE:
            raise ExecutableBuildError(f"object {entry.id} is not a Function")
        return entry.id
    if isinstance(entry, bool) or not isinstance(entry, int):
        raise TypeError("entry must be a Function or uint64 ID")
    return entry


def _architecture(target: ExecutableTarget) -> Architecture:
    if target.architecture_id == X86_64_ID:
        return Architecture.X86_64
    if target.architecture_id == AARCH64_ID:
        return Architecture.AARCH64
    raise ExecutableBuildError(
        f"unsupported executable architecture {target.architecture_id}"
    )


def _materialize_code(
    container, function_id, target, native, ir, allocate, source=None
):
    """Add schema-valid Code/ABI/provenance objects without replacing a body."""
    owner = container.node_by_id(function_id)
    assert owner is not None
    contract = decode_function(owner)
    code_id, provenance_id = allocate(), allocate()
    abi_id = 0
    extra = []
    if source is not None:
        abi_ids = [r.target for r in source.refs if r.to_off == CallRole.ABI_SIGNATURE]
        abi_id = abi_ids[0] if abi_ids else 0
        raw = decode_code(source)
        text, entry_offset = raw.raw_bytes, raw.entry_offset
        source_id = source.id
    else:
        text, entry_offset, source_id = native.text, 0, owner.id
        if native.rodata:
            raise ExecutableBuildError("native emitter requires unrepresented rodata")
        abi_id = allocate()
        # Semantic programs expose the actual ABI locations used by the emitter.
        locations = getattr(
            ir,
            "x86_entry_abi"
            if native.architecture is Architecture.X86_64
            else "arm_entry_abi",
            None,
        )
        if locations is not None:
            locations = tuple(
                replace(v, id=allocate(), parent=abi_id) for v in locations
            )
        else:
            if ir.parameters:
                raise ExecutableBuildError(
                    "scalar composed emitter has no parameter entry ABI"
                )
            results = container.node_by_id(contract.signature_id)
            result_ids = [r.target for r in results.refs if r.to_off == CallRole.RESULT]
            locations = (
                ABIValueLocation(
                    allocate(),
                    "hidden_context",
                    abi_id,
                    0,
                    0,
                    ABIRole.HIDDEN_CONTEXT,
                    ABIPassingMode.INDIRECT_BY_REFERENCE,
                    ABIClass.POINTER,
                    8,
                    8,
                    1,
                    ABIRegisterBank.INTEGER,
                    (0,),
                    pointee_type=REF_TYPE,
                    hidden=True,
                ),
                ABIValueLocation(
                    allocate(),
                    "output",
                    abi_id,
                    result_ids[0] if result_ids else 0,
                    0,
                    ABIRole.TRANSIENT_OUTPUT,
                    ABIPassingMode.INDIRECT_BY_REFERENCE,
                    ABIClass.POINTER,
                    8,
                    8,
                    1,
                    ABIRegisterBank.INTEGER,
                    (1,),
                    pointee_type=ir.result_type.id,
                    hidden=True,
                    mutable=True,
                    output_only=True,
                ),
                ABIValueLocation(
                    allocate(),
                    "status",
                    abi_id,
                    0,
                    0,
                    ABIRole.STATUS_RETURN,
                    ABIPassingMode.DIRECT_SCALAR,
                    ABIClass.INTEGER,
                    4,
                    4,
                    1,
                    ABIRegisterBank.RETURN,
                    (0,),
                    pointee_type=U32_TYPE,
                    hidden=True,
                ),
            )
        extra.append(
            ABISignature(
                abi_id,
                "executable_entry_abi",
                owner.id,
                target.target_id,
                ABIKind.SYSV_X86_64
                if native.architecture is Architecture.X86_64
                else ABIKind.AAPCS64,
                tuple(
                    v.value_class
                    for v in locations
                    if v.role is ABIRole.SEMANTIC_ARGUMENT
                ),
                location_ids=tuple(v.id for v in locations),
            ).to_node()
        )
        extra.extend(v.to_node() for v in locations)
    provenance = CompilerProvenance(
        provenance_id,
        "executable_materialization",
        owner.id,
        1,
        hashlib.sha256(text + struct.pack("<2Q", source_id, target.target_id)).digest(),
    ).to_node()
    provenance.refs.append(
        RefDef(provenance_id, source_id, RefKind.DATA, to_off=int(CallRole.SOURCE))
    )
    code = CodeObject(
        code_id,
        f"{owner.name}__executable_{target.target_id}",
        owner.id,
        owner.id,
        target.target_id,
        CodeFormat.NATIVE,
        text,
        contract.signature_id,
        contract.effects,
        contract.semantic_version,
        contract.semantic_digest,
        entry_offset=entry_offset,
        abi_signature_id=abi_id,
        provenance_id=provenance_id,
    ).to_node()
    code.refs.append(
        RefDef(code_id, source_id, RefKind.DATA, to_off=int(CallRole.SOURCE))
    )
    owner.refs.append(
        RefDef(owner.id, code_id, RefKind.DATA, to_off=int(CallRole.IMPLEMENTATION))
    )
    container.nodes.extend([code, provenance, *extra])
    return code


def plan_executable(
    container: Container,
    entry: int | NodeDef,
    target: ExecutableTarget,
    capabilities: Mapping[int, object] | CapabilityManifest | None = None,
    *,
    layout: ExecutableLayout | None = None,
) -> ExecutablePlan:
    layout = layout or ExecutableLayout()
    source_digest = hashlib.sha256(encode_graph(container).data).digest()
    container = deepcopy(container)
    entry_id = _entry_id(entry)
    binding = preflight(container, entry_id, target.target_id, capabilities)
    if not binding.ok and target.platform is ExecutablePlatform.BIOS:
        linux_target = next(
            node.id for node in container.nodes if node.name == "x86_64_linux_sysv"
        )
        binding = preflight(container, entry_id, linux_target, capabilities)
    if not binding.ok:
        raise ExecutableBuildError(
            "; ".join(f"{d.code}: {d.message}" for d in binding.diagnostics)
        )
    selected = {b.function_id: b.code_id for b in binding.functions}
    imports = {b.function_id: b for b in binding.imports}
    arch = _architecture(target)
    pending, seen, compiled, terminal = [entry_id], set(), {}, {}
    all_functions = set(selected) | set(imports)
    while pending:
        fid = pending.pop()
        if fid in seen:
            continue
        seen.add(fid)
        all_functions.add(fid)
        node = container.node_by_id(fid)
        if node is None or node.type_id != FUNCTION_TYPE:
            raise ExecutableBuildError(f"reachable call target {fid} is not a Function")
        record = decode_function(node)
        if record.implementation is FunctionImplementation.COMPOSED:
            native = emit(compile_function(container, fid), arch)
            compiled[fid] = native
            pending.extend(native.function_ids)
        elif fid in selected:
            code_id = selected[fid]
            code_node = container.node_by_id(code_id)
            if code_node is None:
                raise ExecutableBuildError(f"selected Code {code_id} is missing")
            code = decode_code(code_node)
            terminal[fid] = (code_id, code.raw_bytes, code.entry_offset)
        elif fid not in imports:
            raise ExecutableBuildError(
                f"Function {fid} has no executable implementation"
            )
    global_functions = tuple(sorted(all_functions))
    next_id = max(n.id for n in container.nodes) + 1

    def allocate():
        nonlocal next_id
        if next_id >= LIMIT - 1:
            raise ExecutableBuildError("generated object ID overflow")
        result = next_id
        next_id += 1
        return result

    generated = {}
    compiled_ir = {}
    for fid in sorted(compiled):
        ir = compile_function(container, fid)
        compiled_ir[fid] = ir
        generated[fid] = _materialize_code(
            container, fid, target, compiled[fid], ir, allocate
        )
    relocated = {p.code_id for p in binding.patches}
    bound_codes = {}
    for fid, (cid, text, entry_off) in sorted(terminal.items()):
        if cid in relocated:
            bound_codes[fid] = _materialize_code(
                container, fid, target, None, None, allocate, container.node_by_id(cid)
            )
    # All generated metadata is present before resolving compiler object indices.
    object_ids = tuple(sorted(n.id for n in container.nodes))
    planned = []
    for fid in sorted(compiled):
        ir = replace(
            compiled_ir[fid],
            binding_function_ids=global_functions,
            binding_object_ids=object_ids,
        )
        native = emit(ir, arch)
        code = generated[fid]
        code.data = code.data[:CODE_HEADER_SIZE] + native.text
        # Re-emission can change instruction size with wider global table indices.
        payload = bytearray(code.data)
        struct.pack_into("<Q", payload, 48, len(native.text))
        code.data = bytes(payload)
        provenance_id = next(
            r.target for r in code.refs if r.to_off == CallRole.PROVENANCE
        )
        provenance = container.node_by_id(provenance_id)
        assert provenance is not None
        recipe = hashlib.sha256(native.text + native.semantic_digest)
        for ids in (global_functions, object_ids):
            recipe.update(struct.pack("<Q", len(ids)))
            for oid in ids:
                recipe.update(struct.pack("<Q", oid))
        provenance.data = provenance.data[:8] + recipe.digest()
        function_node = container.node_by_id(fid)
        assert function_node is not None
        planned.append(
            PlannedFunction(fid, function_node.name, code.id, 0, native.text, native)
        )
    for fid, (cid, text, entry_off) in sorted(terminal.items()):
        node = container.node_by_id(fid)
        assert node is not None
        code_id = bound_codes[fid].id if fid in bound_codes else cid
        planned.append(
            PlannedFunction(
                fid, node.name, code_id, entry_off, text, source_code_id=cid
            )
        )
    for fid in sorted(imports):
        node = container.node_by_id(fid)
        assert node is not None
        planned.append(PlannedFunction(fid, node.name, None, 0, b""))
    planned.sort(key=lambda f: f.function_id)
    capabilities_ids = tuple(sorted({b.requirement_id for b in binding.imports}))
    return ExecutablePlan(
        container,
        target,
        entry_id,
        binding,
        tuple(planned),
        object_ids,
        capabilities_ids,
        layout,
        source_digest,
    )


def _patch(data: bytearray, offset: int, width: int, value: int, signed: bool) -> None:
    if width not in (1, 2, 4, 8) or offset < 0 or offset + width > len(data):
        raise ExecutableBuildError("relocation patch is out of bounds")
    low = -(1 << (8 * width - 1)) if signed else 0
    high = (1 << (8 * width - (1 if signed else 0))) - 1
    if not low <= value <= high:
        raise ExecutableBuildError("relocation value does not fit patch width")
    data[offset : offset + width] = value.to_bytes(width, "little", signed=signed)


def build_image(plan: ExecutablePlan) -> ExecutableImage:
    c, page = plan.layout, plan.layout.page_size
    startup = _add(c.base_address, page)
    graph_addr = _add(startup, page)
    container = deepcopy(plan.container)
    nodes = {node.id: node for node in container.nodes}
    graph = encode_graph(container)
    # RXF has an exact in-memory extent. Page alignment belongs between mappings;
    # representing tail padding as RX BSS is rejected by Linux/QEMU and invents
    # memory that is not part of the object image.
    graph_mem = len(graph.data)
    tables_addr = _align(_add(graph_addr, graph_mem), page)
    object_addr = {
        oid: _add(graph_addr, graph.payload_offsets[oid]) for oid in plan.object_ids
    }
    function_addr = {
        f.function_id: _add(graph_addr, graph.code_entry_offsets[f.code_id])
        for f in plan.functions
        if f.code_id is not None
    }
    function_ids = tuple(f.function_id for f in plan.functions)
    ft = _add(tables_addr, RUNTIME_CONTEXT_SIZE)
    ot = _add(ft, len(function_ids) * 24)
    ct = _add(ot, len(plan.object_ids) * 80)
    tt = _add(ct, len(plan.capability_ids) * 24)
    runtime_size = _align(
        RUNTIME_CONTEXT_SIZE
        + len(function_ids) * 24
        + len(plan.object_ids) * 80
        + len(plan.capability_ids) * 24
        + 48,
        page,
    )
    heap = _add(tables_addr, runtime_size)
    heap_size = _align(c.heap_size, page)
    journal = _add(heap, heap_size)
    journal_size = _align(max(1, c.journal_entries) * 72, page)
    cleanup = _add(journal, journal_size)
    cleanup_size = _align(max(1, c.cleanup_entries) * 8, page)
    stack = _add(cleanup, cleanup_size)
    stack_size = _align(c.stack_size, page)
    _add(stack, stack_size)
    import_slots = {
        b.function_id: _add(ct, plan.capability_ids.index(b.requirement_id) * 24)
        for b in plan.binding.imports
    }
    for p in plan.binding.patches:
        matches = [f for f in plan.functions if f.source_code_id == p.code_id]
        if len(matches) != 1:
            raise ExecutableBuildError(f"relocation Code {p.code_id} is ambiguous")
        code_id = matches[0].code_id
        assert code_id is not None and code_id != p.code_id
        base = graph.code_offsets[code_id]
        node = container.node_by_id(p.relocation_id)
        if node is None:
            raise ExecutableBuildError(f"Relocation {p.relocation_id} is missing")
        relocation = decode_relocation(node)
        place = _add(graph_addr, base + p.offset)
        if relocation.kind is RelocationKind.FUNCTION_IMPORT:
            target = import_slots.get(p.target_id)
            if target is None:
                raise ExecutableBuildError(
                    f"runtime import slot {p.target_id} is unresolved"
                )
            value, signed = target + p.addend, False
        else:
            target = function_addr.get(p.target_id, object_addr.get(p.target_id))
            if target is None:
                raise ExecutableBuildError(
                    f"static relocation target {p.target_id} is unresolved"
                )
            if relocation.kind is RelocationKind.ABSOLUTE:
                value, signed = target + p.addend, False
            elif relocation.kind is RelocationKind.PC_RELATIVE:
                value, signed = target + p.addend - place, True
            else:
                raise ExecutableBuildError(
                    f"unsupported relocation kind {int(relocation.kind)}"
                )
        code_node = container.node_by_id(code_id)
        assert code_node is not None
        data = bytearray(code_node.data)
        raw_offset = len(data) - len(decode_code(code_node).raw_bytes)
        _patch(data, raw_offset + p.offset, p.width, value, signed)
        code_node.data = bytes(data)
    if plan.binding.patches:
        for function in plan.functions:
            if function.source_code_id not in {p.code_id for p in plan.binding.patches}:
                continue
            assert function.code_id is not None
            code = nodes[function.code_id]
            provenance_id = next(
                r.target for r in code.refs if r.to_off == CallRole.PROVENANCE
            )
            provenance = nodes[provenance_id]
            recipe = hashlib.sha256(
                struct.pack("<2Q", graph_addr, function.source_code_id)
            )
            recipe.update(nodes[function.source_code_id].data)
            recipe.update(code.data)
            provenance.data = provenance.data[:8] + recipe.digest()
        bound = encode_graph(container)
        if bound.payload_offsets != graph.payload_offsets or len(bound.data) != len(
            graph.data
        ):
            raise ExecutableBuildError(
                "bound Code materialization changed image geometry"
            )
        graph = bound
    runtime = bytearray(runtime_size)
    struct.pack_into(
        "<18Q",
        runtime,
        0,
        2,
        144,
        len(function_ids),
        ft,
        len(plan.object_ids),
        ot,
        len(plan.capability_ids),
        ct,
        heap,
        0,
        c.heap_size,
        0,
        0,
        c.journal_entries,
        journal,
        0,
        c.cleanup_entries,
        cleanup,
    )
    cursor = 144
    for fid in function_ids:
        struct.pack_into(
            "<3Q",
            runtime,
            cursor,
            fid,
            1,
            function_addr.get(fid, import_slots.get(fid, 0)),
        )
        cursor += 24
    for oid in plan.object_ids:
        node = nodes.get(oid)
        assert node is not None
        struct.pack_into(
            "<10Q",
            runtime,
            cursor,
            oid,
            node.generation,
            node.type_id,
            object_addr[oid],
            len(node.data),
            len(node.data),
            1,  # RXF_RUNTIME_OBJECT_LIVE; wire lifecycle is separate.
            0,
            0,
            0,
        )
        cursor += 80
    for cid in plan.capability_ids:
        struct.pack_into("<3Q", runtime, cursor, cid, 1, 0)
        cursor += 24
    fileoff = 0
    segments = []

    def segment(name, kind, address, size, permissions, data=b""):
        nonlocal fileoff
        fileoff = _align(fileoff, page)
        result = ExecutableSegment(
            name, kind, address, size, fileoff, page, permissions, data
        )
        fileoff += size
        segments.append(result)

    rx = SegmentPermissions.READ | SegmentPermissions.EXECUTE
    rw = SegmentPermissions.READ | SegmentPermissions.WRITE
    segment("startup", SegmentKind.STARTUP, startup, page, rx)
    segment("rxf", SegmentKind.RXF_IMAGE, graph_addr, graph_mem, rx, graph.data)
    segment(
        "runtime",
        SegmentKind.RUNTIME_TABLES,
        tables_addr,
        runtime_size,
        rw,
        bytes(runtime),
    )
    segment("heap", SegmentKind.HEAP, heap, heap_size, rw)
    segment("journal", SegmentKind.JOURNAL, journal, journal_size, rw)
    segment("cleanup", SegmentKind.CLEANUP, cleanup, cleanup_size, rw)
    segment("stack", SegmentKind.STACK, stack, stack_size, rw)
    symbols = [
        ExecutableSymbol(
            f.function_id,
            f.name,
            SymbolKind.ENTRY
            if f.function_id == plan.entry_function_id
            else SymbolKind.FUNCTION,
            function_addr.get(f.function_id, import_slots.get(f.function_id, 0)),
            len(f.text),
        )
        for f in plan.functions
    ]
    for oid in plan.object_ids:
        node = nodes.get(oid)
        assert node is not None
        symbols.append(
            ExecutableSymbol(
                oid, node.name, SymbolKind.OBJECT, object_addr[oid], len(node.data)
            )
        )
    symbols.sort(key=lambda s: (s.address, s.object_id, s.name))
    tables = (
        RuntimeTable(RuntimeTableKind.FUNCTION, ft, 24, function_ids),
        RuntimeTable(RuntimeTableKind.OBJECT, ot, 80, plan.object_ids),
        RuntimeTable(RuntimeTableKind.CAPABILITY, ct, 24, plan.capability_ids),
        RuntimeTable(RuntimeTableKind.TRANSACTION, tt, 48, ()),
    )
    entry = function_addr.get(plan.entry_function_id)
    if entry is None:
        raise ExecutableBuildError("entry Function has no text address")
    output = hashlib.sha256()
    for s in segments:
        output.update(
            struct.pack(
                "<4Q", int(s.kind), s.virtual_address, s.memory_size, int(s.permissions)
            )
        )
        output.update(s.data)
    return ExecutableImage(
        plan.target,
        plan.entry_function_id,
        entry,
        tuple(segments),
        tuple(symbols),
        tables,
        provenance=ExecutableProvenance(
            plan.source_digest, output.digest(), ("rxf-executable-v1",)
        ),
    )


def build_executable_image(
    container: Container,
    entry: int | NodeDef,
    target: ExecutableTarget,
    capabilities: Mapping[int, object] | CapabilityManifest | None = None,
    *,
    layout: ExecutableLayout | None = None,
) -> ExecutableImage:
    return build_image(
        plan_executable(container, entry, target, capabilities, layout=layout)
    )
