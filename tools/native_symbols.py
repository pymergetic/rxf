"""Expected exported symbols for the complete RXF native numeric corpus."""

TYPES = (
    "uint8_t",
    "uint16_t",
    "uint32_t",
    "uint64_t",
    "int8_t",
    "int16_t",
    "int32_t",
    "int64_t",
    "float",
    "double",
)
INTEGER = TYPES[:8]
SIGNED = TYPES[4:8]
INTEGER_OPS = (
    "equal",
    "less",
    "minimum",
    "maximum",
    "checked_add",
    "wrapping_add",
    "saturating_add",
    "checked_subtract",
    "wrapping_subtract",
    "saturating_subtract",
    "checked_multiply",
    "wrapping_multiply",
    "saturating_multiply",
    "divide",
    "remainder",
    "bit_not",
    "bit_and",
    "bit_or",
    "bit_xor",
    "shift_left",
    "shift_right",
)
FLOAT_OPS = (
    "equal",
    "less",
    "minimum",
    "maximum",
    "checked_add",
    "checked_subtract",
    "checked_multiply",
    "divide",
    "checked_negate",
    "checked_absolute",
)
EXPECTED = {
    *(f"{op}_{name}" for name in INTEGER for op in INTEGER_OPS),
    *(
        f"{op}_{name}"
        for name in SIGNED
        for op in ("checked_negate", "checked_absolute")
    ),
    *(f"{op}_{name}" for name in TYPES[8:] for op in FLOAT_OPS),
    *(
        f"convert_{source}_to_{destination}"
        for source in TYPES
        for destination in TYPES
        if source != destination
    ),
}
assert len(EXPECTED) == 286
