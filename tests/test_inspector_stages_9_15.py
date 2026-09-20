"""Unified stage 9-15 inspector API tests."""

import base64
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.routing import APIRoute

from pymergetic.rxf.bridge import container_to_layout
from pymergetic.rxf.expand import NATIVE_ENTRY_FUNCTION_ID, native_binding_template
from pymergetic.rxf.model.target import X86_64_LINUX_TARGET_ID
from pymergetic.rxf.output.engine import pack_layout
from pymergetic.rxf.server.api import create_app
from pymergetic.rxf.server.config import ServerConfig
from pymergetic.rxf.server.state import RXFLibrary


def test_stage_tooling_api_surfaces(tmp_path: Path) -> None:
    mount = tmp_path / "mount"
    mount.mkdir()
    blob = pack_layout(container_to_layout(native_binding_template().build()))
    (mount / "native.rxf").write_bytes(blob)
    config = tmp_path / "server.toml"
    config.write_text('[mounts.safe]\npath = "mount"\n')
    app = create_app(RXFLibrary(ServerConfig.from_toml(config)))
    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    token = base64.urlsafe_b64encode(b"safe:native.rxf").decode().rstrip("=")
    args = (token, str(NATIVE_ENTRY_FUNCTION_ID), str(X86_64_LINUX_TARGET_ID))
    assert endpoints["/api/rxfs/{entry_id}/boot"](*args)["ok"]
    reachability = endpoints["/api/rxfs/{entry_id}/reachability"](*args)
    assert reachability["reachable_ids"] and reachability["reasons"]
    reflection = endpoints["/api/rxfs/{entry_id}/reflection"](token)
    assert reflection["types"] and reflection["functions"]
    source = reflection["types"][0]["id"]
    assert endpoints["/api/rxfs/{entry_id}/migration"](token, source, source)["ok"]
    assert endpoints["/api/rxfs/{entry_id}/capabilities"](token) == {"requirements": []}
    download = endpoints["/api/rxfs/{entry_id}/canonical.json"](token)
    assert download.media_type == "application/json"
    assert download.body.endswith(b"\n")
