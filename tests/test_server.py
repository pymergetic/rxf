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
    library.load("safe:counter.rxf")
    app = create_app(library)
    inspector = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/rxf/{entry_id}"
    )
    token = "c2FmZTpjb3VudGVyLnJ4Zg"
    html = inspector(token, None, None).body.decode()
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
    library.load("safe:counter.rxf")
    library.load("safe:native.rxf")
    app = create_app(library)
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"safe:counter.rxf").decode().rstrip("=")
    dashboard = endpoints["/rxf/{entry_id}"](token, None, None).body
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

    from pymergetic.rxf.expand import STARTER_MAIN_FUNCTION_ID
    from pymergetic.rxf.server.api import create_app
    from pymergetic.rxf.ty.builtins import (
        CODE_TYPE,
        FUNCTION_TYPE,
        RUNTIME_TARGET_TYPE,
        TARGET_TYPE,
    )

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    library.load("examples:starter.rxf")
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

    html = endpoints["/rxf/{entry_id}"](token, None, None).body
    nodes = library.load("examples:starter.rxf").container.nodes
    expected_counts = (
        (sum(node.type_id == FUNCTION_TYPE for node in nodes), "Functions"),
        (sum(node.type_id == CODE_TYPE for node in nodes), "Code"),
        (
            sum(node.type_id in (TARGET_TYPE, RUNTIME_TARGET_TYPE) for node in nodes),
            "targets",
        ),
    )
    assert f'const initialObject="{STARTER_MAIN_FUNCTION_ID}"'.encode() in html
    assert b'data-nav="entry"' in html
    assert b'data-nav="application"' in html
    assert b'data-nav="library"' in html
    assert b'data-nav="foundation"' in html
    assert b'data-nav="code"' in html
    assert all(f"{count} {label}".encode() in html for count, label in expected_counts)


def _memory_bins_reference(loaded, start: int, end: int, bins: int):
    from pymergetic.rxf.server.api import _heap_view

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
    nodes = {node.id: node for node in loaded.layout.nodes}
    for cell in heap.cells:
        if cell.end <= start or cell.offset >= end:
            continue
        first = max(0, (max(cell.offset, start) - start) * bins // span)
        last = min(bins - 1, (max(cell.end - 1, start) - start) * bins // span)
        for index in range(first, last + 1):
            bs = start + index * span // bins
            be = start + (index + 1) * span // bins
            used = max(0, min(cell.end, be) - max(cell.offset, bs))
            item = result[index]
            item["used"] += used
            item["count"] += 1
            type_id = str(cell.header.type_id)
            item["types"][type_id] = item["types"].get(type_id, 0) + used
            owner = nodes[cell.header.id].owner_kind.name
            disposition = nodes[cell.header.id].state.disposition.name
            item["owners"][owner] = item["owners"].get(owner, 0) + used
            item["dispositions"][disposition] = (
                item["dispositions"].get(disposition, 0) + used
            )
            cell_id = str(cell.header.id)
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
        del item["types"], item["owners"], item["dispositions"]
    return {"heap": _heap_view(loaded), "start": start, "end": end, "bins": result}


def test_preload_progress_shell_and_shared_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64
    import threading
    import time

    from fastapi.routing import APIRoute

    from pymergetic.rxf.server import state
    from pymergetic.rxf.server.api import create_app

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    original = state.unpack_layout
    gate = threading.Event()
    calls = 0

    def delayed(blob, progress=None):
        nonlocal calls
        calls += 1
        gate.wait(10)
        return original(blob, progress=progress)

    monkeypatch.setattr(state, "unpack_layout", delayed)
    app = create_app(library)
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"examples:starter.rxf").decode().rstrip("=")
    began = time.perf_counter()
    shell = endpoints["/rxf/{entry_id}"](token, "63155", None).body.decode()
    assert time.perf_counter() - began < 1.0
    assert "Starting measured RXF preload" in shell
    assert f'const token="{token}"' in shell
    assert '"object": "63155"' in shell
    assert "setTimeout(poll,delay)" in shell
    assert "location.replace(readyUrl())" in shell
    assert "if(stopped)return" in shell and "reloaded=true" in shell
    progress_endpoint = endpoints["/api/rxfs/{entry_id}/load-progress"]
    began = time.perf_counter()
    first = progress_endpoint(token)
    assert time.perf_counter() - began < 0.1
    assert first["state"] == "loading" and first["phase"] == "reading"
    endpoints["/api/rxfs/{entry_id}/preload"](token)
    deadline = time.monotonic() + 2
    while calls == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert calls == 1
    gate.set()
    deadline = time.monotonic() + 60
    samples = []
    while time.monotonic() < deadline:
        sample = progress_endpoint(token)
        samples.append(sample)
        if sample["done"]:
            break
        time.sleep(0.01)
    assert samples[-1]["state"] == "done"
    percents = [
        sample["percent"] for sample in samples if sample["percent"] is not None
    ]
    completed = [sample["completed"] for sample in samples]
    assert percents == sorted(percents) and completed == sorted(completed)
    assert samples[-1]["percent"] == 100.0
    assert samples[-1]["elapsed_seconds"] >= 0
    assert library.cached("examples:starter.rxf") is not None
    ready = endpoints["/rxf/{entry_id}"](token, "63155", "1").body.decode()
    assert "Parent / child object tree" in ready


def test_load_progress_resets_after_mount_invalidation(tmp_path: Path) -> None:
    import time

    mount = tmp_path / "mount"
    mount.mkdir()
    target = mount / "counter.rxf"
    shutil.copyfile(Path(__file__).with_name("counter.rxf"), target)
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    first = library.load("safe:counter.rxf")
    assert library.load_progress("safe:counter.rxf")["state"] == "done"
    time.sleep(0.002)
    target.touch()
    library.scan()
    assert library.cached("safe:counter.rxf") is None
    progress = library.load_progress("safe:counter.rxf")
    assert progress["state"] == "idle" and progress["percent"] is None
    second = library.load("safe:counter.rxf")
    assert second is not first


def test_load_progress_error_and_cache_hit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import time

    from pymergetic.rxf.server import state

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(Path(__file__).with_name("counter.rxf"), mount / "counter.rxf")
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    monkeypatch.setattr(
        state,
        "unpack_layout",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("measured parse failure")
        ),
    )
    library.preload("safe:counter.rxf")
    deadline = time.monotonic() + 5
    progress = library.load_progress("safe:counter.rxf")
    while time.monotonic() < deadline:
        progress = library.load_progress("safe:counter.rxf")
        if progress["state"] == "error":
            break
        time.sleep(0.01)
    assert (
        progress["state"] == "error" and progress["error"] == "measured parse failure"
    )


def test_memory_bins_match_reference_and_cache_starter(tmp_path: Path) -> None:
    import time

    from pymergetic.rxf.server.api import _memory_bins

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    loaded = library.load("examples:starter.rxf")
    assert len(loaded.memory_cells) == len(loaded.layout.heap.cells)
    for start, end, bins in ((0, 0, 635), (1000, 100000, 73), (500000, 900000, 257)):
        expected = _memory_bins_reference(loaded, start, end, bins)
        began = time.perf_counter()
        actual = _memory_bins(loaded, start, end, bins)
        elapsed = time.perf_counter() - began
        assert actual == expected
        assert elapsed < 2.0
        assert _memory_bins(loaded, start, end, bins) is actual
    assert len(loaded.memory_cache) == 3


def test_snapshot_load_is_single_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading
    import time

    from pymergetic.rxf.server import state

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    original = state.unpack_layout
    calls = 0

    def counted(blob, progress=None):
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return original(blob, progress=progress)

    monkeypatch.setattr(state, "unpack_layout", counted)
    barrier = threading.Barrier(3)
    snapshots = []

    def load():
        barrier.wait()
        snapshots.append(library.load("examples:starter.rxf"))

    threads = [threading.Thread(target=load) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=60)
    assert calls == 1
    assert len(snapshots) == 2 and snapshots[0] is snapshots[1]


def test_inspector_deep_link_initialization_and_query_preservation(
    tmp_path: Path,
) -> None:
    import base64

    from fastapi.routing import APIRoute

    from pymergetic.rxf.server.api import create_app

    mount = tmp_path / "mount"
    mount.mkdir()
    shutil.copyfile(
        Path(__file__).parents[1] / "examples/starter.rxf", mount / "starter.rxf"
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.examples]\npath = "mount"\n'))
    library.load("examples:starter.rxf")
    app = create_app(library)
    document = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/rxf/{entry_id}"
    )
    token = base64.urlsafe_b64encode(b"examples:starter.rxf").decode().rstrip("=")

    html = document(token, "63155", None).body.decode()
    assert 'const initialObject="63155"' in html
    assert "const initialObjectDiagnostic=null" in html
    assert "async function initializeInspector()" in html
    assert "const memoryPromise=loadMemory().catch(()=>null)" in html
    assert "await appendChildren(tree);const selectedOk=await selectNode" in html
    assert "await memoryPromise" in html
    assert "Loading memory map…" in html
    assert "await selectNode(initialObject,false)" in html
    assert "params.get('compiler_target')||'x86_64'" in html
    assert "params.get('compiler_mode')||'optimized'" in html
    assert "u.searchParams.set('object',selected)" in html
    assert "u.searchParams.set('compiler_target',architecture.value)" in html
    assert "u.searchParams.set('compiler_mode',mode.value)" in html
    assert "const nodes=await fetchJson(`/api/rxfs/${token}/nodes`)" in html
    assert "holder.firstChild.onclick=()=>selectNode(n.id,true)" in html
    assert "canvas.dataset.selectedStart" in html
    assert token in html and f"{token}%" not in html

    entry_id = str(library.load("examples:starter.rxf").container.header.entry_node)
    missing = document(token, "18446744073709551615", None).body.decode()
    assert f'const initialObject="{entry_id}"' in missing
    assert (
        "Object 18446744073709551615 was not found; selected entry instead." in missing
    )
    invalid = document(token, "not-a-uint64", None).body.decode()
    assert f'const initialObject="{entry_id}"' in invalid
    assert "object must be a decimal uint64 object ID" in invalid


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


def _inspector_elf(raw: bytes) -> bytes:
    """An independent wrapper fixture, without native toolchains or sidecars."""
    from dataclasses import replace

    from pymergetic.rxf.executable.elf import format_elf64, layout_elf64_image
    from pymergetic.rxf.executable.targets import resolve_executable_target

    image = layout_elf64_image(
        resolve_executable_target("x86_64_linux_sysv"), 5001, b"\xc3", data=b"\0"
    )
    text, rodata, data = image.segments
    extent = (len(raw) + 4095) & -4096
    return format_elf64(
        replace(
            image,
            segments=(
                replace(text, data=raw, memory_size=len(raw)),
                replace(
                    rodata,
                    virtual_address=text.virtual_address + extent,
                    file_offset=text.file_offset + extent,
                ),
                replace(
                    data,
                    virtual_address=text.virtual_address + extent + 4096,
                    file_offset=text.file_offset + extent + 4096,
                ),
            ),
        )
    )


def test_standalone_elf_inspector_preserves_graph_code_and_memory(
    tmp_path: Path,
) -> None:
    import base64

    from fastapi.routing import APIRoute

    from pymergetic.rxf.bridge import container_to_layout
    from pymergetic.rxf.expand import NATIVE_X86_CODE_ID, native_binding_template
    from pymergetic.rxf.output.engine import pack_layout
    from pymergetic.rxf.server.api import _memory_bins, create_app

    mount = tmp_path / "mount"
    mount.mkdir()
    raw = pack_layout(container_to_layout(native_binding_template().build()))
    sidecar = mount / "standalone.rxf"
    sidecar.write_bytes(raw)
    executable = mount / "standalone"
    executable.write_bytes(_inspector_elf(sidecar.read_bytes()))
    sidecar.unlink()
    assert list(mount.iterdir()) == [executable]
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    assert [entry.id for entry in library.list_entries()] == ["safe:standalone"]
    snapshot = library.load("safe:standalone")
    assert snapshot.blob == raw
    assert snapshot.entry.size == executable.stat().st_size > len(raw)
    assert snapshot.entry.mtime_ns == executable.stat().st_mtime_ns
    assert NATIVE_X86_CODE_ID in snapshot.layout_nodes_by_id
    assert _memory_bins(snapshot, 0, 0, 31) == _memory_bins_reference(
        snapshot, 0, 0, 31
    )
    endpoints = {
        route.path: route.endpoint
        for route in create_app(library).routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"safe:standalone").decode().rstrip("=")
    html = endpoints["/rxf/{entry_id}"](token, None, None).body
    assert b"Parent / child object tree" in html
    code = endpoints["/api/rxfs/{entry_id}/nodes/{node_id}"](token, NATIVE_X86_CODE_ID)
    assert code["execution"]["raw_hex"] == "89f8c3"
    assert library.load_progress("safe:standalone")["percent"] == 100.0
    assert library.load("safe:standalone") is snapshot


@pytest.mark.parametrize(
    "name", ["program.elf", "program.efi", "program.img", "program"]
)
def test_executable_catalog_validates_contents_and_wrapper_freshness(
    tmp_path: Path, name: str
) -> None:
    import os

    raw = Path(__file__).with_name("counter.rxf").read_bytes()
    mount = tmp_path / "mount"
    mount.mkdir()
    target = mount / name
    target.write_bytes(_inspector_elf(raw))
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    first = library.load(f"safe:{name}")
    assert first.blob == raw
    os.utime(target, ns=(first.entry.mtime_ns, first.entry.mtime_ns + 1_000_000))
    second = library.load(f"safe:{name}")
    assert second is not first
    assert second.blob == first.blob
    target.write_bytes(target.read_bytes()[:100])
    assert library.scan() == ()


def test_executable_catalog_rejects_invalid_wrappers_and_unmapped_rxf(
    tmp_path: Path,
) -> None:
    from pymergetic.rxf.executable.elf import format_elf64, layout_elf64_image
    from pymergetic.rxf.executable.targets import resolve_executable_target

    mount = tmp_path / "mount"
    mount.mkdir()
    raw = Path(__file__).with_name("counter.rxf").read_bytes()
    plain = format_elf64(
        layout_elf64_image(
            resolve_executable_target("x86_64_linux_sysv"), 1, b"\xc3", data=b"\0"
        )
    )
    for name, blob in {
        "truncated.elf": b"\x7fELF",
        "truncated.efi": b"MZ" + bytes(510),
        "truncated.img": bytes(510) + b"\x55\xaa",
        "trailer.elf": plain + raw,
        "non-rxf": plain,
        "invalid-graph.elf": _inspector_elf(b"RXFB" + bytes(508)),
    }.items():
        (mount / name).write_bytes(blob)
    assert (
        RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n')).list_entries()
        == ()
    )


def test_executable_discovery_limits_reads_and_enforces_mount_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mount = tmp_path / "mount"
    mount.mkdir()
    outside = tmp_path / "outside.elf"
    outside.write_bytes(
        _inspector_elf(Path(__file__).with_name("counter.rxf").read_bytes())
    )
    (mount / "escape.elf").symlink_to(outside)
    (mount / "unrelated.txt").write_bytes(b"text")
    (mount / "unrelated").write_bytes(b"text" * 100)
    (mount / "huge.elf").write_bytes(bytes(2048))
    original_open = Path.open
    reads = []

    class PrefixOnly:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def read(self, size=-1):
            reads.append(size)
            assert size == 4
            return b"text"

    def bounded_open(path, *args, **kwargs):
        assert path not in (outside, mount / "unrelated.txt", mount / "huge.elf")
        if path == mount / "unrelated":
            return PrefixOnly()
        return original_open(path, *args, **kwargs)

    config = _config(tmp_path, 'max_file_size = 1024\n[mounts.safe]\npath = "mount"\n')
    monkeypatch.setattr(Path, "open", bounded_open)
    assert RXFLibrary(config).list_entries() == ()
    assert reads == [4]


@pytest.mark.parametrize("preload", [False, True])
def test_executable_load_rechecks_mount_after_catalog(
    tmp_path: Path, preload: bool
) -> None:
    import time

    mount = tmp_path / "mount"
    mount.mkdir()
    target = mount / "program.elf"
    target.write_bytes(
        _inspector_elf(Path(__file__).with_name("counter.rxf").read_bytes())
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    outside = tmp_path / "outside.elf"
    target.rename(outside)
    target.symlink_to(outside)
    if not preload:
        with pytest.raises(ValueError, match="escapes its mount"):
            library.load("safe:program.elf")
        return
    library.preload("safe:program.elf")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        progress = library.load_progress("safe:program.elf")
        if progress["state"] == "error":
            break
        time.sleep(0.01)
    assert progress["state"] == "error"
    assert "escapes its mount" in progress["error"]
    assert library.cached("safe:program.elf") is None


@pytest.mark.parametrize("platform", ["uefi", "bios"])
def test_inspector_extracts_actual_firmware_envelopes(
    tmp_path: Path, platform: str
) -> None:
    from pymergetic.rxf.executable.bios import build_bios_image
    from pymergetic.rxf.executable.models import (
        ExecutableImage,
        ExecutableSegment,
        SegmentKind,
        SegmentPermissions,
    )
    from pymergetic.rxf.executable.pe import format_pe32_plus
    from pymergetic.rxf.executable.targets import resolve_executable_target

    raw = Path(__file__).with_name("counter.rxf").read_bytes()
    rx = SegmentPermissions.READ | SegmentPermissions.EXECUTE
    rw = SegmentPermissions.READ | SegmentPermissions.WRITE
    graph_address = 0x402000
    data_address = graph_address + ((len(raw) + 4095) & -4096)
    image = ExecutableImage(
        resolve_executable_target(f"x86_64_{platform}_sysv"),
        1,
        0x401000,
        (
            # The two startup pointer slots give PE its relocation records.
            ExecutableSegment(
                "startup", SegmentKind.STARTUP, 0x401000, 16, 0, 4096, rx, bytes(16)
            ),
            ExecutableSegment(
                "graph",
                SegmentKind.RXF_IMAGE,
                graph_address,
                len(raw),
                0,
                4096,
                rx,
                raw,
            ),
            ExecutableSegment(
                "data", SegmentKind.DATA, data_address, 1, 0, 4096, rw, b"\0"
            ),
        ),
    )
    blob = format_pe32_plus(image) if platform == "uefi" else build_bios_image(image)
    mount = tmp_path / "mount"
    mount.mkdir()
    name = "standalone.efi" if platform == "uefi" else "standalone.img"
    (mount / name).write_bytes(blob)
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))
    snapshot = library.load(f"safe:{name}")
    assert snapshot.blob == raw
    assert snapshot.entry.size == len(blob)
    assert snapshot.memory_cells
    assert len(snapshot.layout.nodes) == len(snapshot.container.nodes)


def test_wrapper_extraction_failure_is_reported_in_load_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pymergetic.rxf.server import state

    mount = tmp_path / "mount"
    mount.mkdir()
    (mount / "program.elf").write_bytes(
        _inspector_elf(Path(__file__).with_name("counter.rxf").read_bytes())
    )
    library = RXFLibrary(_config(tmp_path, '[mounts.safe]\npath = "mount"\n'))

    def fail(_blob):
        raise ValueError("invalid ELF64 PT_LOAD extent or alignment")

    monkeypatch.setattr(state, "extract_rxf", fail)
    with pytest.raises(ValueError, match="PT_LOAD extent"):
        library.load("safe:program.elf")
    progress = library.load_progress("safe:program.elf")
    assert progress["state"] == "error"
    assert progress["error"] == "invalid ELF64 PT_LOAD extent or alignment"
    assert library.cached("safe:program.elf") is None
