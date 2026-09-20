"""Focused deterministic executable closure and layout proofs."""

from __future__ import annotations

import random
from copy import deepcopy
from dataclasses import replace

import pytest

from pymergetic.rxf.executable import (
    CAPABILITY_ENTRY_SIZE,
    FUNCTION_ENTRY_SIZE,
    OBJECT_ENTRY_SIZE,
    RUNTIME_CONTEXT_SIZE,
    TRANSACTION_ENTRY_SIZE,
    ExecutableBuildError,
    ExecutableLayout,
    RuntimeTableKind,
    SegmentPermissions,
    SymbolKind,
    build_executable_image,
    build_image,
    plan_executable,
    resolve_executable_target,
)
from pymergetic.rxf.executable.pipeline import _patch
from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID, starter_template

TARGET = resolve_executable_target(817)


def test_closure_layout_is_deterministic_under_shuffled_nodes() -> None:
    original = starter_template().build()
    shuffled = deepcopy(original)
    random.Random(941).shuffle(shuffled.nodes)
    first = build_executable_image(original, STARTER_MAIN_FUNCTION_ID, TARGET)
    second = build_executable_image(shuffled, STARTER_MAIN_FUNCTION_ID, TARGET)
    assert first == second
    assert first.provenance == second.provenance


def test_layout_has_exact_runtime_abi_and_wx_separation() -> None:
    image = build_executable_image(
        starter_template().build(), STARTER_MAIN_FUNCTION_ID, TARGET
    )
    assert RUNTIME_CONTEXT_SIZE == 144
    assert [(table.kind, table.entry_size) for table in image.runtime_tables] == [
        (RuntimeTableKind.FUNCTION, FUNCTION_ENTRY_SIZE),
        (RuntimeTableKind.OBJECT, OBJECT_ENTRY_SIZE),
        (RuntimeTableKind.CAPABILITY, CAPABILITY_ENTRY_SIZE),
        (RuntimeTableKind.TRANSACTION, TRANSACTION_ENTRY_SIZE),
    ]
    assert (
        FUNCTION_ENTRY_SIZE,
        OBJECT_ENTRY_SIZE,
        CAPABILITY_ENTRY_SIZE,
        TRANSACTION_ENTRY_SIZE,
    ) == (24, 80, 24, 48)
    assert all(
        not (
            s.permissions & SegmentPermissions.WRITE
            and s.permissions & SegmentPermissions.EXECUTE
        )
        for s in image.segments
    )
    assert tuple(s.virtual_address for s in image.segments) == tuple(
        sorted(s.virtual_address for s in image.segments)
    )
    assert image.entry_address == next(
        s.address
        for s in image.symbols
        if s.object_id == STARTER_MAIN_FUNCTION_ID and s.kind is SymbolKind.ENTRY
    )


def test_plan_exposes_sorted_closure_for_packagers() -> None:
    plan = plan_executable(starter_template().build(), STARTER_MAIN_FUNCTION_ID, TARGET)
    assert tuple(f.function_id for f in plan.functions) == tuple(
        sorted(f.function_id for f in plan.functions)
    )
    assert plan.object_ids == tuple(sorted(set(plan.object_ids)))
    assert plan.capability_ids == tuple(sorted(set(plan.capability_ids)))
    assert plan.binding.ok


def test_missing_or_incompatible_code_refuses() -> None:
    container = starter_template().build()
    target = replace(TARGET, target_id=999_999)
    with pytest.raises(ExecutableBuildError, match="RuntimeTarget|compatible|target"):
        plan_executable(container, STARTER_MAIN_FUNCTION_ID, target)


def test_address_overflow_refuses() -> None:
    plan = plan_executable(
        starter_template().build(),
        STARTER_MAIN_FUNCTION_ID,
        TARGET,
        layout=ExecutableLayout(base_address=(1 << 64) - 0x1000),
    )
    with pytest.raises(ExecutableBuildError, match="overflow"):
        build_image(plan)


def test_model_still_refuses_writable_executable_segment() -> None:
    image = build_executable_image(
        starter_template().build(), STARTER_MAIN_FUNCTION_ID, TARGET
    )
    with pytest.raises(ValueError, match="writable and executable"):
        replace(
            image.segments[1],
            permissions=SegmentPermissions.WRITE | SegmentPermissions.EXECUTE,
        )


def test_relocation_integer_math_and_bounds() -> None:
    data = bytearray(12)
    _patch(data, 2, 4, 0x12345678, False)
    _patch(data, 8, 4, -4, True)
    assert data[2:6] == bytes.fromhex("78563412")
    assert data[8:12] == bytes.fromhex("fcffffff")
    with pytest.raises(ExecutableBuildError, match="out of bounds"):
        _patch(data, 10, 4, 0, False)
    with pytest.raises(ExecutableBuildError, match="fit patch width"):
        _patch(data, 0, 1, 256, False)
    with pytest.raises(ExecutableBuildError, match="fit patch width"):
        _patch(data, 0, 1, -129, True)
