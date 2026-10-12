"""A wasm module of snippets under the convention (memory 0, global 0)."""


def leb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def sleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if (value == 0 and not byte & 0x40) or (value == -1 and byte & 0x40):
            out.append(byte)
            return bytes(out)
        out.append(byte | 0x80)


def section(sid: int, payload: bytes) -> bytes:
    return bytes([sid]) + leb(len(payload)) + payload


def vec(items: list[bytes]) -> bytes:
    return leb(len(items)) + b"".join(items)


def string(text: str) -> bytes:
    raw = text.encode()
    return leb(len(raw)) + raw


VALUE_BYTE = {"i32": 0x7F, "i64": 0x7E, "f32": 0x7D, "f64": 0x7C}


TABLE_SIZE = 64


def module_of(
    functions: list[tuple[str, list[str], bytes]],
    call_types: list[list[str]] | None = None,
    stack: int = 1 << 16,
) -> bytes:
    """A wasm module of the snippets under the convention: memory 0, table 0, global 0.

    ``call_types`` are the parameters of the leaves a composed function calls
    through the table, in the order of their ids: the types the snippet's
    ``call_indirect`` instructions name come first, in this order.
    """
    types, index = [], {}
    for params in call_types or []:
        types.append(
            b"\x60"
            + vec([bytes([VALUE_BYTE[p]]) for p in params])
            + vec([bytes([0x7F])])
        )
        index.setdefault(tuple(params), len(types) - 1)
    for _, params, _ in functions:
        key = tuple(params)
        if key not in index:
            index[key] = len(types)
            types.append(
                b"\x60"
                + vec([bytes([VALUE_BYTE[p]]) for p in params])
                + vec([bytes([0x7F])])
            )
    body = b"\0asm\1\0\0\0"
    body += section(1, vec(types))
    body += section(
        2,
        vec(
            [
                string("env") + string("memory") + b"\x02\x00" + leb(2),
                string("env") + string("table") + b"\x01\x70\x00" + leb(TABLE_SIZE),
            ]
        ),
    )
    body += section(3, vec([leb(index[tuple(p)]) for _, p, _ in functions]))
    body += section(6, vec([b"\x7f\x01\x41" + sleb(stack) + b"\x0b"]))
    body += section(
        7, vec([string(n) + b"\x00" + leb(i) for i, (n, _, _) in enumerate(functions)])
    )
    body += section(10, vec([leb(len(code)) + code for _, _, code in functions]))
    return body
