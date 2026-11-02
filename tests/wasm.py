"""A wasm module of snippets under the convention (memory 0, table 0, global 0).

The composer has one home -- ``tools/native/wasm_module.py``, where the boot
build composes the wasm32 seat's function table too -- and this module keeps
the tests' import surface.
"""

from wasm_module import (
    TABLE_SIZE,
    VALUE_BYTE,
    leb,
    module_of,
    section,
    sleb,
    string,
    vec,
    wasm_type,
)

__all__ = [
    "TABLE_SIZE",
    "VALUE_BYTE",
    "leb",
    "module_of",
    "section",
    "sleb",
    "string",
    "vec",
    "wasm_type",
]
