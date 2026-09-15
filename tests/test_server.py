from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from pymergetic.rxf.server.config import ServerConfig
from pymergetic.rxf.server.state import RXFLibrary


def _config(tmp_path: Path, body: str) -> ServerConfig:
    path = tmp_path / "server.toml"
    path.write_text(body)
    return ServerConfig.from_toml(path)


def test_mount_catalog_and_lazy_cache(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    (second / "nested").mkdir(parents=True)
    fixture = Path(__file__).with_name("counter.rxf")
    shutil.copyfile(fixture, first / "a.rxf")
    shutil.copyfile(fixture, second / "nested" / "b.rxf")
    library = RXFLibrary(
        _config(
            tmp_path,
            '[mounts.one]\npath = "first"\n[mounts.two]\npath = "second"\n',
        )
    )
    assert [entry.id for entry in library.list_entries()] == [
        "one:a.rxf",
        "two:nested/b.rxf",
    ]
    first_snapshot = library.load("one:a.rxf")
    assert library.load("one:a.rxf") is first_snapshot
    assert library.load("two:nested/b.rxf").entry.mount == "two"


def test_rendered_inspector_javascript_is_syntax_valid(tmp_path: Path) -> None:
    from fastapi.routing import APIRoute

    from pymergetic.rxf.server.api import create_app

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(Path(__file__).with_name("counter.rxf"), mount / "counter.rxf")
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    app = create_app(library)
    inspector = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/rxf/{entry_id}"
    )
    token = "c2FmZTpjb3VudGVyLnJ4Zg"
    html = inspector(token, None).body.decode()
    script_start = html.index("<script>") + len("<script>")
    script_end = html.index("</script>", script_start)
    script = html[script_start:script_end]

    result = subprocess.run(
        [
            node,
            "-e",
            'const fs=require("fs"); new Function(fs.readFileSync(0, "utf8"));',
        ],
        input=script,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_symlink_escape_is_not_cataloged(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    outside = tmp_path / "outside"
    mount.mkdir()
    outside.mkdir()
    shutil.copyfile(Path(__file__).with_name("counter.rxf"), outside / "secret.rxf")
    (mount / "escape.rxf").symlink_to(outside / "secret.rxf")
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    assert library.list_entries() == ()


def test_config_requires_mounts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="at least one"):
        _config(tmp_path, "max_file_size = 100\n")


def test_invalid_or_obsolete_rxf_is_not_cataloged(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "broken.rxf").write_bytes(b"RXFB" + b"\x00" * 92)
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    assert library.list_entries() == ()


def test_unified_inspector_and_tree_data_apis(tmp_path: Path) -> None:
    import base64

    from fastapi.responses import RedirectResponse
    from fastapi.routing import APIRoute

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import (
        NATIVE_ENTRY_FUNCTION_ID,
        NATIVE_X86_CODE_ID,
        native_binding_template,
    )
    from pymergetic.rxf.model.target import X86_64_LINUX_TARGET_ID
    from pymergetic.rxf.output.engine import pack_layout
    from pymergetic.rxf.server.api import _hexdump, _node_view, create_app
    from pymergetic.rxf.ty.builtins import (
        FUNCTION_TYPE,
        SPECIALIZATION_TYPE,
        TRAIT_TYPE,
    )

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(Path(__file__).with_name("counter.rxf"), mount / "counter.rxf")
    native_blob = pack_layout(container_to_layout(native_binding_template().build()))
    (mount / "native.rxf").write_bytes(native_blob)
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    app = create_app(library)
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"safe:counter.rxf").decode().rstrip("=")
    dashboard = endpoints["/rxf/{entry_id}"](token, None).body
    assert b"Parent / child object tree" in dashboard
    assert b"Global HeapImage" in dashboard
    assert b"Selected object details" in dashboard
    assert b"Object ownership tree" not in dashboard
    assert b"Deep views" not in dashboard
    assert b"Number(b.dataset.id)" not in dashboard
    assert b"collapse-all" in dashboard
    assert b"/memory?" in dashboard
    assert b"region=" not in dashboard
    assert b"execution.object_links" in dashboard
    assert b"grid-template-columns:minmax(5.5rem,7.5rem) minmax(0,1fr)" in dashboard
    assert b'class="kind" title=' in dashboard
    assert b"function positionTooltip(e)" in dashboard
    assert b"cursorX-offset-tipWidth" in dashboard
    assert b"cursorY-offset-tipHeight" in dashboard
    assert b"Find object by FQN or ID" in dashboard
    assert b"Active target" in dashboard
    assert b"Binding health" in dashboard
    assert b"/preflight?${query}" in dashboard
    assert b"function loadTargets()" in dashboard
    assert b"Native Code" in dashboard
    assert b"hex-dump" in dashboard
    assert b"entry-byte" in dashboard
    assert b"Target facts" in dashboard
    assert b"Contract status" in dashboard
    assert b"<h3>Imports</h3>" in dashboard
    assert b"<h3>Relocations</h3>" in dashboard
    assert b"searchParams.set('target'" in dashboard

    redirect = endpoints["/rxf/{entry_id}/nodes/{node_id}"](token, 0)
    assert isinstance(redirect, RedirectResponse)
    assert redirect.headers["location"].endswith("?object=0")
    for path in ("heap", "explore", "graph", "types", "functions"):
        response = endpoints[f"/rxf/{{entry_id}}/{path}"](token)
        assert isinstance(response, RedirectResponse)
        assert response.headers["location"] == f"/rxf/{token}"

    roots = endpoints["/api/rxfs/{entry_id}/tree"](token, None)
    assert [item["id"] for item in roots] == ["0"]
    assert isinstance(roots[0]["child_count"], int)
    children = endpoints["/api/rxfs/{entry_id}/tree"](token, 0)
    assert children
    assert any(item["kind"] == "MODULE" for item in children)
    assert all("fqn" in item for item in children)
    assert all(item["parent"] == "0" for item in children)
    assert endpoints["/api/rxfs/{entry_id}/tree"](token, 18446744073709551615) == []

    high_node = library.load("safe:counter.rxf").layout.nodes[0].model_copy()
    high_node.id = (1 << 63) + 7
    view = _node_view(high_node)
    assert view["id"] == "9223372036854775815"

    target_health = endpoints["/api/rxfs/{entry_id}/targets"](token, "817")
    assert target_health["active_target"] == "817"
    assert target_health["targets"]
    assert all(isinstance(item["id"], str) for item in target_health["targets"])
    assert all(
        "architecture" in item and "features" in item
        for item in target_health["targets"]
    )

    native_token = base64.urlsafe_b64encode(b"safe:native.rxf").decode().rstrip("=")
    native_targets = endpoints["/api/rxfs/{entry_id}/targets"](
        native_token, str(X86_64_LINUX_TARGET_ID)
    )
    active = next(item for item in native_targets["targets"] if item["active"])
    assert active["architecture"]["name"] == "x86_64"
    assert active["features"][0]["name"] == "x86_64_baseline"
    plan = endpoints["/api/rxfs/{entry_id}/preflight"](
        native_token,
        str(NATIVE_ENTRY_FUNCTION_ID),
        str(X86_64_LINUX_TARGET_ID),
    )
    assert plan["ok"] and plan["health"] == "ready"
    assert plan["selected"][0]["code_id"] == str(NATIVE_X86_CODE_ID)
    assert plan["diagnostics"] == []
    code_detail = endpoints["/api/rxfs/{entry_id}/nodes/{node_id}"](
        native_token, NATIVE_X86_CODE_ID
    )["execution"]
    assert code_detail["raw_hex"] == "89f8c3"
    assert code_detail["byte_count"] == 3
    assert code_detail["entry_offset"] == 0
    assert code_detail["hexdump"]["rows"] == [
        {
            "offset": 0,
            "offset_hex": "00000000",
            "hex": "89 f8 c3",
            "cells": ["89", "f8", "c3"] + [None] * 13,
            "ascii": "...",
            "contains_entry": True,
            "entry_index": 0,
        }
    ]
    assert code_detail["contract"]["agrees"] is True
    assert code_detail["target"]["architecture"]["name"] == "x86_64"
    assert code_detail["target"]["id"] == str(X86_64_LINUX_TARGET_ID)
    assert code_detail["owner_function"]["id"] == "5001"
    assert all(isinstance(link["id"], str) for link in code_detail["object_links"])
    large = _hexdump(bytes(range(256)) * 300, 65535)
    assert large["displayed_byte_count"] == 65536
    assert large["truncated"] is True
    assert len(large["rows"]) == 4096
    assert large["rows"][-1]["entry_index"] == 15
    refusal = endpoints["/api/rxfs/{entry_id}/preflight"](
        token, "420", str(X86_64_LINUX_TARGET_ID)
    )
    assert not refusal["ok"] and refusal["diagnostics"]
    assert isinstance(refusal["diagnostics"][0]["object_id"], str)

    memory = endpoints["/api/rxfs/{entry_id}/memory"](token, 0, 0, 64)
    assert len(memory["bins"]) == 64
    assert any(item["used"] for item in memory["bins"])
    assert all(
        item["node_id"] is None or isinstance(item["node_id"], str)
        for item in memory["bins"]
    )

    loaded = library.load("safe:counter.rxf")
    function = next(
        node for node in loaded.layout.nodes if node.type_id == FUNCTION_TYPE
    )
    function_detail = endpoints["/api/rxfs/{entry_id}/nodes/{node_id}"](
        token, function.id
    )
    assert function_detail["execution"]["category"] == "Function"
    assert function_detail["fqn"].startswith("pymergetic.rxf.")
    assert function_detail["execution"]["object_links"]
    assert all(
        isinstance(link["id"], str)
        for link in function_detail["execution"]["object_links"]
    )
    for type_id in (TRAIT_TYPE, SPECIALIZATION_TYPE):
        semantic = next(node for node in loaded.layout.nodes if node.type_id == type_id)
        detail = endpoints["/api/rxfs/{entry_id}/nodes/{node_id}"](token, semantic.id)
        assert detail["execution"]["object_links"]
        assert all(
            isinstance(link["id"], str) for link in detail["execution"]["object_links"]
        )


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (["--config", "server.toml"], ("server.toml", "127.0.0.1", 8420)),
        (
            ["--config", "server.toml", "--host", "0.0.0.0", "--port", "9000"],
            ("server.toml", "0.0.0.0", 9000),
        ),
        (["--config", "server.toml", "--host", "::"], ("server.toml", "::", 8420)),
        (["--config", "server.toml", "--host", "[::]"], ("server.toml", "::", 8420)),
        (
            ["--host", "inspector.example.test", "--config", "server.toml"],
            ("server.toml", "inspector.example.test", 8420),
        ),
    ],
)
def test_parse_serve_arguments(
    arguments: list[str], expected: tuple[str, str, int]
) -> None:
    from pymergetic.rxf.cli import _parse_serve_arguments

    assert _parse_serve_arguments(arguments) == expected


@pytest.mark.parametrize(
    "host",
    [
        "",
        "bad host",
        "bad\nhost",
        "http://localhost",
        "localhost/path",
        "localhost:8420",
        "[::1",
        "-bad.example",
        "bad_.example",
    ],
)
def test_parse_serve_arguments_rejects_invalid_hosts(host: str) -> None:
    from pymergetic.rxf.cli import _parse_serve_arguments

    with pytest.raises(ValueError, match="serve:"):
        _parse_serve_arguments(["--config", "server.toml", "--host", host])


@pytest.mark.parametrize("port", ["0", "65536", "-1", "not-an-integer"])
def test_parse_serve_arguments_rejects_invalid_ports(port: str) -> None:
    from pymergetic.rxf.cli import _parse_serve_arguments

    with pytest.raises(ValueError, match="serve:"):
        _parse_serve_arguments(["--config", "server.toml", "--port", port])


def test_parse_serve_arguments_rejects_unknown_arguments() -> None:
    from pymergetic.rxf.cli import _parse_serve_arguments

    with pytest.raises(ValueError, match="usage: rxf serve"):
        _parse_serve_arguments(["--config", "server.toml", "--workers", "2"])


def test_serve_passes_selected_binding_to_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from pymergetic.rxf.cli import main

    mount = tmp_path / "mount"
    mount.mkdir()
    config = tmp_path / "server.toml"
    config.write_text('[mounts.safe]\npath = "mount"\n')
    calls: list[tuple[object, dict[str, object]]] = []
    monkeypatch.setitem(
        sys.modules,
        "uvicorn",
        SimpleNamespace(run=lambda app, **kwargs: calls.append((app, kwargs))),
    )
    fake_app = object()
    monkeypatch.setitem(
        sys.modules,
        "pymergetic.rxf.server.api",
        SimpleNamespace(create_app=lambda library: fake_app),
    )

    assert (
        main(
            [
                "rxf",
                "serve",
                "--config",
                str(config),
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
            ]
        )
        == 0
    )
    assert calls and calls[0][1] == {
        "host": "0.0.0.0",
        "port": 9000,
        "log_level": "info",
    }
    assert "listening on 0.0.0.0:9000" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", "http://localhost:8420"),
        ("::1", "http://[::1]:8420"),
        ("::", "listening on [::]:8420"),
    ],
)
def test_serve_endpoint_output(host: str, expected: str) -> None:
    from pymergetic.rxf.cli import _serve_endpoint

    assert expected in _serve_endpoint(host, 8420)


def test_single_image_redirects_and_starter_defaults_to_entry(tmp_path: Path) -> None:
    import base64

    from fastapi.responses import RedirectResponse
    from fastapi.routing import APIRoute

    from pymergetic.rxf.expand import (
        STARTER_MAIN_FUNCTION_ID,
    )
    from pymergetic.rxf.server.api import create_app

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    app = create_app(library)
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"examples:starter.rxf").decode().rstrip("=")
    redirect = endpoints["/"]()
    assert isinstance(redirect, RedirectResponse)
    assert redirect.headers["location"] == f"/rxf/{token}"

    html = endpoints["/rxf/{entry_id}"](token, None).body
    assert f'const initialObject="{STARTER_MAIN_FUNCTION_ID}"'.encode() in html
    assert b'data-nav="entry"' in html
    assert b'data-nav="application"' in html
    assert b'data-nav="library"' in html
    assert b'data-nav="foundation"' in html
    assert b'data-nav="code"' in html
    assert b"319 Functions" in html and b"572 Code" in html and b"2 targets" in html


def test_server_catalogs_explicit_v4_inspection_image(tmp_path: Path) -> None:
    import struct

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import native_binding_template
    from pymergetic.rxf.output.engine import pack_layout

    mount = tmp_path / "mount"
    mount.mkdir()
    blob = bytearray(
        pack_layout(container_to_layout(native_binding_template().build()))
    )
    struct.pack_into("<I", blob, 4, 4)
    (mount / "legacy.rxf").write_bytes(blob)
    library = RXFLibrary(_config(tmp_path, '[mounts.legacy]\npath = "mount"\n'))
    assert [entry.id for entry in library.list_entries()] == ["legacy:legacy.rxf"]
    assert library.load("legacy:legacy.rxf").layout.header.version == 4
