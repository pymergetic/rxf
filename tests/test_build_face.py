from __future__ import annotations

from pathlib import Path

import pytest

from pymergetic.rxf.cli import _BUILD_USAGE, _parse_build_arguments, main
from pymergetic.rxf.executable.build import publish_executable
from pymergetic.rxf.executable.targets import resolve_executable_target


def test_build_cli_exact_parse_and_target_name_id() -> None:
    args = [
        "image.rxf",
        "--entry",
        "0x10",
        "--target",
        "x86_64_linux_sysv",
        "--output",
        "app",
    ]
    assert _parse_build_arguments(args) == ("image.rxf", 16, "x86_64_linux_sysv", "app")
    target = resolve_executable_target("x86_64_linux_sysv")
    assert resolve_executable_target(str(target.target_id)) is target


@pytest.mark.parametrize(
    "arguments", [[], ["image.rxf", "--wat", "x"], ["image.rxf", "--entry", "1"]]
)
def test_build_cli_rejects_unknown_or_incomplete_options(arguments: list[str]) -> None:
    with pytest.raises(ValueError, match="usage: rxf build"):
        _parse_build_arguments(arguments)


def test_build_cli_errors_document_exact_usage(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["rxf", "build", "image.rxf", "--unknown", "x"]) == 2
    assert _BUILD_USAGE in capsys.readouterr().err


def test_build_cli_prints_summary_not_entire_object_world(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    from pymergetic.rxf import cli

    monkeypatch.setattr(
        cli,
        "build_executable",
        lambda *args: {
            "ok": True,
            "path": "app",
            "manifest": "app.manifest.json",
            "size": 100,
            "sizes": {"artifact_bytes": 100},
            "rxf": {"nodes": 3},
            "layout": {"symbols": [1, 2, 3]},
            "plan": {},
            "selected": [1, 2, 3],
        },
    )
    assert (
        main(
            [
                "rxf",
                "build",
                "source.rxf",
                "--entry",
                "1",
                "--target",
                "x86_64_linux_sysv",
                "--output",
                "app",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["rxf"]["nodes"] == 3
    assert result["manifest"] == "app.manifest.json"
    assert not {"layout", "plan", "selected"} & result.keys()


def _prepared() -> dict:
    return {
        "ok": True,
        "artifact": b"artifact",
        "target": {"target_id": 817},
        "entry": "1",
        "selected": [],
        "source_digest": "00" * 32,
        "output_digest": "11" * 32,
        "plan": {},
        "layout": {},
        "provenance": {"toolchain": ["test"]},
    }


def test_segment_manifest_describes_bytes_without_hex_copy() -> None:
    from pymergetic.rxf.executable.build import _segment_view
    from pymergetic.rxf.executable.models import (
        ExecutableSegment,
        SegmentKind,
        SegmentPermissions,
    )

    segment = ExecutableSegment(
        "runtime",
        SegmentKind.RUNTIME_TABLES,
        0x400000,
        4096,
        4096,
        4096,
        SegmentPermissions.READ | SegmentPermissions.WRITE,
        b"abc",
    )
    view = _segment_view(segment)
    assert "data" not in view
    assert view["file_size"] == 3
    assert view["zero_fill_size"] == 4093
    assert len(view["sha256"]) == 64


def test_atomic_publish_and_manifest_are_deterministic(tmp_path: Path) -> None:
    output = tmp_path / "app"
    destination, manifest = publish_executable(output, _prepared())
    assert destination.read_bytes() == b"artifact"
    first = manifest.read_bytes()
    assert first.endswith(b"\n") and b'"artifact"' not in first
    with pytest.raises(FileExistsError):
        publish_executable(output, _prepared())
    assert manifest.read_bytes() == first


def test_publish_rolls_back_no_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from pymergetic.rxf.executable import build

    real_link = os.link
    count = 0

    def failing_link(source, destination):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("publish failure")
        return real_link(source, destination)

    monkeypatch.setattr(build.os, "link", failing_link)
    output = tmp_path / "app"
    with pytest.raises(OSError, match="publish failure"):
        publish_executable(output, _prepared())
    assert not output.exists()
    assert not Path(str(output) + ".manifest.json").exists()
    assert not list(tmp_path.glob(".*"))


def test_publish_linux_is_executable_and_uefi_is_not(tmp_path: Path) -> None:
    linux = _prepared()
    linux["target"]["platform"] = "LINUX"
    destination, _ = publish_executable(tmp_path / "linux", linux)
    assert destination.stat().st_mode & 0o111
    uefi = _prepared()
    uefi["target"]["platform"] = "UEFI"
    destination, _ = publish_executable(tmp_path / "uefi", uefi)
    assert not destination.stat().st_mode & 0o111
