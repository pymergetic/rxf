"""Focused compiler artifact inspector projections."""

from dataclasses import replace

import pytest

from pymergetic.rxf.expand import starter_template
from pymergetic.rxf.server.compiler_views import compiler_artifact_view


@pytest.fixture(scope="module")
def container():
    return starter_template().build()


@pytest.mark.parametrize(
    "function_name", ["hash_map_get", "stable_sort", "count", "map_success"]
)
def test_representative_semantic_functions_expose_graph_and_native_image(
    container, function_name
):
    function = next(
        node
        for node in container.nodes
        if node.name == function_name and node.type_id == 14
    )
    view = compiler_artifact_view(container, function.id, "x86_64", True)
    assert view["ok"], view["diagnostics"]
    assert view["semantic_graph"]["blocks"]
    assert view["native_image"]["code_size"] > 0
    assert view["native_image"]["digest"]
    assert view["abi"]["entry"]


def test_target_mode_switching_and_provenance(container):
    function = next(
        node
        for node in container.nodes
        if node.name == "stable_sort" and node.type_id == 14
    )
    raw = compiler_artifact_view(container, function.id, "x86_64", False)
    arm = compiler_artifact_view(container, function.id, "aarch64", True)
    assert raw["ok"] and arm["ok"]
    assert raw["mode"] == "unoptimized" and arm["mode"] == "optimized"
    assert raw["native_image"]["architecture"] == "x86_64"
    assert arm["native_image"]["architecture"] == "aarch64"
    counts = arm["optimization"]["counts"]
    assert counts["blocks"]["after"] <= counts["blocks"]["before"]
    assert counts["operations"]["after"] <= counts["operations"]["before"]
    assert arm["optimization"]["provenance"]


def test_bindings_fixups_and_uint64_ids_are_strings(container):
    function = next(
        node
        for node in container.nodes
        if node.name == "hash_map_get" and node.type_id == 14
    )
    view = compiler_artifact_view(container, function.id, "x86_64", True)
    image = view["native_image"]
    assert image["function_bindings"] and image["fixups"]
    assert all(isinstance(link["id"], str) for link in image["function_bindings"])
    assert all(isinstance(fixup["target"]["id"], str) for fixup in image["fixups"])
    assert isinstance(view["function_id"], str)


def test_compile_failure_is_returned_as_exact_diagnostic(container):
    function = next(
        node
        for node in container.nodes
        if node.name == "stable_sort" and node.type_id == 14
    )
    broken = replace(
        container, nodes=[node for node in container.nodes if node.id != function.id]
    )
    view = compiler_artifact_view(broken, function.id, "x86_64", True)
    assert not view["ok"]
    assert view["diagnostics"][0]["function_id"] == str(function.id)
    assert str(function.id) in view["diagnostics"][0]["message"]
