"""align.py — alignment rules."""

import math


def align_up(val: int, align: int) -> int:
    """Round val up to the nearest multiple of align."""
    if align == 0:
        return val
    return (val + align - 1) // align * align


def natural_align(size: int) -> int:
    """Return the natural alignment for a type of `size` bytes.

    Natural alignment is min(size, 2**floor(log2(size))), clamped to max_align.
    """
    if size == 0:
        return 1
    return min(size, max_align(size))


def max_align(size: int) -> int:
    """Return the maximum alignment — clamp to 16 (max_align_t on x86-64)."""
    if size == 0:
        return 1
    p2 = 2 ** int(math.log2(size))
    return min(p2, 16)
