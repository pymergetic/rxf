"""The C/C++/Rust -> RXF importer: a minimal LLVM-IR subset as a semantic graph of the image.

Phase 6 groundwork (a prototype): a function arrives as textual LLVM IR and the SSA of one
function is lowered to a ``SemanticGraph`` of the image -- the same objects the starter
describes in XML. The entrance format is the ``.ll`` itself (docs/22): ``compile_source``
reads a raw ``.ll`` as it stands and runs no compiler on it, while the language suffixes
are convenience doors that run the matching compiler first -- ``clang-18 -S -emit-llvm
-O1`` for ``.c``, ``clang++-18`` for ``.cpp``, ``rustc --emit=llvm-ir -C opt-level=0
-C panic=abort`` for ``.rs``. The IR text is ephemeral (never stored in the image; the
provenance records are the record). ``tools/native/compose.py``
turns that graph into program words like any other graph, and the words run on the interpreter
of ``native/graph.c`` through ``tests/rxf_native.py``.

SSA maps to RXF nearly 1:1 (docs/19 and the design of the composer):

    LLVM                       RXF
    basic block                SemanticBlock
    phi                        block parameters (a Bound value per phi; the edge of each
                               predecessor passes its incoming values as copies)
    ret                        Terminator.Return (its TerminatorValue is the result)
    br i1 c, t, f              Terminator.Branch (edges[0] taken on not zero, edges[1] on
                               zero: the condition of this pass is never a status)
    br label t                 Terminator.Goto
    switch                     Terminator.SwitchTag (case tags in the block data, default last)
    unreachable                Terminator.Refuse with RXF_REFUSE_UNSUPPORTED (16)
    select                     lowered to Branch + two Goto edges that pass the chosen value
                               to a join block parameter (the composer has no select opcode)
    icmp / integer arithmetic  one CallCallback operation per leaf: ``add`` is the leaf
                               ``wrapping_add_uint64_t`` of the starter, and so on (LEAVES)
    call of a leaf-shaped      OP_CALL with the leaf id: the callee must be a symbol of the
    C function                 interpreter's function table (the ``rxf_*`` of native/basics.c)
                               whose trailing pointer argument is the out slot, the ABI that
                               ``shape_of`` of native/graph.h gives

Runtime services stay leaves (the way ``stdlib_memory`` is): ``compose.Composer.leaf_id`` finds
the leaf from the callee's path, and ``native/graph.c`` dispatches it through the function
table at run time. The program words do not depend on the machine.

Semantics contract: for deterministic code (C, C++ or Rust) without undefined behavior the
imported program has the same observable behavior as the function compiled natively -- the
same (status, value) on every input of the differential tests. Inside the contract nothing
is silently wrong; outside it the importer refuses rather than guesses (below).

The subset of LLVM IR this prototype imports (anything else raises ``Unsupported``):

    types      i1/i8/i16/i32/i64 operands and results -- a narrow value is the low bits of
               its 64-bit word, kept zero-extended; ptr as a 64-bit address word; i32 also
               as the dropped status of a recognized leaf call
    ops        add, sub, mul, udiv, sdiv, urem, srem, and, or, xor, shl, lshr, ashr on
               every width (wrapping or bit semantics; the count is the leaf's u32
               parameter; a multiply is the wrapping leaf in the low bits and the divides
               and remainders are the width-appropriate integer leaves of native/graph.h,
               ids 39-48: i32 takes its own pair and a narrow signed form the
               sign-extended words)
    icmp       eq, ne, ult, ule, ugt, uge, slt, sle, sgt, sge on every width (the signed
               ones compare the sign-extended words), and eq/ne of two ``ptr`` (the word
               compare of the addresses they carry)
    control    br (conditional and not), switch, ret i64, unreachable
    other      select, phi, the casts zext/sext/trunc between every width and
               inttoptr/ptrtoint (an address is an i64 word), alloca with a store and a
               load of a local scalar slot (the out slot of a recognized leaf call) or of
               an address-taken local (a wide frame slot), getelementptr (pointer
               arithmetic: an array index scaled by the element size, a struct field at a
               constant offset, each summed onto the base address), a load/store at an
               address of the flat memory (a pointer parameter, a getelementptr result, or a
               select/phi of addresses -- the bytes move through the ``memory_copy`` leaf), a
               load/store of a ptr value (the address word read or written as 8 bytes), a
               call of a leaf-shaped
               function, the intrinsics llvm.umin/umax/smin/smax.i64 (as select),
               llvm.fshl/fshr.iN (as two masked shifts and a select), llvm.memcpy and
               llvm.memset (as the ``memory_copy`` / ``memory_fill`` leaves),
               llvm.{uadd,sadd,usub,ssub}.with.overflow.iN (the sum is one wrapping leaf and
               the overflow one compare; extractvalue/insertvalue carry the { i64, i1 }),
               llvm.{umul,smul}.with.overflow.iN (the product is one wrapping leaf and the
               overflow the bit ``p != p_N`` or ``(a != 0) & (p / a != b)``),
               llvm.lifetime.start/end (nothing), and a call directly before an unreachable
               (a Rust panic path, a C ``noreturn`` call, a MicroPython ``mp_raise_*``): it
               lowers to nothing and the unreachable refuses the run on that path
    returns    i1..i64/ptr, and ``void`` (a void body answers 0 -- a caller of a void
               function reads none of it, so the answer is only the run's word)
    nlr        ``nlr_push``/``nlr_pop``/``nlr_jump`` of py/nlr.h lower by name: the push
               is its first-return 0, the pop nothing, and the nlr_jump is the divergence
               it always is in the IR (the unreachable after it refuses the run). The
               policy and its soundness boundary are documented at ``lower_nlr``
    operands   names, integer constants, and the constant expressions ``inttoptr (i64 N to
               ptr)`` (the address word N: MicroPython's REPR A immediate objects arrive
               this way) and ``getelementptr`` of a global (it refuses as that global)

Where no leaf exists the importer lowers to what does (the same result, more words):
``xor`` is ``(a | b) - (a & b)``; ``sext`` is the centering ``((x + 2^(n-1)) & (2^n - 1))
- 2^(n-1)``; ``ashr`` is the sign-word form ``(a ^ s) >> k ^ s`` with ``s = 0 - ((a >> 63)
& 1)`` (each xor of a 0-or-all-ones word is ``(x | s) & ~(x & s)``); a funnel shift is
two masked shifts and a select on the count being 0; the overflow bit of a signed
with.overflow is the sign of ``(a ^ s) & (b ^ s)`` (add) or ``(a ^ b) & (a ^ s)`` (sub)
on the sign-extended words. Each lowering is documented at its helper.
    per file   every ``define`` of the module is imported as one Function with one graph;
               labels are the ones clang (or rustc) prints

The C++ subset (``compile_cpp``; the differential corpus ``tests/import_corpus/cpp_refs.cpp``):

    extern "C" functions  the entry points; their IR is C's (clang++ -O1) and every name
                          of the module stays a plain C name (``bool_pick``)
    references            a ``T&`` parameter is the ``ptr`` of its object: the body reads
                          and writes the caller's bytes through it (``ref_add``)
    methods               one graph per method too: ``this`` is the first (pointer)
                          parameter and the body is its field loads and stores
                          (``Counter::bump`` is imported by its mangled name as well as
                          through the extern "C" wrapper ``counter_bump``)
    bool                  a parameter is the i1 the ABI zero-extends (i1 loads and stores
                          are 1 byte of memory); the corpus feeds 0/1 -- a C++ caller reads
                          any nonzero register as true while the subset keeps the low bit,
                          so only 0/1 are inside the contract

The Rust subset (``compile_rs``; the differential corpus ``rust_int.rs``, ``rust_mem.rs``,
``rust_overflow.rs``):

    #[no_mangle] pub extern "C"   the entry points; rustc's v0 mangling is only ever seen
                          in the runtime callees the importer refuses by name
    integers              i8/i16/i32/i64 parameters and results of the flat profile:
                          wrapping arithmetic, the shifts (the count is masked in the
                          source, as in the C corpus), the compares and the casts
                          (``r_add8``, ``r_shl8``, ``r_ashr32``, ``r_zext_mix``)
    raw pointers          ``*mut u8`` / ``*const u32`` are the addresses of the flat
                          memory: getelementptr arithmetic and the loads and stores
                          through them (``r_idx8``, ``r_store8``, ``r_load32``)
    overflowing_*         the add/sub + compare shape of llvm.*.with.overflow: the
                          { i64, i1 } is built by insertvalue and read by extractvalue
                          (``r_uadd_ovf``; the same intrinsics arrive from the C
                          ``__builtin_*_overflow`` of ``overflow.c``)
    panic paths           a call of core/std::panicking diverges: the call lowers to
                          nothing and the unreachable after it to the run's Refuse
                          (RXF_REFUSE_UNSUPPORTED) -- the status-branch idiom, so a run
                          refuses on the path that would panic instead of unwinding.
                          ``r_panic_if_zero`` answers the same as the native code on its
                          inputs and refuses the run at zero; the null and alignment
                          guards rustc puts on every raw dereference are the same shape
                          (``test_panic_paths_refuse_the_run``). A panic call that is not
                          followed by unreachable refuses at import (prescan). A body
                          whose every path panics this way has no Return of its own and
                          imports as a graph that refuses every run
                          (``r_always_panics``, ``always_booms``)

What these refuse, by name (never lowered to wrong words):

    a C++ exception          the __cxa_*/_Unwind_* runtime calls, and the invoke,
                             landingpad and resume edges of a throw or a try/catch
                             (``cpp_throw``, ``cpp_catch``)
    RTTI                     a __dynamic_cast and the typeinfo symbols _ZTI*/_ZTV*/_ZTS*
                             (``rtti_call``); an ordinary dynamic_cast lowers the null
                             check of its cast now and refuses at the __dynamic_cast
                             runtime call itself (``dyn``)
    a call through a word     a call of a register (a function word: a virtual method or
                             a slot of a type cell) dispatches on the words this import
                             makes memory carry (``lower_indirect``: the store that wires
                             the slot names the body, one call card per word); a word no
                             store of the import wires refuses the import, a word the
                             arms do not name refuses the run (``slot``)
    a by-value aggregate     a return through the sret pointer ("returns through an sret
                             pointer", ``big_make``) and a byval parameter (a copy the
                             callee may write, ``big_take``)
    a module-level global    the address of a panic message or a typeinfo: the subset
                             imports no globals
    a value carried          rustc's opt-level 0 keeps the join of a branch in a local
    through memory           slot (and clang does the same when volatile keeps a local
                             un-promoted): a local reads back only where a store of its
                             own block wrote it, else "a value carried through memory"
                             (``rust_refuse.rs``'s r_join, ``memory.c``'s slot_join)
    a getelementptr of a     the pass walks flat addresses only: a local's own bytes are
    local's own address      a frame slot, not memory it can offset into (r_array); and
                             any GEP of a local is refused the same way
    a function with no       a body with no Return and a cycle the entry reaches (an
    return of its own        endless loop) answers nothing ("no return of an i64 value",
                             refusebody.c's never_answers). A body whose every path
                             panics has no such cycle and imports as a graph that
                             refuses every run (the Refuse-only shape of r_always_panics
                             and refusebody.c's always_booms)

What is NOT supported (and why), for the full importer:

    llvm.{umul,smul}.with     an overflow multiply of a width past 32 and not 64 has no
    overflow of iN, N > 32     exact product in the 64-bit words (the identity the widths
                               of 32 and of 64 take is refused rather than guessed;
                               ``overflow.c``'s mul_ovf_* are the widths that lower)
    fadd/fcmp and all floats      the float leaves refuse non-finite values, and the value of
                                  this pass is whole words
    load/store through a pointer   a load/store resolves through a known slot: a local scalar
    the pass cannot name           (the out slot of a leaf call), a wide address-taken local, or
                                  an address of the flat memory (a pointer parameter, a
                                  getelementptr result, or a select/phi of addresses -- the
                                  chosen address is tracked). What is refused is arithmetic on
                                  an address-taken local's own address: its bytes are a frame
                                  slot a leaf may write, but the pass does not do pointer
                                  arithmetic on it
    getelementptr past the        an array index scaled by the element size and a struct
    layout the pass tracks        field at a constant offset are lowered; a field index that
                                  is not constant, or a step into a type with no known
                                  layout, is refused
    icmp of a ptr, signed      eq/ne of two address words lowers as ``equal_uint64_t`` on
                                  the words and the unsigned orders as ``less_uint64_t``
                                  (the flat profile compares addresses as words;
                                  ``ptrcmp.c``, ``ptrord.c``); a signed order of two
                                  addresses is refused ("icmp of ptr slt") -- nothing in
                                  the contract signs the flat space
    a value carried through       a local reads back only where a store of its own block
    memory (a branch join)        wrote it (or where nothing ever stored: it starts zero,
                                  like the frame). The join of a branch -- what rustc's
                                  opt-level 0 keeps in a local slot -- is refused rather
                                  than read from the wrong store; promoting such joins to
                                  block parameters (mem2reg) is the next step
    recursion (direct or mutual)  the inline path refuses a call of a function already being
                                  inlined on the way; the graph call of the full importer is
                                  what recursion needs
    the status of a leaf call     the status of an ``rxf_*`` call is dropped (the corpus reads
                                  it no more than the C does); wiring it is a status_value and
                                  a refusal branch, as the starter's graphs do
    signed overflow in C          undefined in C, outside the contract; the nsw/nuw flags of
                                  LLVM are ignored (wrapping), so the import is *more* defined
    shift counts >= 64            undefined in C, outside the contract; the leaf refuses with
                                  RXF_REFUSE_SHIFT_COUNT instead of poison
    frames past the run cell     RXF_GRAPH_CELL_BYTES of native/graph.h: the run cell carries
                                 the active chain's frames as variable-length (base, size)
                                 links, so ``lower()`` refuses a frame that cannot fit one
                                 cell rather than misplace values at run time (the explicit
                                 frame-size policy is the section below; the 1024-byte wall
                                 of RXF_GRAPH_FRAME is gone -- history in docs/20)
    edge copies over 256 bytes    RXF_GRAPH_COPY of native/graph.h: the run stages one edge's
                                  argument copies in a 256-byte buffer (native/graph.c), so an
                                  edge that would copy more refuses at import
    inline copies over 48 words   the inline budget: a callee whose inlined body would add
                                  more than 48 words to the caller's frame is not inlined --
                                  the call goes as the call card of graph.h instead (the
                                  callee is a program of its own; only a callee whose frame
                                  would not fit a whole run cell is refused outright)

Frame-size policy (what a MicroPython C frame maps to):

    A C function's frame becomes the run's frame words one value at a time. Every non-literal
    value of the graph -- a function parameter, a block parameter (a phi's result), and each
    operation result (a leaf call's answer) -- occupies one 8-byte frame word (``LValue.width``,
    rounded up to 8). Locals split by whether their address escapes the function:

      narrow local  an alloca whose address never reaches a leaf's pointer parameter. It is
                    SSA-renamed -- the name is whatever the last store put there and a load
                    reads back -- so it takes no dedicated frame words of its own; only the
                    values it holds are counted (``vol_round`` and ``bump`` of slots.c).
      wide local    an alloca whose address reaches a leaf's pointer parameter (a leaf writes
                    it through ``pointer`` of native/graph.h). It is a 16-byte
                    ``FixedArrayU8Of16`` slot and ``local_addr`` hands its address to the leaf
                    (``local_round`` of slots.c passes ``&t`` to ``memory_copy``).

    The frame of a function is the sum over every non-literal value of its width rounded up to
    8 bytes (a wide slot counts 16). The budget is the run cell: ``RXF_GRAPH_CELL_BYTES`` (8192
    bytes, native/graph.h) must hold the frame -- the run carves the active chain's frames as
    variable-length (base, size) links of one cell, and refuses a chain whose sum does not fit
    (RXF_REFUSE_CELL_BUDGET), never spilled and never split. When a function's frame cannot fit
    the cell, ``lower()`` refuses with ``Unsupported``
    ("a frame of N bytes is too large") at import time -- before any words are emitted -- rather
    than misplace values at run time. Inlining is counted: an inlined callee's values add to the
    caller's frame, so a deeply inlined call chain can refuse that way -- ``wide.c`` (six bodies
    of ~30 values each, 1264 bytes) does not: it fits the cell and imports (the wall casualties
    of the fixed 1024-byte frame flip with the variable frames, docs/20).
    Two more budgets keep the
    counting honest: one edge may stage at most ``RXF_GRAPH_COPY`` bytes of argument copies
    (256, native/graph.h), and one inlined callee may add at most ``INLINE_WORDS`` (48) words to
    the caller -- past the copy budget the import refuses, past the inline budget the call is
    not inlined and goes as a call card instead (``test_outside_the_subset_is_refused``).

    A load of the flat space stages its bytes in a wide slot before the word is read out of it
    (OP_MOVE reads operands, not the memory an address names). The staging is filled right
    before it is read, so the whole graph keeps one such scratch slot, not one per load: the
    frame counts the values a body asks for, and a slot per load is not one of them
    (``load_scratch_of``; the qstr bodies of ``qstr.c`` are where it matters).

    This is the policy the MicroPython core import gives: a MicroPython C frame is not a flat
    stack block but this word-per-value layout, so the frame a function needs is sized by its
    SSA value count plus its address-taken locals (16 bytes each), not by its C stack frame.
    A MicroPython function whose SSA values plus wide slots cannot fit a run cell
    (``RXF_GRAPH_CELL_BYTES``) refuses at import; anything below the cell lowers -- the
    interning path of ``qstr.c`` (qstr_from_strn's 1608-byte body) is the proof, imported
    where the fixed 1024-byte wall used to refuse it (docs/20). A value-width policy for
    MicroPython's word (32- vs 64-bit) is the one open piece (the pass assumes 64-bit words
    today).

Next steps for the full importer: the flat memory profile for load/store is in (getelementptr
arithmetic, loads and stores at a named address -- including a select or phi of addresses -- and
the memcpy/memset intrinsics to the memory leaves), the frame-size policy is written above, and
the C++ and Rust subsets of their sections. What remains:

    the checked multiply as    llvm.{umul,smul}.with.overflow takes its overflow bit from
    the overflow bit           the identity (one divide and two compares) today; the answer
                               native/graph.h gives is one checked_multiply whose status is
                               that bit -- it waits for the status wiring below
    mem2reg                    the joins a branch keeps in a local slot (rustc's opt-level 0,
                               and clang's volatile locals) become block parameters the phi
                               machinery already wires; until then the load rule refuses them
                               ("a value carried through memory")
    a signed order of        addresses compare as words already (eq/ne as equal_uint64_t,
    addresses                  the unsigned orders as less_uint64_t); a signed order of
                               addresses stays refused ("icmp of ptr slt")
    the status wiring          the status of a leaf call as a status branch, as the starter's
                               graphs do it -- the emitter's status_value field is in (rxpy's
                               // and % lower through it, the leaf's own refusal as the
                               branch); wiring this strand's own leaves through it is what
                               remains here
    graph calls                what recursion needs, with the refusal/status wiring of the
                               starter

and then MicroPython-core-as-corpus (the leaf table of native/graph.h grows in the change
that owns that file -- this pass only names the leaves it needs).

The C++ exception/RTTI runtimes and by-value aggregates (sret/byval) stay refused by policy:
they are runtime machinery the interpreter has no card for, and this pass never lowers them to
words that would run wrong. A call through a function word is no longer one of them where the
import makes memory carry the words (the unary_op slot dispatch of ``lower_indirect``); the
rest of that class stays refused with the reason the pass gives it.

The value-width policy is the module's own (``rxemit.DataModel``): the datalayout's pointer
width decides what a word of the flat memory is -- ILP32 and LP64 both import, each value
word states its width and a store writes the bytes the model gives the type. A module whose
widths the model cannot decide honestly (a pointer width that is neither 4 nor 8, a
datalayout and typed pointers that disagree) refuses the import, never a guess.

A module-level global whose initializer carries an address -- a function pointer in a
callback struct, a pointer to another global -- is a **relocation** (``@data = global
%struct { i32, void (i8*)* @callback }``): the cell is placed as its byte image with that
slot zero, and the wire that fills it is one store of the word by the module's boot
(``__wires_boot``, the loader's work a port runs before anything else), which is the link
fact ``function_wiring`` arms a dispatch's call cards from. A relocation this pass cannot
place (a C++ runtime symbol, a name the link cannot name, a slot the model's word does not
fit) refuses the cell and every use of it, never a slot left silently wrong.

The source beside the IR is read for more than its bodies (docs/22): every comment of the
file is lifted to a Doc object of the image -- a task marker its own managed kind -- and the
run's ``--provenance`` claim cites each file it compiles (one origin record, one citation per
file with its locator). A run that claims nothing still lifts its comments and says
``provenance: none``; a run that claims an anonymous word is refused at the floor.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import graph_header
import rxemit
from rxemit import (
    LP64,
    U32,
    U64,
    UNDECIDED,
    WIDE,
    DataModel,
    Global,
    LBlock,
    LGraph,
    LOp,
    LValue,
    MOperand,
    ParseError,
    ValueOperand,
    align,
    emit_delta,
)

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.kinds import DocKind, DocRole

# ---- the leaves of the interpreter the importer wires -----------------------------------------

# The leaf id name of native/graph.h -> the Function of the starter that is that leaf, how many
# scalar arguments its call takes (the trailing pointer to the out slot is not counted), whether
# it writes an out slot, and which of its arguments are pointer positions (an address, or the
# address of a wide slot: ``pointer`` of native/graph.h gives a wide slot's address to a leaf).
# The arities are the ones ``shape_of`` of native/graph.h gives: QQO takes two, QO one, QWO two,
# PQPQQ five and PQBQ four without an out, PQPQQO five with one.
LEAVES: dict[str, tuple[str, int, bool, frozenset[int]]] = {
    "wrapping_add_uint64_t": ("pymergetic.rxf.numeric.wrapping.add_uint64_t", 2, True, frozenset()),
    "wrapping_subtract_uint64_t": ("pymergetic.rxf.numeric.wrapping.subtract_uint64_t", 2, True, frozenset()),
    "bit_and_uint64_t": ("pymergetic.rxf.numeric.bitwise.bit_and_uint64_t", 2, True, frozenset()),
    "bit_or_uint64_t": ("pymergetic.rxf.numeric.bitwise.bit_or_uint64_t", 2, True, frozenset()),
    "bit_not_uint64_t": ("pymergetic.rxf.numeric.bitwise.bit_not_uint64_t", 1, True, frozenset()),
    "shift_left_uint64_t": ("pymergetic.rxf.numeric.bitwise.shift_left_uint64_t", 2, True, frozenset()),
    "shift_right_uint64_t": ("pymergetic.rxf.numeric.bitwise.shift_right_uint64_t", 2, True, frozenset()),
    "equal_uint64_t": ("pymergetic.rxf.numeric.compare.equal_uint64_t", 2, True, frozenset()),
    "less_uint64_t": ("pymergetic.rxf.numeric.compare.less_uint64_t", 2, True, frozenset()),
    "memory_copy": ("pymergetic.rxf.foundation.memory.copy", 5, False, frozenset({0, 2})),
    "memory_fill": ("pymergetic.rxf.foundation.memory.fill", 4, False, frozenset({0})),
    "memory_compare": ("pymergetic.rxf.foundation.memory.compare", 5, True, frozenset({0, 2})),
    # the integer leaves of the flat profile (native/graph.h): multiply, divide and
    # remainder, both kinds of integer. A division by zero refuses DIVISION_BY_ZERO at run
    # time and the signed forms refuse OVERFLOW at INT_MIN / -1 -- the shapes LLVM leaves
    # undefined never answer a wrong number instead
    "wrapping_multiply_uint64_t": ("pymergetic.rxf.numeric.wrapping.multiply_uint64_t", 2, True, frozenset()),
    "divide_uint64_t": ("pymergetic.rxf.numeric.division.divide_uint64_t", 2, True, frozenset()),
    "remainder_uint64_t": ("pymergetic.rxf.numeric.division.remainder_uint64_t", 2, True, frozenset()),
    "divide_int64_t": ("pymergetic.rxf.numeric.division.divide_int64_t", 2, True, frozenset()),
    "remainder_int64_t": ("pymergetic.rxf.numeric.division.remainder_int64_t", 2, True, frozenset()),
    "checked_multiply_uint64_t": ("pymergetic.rxf.numeric.checked.multiply_uint64_t", 2, True, frozenset()),
    "checked_multiply_int64_t": ("pymergetic.rxf.numeric.checked.multiply_int64_t", 2, True, frozenset()),
    # the 32-bit division leaves (native/graph.h ids 46-48 and 25): an i32 divide or
    # remainder takes its own leaf, the type's own refusal (INT32_MIN / -1) included
    "divide_uint32_t": ("pymergetic.rxf.numeric.division.divide_uint32_t", 2, True, frozenset()),
    "remainder_uint32_t": ("pymergetic.rxf.numeric.division.remainder_uint32_t", 2, True, frozenset()),
    "divide_int32_t": ("pymergetic.rxf.numeric.division.divide_int32_t", 2, True, frozenset()),
    "remainder_int32_t": ("pymergetic.rxf.numeric.division.remainder_int32_t", 2, True, frozenset()),
}
SYMBOLS = {f"rxf_{key}": key for key in LEAVES}  # the C names, as native/basics.c spells them
UNSUPPORTED_STATUS = graph_header.numbers()["RXF_REFUSE_UNSUPPORTED"]  # unreachable -> Refuse


def leaf_key(callee: str) -> str | None:
    """The LEAVES key of a callee that is a leaf of the interpreter (a leaf is spelled
    ``rxf_*``, plain or qualified with its path), else None."""
    name = callee.rsplit(".", 1)[-1]
    return SYMBOLS.get(name if "." in callee else callee)


def is_panic_callee(callee: str) -> bool:
    """A Rust panic path (``core::panicking::``, ``std::panicking::`` of the v0 mangling):
    it diverges -- the run refuses on that path instead of unwinding."""
    return "panicking" in callee


def is_runtime_callee(callee: str) -> bool:
    """A C++ exception/RTTI runtime call, or a Rust runtime call (allocation, panic start):
    none of them has a leaf of the interpreter, so the import refuses by name."""
    return callee.startswith(
        ("__cxa_", "_Unwind", "_ZTI", "_ZTV", "_ZTS", "__gxx_personality", "__rust")
    ) or "dynamic_cast" in callee


def is_runtime_symbol(name: str) -> bool:
    """A symbol of the C++ exception/RTTI machinery (a vtable, a typeinfo, a type name:
    ``_ZTV*``/``_ZTI*``/``_ZTS*``, the ``__cxa_*``/``_Unwind*``/``__gxx_*`` runtime), or a
    Rust runtime symbol. A relocation of one refuses the cell it sits in: these words
    name runtime machinery the interpreter has no card for (the class ``is_runtime_callee``
    refuses the calls of), so no wire of this pass may make memory carry them."""
    return is_runtime_callee(name)


# the nlr ABI of MicroPython (py/nlr.h): a setjmp-style push, a noreturn jump and a pop.
# The nlr policy below lowers these by name
NLR_PUSH = "nlr_push"
NLR_JUMP = "nlr_jump"
NLR_POP = "nlr_pop"
BINOPS = frozenset({"add", "sub", "mul", "udiv", "sdiv", "urem", "srem", "and", "or", "xor", "shl", "lshr", "ashr"})
CASTS = frozenset({"zext", "trunc", "sext", "inttoptr", "ptrtoint"})

# the kind, role and refusal names the delta is spelled in live in rxemit (one vocabulary,
# one emitter for every strand); re-exported here under the names this module has always
# answered to
KINDS = rxemit.KINDS
SEMANTIC = rxemit.SEMANTIC
REFUSAL_SET = rxemit.REFUSAL_SET
# The frame budget of a run is the run cell itself (native/graph.h): the frames of the
# active call chain are variable-length (base, size) links carved from one cell of
# RXF_GRAPH_CELL_BYTES, exact to the frame bytes a program declares. A frame is lowered
# only where it can fit that cell; a chain whose frames together do not fit is refused
# by the run (RXF_REFUSE_CELL_BUDGET), never spilled and never split. The 1024 of
# RXF_GRAPH_FRAME is the fixed-region wall of the old format and measures nothing here.
GRAPH_CELL_BYTES = graph_header.numbers()["RXF_GRAPH_CELL_BYTES"]  # the cell a frame must fit
GRAPH_COPY = graph_header.numbers()["RXF_GRAPH_COPY"]  # what one edge's copies may stage
# an inlined callee may add at most these many words to the caller's frame; past it the
# inline is undone and the call goes as the call card of graph.h instead -- the callee is
# a program of its own, not a body the caller's frame was never meant to hold (only a
# callee whose frame would not fit a whole run cell is refused outright in lower_inline)
INLINE_WORDS = 48
FULL = rxemit.FULL  # the word's mask, one home (the emitter's narrow-integer helpers)
# what a narrow (i1/i8/i16/i32) value is: the low bits of its word. Every operation of the
# subset leaves its result in this form, so a value is always the zero-extension of the
# integer it names, whatever width it is typed with
MASKS = {"i1": 1, "i8": 0xFF, "i16": 0xFFFF, "i32": 0xFFFFFFFF, "i64": FULL}


class Unsupported(Exception):
    """A construct of the input that the subset of this importer does not cover."""


# ---- the width model of one module (rxemit.DataModel) -----------------------------------------

# the pointer width a ``target datalayout`` states: ``p:32:32`` (and the default address
# space's ``p0:32:32``) say what one word of the flat memory is; the address-space specs
# of a layout (``p10:8:8``: the wasm tables) are not the pointer the C of the module uses
RE_DATA_LAYOUT = re.compile(r'target\s+datalayout\s*=\s*"([^"]*)"')
RE_POINTER_SPEC = re.compile(r"\bp0?:(\d+):")


def module_model(text: str) -> DataModel:
    """The width model of a textual module: what one word of its flat memory is.

    The ``target datalayout`` decides it: the pointer width of its default address space
    (``p:32:32``/``p0:32:32`` is one 4-byte word), and where a layout states no pointer
    width it is the 64-bit default that layout stands on (clang's native datalayout
    spells only its own address spaces -- ``p270``/``p271``/``p272`` -- and the default
    pointer of it is 8 bytes). A module with no ``target datalayout`` at all states no
    width this pass can take: it is placed against the undecided model, whose integers
    still know their widths but whose pointer words refuse where a store, a load or a
    cell would use one. The same goes for a pointer width that is neither 4 nor 8, or a
    layout that states two that disagree: the import refuses with the reason, and the
    refusal is the story."""
    layout = RE_DATA_LAYOUT.search(text)
    if layout is None:
        return UNDECIDED
    widths = {int(found.group(1)) // 8 for found in RE_POINTER_SPEC.finditer(layout.group(1))}
    if len(widths) > 1:
        raise Unsupported(
            f"the module's width model cannot be decided honestly: its datalayout states "
            f"the pointer widths {sorted(8 * width for width in widths)}"
        )
    if not widths:
        return LP64  # the default pointer of a layout that states none: LLVM's own
    width = widths.pop()
    if width not in (4, 8):
        raise Unsupported(
            f"the module's width model cannot be decided honestly: a {width * 8}-bit "
            "pointer (this pass places 32- and 64-bit words only)"
        )
    return DataModel(pointer=width)


def pointer_width(model: DataModel) -> int:
    """The bytes one pointer takes in the model's flat memory, or the honest refusal: a
    module that states no pointer width places no pointer word this pass can size (an
    ``i32`` still knows it is 4 bytes; only the pointer is undecided), so a store, a
    load or a cell that would need the width refuses instead of guessing it."""
    if model.pointer is None:
        raise Unsupported(
            "the width of a pointer of this module is not decided (its datalayout states "
            "none): this pass places 32- and 64-bit words and will not guess"
        )
    return model.pointer


# ---- the layout of the types a getelementptr walks -------------------------------------------


def split_top(body: str) -> list[str]:
    """The comma-separated items of a bracketed body, at the top level only.

    What is inside a ``[...]`` or ``{...}`` belongs to one item, so a field of a struct or
    an element of an array does not split its container; the same goes for the commas
    inside a byte string (``c"hello, world"`` is one item)."""
    parts: list[str] = []
    depth = 0
    quoted = False
    current: list[str] = []
    at = 0
    while at < len(body):
        char = body[at]
        if quoted:
            if char == "\\":
                current.append(char)
                at += 1
                if at < len(body):
                    current.append(body[at])
                at += 1
                continue
            if char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        if char == "," and depth == 0 and not quoted:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        at += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [part for part in parts if part]


def split_params(text: str) -> list[str]:
    """The top-level comma parts of a parameter list: an attribute may carry a type in
    parens (``sret(%struct.X)``, ``dereferenceable(8)``), which no comma ever enters."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in text:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return [part for part in (part.strip() for part in parts) if part]


def struct_body(ty: str, structs: dict[str, str]) -> tuple[list[str], bool] | None:
    """The (field types, packed) of a struct type (a ``{...}`` or ``<{...}>`` body, or a
    name that is one), else None. ``<{...}>`` is the packed form of LLVM (a pragma pack,
    a rustc repr(packed)): its fields carry no padding, and the type itself aligns like
    one byte -- the layout below follows what the differential's clang build gives."""
    ty = ty.strip()
    seen: set[str] = set()
    while ty.startswith("%") and ty in structs and ty not in seen:
        seen.add(ty)
        ty = structs[ty].strip()
    packed = ty.startswith("<{") and ty.endswith("}>")
    if packed:
        ty = ty[1:-1]  # the inner ``{...}`` of the packed form
    if ty.startswith("{") and ty.endswith("}"):
        inner = ty[1:-1].strip()
        return (split_top(inner) if inner else []), packed
    return None


def struct_fields(ty: str, structs: dict[str, str]) -> list[str] | None:
    """The field types of a struct type (a ``{...}`` body, or a name that is one), else None."""
    found = struct_body(ty, structs)
    return found[0] if found is not None else None


def struct_layout(
    fields: list[str], structs: dict[str, str], packed: bool = False, model: DataModel = UNDECIDED,
) -> tuple[int, int]:
    """The (size, align) of a struct of these fields, with the padding C gives it (a
    packed struct takes no padding and aligns like one byte)."""
    at = 0
    align_of = 1
    for member in fields:
        here = 1 if packed else type_align(member, structs, model)
        at = align(at, here)
        at += type_size(member, structs, model)
        align_of = max(align_of, here)
    return align(at, align_of), align_of


def field_offset(
    ty: str, index: int, structs: dict[str, str], model: DataModel = UNDECIDED
) -> tuple[int, str]:
    """The (byte offset, field type) of one field of a struct type."""
    body = struct_body(ty, structs)
    if body is None or not 0 <= index < len(body[0]):
        raise Unsupported(f"no field {index} of {ty}")
    fields, packed = body
    at = 0
    for position, member in enumerate(fields):
        here = 1 if packed else type_align(member, structs, model)
        at = align(at, here)
        if position == index:
            return at, member
        at += type_size(member, structs, model)
    raise Unsupported(f"no field {index} of {ty}")


def array_shape(ty: str) -> tuple[int, str] | None:
    """The (count, element type) of an ``[N x T]`` type, else None."""
    found = re.fullmatch(r"\[\s*(\d+)\s+x\s+(.+)\]", ty.strip())
    return (int(found.group(1)), found.group(2)) if found else None


def array_element(ty: str) -> str | None:
    """The element type of an ``[N x T]`` type, else None."""
    shape = array_shape(ty)
    return shape[1] if shape else None


def type_size(ty: str, structs: dict[str, str], model: DataModel = UNDECIDED) -> int:
    """The bytes a value of this type takes in memory (the stride of a getelementptr,
    the width of the cell a store writes): a ``ptr`` is the model's word, never the
    machine's. A caller that threads no model gets the undecided one -- integers size,
    a pointer refuses -- so LP64 is never a default guess over a 32-bit module."""
    ty = ty.strip()
    if ty == "ptr" or ty.endswith("*"):  # a typed pointer of older IR is one address word
        return pointer_width(model)
    scalar = re.fullmatch(r"i(\d+)", ty)
    if scalar:
        return (int(scalar.group(1)) + 7) // 8
    shape = array_shape(ty)
    if shape is not None:
        return shape[0] * type_size(shape[1], structs, model)
    body = struct_body(ty, structs)
    if body is not None:
        return struct_layout(body[0], structs, body[1], model)[0]
    raise Unsupported(f"the size of {ty} is unknown to the subset")


def type_align(ty: str, structs: dict[str, str], model: DataModel = UNDECIDED) -> int:
    """The alignment a value of this type takes in memory (the model's word for a
    pointer, as ``type_size`` sizes it)."""
    ty = ty.strip()
    if ty == "ptr" or ty.endswith("*"):
        return pointer_width(model)
    scalar = re.fullmatch(r"i(\d+)", ty)
    if scalar:
        size = (int(scalar.group(1)) + 7) // 8
        if size > 4 and model.pointer == 4:
            raise Unsupported(
                f"the alignment of {ty} in memory is not this model's to decide: an "
                "ILP32 ABI aligns a 64-bit scalar 4 or 8 (i386 and arm32 disagree), so a "
                "layout that needs it refuses instead of guessing one"
            )
        return max(1, min(size, 8))
    element = array_element(ty)
    if element is not None:
        return type_align(element, structs, model)
    body = struct_body(ty, structs)
    if body is not None:
        return struct_layout(body[0], structs, body[1], model)[1]
    raise Unsupported(f"the alignment of {ty} is unknown to the subset")


# ---- module globals as named data objects -------------------------------------------------------

def object_id_of(path: str) -> int:
    """The id under which the run finds an object: the first 8 bytes of the SHA-256 of its
    path, read little-endian -- the same number ``compose.object_id`` gives a data object
    that carries no id of its own. A graph references a global by this handle (an operand
    of kind Object resolves it to the payload address at run time)."""
    return int.from_bytes(hashlib.sha256(path.encode()).digest()[:8], "little")


# Global (a named data object of the import unit) lives in rxemit with the delta
# vocabulary: its ``cell`` answers the form the delta writes the cell with


def _read_constant(text: str) -> tuple[str, str]:
    """One constant of an initializer and the text after it (the attributes of the
    definition: ``align 8`` and kin, which are not the value)."""
    text = text.strip()
    if not text:
        return "", ""
    if text.startswith(("c\"", '"')):
        end = 1 if text.startswith('"') else 2
        while end < len(text):
            if text[end] == "\\":
                end += 2
                continue
            if text[end] == '"':
                return text[: end + 1], text[end + 1 :].strip()
            end += 1
        return text, ""
    if text[0] in "{[<":
        depth = 0
        for at, char in enumerate(text):
            if char in "{[<":
                depth += 1
            elif char in ">]}":
                depth -= 1
            if depth == 0:
                return text[: at + 1], text[at + 1 :].strip()
        return text, ""
    head = re.split(r"[,\s]", text, maxsplit=1)
    return head[0], (head[1] if len(head) > 1 else "").strip()


def _unescape(text: str) -> bytes:
    """The bytes of an LLVM byte string (``c"..."``): \\XX hex escapes and the rest literal."""
    out = bytearray()
    at = 0
    while at < len(text):
        if text[at] == "\\" and at + 2 < len(text):
            out.append(int(text[at + 1 : at + 3], 16))
            at += 3
        else:
            out.append(ord(text[at]))
            at += 1
    return bytes(out)


def item_value(item: str) -> str:
    """One element of a nested aggregate initializer: LLVM spells it ``<type> <value>``
    (``i64 10``, ``[8 x i64] [i64 1, ...]``), while a byte string (``c"..."``), a bare
    aggregate value and ``zeroinitializer`` carry no type of their own. The value alone is
    what the recursion of ``init_bytes`` takes."""
    text = item.strip()
    if text.startswith(("c\"", '"', "zeroinitializer", "undef", "poison", "null")):
        return text
    _, rest = split_type_init(text)
    return rest if rest else text


RE_SYMBOL = re.compile(r'@[A-Za-z0-9_.$-]+|@"[^"]+"')
# the slot a relocation can sit in: a scalar of the integer widths this pass stores words
# of, or any pointer (the typed pointers of older IR included)
RE_SLOT = re.compile(r"i(?:1|8|16|32|64)|ptr")


@dataclass
class Relocation:
    """One relocation of a global's initializer: the slot at byte ``slot`` of the cell
    holds the word of ``symbol`` (a function pointer in a callback struct, a pointer to
    another global), not bytes the compiler can write.

    The wire that places it is one store of that word into the slot -- the module's boot
    (``__wires_boot``) makes every one of them before anything runs -- and the cell is
    placed as its byte image with the slot zero until then, exactly like the native
    static storage before its loader has placed the relocations. What the pass cannot
    place refuses the cell (``Global.refusal``): a runtime symbol of the C++ machinery,
    a name the link cannot name, a slot the model's word does not fit."""

    slot: int  # the byte offset of the word inside the cell
    form: str  # the initializer text of the atom (the wire's value operand)
    symbol: str  # the ``@name`` the form names
    slot_type: str  # the type of the slot: the width the wire store writes


def init_bytes(
    ty: str, text: str, structs: dict[str, str], model: DataModel,
    relocs: list[Relocation], base: int = 0,
) -> bytes | None:
    """The initial payload of a global of this type, or None for an initializer outside
    the subset.

    An initializer that names a symbol -- ``@data = global %struct { i32, void (i8*)*
    @callback }`` -- is a relocation: its slot is placed as the zero of the byte image
    and the relocation is recorded (``relocs``) for the wire that fills it. Every other
    form outside the subset answers None: the cell refuses by name, never a guessed
    payload."""
    ty = ty.strip()
    text = text.strip()
    size = type_size(ty, structs, model)
    if text in ("", "zeroinitializer", "null", "undef", "poison"):
        return bytes(size)
    string = re.fullmatch(r'c"((?:[^"\\]|\\.)*)"', text)
    if string:
        found = _unescape(string.group(1))
        return found if len(found) == size else None
    number = re.fullmatch(r"-?\d+", text)
    if number:
        scalar = re.fullmatch(r"i(\d+)", ty)
        if not scalar:
            return None
        return int(number.group(0)).to_bytes(size, "little", signed=True)
    shape = array_shape(ty)
    if shape is not None and text.startswith("[") and text.endswith("]"):
        count, element = shape
        items = split_top(text[1:-1])
        # a zero-count array is the flexible array member at a struct's tail
        # (``qstr_pool_t``'s ``qstrs[]`` of py/qstr.h): the initializer's items are the
        # entries the cell's payload carries past the struct's own size
        if len(items) != count and not (count == 0 and len(items) > 0):
            return None
        out = bytearray()
        for item in items:
            at = base + len(out)
            made = init_bytes(element, item_value(item), structs, model, relocs, at)
            if made is None:
                return None
            out += made
        return bytes(out)
    fields = struct_fields(ty, structs)
    if fields is not None and text.startswith("{") and text.endswith("}"):
        items = split_top(text[1:-1])
        if len(items) != len(fields):
            return None
        out = bytearray(bytes(size))
        for index, item in enumerate(items):
            at, field_type = field_offset(ty, index, structs, model)
            made = init_bytes(field_type, item_value(item), structs, model, relocs, base + at)
            if made is None:
                return None
            out[at : at + len(made)] = made
        return bytes(out)
    # a relocation: the atom names the symbol whose word the slot holds (a function
    # pointer in a callback struct, a pointer to another global -- plain, or through a
    # constant expression of one). The slot is placed as the zero the byte image carries
    # and the wire of the boot fills it
    if RE_SLOT.fullmatch(ty) or ty.endswith("*"):
        symbol = RE_SYMBOL.search(text)
        if symbol is not None:
            slot_type = "ptr" if (ty == "ptr" or ty.endswith("*")) else ty
            relocs.append(Relocation(base, text, symbol.group(0), slot_type))
            return bytes(size)
    return None


def split_type_init(rest: str) -> tuple[str, str]:
    """The type of a global and its initializer: ``i64 7``, ``[6 x i8] c"hello\\00"``,
    ``{ i64, i32 } { i64 1, i32 2 }`` -- a leading balanced group is the aggregate type.
    A declaration carries no initializer at all: ``i64, align 8`` is the type and its
    attributes (the comma that ends the type is what says so)."""
    rest = rest.strip()
    if rest.startswith(("{", "[", "<")):
        depth = 0
        for at, char in enumerate(rest):
            if char in "{[<":
                depth += 1
            elif char in ">]}":
                depth -= 1
            if depth == 0:
                return rest[: at + 1], rest[at + 1 :].strip()
        return rest, ""
    head = rest.split(None, 1)
    if head[0].endswith(","):
        return head[0][:-1], ""  # the type ends in the comma: only attributes follow
    return head[0], (head[1] if len(head) > 1 else "").strip()


# ---- the textual LLVM IR ----------------------------------------------------------------------

VALUE = r"-?\d+|true|false|poison|undef|null|@[A-Za-z0-9_.$-]+|%[A-Za-z0-9_.$-]+"
# an LLVM identifier: the bare set, or the quoted form rustc prints for inlined names
# (``"_ZN4core...exit"``) with its ``\xx`` escapes. Both keep the text as printed, so a
# definition and every reference to it carry the same key
LABEL = r"(?:[A-Za-z0-9_.$-]+|\"(?:[^\"\\]|\\.)*\")"
# a constant expression of LLVM in an operand position: ``inttoptr (i64 N to ptr)`` is the
# address word N (the immediate objects of MicroPython's REPR A arrive this way), and a
# ``getelementptr`` of a global names that global (the subset imports no globals: the use
# refuses at lowering with the global's name, never a parse error of the whole module)
CONST_EXPR = (
    r"(?:inttoptr|addrspacecast|bitcast|ptrtoint|getelementptr(?:\s+inbounds)?)"
    r"\s*\((?:[^()]|\([^()]*\))*\)"
)
OPERAND = rf"(?:{CONST_EXPR}|{VALUE})"
# one argument of a call: a type, its parameter attributes (``noundef``, ``align 8``,
# ``dereferenceable(8)``, ...), and the operand -- anchored at the end so no attribute
# ever eats the operand that follows it
RE_ARG = re.compile(
    rf"^(?P<type>\S+)\s+(?:[a-z_]+(?:\([^)]*\))?(?:\s+\d+)?\s+)*(?P<opd>{OPERAND})$"
)
# a type of the subset (a return or a parameter): integers, ptr, named aggregates, and
# anything spelled close enough to parse and be refused later with a named error
TYPE = r"(?:void|ptr|i\d+|half|bfloat|float|double|x86_fp80|fp128|ppc_fp128|x86_mmx" r"|%[A-Za-z0-9_.$-]+|[^\s(]+)"
AGGREGATE = r"\{[^}]*\}"

RE_COMMENT = re.compile(r";.*$")
RE_DEFINE = re.compile(
    rf"^define\s+(?:(?P<attrs>.*?)\s+)?(?P<ret>{TYPE})\s+@(?:"  # a rustc name may be quoted
    rf'"(?P<qname>[^"]+)"|(?P<name>[A-Za-z0-9_.$-]+))'
    rf"\((?P<params>.*)\)[^{{]*\{{$"
)
RE_TYPEDEF = re.compile(
    r"^(?P<name>%[A-Za-z0-9_.$-]+)\s*=\s*type\s+(?P<body>\{.*\}|<.*>)$"
)
# a module-level global: ``@name = [prefix] global|constant <type> <initializer>``; a
# definition without an initializer (``external global i64``) records the type alone
RE_GLOBAL = re.compile(
    r'^(?P<name>@[A-Za-z0-9_.$-]+|@"[^"]+")\s*=\s*'
    r"(?:.*?\s+)?(?P<kind>global|constant)\s+(?P<rest>.+)$"
)
RE_LABEL = re.compile(rf"^(?P<name>{LABEL}):\s*$")
RE_ASSIGN = re.compile(r"^%(?P<name>[A-Za-z0-9_.$-]+)\s*=\s*(?P<rest>.+)$")
RE_BINOP = re.compile(
    rf"^(?P<op>add|sub|mul|udiv|sdiv|urem|srem|and|or|xor|shl|lshr|ashr)"
    rf"(?:\s+(?:nuw|nsw|exact|disjoint|nunw))*\s+"
    rf"(?P<type>[a-z][a-z0-9]*)\s+(?P<a>{OPERAND}),\s*(?P<b>{OPERAND})$"
)
RE_ICMP = re.compile(
    rf"^icmp\s+(?P<pred>[a-z]+)\s+(?P<type>[a-z][a-z0-9]*)\s+(?P<a>{OPERAND}),\s*(?P<b>{OPERAND})$"
)
RE_SELECT = re.compile(
    rf"^select\s+[a-z][a-z0-9]*\s+(?P<c>{OPERAND}),\s*"
    rf"(?P<type>[a-z][a-z0-9]*)\s+(?P<a>{OPERAND}),\s*[a-z][a-z0-9]*\s+(?P<b>{OPERAND})$"
)
RE_CAST = re.compile(
    rf"^(?P<op>zext|trunc|sext|inttoptr|ptrtoint)(?:\s+(?:nneg|nuw|nsw|exact))*\s+"
    rf"(?P<type>[a-z][a-z0-9]*)\s+(?P<a>{OPERAND})\s+to\s+(?P<to>[a-z][a-z0-9]*)$"
)
RE_PHI = re.compile(r"^phi\s+(?P<type>[a-z][a-z0-9]*)\s+(?P<pairs>.+)$")
RE_PAIR = re.compile(rf"\[\s*(?P<value>{OPERAND}),\s*%(?P<label>{LABEL})\s*\]")
RE_ALLOCA = re.compile(r"^alloca\s+(?P<type>.+?)(?:,\s*align\s+\d+)?$")
RE_LOAD = re.compile(
    rf"^load\s+(?:(?:volatile|atomic)\s+)*(?P<type>[a-z][a-z0-9]*),\s*ptr\s+(?P<p>{OPERAND})(?:,.*)?$"
)
RE_STORE = re.compile(
    rf"^store\s+(?:(?:volatile|atomic)\s+)*(?P<type>[a-z][a-z0-9]*)\s+(?P<v>{OPERAND}),\s*ptr\s+(?P<p>{OPERAND})(?:,.*)?$"
)
RE_GEP = re.compile(
    rf"^getelementptr(?:\s+inbounds)?(?:\s+(?:nuw|nsw))*\s+(?P<type>.+?),\s*ptr\s+"
    rf"(?P<base>{OPERAND})(?P<indices>.*)$"
)
RE_GEP_INDEX = re.compile(
    rf",\s*(?P<type>[a-z][a-z0-9]*)\s+(?P<idx>{VALUE})"
)
RE_CALL = re.compile(
    rf"^(?:(?:tail|musttail|notail)\s+)?call\s+(?:(?:fastcc|ccc|coldcc|cc\s+\d+|[a-z][a-z0-9_]*)\s+)*"
    # the return type is the instruction's ``type`` (a call of a program answers one: the
    # card's out width is what the callee's Return writes, so a pointer return is the
    # wide value both sides agree on)
    rf"(?P<type>{AGGREGATE}|{TYPE})(?:\s*\([^()]*\))?\s+@(?:"  # a call of a variadic function
    # prints its function type between the return type and the callee; a rustc callee
    # may be quoted as well
    rf'"(?P<qcallee>[^"]+)"|(?P<callee>[A-Za-z0-9_.$-]+))\((?P<args>.*)\)$'
)
RE_RET = re.compile(rf"^ret\s+(?P<type>[a-z][a-z0-9]*)(?:\s+(?P<v>{OPERAND}))?$")
RE_GOTO = re.compile(rf"^br\s+label\s+%(?P<target>{LABEL})$")
RE_BRANCH = re.compile(
    rf"^br\s+i1\s+(?P<c>{VALUE}),\s*label\s+%(?P<t>{LABEL}),\s*label\s+%(?P<f>{LABEL})$"
)
RE_SWITCH = re.compile(
    rf"^switch\s+(?P<type>[a-z][a-z0-9]*)\s+(?P<v>{VALUE}),\s*"
    rf"label\s+%(?P<default>{LABEL})\s*\[(?P<cases>.*)\]$"
)
RE_CASE = re.compile(rf"[a-z][a-z0-9]*\s+(?P<tag>-?\d+),\s*label\s+%(?P<label>{LABEL})")
RE_EXTRACTVALUE = re.compile(
    rf"^extractvalue\s+(?P<type>{AGGREGATE})\s+(?P<a>{VALUE}),\s*(?P<to>\d+)$"
)
RE_INSERTVALUE = re.compile(
    rf"^insertvalue\s+(?P<type>{AGGREGATE})\s+(?P<a>{VALUE}),\s*[a-z][a-z0-9]*\s+"
    rf"(?P<b>{VALUE}),\s*(?P<to>\d+)$"
)
RE_CALLEE = re.compile(r"^(?:(?:tail|musttail|notail)\s+)?call\b")
RE_INDIRECT = re.compile(
    rf"^(?:(?:tail|musttail|notail)\s+)?call\s+(?:[a-z][a-z0-9_]*\s+)*"
    # a call of a register: the callee is a function word in a value (the slot dispatch
    # of a type cell -- mp_unary_op's unary_op slot), not a symbol the line names
    rf"(?P<type>{AGGREGATE}|{TYPE})(?:\s*\([^()]*\))?\s+"
    rf"(?P<callee>[%@][A-Za-z0-9_.$-]+)\((?P<args>.*)\)$"
)
RE_DECLARE = re.compile(r"^declare\b.*@(?P<name>[A-Za-z0-9_.$-]+)\s*\(")
RE_EXCEPTION = re.compile(r"^(?:invoke\b|landingpad\b|resume\b|catchswitch\b|catchpad\b|cleanuppad\b)")
RE_METADATA = re.compile(r"(?:,\s*!\S+(?:\s+!\d+)?)+\s*$")
RE_ATTRGROUP = re.compile(r"\s+#\d+\s*$")
RE_ALIGN = re.compile(r",\s*align\s+\d+")


@dataclass
class Operand:
    """An operand of the IR: a name or a constant."""

    name: str | None = None
    constant: int | None = None
    expr: str | None = None  # a constant expression the subset refuses at lowering

    @staticmethod
    def parse(text: str) -> Operand:
        text = text.strip()
        if text.startswith("%"):
            return Operand(name=text[1:])
        if text.startswith("@"):
            # a module-level global (a panic message, a typeinfo): it parses here so the
            # refusal names the use -- or the refused runtime call around it is reported
            # first -- and never becomes a value of the subset
            return Operand(name=text)
        if text in ("true", "false"):
            return Operand(constant=1 if text == "true" else 0)
        if text in ("poison", "undef", "null"):
            # an unspecified value of LLVM: any deterministic one keeps the contract (a
            # program that observes it is outside the deterministic C the subset imports),
            # and zero is what a frame starts with (and what a null pointer addresses)
            return Operand(constant=0)
        if re.fullmatch(r"-?\d+", text):
            return Operand(constant=int(text))
        if text.startswith(("inttoptr", "addrspacecast", "bitcast", "ptrtoint", "getelementptr")):
            return Operand.parse_expr(text)
        # an expression this pass will not resolve (the use refuses at lowering with the
        # expression named); anything parses here so one construct never kills a module
        return Operand(expr=text)

    @staticmethod
    def parse_expr(text: str) -> Operand:
        """A constant expression of LLVM. ``inttoptr (i64 N to ptr)`` is the address word
        N (the immediate objects of MicroPython's REPR A are spelled this way), and a cast
        of a name is that name. A ``getelementptr`` carries its text: the lowering resolves
        it to the cell of its base plus the bytes its constant indices walk (``ctx.top``,
        ``msg[3]``: the constant field offsets of every C static). The rest carries its
        text too: the lowering refuses it by name, never a guess."""
        if text.startswith(("inttoptr", "addrspacecast", "bitcast")):
            found = re.fullmatch(
                rf"(?:inttoptr|addrspacecast|bitcast)\s*\(\s*[a-z]\d*\s+({VALUE})\s+to\s+ptr\s*\)",
                text,
            )
            if found:
                return Operand.parse(found.group(1))
        return Operand(expr=text)


@dataclass
class Inst:
    op: str
    name: str | None = None
    type: str | None = None
    raw: str | None = None
    a: Operand | None = None
    b: Operand | None = None
    c: Operand | None = None
    pred: str | None = None
    to: str | None = None
    callee: str | None = None
    args: list[tuple[str, Operand]] = field(default_factory=list)
    incoming: list[tuple[Operand, str]] = field(default_factory=list)
    indices: list[tuple[str, Operand]] = field(default_factory=list)  # getelementptr steps
    target: str | None = None
    if_true: str | None = None
    if_false: str | None = None
    default: str | None = None
    cases: list[tuple[int, str]] = field(default_factory=list)
    diverges: bool = False  # a call directly before an unreachable: it lowers to nothing


@dataclass
class Block:
    name: str
    insts: list[Inst] = field(default_factory=list)

    @property
    def terminator(self) -> Inst:
        return self.insts[-1]


@dataclass
class Function:
    name: str
    params: list[tuple[str, str]]  # (type, name)
    ret: str
    blocks: list[Block]
    sret: bool = False  # one parameter is the sret pointer (a by-value aggregate return)
    byval: bool = False  # one parameter is a byval copy (a by-value aggregate parameter)
    variadic: bool = False  # a C variadic definition: its callers pass arguments unnamed


def _strip(line: str) -> str:
    line = RE_COMMENT.sub("", line)
    line = RE_METADATA.sub("", line)
    line = RE_ATTRGROUP.sub("", line)
    return RE_ALIGN.sub("", line).strip()


def _strip_comment(line: str) -> str:
    """A line without its trailing comment -- a ``;`` inside a byte-string of an
    initializer kept, since the message bytes are the value there."""
    out: list[str] = []
    quoted = False
    at = 0
    while at < len(line):
        char = line[at]
        if char == '"':
            quoted = not quoted
        elif char == "\\" and quoted and at + 1 < len(line):
            out.append(line[at : at + 2])
            at += 2
            continue
        elif char == ";" and not quoted:
            break
        out.append(char)
        at += 1
    return "".join(out).strip()


def _instruction(text: str, name: str | None) -> Inst:
    """One instruction line as an Inst."""
    if text == "unreachable":
        return Inst("unreachable", name=name)
    for regex, op in (
        (RE_BINOP, "binop"),
        (RE_ICMP, "icmp"),
        (RE_SELECT, "select"),
        (RE_CAST, "cast"),
        (RE_PHI, "phi"),
        (RE_ALLOCA, "alloca"),
        (RE_GEP, "gep"),
        (RE_LOAD, "load"),
        (RE_STORE, "store"),
        (RE_EXTRACTVALUE, "extractvalue"),
        (RE_INSERTVALUE, "insertvalue"),
        (RE_CALL, "call"),
        (RE_RET, "ret"),
        (RE_GOTO, "goto"),
        (RE_BRANCH, "branch"),
        (RE_SWITCH, "switch"),
    ):
        found = regex.match(text)
        if not found:
            continue
        groups = found.groupdict()
        inst = Inst(groups.get("op") or op, name=name)
        for key in ("type", "pred", "to", "callee", "target", "default"):
            if groups.get(key):
                setattr(inst, key, groups[key])
        if op in ("binop", "icmp"):
            inst.a, inst.b = Operand.parse(groups["a"]), Operand.parse(groups["b"])
        elif op == "select":
            inst.c = Operand.parse(groups["c"])
            inst.a, inst.b = Operand.parse(groups["a"]), Operand.parse(groups["b"])
        elif op == "cast":
            inst.a = Operand.parse(groups["a"])
        elif op == "gep":
            inst.a = Operand.parse(groups["base"])
            inst.indices = [
                (index.group("type"), Operand.parse(index.group("idx")))
                for index in RE_GEP_INDEX.finditer(groups["indices"])
            ]
        elif op == "phi":
            inst.incoming = [
                (Operand.parse(pair["value"]), pair["label"])
                for pair in RE_PAIR.finditer(groups["pairs"])
            ]
            if not inst.incoming:
                raise ParseError(f"a phi without incoming values: {text!r}")
        elif op == "load":
            inst.a = Operand.parse(groups["p"])
        elif op == "store":
            inst.a = Operand.parse(groups["v"])
            inst.b = Operand.parse(groups["p"])
        elif op == "extractvalue":  # the field index rides in `to`, the field type in `type`
            inst.a = Operand.parse(groups["a"])
        elif op == "insertvalue":
            inst.a, inst.b = Operand.parse(groups["a"]), Operand.parse(groups["b"])
        elif op == "call":
            if inst.callee is None:  # a quoted callee (rustc's mangled internal names)
                inst.callee = groups.get("qcallee")
            for argument in split_params(groups["args"]):
                found = RE_ARG.match(argument)
                if not found:
                    raise ParseError(f"an argument outside the subset: {argument!r}")
                inst.args.append((found.group("type"), Operand.parse(found.group("opd"))))
        elif op == "ret":
            inst.a = Operand.parse(groups["v"]) if groups.get("v") else None
        elif op == "branch":
            inst.c = Operand.parse(groups["c"])
            inst.if_true, inst.if_false = groups["t"], groups["f"]
        elif op == "switch":
            inst.a = Operand.parse(groups["v"])
            inst.cases = [(int(m["tag"]), m["label"]) for m in RE_CASE.finditer(groups["cases"])]
        return inst
    if RE_EXCEPTION.match(text):
        return Inst("exception", name=name, raw=text)  # an edge or pad of a C++ exception
    if RE_CALLEE.match(text):
        found = RE_INDIRECT.match(text)
        if found is None:
            return Inst("indirect", name=name, raw=text)  # a callee no pattern names
        inst = Inst(
            "indirect", name=name, type=found.group("type"), callee=found.group("callee")
        )
        for argument in split_params(found.group("args")):
            arg = RE_ARG.match(argument)
            if not arg:
                raise ParseError(f"an argument outside the subset: {argument!r}")
            inst.args.append((arg.group("type"), Operand.parse(arg.group("opd"))))
        return inst
    return Inst("unknown", name=name, raw=text)  # the lowering raises Unsupported if asked


def declared_functions(text: str) -> set[str]:
    """The names the module's ``declare`` lines carry: a function whose body lives
    elsewhere (an ABI face of the port or another module), whose address may still be a
    word a store wires into a cell (the slot of a type object names its body that way)."""
    return {
        found.group("name")
        for line in text.splitlines()
        if (found := RE_DECLARE.match(_strip(line))) is not None
    }


def function_wiring(
    parsed: list[Function], module: str, only: set[str] | None,
    declared: set[str], known: dict[str, str], model: DataModel,
) -> dict[int, str]:
    """The function words one link makes memory carry, to the Function path whose body
    each is: every store of a function word by the bodies of the module (and the
    cross-module words ``known`` names).  The wiring universe is a **link fact** -- the
    store that fills a slot names the body whether or not this import lowers that body
    (a boot of the module fills the slots of the type cells before anything runs), so
    the arms of a dispatch cover the words the whole translation unit wires.

    The word is the model's (``rxemit.DataModel``): the object id of the body's path
    masked to the pointer width the module's datalayout states, so a dispatch compares
    the words the slots of that model carry. Two bodies whose words collide in it refuse
    the link here -- the model cannot tell the words apart, and a dispatch that routed
    on them would guess.

    These words are the arms of a call through a register (lower_indirect's slot
    dispatch): a word a store wires routes to the call card of the body it names; a
    word no store of the link wires is carried as data (a compare against it) and
    dispatches to no card -- what arrives as a caller's argument is wired nowhere this
    link can name (tests/import_corpus/dispatch.c's ``call_a_word``), and stays refused
    with the function-word rule."""
    functions = {function.name: function for function in parsed}
    wired: dict[int, str] = {}

    def word_of(path: str) -> int:
        # the model's word of the body's id: the width is asked only where a store
        # actually wires one (a module that states no width wires none this pass can size)
        return object_id_of(path) & ((1 << (8 * pointer_width(model))) - 1)

    for found in functions.values():  # every body of the link unit, the boot's stores included
        for block in found.blocks:
            for inst in block.insts:
                if inst.op != "store" or inst.a is None or not inst.a.name:
                    continue
                if not inst.a.name.startswith("@"):
                    continue
                bare = inst.a.name[1:]
                if bare in known:
                    path = known[bare]
                elif bare in functions:
                    path = f"{module}.{bare}"
                elif bare in declared:
                    # an ABI face's word is data (the compare of a slot test), no arm
                    continue
                else:
                    continue
                word = word_of(path)
                if wired.get(word, path) != path:
                    raise Unsupported(
                        f"the function words of {wired[word]} and {path} collide in the "
                        f"{pointer_width(model) * 8}-bit word of this model: a dispatch "
                        "could not tell them apart, so the link refuses instead of routing"
                    )
                wired[word] = path
    return wired


# the boot of the module: where the wires of the initializers' relocations are made
WIRES_BOOT = "__wires_boot"
# a relocation whose target this round of the fixpoint cannot decide yet
WAIT = object()


def wire_inst(cell: str, rel: Relocation) -> Inst:
    """The wire of one relocation: the store that places the word the initializer names
    into the slot it sits in (the module's boot makes them all before anything runs --
    the loader's work the ``obj_port_boot``/``qstr_port_boot`` of the corpus does by
    hand, here from the initializer itself)."""
    if "getelementptr" in rel.form:
        value = Operand(expr=rel.form)  # the target cell's address plus the constant walk
    else:
        value = Operand(name=rel.symbol)  # a function word, or a cell's address word
    destination = (
        Operand(name=cell)
        if not rel.slot
        else Operand(expr=f"getelementptr (i8, ptr {cell}, i64 {rel.slot})")
    )
    return Inst("store", type=rel.slot_type, a=value, b=destination)


def wires_boot(wires: list[tuple[str, Relocation]]) -> Function:
    """The boot of the module: one store per relocation of the module's initializers,
    then the void return. It runs before anything else (the loader's relocation pass a
    port's boot is), and ``function_wiring`` takes its stores like any body's: the arms
    of a dispatch cover the words the boot wires."""
    insts = [wire_inst(cell, rel) for cell, rel in wires]
    insts.append(Inst("ret", type="void"))
    return Function(WIRES_BOOT, [], "void", [Block("entry", insts)])


def place_relocations(
    globals_: dict[str, Global],
    relocs_of: dict[str, list[Relocation]],
    functions: dict[str, Function],
    declared: set[str],
    known: dict[str, str],
    structs: dict[str, str],
    model: DataModel,
) -> dict[str, str]:
    """Why each cell's relocations this pass cannot place; every cell this answer does
    not name is placed (its byte image, the relocation slots zero, the wires of the boot
    filling them).

    One fixpoint over the cells the relocations name: a cell whose target is refused is
    refused in turn (``a relocation to @t: ...``), and a cycle of cells that only wait
    on each other is placed (the words its wires store are the wires' own). The refusals
    are the story, never a dropped relocation: a runtime symbol of the C++ machinery, a
    name the link cannot name, a slot the model's word does not fit, or a module that
    states no pointer width at all."""

    def one(rel: Relocation) -> object:
        symbol = rel.symbol
        bare = symbol[1:].strip('"')
        if is_runtime_symbol(bare):
            return (
                f"a relocation of a runtime symbol ({symbol}: the C++ exception/RTTI "
                "machinery is refused by policy)"
            )
        if model.pointer is None:
            return (
                f"a relocation of {symbol} (the width of a pointer of this module is not "
                "decided: its datalayout states none, and this pass places 32- and "
                "64-bit words and will not guess)"
            )
        size = type_size(rel.slot_type, structs, model)
        if not model.pointer <= size <= 8:
            return (
                f"a relocation into a {rel.slot_type} slot of {size} bytes (the word is "
                f"{model.pointer * 8} bits here, and a store moves at most 8)"
            )
        if rel.slot % model.pointer:
            return (
                f"a relocation at byte {rel.slot} of its cell (a word of this model is "
                f"{model.pointer} bytes and its wire store places that word whole, at a "
                "word of the cell: a slot that starts elsewhere cannot carry one)"
            )
        if symbol in globals_:
            target = globals_[symbol]
            if symbol in refused:
                return f"a relocation to {symbol} ({refused[symbol]})"
            if symbol in placed or (target.init is not None and not relocs_of.get(symbol)):
                return ""
            if not relocs_of.get(symbol):
                return f"a relocation to {symbol} ({target.refusal or 'a declaration without bytes'})"
            return WAIT  # the target's own relocations this round cannot decide yet
        if bare in functions or bare in declared or bare in known:
            return ""
        return f"a relocation of {symbol} (not a name of this module or its link)"

    refused: dict[str, str] = {}
    placed: set[str] = set()
    while True:
        grew = False
        for key, rels in sorted(relocs_of.items()):
            if key in refused or key in placed or not rels:
                continue
            outcome: object = ""
            for rel in rels:
                outcome = one(rel)
                if outcome is WAIT or outcome:
                    break
            if outcome is WAIT:
                continue  # the next round decides it (or never does: a placed cycle)
            grew = True
            if outcome:
                refused[key] = str(outcome)
            else:
                placed.add(key)
        if not grew:
            return refused


def crosses_a_card(function: Function, functions: dict[str, Function]) -> bool:
    """Whether a body crosses a call-card boundary: a call of a program of its own (no
    leaf, no inline of the module) or a call through a register (the dispatch of i3).
    What such a body writes through a pointer parameter is an out position (docs/23 4.1)
    -- no raw pointer outlives the step (S7) -- and what a leaf writes through one is an
    address into flat memory (the landed profile the ``memory.c`` cases keep)."""
    seen: set[str] = set()
    pending = [function.name]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        found = functions.get(name)
        if found is None:
            continue
        for block in found.blocks:
            for position, inst in enumerate(block.insts):
                if inst.op == "call_r":
                    return True
                callee = inst.callee
                if inst.op != "call" or callee is None:
                    continue
                if (
                    position + 1 < len(block.insts)
                    and block.insts[position + 1].op == "unreachable"
                ):
                    continue  # a diverging call lowers to nothing (lower_call's shape)
                if (
                    callee in (NLR_PUSH, NLR_POP, NLR_JUMP)
                    or callee.startswith("llvm.")
                    or leaf_key(callee) is not None
                    or is_panic_callee(callee)
                    or is_runtime_callee(callee)
                ):
                    continue
                if callee in functions:
                    pending.append(callee)  # inlined: its cards are this body's
                else:
                    return True
    return False


def parse_module(
    text: str, known: dict[str, str] | None = None,
) -> tuple[list[Function], dict[str, str], dict[str, Global]]:
    """The ``define`` functions of a textual LLVM module, its named struct types, and its
    module-level globals (the named data objects of the import unit).

    A global whose initializer carries an address is a relocation: its cell is placed as
    the byte image with the slot zero, and the store that fills it is one wire of the
    module's boot (``__wires_boot``, appended here when the module has wires). What the
    pass cannot place refuses the cell with its reason (``Global.refusal``)."""
    model = module_model(text)  # the width model of the module's words (the datalayout)
    found: list[Function] = []
    structs: dict[str, str] = {}
    globals_: dict[str, Global] = {}
    pending: list[tuple[str, Global, str]] = []  # resolved once every typedef is known
    raw_lines = text.splitlines()  # unstripped: a byte-string may hold a ``;`` of its own
    lines = [_strip(line) for line in text.splitlines()]
    at = 0
    while at < len(lines):
        line = lines[at]
        if not line:
            at += 1
            continue
        header = RE_DEFINE.match(line)
        if header is None and line.startswith("define"):
            # a header the IR writer wrapped past its column limit: join until the
            # line the body's { ends on, then read it as one header
            while not line.rstrip().endswith("{"):
                at += 1
                if at >= len(lines):
                    raise ParseError(f"a function header without its body: {line!r}")
                line += " " + lines[at].strip()
            header = RE_DEFINE.match(line)
            if header is None:
                raise ParseError(f"not a function header of the subset: {line!r}")
        if header is None:
            # not a function: a module-level global (a named data object of the import
            # unit), a struct typedef, or a declare (an intrinsic or a leaf): not imported
            global_line = _strip_comment(raw_lines[at] if at < len(raw_lines) else line)
            found_global = RE_GLOBAL.match(global_line)
            if found_global is not None:
                name = found_global.group("name")
                ty, init_text = split_type_init(found_global.group("rest"))
                init_text, _dropped_attrs = _read_constant(init_text)  # drop ``align N``
                made = Global(
                    name=name[1:],
                    type=ty,
                    init=None,
                    constant=found_global.group("kind") == "constant",
                )
                if init_text:  # the bytes resolve once every typedef of the module is known
                    pending.append((name, made, init_text))
                globals_[name] = made
                at += 1
                continue
            typedef = RE_TYPEDEF.match(line)
            if typedef is not None:
                structs[typedef.group("name")] = typedef.group("body")
            at += 1  # a declare (an intrinsic or a leaf) or anything else: not imported
            continue
        params = []
        sret = False
        byval = False
        variadic = False
        for part in split_params(header.group("params")):
            if part.strip() == "...":
                # the ellipsis of a C variadic definition: no value of its own (the body
                # refuses at lowering -- its callers pass arguments this pass cannot map)
                variadic = True
                continue
            words = part.split()
            if not words[-1].startswith("%"):
                raise ParseError(f"an unnamed parameter: {part!r}")
            for word in words:
                if word.startswith("sret("):
                    sret = True
                elif word.startswith("byval("):
                    byval = True
            ty = words[0]
            if ty.endswith("*"):  # a typed pointer of older IR is the same address word
                ty = "ptr"
            params.append((ty, words[-1][1:]))
        function = Function(
            header.group("name") or header.group("qname"),
            params,
            header.group("ret"),
            [],
            sret,
            byval,
            variadic,
        )
        current = Block(name="entry")
        function.blocks.append(current)
        at += 1
        while at < len(lines) and lines[at] != "}":
            line = lines[at]
            if not line:
                at += 1
                continue
            while at + 1 < len(lines) and (
                line.count("[") > line.count("]")  # a wrapped phi/switch
                or line.count("(") > line.count(")")  # a wrapped call: its args split
            ):
                at += 1
                line += " " + lines[at]
            label = RE_LABEL.match(line)
            if label:
                if len(function.blocks) == 1 and not function.blocks[0].insts:
                    # rustc labels its entry block (``start:``): the synthetic entry block
                    # of the parser is that block, not an empty one before it
                    function.blocks[0].name = label.group("name")
                    current = function.blocks[0]
                else:
                    current = Block(name=label.group("name"))
                    function.blocks.append(current)
            else:
                assigned = RE_ASSIGN.match(line)
                if assigned:
                    current.insts.append(_instruction(assigned.group("rest"), assigned.group("name")))
                else:
                    current.insts.append(_instruction(line, None))
            at += 1
        at += 1
        for block in function.blocks:
            if not block.insts:
                raise ParseError(f"{function.name}: {block.name} is empty")
        found.append(function)
    relocs_of: dict[str, list[Relocation]] = {}
    for key, made, init_text in pending:
        # the bytes, and the relocations the initializer carries (an address the
        # compiler cannot write: a function pointer in a callback struct, a pointer to
        # another global). A None here is an initializer outside the subset: the use of
        # the global refuses by name at lowering, never a guessed payload
        relocs: list[Relocation] = []
        made.init = init_bytes(made.type, init_text, structs, model, relocs)
        relocs_of[key] = relocs if made.init is not None else []
    functions = {function.name: function for function in found}
    refused = place_relocations(
        globals_, relocs_of, functions, declared_functions(text), known or {}, structs, model
    )
    for key, cell in globals_.items():
        why = refused.get(key)
        if why is not None:
            cell.init = None  # no payload this pass can place: the reason says which
            cell.refusal = why
    wires = [
        (key, rel) for key, rels in sorted(relocs_of.items()) if key not in refused
        for rel in rels
    ]  # fmt: skip
    if wires:
        if WIRES_BOOT in functions:
            raise ParseError(
                f"the module defines its own {WIRES_BOOT}: the wires of its initializers' "
                "relocations have no boot to make them"
            )
        found.append(wires_boot(wires))
    return found, structs, globals_


# ---- the semantic model the emitter writes ---------------------------------------------------
# the model itself (LValue, LOp, LBlock, LGraph) lives in rxemit with the writers; this
# importer fills it below and the one shared emitter writes it as the delta


class BlockLeaves:
    """One block of a Lowering as the emitter's shared helpers take it (``rxemit.Leaves``).

    The narrow-integer and ashr helpers have one home (rxemit, for both lowerings) and
    make their leaf calls through ``call(key, inputs, input_types)`` and ``literal``;
    this importer's call names its block first, so ``Lowering.at`` binds one block."""

    def __init__(self, lowering: Lowering, target: LBlock) -> None:
        self.lowering = lowering
        self.target = target

    def call(
        self, key: str, inputs: list[MOperand], input_types: list[str] | None = None
    ) -> MOperand:
        """One leaf call into the bound block."""
        return self.lowering.call(self.target, key, inputs, input_types=input_types)

    def literal(self, constant: int) -> MOperand:
        """The Literal object of a constant (the Lowering's own, one per constant)."""
        return self.lowering.literal(constant)


class Lowering:
    """One function of the IR as a graph of blocks, operations and values."""

    def __init__(
        self,
        function: Function,
        module: str = "imported",
        functions: dict[str, Function] | None = None,
        stack: tuple[str, ...] = (),
        host: Lowering | None = None,
        tag: str = "",
        structs: dict[str, str] | None = None,
        globals: dict[str, Global] | None = None,
        known: dict[str, str] | None = None,
        declared: set[str] | None = None,
        wiring: dict[int, str] | None = None,
        model: DataModel | None = None,
    ) -> None:
        self.fn = function
        self.module = module
        self.functions = functions or {}
        # the width model of the module's words (rxemit.DataModel, from its datalayout):
        # what a pointer -- and a function word -- is in the flat memory
        self.model = host.model if host is not None else (model or LP64)
        self.structs = host.structs if host is not None else (structs or {})
        self.globals = host.globals if host is not None else (globals or {})
        # the names the module declares but does not define (an ABI face whose body
        # lives elsewhere): their addresses may be words a store wires into a cell
        self.declared: set[str] = host.declared if host is not None else (declared or set())
        # the function words this import makes memory carry (every store of one by the
        # bodies it lowers) to the Function path whose body it is: the arms of a call
        # through a register (lower_indirect's slot dispatch)
        self.wiring: dict[int, str] = host.wiring if host is not None else (wiring or {})
        # the C name of a cross-module callee to the Function path it is imported at
        # (a call card names its callee by that path, the reference the delta links):
        # what a prior import_into of the image answered, carried to the calls this
        # module makes of those functions
        self.known: dict[str, str] = host.known if host is not None else (known or {})
        self.global_values: dict[str, LValue] = host.global_values if host else {}
        self.object_paths: set[str] = host.object_paths if host else set()
        # the paths of every Object value (a global's cell): where the bytes of an operand
        # are read (a move, an edge's argument) such a value is the payload address first
        self.current: LBlock | None = host.current if host else None
        # the block being lowered now: a constant expression that names a cell (the
        # getelementptr of a global) is resolved into it as one wrapping add
        self.stack = stack  # the functions being inlined around this one (for recursion)
        self.path = f"{module}.{function.name}"
        self.graph = host.graph if host else f"{self.path}.{function.name}_graph"
        self.signature = f"{self.path}.signature"
        self.tag = tag  # the blocks of an inlined body take the call's name
        self.values: list[LValue] = host.values if host else []
        self.literals: dict[int, LValue] = host.literals if host else {}
        self.blocks: list[LBlock] = host.blocks if host else []
        self.entry: LBlock | None = None
        self.names: dict[str, MOperand] = {}
        self.params: list[LValue] = []
        self.pointers: set[str] = set()  # names that hold an address (ptr params and results)
        self.ptr_params: set[str] = set()  # the pointer parameters: a pointer to a caller's value
        self.stored_params: set[str] = set()  # the pointer params written through (an out cell)
        self.out_cells: dict[str, LValue] = {}  # the out cell of each written-through param
        self.out_alias: dict[str, str] = {}  # caller local -> the out cell key it reads
        self.standalone = False  # a program of its own (an inline call takes its params)
        self.crosses_card = False  # this body crosses a call-card boundary (prescan)
        self.card_calls: bool = False  # whether the body makes a call card (a program of its own)
        self.narrow_names: set[str] = set()  # the allocas whose address stays inside the function
        self.wide_names: set[str] = set()  # the allocas whose address reaches a memory leaf
        self.wide: dict[str, LValue] = {}  # those locals, by alloca name
        self.wide_of: dict[str, LValue] = {}  # the same locals, by the path of their value
        self.nlr_buffers: set[str] = set()  # the setjmp buffers of nlr_push/nlr_pop calls
        self.dropped: set[str] = set()  # the dropped statuses of leaf calls
        self.aggregates: dict[str, tuple[MOperand, ...]] = {}
        # aggregate values ({ i64, i1 } and kin), by the name of the instruction that made
        # them: they live inside one lowering (they never cross a call), so no sharing
        self.slot_stores: dict[str, set[str]] = {}  # local -> the blocks that store to it
        self.block_stored: set[str] = set()  # locals stored so far in the block being lowered
        self.source_block: str = ""  # the block of the function being lowered now
        self.function_results: list[MOperand] = []  # what the Return terminators answer
        self.flow_edges: dict[str, list[str]] = {}  # source block -> the labels it flows to
        self.block_of: dict[str, LBlock] = {}
        self.incoming: dict[tuple[str, str], list[Operand]] = {}  # (successor, predecessor)
        self.op_count = 0
        # one shared scratch wide slot per frame (every lowering of the frame sees the
        # same one-cell holder): the staging a load of the flat space needs, made once
        self.load_scratch: list[MOperand] = host.load_scratch if host else []
        # the values that ARE slots (a wide local, the load staging): a leaf given one
        # receives its own address (``pointer`` of native/graph.c), where a word is the
        # flat address it carries -- harmonize_widths keeps the flavors apart
        self.slots: set[str] = host.slots if host else set()
        # the values already standing as some block's condition: the image binds one
        # value to one field, so a second branch on the same flag takes a copy
        self.conditions: set[str] = host.conditions if host else set()

    # ---- names -------------------------------------------------------------------------------

    def value(
        self,
        kind: str,
        parent: str,
        name: str,
        *,
        owner_block: str | None = None,
        owner_op: str | None = None,
        literal: int | None = None,
        width: int = 8,
        type_path: str = U64,
        object_id: int | None = None,
        payload: bytes | None = None,
    ) -> LValue:
        made = LValue(
            f"{parent}.{name}", kind,
            owner_block=owner_block, owner_op=owner_op,
            literal=literal, width=width, type_path=type_path,
            object_id=object_id, payload=payload,
        )  # fmt: skip
        self.values.append(made)
        return made

    def literal(self, constant: int) -> MOperand:
        constant &= FULL
        if constant not in self.literals:
            self.literals[constant] = self.value(
                "Literal", self.graph, f"const_{constant:x}", literal=constant
            )
        return ("literal", constant)

    def mask_of(self, type_name: str | None) -> int:
        """What a value of this type is: the low bits of its word."""
        if type_name is None or type_name not in MASKS:
            raise Unsupported(f"{self.fn.name}: a value of {type_name}, not an integer")
        return MASKS[type_name]

    def bytes_of(self, type_name: str | None) -> int:
        """The bytes a value of this type takes in memory: 1, 2, 4 or 8 (a ptr is the
        model's word -- 8 of an LP64 module, 4 of an ILP32 one)."""
        return (self.word_of(type_name).bit_length() + 7) // 8

    def word_of(self, type_name: str | None) -> int:
        """The full word a value of this type occupies. A ``ptr`` is an address word --
        the model's word (``rxemit.DataModel``): 64 bits where the module's datalayout
        says so, 32 of an ILP32 one, and the honest refusal where a module states no
        pointer width (nothing places such a word this pass can size). A load masks the
        frame word to it and a store writes its bytes. An integer is the low bits of its
        width."""
        if type_name == "ptr":
            return (1 << (8 * pointer_width(self.model))) - 1
        return self.mask_of(type_name)

    def function_word(self, name: str) -> int:
        """The word a function's words are named by: the object id of its Function path
        (a body of this module is ``module.name``, a cross-module one the path ``known``
        carries), masked to the model's word (the slot that holds one is a pointer of
        that width). A type cell's slot holds one -- the unary_op slot of the @mp_type_*
        cells carries the address of the per-type body -- and a call through a register
        dispatches on it (lower_indirect compares the callee word against the words this
        import makes memory carry)."""
        bare = name.removeprefix("@")
        return object_id_of(self.known.get(bare, f"{self.module}.{bare}")) & self.word_of("ptr")

    def global_value(self, name: str) -> MOperand:
        """The value that is the module-level global's cell: one value of kind Object with
        the global's ``object_id``, so the run resolves it to the payload address of the
        named cell in the arena (the C static storage the global is). Every graph that
        touches the global carries this value -- the delta emits the cell itself."""
        found = self.global_values.get(name)
        if found is None:
            cell = self.globals.get(name)
            made = self.value(
                "Object", self.graph, f"global_{name[1:]}",
                type_path=U64, object_id=object_id_of(f"{self.module}.{name[1:]}"),
                payload=cell.init if cell is not None else None,
            )  # fmt: skip
            self.global_values[name] = made
            self.object_paths.add(made.path)
            found = made
        return ("value", found.path)

    def crosses_a_card(self) -> bool:
        """Whether this body crosses a call-card boundary: a call of a program of its own
        (no leaf, no inline of the module) or a call through a register (the dispatch).
        What such a body writes through a pointer parameter is an out cell (docs/23 4.1);
        a leaf writes at the address its pointer carries (the landed profile)."""
        return crosses_a_card(self.fn, self.functions)

    def out_position(self, name: str) -> bool:
        """Whether a pointer parameter is an out position (docs/23 4.1): a body of its
        own that crosses a card boundary writes through the Object its caller passes (an
        out cell it holds the handle of) -- no raw pointer outlives a step (S7). A leaf's
        pointer is an address into flat memory: the landed profile of loads and stores at
        a named address stays (the ``memory.c`` cases)."""
        return self.standalone and self.crosses_card and name in self.stored_params

    def card_out_param(self, card: str, index: int) -> bool:
        """Whether the parameter at ``index`` of the program a card names is an out
        position (docs/23 4.1): the body writes through it and crosses a card boundary
        of its own. Such an argument takes the Object (the out cell's handle -- three
        words), never a raw pointer to a caller's value (S7)."""
        bare = card.rsplit(".", 1)[-1]
        callee = self.functions.get(bare)
        if callee is None or index >= len(callee.params) or callee.params[index][0] != "ptr":
            return False
        name = callee.params[index][1]
        writes = any(
            inst.op == "store" and inst.b is not None and inst.b.name == name
            for block in callee.blocks
            for inst in block.insts
        )
        return writes and crosses_a_card(callee, self.functions)

    def out_cell(self, key: str, index: int) -> LValue:
        """The out cell of the out position at parameter ``index`` of the program ``key``
        names (docs/23 4.1): the Object whose handle the call passes (its three words) and
        whose out word -- at the handle's offset -- the callee writes and the caller reads
        back. The cell is named after the callee and its parameter, so a call card and the
        Function it names carry the same cell (one object id, one payload): what lands is
        the caller's, not the callee's copy."""
        made = self.out_cells.get(key)
        if made is None:
            identity = object_id_of(key)
            made = self.value(
                "Object", self.graph, key.replace(".", "_"),
                width=24, type_path=WIDE, object_id=identity,
                # the handle the parameter takes: (identity, generation, offset) over the
                # payload, the out word sitting at the offset the handle names
                payload=struct.pack("<3Q", identity, 1, 24) + bytes(8),
            )  # fmt: skip
            self.out_cells[key] = made
            self.object_paths.add(made.path)
        return made

    def out_word(self, target: LBlock, key: str, index: int) -> MOperand:
        """Where the out cell's out word sits: the Object resolved at each use (the run's
        ``pointer`` of the operand is the payload address) plus the offset its handle
        carries -- the landing of the callee's writes and the caller's reads."""
        cell = self.out_cell(key, index)
        return self.call(
            target, "wrapping_add_uint64_t",
            [("value", cell.path), self.literal(24)],
        )  # fmt: skip

    def operand(self, given: Operand | MOperand | None) -> MOperand:
        if given is None:
            raise ParseError(f"{self.fn.name}: an operand is missing")
        if isinstance(given, Operand):
            if given.constant is not None:
                return self.literal(given.constant)
            name = given.name
            if name is None:
                if given.expr is not None:
                    return self.expr_value(given.expr)
                raise ParseError(f"{self.fn.name}: an operand is missing")
            if name.startswith("@"):
                # a module-level global: its cell is the Object value above
                found = self.globals.get(name)
                if found is None:
                    # else a function word: the address of a body (or ABI face) of the
                    # module -- the words the unary_op slots of the type cells carry
                    bare = name[1:]
                    if bare in self.functions or bare in self.declared or bare in self.known:
                        return self.literal(self.function_word(bare))
                    raise Unsupported(f"{self.fn.name}: {name} is not defined in the module")
                if found.init is None:
                    # a declaration without bytes (the message this has always carried),
                    # or an initializer this pass cannot place: the reason names which
                    raise Unsupported(
                        f"{self.fn.name}: the global {name} has no payload this pass can "
                        + (
                            f"place ({found.refusal})"
                            if found.refusal is not None
                            else "place (a declaration without bytes, or an initializer "
                            "that is a relocation: a link-time address this pass cannot know)"
                        )
                    )
                return self.global_value(name)
            if name in self.nlr_buffers:
                # the setjmp buffer of the nlr ABI: only nlr_push and nlr_pop may name it
                # (lower_nlr never comes through here), and its bytes live only in the
                # unwinding the graph cannot do -- a read or a write of them is refused,
                # never guessed (the policy and its boundary are documented at lower_nlr)
                raise Unsupported(
                    f"{self.fn.name}: a use of the nlr buffer {name} outside the nlr ABI "
                    "(only nlr_push and nlr_pop may name it)"
                )
            if name in self.dropped:
                raise Unsupported(
                    f"{self.fn.name}: the status of a leaf call is dropped in this pass"
                )
            if name not in self.names:
                raise ParseError(f"{self.fn.name}: %{name} is not a value of the subset")
            return self.names[name]
        return given

    def narrow_operand(self, given: Operand | MOperand | None, mask: int) -> MOperand:
        """The operand of a narrow operation: a constant takes its low bits. A name is
        already in this form (every operation of the subset leaves its result so)."""
        made = self.operand(given)
        return self.literal(made[1] & mask) if made[0] == "literal" else made

    def note(self, name: str | None, operand: MOperand) -> None:
        if name is not None:
            self.names[name] = operand

    # ---- constant expressions and cells -------------------------------------------------------

    def expr_value(self, text: str) -> MOperand:
        """A constant expression of LLVM in an operand position.

        A cast is what its operand is (``ptrtoint (ptr @g to i64)`` is the cell's address
        as a word; ``inttoptr (i64 N to ptr)`` is the address word N). A ``getelementptr``
        whose indices are constants names the bytes of a cell at a fixed offset (the
        constant field offsets of every C static: ``ctx.top``, ``msg[3]``): the cell
        resolves to its payload address at run time, so those bytes are at that address
        plus the offset -- one wrapping add into the block being lowered."""
        text = text.strip()
        cast = re.fullmatch(
            rf"(?:inttoptr|addrspacecast|bitcast|ptrtoint)\s*\(\s*(?:{TYPE})\s+({OPERAND})"
            rf"\s+to\s+(?:{TYPE})\s*\)",
            text,
        )
        if cast:
            return self.operand(Operand.parse(cast.group(1)))
        gep = re.fullmatch(r"getelementptr(?:\s+inbounds)?\s*\((.*)\)", text)
        if gep:
            parts = split_top(gep.group(1))
            if len(parts) < 2:
                raise Unsupported(f"{self.fn.name}: a getelementptr of no base ({text})")
            ty = parts[0].strip()
            # every element of the body is ``<type> <operand>``: the base's operand and
            # the constant indices alike
            base_found = re.fullmatch(rf"(?:{TYPE})\s+({OPERAND})", parts[1].strip())
            base = base_found.group(1) if base_found else parts[1].strip()
            indices: list[int] = []
            for part in parts[2:]:
                found = re.fullmatch(r"i\d+\s+(-?\d+)", part.strip())
                if not found:
                    raise Unsupported(
                        f"{self.fn.name}: a getelementptr of a moving index ({text})"
                    )
                indices.append(int(found.group(1)))
            offset = self.gep_offset(ty, indices)
            made = self.operand(Operand.parse(base))
            if offset == 0:
                return made
            if self.current is None:
                raise ParseError(f"{self.fn.name}: a constant expression outside a block")
            return self.call(
                self.current, "wrapping_add_uint64_t", [made, self.literal(offset & FULL)]
            )
        raise Unsupported(f"{self.fn.name}: a constant expression operand ({text})")

    def gep_offset(self, ty: str, indices: list[int]) -> int:
        """The bytes a ``getelementptr``'s constant indices walk: the first index scales
        the whole type, the rest step through arrays and fields (the layout the cells use:
        ``struct_layout``/``field_offset``)."""
        total = indices[0] * type_size(ty, self.structs, self.model)
        for index in indices[1:]:
            shape = array_shape(ty)
            if shape is not None:
                _, element = shape
                total += index * type_size(element, self.structs, self.model)
                ty = element
                continue
            fields = struct_fields(ty, self.structs)
            if fields is None or not 0 <= index < len(fields):
                raise Unsupported(f"{self.fn.name}: the field {index} of {ty} is not here")
            at, field_type = field_offset(ty, index, self.structs, self.model)
            total += at
            ty = field_type
        return total

    def address_value(self, target: LBlock, source: MOperand) -> MOperand:
        """What an Object value (a global's cell) becomes where the bytes of an operand are
        read: a move or an edge's argument copies the bytes of its source, and a cell's
        bytes are its payload -- not the address. The wrapping add of zero takes the
        address ``load`` of native/graph.c gives an Object (its payload address) as the
        word of a leaf call, so the copy sees the address like any scalar."""
        if source[0] == "value" and source[1] in self.object_paths:
            return self.call(target, "wrapping_add_uint64_t", [source, self.literal(0)])
        return source

    # ---- lowering ----------------------------------------------------------------------------

    def lower(self) -> LGraph:
        self.lower_body()
        self.own_terminus()
        self.harmonize_widths()
        if self.card_calls and any(not self.out_position(name) for name in self.stored_params):
            # what the out-cell model of docs/23 4.1 cannot name: a body that calls a
            # program and writes through a pointer its caller gave in a shape the pass
            # cannot make an out cell of. Every out position now rides as the Object its
            # caller passes (the handle its three words); what is left of this class is
            # refused rather than left writing the wind.
            raise Unsupported(
                f"{self.fn.name}: a pointer to a value (the body writes through a pointer its "
                "caller gave, and a call card frame cannot carry it: only an inlined body "
                "reaches a caller's cell)"
            )
        if not self.function_results and self.has_reachable_cycle():
            # a body with no Return that the entry can reach a cycle in (an endless loop)
            # answers nothing: it stays refused rather than imported as a graph that
            # refuses every run. A body whose every path panics has no reachable cycle and
            # imports as the Refuse-only graph the panic policy needs (its terminus
            # answers RXF_REFUSE_UNSUPPORTED)
            raise Unsupported(f"{self.fn.name}: no return of an i64 value")
        frame = 0
        for value in self.values:
            if value.kind not in ("Literal", "Object"):  # a Literal or a cell: no frame slot
                frame += value.width + -value.width % 8
        if frame > GRAPH_CELL_BYTES:
            raise Unsupported(
                f"{self.fn.name}: a frame of {frame} bytes is too large "
                f"(RXF_GRAPH_CELL_BYTES is {GRAPH_CELL_BYTES})"
            )
        widths = {value.path: value.width for value in self.values}
        for block in self.blocks:
            for successor, arguments in block.successors:
                staged = sum(
                    8 if argument[0] == "literal" else widths.get(argument[1], 8)
                    for argument in arguments
                )
                if staged > GRAPH_COPY or len(arguments) > GRAPH_COPY:
                    # the run stages one edge's copies in RXF_GRAPH_COPY bytes
                    # (native/graph.c); an edge past it refuses rather than overflow
                    raise Unsupported(
                        f"{self.fn.name}: the edge {block.path} to {successor} copies "
                        f"{staged} bytes in {len(arguments)} values "
                        f"(RXF_GRAPH_COPY is {GRAPH_COPY})"
                    )
        return LGraph(
            self.fn.name, self.module, self.path, self.graph, self.signature,
            self.params, self.blocks, self.function_results, self.values, self.wide,
            globals=self.globals,
        )  # fmt: skip

    def has_reachable_cycle(self) -> bool:
        """Whether the entry can reach a cycle of the flow graph: a body with no Return
        that loops forever answers nothing (its graph cannot be imported as one that
        refuses every run -- only a body whose every path panics may be)."""
        seen: set[str] = set()
        active: set[str] = set()

        def walk(name: str) -> bool:
            if name in active:
                return True
            if name in seen:
                return False
            seen.add(name)
            active.add(name)
            for successor in self.flow_edges.get(name, []):
                if walk(successor):
                    return True
            active.discard(name)
            return False

        return walk(self.fn.blocks[0].name)

    def lower_body(self, parameters: list[MOperand] | None = None) -> None:
        """The blocks of the function. ``parameters`` is what an inlined call gives its params."""
        if self.fn.sret:
            raise Unsupported(
                f"{self.fn.name}: returns through an sret pointer (a by-value aggregate "
                "return, the C++ sret ABI): the subset returns one word"
            )
        if self.fn.byval:
            raise Unsupported(
                f"{self.fn.name}: a byval parameter (a C++ aggregate passed by value is a "
                "copy the callee may write): the subset passes words and addresses"
            )
        if self.fn.variadic:
            raise Unsupported(
                f"{self.fn.name}: a variadic function (its callers pass arguments this "
                "pass cannot map to parameters)"
            )
        if self.fn.ret not in MASKS and self.fn.ret not in ("ptr", "void"):
            raise Unsupported(f"{self.fn.name}: returns {self.fn.ret}, not an integer")
        self.prescan()
        entry = LBlock(f"{self.graph}.{self.tag}block[0]", 0)
        self.blocks.append(entry)
        self.entry = entry
        self.params = []
        self.standalone = parameters is None
        for index, (type_name, name) in enumerate(self.fn.params):
            if parameters is not None:
                self.names[name] = parameters[index]
            elif type_name in MASKS or type_name == "ptr":
                out = type_name == "ptr" and self.out_position(name)
                found = self.value(
                    "Parameter", self.graph, f"parameter[{index}]",
                    # an out position takes the handle its three words (docs/23 4.1), the
                    # shape run-entry ARGS passes; a scalar or a landed pointer is a word
                    width=24 if out else 8,
                    type_path=WIDE if out else U64,
                )  # fmt: skip
                self.params.append(found)
                given: MOperand = ("value", found.path)
                # a narrow parameter is the low bits of the word the run passes: the C the
                # differential runs takes its argument the same way (an i8 parameter is
                # what the low byte of the register holds)
                self.names[name] = (
                    given if type_name == "ptr" else rxemit.and_mask(self.at(entry), given, MASKS[type_name])
                )
            else:
                raise Unsupported(f"{self.fn.name}: a parameter of {type_name}, not an integer")
            if type_name == "ptr":  # an address of the flat memory, opaque to this pass
                self.pointers.add(name)
                self.ptr_params.add(name)  # a pointer the caller gave: a pointer to a value
        self.resolve_labels(entry)
        for name in sorted(self.wide_names):  # the locals ride as the entry's first parameters
            made = self.value(
                "Bound", entry.path, f"local[{len(self.wide)}]",
                owner_block=entry.path, width=16, type_path=WIDE,
            )  # fmt: skip
            self.slots.add(made.path)  # a wide local is a slot: a leaf gets its address
            self.wide[name] = made
            self.wide_of[made.path] = made
            self.names[name] = ("value", made.path)
            entry.params.append(made)
        ordered = self._ordered_blocks()
        for block in ordered:  # phis first: they name the parameters of their block
            target = self.block_of[block.name]
            for inst in block.insts:
                if inst.op != "phi":
                    break
                mask = self.word_of(inst.type)  # a phi of any width, or a ptr (an address word)
                made = self.value(
                    "Bound", target.path, f"blkparam[{len(target.params)}]", owner_block=target.path
                )
                target.params.append(made)
                self.note(inst.name, ("value", made.path))
                if inst.type == "ptr" and inst.name is not None:
                    self.pointers.add(inst.name)  # the phi carries an address between blocks
                taken_by_pred: dict[str, Operand] = {}
                for incoming, predecessor in inst.incoming:
                    taken = incoming if incoming.constant is None else Operand(constant=incoming.constant & mask)
                    prior = taken_by_pred.get(predecessor)
                    if prior is not None and prior != taken:
                        # LLVM lists one phi entry per edge: a switch with several cases to
                        # one block repeats the label on the phi, and every repeat carries
                        # the same value (the phi reads one value per predecessor block)
                        raise ParseError(
                            f"{self.fn.name}: {block.name} has a phi that passes "
                            f"different values along the same predecessor {predecessor}"
                        )
                    taken_by_pred[predecessor] = taken
                for predecessor, taken in taken_by_pred.items():
                    self.incoming[(block.name, predecessor)] = (
                        self.incoming.get((block.name, predecessor), []) + [taken]
                    )
        for block in ordered:
            current = self.block_of[block.name]
            self.source_block = block.name
            self.block_stored = set()  # a new block starts with no local written in it yet
            for inst in block.insts[:-1]:  # the terminator is the last one, `finish` takes it
                if inst.op == "phi":
                    continue
                current = self.lower_inst(inst, current)
            self.finish(block, current)

    def _ordered_blocks(self) -> list[Block]:
        """The blocks of the function where a definition comes before every use of it: the
        reverse post-order of the flow graph (the loop back-edges aside). What clang prints
        is the layout order, which may read a value of a block that prints later; phis take
        their incoming values at the edges instead, so only they cross the order."""
        by_name = {block.name: block for block in self.fn.blocks}
        placed: list[Block] = []
        seen: set[str] = set()

        def targets(block: Block) -> list[str]:
            found: list[str] = []
            for inst in block.insts:
                if inst.op == "goto" and inst.target:
                    found.append(inst.target)
                elif inst.op == "branch" and inst.if_true and inst.if_false:
                    found += [inst.if_true, inst.if_false]
                elif inst.op == "switch":
                    found += [label for _, label in inst.cases]
                    if inst.default:
                        found.append(inst.default)
            return found

        def visit(block: Block) -> None:
            if block.name in seen:
                return
            seen.add(block.name)
            for label in targets(block):
                found = by_name.get(label)
                if found is not None:
                    visit(found)
            placed.append(block)

        visit(self.fn.blocks[0])
        for block in self.fn.blocks:  # blocks the entry does not reach
            visit(block)
        return placed[::-1]

    def prescan(self) -> None:
        """The allocas of the function, and which of them are address-taken (wide slots)."""
        allocas = {
            inst.name
            for block in self.fn.blocks
            for inst in block.insts
            if inst.op == "alloca" and inst.name is not None
        }
        # the setjmp buffers of the nlr ABI (py/nlr.h): every name that reaches nlr_push
        # or nlr_pop is protected (the policy of lower_nlr), so its other uses refuse
        params = {name for _, name in self.fn.params}
        for block in self.fn.blocks:
            for inst in block.insts:
                # a parameter written through is an out position (docs/23 4.1) -- known
                # before the parameters take their shape, so the out cell can be sized
                if inst.op == "store" and inst.b is not None and inst.b.name in params:
                    self.stored_params.add(inst.b.name)
        self.crosses_card = self.crosses_a_card()
        for block in self.fn.blocks:
            for inst in block.insts:
                if inst.op != "call" or inst.callee not in (NLR_PUSH, NLR_POP):
                    continue
                if len(inst.args) != 1:
                    raise Unsupported(f"{self.fn.name}: {inst.callee} of no one buffer")
                buffer = inst.args[0][1]
                if buffer.name is None or buffer.name not in allocas | params:
                    raise Unsupported(
                        f"{self.fn.name}: {inst.callee} of a buffer that is not a name "
                        "of this function (an alloca or a parameter)"
                    )
                self.nlr_buffers.add(buffer.name)
        taken: set[str] = set()
        for block in self.fn.blocks:
            for inst in block.insts:
                if inst.op == "indirect" or (inst.op == "call" and inst.callee is None):
                    # a call through a register (lower_indirect's slot dispatch): the
                    # callee's body is not named here, so every pointer-typed argument
                    # may reach an out position of one of its arms (docs/23 4.1) -- a
                    # local one of them names is a wide slot of the frame
                    for _, (type_name, argument) in enumerate(inst.args):
                        name = argument.name
                        if type_name == "ptr" and name is not None and name in allocas:
                            taken.add(name)
                    continue
                if inst.op != "call" or inst.callee is None:
                    continue
                key = leaf_key(inst.callee)
                if key is not None:
                    _, _, _, pointers = LEAVES[key]
                elif inst.callee in self.functions:
                    # a function of the module is inlined and stores through its pointer
                    # parameters: the out-parameter idiom of C reaches the callee's bytes
                    pointers = {
                        position
                        for position, (type_name, _) in enumerate(self.functions[inst.callee].params)
                        if type_name == "ptr"
                    }
                elif inst.callee.startswith("llvm.memcpy"):
                    pointers = {0, 1}
                elif inst.callee.startswith("llvm.memset"):
                    pointers = {0}
                elif inst.callee.startswith("llvm."):
                    continue  # an intrinsic of the IR (a lifetime marker): no pointer of its own
                elif inst.callee in (NLR_PUSH, NLR_POP, NLR_JUMP):
                    continue  # the setjmp buffer of the nlr ABI (its policy is lower_nlr)
                else:
                    # an opaque call -- a call card of another program, or an ABI face
                    # whose card is the reference the delta links: its pointer-typed
                    # arguments are addresses the callee reads or writes through (the
                    # flat memory or a local's bytes, the way a leaf's pointers do), so
                    # a local one of them names is a wide slot of the frame
                    pointers = {
                        position
                        for position, (type_name, _) in enumerate(inst.args)
                        if type_name == "ptr"
                    }
                for position, (_, argument) in enumerate(inst.args):
                    name = argument.name
                    if position in pointers and name is not None and name in allocas:
                        taken.add(name)
        self.wide_names = taken
        self.narrow_names = allocas - taken
        # where each local is stored, by block: a load must see a store of its own block, so
        # a local carried between blocks (rustc's opt-level=0 keeps branch joins in memory)
        # is refused rather than read from a store that never reached it
        for block in self.fn.blocks:
            for inst in block.insts:
                name = inst.b.name if inst.op == "store" and inst.b is not None else None
                if name is None and inst.op == "call" and inst.callee is not None and inst.args:
                    # the out slot a leaf call writes is a store of that block too
                    key = leaf_key(inst.callee)
                    if key is not None and LEAVES[key][2]:
                        name = inst.args[-1][1].name
                if name is not None and name in self.narrow_names:
                    self.slot_stores.setdefault(name, set()).add(block.name)
        # a diverging call -- one directly before an unreachable -- lowers to nothing and
        # the unreachable after it refuses the run (the status-branch idiom of the Rust
        # panic path, generalized by shape: a C noreturn call or mp_raise_* of MicroPython
        # ends the block the same way). A panic call that could fall through would run on
        # instead: the IR of a diverging call always ends the block, so anything else is
        # refused.
        for block in self.fn.blocks:
            for position, inst in enumerate(block.insts[:-1]):
                if inst.op != "call" or not inst.callee:
                    continue
                if block.insts[position + 1].op == "unreachable":
                    inst.diverges = True
                elif is_panic_callee(inst.callee) or inst.callee == NLR_JUMP:
                    raise Unsupported(
                        f"{self.fn.name}: the panic call of {inst.callee} is not "
                        "followed by unreachable"
                    )

    def resolve_labels(self, entry: LBlock) -> None:
        """The label of the entry block: only a phi names it, and clang prints its slot."""
        defined = {block.name for block in self.fn.blocks[1:]}
        referenced = {
            label for _, predecessors in self._phi_labels() for label in predecessors
        }
        unknown = referenced - defined
        if len(unknown) > 1:
            raise ParseError(f"{self.fn.name}: labels {sorted(unknown)} are not blocks of it")
        if unknown:  # the slot clang prints for the entry block when a phi names it
            self.fn.blocks[0].name = unknown.pop()
        self.block_of[self.fn.blocks[0].name] = entry
        for number, block in enumerate(self.fn.blocks[1:], start=1):
            made = LBlock(f"{self.graph}.{self.tag}block[{number}]", number)
            self.blocks.append(made)
            self.block_of[block.name] = made

    def _phi_labels(self) -> list[tuple[str, list[str]]]:
        found = []
        for block in self.fn.blocks:
            for inst in block.insts:
                if inst.op == "phi":
                    found.append((block.name, [label for _, label in inst.incoming]))
        return found

    def finish(self, block: Block, target: LBlock) -> None:
        """The terminator of a block and the arguments each of its edges passes."""
        self.current = target  # a constant expression resolves its cell address into it
        inst = block.terminator

        def arguments(successor: str | None) -> tuple[str, list[MOperand]]:
            if successor is None:  # a terminator line that names no label (the regexes keep one)
                raise ParseError(
                    f"{self.fn.name}: {block.name} ends in "
                    f"{inst.raw or inst.op!r} without the label it names"
                )
            wanted = self.block_of[successor]
            given: list[MOperand] = [
                self.address_value(target, self.operand(item))
                for item in self.incoming.get((successor, block.name), [])
            ]
            if wanted is self.entry and len(wanted.params) == len(self.wide) + len(given):
                # a loop back to the entry block: the locals it takes pass themselves through
                through: list[MOperand] = [("value", made.path) for made in self.wide.values()]
                given = through + given
            if len(given) != len(wanted.params):
                raise ParseError(
                    f"{self.fn.name}: {block.name} passes {len(given)} values to "
                    f"{successor}, which has {len(wanted.params)}"
                )
            return wanted.path, given

        if inst.op == "ret":
            if inst.type == "void" and inst.a is None:
                # a void body answers 0 (a caller ignores it; the graph of the run answers
                # one word and the caller of a void function reads none of it)
                target.terminator = "Return"
                made = self.move_value(target, self.literal(0))
                target.terminator_value = made
                self.function_results.append(made)
            elif inst.a is None or inst.type is None or (inst.type not in MASKS and inst.type != "ptr"):
                raise Unsupported(f"{self.fn.name}: a return that is not of an integer")
            else:
                target.terminator = "Return"
                # through a ReturnValue operation: the terminator_value field of the block may not
                # repeat a reference the block already carries (a BlockParameterValue), the way
                # the starter's returns are built. A function may answer from several blocks (the
                # graph has one result value per Return). A returned pointer is the address word,
                # carried in a wide result (its first bytes), as ``pointer`` gives a leaf an address.
                # The answer is one canonical 64-bit word: the zero-extension of a narrow return
                # (the C the differential runs has uint64_t functions)
                wide = inst.type == "ptr"
                made = self.move_value(
                    target,
                    self.narrow_operand(inst.a, FULL if wide else MASKS[inst.type]),
                    width=16 if wide else 8,
                )
                target.terminator_value = made
                self.function_results.append(made)
        elif inst.op == "unreachable":
            target.terminator = "Refuse"
            target.terminator_value = self.literal(UNSUPPORTED_STATUS)
        elif inst.op == "goto":
            self.flow_edges.setdefault(block.name, []).append(inst.target or "")
            target.terminator = "Goto"
            target.successors = [arguments(inst.target)]
        elif inst.op == "branch":
            self.flow_edges.setdefault(block.name, []).append(inst.if_true or "")
            self.flow_edges[block.name].append(inst.if_false or "")
            target.terminator = "Branch"
            target.condition = self.own_condition(self.operand(inst.c), target)
            target.successors = [arguments(inst.if_true), arguments(inst.if_false)]
        elif inst.op == "switch":
            self.flow_edges.setdefault(block.name, []).extend(
                [label for _, label in inst.cases] + [inst.default or ""]
            )
            target.terminator = "SwitchTag"
            target.condition = self.own_condition(self.operand(inst.a), target)
            # the tags mask to the width of the switched value: an i32 case of -1 is the
            # word 0xffffffff of the subset's zero-extended narrow values, not 2^64 - 1
            mask = MASKS.get(inst.type or "")
            if mask is None:
                raise Unsupported(f"{self.fn.name}: a switch of {inst.type}")
            target.case_tags = [tag & mask for tag, _ in inst.cases]
            target.successors = [arguments(label) for _, label in inst.cases]
            target.successors.append(arguments(inst.default))
        else:
            raise Unsupported(
                f"{self.fn.name}: {block.name} ends in {inst.raw or inst.op!r}"
            )

    # ---- instructions ------------------------------------------------------------------------

    def lower_inst(self, inst: Inst, current: LBlock) -> LBlock:
        """One instruction; a select returns the block its lowering goes on with."""
        self.current = current  # a constant expression resolves its cell address into it
        if inst.op in BINOPS:
            self.lower_binop(inst, current)
        elif inst.op in CASTS:
            self.lower_cast(inst, current)
        elif inst.op == "select":
            return self.lower_select(
                self.operand(inst.c), self.operand(inst.a), self.operand(inst.b),
                inst.name, current, inst.type == "ptr",
            )  # fmt: skip
        else:
            made = getattr(self, f"lower_{inst.op}")(inst, current)
            if made is not None:
                current = made
        return current

    def call(
        self,
        target: LBlock,
        key: str,
        inputs: list[MOperand],
        name: str | None = None,
        input_types: list[str] | None = None,
    ) -> MOperand:
        """One leaf call that writes an out slot, as one CallCallback operation.

        The ``pointer`` entry of native/graph.h gives the out a parameter names, and the
        leaf's C writes the result into the caller's bytes directly. """
        callee, arity, has_out, _ = LEAVES[key]
        if not has_out:
            raise Unsupported(f"{self.fn.name}: {key} writes no out slot")
        if len(inputs) != arity:
            raise Unsupported(f"{self.fn.name}: {key} takes {arity} values")
        if any(item[0] not in ("value", "literal") for item in inputs):
            raise Unsupported(f"{self.fn.name}: {key} of an address")
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        result = self.value(
            "OperationResult", op, f"op{self.op_count}_result",
            owner_block=target.path, owner_op=op,
        )  # fmt: skip
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", inputs, input_types or [U64] * arity, result, callee)
        )
        made: MOperand = ("value", result.path)
        self.note(name, made)
        return made

    def call_void(self, target: LBlock, key: str, inputs: list[MOperand]) -> None:
        """One leaf call without an out slot (the memory leaves of ``native/stdlib_memory.c``)
        as one CallCallback operation with no result (``Plan.instruction`` takes the out of
        the result, and ``native/graph.c`` refuses an out on such a shape)."""
        callee, arity, has_out, _ = LEAVES[key]
        if has_out:
            raise Unsupported(f"{self.fn.name}: {key} writes an out slot")
        if len(inputs) != arity:
            raise Unsupported(f"{self.fn.name}: {key} takes {arity} values")
        if any(item[0] not in ("value", "literal") for item in inputs):
            raise Unsupported(f"{self.fn.name}: {key} of an address")
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", inputs, [U64] * arity, None, callee)
        )

    def move_value(self, target: LBlock, source: MOperand, width: int = 8) -> ValueOperand:
        """A ReturnValue operation (the pattern of the starter): a move of a value to a slot.

        The interpreter's OP_MOVE is what reads memory into a value: the bytes of the source
        operand into the result's slot, the rest of the slot zero. A wide result (16 bytes)
        is a wide slot -- an addressable local of the frame.
        """
        source = self.address_value(target, source)  # a cell moves as its address word
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        result = self.value(
            "OperationResult", op, f"op{self.op_count}_result",
            owner_block=target.path, owner_op=op,
            width=width, type_path=WIDE if width > 8 else U64,
        )  # fmt: skip
        target.ops.append(LOp(op, len(target.ops), "ReturnValue", [source], [U64], result))
        return ("value", result.path)

    def own_condition(self, condition: MOperand, target: LBlock) -> MOperand:
        """A branch's condition as its own reference.

        The image binds one value to one field (``condition_value`` is one reference), so
        a flag that already stands as some block's condition -- a loop's exit test that
        also picks an arm of a select, the shape clang's rotated loops make -- takes a
        copy here. The copy is the same bits, so the branch goes the same way."""
        if condition[0] == "value" and condition[1] in self.conditions:
            condition = self.move_value(target, condition)
        if condition[0] == "value":
            self.conditions.add(condition[1])
        return condition

    def operand_path(self, operand: MOperand) -> str:
        """The path of the value an operand names; a constant names its Literal object."""
        return operand[1] if operand[0] == "value" else self.literals[operand[1]].path

    def own_terminus(self) -> None:
        """The condition and the answer of every block, each its own reference.

        One edge, one place (the link check's rule): the ``condition_value`` and
        ``terminator_value`` fields name a value on the block itself, so that value may
        not also stand among the block's own references -- a parameter it takes in
        (``BlockParameterValue``) or a value it hands a successor
        (``SuccessorArgument``). A rotated loop's header branches on the very flag its
        edge carries in, and a join on its own parameter; both take a copy of the same
        bits here, as a return already does, so the branch goes the same way."""
        for block in self.blocks:
            held = {parameter.path for parameter in block.params}
            for _, arguments in block.successors:
                held.update(self.operand_path(argument) for argument in arguments)
            if block.condition is not None and self.operand_path(block.condition) in held:
                block.condition = self.move_value(block, block.condition)
            if (
                block.terminator_value is not None
                and self.operand_path(block.terminator_value) in held
            ):
                block.terminator_value = self.move_value(block, block.terminator_value)

    def harmonize_widths(self) -> None:
        """The width of a block parameter is the width its edges hand it, whole values.

        The two flavors of a pointer the frame carries decide the width.  A word (8
        bytes) is the value itself: ``pointer`` of native/graph.c gives a leaf what the
        slot holds -- the flat address.  A wide value (16 bytes) is one of two things: a
        slot (a wide local or the load staging: a leaf gets the slot's own address) or a
        carrier (the out of a card, the answer of a ret -- "the address word carried in
        a wide result, its first bytes").  A phi joins values of one flavor: a carrier
        hands its word over in a copy of 8 bytes (the first bytes, exactly what the ret
        and the card document), and a slot joins as a wide slot with words copied into
        one.  A parameter that would join a slot and a word is refused rather than
        answered from the wrong bytes: a slot's own address and the flat address a word
        carries are not the same pointer.

        The composer copies whole values only (its width check), so this pass is what
        makes every edge whole before the composer sees one."""
        widths = {value.path: value.width for value in self.values}
        blocks = {block.path: block for block in self.blocks}

        def flavor(path: str) -> str:
            if widths.get(path, 8) == 8:
                return "word"
            return "slot" if path in self.slots else "word"

        # what every parameter of every block is handed, across all the edges to it
        incoming: dict[str, list[tuple[LBlock, str, list[MOperand], int, MOperand]]] = {}
        for block in self.blocks:
            for path, arguments in block.successors:
                target = blocks.get(path)
                if target is None or len(arguments) != len(target.params):
                    continue  # a count that does not line up: the composer speaks first
                for index, (argument, parameter) in enumerate(
                    zip(arguments, target.params, strict=True)
                ):
                    incoming.setdefault(parameter.path, []).append(
                        (block, path, arguments, index, argument)
                    )
        moved: dict[tuple[str, str, int], MOperand] = {}
        for parameter in (p for block in self.blocks for p in block.params):
            hands = incoming.get(parameter.path, [])
            if not hands:
                continue  # no edge hands it: the entry's own parameters (the run gives
                # them, wide locals as 16-byte slots among them) keep the width they carry
            flavors = {
                flavor(argument[1])
                for _, _, _, _, argument in hands
                if argument[0] == "value"
            }
            if len(flavors) > 1:
                raise Unsupported(
                    f"{self.fn.name}: a block parameter that joins a slot and a word (the "
                    "two flavors of a pointer the frame carries: a slot's own address and "
                    "the flat address a word holds are not the same bytes)"
                )
            wanted = 16 if flavors == {"slot"} else 8
            if parameter.width != wanted:
                parameter.width = wanted
                parameter.type_path = WIDE if wanted > 8 else U64
            for block, _, arguments, index, argument in hands:
                if argument[0] != "value" or widths.get(argument[1]) == wanted:
                    continue  # a literal copies its bytes anywhere; a whole value goes
                key = (block.path, argument[1], wanted)
                if key not in moved:
                    moved[key] = self.move_value(block, argument, width=wanted)
                arguments[index] = moved[key]

    def load_scratch_of(self, target: LBlock) -> MOperand:
        """The shared scratch wide slot a load of the flat space stages its bytes in.

        OP_MOVE reads operands, not the memory an address names, so a load copies the
        memory's bytes into a wide slot and reads the word out of the slot. The staging
        is filled right before it is read (the copy in and the word out are adjacent
        ops), so the whole graph shares one such slot -- one wide slot per load is frame
        the body never asked for, and a MicroPython function of pointer walks is exactly
        where that would push the frame past its budget."""
        if not self.load_scratch:
            entry = self.entry or target
            staged = self.move_value(entry, self.literal(0), width=16)
            self.slots.add(staged[1])  # the staging is a slot: its own address is given
            self.load_scratch.append(staged)
        return self.load_scratch[0]

    def at(self, target: LBlock) -> BlockLeaves:
        """This lowering's leaf calls into ``target``, as the emitter's shared helpers
        take them (``rxemit.and_mask(self.at(target), source, mask)`` and kin). The
        narrow-integer and ashr helpers have one home -- rxemit -- and no copy lives
        here; the drift pin of ``tests/test_import.py`` holds that line."""
        return BlockLeaves(self, target)

    def lower_binop(self, inst: Inst, target: LBlock) -> None:
        """The integer operations of the subset, of every width (i1/i8/i16/i32/i64)."""
        if inst.type is None or inst.type not in MASKS:
            raise Unsupported(f"{self.fn.name}: {inst.op} of {inst.type}, not an integer")
        if inst.a is None or inst.b is None:
            raise ParseError(f"{self.fn.name}: {inst.op} without both operands")
        mask = MASKS[inst.type]
        a = self.narrow_operand(inst.a, mask)  # a narrow value is its low bits, constants too
        b = self.narrow_operand(inst.b, mask)
        name = inst.name
        key = {
            "add": "wrapping_add_uint64_t",
            "sub": "wrapping_subtract_uint64_t",
            "and": "bit_and_uint64_t",
            "or": "bit_or_uint64_t",
        }.get(inst.op)
        if key is not None:  # wrapping 64-bit arithmetic kept in the low bits is iN arithmetic
            self.note(name, rxemit.and_mask(self.at(target), self.call(target, key, [a, b]), mask))
        elif inst.op in ("shl", "lshr"):  # the count is the leaf's u32 parameter (it truncates)
            key = "shift_left_uint64_t" if inst.op == "shl" else "shift_right_uint64_t"
            self.note(name, rxemit.and_mask(self.at(target), self.call(target, key, [a, b], input_types=[U64, U32]), mask))
        elif inst.op == "ashr":  # of the sign-extended word, then back to the low bits
            signed = rxemit.sext_value(self.at(target), a, mask)
            self.note(name, rxemit.and_mask(self.at(target), rxemit.ashr_value(self.at(target), signed, b), mask))
        elif inst.op == "xor" and inst.b.constant == -1:  # xor with -1 is `not`
            self.note(name, rxemit.and_mask(self.at(target), self.call(target, "bit_not_uint64_t", [a]), mask))
        elif inst.op == "xor" and inst.a.constant == -1:
            self.note(name, rxemit.and_mask(self.at(target), self.call(target, "bit_not_uint64_t", [b]), mask))
        elif inst.op == "xor":
            # a ^ b is (a | b) - (a & b): no leaf for xor exists
            either = self.call(target, "bit_or_uint64_t", [a, b])
            both = self.call(target, "bit_and_uint64_t", [a, b])
            made = self.call(target, "wrapping_subtract_uint64_t", [either, both])
            self.note(name, rxemit.and_mask(self.at(target), made, mask))
        elif inst.op in ("mul", "udiv", "sdiv", "urem", "srem"):
            self.note(name, self.integer_op(target, inst.op, a, b, mask))
        else:
            raise Unsupported(f"{self.fn.name}: {inst.op} of {inst.type}")

    def integer_op(self, target: LBlock, op: str, a: MOperand, b: MOperand, mask: int) -> MOperand:
        """The multiply, divide and remainder leaves of the flat profile (native/graph.h
        ids 39-48): the product of every width is the 64-bit wrapping leaf kept in the
        low bits; an i32 divide or remainder takes its own 32-bit leaf (46-48 and 25, the
        type's own refusal of INT32_MIN / -1 included) and the rest the 64-bit pair, a
        narrow signed form dividing the sign-extended words.

        The leaves refuse where the C leaves a division undefined (a divisor of zero, and
        INT_MIN / -1 of the signed pair): the out slot the run zeroes before every call
        stays zero and the run goes on. The inputs of the differential never reach those
        paths -- natively they are a SIGFPE, not an answer to compare against."""
        if op == "mul":
            made = self.call(target, "wrapping_multiply_uint64_t", [a, b])
            return rxemit.and_mask(self.at(target), made, mask)
        if mask == MASKS["i32"]:
            key = {
                "udiv": "divide_uint32_t",
                "sdiv": "divide_int32_t",
                "urem": "remainder_uint32_t",
                "srem": "remainder_int32_t",
            }[op]
            # the i32 leaf writes its 4 bytes into the zeroed slot: the word is the
            # zero-extended result the subset keeps every narrow value in
            return self.call(target, key, [a, b], input_types=[U32, U32])
        key = {
            "udiv": "divide_uint64_t",
            "sdiv": "divide_int64_t",
            "urem": "remainder_uint64_t",
            "srem": "remainder_int64_t",
        }[op]
        if op in ("sdiv", "srem"):  # a signed divide is of the sign-extended words
            a = rxemit.sext_value(self.at(target), a, mask)
            b = rxemit.sext_value(self.at(target), b, mask)
        return rxemit.and_mask(self.at(target), self.call(target, key, [a, b]), mask)

    def less(self, target: LBlock, left: MOperand, right: MOperand, signed: bool) -> MOperand:
        if signed:  # a + 2^63 turns a signed compare of full words into an unsigned one
            flip = self.literal(1 << 63)
            left = self.call(target, "wrapping_add_uint64_t", [left, flip])
            right = self.call(target, "wrapping_add_uint64_t", [right, flip])
        return self.call(target, "less_uint64_t", [left, right])

    def lower_icmp(self, inst: Inst, target: LBlock) -> None:
        """The integer compares of every width and predicate: the words are compared as
        they are (unsigned, eq), or sign-extended first (the signed ones). The answer is
        the 0/1 word of the i1 the subset keeps values in."""
        if inst.type is None or inst.pred is None or (inst.type not in MASKS and inst.type != "ptr"):
            raise Unsupported(f"{self.fn.name}: icmp of {inst.type} {inst.pred}")
        if inst.type == "ptr" and inst.pred in ("slt", "sgt", "sle", "sge"):
            # a signed order of addresses: nothing in the contract signs the flat space
            raise Unsupported(f"{self.fn.name}: icmp of ptr {inst.pred}")
        if inst.a is None or inst.b is None:
            raise ParseError(f"{self.fn.name}: icmp without both operands")
        # an address is an i64 word of the flat profile: an eq/ne of two pointers is the
        # word compare of the addresses they carry, and so is an unsigned order of them
        # (the C that range-checks an immediate object word compares addresses; a signed
        # order of addresses the subset still refuses)
        mask = FULL if inst.type == "ptr" else MASKS[inst.type]
        a = self.narrow_operand(inst.a, mask)
        b = self.narrow_operand(inst.b, mask)
        one = self.literal(1)
        if inst.pred in ("eq", "ne"):
            same = self.call(target, "equal_uint64_t", [a, b])
            self.note(inst.name, same if inst.pred == "eq" else self.negate(target, same, one))
            return
        signed = inst.pred.startswith("s")
        if signed:
            a = rxemit.sext_value(self.at(target), a, mask)  # a signed compare is of the full words
            b = rxemit.sext_value(self.at(target), b, mask)
        order = {
            "ult": (a, b), "uge": (a, b), "ugt": (b, a), "ule": (b, a),
            "slt": (a, b), "sge": (a, b), "sgt": (b, a), "sle": (b, a),
        }  # fmt: skip
        if inst.pred not in order:
            raise Unsupported(f"{self.fn.name}: icmp {inst.pred}")
        left, right = order[inst.pred]
        less = self.less(target, left, right, signed)
        if inst.pred in ("ult", "ugt", "slt", "sgt"):
            self.note(inst.name, less)
        else:
            self.note(inst.name, self.negate(target, less, one))

    def negate(self, target: LBlock, value: MOperand, one: MOperand) -> MOperand:
        """1 - value: a 0/1 result turned around."""
        return self.call(target, "wrapping_subtract_uint64_t", [one, value])

    def lower_select(
        self, cond: MOperand, a: MOperand, b: MOperand, name: str | None, target: LBlock,
        pointer: bool = False,
    ) -> LBlock:
        """Branch to two arms that pass their value to one join parameter; return the join.

        ``pointer`` marks a select between two addresses (``select i1, ptr, ptr``): the join
        holds one of the addresses, so the result is named as a pointer and a later load or
        store through it resolves to the flat address it carries."""
        if cond[0] == "literal":  # a constant condition takes one arm
            self.note(name, a if cond[1] else b)
            if pointer and name is not None:
                self.pointers.add(name)
            return target
        join = self.new_block()
        chosen = self.value(
            "Bound", join.path, f"blkparam[{len(join.params)}]", owner_block=join.path
        )
        join.params.append(chosen)
        arms = []
        for argument in (a, b):
            arm = self.new_block()
            arm.terminator = "Goto"
            # an edge's copy takes the bytes of its source: a cell's bytes are its
            # payload, so what a cell hands the join is its address (address_value)
            arm.successors = [(join.path, [self.address_value(arm, argument)])]
            arms.append(arm)
        target.terminator = "Branch"
        target.condition = self.own_condition(cond, target)
        # edges[0] is the taken one and takes the arm that keeps the condition true (a)
        target.successors = [(arm.path, []) for arm in arms]
        self.note(name, ("value", chosen.path))
        if pointer and name is not None:
            self.pointers.add(name)
        return join

    def new_block(self) -> LBlock:
        made = LBlock(
            f"{self.graph}.{self.tag}block[{len(self.blocks)}]", len(self.blocks)
        )
        self.blocks.append(made)
        return made

    def lower_cast(self, inst: Inst, target: LBlock) -> None:
        """The casts between the integer widths of the subset (i1/i8/i16/i32/i64).

        A value is the low bits of its word, so zext is the mask of the source width and
        trunc the mask of the destination; sext is ``sext_value``; ``inttoptr`` and
        ``ptrtoint`` move the address word unchanged (a pointer is an i64 address here)."""
        if inst.a is None or inst.to is None or inst.type is None:
            raise ParseError(f"{self.fn.name}: a cast without its operand or its widths")
        source = self.narrow_operand(inst.a, self.word_of(inst.type))
        if inst.op == "inttoptr" or inst.op == "ptrtoint":
            self.note(inst.name, source)
            if inst.op == "inttoptr" and inst.name is not None:
                self.pointers.add(inst.name)  # the result is an address of the flat memory
        elif inst.op == "zext":
            self.note(inst.name, rxemit.and_mask(self.at(target), source, self.mask_of(inst.type)))
        elif inst.op == "trunc":
            self.note(inst.name, rxemit.and_mask(self.at(target), source, self.mask_of(inst.to)))
        elif inst.op == "sext":
            extended = rxemit.sext_value(self.at(target), source, self.mask_of(inst.type))
            self.note(inst.name, rxemit.and_mask(self.at(target), extended, self.mask_of(inst.to)))
        else:
            raise Unsupported(f"{self.fn.name}: {inst.op} to {inst.to}")

    def scale_index(self, target: LBlock, index: MOperand, scale: int) -> MOperand:
        """``index * scale`` for a getelementptr stride: a constant folds, and a power-of-two
        scale is one shift. Any other constant stride is the sum of the shifted copies (the
        subset has no multiply leaf, and shifts and adds of the words make one)."""
        if scale == 0:
            return self.literal(0)
        if index[0] == "literal":
            return self.literal(index[1] * scale)
        if scale == 1:
            return index
        if scale & (scale - 1) == 0:  # a power of two: one shift
            return self.call(
                target, "shift_left_uint64_t", [index, self.literal(scale.bit_length() - 1)],
                input_types=[U64, U32],
            )  # fmt: skip
        total = self.literal(0)
        bit = 0
        left = scale
        while left:
            if left & 1:
                shifted = index if bit == 0 else self.call(
                    target, "shift_left_uint64_t", [index, self.literal(bit)], input_types=[U64, U32]
                )
                total = self.call(target, "wrapping_add_uint64_t", [total, shifted])
            left >>= 1
            bit += 1
        return total

    def lower_gep(self, inst: Inst, target: LBlock) -> None:
        """``getelementptr``: the address of an element of memory, as pointer arithmetic.

        A pointer of the flat profile is a 64-bit address word, so the result is the base
        plus the byte offset the indices walk out: the first index scales by the size of
        the source type, and each later one steps into an array (the element size) or a
        struct (a constant field offset). The offset is folded when the indices are
        constants and otherwise summed from shifted index words."""
        if inst.a is None or inst.type is None:
            raise ParseError(f"{self.fn.name}: a getelementptr without its base or its type")
        base_name = inst.a.name
        if base_name is not None and base_name in self.wide_names | self.narrow_names:
            raise Unsupported(
                f"{self.fn.name}: getelementptr of the local {base_name} (its bytes are a "
                "frame slot, not flat memory: the pass does no arithmetic on the address "
                "of a local's own bytes)"
            )
        base = self.operand(inst.a)
        constant = 0
        terms: list[tuple[MOperand, int]] = []  # (index word, stride)
        current = inst.type
        for position, (index_type, index) in enumerate(inst.indices):
            if index_type not in MASKS:
                raise Unsupported(f"{self.fn.name}: a getelementptr index of {index_type}")
            if position == 0:
                stride = type_size(current, self.structs, self.model)  # the first index scales the source
            else:
                element = array_element(current)
                if element is not None:  # an array: the index strides the element
                    current = element
                    stride = type_size(current, self.structs, self.model)
                elif struct_fields(current, self.structs) is not None:  # a struct field
                    if index.constant is None:
                        raise Unsupported(
                            f"{self.fn.name}: a getelementptr field of {current} is not a constant"
                        )
                    at, current = field_offset(current, index.constant, self.structs, self.model)
                    constant += at
                    continue
                else:
                    raise Unsupported(f"{self.fn.name}: a getelementptr into {current}")
            if index.constant is not None:
                constant += index.constant * stride
            else:
                word = self.operand(index)
                if index_type != "i64":
                    word = rxemit.sext_value(self.at(target), word, self.mask_of(index_type))
                terms.append((word, stride))
        address = base
        if constant:
            address = self.call(target, "wrapping_add_uint64_t", [address, self.literal(constant & FULL)])
        for word, stride in terms:
            address = self.call(target, "wrapping_add_uint64_t", [address, self.scale_index(target, word, stride)])
        self.note(inst.name, address)
        if inst.name is not None:
            self.pointers.add(inst.name)  # the result addresses memory like any pointer

    def lower_alloca(self, inst: Inst, target: LBlock) -> None:
        """A local. Address-taken locals are wide slots of the frame (made in lower_body):
        the address ``pointer`` of native/graph.h gives a leaf whose parameter is a pointer.
        The rest are names of values only (what a store put there, a load reads back)."""
        name = inst.name
        if name is None:
            raise ParseError(f"{self.fn.name}: an alloca without its name")
        if name in self.wide_names:
            self.names[name] = ("value", self.wide[name].path)  # already made
        elif name in self.narrow_names:
            self.names[name] = self.literal(0)  # an unaliased local starts zero, like the frame
        else:
            raise Unsupported(f"{self.fn.name}: an alloca of {inst.type}")

    def lower_load(self, inst: Inst, target: LBlock) -> None:
        mask = self.word_of(inst.type)  # any integer width, or a ptr (an address word)
        count = self.bytes_of(inst.type)
        if inst.a is None:
            raise ParseError(f"{self.fn.name}: a load without its address")
        pointer = inst.a.name
        source = self.operand(inst.a)  # an unresolved constant expression refuses here
        if pointer is not None and self.out_position(pointer):
            # the out position (docs/23 4.1): a read back is the out word of the Object
            # this body writes through -- resolved at the use, where the writes land
            index = [n for _, n in self.fn.params].index(pointer)
            source = self.out_word(target, f"{self.path}.out_{index}", index)
        # the address is flat memory: a parameter, the address of one (the result of a
        # getelementptr), a global's cell (the Object value above resolves at run time),
        # or the word a constant expression that names a cell resolved to
        flat = (
            (pointer is None and source[0] == "value")
            or (pointer is not None and (pointer in self.pointers or pointer.startswith("@")))
        )
        if pointer in self.narrow_names:  # no bytes: the name is what a store put there
            if pointer in self.block_stored:
                made = source  # the store of this block, before the load: it reaches it
            elif not self.slot_stores.get(pointer):
                made = source  # no store anywhere: the local starts zero, like the frame
            else:
                raise Unsupported(
                    f"{self.fn.name}: the local {pointer} is read where only another block "
                    "wrote it (a value carried through memory): the subset needs the store "
                    "and the load in one block"
                )
        elif source[0] == "value" and source[1] in self.wide_of:
            # the bytes of a wide slot, through OP_MOVE
            made = self.move_value(target, source)
        elif flat and source[0] == "value":
            # memory at an address of the flat space: the bytes are copied into the shared
            # scratch wide slot first (OP_MOVE reads operands, not the memory an address
            # names), then the word is read out of the slot
            scratch = self.load_scratch_of(target)
            self.call_void(target, "memory_copy",
                           [scratch, self.literal(16), source, self.literal(count), self.literal(count)])
            made = self.move_value(target, scratch)
        elif flat:
            raise Unsupported(f"{self.fn.name}: a load of an address that is not a value")
        else:
            raise Unsupported(f"{self.fn.name}: a load that is not from a known slot")
        self.note(inst.name, rxemit.and_mask(self.at(target), made, mask))
        if inst.type == "ptr" and inst.name is not None:
            self.pointers.add(inst.name)  # what is loaded is an address of the flat memory

    def lower_store(self, inst: Inst, target: LBlock) -> None:
        mask = self.word_of(inst.type)  # any integer width, or a ptr (an address word)
        count = self.bytes_of(inst.type)
        if inst.b is None:
            raise ParseError(f"{self.fn.name}: a store without its address")
        pointer = inst.b.name
        if pointer is not None and pointer in self.ptr_params:
            # the store writes through a pointer the caller gave (an out cell of the
            # caller's frame): remembered to refuse where a call card would carry it
            self.stored_params.add(pointer)
        if pointer is not None and pointer in self.stored_params and self.out_position(pointer):
            # the out position (docs/23 4.1): this body crosses a card boundary and no
            # raw pointer outlives a step (S7) -- the write goes through the Object its
            # caller passes (the out cell it holds the handle of), resolved at each use
            index = [n for _, n in self.fn.params].index(pointer)
            scratch = self.move_value(target, self.narrow_operand(inst.a, mask), width=16)
            self.call_void(
                target,
                "memory_copy",
                [self.out_word(target, f"{self.path}.out_{index}", index),
                 self.literal(count), scratch, self.literal(8), self.literal(count)],
            )  # fmt: skip
            return
        made = self.narrow_operand(inst.a, mask)  # what is stored is the low bits of its word
        destination = self.operand(inst.b)  # an unresolved constant expression refuses here
        # the address is flat memory: a parameter, the address of one, a global's cell, or
        # the word a constant expression that names a cell resolved to
        flat = (
            (pointer is None and destination[0] == "value")
            or (pointer is not None and (pointer in self.pointers or pointer.startswith("@")))
        )
        if pointer in self.narrow_names:  # no bytes move: the name is what a load reads
            self.names[pointer] = made
            self.block_stored.add(pointer)  # a load of this block may read it back
        elif destination[0] == "value" and destination[1] in self.wide_of:
            # into a wide slot: the value into a scratch wide slot first, then the bytes over
            # (a leaf reads its pointer positions as addresses: the value must be in memory)
            scratch = self.move_value(target, made, width=16)
            self.call_void(
                target,
                "memory_copy",
                [destination, self.literal(16), scratch, self.literal(8), self.literal(count)],
            )
        elif flat and destination[0] == "value":
            # memory at an address of the flat space
            scratch = self.move_value(target, made, width=16)
            self.call_void(
                target,
                "memory_copy",
                [destination, self.literal(count), scratch, self.literal(8), self.literal(count)],
            )
        elif flat:
            raise Unsupported(f"{self.fn.name}: a store of an address that is not a value")
        else:
            raise Unsupported(f"{self.fn.name}: a store that is not to a known slot")

    def local_addr(self, target: LBlock, name: str) -> MOperand:
        """The address of a local's bytes: a wide slot is given to a leaf by ``pointer``
        (native/graph.h); a narrow local has no address this pass can hand out."""
        if name not in self.wide:
            raise Unsupported(f"{self.fn.name}: the address of a local that is not address-taken")
        return ("value", self.wide[name].path)

    def frame_moperand(self, target: LBlock, given: Operand) -> MOperand:
        """What a pointer parameter of a leaf is given: the address of a wide slot (``pointer``
        of native/graph.h gives it), or an address of the flat memory the run lives in (a
        parameter of the C function). Only these two reach a leaf's pointers here."""
        name = given.name
        if name is not None and name in self.wide_names:
            return self.local_addr(target, name)
        if name is not None and name in self.names and name not in self.pointers:
            raise Unsupported(
                f"{self.fn.name}: a pointer to a value (only locals and flat addresses reach "
                "a leaf's pointer parameters in this pass)"
            )
        return self.operand(given)  # a pointer parameter is an address of the flat memory

    def lower_unknown(self, inst: Inst, target: LBlock) -> None:
        raise Unsupported(f"{self.fn.name}: an instruction outside the subset: {inst.raw!r}")

    def lower_nlr(self, inst: Inst, target: LBlock) -> LBlock | None:
        """The nlr ABI (py/nlr.h): one frame, and nlr_jump is the graph's own ending.

        ``nlr_push(buf)`` is a setjmp: it returns 0 through the saved frame.  MicroPython
        comes back to that frame through ``nlr_jump``, the longjmp, which unwinds the whole
        C stack -- a stack the graph does not run: lower_call inlines callees by
        substitution and leaves calls to nothing but leaves (and, when the recursion of
        the inlining bottoms out, diverges).  A graph that could reach nlr_jump would need
        the machine behind it gone through; with no machine, there is no jump.

        So the second return of nlr_push is dead here, the first is its whole body, and
        nlr_jump is the divergence it always is in the IR:

        * ``nlr_push(buf)`` -> 0 (the first-return value; buf is untouched)
        * ``nlr_pop(buf)`` -> nothing
        * ``nlr_jump(code)`` -> diverges: the ``unreachable`` after it refuses the run

        where buf must be a name of the function (an alloca or a parameter) and every name
        that reaches the nlr ABI is protected: its other uses refuse the import (prescan).

        THE SOUNDNESS BOUNDARY.  The graph lowers the push of an unwinding frame; it
        cannot unwind into one.  The lowering is correct where the call the frame guards
        diverges (a leaf that Refuses -- mp_raise_* -- or a call to nothing) and the raise
        path of the C source ends there: the source raises, the graph refuses, and both
        stop before the bytes after the guarded call are computed.  A guarded call that
        RETURNS the graph computes as if it never raised -- the nlr_jump of that C would
        have had to reach through the frame to get there, and a name kept alive by
        nlr_push alone refuses every such use before the import can claim the bytes."""
        callee = inst.callee
        if callee == NLR_JUMP:
            if not inst.diverges:
                raise Unsupported(f"{self.fn.name}: nlr_jump that would come back (it is "
                                  "noreturn: the unreachable after it is missing)")  # fmt: skip
            return None  # the unreachable after it refuses the run on that path
        given = [argument for _, argument in inst.args]
        if len(given) != 1:
            raise Unsupported(f"{self.fn.name}: {callee} of no one buffer")
        name = given[0].name
        if name is None or name not in self.nlr_buffers:
            raise Unsupported(f"{self.fn.name}: {callee} of a buffer that is not protected")
        if callee == NLR_PUSH:
            self.note(inst.name, self.literal(0))  # the first-return value of the setjmp
        elif inst.name is not None:
            self.dropped.add(inst.name)  # nlr_pop is void in the ABI; no name may read it
        return None

    def lower_call(self, inst: Inst, target: LBlock) -> LBlock | None:
        callee = inst.callee
        if callee is None:
            raise ParseError(f"{self.fn.name}: a call without its callee")
        if callee in (NLR_PUSH, NLR_POP, NLR_JUMP):
            return self.lower_nlr(inst, target)
        if inst.diverges:
            # a call directly before an unreachable (a Rust panic path, a C noreturn call,
            # a MicroPython mp_raise_*): it never comes back, so it lowers to nothing and
            # the unreachable after it refuses the run on that path (prescan checked it)
            return None
        if callee.startswith("llvm."):
            return self.lower_intrinsic(inst, target)
        if is_panic_callee(callee):
            # a Rust panic path: the call diverges (prescan checked the unreachable after
            # it) and lowers to nothing -- the unreachable refuses the run on that path
            # (the status-branch idiom) instead of unwinding
            return None
        if is_runtime_callee(callee):
            raise Unsupported(
                f"{self.fn.name}: a call of {callee} (a C++ exception/RTTI runtime call or "
                "a Rust runtime call): the subset refuses exceptions, RTTI and allocation"
            )
        key = leaf_key(callee)
        if key is not None:
            return self.lower_leaf_call(inst, target, key)
        if callee in self.functions:
            return self.lower_inline(inst, target)
        # a callee that is no leaf and no function of the module: the call card, named
        # by the Function path the callee is imported at (the reference the delta links;
        # a cross-module callee is carried in `known`), else its bare name for the
        # composer to resolve where the whole image is known -- what no program of the
        # image answers is refused there (a runtime face another lane owns, a typo)
        bare = callee.rsplit(".", 1)[-1]
        return self.lower_card(inst, target, self.known.get(bare, bare))

    def lower_leaf_call(self, inst: Inst, target: LBlock, key: str) -> LBlock | None:
        _callee, arity, has_out, pointers = LEAVES[key]
        given = inst.args
        if len(given) != arity + (1 if has_out else 0):
            raise Unsupported(f"{self.fn.name}: {inst.callee} takes {arity} values")
        if has_out and given[-1][0] != "ptr":
            raise Unsupported(f"{self.fn.name}: {inst.callee} writes where the pass cannot see")
        arguments = [
            self.frame_moperand(target, argument) if position in pointers
            else self.operand(argument)
            for position, (_, argument) in enumerate(given[:arity])
        ]
        types = [
            U64 if type_name in ("i64", "i1", "ptr") else U32
            for type_name, _ in given[:arity]
        ]
        if not has_out:  # no out and no result: the call is just its work
            self.call_void(target, key, arguments)
            if inst.name is not None:
                self.dropped.add(inst.name)
            return None
        made = self.call(target, key, arguments, input_types=types)
        slot = given[-1][1]
        if slot.name is None or slot.name not in self.wide_names | self.narrow_names:
            raise Unsupported(f"{self.fn.name}: {inst.callee} writes where the pass cannot see")
        self.names[slot.name] = made  # the slot holds what the leaf wrote
        self.block_stored.add(slot.name)  # a load of this block may read it back
        if inst.name is not None:
            self.dropped.add(inst.name)  # the i32 status of the call is not a value of this pass

    def lower_inline(self, inst: Inst, target: LBlock) -> LBlock:
        """A call of another function of the module, inlined as its blocks (the composer's
        inline path). Recursion is refused rather than inlined forever.

        A call is a run of the callee's blocks inside the caller's: the caller's block goes
        to the callee's entry, and every Return of the callee goes to one join block that
        takes the answer as its parameter (a Return would end the whole run). The join is
        the block the caller goes on with."""
        callee_name = inst.callee
        if callee_name is None:
            raise ParseError(f"{self.fn.name}: a call without its callee")
        callee = self.functions[callee_name]
        if callee.name in self.stack or callee.name == self.fn.name:
            raise Unsupported(f"{self.fn.name}: recursion (a call of {callee.name} on the way)")
        if len(inst.args) != len(callee.params):
            raise Unsupported(f"{self.fn.name}: {callee.name} takes {len(callee.params)} values")
        arguments = [
            self.frame_moperand(target, argument) if type_name == "ptr"
            else self.narrow_operand(argument, self.mask_of(type_name))
            for type_name, argument in inst.args
        ]
        # the blocks of the call take the count of blocks made so far as their name: two
        # calls of one function, nested or not, never number their blocks the same way
        before = len(self.values)
        blocks_before = len(self.blocks)
        literals_before = set(self.literals)
        conditions_before = set(self.conditions)
        scratch_before = len(self.load_scratch)
        slots_before = set(self.slots)
        globals_before = dict(self.global_values)
        object_paths_before = set(self.object_paths)
        sub = Lowering(
            callee, self.module, self.functions, (*self.stack, self.fn.name),
            host=self, tag=f"call{len(self.blocks)}_",
        )  # fmt: skip
        sub.lower_body(arguments)
        entry = sub.entry
        if entry is None:  # lower_body always makes one; the type needs to hear it
            raise ParseError(f"{self.fn.name}: {callee.name} has no entry block")
        if not sub.function_results:
            raise Unsupported(f"{self.fn.name}: {callee.name} returns nothing")
        if len(entry.params) != len(sub.wide):
            raise Unsupported(
                f"{self.fn.name}: {callee.name} takes phi values at its entry block"
            )
        join = self.new_block()
        chosen = self.value(
            "Bound", join.path, f"blkparam[{len(join.params)}]", owner_block=join.path,
            width=16 if callee.ret == "ptr" else 8,
            type_path=WIDE if callee.ret == "ptr" else U64,
        )  # fmt: skip
        join.params.append(chosen)
        for block in sub.blocks:  # every Return of the callee answers into the join
            if block.terminator == "Return":
                answered = block.terminator_value
                if answered is None:
                    raise ParseError(f"{self.fn.name}: {callee.name} returns without a value")
                block.terminator = "Goto"
                block.terminator_value = None
                block.successors = [(join.path, [answered])]
        # the caller's block goes to the callee's entry: its locals (the entry's parameters)
        # start zero, the way a frame does
        terminator_before = target.terminator
        successors_before = target.successors
        target.terminator = "Goto"
        target.successors = [(entry.path, [self.literal(0)] * len(entry.params))]
        copied = sum(
            value.width + -value.width % 8
            for value in self.values[before:]
            if value.kind not in ("Literal", "Object")
        )
        if copied > GRAPH_CELL_BYTES:
            # a callee this big is no program of its own either -- its frame would not
            # fit even the empty run cell (RXF_GRAPH_CELL_BYTES), so the call can neither
            # stay inlined nor become a card that names a program no run can hold. The
            # caller is refused here rather than left a card that resolves to nothing at
            # run time. This is the honest reason (a frame past the cell), not the
            # inline budget.
            raise Unsupported(
                f"{self.fn.name}: {callee.name} needs a frame of {copied} bytes "
                f"(RXF_GRAPH_CELL_BYTES is {GRAPH_CELL_BYTES})"
            )
        if copied > INLINE_WORDS * 8:
            # the inline budget refuses to inline in favor of the call, not instead of
            # one: a body this big would become most of the caller's frame, so the
            # inline is undone and the call goes as the call card of graph.h (the
            # callee is a program of its own, named by the object id of its words).
            # A callee whose frame fits RXF_GRAPH_CELL_BYTES is a program the run can
            # hold; one past it is refused above.
            del self.values[before:]
            del self.blocks[blocks_before:]
            for key in set(self.literals) - literals_before:
                del self.literals[key]
            self.conditions.clear()
            self.conditions |= conditions_before
            del self.load_scratch[scratch_before:]
            # the shared registries of the globals go back too: a cell the inline named
            # and the rollback dropped must be named again by the caller's own body (a
            # stale entry would leave the operand pointing at no value of the graph)
            self.global_values.clear()
            self.global_values.update(globals_before)
            self.object_paths.clear()
            self.object_paths |= object_paths_before
            self.slots.clear()
            self.slots |= slots_before
            target.terminator = terminator_before
            target.successors = successors_before
            return self.lower_card(inst, target, f"{self.module}.{callee.name}")
        # an inlined callee's own call cards ride in the caller's body now: the caller is
        # a program of its own if the callee was
        self.card_calls |= sub.card_calls
        self.note(inst.name, ("value", chosen.path))
        if inst.type == "ptr" and inst.name is not None:  # the result holds an address
            self.pointers.add(inst.name)
        return join

    def lower_card(self, inst: Inst, target: LBlock, name: str) -> LBlock:
        """A call of a program: the call card of ``native/graph.h`` -- the callee's
        words are named by their object id, its parameters are the call's arguments as
        words of the caller's frame (a scalar one word, a handle its three), and its
        Return is the out word of the call. What the composer makes of this operation
        is the card; what a run makes of it is a frame of the run cell. ``name`` is the
        callee's function path of the image, or its bare name where the callee is a
        function of another module (the composer resolves it against the image)."""
        self.card_calls = True  # the body calls a program: it is a program of its own
        arguments = []
        for index, (type_name, argument) in enumerate(inst.args):
            local = argument.name
            if (
                type_name == "ptr"
                and local is not None
                and local in self.wide_names
                and self.card_out_param(name, index)
            ):
                # the out position (docs/23 4.1): the value the argument names becomes
                # the out cell of the callee's parameter -- its handle (three words, the
                # shapes run-entry ARGS takes) rides the frame, never a raw pointer to a
                # caller's value (S7); the local reads back the same cell after the call
                key = f"{name}.out_{index}"
                prior = self.out_alias.get(local)
                if prior is not None and prior != key:
                    # a dispatch of several bodies writes through this argument: the
                    # pass names one cell per body and the caller reads one cell back --
                    # refused rather than left reading the wrong one
                    raise Unsupported(
                        f"{self.fn.name}: an out position at a call through a function word "
                        "(several bodies write it, one cell the caller can read)"
                    )
                self.out_alias[local] = key
                self.names[local] = self.out_word(target, key, index)
                self.pointers.add(local)
                arguments.append(("value", self.out_cell(key, index).path))
                continue
            arguments.append(
                self.address_value(
                    target,
                    self.frame_moperand(target, argument) if type_name == "ptr"
                    else self.narrow_operand(argument, self.mask_of(type_name)),
                )
            )
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        # the answer of a pointer is the address word carried in a wide result (its
        # first bytes) -- the ret of ``finish`` answers a ptr the same way, and the run
        # copies an answer whole into the card's out (graph.c: an answer wider than the
        # out refuses), so the out of a card is as wide as the answers it can take
        result = self.value(
            "OperationResult", op, f"op{self.op_count}_result",
            owner_block=target.path, owner_op=op,
            width=16 if inst.type == "ptr" else 8,
            type_path=WIDE if inst.type == "ptr" else U64,
        )  # fmt: skip
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", arguments,
                [U64 if type_name in ("i64", "i1", "ptr") else U32
                 for type_name, _ in inst.args],
                result, name)
        )  # fmt: skip
        self.note(inst.name, ("value", result.path))
        if inst.type == "ptr" and inst.name is not None:  # the result holds an address
            self.pointers.add(inst.name)
        return target

    def lower_intrinsic(self, inst: Inst, target: LBlock) -> LBlock | None:
        callee = inst.callee
        if callee is None:
            raise ParseError(f"{self.fn.name}: a call without its callee")
        intrinsic = callee[len("llvm.") :]
        if intrinsic.startswith("lifetime."):
            return None
        if intrinsic in ("umin.i64", "umax.i64", "smin.i64", "smax.i64"):
            a = self.operand(inst.args[0][1])
            b = self.operand(inst.args[1][1])
            signed = intrinsic[0] == "s"
            if intrinsic.startswith(("umin", "smin")):
                return self.lower_select(self.less(target, a, b, signed), a, b, inst.name, target)
            return self.lower_select(self.less(target, b, a, signed), a, b, inst.name, target)
        if intrinsic.startswith(("fshl.i", "fshr.i")):
            width = int(intrinsic.rsplit(".i", 1)[1])
            a = self.operand(inst.args[0][1])
            b = self.operand(inst.args[1][1])
            c = self.operand(inst.args[2][1])
            return self.funnel(target, inst, a, b, c, width, intrinsic.startswith("fshl"))
        if intrinsic.startswith("memcpy."):
            # llvm.memcpy.p0.p0.i64(dst, src, len, volatile) -> the memory.copy leaf
            dst = self.frame_moperand(target, inst.args[0][1])
            src = self.frame_moperand(target, inst.args[1][1])
            length = self.operand(inst.args[2][1])
            self.call_void(target, "memory_copy", [dst, length, src, length, length])
            return None
        if intrinsic.startswith("memset."):
            # llvm.memset.p0.i64(dst, value, len, volatile) -> the memory.fill leaf
            dst = self.frame_moperand(target, inst.args[0][1])
            value = rxemit.and_mask(self.at(target), self.operand(inst.args[1][1]), 0xFF)
            length = self.operand(inst.args[2][1])
            self.call_void(target, "memory_fill", [dst, length, value, length])
            return None
        found = re.fullmatch(r"(uadd|sadd|usub|ssub|umul|smul)\.with\.overflow\.i(\d+)", intrinsic)
        if found:
            self.lower_with_overflow(inst, target, found.group(1), int(found.group(2)))
            return None
        raise Unsupported(f"{self.fn.name}: the intrinsic {inst.callee}")

    def lower_with_overflow(
        self, inst: Inst, target: LBlock, kind: str, width: int
    ) -> None:
        """``llvm.{uadd,sadd,usub,ssub,umul,smul}.with.overflow.iN(a, b)`` -> the
        aggregate ``(value, overflow)``: the arithmetic is one wrapping leaf and the
        overflow one bit (a carry/borrow, the sign form ``(a ^ s) & (b ^ s)``, or the
        product that leaves the width). An ``extractvalue`` reads the fields.

        The multiply bit is ``p != p_N`` (a product the words hold exactly: the operands
        are half the width or less) or ``(a != 0) & (p / a != b)`` at 64 bits -- the
        divide leaf answers 0 where it refuses (a is zero) and the ``and`` takes the bit
        only where the division ran. native/graph.h's own answer for these shapes is one
        checked_multiply whose status is this bit; that wiring waits for the status
        wiring of the roadmap (a leaf's status as a value), and the identity keeps every
        input of the differential comparable until then. A multiply of a width past 32
        and not 64 has no exact product in the 64-bit words and is refused."""
        if inst.name is None or len(inst.args) != 2:
            raise ParseError(f"{self.fn.name}: an overflow intrinsic without its operands")
        mask = self.mask_of(f"i{width}")
        a = self.narrow_operand(inst.args[0][1], mask)
        b = self.narrow_operand(inst.args[1][1], mask)
        one = self.literal(1)
        if kind in ("umul", "smul"):
            if width > 32 and width != 64:
                raise Unsupported(
                    f"{self.fn.name}: {inst.callee} (only widths of 32 bits and of 64 "
                    "have a product this pass can see whole)"
                )
            signed = kind == "smul"
            if signed:
                a = rxemit.sext_value(self.at(target), a, mask)
                b = rxemit.sext_value(self.at(target), b, mask)
            product = self.call(target, "wrapping_multiply_uint64_t", [a, b])
            answer = rxemit.and_mask(self.at(target), product, mask)
            if width < 64:
                back = rxemit.sext_value(self.at(target), answer, mask) if signed else answer
                overflow = self.negate(
                    target, self.call(target, "equal_uint64_t", [product, back]), one
                )
            else:
                quotient = self.call(
                    target, "divide_int64_t" if signed else "divide_uint64_t", [product, a]
                )
                missed = self.negate(
                    target, self.call(target, "equal_uint64_t", [quotient, b]), one
                )
                nonzero = self.negate(
                    target, self.call(target, "equal_uint64_t", [a, self.literal(0)]), one
                )
                overflow = self.call(target, "bit_and_uint64_t", [nonzero, missed])
            self.aggregates[inst.name] = (answer, overflow)
            return
        total = self.call(
            target,
            "wrapping_add_uint64_t" if kind in ("uadd", "sadd") else "wrapping_subtract_uint64_t",
            [a, b],
        )
        answer = rxemit.and_mask(self.at(target), total, mask)
        if kind == "uadd":
            overflow = self.less(target, answer, a, signed=False)  # a carry: sum < a
        elif kind == "usub":
            overflow = self.less(target, a, b, signed=False)  # a borrow: a < b
        else:
            overflow = self.signed_overflow(target, a, b, answer, kind, mask)
        self.aggregates[inst.name] = (answer, overflow)

    def signed_overflow(
        self, target: LBlock, a: MOperand, b: MOperand, answer: MOperand, kind: str, mask: int
    ) -> MOperand:
        """The overflow bit of a signed add/sub: the sign of (a ^ s) & (b ^ s) or of
        (a ^ b) & (a ^ s), of the sign-extended words (one compare per side)."""
        def mixed(left: MOperand, right: MOperand) -> MOperand:
            # left ^ right, as (left | right) - (left & right): no xor leaf exists
            either = self.call(target, "bit_or_uint64_t", [left, right])
            both = self.call(target, "bit_and_uint64_t", [left, right])
            return self.call(target, "wrapping_subtract_uint64_t", [either, both])

        sa = rxemit.sext_value(self.at(target), a, mask)  # the sign form reads the sign of the words
        sb = rxemit.sext_value(self.at(target), b, mask)
        ss = rxemit.sext_value(self.at(target), answer, mask)
        if kind == "sadd":
            joined = self.call(target, "bit_and_uint64_t", [mixed(sa, ss), mixed(sb, ss)])
        else:
            joined = self.call(target, "bit_and_uint64_t", [mixed(sa, sb), mixed(sa, ss)])
        return self.call(target, "shift_right_uint64_t", [joined, self.literal(63)])

    def lower_extractvalue(self, inst: Inst, target: LBlock) -> None:
        """``extractvalue { ... } %agg, i``: field ``i`` of an aggregate (what an
        ``llvm.*.with.overflow`` call or an ``insertvalue`` made)."""
        if inst.a is None or inst.to is None or inst.type is None:
            raise ParseError(f"{self.fn.name}: an extractvalue without its aggregate")
        aggregate = self.aggregates.get(inst.a.name) if inst.a.name is not None else None
        if aggregate is None:
            raise Unsupported(
                f"{self.fn.name}: an extractvalue of a value that is no aggregate "
                "of the subset"
            )
        fields = struct_fields(inst.type, self.structs)
        index = int(inst.to)
        if fields is None or index >= len(aggregate) or index >= len(fields):
            raise ParseError(f"{self.fn.name}: an extractvalue of field {index} of {inst.type}")
        self.note(inst.name, rxemit.and_mask(self.at(target), aggregate[index], self.word_of(fields[index])))

    def lower_insertvalue(self, inst: Inst, target: LBlock) -> None:
        """``insertvalue { ... } %agg, v, i``: the aggregate with field ``i`` replaced. A
        base of poison or undef is the zero aggregate (Operand.parse's poison convention):
        an unspecified field is any deterministic one, and zero is the frame's."""
        if inst.a is None or inst.b is None or inst.to is None or inst.type is None:
            raise ParseError(f"{self.fn.name}: an insertvalue without its operands")
        fields = struct_fields(inst.type, self.structs)
        index = int(inst.to)
        if fields is None or index >= len(fields):
            raise Unsupported(f"{self.fn.name}: an insertvalue into {inst.type}")
        if inst.a.name is not None:
            aggregate = self.aggregates.get(inst.a.name)
            if aggregate is None:
                raise Unsupported(
                    f"{self.fn.name}: an insertvalue into a value that is no aggregate "
                    "of the subset"
                )
        elif inst.a.constant is not None:  # poison or undef: the zero aggregate
            aggregate = tuple(self.literal(0) for _ in fields)
        else:
            raise ParseError(f"{self.fn.name}: an insertvalue without its aggregate")
        made = list(aggregate)
        made[index] = self.narrow_operand(inst.b, self.word_of(fields[index]))
        if inst.name is None:
            raise ParseError(f"{self.fn.name}: an insertvalue without its name")
        self.aggregates[inst.name] = tuple(made)

    def lower_indirect(self, inst: Inst, target: LBlock) -> LBlock | None:
        """A call through a function word: the type-slot dispatch of MicroPython (the
        unary_op slot of the @mp_type_* cells carries the address of the per-type body,
        and mp_unary_op calls it).

        The dispatch is on the word, not on a type lookup: every store of a function word
        by the bodies this import lowers (the boot that fills the slots of the type cells)
        arms it, and the chain compares the callee word against those arms, each arm the
        call card of the body whose word it is. A word the wiring names routes; what the
        card model cannot express is refused, never a guess:

        * no arm at all -- the import refuses the body (below): a call through a function
          word this import makes no body for. The card model names its callee when the
          words are made (the store that wires the slot is what names the body), so a
          dispatch whose words no store of this import carries cannot name one.
        * a word the arms do not name refuses the run at the chain's fall-through: the
          body it would name is outside this import (a type of the runtime another lane
          owns, a Python class's own slot) and no card of it exists. The runtime type
          lookup behind such a word is what the card model cannot express; the refuse is
          the honest end of the path (the status-branch idiom), not a wrong answer."""
        if inst.callee is None:
            raise ParseError(f"{self.fn.name}: an indirect call without its callee")
        callee = self.operand(Operand.parse(inst.callee))
        arms = sorted(self.wiring.items())
        if not arms:
            raise Unsupported(
                f"{self.fn.name}: a call through a function word (a slot dispatch): this "
                "import makes no body its word can name (the card model names its callee "
                "when the words are made)"
            )
        if callee[0] == "literal":
            # a constant function word (the immediate the C names): its arm is known here
            for word, card in arms:
                if word & FULL == callee[1] & FULL:
                    arm = self.lower_card(
                        Inst("call", type=inst.type, callee=card, args=list(inst.args)),
                        target, card,
                    )
                    made = arm.ops[-1].result
                    if made is None:
                        raise ParseError(f"{self.fn.name}: a call card that produced no result")
                    self.note(inst.name, ("value", made.path))
                    if inst.type == "ptr" and inst.name is not None:
                        self.pointers.add(inst.name)
                    return target
            raise Unsupported(
                f"{self.fn.name}: a call through the function word {callee[1]} (a slot "
                "dispatch): this import wires no body for it"
            )
        join = self.new_block()
        chosen = self.value(
            "Bound", join.path, f"blkparam[{len(join.params)}]", owner_block=join.path,
            width=16 if inst.type == "ptr" else 8,
            type_path=WIDE if inst.type == "ptr" else U64,
        )  # fmt: skip
        join.params.append(chosen)
        current = target
        for index, (word, card) in enumerate(arms):
            same = self.call(current, "equal_uint64_t", [callee, self.literal(word & FULL)])
            arm = self.new_block()
            called = self.lower_card(
                Inst("call", type=inst.type, callee=card, args=list(inst.args)), arm, card
            )
            made = called.ops[-1].result
            if made is None:
                raise ParseError(f"{self.fn.name}: a call card that produced no result")
            arm.terminator = "Goto"
            arm.successors = [(join.path, [("value", made.path)])]
            if index == len(arms) - 1:
                # the fall-through: a word no arm names (the refuse of the boundary above)
                fall = self.new_block()
                fall.terminator = "Refuse"
                fall.terminator_value = self.literal(UNSUPPORTED_STATUS)
                tails = (arm, fall)
            else:
                nxt = self.new_block()
                tails = (arm, nxt)
            current.terminator = "Branch"
            current.condition = self.own_condition(same, current)
            current.successors = [(tail.path, []) for tail in tails]
            current = tails[1]
        self.note(inst.name, ("value", chosen.path))
        if inst.type == "ptr" and inst.name is not None:
            self.pointers.add(inst.name)
        return join

    def lower_exception(self, inst: Inst, target: LBlock) -> None:
        raise Unsupported(
            f"{self.fn.name}: a C++ exception edge ({inst.raw!r}): the subset refuses "
            "exceptions (invoke, landingpad and resume never lower)"
        )

    def funnel(
        self, target: LBlock, inst: Inst, a: MOperand, b: MOperand, c: MOperand, width: int, left: bool
    ) -> LBlock:
        """``llvm.fshl/fshr.iN(a, b, c)``: the funnel shift of the 2N-bit word (a, b).

        ``fshl`` takes the low N bits of ``(a << c) | (b >> (N - c))`` with the count
        reduced modulo N; ``fshr`` mirrors it (``(a << (N - c)) | (b >> c)``).  Each
        lowers to two masked shifts and an or, with a select on ``c & (N-1) == 0`` —
        the one count where the complementary shift degenerates to a shift by N (0
        after masking for N < 64, but 0 modulo 64 for N = 64)."""
        mask = self.mask_of(f"i{width}")
        count = rxemit.and_mask(self.at(target), c, width - 1)  # i1 keeps the count at 0
        other = self.call(target, "wrapping_subtract_uint64_t", [self.literal(width), count])
        if left:
            hi = self.call(target, "shift_left_uint64_t", [a, count], input_types=[U64, U32])
            lo = self.call(target, "shift_right_uint64_t", [b, other], input_types=[U64, U32])
            base = a
        else:
            hi = self.call(target, "shift_left_uint64_t", [a, other], input_types=[U64, U32])
            lo = self.call(target, "shift_right_uint64_t", [b, count], input_types=[U64, U32])
            base = b
        shifted = rxemit.and_mask(self.at(target), self.call(target, "bit_or_uint64_t", [hi, lo]), mask)
        zero = self.call(target, "equal_uint64_t", [count, self.literal(0)])
        return self.lower_select(zero, base, shifted, inst.name, target)


# ---- the entry points ------------------------------------------------------------------------

CLANG = "clang-18"
CLANGXX = "clang++-18"
RUSTC = shutil.which("rustc") or str(Path.home() / ".cargo" / "bin" / "rustc")


def compile_c(source: str | Path) -> str:
    """The textual LLVM IR of a C file: ``clang-18 -S -emit-llvm -O1`` (no pip dependency).

    ``RXF_IMPORT_FACES`` is defined on this side and not on the native link of the
    differential: a corpus may give the module IR bodies for the libc faces its verbatim
    bodies call (the IR-shim TU of ``import_corpus/qstr_faces.c``), while the natively
    compiled binary binds the same calls to libc.  Nothing else in the compile changes.
    """
    done = subprocess.run(
        [CLANG, "-S", "-emit-llvm", "-O1", "-DRXF_IMPORT_FACES", str(source), "-o", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise ParseError(f"{CLANG} could not compile {source}: {done.stderr.strip()}")
    return done.stdout


def compile_cpp(source: str | Path) -> str:
    """The textual LLVM IR of a C++ file: ``clang++-18 -S -emit-llvm -O1`` (the extern "C"
    subset of the source, so every name of the module is a plain C name)."""
    done = subprocess.run(
        [CLANGXX, "-S", "-emit-llvm", "-O1", str(source), "-o", "-"],
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode:
        raise ParseError(f"{CLANGXX} could not compile {source}: {done.stderr.strip()}")
    return done.stdout


def compile_rs(source: str | Path) -> str:
    """The textual LLVM IR of a Rust file: ``rustc --emit=llvm-ir -C opt-level=0
    -C panic=abort`` (opt-level 0 is what a bare rustc answers; the functions are
    ``#[no_mangle] pub extern "C"`` so their names survive the mangling)."""
    # a working directory of its own: rustc drops ``stdout.<crate>.<hash>-cgu.*.rcgu.ll``
    # codegen halves next to the output name in the working directory, and two imports
    # at once (the parallel test runs) collide on those names and lose a half -- the
    # copy to stdout then finds no file
    with tempfile.TemporaryDirectory(prefix="rximport-") as own:
        done = subprocess.run(
            [RUSTC, "--emit=llvm-ir", "--crate-type=lib", "-C", "opt-level=0", "-C",
             "panic=abort", str(Path(source).resolve()), "-o", "-"],
            capture_output=True,
            text=True,
            check=False,
            cwd=own,
        )
    if done.returncode:
        raise ParseError(f"{RUSTC} could not compile {source}: {done.stderr.strip()}")
    return done.stdout


def compile_source(source: str | Path) -> str:
    """The textual LLVM IR of a source file of the corpus: the entrance format itself for a
    ``.ll`` (read as it stands, no compiler runs -- docs/22), the compiler by its suffix for
    the rest (``.c`` is clang, ``.cpp`` is clang++, ``.rs`` is rustc)."""
    suffix = Path(source).suffix
    if suffix == ".ll":
        return Path(source).read_text()
    if suffix == ".c":
        return compile_c(source)
    if suffix == ".cpp":
        return compile_cpp(source)
    if suffix == ".rs":
        return compile_rs(source)
    raise ParseError(f"{source}: no compiler of the subset for a {suffix} file")


def graphs_of(
    text: str, module: str = "imported", only: set[str] | None = None,
    known: dict[str, str] | None = None,
) -> dict[str, LGraph]:
    """The functions of a textual module as graphs; ``only`` names the ones wanted.

    A call of another function of the module is inlined into the caller's graph (the
    composer's inline path), so a function that is only a callee of a wanted one does not
    get a graph of its own. A function outside the subset raises ``Unsupported`` when it is
    asked for (in ``only``), and is skipped when it is not: a corpus file may carry helpers
    this pass cannot lower. ``known`` maps the C name of a function of another module to
    the Function path it is imported at, so a call of one of them becomes a call card that
    names that path (the reference the delta links).
    """
    model = module_model(text)  # the width model of the module's words (the datalayout)
    parsed, structs, globals_ = parse_module(text, known=known)
    functions = {function.name: function for function in parsed}
    declared = declared_functions(text)
    wired = function_wiring(parsed, module, only, declared, known or {}, model)
    found: dict[str, LGraph] = {}
    for function in parsed:
        if only is not None and function.name not in only:
            continue
        found[function.name] = Lowering(
            function, module, functions, structs=structs, globals=globals_, known=known,
            declared=declared, wiring=wired, model=model,
        ).lower()
    if only is not None and set(found) != only:
        missing = ", ".join(sorted(only - set(found)))
        raise ParseError(f"not functions of the module: {missing}")
    return {name: found[name] for name in sorted(found)}


# ---- the source beside the IR: comment capture and the provenance claim (docs/22) -------------

# The comment marks of the entrance languages: ``;`` is the LLVM IR's line comment, ``//`` the
# C family's (and Rust's), and ``/* */`` is a block comment of both. A ``#`` line of a C source
# is a preprocessor directive, never a comment: nothing lifts it.
COMMENT_MARKS = {".ll": (";",), ".c": ("//",), ".cpp": ("//",), ".rs": ("//",)}

# The task markers whose comments import as their own managed kind (docs/22): a TODO is a fact
# with a state, not a comment that rots.
TASK_MARKER = re.compile(r"\s*(TODO|FIXME|XXX)\b")

# A C character literal (``'x'``, ``'\n'``): consumed as one token so its quote cannot open a
# string. A Rust lifetime (``&'a``) matches nothing here and stays an ordinary character.
_CHAR_LITERAL = re.compile(r"'(?:\\.|[^\\'])'")


@dataclass(frozen=True)
class Comment:
    """One comment of a source file: the lines it occupies (1-based), its text, its task marker."""

    first_line: int
    last_line: int
    text: str
    todo: bool


def _comment(first: int, last: int, raw: str) -> Comment | None:
    """The comment as a fact: its stars stripped, its task marker seen. An empty comment is
    not a fact and is left out (nothing to say is nothing to lift)."""
    body = "\n".join(re.sub(r"^\s*\* ?", "", part) for part in raw.splitlines()).strip()
    if not body:
        return None
    return Comment(first, last, body, TASK_MARKER.match(body) is not None)


def scan_comments(src: str, marks: Sequence[str] = ("//", ";")) -> list[Comment]:
    """Every comment of ``src``, with the line span it occupies.

    The scanner reads the file the way ``_strip_comment`` reads one IR line: a mark inside a
    string is a string. A ``/* */`` block carries the newlines it contains (its span is the
    line it opens on through the line it closes on); a line comment ends at its newline.
    """
    out: list[Comment] = []
    line = 1
    i = 0
    n = len(src)
    while i < n:
        ch = src[i]
        if ch == "\n":
            line += 1
            i += 1
        elif ch == '"':
            i += 1
            while i < n and src[i] != '"':
                if src[i] == "\\":
                    i += 1
                elif src[i] == "\n":
                    line += 1
                i += 1
            i += 1
        elif src.startswith("/*", i):
            first = line
            body = i + 2
            i = body
            while i + 1 < n and src[i : i + 2] != "*/":
                if src[i] == "\n":
                    line += 1
                i += 1
            comment = _comment(first, line, src[body:i])
            if comment is not None:
                out.append(comment)
            i += 2
        else:
            literal = _CHAR_LITERAL.match(src, i)
            if literal is not None:
                i = literal.end()
                continue
            mark = next((mark for mark in marks if src.startswith(mark, i)), None)
            if mark is None:
                i += 1
                continue
            body = i + len(mark)
            end = src.find("\n", body)
            end = n if end < 0 else end
            comment = _comment(line, line, src[body:end])
            if comment is not None:
                out.append(comment)
            i = end
    return out


def comment_targets(src: str, comments: Sequence[Comment], names: Sequence[str]) -> list[str]:
    """The function each comment introduces, or ``""`` for the module it documents.

    A comment rides on the definition it directly introduces (docs/22): the next line that is
    neither blank nor comment names exactly one imported function at its declarator -- the
    ``define ... @name(`` of the entrance format or the C form above a body. Anything else --
    a comment another comment speaks after, one inside a body, one before a line no definition
    matches -- is free-floating and documents the module. This is an editorial placement; the
    locator a comment is cited with is the file's, never a guess at a finer one.
    """
    lines = src.splitlines()
    inside = {number for comment in comments for number in range(comment.first_line, comment.last_line + 1)}
    out: list[str] = []
    for comment in comments:
        name = ""
        for number in range(comment.last_line + 1, len(lines) + 1):
            if number in inside:
                break  # another comment speaks first: this one is free-floating
            line = lines[number - 1].strip()
            if not line:
                continue
            matched = [
                fn for fn in names if re.match(rf"^[\w\s\*@]*\b{re.escape(fn)}\s*\(", line)
            ]
            name = matched[0] if len(matched) == 1 else ""
            break
        out.append(name)
    return out


def relative(path: str | Path) -> str:
    """The path a locator names: relative to the run's working directory when it is under it
    (docs/22: ``path=py/obj.c``), the path as given when it is not."""
    text = str(path)
    if not text:
        return text
    rel = os.path.relpath(text, os.getcwd())
    return rel if not rel.startswith("..") else text


@dataclass
class Provenance:
    """One import run's claim (docs/22): the tier it declares and the origin facts behind it.

    The ``--provenance`` flag is one spec -- ``tier,repository=...,commit=...,license=...``:
    the tier word first (``cite`` when it is left out) and the facts after it as ``key=value``
    (``upstream`` defaults to the repository, ``importer`` to this pass). The three facts a
    claim stands on -- the repository, the commit taken, the license -- are required; at
    ``hash`` the facts are refused (hash is identity without location). ``tier=None`` is the
    run that claims nothing (no flag at all): it lifts the comments and the report says
    ``provenance: none``. ``was_once_python`` is the marker of docs/23 5 (docs/22: provenance
    for code that was Python): the Python strand sets it for every module it compiles and
    the report speaks it; every C, C++ and Rust import leaves it False.
    """

    tier: str | None = "cite"
    repository: str = ""
    commit: str = ""
    license: str = ""
    upstream: str = ""
    importer: str = "rximport"
    # the marker *was once Python* of docs/23 5 and docs/22 (a fact at every level --
    # cheap and permanent): True where the strand compiled Python to the graphs, False
    # for every C, C++ or Rust import. The slot is the record's own; the report speaks
    # it, and a claim that names no tier still keeps the fact.
    was_once_python: bool = False
    origin_name: str = "origin"
    files: list[dict] = field(default_factory=list)
    _origin: int | None = field(default=None, compare=False)

    @classmethod
    def parse(cls, spec: Provenance | str | Sequence[str]) -> Provenance:
        """The flag's spec as a run: ``full,repository=...,commit=...,license=...`` (or words)."""
        if isinstance(spec, Provenance):
            return spec
        words = spec.split(",") if isinstance(spec, str) else list(spec)
        facts: dict[str, str] = {}
        tiers: list[str] = []
        for word in words:
            key, sep, value = word.strip().partition("=")
            if sep:
                if key not in ("repository", "upstream", "commit", "license", "importer"):
                    raise ValueError(f"--provenance has no {key!r} fact")
                facts[key] = value
            else:
                tiers.append(key)
        if len(tiers) > 1:
            raise ValueError(f"--provenance takes one tier word, not {len(tiers)}")
        tier = tiers[0] if tiers else ("cite" if facts else "")
        if tier not in ("full", "cite", "hash"):
            # The floor speaks first (docs/22): an anonymous or unknown word is refused by
            # the image itself at the first record -- no fact list buys past it.
            return cls(
                tier=tier,
                repository=facts.get("repository", ""),
                upstream=facts.get("upstream", ""),
                commit=facts.get("commit", ""),
                license=facts.get("license", ""),
                importer=facts.get("importer", "rximport"),
            )
        if tier == "hash":
            if facts:
                raise ValueError(
                    "the hash tier is identity without location: it takes no origin facts"
                )
            return cls(tier=tier)
        missing = [key for key in ("repository", "commit", "license") if not facts.get(key)]
        if missing:
            raise ValueError(f"--provenance is missing its {missing[0]!r} fact")
        return cls(
            tier=tier,
            repository=facts["repository"],
            commit=facts["commit"],
            license=facts["license"],
            upstream=facts.get("upstream", facts["repository"]),
            importer=facts.get("importer", "rximport"),
        )

    def open(self, image) -> None:
        """The run's first write: its origin record (cite and up), before anything is imported.

        This is where the floor speaks (docs/22): a tier word below hash is refused here by
        the image -- ``the provenance floor is hash: the importer takes no anonymous input`` --
        and the run stops with the image as it was. At ``hash`` there is no origin record
        (hash is identity without location); the file's hash record is written per file.
        """
        if self.tier is None or self.tier == "hash":
            return
        if self._origin is None:
            self._origin = image.doc_origin(
                0,
                self.origin_name,
                self.tier,
                upstream=self.upstream,
                repository=self.repository,
                commit=self.commit,
                license=self.license,
                importer=self.importer,
            )

    def record(
        self,
        image,
        module: str,
        mapping: dict[str, str],
        path: str,
        text: str,
        comments: Sequence[tuple[Comment, str]],
    ) -> None:
        """One file's claim (docs/22): its citation (cite up) or its hash record (hash), and
        every captured comment lifted and cited through the file's citation.

        The citation spans the whole file -- 1 through its last line -- because the whole file
        is what the pass read and what the sha256 hashes; a finer locator would need the debug
        info this subset's compiles do not carry, and the importer never guesses. Each comment
        rides on the citation as a ``placed_on`` document, so every quoted line keeps the
        locator (path/lines/sha256) and the permalink ``doc_permalink`` derives from it.
        """
        stem = Path(path).stem
        sha256 = hashlib.sha256(text.encode()).hexdigest()
        record = {
            "path": path,
            "sha256": sha256,
            "module": module,
            "functions": sorted(mapping),
            "citation": None,
            "hash": None,
            "locator": None,
            "permalink": None,
            "comments": [],
        }
        self.files.append(record)
        lifted: list[int] = []
        for comment, target in comments:
            owner = image.handle(f"{module}.{target}") if target else image.handle(module)
            name = f"comment_{comment.first_line}_{comment.last_line}"
            taken = image.resolve(f"{image.path(owner)}.{name}")
            if taken is not None:  # two marks opening on one line: each fact keeps its name
                name = f"{name}_{len(lifted)}"
            doc = image.lift_comment(
                owner,
                name,
                comment.text,
                int(DocKind.Todo) if comment.todo else int(DocKind.Narrates),
                role=int(DocRole.Narrates),
            )
            lifted.append(doc)
            record["comments"].append(
                {
                    "name": image.path(doc),
                    "first_line": comment.first_line,
                    "last_line": comment.last_line,
                    "kind": "DocKind.Todo" if comment.todo else "DocKind.Narrates",
                    "target": image.path(owner),
                }
            )
        if self.tier is None:
            return
        if self.tier == "hash":
            record["hash"] = image.path(image.doc_hash(0, f"{stem}_sha", "hash", sha256))
            return
        origin = self._origin
        stored = None
        if self.tier == "full":
            stored = image.doc_source(0, f"{stem}_source", "full", text, origin=origin)
            for owner in [image.handle(module), *(image.handle(p) for p in mapping.values())]:
                image.doc_place(owner, stored, int(DocRole.References))
        citation = image.doc_cite(
            0,
            f"{stem}_cite",
            self.tier,
            [image.handle(module), *(image.handle(p) for p in mapping.values()), *lifted],
            [origin] if stored is None else [origin, stored],
            path=path,
            first_line=1,
            last_line=len(text.splitlines()),
            sha256=sha256,
        )
        record["citation"] = image.path(citation)
        record["locator"] = image.doc_locator(citation)
        record["permalink"] = image.doc_permalink(citation)

    def report(self) -> str:
        """What the run claimed, fact by fact; the first line names the tier.

        The locator and the permalink it derives are read back out of the image -- the report
        says what is recorded, never what was meant.
        """
        out = [f"provenance: {'none' if self.tier is None else self.tier}"]
        if self.was_once_python:
            out.append("marker: was once Python")
        if self._origin is not None:
            out.append(
                f"origin: {self.origin_name} repository={self.repository}"
                f" upstream={self.upstream} commit={self.commit}"
                f" license={self.license} importer={self.importer}"
            )
        for record in self.files:
            out.append(f"file: {record['path']} sha256={record['sha256']}")
            if record.get("hash"):
                out.append(f"hash: {record['hash']}")
            if record["locator"] is not None:
                locator = record["locator"]
                out.append(
                    f"cite: {record['citation']}"
                    f" lines={locator['first_line']}-{locator['last_line']}"
                    f" {record['permalink']}"
                )
            for comment in record["comments"]:
                out.append(
                    f"comment: {comment['name']}"
                    f" lines={comment['first_line']}-{comment['last_line']}"
                    f" kind={comment['kind']} target={comment['target']}"
                )
        return "\n".join(out) + "\n"


def import_into(
    image, text: str, module: str = "imported", only: set[str] | None = None,
    known: dict[str, str] | None = None, provenance=None,
    source: tuple[str, str] | None = None,
) -> dict[str, str]:
    """One delta onto the image; the C name of every imported function to its Function path.

    ``image`` is a ``pymergetic.rxftk.core.object.Image`` with the starter already in it (the
    leaves the graphs call live there); ``Image.apply`` is the only thing done to it.
    ``known`` maps the C name of a function of another module (already imported into the
    image) to the Function path it is at, so a call of one of them becomes a call card
    naming that path. The mapping this returns is the shape to pass as ``known`` to a
    later import of a module that calls these.

    ``source`` is the source beside the IR (docs/22): ``(path, text)`` of the file the IR
    came from (the ``.ll`` itself at the entrance format). Its comments are lifted into the
    image whatever the run claims. ``provenance`` is the run's claim -- a ``Provenance`` or
    its ``--provenance`` spec: one citation per source file, its lifted comments placed on
    it. Without a claim the comments are lifted and nothing cites them (the report says
    ``provenance: none``). A claim needs ``source``: a citation names the file it came from
    (the importer takes no anonymous input).
    """
    graphs = list(graphs_of(text, module, only, known=known).values())
    if not graphs:
        return {}
    if provenance is not None and source is None:
        raise ValueError("a claim names its file: import the source, not its IR text alone")
    prov = (
        Provenance.parse(provenance) if provenance is not None else Provenance(tier=None)
    )
    if source is not None:
        path, src = source
        comments = scan_comments(src, COMMENT_MARKS.get(Path(path).suffix, ("//",)))
        targets = comment_targets(src, comments, [graph.name for graph in graphs])
        claimed = list(zip(comments, targets))
    else:
        path, src, claimed = "", "", []
    prov.open(image)  # the claim's first write: the floor speaks before anything is imported
    image.apply(emit_delta(graphs, module))
    mapping = {graph.name: graph.path for graph in graphs}
    if source is not None:
        prov.record(image, module, mapping, relative(path), src, claimed)
    return mapping


def import_c_file(
    image, source: str | Path, module: str = "imported", only: set[str] | None = None,
    provenance=None,
) -> dict[str, str]:
    """Compile a C file and import its functions (its comments lifted beside the IR)."""
    return import_into(
        image, compile_c(source), module, only,
        provenance=provenance, source=(str(source), Path(source).read_text()),
    )


def import_source_file(
    image, source: str | Path, module: str = "imported", only: set[str] | None = None,
    known: dict[str, str] | None = None, provenance=None,
) -> dict[str, str]:
    """Read a ``.ll`` (the entrance format, as-is) or compile a C, C++ or Rust file (by its
    suffix) and import its functions (its comments lifted beside the IR)."""
    return import_into(
        image, compile_source(source), module, only, known=known,
        provenance=provenance, source=(str(source), Path(source).read_text()),
    )


def import_run(
    image, sources: Sequence[str | Path], module: str | None = None,
    only: set[str] | None = None, known: dict[str, str] | None = None, provenance=None,
) -> str:
    """One import run over every source (docs/22): one origin record, one citation per file
    compiled, every comment of every file lifted and cited. The run answers its report.

    ``provenance`` is the run's claim -- the ``--provenance`` spec
    (``full,repository=...,commit=...,license=...``) or a ``Provenance``. Without it the run
    claims nothing: the comments are still lifted and the report says ``provenance: none``.
    """
    prov = Provenance.parse(provenance) if provenance is not None else Provenance(tier=None)
    paths = [Path(source) for source in sources]
    if not paths:
        raise ValueError("an import run compiles at least one source file")
    stems = [path.stem for path in paths]
    if len(set(stems)) != len(stems):
        raise ValueError(f"the sources of one run need distinct names: {', '.join(sorted(stems))}")
    if module is not None and len(paths) > 1:
        raise ValueError("one module is one source: a run names each module after its file")
    known = dict(known or {})
    for path in paths:
        imported = import_source_file(
            image, path, module=module or path.stem, only=only, known=known, provenance=prov,
        )
        known.update(imported)
    return prov.report()


def main(argv: Sequence[str] | None = None) -> int:
    """The command line of the import lane: compile the sources, import them, print the report.

    ``rximport.py source.c --provenance full,repository=...,commit=...,license=...`` -- the one
    flag carries the run's tier and its origin facts (docs/22); without it the report says
    ``provenance: none`` and the comments are still lifted.
    """
    parser = argparse.ArgumentParser(
        prog="rximport",
        description="Import .ll/.c/.cpp/.rs sources into an RXF image; the report says what "
        "the run claimed.",
    )
    parser.add_argument(
        "sources", nargs="+", metavar="SOURCE", help="a .ll (as-is), .c, .cpp or .rs file",
    )
    parser.add_argument(
        "--module", help="the module the one source imports to (default: its name)",
    )
    parser.add_argument(
        "--provenance", metavar="SPEC",
        help="tier,repository=...,commit=...,license=...[,upstream=...][,importer=...] -- "
        "the run's claim (docs/22); without it the run says 'provenance: none'",
    )
    parser.add_argument(
        "--starter", default=str(Path(__file__).resolve().parents[2] / "starter"),
        help="the starter directory the run begins from (the leaves the graphs call)",
    )
    parser.add_argument(
        "--output", "-o", metavar="DELTA", help="where the imported image's delta is written",
    )
    args = parser.parse_args(argv)
    image = tree.load(args.starter)
    sys.stdout.write(import_run(image, args.sources, module=args.module, provenance=args.provenance))
    if args.output:
        Path(args.output).write_text(image.export())
    return 0
