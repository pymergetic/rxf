"""Differential tests of the C/C++/Rust -> RXF importer (``tools/native/rximport.py``).

Every function of ``tests/import_corpus/`` (except the ones ``test_outside_the_subset_is_refused``
names, which are outside the subset on purpose) is imported two ways and run on the same
inputs:

* through the importer: the source compiled to textual LLVM IR (``clang-18 -S -emit-llvm
  -O1`` for ``.c``, ``clang++-18`` for ``.cpp``, ``rustc --emit=llvm-ir -C opt-level=0
  -C panic=abort`` for ``.rs``) -> the textual IR parsed and lowered to a ``SemanticGraph``
  delta -> ``Image.apply`` -> ``compose.programs`` -> the program words ->
  ``rxf_native.run``, the real interpreter of ``native/graph.c``;
* natively: the same source compiled by the same family of compiler into a shared library
  and called with ctypes. ``leaf.c``, ``memory.c`` and ``slots.c`` are linked with
  ``native/basics.c`` and ``native/stdlib_memory.c`` so a leaf call runs the very same C
  on both sides.

The contract of the importer: deterministic code without undefined behavior has the same
observable behavior on both sides. Each test compares ``(status, value)`` per input
against the natively compiled function (and the bytes the memory cases write, both
sides); nothing is skipped. The refused programs (recursion, a frame past the budget, the
C++ exception/RTTI runtime calls and the values rustc keeps in memory at
opt-level 0) are asserted to be refused at import, not guessed; the panic paths of Rust
are asserted to refuse the *run* (``test_panic_paths_refuse_the_run``), one-sidedly --
the natively compiled call would abort the process on those inputs.
"""

from __future__ import annotations

import ctypes
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

import compose
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
    # a Rust panic path: the same answers as the native code on the inputs that do not
    # panic, and a Refuse of the run at zero (AT_RUN_REFUSED below)
    "r_panic_if_zero": ("rust_panic.rs", [(x,) for x in VALUES if x]),
    # __builtin_*_overflow of C: the llvm.*.with.overflow intrinsics and their extractvalue
    "add_ovf_u64": ("overflow.c", PAIRS),
    "sub_ovf_u64": ("overflow.c", PAIRS),
    "add_ovf_i32": ("overflow.c", NARROW_PAIRS),
    "sub_ovf_i32": ("overflow.c", NARROW_PAIRS),
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
# frame past the budget of a run, the C++ ABI shapes and runtimes, and what rustc keeps in
# memory at opt-level 0
REFUSED = {
    ("calls.c", "ping"): "recursion",
    ("calls.c", "pong"): "recursion",
    ("wide.c", "wide"): "frame",
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
    ("cpp_refuse.cpp", "vcall"): "indirect",
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
GLOBAL_ADDRESSES: dict[tuple[str, str], str] = {("global_scalar.c", "counter_at"): "counter"}


@dataclass
class World:
    image: object
    programs: dict[str, bytes]  # the words compose made, keyed by the path of the Function
    native: dict[str, ctypes.CDLL]  # the corpus compiled natively, by the name of the file


# a corpus file that only declares an ABI whose machine lives elsewhere: MicroPython keeps
# the nlr ABI of py/nlr.h in its own translation unit (nlr_posix.c), so the differential
# links its own stubs (nlr_stubs.c) the same way -- the IR the importer reads keeps every
# call opaque, exactly as MicroPython's own build does
FRIENDS: dict[str, tuple[str, ...]] = {"nlr_call.c": ("nlr_stubs.c",)}


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
        self.emulator.map(graph_world.BASE, graph_world.MAPPED)
        addresses = {
            index: self.emulator.load(data) for index, data in code.leaves["x86_64"].items()
        }
        case = graph_world.Case("cells", programs[min(programs)], b"", objects)
        self.emulator.write(
            graph_world.BASE, graph_world.memory(case, sorted(addresses.items()))
        )
        self.run_code = self.emulator.load(code.run["x86_64"])
        self.handle = case.handle
        # a run reads the words of its program out of the payload of its handle's object:
        # the entry's size is patched when the sequence moves on to the next program
        count = struct.unpack_from(
            "<Q", self.emulator.read(graph_world.CONTEXT, 144), 32
        )[0]
        table = self.emulator.read(graph_world.OBJECTS, count * 80)
        self.program_entry = graph_world.OBJECTS
        for index in range(count):
            if struct.unpack_from("<Q", table, index * 80)[0] == graph_world.PROGRAM_ID:
                self.program_entry = graph_world.OBJECTS + index * 80
        self.placed: bytes | None = None

    def call(self, name: str, args: tuple[int, ...]) -> tuple[int, int]:
        """One call of the sequence: (status, the answer word). The world, its cells
        included, stays as the last call left it."""
        program = self.programs[name]
        if program is not self.placed:
            self.emulator.write(graph_world.PROGRAM, program)
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


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> World:
    build = tmp_path_factory.mktemp("import_corpus")
    image = tree.load(STARTER)
    native: dict[str, ctypes.CDLL] = {}
    homes = sorted(
        {home for home, _ in CASES.values()}
        | {home for home, _ in MEMORY_CASES.values()}
        | {home for home, _ in PAIR_MEMORY_CASES.values()}
        | {home for home, _ in AT_RUN_REFUSED.values()}
        | set(GLOBAL_CASES)
    )
    for file in homes:
        source = CORPUS / file
        stem = source.stem
        wanted = (
            {name for name, (home, _) in CASES.items() if home == file}
            | {name for name, (home, _) in MEMORY_CASES.items() if home == file}
            | {name for name, (home, _) in PAIR_MEMORY_CASES.items() if home == file}
            | {name for name, (home, _) in AT_RUN_REFUSED.items() if home == file}
            | {name for name, _ in GLOBAL_CASES.get(file, ("", ()))[1]}
            | {name for home, name in GLOBAL_ADDRESSES if home == file}
        )
        imported = rximport.import_source_file(image, source, module=stem, only=wanted)
        assert set(imported) == wanted, f"{file}: imported {sorted(imported)}, not {sorted(wanted)}"
        shared = build / f"{stem}.so"
        link(source, shared)
        native[stem] = ctypes.CDLL(str(shared))
    assert image.check() == []  # the deltas of the importer hold together
    return World(image, compose.programs(image), native)  # compose consumes the imported graphs


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


@pytest.mark.parametrize(("file", "name"), sorted(GLOBAL_ADDRESSES))
def test_global_address_is_the_cell(world: World, file: str, name: str):
    """What ``&static`` answers: the payload address the Object handle resolves to. The
    two sides map the cell somewhere of their own, so the word itself cannot compare: its
    shape can -- the same address at every call, aligned, inside the arena."""
    stem = Path(file).stem
    run = CellRun(world.image, {name: world.programs[f"{stem}.{name}"]})
    status, first = run.call(name, ())
    again, second = run.call(name, ())
    assert (status, again) == (OK, OK)
    assert first == second, f"{name}: two calls answered {first:#x} and {second:#x}"
    assert first and first % 8 == 0, f"{name}: {first:#x} is no aligned address"
    assert graph_world.BASE <= first < graph_world.BASE + graph_world.MAPPED
