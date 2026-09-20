"""Late-bound executable planning, packaging, and atomic publication."""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, cast

from pymergetic.rxf.checker import check
from pymergetic.rxf.executable.models import (
    ArtifactFormat,
    ExecutablePlatform,
    SegmentKind,
)
from pymergetic.rxf.executable.targets import resolve_executable_target
from pymergetic.rxf.execution.binder import preflight
from pymergetic.rxf.model.target import AARCH64_ID


class IntegrationGap(RuntimeError):
    """A required shared planner, layout engine, or packager is unavailable."""


def _json(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _json(item) for key, item in asdict(cast(Any, value)).items()}
    if hasattr(value, "to_dict"):
        return _json(value.to_dict())
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if hasattr(value, "name") and hasattr(value, "value"):
        return value.name
    return value


def _segment_view(segment: Any) -> dict[str, Any]:
    """Describe load geometry without duplicating the program as hex in JSON."""
    return {
        "name": segment.name,
        "kind": segment.kind.name,
        "virtual_address": segment.virtual_address,
        "memory_size": segment.memory_size,
        "file_offset": segment.file_offset,
        "alignment": segment.alignment,
        "permissions": segment.permissions.name,
        "file_size": len(segment.data),
        "zero_fill_size": segment.memory_size - len(segment.data),
        "sha256": hashlib.sha256(segment.data).hexdigest(),
    }


def _invoke(module: str, names: tuple[str, ...], **kwargs: Any) -> Any:
    try:
        loaded = importlib.import_module(module)
    except ModuleNotFoundError as error:
        if error.name == module:
            raise IntegrationGap(f"missing shared module {module}") from error
        raise
    for name in names:
        function = getattr(loaded, name, None)
        if callable(function):
            parameters = inspect.signature(function).parameters
            if any(
                value.kind is inspect.Parameter.VAR_KEYWORD
                for value in parameters.values()
            ):
                return function(**kwargs)
            return function(
                **{key: value for key, value in kwargs.items() if key in parameters}
            )
    raise IntegrationGap(f"{module} must export one of {', '.join(names)}")


def _tool(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path is not None:
            return path
    raise IntegrationGap(f"required executable tool is missing: {' or '.join(names)}")


def _startup_blob(target: Any) -> bytes:
    root = Path(__file__).resolve().parents[4]
    architecture = "aarch64" if target.architecture_id == AARCH64_ID else "x86_64"
    platform = target.platform.name.lower()
    source = root / "native" / "executable" / platform / f"{architecture}_start.S"
    clang = _tool("clang-18", "clang")
    linker = _tool("ld.lld-18", "ld.lld")
    objcopy = _tool("llvm-objcopy-18", "llvm-objcopy")
    triple = (
        f"{architecture}-linux-gnu"
        if platform == "linux"
        else f"{architecture}-none-elf"
    )
    with tempfile.TemporaryDirectory(prefix="rxf-startup-") as directory:
        work = Path(directory)
        obj, linked, raw = (
            work / "startup.o",
            work / "startup.elf",
            work / "startup.bin",
        )
        subprocess.run(
            [
                clang,
                "-target",
                triple,
                "-ffreestanding",
                "-fno-stack-protector",
                "-c",
                source,
                "-o",
                obj,
            ],
            check=True,
            capture_output=True,
        )
        if platform == "uefi" and architecture == "x86_64":
            extract_source = obj
        else:
            linker_script = work / "startup.ld"
            address = "0x401000" if platform == "linux" else "0"
            linker_script.write_text(
                f"SECTIONS {{ . = {address}; .text.startup : {{ *(.text.startup) }} }}\n"
            )
            entry = "efi_main" if platform == "uefi" else "_start"
            subprocess.run(
                [linker, "-T", linker_script, f"--entry={entry}", "-o", linked, obj],
                check=True,
                capture_output=True,
            )
            extract_source = linked
        objcopy_args = (
            [
                objcopy,
                "-O",
                "binary",
                "--only-section=.text.startup",
                extract_source,
                raw,
            ]
            if platform == "linux"
            else [objcopy, f"--dump-section=.text={raw}", extract_source]
        )
        subprocess.run(objcopy_args, check=True, capture_output=True)
        return raw.read_bytes()


def _install_startup(image: Any, target: Any) -> Any:
    startup = _startup_blob(target)
    if target.platform is ExecutablePlatform.LINUX:
        from pymergetic.rxf.executable.elf import install_linux_startup

        return install_linux_startup(image, startup)
    from pymergetic.rxf.executable.pe import install_uefi_startup

    return install_uefi_startup(image, startup)


def _binding_view(binding: Any) -> dict[str, Any]:
    return {
        "selected": [
            {
                "function_id": str(item.function_id),
                "code_id": str(item.code_id),
                "rank": list(item.rank),
            }
            for item in binding.bindings
        ],
        "diagnostics": [
            {
                "code": item.code.value,
                "object_id": str(item.object_id),
                "message": item.message,
            }
            for item in binding.diagnostics
        ],
    }


def prepare_executable(
    container: Any,
    source_blob: bytes,
    entry: int,
    target_value: int | str,
    *,
    package: bool = True,
) -> dict[str, Any]:
    """Validate and plan an executable entirely in memory."""
    target = resolve_executable_target(target_value)
    errors = check(container)
    if errors:
        return {"ok": False, "errors": errors, "target": target.to_dict()}
    binding = preflight(container, entry, target.target_id)
    if not binding.ok and target.legacy_shim:
        binding = preflight(
            container,
            entry,
            resolve_executable_target("x86_64_linux_sysv").target_id,
        )
    binding_view = _binding_view(binding)
    if not binding.ok:
        return {
            "ok": False,
            "errors": [item.message for item in binding.diagnostics],
            "target": target.to_dict(),
            **binding_view,
        }
    base = {
        "target": target.to_dict(),
        "entry": str(entry),
        "source_digest": hashlib.sha256(source_blob).hexdigest(),
        **binding_view,
    }
    try:
        plan = _invoke(
            "pymergetic.rxf.executable.pipeline",
            ("plan_executable", "build_plan"),
            container=container,
            entry=entry,
            entry_function_id=entry,
            target=target,
            binding_plan=binding,
        )
        image = _invoke(
            "pymergetic.rxf.executable.pipeline",
            ("build_image", "build_layout", "layout_executable"),
            plan=plan,
        )
        if package and target.platform in {
            ExecutablePlatform.LINUX,
            ExecutablePlatform.UEFI,
        }:
            image = _install_startup(image, target)
        result = {
            **base,
            "ok": True,
            "plan": {
                "functions": [
                    {
                        "function_id": str(item.function_id),
                        "code_id": None if item.code_id is None else str(item.code_id),
                        "name": item.name,
                    }
                    for item in plan.functions
                ],
                "objects": [str(value) for value in plan.object_ids],
                "capabilities": [str(value) for value in plan.capability_ids],
            },
            "layout": {
                "entry_address": image.entry_address,
                "segments": [_segment_view(item) for item in image.segments],
                "symbols": [_json(item) for item in image.symbols],
                "runtime_tables": [_json(item) for item in image.runtime_tables],
            },
            "provenance": _json(image.provenance),
        }
        graph_segment = next(
            (s for s in image.segments if s.kind is SegmentKind.RXF_IMAGE), None
        )
        if graph_segment is not None:
            from pymergetic.rxf.output.header import HEADER_SIZE, BinaryHeader

            header = BinaryHeader.from_wire(graph_segment.data[:HEADER_SIZE])
            result["rxf"] = {
                "size": header.image_size,
                "nodes": header.node_count,
                "source_nodes": len(container.nodes),
                "virtual_address": graph_segment.virtual_address,
                "sha256": hashlib.sha256(graph_segment.data).hexdigest(),
            }
        result["sizes"] = {
            "mapped_bytes": sum(s.memory_size for s in image.segments),
            "initialized_bytes": sum(len(s.data) for s in image.segments),
            "zero_fill_bytes": sum(s.memory_size - len(s.data) for s in image.segments),
            "startup_bytes": sum(
                len(s.data) for s in image.segments if s.kind is SegmentKind.STARTUP
            ),
            "runtime_table_bytes": sum(
                len(s.data)
                for s in image.segments
                if s.kind is SegmentKind.RUNTIME_TABLES
            ),
        }
        if not package:
            return result
        module, names = {
            ArtifactFormat.ELF64: (
                "pymergetic.rxf.executable.elf",
                ("format_elf64", "pack_elf", "pack_executable"),
            ),
            ArtifactFormat.PE32_PLUS: (
                "pymergetic.rxf.executable.pe",
                ("format_pe32_plus", "pack_pe", "pack_executable"),
            ),
            ArtifactFormat.BIOS_DISK_IMAGE: (
                "pymergetic.rxf.executable.bios",
                ("build_bios_image", "pack_bios", "pack_executable"),
            ),
        }[target.artifact_format]
        packed = _invoke(module, names, image=image, layout=image, target=target)
        if isinstance(packed, bytes):
            artifact, provenance = packed, result["provenance"]
        elif isinstance(packed, tuple) and len(packed) == 2:
            artifact, provenance = packed
        else:
            artifact = getattr(packed, "artifact", None)
            provenance = getattr(packed, "provenance", result["provenance"])
        if not isinstance(artifact, bytes):
            raise TypeError("executable packager artifact must be bytes")
        from pymergetic.rxf.executable.artifact import extract_rxf

        if graph_segment is None:
            raise ValueError("executable packaging must retain the complete RXF image")
        recovered = extract_rxf(artifact)
        if recovered != graph_segment.data:
            raise ValueError("executable envelope changed the authoritative RXF image")
        result["sizes"]["artifact_bytes"] = len(artifact)
        result["sizes"]["envelope_bytes"] = len(artifact) - len(recovered)
        result.update(
            artifact=artifact,
            output_digest=hashlib.sha256(artifact).hexdigest(),
            provenance=_json(provenance),
        )
        return result
    except IntegrationGap as error:
        return {
            **base,
            "ok": False,
            "errors": [str(error)],
            "integration_gaps": [str(error)],
        }


def publish_executable(
    output: str | Path, prepared: dict[str, Any]
) -> tuple[Path, Path]:
    """Publish output and deterministic sidecar without overwriting or partial files."""
    destination = Path(output)
    sidecar = Path(str(destination) + ".manifest.json")
    if destination.exists() or sidecar.exists():
        raise FileExistsError(
            f"build refuses to overwrite {destination if destination.exists() else sidecar}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {key: value for key, value in prepared.items() if key != "artifact"}
    manifest_bytes = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode()
    temps: list[Path] = []
    published: list[Path] = []
    try:
        for final, data in (
            (destination, prepared["artifact"]),
            (sidecar, manifest_bytes),
        ):
            descriptor, name = tempfile.mkstemp(
                prefix=f".{final.name}.", dir=final.parent
            )
            temp = Path(name)
            temps.append(temp)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(data)
                if (
                    final == destination
                    and prepared.get("target", {}).get("platform") == "LINUX"
                ):
                    os.fchmod(handle.fileno(), 0o755)
                handle.flush()
                os.fsync(handle.fileno())
        for temp, final in zip(temps, (destination, sidecar), strict=True):
            os.link(temp, final)
            published.append(final)
            temp.unlink()
        return destination, sidecar
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in temps:
            path.unlink(missing_ok=True)
