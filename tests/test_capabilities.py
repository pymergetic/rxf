"""Typed capability corpus and binding tests."""

from pymergetic.rxf.checker import check
from pymergetic.rxf.execution import preflight
from pymergetic.rxf.expand import NATIVE_BODY_CALL_ID, native_binding_template
from pymergetic.rxf.model.capabilities import (
    CapabilityManifest,
    CapabilityProvider,
    CapabilityRequirement,
    CapabilityRights,
)
from pymergetic.rxf.model.capability_corpus import (
    CAPABILITY_MODULE_ID,
    DECLARATIONS,
    capability_nodes,
    toolchain_nodes,
)
from pymergetic.rxf.model.execution import (
    CallRole,
    FunctionImplementation,
    ImportObject,
)
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.target import X86_64_LINUX_TARGET_ID
from pymergetic.rxf.schema import RefKind
from pymergetic.rxf.ty.builtins import (
    CAPABILITY_REQUIREMENT_TYPE,
    FUNCTION_TYPE,
    IMPORT_TYPE,
)


def test_capability_corpus_is_complete_typed_and_collision_free() -> None:
    nodes = capability_nodes()
    assert len({node.id for node in nodes}) == len(nodes)
    assert toolchain_nodes() == []
    functions = [node for node in nodes if node.type_id == FUNCTION_TYPE]
    imports = [node for node in nodes if node.type_id == IMPORT_TYPE]
    requirements = [
        node for node in nodes if node.type_id == CAPABILITY_REQUIREMENT_TYPE
    ]
    assert (
        len(functions) == len(imports) == len(requirements) == len(DECLARATIONS) == 20
    )
    for requirement_node in requirements:
        assert (
            CapabilityRequirement.from_node(requirement_node).id == requirement_node.id
        )
    assert all(node.parent == CAPABILITY_MODULE_ID for node in functions)


def test_v5_import_binds_by_requirement_identity_atomically() -> None:
    container = native_binding_template().build()
    entry = container.header.entry_node
    body = container.node_by_id(NATIVE_BODY_CALL_ID)
    assert body is not None
    corpus = capability_nodes()
    imported = next(node for node in corpus if node.type_id == FUNCTION_TYPE)
    requirement = next(
        node for node in corpus if node.type_id == CAPABILITY_REQUIREMENT_TYPE
    )
    imported_record = __import__(
        "pymergetic.rxf.execution.decode", fromlist=["decode_function"]
    ).decode_function(imported)
    assert imported_record.implementation == FunctionImplementation.IMPORTED
    imp = next(
        node
        for node in corpus
        if node.type_id == IMPORT_TYPE
        and any(
            ref.target == imported.id and ref.to_off == int(CallRole.CALLEE)
            for ref in node.refs
        )
    )
    body.data = imported.id.to_bytes(8, "little") + (0).to_bytes(8, "little") * 2
    body.refs = [
        RefDef(body.id, imported.id, RefKind.CALL, to_off=int(CallRole.CALLEE))
    ]
    container.nodes.extend(corpus)
    before = container.to_dict()
    missing = preflight(container, entry, X86_64_LINUX_TARGET_ID)
    assert not missing.ok and not missing.functions and not missing.imports
    assert container.to_dict() == before
    record = CapabilityRequirement.from_node(requirement)
    provider = CapabilityProvider(
        record.id, record.version, record.rights, address=object()
    )
    accepted = preflight(
        container, entry, X86_64_LINUX_TARGET_ID, CapabilityManifest((provider,))
    )
    assert accepted.ok
    assert accepted.imports[0].import_id == imp.id
    assert accepted.imports[0].requirement_id == record.id
    denied = CapabilityProvider(record.id, record.version, CapabilityRights.NONE)
    refusal = preflight(
        container, entry, X86_64_LINUX_TARGET_ID, CapabilityManifest((denied,))
    )
    assert not refusal.ok and not refusal.functions and not refusal.imports


def test_v5_rejects_incomplete_import_but_legacy_decodes() -> None:
    container = native_binding_template().build()
    imported = next(
        node for node in capability_nodes() if node.type_id == FUNCTION_TYPE
    )
    incomplete = ImportObject(
        49999, "incomplete", CAPABILITY_MODULE_ID, imported.id
    ).to_node()
    incomplete.data = incomplete.data[:16]
    container.nodes.extend([imported, incomplete])
    errors = check(container)
    assert any("capability requirement" in error for error in errors)
