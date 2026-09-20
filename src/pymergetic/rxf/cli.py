"""rxf CLI — mint, inspect, replay, certify, dump, layout state binaries."""

import ipaddress
import json
import sys
from pathlib import Path

from pymergetic.rxf import __version__ as _version
from pymergetic.rxf.bridge import layout_to_container
from pymergetic.rxf.executable.artifact import extract_rxf
from pymergetic.rxf.expand import COUNTER
from pymergetic.rxf.face import (
    boot,
    build_executable,
    certify,
    certify_lane,
    compile,
    dump_cmd,
    inspect,
    mint,
    mint_from_template,
    preflight,
    replay,
    targets,
)
from pymergetic.rxf.layout import cell_map, relations
from pymergetic.rxf.output.engine import unpack_layout
from pymergetic.rxf.view import view

COMMANDS = {
    "mint": "JSON -> .rxf",
    "template": "build from class-based template (counter)",
    "inspect": "dump .rxf as JSON",
    "view": "pretty-print .rxf as tree",
    "dump": "annotated hex dump of .rxf",
    "layout": "memory cell map + relation graph",
    "replay": "unpack + repack, verify identity",
    "certify": "validate + report",
    "certify-lane": "strict certification with external tools",
    "boot": "build deterministic boot plan",
    "build": "package executable image for a canonical target",
    "compile": "compile composed Function for native targets",
    "targets": "list native runtime targets [active-target-id]",
    "preflight": "validate entry Function binding for a target",
    "serve": "start FastAPI inspector --config FILE [--host HOST] [--port PORT]",
}


USAGE = f"""pymergetic-rxf {_version}

usage: rxf <command> <path> [output]
       rxf serve --config FILE [--host HOST] [--port PORT]

commands:
""" + "\n".join(f"  {k:<10} {v}" for k, v in sorted(COMMANDS.items()))

_SERVE_USAGE = "usage: rxf serve --config FILE [--host HOST] [--port PORT]"

_BUILD_USAGE = "usage: rxf build <image.rxf> --entry <function-id> --target <id-or-name> --output <path>"


def _parse_build_arguments(arguments: list[str]) -> tuple[str, int, str, str]:
    if not arguments or arguments[0].startswith("-"):
        raise ValueError(_BUILD_USAGE)
    path = arguments[0]
    values: dict[str, str] = {}
    remaining = arguments[1:]
    while remaining:
        option = remaining.pop(0)
        if option not in {"--entry", "--target", "--output"} or not remaining:
            raise ValueError(_BUILD_USAGE)
        if option in values:
            raise ValueError(f"build: duplicate {option}\n{_BUILD_USAGE}")
        values[option] = remaining.pop(0)
    missing = [option for option in ("--entry", "--target", "--output") if option not in values]
    if missing:
        raise ValueError(f"build: {' '.join(missing)} required\n{_BUILD_USAGE}")
    try:
        entry = int(values["--entry"], 0)
    except ValueError as error:
        raise ValueError(f"build: --entry requires an integer\n{_BUILD_USAGE}") from error
    if not 0 <= entry < 1 << 64:
        raise ValueError(f"build: --entry is outside uint64 range\n{_BUILD_USAGE}")
    return path, entry, values["--target"], values["--output"]



def _parse_serve_arguments(arguments: list[str]) -> tuple[str, str, int]:
    host = "127.0.0.1"
    port = 8420
    config_path: str | None = None
    remaining = list(arguments)
    while remaining:
        option = remaining.pop(0)
        if option not in {"--config", "--host", "--port"} or not remaining:
            raise ValueError(_SERVE_USAGE)
        value = remaining.pop(0)
        if option == "--config":
            config_path = value
        elif option == "--host":
            host = _validate_host(value)
        else:
            try:
                port = int(value)
            except ValueError as error:
                raise ValueError("serve: --port requires an integer") from error
            if not 1 <= port <= 65535:
                raise ValueError("serve: --port must be between 1 and 65535")
    if config_path is None:
        raise ValueError("serve: --config FILE is required")
    return config_path, host, port


def _validate_host(value: str) -> str:
    if not value or any(
        character.isspace() or ord(character) < 32 for character in value
    ):
        raise ValueError("serve: --host must be nonempty and contain no whitespace")
    if "://" in value or "/" in value or "\\" in value:
        raise ValueError("serve: --host must be a host, not a URL or path")
    candidate = value
    if value.startswith("[") or value.endswith("]"):
        if not (value.startswith("[") and value.endswith("]")):
            raise ValueError("serve: invalid bracketed IPv6 host")
        candidate = value[1:-1]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if ":" in candidate:
            raise ValueError("serve: --host must not include a port") from None
        labels = candidate.split(".")
        if any(
            not label
            or len(label) > 63
            or label[0] == "-"
            or label[-1] == "-"
            or not all(character.isalnum() or character == "-" for character in label)
            for label in labels
        ):
            raise ValueError("serve: invalid hostname") from None
        return candidate
    return str(address)


def _serve_endpoint(host: str, port: int) -> str:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return f"rxf inspector → http://{host}:{port}"
    if address.is_unspecified:
        bind_host = f"[{host}]" if address.version == 6 else host
        return (
            f"listening on {bind_host}:{port} (use this server's IP address to connect)"
        )
    url_host = f"[{host}]" if address.version == 6 else host
    return f"rxf inspector → http://{url_host}:{port}"


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv

    if len(argv) < 2 or argv[1] in ("-h", "--help"):
        print(USAGE)
        return 0

    cmd = argv[1]
    if cmd not in COMMANDS:
        print(f"unknown command: {cmd}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 1

    if cmd == "mint":
        input_path = argv[2]
        output_path = argv[3] if len(argv) > 3 else None
        result = mint(input_path, output_path)

    elif cmd == "template":
        output_path = argv[2] if len(argv) > 2 else "counter.rxf"
        result = mint_from_template(COUNTER, output_path)

    elif cmd == "inspect":
        result = inspect(argv[2])

    elif cmd == "view":
        blob = extract_rxf(Path(argv[2]).read_bytes())
        layout = unpack_layout(blob)
        container = layout_to_container(layout)
        print(view(container))
        return 0

    elif cmd == "dump":
        print(dump_cmd(argv[2])["dump"])
        return 0

    elif cmd == "layout":
        blob = extract_rxf(Path(argv[2]).read_bytes())
        layout = unpack_layout(blob)
        container = layout_to_container(layout)
        print("── RELATIONS ──")
        print(relations(container))
        print()
        print("── CELL MAP ──")
        print(cell_map(container))
        return 0

    elif cmd == "targets":
        result = targets(argv[2], int(argv[3], 0) if len(argv) > 3 else None)

    elif cmd == "build":
        try:
            path, entry, target, output = _parse_build_arguments(argv[2:])
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        result = build_executable(path, entry, target, output)
        if result.get("ok"):
            # Detailed bindings and object addresses live in the manifest, not
            # thousands of terminal lines (and never a second hex copy of RXF).
            result = {
                key: value for key, value in result.items()
                if key not in {"layout", "plan", "selected"}
            }

    elif cmd == "boot":
        if len(argv) != 5:
            print(
                "usage: rxf boot <path> <entry-function-id> <target-id>",
                file=sys.stderr,
            )
            return 2
        result = boot(argv[2], int(argv[3], 0), int(argv[4], 0))

    elif cmd == "preflight":
        if len(argv) != 5:
            print(
                "usage: rxf preflight <path> <entry-function-id> <target-id>",
                file=sys.stderr,
            )
            return 2
        result = preflight(argv[2], int(argv[3], 0), int(argv[4], 0))

    elif cmd == "compile":
        if len(argv) != 4:
            print("usage: rxf compile <path> <entry-function-id>", file=sys.stderr)
            return 2
        result = compile(argv[2], int(argv[3], 0))

    elif cmd == "replay":
        result = replay(argv[2])

    elif cmd == "certify":
        result = certify(argv[2])

    elif cmd == "certify-lane":
        result = certify_lane(argv[2])

    elif cmd == "serve":
        import uvicorn

        from pymergetic.rxf.server.api import create_app
        from pymergetic.rxf.server.config import ServerConfig
        from pymergetic.rxf.server.state import RXFLibrary

        try:
            config_path, host, port = _parse_serve_arguments(argv[2:])
        except ValueError as error:
            print(error, file=sys.stderr)
            return 2
        try:
            config = ServerConfig.from_toml(Path(config_path).expanduser().resolve())
            library = RXFLibrary(config)
        except ValueError as error:
            print(f"serve: {error}", file=sys.stderr)
            return 1
        app = create_app(library)
        print(_serve_endpoint(host, port))
        uvicorn.run(app, host=host, port=port, log_level="info")
        return 0

    else:
        raise AssertionError(f"unhandled command: {cmd}")

    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
