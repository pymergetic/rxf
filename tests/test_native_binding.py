"""Native target model, graph reachability, and atomic binding proofs."""

import struct
from copy import deepcopy
from pathlib import Path

import pytest

from pymergetic.rxf.checker import check
from pymergetic.rxf.execution import preflight, reachable_terminal_functions
from pymergetic.rxf.execution.binder import candidate_details
from pymergetic.rxf.expand import counter_template
from pymergetic.rxf.model.execution import (
    CallObject,
    CodeFormat,
    CodeObject,
    Effect,
    FunctionImplementation,
    FunctionLayer,
    FunctionObject,
    ImportObject,
    RelocationKind,
    RelocationObject,
    SignatureObject,
    semantic_digest,
)
from pymergetic.rxf.model.refs import RefDef
from pymergetic.rxf.model.target import (
    AARCH64_RETURN_U32,
    AARCH64_UEFI_TARGET_ID,
    X86_64_LINUX_TARGET_ID,
    X86_64_RETURN_U32,
    ABIObject,
    ArchitectureObject,
    EnvironmentObject,
    FeatureKind,
    FeatureObject,
    FeatureSetObject,
    RuntimeTargetObject,
)
from pymergetic.rxf.schema import RefKind
from pymergetic.rxf.ty.builtins import (
    ABI_TYPE,
    ARCHITECTURE_TYPE,
    ENVIRONMENT_TYPE,
    FEATURE_SET_TYPE,
    FEATURE_TYPE,
    RUNTIME_TARGET_TYPE,
    U32_TYPE,
)

ENTRY = 9000
LEAF = 9001
SIG = 9002
BODY = 9003
XCODE = 9004
ACODE = 9005


def native_container():
    c = counter_template(include_lowered=False).build()
    digest = semantic_digest("leaf", SIG, Effect.NONE, 1)
    c.nodes += [
        SignatureObject(SIG, "native_signature", LEAF, U32_TYPE).to_node(),
        FunctionObject(
            LEAF,
            "leaf",
            500,
            SIG,
            FunctionImplementation.CODE_BACKED,
            FunctionLayer.FOUNDATION,
            implementation_ids=(XCODE, ACODE),
        ).to_node(),
        CallObject(BODY, "body", ENTRY, LEAF).to_node(),
        FunctionObject(
            ENTRY,
            "native_entry",
            500,
            SIG,
            FunctionImplementation.COMPOSED,
            FunctionLayer.APPLICATION,
            body_id=BODY,
        ).to_node(),
        CodeObject(
            XCODE,
            "x86",
            LEAF,
            LEAF,
            X86_64_LINUX_TARGET_ID,
            CodeFormat.NATIVE,
            X86_64_RETURN_U32,
            SIG,
            Effect.NONE,
            1,
            digest,
        ).to_node(),
        CodeObject(
            ACODE,
            "arm",
            LEAF,
            LEAF,
            AARCH64_UEFI_TARGET_ID,
            CodeFormat.NATIVE,
            AARCH64_RETURN_U32,
            SIG,
            Effect.NONE,
            1,
            digest,
        ).to_node(),
    ]
    return c


def test_target_models_roundtrip_and_self_description():
    c = native_container()
    decoders = {
        ARCHITECTURE_TYPE: ArchitectureObject.from_node,
        ABI_TYPE: ABIObject.from_node,
        ENVIRONMENT_TYPE: EnvironmentObject.from_node,
        FEATURE_TYPE: FeatureObject.from_node,
        FEATURE_SET_TYPE: FeatureSetObject.from_node,
        RUNTIME_TARGET_TYPE: RuntimeTargetObject.from_node,
    }
    for node in c.nodes:
        if node.type_id in decoders:
            assert decoders[node.type_id](node).to_node().data == node.data
    assert set(decoders) <= {n.id for n in c.nodes if n.kind.name == "TYPE"}


def test_feature_model_rejects_noncanonical_sets_and_ref_disagreement():
    with pytest.raises(ValueError, match="sorted and unique"):
        FeatureSetObject(9990, "bad", 76, (820, 819))
    feature = FeatureObject(9991, "feature", 76, FeatureKind.ISA, 810).to_node()
    feature.refs[0].target = 811
    with pytest.raises(ValueError, match="payload/ref disagreement"):
        FeatureObject.from_node(feature)


def test_native_fixture_preflights_both_targets_exact_bytes():
    c = native_container()
    assert check(c) == []
    x = preflight(c, ENTRY, X86_64_LINUX_TARGET_ID)
    a = preflight(c, ENTRY, AARCH64_UEFI_TARGET_ID)
    x_code = c.node_by_id(XCODE)
    assert x_code is not None
    assert x.ok and x.functions[0].code_id == XCODE
    assert x_code.data.endswith(b"\x89\xf8\xc3")
    a_code = c.node_by_id(ACODE)
    assert a_code is not None
    assert a.ok and a.functions[0].code_id == ACODE
    assert a_code.data.endswith(b"\xc0\x03\x5f\xd6")


def test_unrelated_child_call_not_reachable_and_cycle_safe():
    c = native_container()
    c.nodes.append(CallObject(9010, "unrelated", ENTRY, 400).to_node())
    assert reachable_terminal_functions(c, ENTRY) == {LEAF}
    call = c.node_by_id(BODY)
    assert call is not None
    call.refs[0].target = ENTRY
    call.data = struct.pack("<3Q", ENTRY, 0, 0)
    assert reachable_terminal_functions(c, ENTRY) == set()


def test_nested_call_argument_is_reached():
    c = native_container()
    inner = CallObject(9011, "inner", BODY, LEAF).to_node()
    c.nodes.append(inner)
    body = c.node_by_id(BODY)
    assert body is not None
    body.refs.append(RefDef(body.id, 9011, RefKind.DATA, to_off=203))
    body.data = struct.pack("<3Q", LEAF, 1, 0)
    assert reachable_terminal_functions(c, ENTRY) == {LEAF}


def test_ambiguity_is_atomic_and_candidates_explain_rejections():
    c = native_container()
    source = c.node_by_id(XCODE)
    assert source is not None
    duplicate = deepcopy(source)
    duplicate.id = 9012
    duplicate.name = "x86_duplicate"
    c.nodes.append(duplicate)
    leaf = c.node_by_id(LEAF)
    assert leaf is not None
    leaf.refs.append(RefDef(leaf.id, 9012, RefKind.DATA, to_off=207))
    plan = preflight(c, ENTRY, X86_64_LINUX_TARGET_ID)
    assert (
        not plan.ok
        and not plan.functions
        and plan.diagnostics[0].code.value == "ambiguous"
    )
    details = candidate_details(c, LEAF, AARCH64_UEFI_TARGET_ID)
    assert any(not d.compatible and d.reason for d in details)


def test_import_requires_capability_and_plan_does_not_mutate_graph():
    c = native_container()
    imported = FunctionObject(
        9020,
        "host_clock",
        500,
        SIG,
        FunctionImplementation.IMPORTED,
        FunctionLayer.FOUNDATION,
    ).to_node()
    imp = ImportObject(9021, "clock_import", 500, 9020).to_node()
    body = c.node_by_id(BODY)
    assert body is not None
    body.data = struct.pack("<3Q", 9020, 0, 0)
    body.refs[0].target = 9020
    c.nodes += [imported, imp]
    before = c.to_dict()
    refused = preflight(c, ENTRY, X86_64_LINUX_TARGET_ID)
    assert (
        not refused.ok
        and refused.diagnostics[0].code.value == "missing_capability"
        and c.to_dict() == before
    )
    accepted = preflight(c, ENTRY, X86_64_LINUX_TARGET_ID, {9020: object()})
    assert accepted.ok and accepted.imports[0].import_id == 9021


def test_relocation_width_and_contract_disagreement_checked():
    c = native_container()
    relocation = RelocationObject(
        9030, "patch", XCODE, 0, RelocationKind.ABSOLUTE, LEAF, 4
    ).to_node()
    code = c.node_by_id(XCODE)
    assert code is not None
    code.refs.append(RefDef(code.id, 9030, RefKind.DATA, to_off=210))
    c.nodes.append(relocation)
    assert any("out of byte bounds" in e for e in check(c))
    broken = deepcopy(c)
    broken_code = broken.node_by_id(ACODE)
    assert broken_code is not None
    data = bytearray(broken_code.data)
    data[16:24] = (999).to_bytes(8, "little")
    broken_code.data = bytes(data)
    assert any("contract disagreement" in e for e in check(broken))


def test_call_payload_ref_disagreement_and_lowering_collision_are_errors():
    c = native_container()
    call = c.node_by_id(BODY)
    assert call is not None
    call.data = struct.pack("<3Q", LEAF, 1, 0)
    assert any("payload/ref disagreement" in error for error in check(c))

    collision = native_container()
    from pymergetic.rxf.ops.lowering import derived_id

    existing = deepcopy(collision.nodes[0])
    existing.id = derived_id(BODY, "call")
    existing.name = "collision"
    existing.parent = 0
    collision.nodes.append(existing)
    marker = deepcopy(collision.nodes[0])
    marker.id = derived_id(BODY, "marker")
    marker.name = "derived_marker"
    marker.attrs["derived"] = True
    collision.nodes.append(marker)
    assert any("lowering failed" in error for error in check(collision))


def test_targets_and_preflight_faces_are_json_safe(tmp_path):
    import json

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import (
        NATIVE_ENTRY_FUNCTION_ID,
        native_binding_template,
    )
    from pymergetic.rxf.face import preflight as preflight_face
    from pymergetic.rxf.face import targets as targets_face
    from pymergetic.rxf.output.engine import pack_layout

    path = tmp_path / "native.rxf"
    path.write_bytes(
        pack_layout(container_to_layout(native_binding_template().build()))
    )
    target_result = targets_face(str(path), X86_64_LINUX_TARGET_ID)
    plan_result = preflight_face(
        str(path), NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID
    )
    assert json.loads(json.dumps(target_result))["active_target"] == str(
        X86_64_LINUX_TARGET_ID
    )
    assert json.loads(json.dumps(plan_result))["ok"] is True


def test_face_targets_and_preflight_results(tmp_path):
    import json

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import (
        LESS_THAN_FUNCTION_ID,
        NATIVE_AARCH64_CODE_ID,
        NATIVE_ENTRY_FUNCTION_ID,
        NATIVE_LEAF_FUNCTION_ID,
        NATIVE_X86_CODE_ID,
        native_binding_template,
    )
    from pymergetic.rxf.face import preflight as preflight_face
    from pymergetic.rxf.face import targets as targets_face
    from pymergetic.rxf.output.engine import pack_layout

    native_path = tmp_path / "native.rxf"
    native_path.write_bytes(
        pack_layout(container_to_layout(native_binding_template().build()))
    )
    target_result = json.loads(
        json.dumps(targets_face(str(native_path), X86_64_LINUX_TARGET_ID))
    )
    assert target_result["ok"] is True
    assert target_result["active_target"] == str(X86_64_LINUX_TARGET_ID)
    assert {int(value) for value in target_result["targets"]} == {
        X86_64_LINUX_TARGET_ID,
        AARCH64_UEFI_TARGET_ID,
    }
    assert target_result["candidates"] == [
        {
            "function_id": str(NATIVE_LEAF_FUNCTION_ID),
            "candidates": [
                {
                    "code_id": str(NATIVE_X86_CODE_ID),
                    "target_id": str(X86_64_LINUX_TARGET_ID),
                    "compatible": True,
                    "rank": [0, 0, -1],
                    "reason": None,
                },
                {
                    "code_id": str(NATIVE_AARCH64_CODE_ID),
                    "target_id": str(AARCH64_UEFI_TARGET_ID),
                    "compatible": False,
                    "rank": None,
                    "reason": "architecture, ABI, environment, width, or endianness mismatch",
                },
            ],
        }
    ]

    plan = json.loads(
        json.dumps(
            preflight_face(
                str(native_path), NATIVE_ENTRY_FUNCTION_ID, X86_64_LINUX_TARGET_ID
            )
        )
    )
    assert plan["ok"] is True
    assert plan["health"] == "ready"
    assert plan["selected"][0]["code_id"] == str(NATIVE_X86_CODE_ID)

    counter_path = tmp_path / "counter.rxf"
    counter_path.write_bytes(
        pack_layout(
            container_to_layout(counter_template(include_lowered=False).build())
        )
    )
    refusal = preflight_face(
        str(counter_path), LESS_THAN_FUNCTION_ID, X86_64_LINUX_TARGET_ID
    )
    assert refusal["ok"] is False
    assert refusal["health"] == "refused"
    assert refusal["refusals"][0]["code"] == "unimplemented"
    assert refusal["refusals"][0]["object_id"] == str(LESS_THAN_FUNCTION_ID)


def test_cli_targets_and_preflight_dispatch(tmp_path, capsys):
    import json

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.cli import COMMANDS, main
    from pymergetic.rxf.expand import (
        LESS_THAN_FUNCTION_ID,
        NATIVE_ENTRY_FUNCTION_ID,
        NATIVE_X86_CODE_ID,
        native_binding_template,
    )
    from pymergetic.rxf.output.engine import pack_layout

    native_path = tmp_path / "native.rxf"
    native_path.write_bytes(
        pack_layout(container_to_layout(native_binding_template().build()))
    )
    counter_path = tmp_path / "counter.rxf"
    counter_path.write_bytes(
        pack_layout(
            container_to_layout(counter_template(include_lowered=False).build())
        )
    )

    assert "run" not in COMMANDS
    assert main(["rxf", "targets", str(native_path), str(X86_64_LINUX_TARGET_ID)]) == 0
    targets_output = json.loads(capsys.readouterr().out)
    assert targets_output["active_target"] == str(X86_64_LINUX_TARGET_ID)

    assert (
        main(
            [
                "rxf",
                "preflight",
                str(native_path),
                str(NATIVE_ENTRY_FUNCTION_ID),
                str(X86_64_LINUX_TARGET_ID),
            ]
        )
        == 0
    )
    success = json.loads(capsys.readouterr().out)
    assert success["selected"][0]["code_id"] == str(NATIVE_X86_CODE_ID)

    assert (
        main(
            [
                "rxf",
                "preflight",
                str(counter_path),
                str(LESS_THAN_FUNCTION_ID),
                str(X86_64_LINUX_TARGET_ID),
            ]
        )
        == 1
    )
    refused = json.loads(capsys.readouterr().out)
    assert refused["health"] == "refused"
    assert refused["refusals"][0]["code"] == "unimplemented"

    assert main(["rxf", "preflight", str(native_path)]) == 2
    malformed = capsys.readouterr()
    assert malformed.out == ""
    assert "usage: rxf preflight" in malformed.err


def test_checked_in_native_fixture_is_deterministic():
    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import native_binding_template
    from pymergetic.rxf.output.engine import pack_layout

    expected = pack_layout(container_to_layout(native_binding_template().build()))
    assert Path(__file__).with_name("native-binding.rxf").read_bytes() == expected
