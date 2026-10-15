"""Differential tests of the C/C++/Rust -> RXF importer (``tools/native/rximport.py``).

Every function of ``tests/import_corpus/`` (except the ones ``test_outside_the_subset_is_refused``
names, which are outside the subset on purpose) is imported two ways and run on the same
inputs:

* through the importer: the source as textual LLVM IR (the entrance format itself for a
  raw ``.ll``, read as it stands with no compiler -- ``test_the_ll_entrance_is_read_as_is``;
  ``clang-18 -S -emit-llvm -O1`` for ``.c``, ``clang++-18`` for ``.cpp``,
  ``rustc --emit=llvm-ir -C opt-level=0 -C panic=abort`` for ``.rs``) -> the textual IR
  parsed and lowered to a ``SemanticGraph``
  delta -> ``Image.apply`` -> ``compose.programs`` -> the program words ->
  ``rxf_native.run``, the real interpreter of ``native/graph.c``;
* natively: the same source compiled by the same family of compiler into a shared library
  and called with ctypes. ``leaf.c``, ``memory.c`` and ``slots.c`` are linked with
  ``native/basics.c`` and ``native/stdlib_memory.c`` so a leaf call runs the very same C
  on both sides.

The contract of the importer: deterministic code without undefined behavior has the same
observable behavior on both sides. Each test compares ``(status, value)`` per input
against the natively compiled function (and the bytes the memory cases write, both
sides); nothing is skipped. The refused programs (recursion, a frame no run cell can hold,
the pointer-write and function-word rules, the C++ exception/RTTI runtime calls and the
values rustc keeps in memory at opt-level 0) are asserted to be refused at import, not
guessed; the panic paths of Rust are asserted to refuse the *run*
(``test_panic_paths_refuse_the_run``), one-sidedly --
the natively compiled call would abort the process on those inputs. The wall casualties of
the fixed 1024-byte frame (``wide.c``, ``qstr.c``'s qstr_from_str/qstr_from_strn) refused
there before and lower now with the variable-length frames of the run cell --
``REFUSED`` below records that history; ``test_the_interning_path_lowers_with_variable_
frames`` runs the flip end to end (a 1608-byte frame in one cell, over a genuine call card).
"""

from __future__ import annotations

import ctypes
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import compose
import graph_header
import pytest
import rximport

from pymergetic.rxf.core import tree

from . import graph_world, rxf_native
from .emulator import Blob, Emulator, Integer
from .snippets import STARTER

CORPUS = Path(__file__).parent / "import_corpus"
NATIVE = Path(__file__).parents[1] / "native"
CLANG = "clang-18"
CLANGXX = "clang++-18"
RUSTC = rximport.RUSTC  # rustc of the PATH, or ~/.cargo/bin/rustc
OK = 0
U64 = 1 << 64

VALUES = (
    0,
    1,
    2,
    3,
    5,
    100,
    0xF0F0,
    0xFF00,
    0xFFFFFFFF,
    0x100000000,
    1 << 63,
    (1 << 63) + 5,
    U64 - 2,
    U64 - 1,
)
COUNTS = (0, 1, 7, 8, 31, 32, 63)  # shift counts inside the C contract (below 64)
ROUNDS = (0, 1, 2, 3, 5, 8)  # the rounds of the loop functions stay small

PAIRS = [(a, b) for a in VALUES for b in VALUES]
SHIFTED = [(a, n) for a in VALUES for n in COUNTS]
# leaf_shift narrows the count with (uint32_t), a trunc the importer lowers: 2^32 + 1
# shifts by 1 on both sides, and 2^32 + 70 refuses on both sides (the leaf checks)
LEAF_SHIFTED = [(a, n) for a in VALUES for n in (*COUNTS, 70, (1 << 32) + 1, (1 << 32) + 70)]
TRIPLES = [(x, lo, hi) for (lo, hi) in ((10, 20), (0, U64 - 1), (5, 5)) for x in VALUES]
# calls between imported functions, and locals: fewer pairs than the leaves, the run stays
# in seconds
SPARSE = [(a, b) for a in VALUES[::2] for b in VALUES[::2]]
# a C++ bool parameter is the i1 of the ABI: only 0/1 are inside its contract (the C++
# side reads the register as true for any nonzero value, the subset keeps the low bit)
BOOLS = [(a, b, x) for a in (0, 1) for b in (0, 1) for x in VALUES[::2]]
# the narrow widths: the edges of i8/i16/i32 and the words just past them (the pass keeps a
# narrow value in the low bits of its word; inputs uncanonical for the width are part of it)
NARROW = (
    0, 1, 2, 0x7F, 0x80, 0xFF, 0x100,
    0x7FFF, 0x8000, 0xFFFF, 0x10000,
    0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x100000000,
    1 << 63, U64 - 1,
)  # fmt: skip
NARROW_PAIRS = [(a, b) for a in NARROW[::2] for b in NARROW[::2]]
NARROW_VALUES = [(a,) for a in NARROW]
# the shift counts inside each width (at or past it is undefined in C, outside the contract)
COUNTS_8 = (0, 1, 3, 7)
COUNTS_16 = (0, 1, 7, 15)
COUNTS_32 = (0, 1, 7, 15, 31)

# the signed words of the small-int paths (py/smallint.c and its 32-bit port): both kinds
# of sign at the edges of the type and around zero. The words pass as u64 and the ABI
# truncates them to the parameter both sides read
SIGNED = (
    0, 1, U64 - 1, 2, U64 - 2, 3, U64 - 3, 100, U64 - 100,
    0x7FFFFFFF, 0x80000000, 1 << 63, (1 << 63) - 1, (1 << 63) + 1,
)  # fmt: skip
SIGNED_32 = (
    0, 1, 0xFFFFFFFF, 2, 0xFFFFFFFE, 3, 0xFFFFFFFD, 100, 0xFFFFFF9C,
    0x7FFFFFFF, 0x80000000,
)  # fmt: skip


def signed(word: int, bits: int = 64) -> int:
    """The two's complement reading of a word of this many bits, as Python's int."""
    word &= (1 << bits) - 1
    return word - (1 << bits) if word >> (bits - 1) else word


def modulo_defined(dividend: int, divisor: int, bits: int = 64) -> bool:
    """The inputs the C of mp_small_int_modulo defines: the truncating remainder has no
    answer for a zero divisor and for INT_MIN % -1 -- natively a SIGFPE, not a value the
    differential could compare against (the leaves refuse those instead of miscounting)."""
    low = -(1 << (bits - 1))
    dividend, divisor = signed(dividend, bits), signed(divisor, bits)
    return divisor != 0 and not (dividend == low and divisor == -1)


def floor_divide_defined(num: int, denom: int, bits: int = 64) -> bool:
    """The inputs the C of mp_small_int_floor_divide defines: its own divides (above) and
    the adjustments ``num += -denom - 1`` and ``num += -denom + 1``, whose signed
    overflow is undefined in C (and wraps on the graph's wrapping leaves)."""
    low, high = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    num, denom = signed(num, bits), signed(denom, bits)
    if not modulo_defined(num, denom, bits):
        return False
    if denom == low and num >= 0:  # -denom is no number of the type
        return False
    if num >= 0 and denom < 0:
        return num - denom - 1 <= high
    if num < 0 and denom >= 0:
        return num - denom + 1 >= low
    return True


MOD_PAIRS = [(a, b) for a in SIGNED for b in SIGNED if modulo_defined(a, b)]
FLOOR_PAIRS = [(a, b) for a in SIGNED for b in SIGNED if floor_divide_defined(a, b)]
MOD32_PAIRS = [(a, b) for a in SIGNED_32 for b in SIGNED_32 if modulo_defined(a, b, 32)]
FLOOR32_PAIRS = [(a, b) for a in SIGNED_32 for b in SIGNED_32 if floor_divide_defined(a, b, 32)]
# the checked multiply runs every pair: the checks themselves bound every division of the
# body (CERT INT32-C), so there is no input the C leaves undefined
MUL_PAIRS = [(a, b) for a in SIGNED for b in SIGNED]
DIVMOD_PAIRS = [(a, b) for a in SIGNED_32 for b in SIGNED_32 if signed(b, 32) != 0]


# the corpus: function -> (the source file it lives in, the inputs both sides run on)
CASES: dict[str, tuple[str, list[tuple[int, ...]]]] = {
    "wrapping_add": ("arith.c", PAIRS),
    "wrapping_sub": ("arith.c", PAIRS),
    "bit_and": ("arith.c", PAIRS),
    "bit_or": ("arith.c", PAIRS),
    "bit_not": ("arith.c", [(a,) for a in VALUES]),
    "xor_mix": ("arith.c", PAIRS),
    "shift_left": ("arith.c", SHIFTED),
    "shift_right": ("arith.c", SHIFTED),
    "cmp_eq": ("compare.c", PAIRS),
    "cmp_ne": ("compare.c", PAIRS),
    "cmp_ult": ("compare.c", PAIRS),
    "cmp_uge": ("compare.c", PAIRS),
    "cmp_slt": ("compare.c", PAIRS),
    "cmp_sle": ("compare.c", PAIRS),
    "abs_diff": ("branch.c", PAIRS),
    "clamp64": ("branch.c", TRIPLES),
    "min64": ("branch.c", PAIRS),
    "max64": ("branch.c", PAIRS),
    "signed_min": ("branch.c", PAIRS),
    "signed_max": ("branch.c", PAIRS),
    "sum_bits": ("loop.c", [(n, a) for n in ROUNDS for a in VALUES]),
    "count_ones": ("loop.c", [(x,) for x in VALUES]),
    "leaf_and": ("leaf.c", PAIRS),
    "leaf_add": ("leaf.c", PAIRS),
    "leaf_shift": ("leaf.c", LEAF_SHIFTED),
    "leaf_mix": ("leaf.c", PAIRS),
    # the integer leaves of the flat profile (native/graph.h ids 39-45) called straight
    # from the corpus: every input runs -- a leaf that refuses (a checked multiply past
    # the type, a division by zero, INT64_MIN / -1) leaves the zeroed out slot behind on
    # both sides, a zero to compare rather than a wrong number
    "leaf_mul": ("leaf.c", PAIRS),
    "leaf_mulu": ("leaf.c", PAIRS),
    "leaf_muls": ("leaf.c", PAIRS),
    "leaf_div": ("leaf.c", PAIRS),
    "leaf_rem": ("leaf.c", PAIRS),
    "leaf_divs": ("leaf.c", PAIRS),
    "leaf_rems": ("leaf.c", PAIRS),
    # calls between imported functions, inlined into the caller's graph
    "blend": ("calls.c", SPARSE),
    "nudge": ("calls.c", [(a,) for a in VALUES[::2]]),
    "chain": ("calls.c", SPARSE),
    "twice": ("calls.c", [(a,) for a in VALUES[::2]]),
    # clang turns its tail call into a loop at -O1: it imports like any other loop
    "count_down": ("calls.c", [(n,) for n in ROUNDS]),
    # locals: slots of values, and one local whose address reaches a memory leaf
    "vol_round": ("slots.c", SPARSE),
    "local_round": ("slots.c", SPARSE),
    "bump": ("slots.c", [(a,) for a in VALUES[::2]]),
    # a fixed local array: GEPs with constant indices into an address-taken frame slot
    "local_arr": ("memory.c", [(a,) for a in VALUES[::2]]),
    # the narrow widths: i8/i16/i32 arithmetic, compares, casts and a narrow loop
    "add8": ("narrow.c", NARROW_PAIRS),
    "sub16": ("narrow.c", NARROW_PAIRS),
    "add32": ("narrow.c", NARROW_PAIRS),
    "and8": ("narrow.c", NARROW_PAIRS),
    "or16": ("narrow.c", NARROW_PAIRS),
    "xor32": ("narrow.c", NARROW_PAIRS),
    "shl8": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_8]),
    "lshr16": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_16]),
    "ashr32": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_32]),
    "ashr8": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_8]),
    "sext8": ("narrow.c", NARROW_VALUES),
    "sext16": ("narrow.c", NARROW_VALUES),
    "sext32": ("narrow.c", NARROW_VALUES),
    "trunc32": ("narrow.c", NARROW_PAIRS),
    "mix": ("narrow.c", NARROW_PAIRS),
    "ult8": ("narrow.c", NARROW_PAIRS),
    "slt8": ("narrow.c", NARROW_PAIRS),
    "sge16": ("narrow.c", NARROW_PAIRS),
    "ugt32": ("narrow.c", NARROW_PAIRS),
    "sle32": ("narrow.c", NARROW_PAIRS),
    "eq8": ("narrow.c", NARROW_PAIRS),
    "lfsr8": ("narrow.c", [(n, a) for n in ROUNDS for a in NARROW[::2]]),
    # the funnel shifts: llvm.fshl.i8 and llvm.fshr.i8
    "rotl8": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_8]),
    "rotr8": ("narrow.c", [(a, n) for a in NARROW[::2] for n in COUNTS_8]),
    # Rust (rustc --emit=llvm-ir -C opt-level=0 -C panic=abort): the integers of the flat
    # profile as #[no_mangle] pub extern "C" functions, plain SSA of the arguments
    "r_add8": ("rust_int.rs", NARROW_PAIRS),
    "r_sub16": ("rust_int.rs", NARROW_PAIRS),
    "r_add32": ("rust_int.rs", NARROW_PAIRS),
    "r_xor64": ("rust_int.rs", PAIRS),
    "r_shl8": ("rust_int.rs", [(a, n) for a in NARROW[::2] for n in (0, 1, 3, 7, 0xFF, 0x100000007)]),
    "r_ashr32": ("rust_int.rs", [(a, n) for a in NARROW[::2] for n in (0, 1, 7, 15, 31, 0xFFFFFFFF)]),
    "r_ult8": ("rust_int.rs", NARROW_PAIRS),
    "r_slt32": ("rust_int.rs", NARROW_PAIRS),
    "r_zext_mix": ("rust_int.rs", NARROW_PAIRS),
    # Rust overflowing arithmetic: the { i64, i1 } built by insertvalue and read by
    # extractvalue (the add/sub + compare shape of llvm.*.with.overflow)
    "r_uadd_ovf": ("rust_overflow.rs", PAIRS),
    "r_usub_ovf": ("rust_overflow.rs", PAIRS),
    "r_sadd_ovf": ("rust_overflow.rs", PAIRS),
    "r_ssub_ovf": ("rust_overflow.rs", PAIRS),
    # a Rust panic path: the same answers as the native code on the inputs that do not
    # panic, and a Refuse of the run at zero (AT_RUN_REFUSED below)
    "r_panic_if_zero": ("rust_panic.rs", [(x,) for x in VALUES if x]),
    # __builtin_*_overflow of C: the llvm.*.with.overflow intrinsics and their extractvalue
    "add_ovf_u64": ("overflow.c", PAIRS),
    "sub_ovf_u64": ("overflow.c", PAIRS),
    "add_ovf_i32": ("overflow.c", NARROW_PAIRS),
    "sub_ovf_i32": ("overflow.c", NARROW_PAIRS),
    "add_ovf_i64": ("overflow.c", PAIRS),
    "sub_ovf_i64": ("overflow.c", PAIRS),
    # the multiply forms: one wrapping leaf and the overflow bit of the identity, every
    # pair comparable -- the overflowing ones where __builtin_mul_overflow answers 1 are
    # exactly the interesting ones
    "mul_ovf_u64": ("overflow.c", PAIRS),
    "mul_ovf_i64": ("overflow.c", PAIRS),
    "mul_ovf_i32": ("overflow.c", NARROW_PAIRS),
    # C++ (clang++ -O1): a bool parameter is the i1 the ABI zero-extends (the corpus
    # feeds 0/1 for the two bools of this one)
    "bool_pick": ("cpp_refs.cpp", BOOLS),
    # the MicroPython recon shapes (the v1 lane spike): inttoptr constant-expression
    # operands (REPR A immediate objects), pointer compares, void bodies, the
    # out-parameter idiom, the i32 switch of a VM dispatch, and lines the IR writer wraps
    "pass14": ("ptrconst.c", [(a,) for a in VALUES[::2]]),
    "pass30": ("ptrconst.c", [(a,) for a in VALUES[::2]]),
    "pick1430": ("ptrconst.c", [(a,) for a in VALUES]),
    "ptr_same": ("ptrcmp.c", SPARSE),
    "ptr_diff": ("ptrcmp.c", SPARSE),
    "ptr_null": ("ptrcmp.c", [(a,) for a in VALUES]),
    "ptr_is14": ("ptrcmp.c", [(a,) for a in VALUES]),
    "v_call_nothing": ("voids.c", [(a,) for a in VALUES[::2]]),
    # the noreturn calls answer as the native code on every other input; the panicking
    # inputs are run one-sidedly below (AT_RUN_REFUSED)
    "guarded": ("noreturn.c", [(a,) for a in VALUES if a]),
    "asserted": ("noreturn.c", [(a,) for a in VALUES if a != 7]),
    # the i32 switch: the case tags mask to the width of the value (a case of -1 is the
    # word 0xffffffff of the subset's narrow values, and -2000 is 0xfffff830)
    "classify": ("swtch.c", NARROW_VALUES),
    "sum_pair": ("outparam.c", [(a,) for a in VALUES[::2]]),
    "ten": ("wrapcall.c", [(1, 2, 3, 4, 5, 6, 7, 8, 9, 10), (0, 1, 2, 3, 4, 5, 6, 7, 8, 9)]),
    "ten_sum": ("wrapcall.c", [(a,) for a in VALUES[::2]]),
    "vol_const": ("volconst.c", [(a,) for a in VALUES[::2]]),
    # the first wall casualty of the fixed 1024-byte frame, flipped (REFUSED's history):
    # six inlined bodies of ~30 values each make a 1264-byte frame, refused by
    # RXF_GRAPH_FRAME's wall before and lowered now that frames are variable-length
    # (base, size) links of the run cell (RXF_GRAPH_CELL_BYTES, 8192). Every inline
    # stays an inline (each is under INLINE_WORDS): what flips is the whole frame
    # running where the wall said no -- run here against the natively compiled wide.c
    "wide": ("wide.c", [(x,) for x in VALUES[::2]]),
    # the unsigned order of two address words (mp_binary_op's range check of an
    # immediate-object word): less_uint64_t on the words, like the integer compares
    "ptr_ult": ("ptrord.c", SPARSE),
    "ptr_uge": ("ptrord.c", SPARSE),
    "ptr_ule": ("ptrord.c", SPARSE),
    "ptr_ugt": ("ptrord.c", SPARSE),
    "ptr_between": ("ptrord.c", SPARSE),
    # one phi entry per edge: several cases of a switch reach one block, the phi names
    # the same predecessor once per case, and every repeat carries the same value
    "dupedge": ("dupedge.c", [(x, v) for x in (0, 1, 2, 3, 4, 100) for v in VALUES[::4]]),
    # the nlr ABI of MicroPython (py/nlr.h): the frame is pushed, the work runs, and the
    # raise path jumps out (AT_RUN_REFUSED below). The safe path computes on both sides;
    # the second return of the setjmp is dead in the graph (nlr_push answers 0 there)
    "nlr_guarded": ("nlr_call.c", [(x,) for x in VALUES if x != 0]),
    # the multiply of C: one wrapping leaf kept in the low bits (squared.c was the
    # importer's first refused arithmetic; slice 1 wires the leaf in)
    "squared": ("squared.c", [(a,) for a in VALUES]),
    # py/smallint.c of MicroPython end to end: the modulo that takes the sign of the
    # second operand (srem), the floor division that adjusts before one truncating divide
    # (sdiv), and the 32-bit port's own -- the i32 leaves of native/graph.h (ids 47-48).
    # The pairs keep to the inputs the C defines (modulo_defined, floor_divide_defined)
    "mp_small_int_modulo": ("smallint.c", MOD_PAIRS),
    "mp_small_int_floor_divide": ("smallint.c", FLOOR_PAIRS),
    "mp_small_int_modulo32": ("smallint.c", MOD32_PAIRS),
    "mp_small_int_floor_divide32": ("smallint.c", FLOOR32_PAIRS),
}

# the flat memory cases: which parameters are addresses of memory (the run's flat space on
# one side, a ctypes buffer on the other), and what bytes each address starts with
MEMORY_CASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "copy8": ("memory.c", ("ptr", "ptr", "value")),
    "set8": ("memory.c", ("ptr", "value")),
    "store64": ("memory.c", ("ptr", "value")),
    "load64": ("memory.c", ("ptr",)),
    "same8": ("memory.c", ("ptr", "ptr")),
    # the narrow widths through flat memory: only the bytes of the type are written/read
    "store8": ("memory.c", ("ptr", "value")),
    "store16": ("memory.c", ("ptr", "value")),
    "store32": ("memory.c", ("ptr", "value")),
    "load8": ("memory.c", ("ptr",)),
    "load16": ("memory.c", ("ptr",)),
    "load32": ("memory.c", ("ptr",)),
    # pointer arithmetic (getelementptr): a bounded variable index, scaled by the element
    "idx8": ("memory.c", ("ptr", "value")),
    "idx32": ("memory.c", ("ptr", "value")),
    "idx8_put": ("memory.c", ("ptr", "value", "value")),
    # struct field access: two constant-offset GEPs and their loads
    "pair_mix": ("memory.c", ("ptr",)),
    "pair_pick": ("memory.c", ("ptr", "value")),
    # a select or phi of addresses, then a load/store through the chosen pointer
    "pick_load": ("memory.c", ("ptr", "value")),
    "pick_store": ("memory.c", ("ptr", "value", "value")),
    "walk": ("memory.c", ("ptr", "value")),
    # a pointer round-tripped through an integer: ptrtoint then inttoptr
    "addr_roundtrip": ("memory.c", ("ptr", "value")),
    # memcpy / memset: the llvm.memcpy and llvm.memset intrinsics reach the memory leaves
    "mcopy1": ("memory.c", ("ptr", "ptr")),
    "mcopy4": ("memory.c", ("ptr", "ptr")),
    "mcopyn": ("memory.c", ("ptr", "ptr", "value")),
    "mfill4": ("memory.c", ("ptr", "value")),
    # Rust through flat memory: raw pointer parameters (the null and alignment guards
    # rustc emits lower to the run's Refuse; the addresses here never take them)
    "r_store8": ("rust_mem.rs", ("ptr", "value")),
    "r_load32": ("rust_mem.rs", ("ptr",)),
    "r_idx8": ("rust_mem.rs", ("ptr", "value", "value")),
    # C++: a reference is the address of its object; a method is its this pointer
    "ref_add": ("cpp_refs.cpp", ("ptr", "value")),
    "counter_bump": ("cpp_refs.cpp", ("ptr", "value")),
    "_ZN7Counter4bumpEm": ("cpp_refs.cpp", ("ptr", "value")),
    # a void body through the memory profile: v_wrap runs v_store (a void callee that
    # writes through the out-parameter of the caller's wide local) and adds one
    "v_wrap": ("voids.c", ("ptr", "value")),
}

# the memory profile where both values vary (the harness above repeats one value in every
# value parameter of a case): the checked multiply of the small-int path, whose product
# goes out through its pointer and whose flag is the answer, and the u32 divmod whose
# quotient goes out the same way
PAIR_MEMORY_CASES: dict[str, tuple[str, list[tuple[int, ...]]]] = {
    "mp_small_int_multiply": ("smallint.c", MUL_PAIRS),
    "mp_small_int_divmod_u32": ("smallint.c", DIVMOD_PAIRS),
}
FILLS = (
    bytes(8),
    b"\xff" * 8,
    b"\x01\x23\x45\x67\x89\xab\xcd\xef",
    b"\xef\xcd\xab\x89\x67\x45\x23\x01",
    b"\x00\x00\x00\x00\x00\x00\x00\x01",
)
VALUES_FOR_MEMORY = (0, 1, 0x42, 0xFF, 0x0123456789ABCDEF, U64 - 1, 1 << 63)

# what the importer refuses at import time (a message, not a guess): mutual recursion, a
# frame no run cell can hold, the C++ ABI shapes and runtimes, and what rustc keeps in
# memory at opt-level 0.
#
# The frame wall's casualties, REFUSED -> lowered with variable frames (the frames of the
# run cell are variable-length (base, size) links now, RXF_GRAPH_CELL_BYTES = 8192 where
# RXF_GRAPH_FRAME's wall was 1024). These three entries left this table; each one now
# lowers and runs (history kept here and in docs/20, never blurred):
#   ("wide.c", "wide"): "frame"
#       -> lowered (CASES["wide"]): a 1264-byte frame of six inlined bodies, run
#          against the natively compiled wide.c
#   ("qstr.c", "qstr_from_str"): "needs a frame"
#       -> lowered with a genuine CALL card naming qstr_from_strn (the inline budget
#          refuses to inline in favor of one, not instead of it)
#   ("qstr.c", "qstr_from_strn"): "a frame of 1608 bytes is too large"
#       -> lowered: the 1608-byte body (clang -O1 keeps the interning's qstr_add path
#          inside it) is a program of its own whose frame one run cell holds
# Both qstr flips run end to end in
# test_the_interning_path_lowers_with_variable_frames.
REFUSED = {
    ("calls.c", "ping"): "recursion",
    ("calls.c", "pong"): "recursion",
    # a local whose join lives in memory (volatile keeps clang from promoting it)
    ("memory.c", "slot_join"): "carried through memory",
    # a body with no Return and a cycle the entry reaches (an endless loop answers
    # nothing; a body whose every path panics imports instead, AT_RUN_REFUSED below)
    ("refusebody.c", "never_answers"): "no return",
    # C++: the ABI shapes (a by-value aggregate, an indirect call) and the runtimes
    ("cpp_refuse.cpp", "big_make"): "sret",
    ("cpp_refuse.cpp", "big_take"): "byval",
    ("cpp_refuse.cpp", "cpp_throw"): "exception",
    ("cpp_refuse.cpp", "cpp_catch"): "exception",
    # a C++ virtual call: the vtable's words live in the constant initializer of a
    # global (a relocation the flat profile refuses to place), so no store of the
    # import wires them and the call through the vtable word is refused with the
    # function-word rule of lower_indirect (its slot dispatch is the same one the
    # unary_op route uses -- where a boot wires the words, it routes)
    ("cpp_refuse.cpp", "vcall"): "function word",
    # the function-word rule itself as a pin: a call through a word the run carries
    # where no store of the import wires one (dispatch.c has no boot to fill a slot):
    # the card model names its callee when the words are made, so the body refuses
    ("dispatch.c", "call_a_word"): "function word",
    # the null check of the cast lowers as a word compare now; the cast refuses at the
    # __dynamic_cast runtime call itself (its virtual call is an indirect one after it)
    ("cpp_refuse.cpp", "dyn"): "dynamic_cast",
    ("cpp_refuse.cpp", "rtti_call"): "RTTI",
    # Rust: the shapes opt-level 0 keeps in memory
    ("rust_refuse.rs", "r_join"): "carried through memory",
    ("rust_refuse.rs", "r_array"): "getelementptr of the local",
    # globals: a cell this pass cannot place (its initializer is a relocation: the bytes
    # would be a link-time address) and a declaration without bytes
    ("global_reloc.c", "use_reloc"): "relocation",
    ("global_reloc.c", "use_decl"): "no payload",
    # the nlr policy's soundness boundary: the setjmp buffer the frame alone keeps alive
    # is opaque, and a body that reads or writes its bytes is refused, never guessed
    ("nlr_call.c", "nlr_abuse_read"): "nlr buffer",
    ("nlr_call.c", "nlr_abuse_write"): "nlr buffer",
    # the interning path of MicroPython (qstr_from_str -> qstr_from_strn -> the helper):
    # REFUSED -> lowered with variable frames (the flip history above): the frame wall
    # was all that kept these two out -- clang -O1 keeps the interning's qstr_add path
    # inside qstr_from_strn itself (186 instructions -> 195 frame values, 1608 bytes --
    # 1560 even packed) and the wall was RXF_GRAPH_FRAME's 1024. What never changed: the
    # call of qstr_find_strn is a call card (the inline budget refuses to inline in
    # favor of one, not instead of it), and the core the gate needs (qstr_compute_hash,
    # qstr_find_strn, qstr_hash and the accessors of find_qstr) imports and runs below.
    # The two flip with the run cell's variable frames and run end to end in
    # test_the_interning_path_lowers_with_variable_frames.
    # the obj class of py/obj.c: the type -> name path (mp_obj_get_type_str -> the
    # qstr corpus's qstr_str) imports now -- its call of qstr_str is a call card (the
    # cross-file shape of the slice), proven end to end on the host runner in
    # test_the_type_name_path_reaches_the_qstr_program. What stays refused is the
    # runtime-dispatch class where its words are not made by the same import: the slot
    # dispatch of slices 4 routes on the words obj_port_boot stores into the @mp_type_*
    # cells (test_the_unary_slot_dispatch_routes_to_the_type_body imports that boot and
    # the bodies together), and mp_obj_is_true imported here alone has no such store --
    # the precise rule of lower_indirect (a call through a function word this import
    # makes no body for). mp_obj_is_true carries its own slot read. mp_obj_get_int_maybe
    # calls mp_unary_op, whose body this corpus now carries (the unary route of slice 4),
    # and that call inlines the route into the caller's blocks -- so the first thing this
    # import cannot name is the same dispatch. The pointer-write rule holds for that body
    # too (a write through the caller's out cell lands in a call frame's copy: refused in
    # lower() where a body both writes one and makes a card call) but the inlined dispatch
    # is asked first
    ("obj.c", "mp_obj_is_true"): "function word",
    ("obj.c", "mp_obj_get_int_maybe"): "function word",
}

# the panic paths: the run refuses (RXF_REFUSE_UNSUPPORTED) on the path that would panic
# instead of unwinding (the status-branch idiom). The graph is run on the panicking input
# alone -- the natively compiled call would abort the process there, so there is nothing
# to compare against (the differential above covers the inputs that do not panic)
AT_RUN_REFUSED: dict[str, tuple[str, list[tuple[int, ...]]]] = {
    "r_panic_if_zero": ("rust_panic.rs", [(0,)]),
    # the Refuse-only body: every path panics and there is no Return of its own, so every
    # run of it refuses (r_always_panics, always_booms -- the shape of every mp_raise_*)
    "r_always_panics": ("rust_panic.rs", [(0,)]),
    "always_booms": ("refusebody.c", [(0,), (7,)]),
    "r_idx8": ("rust_mem.rs", [(0, 0, 0)]),  # the null guard of a raw pointer dereference
    # the diverging call of C: the variadic noreturn my_raise and the assert's fail
    "guarded": ("noreturn.c", [(0,)]),
    "asserted": ("noreturn.c", [(7,)]),
    # the nlr ABI of MicroPython: the raise path of the guard idiom jumps out of the
    # frame, and the nlr_jump itself is the graph's own ending (a Refuse-only body, like
    # always_booms above: every mp_raise_* definition is this shape)
    "nlr_guarded": ("nlr_call.c", [(0,)]),
    "nlr_boom": ("nlr_call.c", [(0,), (7,)]),
}

# globals as arena cells: one corpus file, the name of its cell, and the sequence of calls
# that walks the cell across calls. The differential's native side keeps the same static
# storage (the shared library's), the graph side the same arena cell: the answers of the
# sequence compare the state evolution of both sides, not one call alone
GLOBAL_CASES: dict[str, tuple[str, tuple[tuple[str, tuple[int, ...]], ...]]] = {
    "global_scalar.c": (
        "counter",
        (
            ("counter_get", ()),  # the initial payload the cell starts with
            ("counter_add", (5,)),
            ("counter_add", (3,)),  # the state survives between the calls
            ("counter_get", ()),
            ("counter_set", (100,)),
            ("counter_get", ()),
        ),
    ),
    "global_struct.c": (
        "ctx",
        (
            ("ctx_top", ()),  # a constant field offset: a constant-expression getelementptr
            ("ctx_bump", (3, 10)),  # a moving index: a getelementptr instruction
            ("ctx_bump", (3, 1)),
            ("ctx_read", (3,)),
            ("ctx_top", ()),  # the field walks the same cell across the calls
            ("ctx_limit", ()),
            ("ctx_read", (0,)),
        ),
    ),
}

# the cell's address as a word: what a run resolves the Object handle to. The two sides
# map the cell somewhere of their own, so the shape is what matches, not the word
GLOBAL_ADDRESSES: dict[tuple[str, str], str] = {
    ("global_scalar.c", "counter_at"): "counter",
}

# the @mp_type_* cells, the ROM objects and the arena of the obj corpus: the Object
# handles their graphs resolve, and the targets obj_port_boot stores.  These live in
# the obj world (obj_world below), not the shared one: the class's cells are past the
# 120 bytes the starter's and qstr's cells leave there
OBJ_ADDRESSES: dict[tuple[str, str], str] = {
    ("obj.c", "obj_type_type_at"): "mp_type_type",
    ("obj.c", "obj_type_int_at"): "mp_type_int",
    ("obj.c", "obj_type_str_at"): "mp_type_str",
    ("obj.c", "obj_type_bool_at"): "mp_type_bool",
    ("obj.c", "obj_type_nonetype_at"): "mp_type_NoneType",
    ("obj.c", "obj_none_at"): "mp_const_none_obj",
    ("obj.c", "obj_false_at"): "mp_const_false_obj",
    ("obj.c", "obj_true_at"): "mp_const_true_obj",
    ("obj.c", "obj_arena_at"): "m_arena",
}

# the qstr core of MicroPython (py/qstr.c over the corpus's arena port): the pool cells
# the world keeps across the calls are the statics the native library keeps between its
# ctypes calls, and the string a case searches is bytes of the world's flat memory at
# the address it passes. The gate of the slice is the hash/find roundtrip: what
# qstr_find_strn answers, the stored hash qstr_hash reads back, and the hash
# qstr_compute_hash makes of the bytes agree -- with each other and with the natively
# compiled library (makeqstrdata's contract: every stored hash is the computed hash of
# its string)
QSTR_PROGRAMS = (
    "qstr_port_boot",
    "qstr_init",
    "qstr_compute_hash",
    "qstr_find_strn",
    "qstr_hash",
    # the accessors of find_qstr: the callers of the core that import. The interning
    # path (qstr_from_str -> qstr_from_strn, REFUSED -> lowered with variable frames) is
    # NOT in this room: its arena cell (m_arena, the qstr_add chunk) outgrows the
    # emulator world's payload room (graph_world.memory's fixed layout, the CellRun
    # contract of one room per world), so its flip is pinned on the ground-truth host
    # seat -- test_the_interning_path_lowers_with_variable_frames
    "qstr_len",
    "qstr_str",
    "qstr_data",
)
QSTR_STRINGS = (
    b"",
    b"a",
    b"ab",
    b"print",
    b"len",
    b"hello",
    b"import",
    b"micropython",
    b"qstr",
    # the near misses the searches must not confuse: prefixes, extensions, an embedded
    # NUL (the compares are length-bounded, not NUL-terminated), and strings no pool holds
    b"b",
    b"prin",
    b"prints",
    b"hell",
    b"qstr\x00x",
    b"micropytho",
    b"zzzzzzzz",
)
QSTR_CASES: dict[str, tuple[str, ...]] = {"qstr.c": QSTR_PROGRAMS}

# the obj class of MicroPython (py/obj.c over the corpus's arena port): the @mp_type_*
# cells the world keeps across the calls are the statics the native library keeps
# between its ctypes calls.  The gate of the slice is the ctor/type-test roundtrip:
# the type cells are arena cells whose byte images carry the literal words and whose
# one relocation word obj_port_boot stores (the boot rule of qstr.c's pools, one file
# over), and then the ctor stamps a type and the type test reads it back -- on the
# tagged immediates of REPR A, on the ROM objects of objbool.c/objnone.c, and on the
# type cells themselves (every type is its own object whose header names
# mp_type_type)
OBJ_PROGRAMS = (
    "obj_port_boot",
    "mp_obj_malloc_helper",
    "mp_obj_get_type",
    "obj_type_type_at",
    "obj_type_int_at",
    "obj_type_str_at",
    "obj_type_bool_at",
    "obj_type_nonetype_at",
    "obj_none_at",
    "obj_false_at",
    "obj_true_at",
    "obj_arena_at",
)
OBJ_CASES: dict[str, tuple[str, ...]] = {"obj.c": OBJ_PROGRAMS}


@dataclass
class World:
    image: object
    programs: dict[str, bytes]  # the words compose made, keyed by the path of the Function
    native: dict[str, ctypes.CDLL]  # the corpus compiled natively, by the name of the file


# a corpus file that only declares an ABI whose machine lives elsewhere: MicroPython keeps
# the nlr ABI of py/nlr.h in its own translation unit (nlr_posix.c), so the differential
# links its own stubs (nlr_stubs.c) the same way -- the IR the importer reads keeps every
# call opaque, exactly as MicroPython's own build does
FRIENDS: dict[str, tuple[str, ...]] = {
    "nlr_call.c": ("nlr_stubs.c",),
    "obj.c": ("obj_stubs.c",),
}


def link(file: Path, shared: Path) -> None:
    """The corpus file and the leaves it calls, as one shared library: the compiler of the
    suffix (clang for C, clang++ for C++, rustc for Rust)."""
    if file.suffix == ".rs":
        subprocess.run(
            [RUSTC, "--crate-type=cdylib", "-C", "opt-level=0", "-C", "panic=abort",
             str(file), "-o", str(shared)],
            check=True,
        )
        return
    compiler = CLANGXX if file.suffix == ".cpp" else CLANG
    subprocess.run(
        [compiler, "-O1", "-shared", "-fPIC", str(file),
         *(str(CORPUS / friend) for friend in FRIENDS.get(file.name, ())),
         str(NATIVE / "basics.c"), str(NATIVE / "stdlib_memory.c"),
         "-o", str(shared)],
        check=True,
    )  # fmt: skip


# behind the world's memory: the room a sequence's programs are placed in (the world's
# own program slot is 0x1800 bytes, an imported body is bigger)
PROGRAM_ROOM = 0x40000


class CellRun:
    """One world of the emulator seat with the arena cells of a corpus file's globals.

    A graph names a cell by handle (an operand of kind Object), and the run resolves the
    handle to the cell's payload address in its object table. The host runner of
    tests/host/runner.c holds one object in that table (the program itself), so a program
    that names a cell cannot run there yet -- it would answer RXF_REFUSE_UNBOUND. The
    emulator seat's world carries the full table (tests/graph_world.py lays it out the way
    the starter's harness does: the cell in the arena before the run, exactly like a
    request struct), and one world stays up across every call of a sequence: the cell is
    the C static the differential's native binary keeps between its calls."""

    def __init__(self, image, programs: dict[str, bytes]) -> None:
        """``programs`` are the composed programs of one corpus file, by function name."""
        code = graph_world.code_of(image)
        objects = graph_world.bindings(image)
        self.programs = programs
        self.emulator = Emulator("x86_64")
        self.emulator.map(graph_world.BASE, graph_world.MAPPED + PROGRAM_ROOM)
        addresses = {
            index: self.emulator.load(data) for index, data in code.leaves["x86_64"].items()
        }
        case = graph_world.Case("cells", b"", b"", objects)
        self.emulator.write(
            graph_world.BASE, graph_world.memory(case, sorted(addresses.items()))
        )
        self.run_code = self.emulator.load(code.run["x86_64"])
        self.handle = case.handle
        # a run reads the words of its program out of the payload of its handle's object.
        # The starter's world leaves the program 0x1800 bytes before the arguments and
        # writes the arguments and the out box over whatever follows on every call: fine
        # for the starter's programs, but an imported body (a caller with its callees
        # inlined) is tens of KiB and its words would be the arguments' bytes by the time
        # it runs. The entry's payload moves to a room of its own behind the world, the
        # way the harness of the world hands a run its request struct, and the entry's
        # payload and size are patched when the sequence moves on to the next program.
        self.program_at = graph_world.BASE + graph_world.MAPPED
        count = struct.unpack_from(
            "<Q", self.emulator.read(graph_world.CONTEXT, 144), 32
        )[0]
        table = self.emulator.read(graph_world.OBJECTS, count * 80)
        self.program_entry = graph_world.OBJECTS
        for index in range(count):
            if struct.unpack_from("<Q", table, index * 80)[0] == graph_world.PROGRAM_ID:
                self.program_entry = graph_world.OBJECTS + index * 80
        self.emulator.write(
            self.program_entry + 24, struct.pack("<Q", self.program_at)
        )
        self.placed: bytes | None = None

    def cell_size(self, address: int) -> int:
        """The size of the cell whose payload sits at ``address`` -- the object table's
        own word, so a case that bounds a walk of a cell (the arena's extent) reads the
        one definition of it (the C array's length, as the world placed the cell)."""
        count = struct.unpack_from(
            "<Q", self.emulator.read(graph_world.CONTEXT, 144), 32
        )[0]
        table = self.emulator.read(graph_world.OBJECTS, count * 80)
        for index in range(count):
            if struct.unpack_from("<Q", table, index * 80 + 24)[0] == address:
                return struct.unpack_from("<Q", table, index * 80 + 32)[0]
        raise AssertionError(f"{address:#x}: no cell of the world answers that payload")

    def call(self, name: str, args: tuple[int, ...]) -> tuple[int, int]:
        """One call of the sequence: (status, the answer word). The world, its cells
        included, stays as the last call left it."""
        program = self.programs[name]
        if program is not self.placed:
            self.emulator.write(self.program_at, program)
            self.emulator.write(
                self.program_entry + 32, struct.pack("<Q", len(program))
            )
            self.placed = program
        self.emulator.write(
            graph_world.ARGS, struct.pack(f"<{len(args)}Q", *args) if args else b""
        )
        self.emulator.write(graph_world.OUT, bytes(graph_world.OUT_BYTES))
        status = self.emulator.call(
            self.run_code,
            [
                Integer(graph_world.CONTEXT),
                Blob(struct.pack("<3Q", *self.handle)),
                Integer(graph_world.ARGS),
                Integer(len(args)),
                Integer(graph_world.OUT),
            ],
        )
        return status, int.from_bytes(self.emulator.read(graph_world.OUT, 8), "little")


def build_world(build: Path, homes: set[str]) -> World:
    """One world of the imported corpus: the files ``homes`` names, every wanted body
    imported into one image and linked natively, ``image.check()`` holding the deltas
    together.  A world is one room of cells: the shared one carries the corpus but
    obj.c, whose class takes payload room past what the starter's and qstr's cells
    leave (120 bytes), so the obj class builds a world of its own -- the CellRun
    contract of one world carrying one corpus file's cells."""
    image = tree.load(STARTER)
    native: dict[str, ctypes.CDLL] = {}
    for file in sorted(homes):
        source = CORPUS / file
        stem = source.stem
        wanted = (
            {name for name, (home, _) in CASES.items() if home == file}
            | {name for name, (home, _) in MEMORY_CASES.items() if home == file}
            | {name for name, (home, _) in PAIR_MEMORY_CASES.items() if home == file}
            | {name for name, (home, _) in AT_RUN_REFUSED.items() if home == file}
            | {name for name, _ in GLOBAL_CASES.get(file, ("", ()))[1]}
            | {name for home, name in GLOBAL_ADDRESSES if home == file}
            | {name for home, name in OBJ_ADDRESSES if home == file}
            | set(QSTR_CASES.get(file, ()))
            | set(OBJ_CASES.get(file, ()))
        )
        imported = rximport.import_source_file(image, source, module=stem, only=wanted)
        assert set(imported) == wanted, f"{file}: imported {sorted(imported)}, not {sorted(wanted)}"
        shared = build / f"{stem}.so"
        link(source, shared)
        native[stem] = ctypes.CDLL(str(shared))
    assert image.check() == []  # the deltas of the importer hold together
    return World(image, compose.programs(image), native)  # compose consumes the imported graphs


CORPUS_HOMES = (
    {home for home, _ in CASES.values()}
    | {home for home, _ in MEMORY_CASES.values()}
    | {home for home, _ in PAIR_MEMORY_CASES.values()}
    | {home for home, _ in AT_RUN_REFUSED.values()}
    | set(GLOBAL_CASES)
    | set(QSTR_CASES)
    | set(OBJ_CASES)
)
OBJ_HOMES = set(OBJ_CASES)


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    return build_world(tmp_path_factory.mktemp("import_corpus"), CORPUS_HOMES - OBJ_HOMES)


@pytest.fixture(scope="module")
def obj_world(tmp_path_factory) -> World:
    """The obj class's world: its cells are past the room the shared world's cells leave
    (120 bytes), so obj.c imports into a world of its own -- one world, one corpus
    file's cells, the CellRun contract."""
    return build_world(tmp_path_factory.mktemp("import_obj"), OBJ_HOMES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_imported_matches_natively_compiled(world: World, name: str):
    file, inputs = CASES[name]
    key = f"{Path(file).stem}.{name}"
    assert key in world.programs, f"compose made no program for the imported {key}"
    program = world.programs[key]
    function = getattr(world.native[Path(file).stem], name)
    arity = len(inputs[0])
    function.argtypes = [ctypes.c_uint64] * arity
    function.restype = ctypes.c_uint64
    with rxf_native.Memory(1 << 16) as memory:
        for case in inputs:
            expected = function(*case)
            status, value = rxf_native.run(program, struct.pack(f"<{arity}Q", *case), memory)
            assert (status, value) == (OK, expected), (
                f"{name}{case}: the imported graph answered (status, value) = "
                f"({status}, {value:#x}), natively it is ({OK}, {expected:#x})"
            )


@pytest.mark.parametrize("name", sorted(MEMORY_CASES))
def test_memory_matches_natively_compiled(world: World, name: str):
    """The flat memory profile: the same bytes written, on both sides."""
    file, kinds = MEMORY_CASES[name]
    key = f"{Path(file).stem}.{name}"
    program = world.programs[key]
    function = getattr(world.native[Path(file).stem], name)
    function.argtypes = [ctypes.c_uint64] * len(kinds)
    function.restype = ctypes.c_uint64
    values = VALUES_FOR_MEMORY if "value" in kinds else (0,)
    for first, second in ((bytes(8), b"\xff" * 8), (FILLS[2], FILLS[2]), (FILLS[3], FILLS[2])):
        for value in values:
            pointers = kinds.count("ptr")
            buffers = [bytearray(first), *(bytearray(second) for _ in range(pointers - 1))]
            native_buffers = [ctypes.create_string_buffer(bytes(b), 8) for b in buffers]
            arguments: list[int] = []
            native_arguments: list[int] = []
            with rxf_native.Memory(1 << 16) as memory:
                taken = 0
                for kind in kinds:
                    if kind == "ptr":
                        region = 0x200 + 0x80 * taken
                        memory.write(region, bytes(buffers[taken]))
                        arguments.append(memory.at(region))
                        native_arguments.append(ctypes.addressof(native_buffers[taken]))
                        taken += 1
                    else:
                        arguments.append(value)
                        native_arguments.append(value)
                expected = function(*native_arguments)
                status, answer = rxf_native.run(
                    program, struct.pack(f"<{len(kinds)}Q", *arguments), memory
                )
                assert (status, answer) == (OK, expected), (
                    f"{name}{tuple(arguments)}: the imported graph answered "
                    f"({status}, {answer:#x}), natively it is ({OK}, {expected:#x})"
                )
                for index in range(pointers):
                    wrote = bytes(memory.read(0x200 + 0x80 * index, 8))
                    native_wrote = ctypes.string_at(ctypes.addressof(native_buffers[index]), 8)
                    assert wrote == native_wrote, (
                        f"{name}: memory {index} is {wrote.hex()} after the graph and "
                        f"{native_wrote.hex()} after the natively compiled call"
                    )


@pytest.mark.parametrize("name", sorted(PAIR_MEMORY_CASES))
def test_pair_memory_matches_natively_compiled(world: World, name: str):
    """The memory profile where both values vary: the checked multiply of the small-int
    path and the u32 divmod write their product (or quotient) through a pointer and
    answer the flag (or the remainder). The bytes the pointer writes and the answer
    compare on both sides, every pair of the case's own list."""
    file, inputs = PAIR_MEMORY_CASES[name]
    key = f"{Path(file).stem}.{name}"
    program = world.programs[key]
    function = getattr(world.native[Path(file).stem], name)
    arity = len(inputs[0])  # the values, then the pointer the result is written through
    function.argtypes = [ctypes.c_uint64] * arity + [ctypes.c_void_p]
    function.restype = ctypes.c_uint64
    for case in inputs:
        native_buffer = ctypes.create_string_buffer(bytes(8), 8)
        expected = function(*case, ctypes.addressof(native_buffer))
        with rxf_native.Memory(1 << 16) as memory:
            memory.write(0x200, bytes(8))
            status, answer = rxf_native.run(
                program, struct.pack(f"<{arity + 1}Q", *case, memory.at(0x200)), memory
            )
            wrote = bytes(memory.read(0x200, 8))
        assert (status, answer) == (OK, expected), (
            f"{name}{case}: the imported graph answered (status, value) = "
            f"({status}, {answer:#x}), natively it is ({OK}, {expected:#x})"
        )
        native_wrote = ctypes.string_at(ctypes.addressof(native_buffer), 8)
        assert wrote == native_wrote, (
            f"{name}{case}: memory is {wrote.hex()} after the graph and "
            f"{native_wrote.hex()} after the natively compiled call"
        )


@pytest.mark.parametrize("name", sorted(AT_RUN_REFUSED))
def test_panic_paths_refuse_the_run(world: World, name: str):
    """A path that would panic refuses the run (the status-branch idiom): the panic call
    of the rustc output is lowered to nothing and the unreachable after it to the Refuse
    of RXF_REFUSE_UNSUPPORTED. One-sided on purpose: the natively compiled call would
    abort the process on these inputs."""
    file, inputs = AT_RUN_REFUSED[name]
    program = world.programs[f"{Path(file).stem}.{name}"]
    for case in inputs:
        with rxf_native.Memory(1 << 16) as memory:
            status, _ = rxf_native.run(program, struct.pack(f"<{len(case)}Q", *case), memory)
        assert status == rximport.UNSUPPORTED_STATUS, (
            f"{name}{case}: the panic path answered status {status}, not the Refuse "
            f"({rximport.UNSUPPORTED_STATUS}) of the subset"
        )


@pytest.mark.parametrize(("file", "name"), sorted(REFUSED))
def test_outside_the_subset_is_refused(file: str, name: str):
    """The importer refuses what it cannot lower rather than guessing."""
    text = rximport.compile_source(CORPUS / file)
    with pytest.raises(rximport.Unsupported, match=REFUSED[(file, name)]):
        rximport.graphs_of(text, only={name})


@pytest.mark.parametrize("file", sorted(GLOBAL_CASES))
def test_globals_are_arena_cells(world: World, file: str):
    """A module global is one named cell of the arena: the graph's loads and stores of the
    static are flat loads and stores at the cell's payload address, and the cell survives
    between the calls of the sequence exactly like the C static the native binary keeps.
    The sequence compares the state evolution of both sides -- the initial payload the
    cell starts with, and the walks of a wide struct's fields (a moving index and a
    constant field offset alike)."""
    cell, steps = GLOBAL_CASES[file]
    stem = Path(file).stem
    run = CellRun(world.image, {name: world.programs[f"{stem}.{name}"] for name, _ in steps})
    for name, args in steps:
        function = getattr(world.native[stem], name)
        function.argtypes = [ctypes.c_uint64] * len(args)
        function.restype = ctypes.c_uint64
        expected = function(*args)
        status, value = run.call(name, args)
        assert (status, value) == (OK, expected), (
            f"{name}{args}: the imported graph answered (status, value) = "
            f"({status}, {value:#x}) with the {cell} cell it kept, natively the same "
            f"call answers ({OK}, {expected:#x}) with its static"
        )


def check_global_address(image, programs: dict[str, bytes], stem: str, name: str) -> None:
    """What ``&static`` answers: the payload address the Object handle resolves to. The
    two sides map the cell somewhere of their own, so the word itself cannot compare: its
    shape can -- the same address at every call, aligned, inside the arena."""
    run = CellRun(image, {name: programs[f"{stem}.{name}"]})
    status, first = run.call(name, ())
    again, second = run.call(name, ())
    assert (status, again) == (OK, OK)
    assert first == second, f"{name}: two calls answered {first:#x} and {second:#x}"
    assert first and first % 8 == 0, f"{name}: {first:#x} is no aligned address"
    assert graph_world.BASE <= first < graph_world.BASE + graph_world.MAPPED


@pytest.mark.parametrize(("file", "name"), sorted(GLOBAL_ADDRESSES))
def test_global_address_is_the_cell(world: World, file: str, name: str):
    check_global_address(world.image, world.programs, Path(file).stem, name)


@pytest.mark.parametrize(("file", "name"), sorted(OBJ_ADDRESSES))
def test_obj_global_address_is_the_cell(obj_world: World, file: str, name: str):
    """The obj class's cells answer as the other files' statics do -- in the class's own
    world, whose room the shared world's cells leave short."""
    check_global_address(obj_world.image, obj_world.programs, Path(file).stem, name)


def test_qstr_hash_find_roundtrip_matches_native(world: World):
    """The qstr core of MicroPython end to end (the gate of the slice): the imported
    graphs answer the same as the natively compiled library. The sequence boots the pool
    cells the way a port's boot fills the pointers a static initializer would carry as
    relocations (qstr_port_boot), starts the interning state (qstr_init), and then every
    string of QSTR_STRINGS runs the roundtrip: the hash of the bytes, the qstr the pools
    answer for them, and the hash that qstr carries back -- the three agree with each
    other (makeqstrdata's contract) and with the native side's statics on the same
    bytes. The near misses stay absent on both sides: the searches are length-bounded
    compares, not NUL-terminated ones. A found qstr then walks the accessors of
    find_qstr (qstr_len, qstr_str, qstr_data): the numbers compare directly, the
    addresses cannot (each side maps its cells somewhere of its own, the shape of
    test_global_address_is_the_cell), so the strings they name compare by content."""
    stem = "qstr"
    run = CellRun(
        world.image, {name: world.programs[f"{stem}.{name}"] for name in QSTR_PROGRAMS}
    )
    native = world.native[stem]
    native.qstr_port_boot()
    native.qstr_init()
    for name, argtypes in (
        ("qstr_compute_hash", [ctypes.c_void_p, ctypes.c_uint64]),
        ("qstr_find_strn", [ctypes.c_void_p, ctypes.c_uint64]),
        ("qstr_hash", [ctypes.c_uint64]),
        ("qstr_len", [ctypes.c_uint64]),
    ):
        function = getattr(native, name)
        function.argtypes = argtypes
        function.restype = ctypes.c_uint64
    for name, argtypes in (
        ("qstr_str", [ctypes.c_uint64]),
        ("qstr_data", [ctypes.c_uint64, ctypes.POINTER(ctypes.c_uint64)]),
    ):
        function = getattr(native, name)
        function.argtypes = argtypes
        function.restype = ctypes.c_void_p
    scratch = graph_world.HEAP + 0x800  # the bytes of the world the cases search
    lengths_at = scratch + 0x40  # the out word of qstr_data, one word past them
    # the same boot on the emulator seat: the pools' pointer fields are the addresses
    # qstr_port_boot stores before qstr_init starts the interning state -- one world of
    # cells across the sequence, exactly like the native library's statics
    for boot in ("qstr_port_boot", "qstr_init"):
        status, _ = run.call(boot, ())
        assert status == OK, f"{boot}: the imported graph refused the boot ({status})"
    for text in QSTR_STRINGS:
        raw = text + b"\0"
        run.emulator.write(scratch, raw)
        held = ctypes.create_string_buffer(raw)  # alive through the native calls below
        address = ctypes.addressof(held)
        computed = native.qstr_compute_hash(address, len(text))
        status, value = run.call("qstr_compute_hash", (scratch, len(text)))
        assert (status, value) == (OK, computed), (
            f"qstr_compute_hash({text!r}): the imported graph answered "
            f"({status}, {value:#x}), natively it is ({OK}, {computed:#x})"
        )
        found = native.qstr_find_strn(address, len(text))
        status, answer = run.call("qstr_find_strn", (scratch, len(text)))
        assert (status, answer) == (OK, found), (
            f"qstr_find_strn({text!r}): the imported graph answered "
            f"({status}, {answer:#x}), natively it is ({OK}, {found:#x})"
        )
        if found:
            stored = native.qstr_hash(found)
            status, read = run.call("qstr_hash", (answer,))
            assert (status, read) == (OK, stored), (
                f"qstr_hash({text!r}): the imported graph answered "
                f"({status}, {read:#x}), natively it is ({OK}, {stored:#x})"
            )
            assert stored == computed, (
                f"{text!r}: the pool stores hash {stored} but qstr_compute_hash "
                f"answers {computed} -- makeqstrdata's contract is one hash of the bytes"
            )
            expected_len = native.qstr_len(found)
            status, length = run.call("qstr_len", (answer,))
            assert (status, length) == (OK, expected_len), (
                f"qstr_len({text!r}): the imported graph answered "
                f"({status}, {length}), natively it is ({OK}, {expected_len})"
            )
            assert expected_len == len(text), (
                f"{text!r}: qstr_len answers {expected_len}, the search was of "
                f"{len(text)} bytes"
            )
            expected_str = ctypes.string_at(native.qstr_str(found), expected_len)
            status, pointed = run.call("qstr_str", (answer,))
            assert status == OK, f"qstr_str({text!r}): the graph refused ({status})"
            assert bytes(run.emulator.read(pointed, expected_len)) == expected_str == text, (
                f"qstr_str({text!r}): the graph names {bytes(run.emulator.read(pointed, expected_len))!r} "
                f"at {pointed:#x}, natively the cell holds {expected_str!r}"
            )
            written = ctypes.c_uint64()
            expected_data = ctypes.string_at(
                native.qstr_data(found, ctypes.byref(written)), written.value
            )
            run.emulator.write(lengths_at, bytes(8))
            status, pointed = run.call("qstr_data", (answer, lengths_at))
            assert status == OK, f"qstr_data({text!r}): the graph refused ({status})"
            length = int.from_bytes(run.emulator.read(lengths_at, 8), "little")
            assert (length, bytes(run.emulator.read(pointed, length))) == (
                written.value,
                expected_data,
            ), (
                f"qstr_data({text!r}): the graph answered {length} bytes of "
                f"{bytes(run.emulator.read(pointed, length))!r}, natively it is "
                f"{written.value} bytes of {expected_data!r}"
            )


def test_obj_ctor_type_roundtrip_matches_native(obj_world: World):
    """The obj class of MicroPython end to end (the gate of the slice): the imported
    graphs answer the same as the natively compiled library.  The sequence boots the
    @mp_type_* cells the way a port's boot fills the relocation words a static
    initializer would carry (obj_port_boot, the boot rule of qstr.c's pools), and then
    the smallest real call path runs: the constructor (mp_obj_malloc_helper) stamps a
    type and the type test (mp_obj_get_type) reads it back.  Before the boot the cells
    answer their byte images (the relocation word is the zero the flat profile
    places); after it the type-of-type fixed point holds (every type is its own
    object whose header names mp_type_type).  The tagged words of REPR A dispatch to
    the type cells without touching memory, and the ROM objects of
    objbool.c/objnone.c dispatch on their headers like the constructor's objects do.

    The two sides map their cells somewhere of their own, so a type word cannot
    compare across them: what compares is the relation (each answer names the cell
    the case expects -- the same relation asserted on both sides), the content of the
    cells the answers name (the relocation word aside, whose own words relate), and
    the arena offset of every constructor answer -- the bump of the port's heap is
    deterministic, so those words compare directly."""
    stem = "obj"
    run = CellRun(
        obj_world.image, {name: obj_world.programs[f"{stem}.{name}"] for name in OBJ_PROGRAMS}
    )
    native = obj_world.native[stem]

    def graph_at(name: str) -> int:
        status, value = run.call(name, ())
        assert status == OK, f"{name}: the imported graph refused ({status})"
        return value

    def native_at(name: str) -> int:
        function = getattr(native, name)
        function.restype = ctypes.c_void_p
        return function() or 0

    def check_type(
        label: str, graph_word: int, native_word: int, graph_cell: int, native_cell: int
    ) -> None:
        """The type test on one shape of word, each side its own cell addresses: the
        answer must name the cell the case expects, and the same relation holds or
        breaks together on both sides (that agreement is the differential)."""
        status, answer = run.call("mp_obj_get_type", (graph_word,))
        native.mp_obj_get_type.argtypes = [ctypes.c_void_p]
        native.mp_obj_get_type.restype = ctypes.c_void_p
        expected = native.mp_obj_get_type(native_word) or 0
        assert (status, answer == graph_cell) == (OK, expected == native_cell) == (
            OK,
            True,
        ), (
            f"mp_obj_get_type({label}): the imported graph answered ({status}, "
            f"{answer:#x}) and natively it is ({OK}, {expected:#x}); the case expects "
            f"the {graph_cell:#x} cell on the graph side and the {native_cell:#x} "
            f"cell natively"
        )

    def check_content(label: str, graph_cell: int, native_cell: int, size: int) -> None:
        """The bytes of a cell the answers name: identical across the sides but for
        the relocation word at offset 0, which is an address of the cell's own side
        (the callers compare it against that side's type address)."""
        assert bytes(run.emulator.read(graph_cell + 8, size - 8)) == ctypes.string_at(
            native_cell + 8, size - 8
        ), f"{label}: the cell's bytes past its relocation word differ across the sides"

    def check_reloc(label: str, graph_cell: int, native_cell: int, target: str) -> None:
        """The relocation word obj_port_boot stores: each side's answer for the cell
        it points at -- one relation, two address spaces."""
        word = int.from_bytes(run.emulator.read(graph_cell, 8), "little")
        held = ctypes.string_at(native_cell, 8)
        expect = int.from_bytes(held, "little")
        graph_target = graph_at(target)
        native_target = native_at(target)
        assert word == graph_target and expect == native_target, (
            f"{label}: the boot stored {word:#x} (graph) and {expect:#x} (native), "
            f"the {target} cells are at {graph_target:#x} and {native_target:#x}"
        )

    cells = (
        ("type", "obj_type_type_at"),
        ("int", "obj_type_int_at"),
        ("str", "obj_type_str_at"),
        ("bool", "obj_type_bool_at"),
        ("NoneType", "obj_type_nonetype_at"),
    )
    roms = (
        # the cell bytes compared past the relocation word: None's base alone, the
        # bools' value (the padding C leaves beside it compares nothing)
        ("none", "obj_none_at", "obj_type_nonetype_at", 8),
        ("false", "obj_false_at", "obj_type_bool_at", 9),
        ("true", "obj_true_at", "obj_type_bool_at", 9),
    )
    at = {name: (graph_at(name), native_at(name)) for _, name in cells}
    at.update({name: (graph_at(name), native_at(name)) for _, name, _, _ in roms})
    at["obj_arena_at"] = (graph_at("obj_arena_at"), native_at("obj_arena_at"))
    graph_type, native_type = at["obj_type_type_at"]
    graph_arena, native_arena = at["obj_arena_at"]
    arena_bytes = run.cell_size(graph_arena)  # the C array's length, one definition

    # before the boot the cells answer their byte images: the relocation word is the
    # zero the flat profile places, and both sides answer it as a word
    for label, name in cells:
        graph_cell, native_cell = at[name]
        check_type(f"&mp_type_{label} before the boot", graph_cell, native_cell, 0, 0)
    for label, name, _, _ in roms:
        graph_cell, native_cell = at[name]
        check_type(f"&mp_const_{label}_obj before the boot", graph_cell, native_cell, 0, 0)

    status, _ = run.call("obj_port_boot", ())
    assert status == OK, f"obj_port_boot: the imported graph refused the boot ({status})"
    native.obj_port_boot()

    # the type-of-type fixed point: every type is its own object and its header names
    # mp_type_type -- including mp_type_type itself
    for label, name in cells:
        graph_cell, native_cell = at[name]
        check_type(f"&mp_type_{label}", graph_cell, native_cell, graph_type, native_type)
        check_reloc(f"mp_type_{label}", graph_cell, native_cell, "obj_type_type_at")
        check_content(f"mp_type_{label}", graph_cell, native_cell, 24)
    for label, name, typed, size in roms:
        graph_cell, native_cell = at[name]
        graph_typed, native_typed = at[typed]
        check_type(f"&mp_const_{label}_obj", graph_cell, native_cell, graph_typed, native_typed)
        check_reloc(f"mp_const_{label}_obj", graph_cell, native_cell, typed)
        check_content(f"mp_const_{label}_obj", graph_cell, native_cell, size)

    # the constructor/type-test roundtrip: the ctor stamps the type, the test reads it
    # back; the bump of the port's heap is deterministic, so the arena offsets of the
    # two sides agree as words
    for label, name in cells:
        graph_cell, native_cell = at[name]
        status, made = run.call("mp_obj_malloc_helper", (24, graph_cell))
        native.mp_obj_malloc_helper.argtypes = [ctypes.c_uint64, ctypes.c_void_p]
        native.mp_obj_malloc_helper.restype = ctypes.c_void_p
        held = native.mp_obj_malloc_helper(24, native_cell) or 0
        assert (status, (made - graph_arena) % U64) == (OK, (held - native_arena) % U64), (
            f"mp_obj_malloc_helper(24, &mp_type_{label}): the imported graph answered "
            f"({status}, offset {(made - graph_arena) % U64}), natively it is "
            f"({OK}, offset {(held - native_arena) % U64})"
        )
        for word, where in ((made, "graph"), (held, "native")):
            base = graph_arena if where == "graph" else native_arena
            assert word % 16 == 0 and base <= word < base + arena_bytes, (
                f"mp_obj_malloc_helper(24, &mp_type_{label}): the {where} object "
                f"{word:#x} is no aligned word of the arena at {base:#x}"
            )
        check_type(f"mp_obj_malloc_helper(24, &mp_type_{label})", made, held, graph_cell, native_cell)
        # the stamped header: the object's word is the type cell's address
        stamped = int.from_bytes(run.emulator.read(made, 8), "little")
        native_stamped = int.from_bytes(ctypes.string_at(held, 8), "little")
        assert stamped == graph_cell and native_stamped == native_cell, (
            f"mp_obj_malloc_helper(24, &mp_type_{label}): the stamped header is "
            f"{stamped:#x} (graph) and {native_stamped:#x} (native), the type cells "
            f"are at {graph_cell:#x} and {native_cell:#x}"
        )

    # the tagged words of REPR A: the dispatch touches no memory, and the words are
    # the same on both sides (bit 0 set is a small int, the qstr tag is x << 3 | 2)
    graph_int, native_int = at["obj_type_int_at"]
    graph_str, native_str = at["obj_type_str_at"]
    for value in (0, 1, 5, -3, (1 << 62) - 1):
        check_type(
            f"MP_OBJ_NEW_SMALL_INT({value})",
            (value << 1 | 1) % U64,
            (value << 1 | 1) % U64,
            graph_int,
            native_int,
        )
    for qst in (0, 1, 7, 41):
        check_type(
            f"MP_OBJ_NEW_QSTR({qst})",
            (qst << 3 | 2) % U64,
            (qst << 3 | 2) % U64,
            graph_str,
            native_str,
        )


def test_the_type_name_path_reaches_the_qstr_program(tmp_path: Path):
    """The cross-file call of the type -> name path end to end on the host runner (the
    ground truth seat of ``tests/host/runner.c``: ``rxf_graph_run`` with the callee
    registered in its object table), the same sequence of the natively compiled corpora
    beside it. mp_obj_malloc_helper stamps a type and mp_obj_get_type_str gets that
    object's type name back -- over the call card of ``native/graph.h`` (RXF_GRAPH_CALL)
    that names the qstr corpus's qstr_str as a program of its own. This is the flip
    REFUSED used to record for mp_obj_get_type_str (its call of qstr_str refused the
    import); with the call card the call goes.

    The two frozen corpora carry different qstr enums on purpose (obj.c's comment: its
    enum stands in for genhdr's and nothing of the class reads it), so the name of the
    type mp_type_int is qstr 2, and the qstr corpus's pool answers its entry 2 for it:
    the strings both sides answer compare by content, the addresses cannot (each side
    keeps its cells somewhere of its own -- test_global_address_is_the_cell)."""
    image = tree.load(STARTER)
    known = rximport.import_into(
        image, rximport.compile_source(CORPUS / "qstr.c"), module="qstr",
        only={"qstr_port_boot", "qstr_init", "qstr_str"},
    )  # fmt: skip
    rximport.import_into(
        image, rximport.compile_source(CORPUS / "obj.c"), module="obj",
        only={"obj_port_boot", "mp_obj_malloc_helper", "mp_obj_get_type_str"},
        known=known,
    )  # fmt: skip
    assert image.check() == []
    programs = compose.programs(image)
    callee = "qstr.qstr_str"
    callee_handle = image.resolve(callee)
    assert callee_handle is not None, callee
    callee_id = compose.object_id(image, callee_handle)

    # the cells the running graphs name (the pools and strings of the qstr corpus, the
    # type cells and the arena of obj.c) as live bytearrays: the boot calls store the
    # addresses of the cells into them and the later calls read what was stored
    running = (
        "qstr.qstr_port_boot", "qstr.qstr_init", callee,
        "obj.obj_port_boot", "obj.mp_obj_malloc_helper", "obj.mp_obj_get_type_str",
    )  # fmt: skip
    composer = compose.Composer(image)
    cells: dict[str, tuple[int, bytearray]] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
            continue
        path = image.path(handle)
        if not any(path.startswith(f"{name}.") for name in running):
            continue
        cells[path.rsplit(".", 1)[-1][len("global_") :]] = (
            int(values["object_id"]),
            bytearray(bytes.fromhex(values.get("data", ""))),
        )
    objects: dict[int, bytes | bytearray] = {identity: data for identity, data in cells.values()}
    objects[callee_id] = programs[callee]  # the callee of the call card, by its id

    def address_of(cell: str) -> int:
        data = cells[cell][1]
        return ctypes.addressof((ctypes.c_uint8 * len(data)).from_buffer(data))

    with rxf_native.Memory(1 << 16) as memory:

        def call(name: str, words: tuple[int, ...]) -> int:
            status, value = rxf_native.run(
                programs[name],
                struct.pack(f"<{len(words)}Q", *words),
                memory,
                objects=objects,
            )
            assert status == OK, f"{name}: the host run refused ({status})"
            return value

        for boot in ("qstr.qstr_port_boot", "qstr.qstr_init", "obj.obj_port_boot"):
            call(boot, ())
        made = call("obj.mp_obj_malloc_helper", (24, address_of("mp_type_int")))
        name_at = call("obj.mp_obj_get_type_str", (made,))

    # the same sequence natively, in one library of the two corpora (obj.c linked with
    # qstr.c: its qstr_str the real one, not the abort stub of obj_stubs.c) -- the
    # ground truth of what the type name is
    shared = tmp_path / "libqstrobj.so"
    subprocess.run(
        [CLANG, "-O1", "-shared", "-fPIC",
         # the two corpora each carry their own bounded-arena port (m_malloc_maybe and
         # m_malloc_fail over their own static m_arena), and obj_stubs.c stubs qstr_str
         # to abort where the real qstr corpus defines it. qstr.c comes first so its
         # qstr_str is the definition the linker keeps (the type name path needs the
         # real one); the identical arena helpers may be one definition here -- the
         # path only needs obj's ctor to bump once and the qstr pool to answer
         "-Wl,--allow-multiple-definition",
         str(CORPUS / "qstr.c"), str(CORPUS / "obj.c"), str(CORPUS / "obj_stubs.c"),
         str(NATIVE / "basics.c"), str(NATIVE / "stdlib_memory.c"), "-o", str(shared)],
        check=True,
    )  # fmt: skip
    native = ctypes.CDLL(str(shared))
    native.qstr_port_boot()
    native.qstr_init()
    native.obj_port_boot()
    native.mp_obj_malloc_helper.argtypes = [ctypes.c_uint64, ctypes.c_void_p]
    native.mp_obj_malloc_helper.restype = ctypes.c_void_p
    native.mp_obj_get_type_str.argtypes = [ctypes.c_void_p]
    native.mp_obj_get_type_str.restype = ctypes.c_void_p
    native.obj_type_int_at.restype = ctypes.c_void_p
    type_cell = native.obj_type_int_at() or 0
    held = native.mp_obj_malloc_helper(24, type_cell) or 0
    native_at = native.mp_obj_get_type_str(held) or 0

    expect = ctypes.string_at(native_at)
    assert expect == b"a", (
        f"the name of mp_type_int is qstr 2 and the pool of the qstr corpus answers "
        f"{expect!r} for it (its entry 2)"
    )
    assert ctypes.string_at(name_at) == expect, (
        f"the host run of the call card answered {ctypes.string_at(name_at)!r} for the "
        f"type name and natively it is {expect!r}"
    )


def test_the_unary_slot_dispatch_routes_to_the_type_body(tmp_path: Path):
    """The unary_op slot dispatch end to end on the host runner (the ground truth seat
    of ``tests/host/runner.c``), the same sequence of the natively compiled corpora
    beside it.  obj_port_boot wires the words of the per-type unary bodies into the
    slots of the @mp_type_* cells, and mp_unary_op's call through the slot dispatches
    on those words -- the route of ``lower_indirect``: one call card per wired word,
    each naming the body whose word the slot carries.  The cases walk every route: the
    fast paths of the immediate words (the small ints), the per-type bodies behind the
    slots (bool_unary_op of the bool cells, mp_obj_int_unary_op of an int object), the
    str-or-bytes hash fast path over a qstr word (which calls the qstr corpus's
    qstr_hash across the call card), and mp_obj_len_maybe's own slot call.

    The two sides keep their cells somewhere of their own, so a case names the answer
    both ways (the graph's cell word and the native one); a small int is the same word
    on both sides."""
    image = tree.load(STARTER)
    known = rximport.import_into(
        image, rximport.compile_source(CORPUS / "qstr.c"), module="qstr",
        only={"qstr_port_boot", "qstr_init", "qstr_str", "qstr_hash", "qstr_compute_hash",
              "qstr_data", "qstr_len"},
    )  # fmt: skip
    rximport.import_into(
        image, rximport.compile_source(CORPUS / "obj.c"), module="obj",
        only={"obj_port_boot", "mp_obj_malloc_helper", "mp_obj_is_true", "mp_unary_op",
              "mp_obj_int_unary_op", "bool_unary_op", "uni_unary_op", "mp_obj_len_maybe",
              "mp_obj_new_int", "obj_type_int_at", "obj_true_at", "obj_false_at"},
        known=known,
    )  # fmt: skip
    assert image.check() == []
    programs = compose.programs(image)

    # the cells the running graphs name (the type cells, the ROM objects, the arena and
    # the qstr pools) as live bytearrays, and every program of the image by its object
    # id -- the call cards of the dispatch name their bodies that way
    composer = compose.Composer(image)
    cells: dict[str, tuple[int, bytearray]] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
            continue
        path = image.path(handle)
        name = path.rsplit(".", 1)[-1]
        if name.startswith("global_"):
            cells[name[len("global_") :]] = (
                int(values["object_id"]),
                bytearray(bytes.fromhex(values.get("data", ""))),
            )
    objects: dict[int, bytes | bytearray] = {identity: data for identity, data in cells.values()}
    # the programs the call cards name, by their object id (a leaf of the interpreter
    # needs no binding): the bodies the unary_op slots dispatch to and the qstr face
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "SemanticOperation":
            continue
        callee = composer.fields(handle).get("callee")
        target = image.resolve(callee) if callee else None
        if target is not None and image.path(target) in programs:
            objects[compose.object_id(image, target)] = programs[image.path(target)]

    def address_of(cell: str) -> int:
        data = cells[cell][1]
        return ctypes.addressof((ctypes.c_uint8 * len(data)).from_buffer(data))

    with rxf_native.Memory(1 << 16) as memory:

        def call(name: str, words: tuple[int, ...]) -> int:
            status, value = rxf_native.run(
                programs[name],
                struct.pack(f"<{len(words)}Q", *words),
                memory,
                objects=objects,
            )
            assert status == OK, f"{name}: the host run refused ({status})"
            return value

        for boot in ("qstr.qstr_port_boot", "qstr.qstr_init", "obj.obj_port_boot"):
            call(boot, ())
        graph_true = call("obj.obj_true_at", ())
        graph_false = call("obj.obj_false_at", ())
        graph_int_cell = call("obj.obj_type_int_at", ())
        graph_made = call("obj.mp_obj_malloc_helper", (24, graph_int_cell))

        # the same sequence natively, in one library of the two corpora (obj.c linked
        # with qstr.c: its qstr_hash the real one, not the abort stub of obj_stubs.c)
        shared = tmp_path / "libqstrobj.so"
        subprocess.run(
            [CLANG, "-O1", "-shared", "-fPIC", "-Wl,--allow-multiple-definition",
             str(CORPUS / "qstr.c"), str(CORPUS / "obj.c"), str(CORPUS / "obj_stubs.c"),
             str(NATIVE / "basics.c"), str(NATIVE / "stdlib_memory.c"), "-o", str(shared)],
            check=True,
        )  # fmt: skip
        native = ctypes.CDLL(str(shared))
        native.qstr_port_boot()
        native.qstr_init()
        native.obj_port_boot()
        for routine, arguments, answer in (
            ("mp_unary_op", (ctypes.c_int, ctypes.c_uint64), ctypes.c_uint64),
            ("mp_obj_len_maybe", (ctypes.c_uint64,), ctypes.c_uint64),
            ("mp_obj_malloc_helper", (ctypes.c_uint64, ctypes.c_void_p), ctypes.c_void_p),
            ("qstr_hash", (ctypes.c_uint64,), ctypes.c_uint64),
        ):
            getattr(native, routine).argtypes = list(arguments)
            getattr(native, routine).restype = answer
        for routine in ("obj_type_int_at", "obj_true_at", "obj_false_at"):
            getattr(native, routine).restype = ctypes.c_void_p
        native_true = native.obj_true_at() or 0
        native_false = native.obj_false_at() or 0
        native_held = native.mp_obj_malloc_helper(24, native.obj_type_int_at() or 0) or 0

        def small(value: int) -> int:
            return (value << 1 | 1) % U64

        # the op words of py/runtime0.h (LEN is no small-int op -- its route is the
        # length face of mp_obj_len_maybe, walked at the end of this test)
        positive, negative, invert, not_, boolean, _length, hashing, absolute, int_maybe = range(9)
        qstr_word = (2 << 3) | 2  # MP_OBJ_NEW_QSTR(2): the qstr word both sides hash
        qstr_hashed = small(native.qstr_hash(2))
        # every case: (label, the op, the argument on both sides, the answer on both sides)
        cases = (
            ("MP_UNARY_OP_POSITIVE of -2 is the word itself", positive,
             (small(-2), small(-2)), (small(-2), small(-2))),
            ("MP_UNARY_OP_NEGATIVE of 5", negative, (small(5), small(5)), (small(-5), small(-5))),
            ("MP_UNARY_OP_INVERT of 5", invert, (small(5), small(5)), (small(~5), small(~5))),
            ("MP_UNARY_OP_NOT of 0 is the true cell", not_, (small(0), small(0)), (graph_true, native_true)),
            ("MP_UNARY_OP_NOT of 3 is the false cell", not_, (small(3), small(3)), (graph_false, native_false)),
            ("MP_UNARY_OP_BOOL of 0 is the false cell", boolean, (small(0), small(0)), (graph_false, native_false)),
            ("MP_UNARY_OP_BOOL of 7 is the true cell", boolean, (small(7), small(7)), (graph_true, native_true)),
            ("MP_UNARY_OP_HASH of a small int is the word itself", hashing,
             (small(7), small(7)), (small(7), small(7))),
            ("MP_UNARY_OP_ABS of -3", absolute, (small(-3), small(-3)), (small(3), small(3))),
            ("MP_UNARY_OP_ABS of 4 is the word itself", absolute, (small(4), small(4)), (small(4), small(4))),
            ("MP_UNARY_OP_INT_MAYBE of a small int is the word itself", int_maybe,
             (small(9), small(9)), (small(9), small(9))),
            # the per-type bodies behind the slots the boot wires
            ("MP_UNARY_OP_BOOL of the true cell routes to bool_unary_op", boolean,
             (graph_true, native_true), (graph_true, native_true)),
            ("MP_UNARY_OP_BOOL of the false cell routes to bool_unary_op", boolean,
             (graph_false, native_false), (graph_false, native_false)),
            ("MP_UNARY_OP_INT_MAYBE of an int object routes to mp_obj_int_unary_op (the null word)",
             int_maybe, (graph_made, native_held), (0, 0)),
            ("MP_UNARY_OP_HASH of a qstr word hashes through the qstr corpus's qstr_hash", hashing,
             (qstr_word, qstr_word), (qstr_hashed, qstr_hashed)),
        )
        for label, op, (graph_arg, native_arg), (graph_want, native_want) in cases:
            got = call("obj.mp_unary_op", (op, graph_arg))
            assert got == graph_want, (
                f"{label}: the imported graph answered {got:#x}, the route's answer is "
                f"{graph_want:#x}"
            )
            want = native.mp_unary_op(op, native_arg)
            assert want == native_want, (
                f"{label}: natively it is {want:#x}, the route's answer is {native_want:#x}"
            )
        # mp_obj_len_maybe takes the same route: the slot the boot wires answers the
        # bool body's LEN (no length: the null word both sides)
        assert call("obj.mp_obj_len_maybe", (graph_true,)) == 0
        assert native.mp_obj_len_maybe(native_true) == 0


def sign_bit(word: int) -> int:
    return (word >> 63) & 1


@pytest.mark.parametrize(
    "name",
    (
        "add_ovf_u64",
        "sub_ovf_u64",
        "add_ovf_i64",
        "sub_ovf_i64",
        "r_uadd_ovf",
        "r_usub_ovf",
        "r_sadd_ovf",
        "r_ssub_ovf",
    ),
)
def test_with_overflow_bits_match_the_contract(world: World, name: str):
    """The add/sub rows of the with.overflow contract pinned to their identities, not
    only to the native call: a carry is ``less(sum, a)`` and a borrow ``less(a, b)`` on
    the words (``wrapping_add_uint64_t`` / ``wrapping_subtract_uint64_t`` + the
    compare), and the signed rows take the bit from the signs --
    ``sign(a) == sign(b) && sign(sum) != sign(a)`` (add) or ``sign(a) != sign(b) &&
    sign(diff) != sign(a)`` (sub). The answer is the wrapped value with the bit folded
    into the top, exactly the shape the corpus and rust_overflow.rs return (the rust
    twins of the rows fold the flag into the same top bit). The inputs are the edges of
    the word that meet every identity: the pairs that carry or borrow at the top and at
    the sign, both."""
    row = {
        "r_uadd_ovf": "add_ovf_u64",
        "r_usub_ovf": "sub_ovf_u64",
        "r_sadd_ovf": "add_ovf_i64",
        "r_ssub_ovf": "sub_ovf_i64",
    }.get(name, name)

    def expected(row: str, a: int, b: int) -> int:
        if row == "add_ovf_u64":
            total, bit = (a + b) % U64, int(a + b >= U64)  # a carry: sum past the word
        elif row == "sub_ovf_u64":
            total, bit = (a - b) % U64, int(a < b)  # a borrow
        elif row == "add_ovf_i64":
            total = (a + b) % U64
            bit = int(sign_bit(a) == sign_bit(b) and sign_bit(total) != sign_bit(a))
        else:
            total = (a - b) % U64
            bit = int(sign_bit(a) != sign_bit(b) and sign_bit(total) != sign_bit(a))
        return total ^ (bit << 63)

    stem = "rust_overflow" if name.startswith("r_") else "overflow"
    program = world.programs[f"{stem}.{name}"]
    function = getattr(world.native[stem], name)
    function.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    function.restype = ctypes.c_uint64
    edges = (0, 1, 2, (1 << 63) - 1, 1 << 63, (1 << 63) + 1, U64 - 2, U64 - 1)
    for a in edges:
        for b in edges:
            want = expected(row, a, b)
            assert function(a, b) == want, f"{name}({a:#x}, {b:#x}): natively {function(a, b):#x}, the contract says {want:#x}"
            with rxf_native.Memory(1 << 16) as memory:
                status, value = rxf_native.run(program, struct.pack("<2Q", a, b), memory)
            assert (status, value) == (OK, want), (
                f"{name}({a:#x}, {b:#x}): the imported graph answered "
                f"({status}, {value:#x}), the contract says ({OK}, {want:#x})"
            )


def test_the_interning_path_lowers_with_variable_frames(tmp_path: Path):
    """The wall casualties of the fixed 1024-byte frame, end to end on the host runner
    (the ground truth seat of ``tests/host/runner.c``: ``rxf_graph_run`` with every
    object of the run registered in its table), the same sequence of the natively
    compiled corpus beside it. REFUSED -> lowered with variable frames: clang -O1 keeps
    the interning's qstr_add path inside qstr_from_strn itself (186 instructions -> 195
    frame values, 1608 bytes) -- a frame the wall (RXF_GRAPH_FRAME, 1024) refused and one
    run cell holds (RXF_GRAPH_CELL_BYTES, 8192; the frames of the call chain are
    variable-length (base, size) links of it, native/graph.c). qstr_from_strn runs its
    own frame as a program of its own; qstr_from_str's call of it is past the inline
    budget and goes as the genuine call card of graph.h (RXF_GRAPH_CALL) -- asserted
    below on the composed words -- so the run steps two frames out of one cell.

    The differential is the interning roundtrip: qstr_from_strn answers the same qstr id
    as the native library (the pools are the corpus's own on both sides and the id is a
    pool index, not an address), a second intern of the same bytes answers the first,
    the search finds what was interned, and the NUL-terminated qstr_from_str (over the
    strlen face) answers the same id."""
    image = tree.load(STARTER)
    wanted = {
        "qstr_port_boot", "qstr_init", "qstr_from_str", "qstr_from_strn",
        "qstr_find_strn", "qstr_len", "qstr_str",
    }  # fmt: skip
    imported = rximport.import_into(
        image, rximport.compile_source(CORPUS / "qstr.c"), module="qstr", only=wanted
    )
    assert set(imported) == wanted, sorted(imported)
    assert image.check() == []
    programs = compose.programs(image)
    numbers = graph_header.numbers()

    # the flip on the composed words: qstr_from_strn's frame is the 1608 bytes the wall
    # refused (word 1 of the program is the frame bytes it declares), inside the cell
    # budget; and qstr_from_str names it with exactly one call card (RXF_GRAPH_CALL
    # whose program operand is qstr_from_strn's object id), not an inline and not a leaf
    callee = "qstr.qstr_from_strn"
    callee_id = compose.object_id(image, image.resolve(callee))
    strn_words = tuple(compose.describe(programs[callee]))
    assert strn_words[1] == 1608, f"{callee} declares a frame of {strn_words[1]} bytes"
    assert strn_words[1] > 1024 and strn_words[1] <= numbers["RXF_GRAPH_CELL_BYTES"]
    words = tuple(compose.describe(programs["qstr.qstr_from_str"]))
    cards = [
        at for at in range(len(words) - 8)
        if words[at] == numbers["RXF_OP_CALL"]
        and words[at + 1] == numbers["RXF_GRAPH_CALL"]
        and words[at + 6 : at + 9] == (numbers["RXF_OPERAND_OBJECT"], callee_id, 0)
    ]  # fmt: skip
    assert len(cards) == 1, f"qstr_from_str carries {len(cards)} call cards, one expected"

    # the cells the running graphs name (the pools, their strings and the arena) as live
    # bytearrays, and the programs the call cards name at their object ids (the host
    # runner's table holds the run's secondaries: the cells and these, no more)
    composer = compose.Composer(image)
    cells: dict[str, tuple[int, bytearray]] = {}
    for handle in range(image.handle_count):
        if not image.live(handle):
            continue
        values = composer.fields(handle)
        if compose.word(values.get("kind")) != "Object" or "object_id" not in values:
            continue
        path = image.path(handle)
        if not path.startswith("qstr."):
            continue
        name = path.rsplit(".", 1)[-1]
        if not name.startswith("global_"):
            continue
        cells[name[len("global_") :]] = (
            int(values["object_id"]),
            bytearray(bytes.fromhex(values.get("data", ""))),
        )
    objects: dict[int, bytes | bytearray] = {
        identity: data for identity, data in cells.values()
    }
    for handle in range(image.handle_count):
        if not image.live(handle) or image.type_name(handle) != "SemanticOperation":
            continue
        callee = composer.fields(handle).get("callee")
        target = image.resolve(callee) if callee else None
        if target is not None and image.path(target) in programs:
            objects[compose.object_id(image, target)] = programs[image.path(target)]

    def address_of(cell: str) -> int:
        data = cells[cell][1]
        return ctypes.addressof((ctypes.c_uint8 * len(data)).from_buffer(data))

    with rxf_native.Memory(1 << 16) as memory:

        def call(name: str, words: tuple[int, ...]) -> tuple[int, int]:
            status, value = rxf_native.run(
                programs[f"qstr.{name}"],
                struct.pack(f"<{len(words)}Q", *words) if words else b"",
                memory,
                objects=objects,
            )
            return status, value

        for boot in ("qstr_port_boot", "qstr_init"):
            status, _ = call(boot, ())
            assert status == OK, f"{boot}: the imported graph refused the boot ({status})"

        # the same sequence natively: one library of the corpus and the very same C the
        # graph ran (its statics keep the pools between the calls, like the cells do)
        shared = tmp_path / "libqstr.so"
        subprocess.run(
            [CLANG, "-O1", "-shared", "-fPIC", str(CORPUS / "qstr.c"),
             str(NATIVE / "basics.c"), str(NATIVE / "stdlib_memory.c"), "-o", str(shared)],
            check=True,
        )  # fmt: skip
        native = ctypes.CDLL(str(shared))
        for routine, arguments, answer in (
            ("qstr_port_boot", [], None),
            ("qstr_init", [], None),
            ("qstr_from_strn", [ctypes.c_void_p, ctypes.c_uint64], ctypes.c_uint64),
            ("qstr_from_str", [ctypes.c_void_p], ctypes.c_uint64),
            ("qstr_find_strn", [ctypes.c_void_p, ctypes.c_uint64], ctypes.c_uint64),
            ("qstr_len", [ctypes.c_uint64], ctypes.c_uint64),
            ("qstr_str", [ctypes.c_uint64], ctypes.c_void_p),
        ):
            function = getattr(native, routine)
            function.argtypes = arguments
            function.restype = answer
        native.qstr_port_boot()
        native.qstr_init()

        for text in QSTR_STRINGS:
            raw = text + b"\0"
            held = ctypes.create_string_buffer(raw)  # alive through both sides' calls
            address = ctypes.addressof(held)

            # the intern: one graph call, one native call, in lockstep (the pool state
            # is the state the last intern left, both sides)
            status, made = call("qstr_from_strn", (address, len(text)))
            expect = native.qstr_from_strn(address, len(text))
            assert (status, made) == (OK, expect), (
                f"qstr_from_strn({text!r}): the imported graph answered "
                f"({status}, {made:#x}), natively it is ({OK}, {expect:#x})"
            )
            status, again = call("qstr_from_strn", (address, len(text)))
            assert (status, again) == (OK, made), (
                f"qstr_from_strn({text!r}) twice: {again:#x} and {made:#x} -- a second "
                "intern of the same bytes must answer the first"
            )
            status, found = call("qstr_find_strn", (address, len(text)))
            assert (status, found) == (OK, made), (
                f"qstr_find_strn({text!r}) after the intern: the search answers "
                f"({status}, {found:#x}), the intern made {made:#x}"
            )
            # the strlen face of qstr_from_str stops at an embedded NUL (that is C's
            # contract, not a graph quirk), so the plain face compares on the texts
            # whose bytes are one string
            if b"\0" not in text:
                status, named = call("qstr_from_str", (address,))
                assert (status, named) == (OK, native.qstr_from_str(address)) == (OK, made), (
                    f"qstr_from_str({text!r}): the card call answers ({status}, "
                    f"{named:#x}), the strn intern is {made:#x}"
                )
            # the string both sides interned reads back as the bytes of the case: the
            # addresses name each side's own cells and cannot compare (the pool layout
            # is the same, so the ids above do), the content can
            status, pointed = call("qstr_str", (made,))
            assert status == OK, f"qstr_str({text!r}): the graph refused ({status})"
            expected_at = native.qstr_str(expect) or 0
            assert bytes(ctypes.string_at(pointed, len(text))) == text == bytes(
                ctypes.string_at(expected_at, len(text))
            ), f"qstr_str({text!r}) reads back wrong on one side"
            assert native.qstr_len(expect) == len(text)


def test_the_ll_entrance_is_read_as_is(monkeypatch):
    """The import entrance format is textual LLVM IR (docs/22): a ``.ll`` is read as it
    stands and no compiler runs on it -- pinned here by breaking every subprocess the
    door could take and importing anyway. What comes out is the same plan the ``.c``
    door gives (byte-identical -- the two fixtures are the same program, entrance.c and
    its hand-written IR entrance.ll), and the IR itself stays ephemeral: no line of it
    rides into the plan (the provenance records are the record, never the IR text)."""
    from_c = rximport.emit_delta(
        list(
            rximport.graphs_of(
                rximport.compile_source(CORPUS / "entrance.c"),
                module="entrance",
                only={"entrance_mix"},
            ).values()
        ),
        "entrance",
    )
    monkeypatch.setattr(
        rximport.subprocess,
        "run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"the .ll door ran a compiler: {args}")
        ),
    )
    entrance = CORPUS / "entrance.ll"
    text = rximport.compile_source(entrance)
    assert text == entrance.read_text()
    plan = rximport.emit_delta(
        list(rximport.graphs_of(text, module="entrance", only={"entrance_mix"}).values()),
        "entrance",
    )
    assert plan.encode() == from_c.encode(), (
        "the .ll door and the .c door disagree on the plan of the same program"
    )
    assert not any(line.strip() and line.strip() in plan for line in text.splitlines()), (
        "the IR text is a reading format, never part of the image"
    )
    # the whole door, as the corpus uses it: import_source_file takes the .ll itself
    image = tree.load(STARTER)
    imported = rximport.import_source_file(
        image, entrance, module="entrance", only={"entrance_mix"}
    )
    assert imported == {"entrance_mix": "entrance.entrance_mix"}
    assert image.check() == []


def test_a_ll_outside_the_subset_is_refused():
    """A raw ``.ll`` the subset cannot take refuses at import with the precise rule
    (docs/22: what passes is decided by the subset of operations the importer lowers,
    never the language the front end came from): garbage.ll's floating-point add has no
    word of the graph to become, and the message names the rule -- an instruction
    outside the subset -- never a guess and never a wrong word."""
    with pytest.raises(
        rximport.Unsupported, match="an instruction outside the subset"
    ) as refused:
        rximport.graphs_of(
            (CORPUS / "garbage.ll").read_text(), module="garbage", only={"garbage_float"}
        )
    assert "fadd" in str(refused.value), refused.value
