"""A small interpreter of the programs of ``native/graph.h`` in Python, for the programs of ``.rxfs``.

It runs what ``tools/native/rxfc.py`` makes and what the compiler in ``native/tiny.rxfs`` does with
it, on a flat memory, with the numeric and the memory leaves written here. It is a tool for
writing such programs (a step is a Python call, not an emulated machine); what a program does on
the real interpreter is what ``tests/test_tiny.py`` asks.
"""

from __future__ import annotations

import struct

import graph_header

N = graph_header.numbers()
LEAF = graph_header.leaves()
BY_ID = {value: name for name, value in LEAF.items()}
MASK = (1 << 64) - 1
NONE = N["RXF_GRAPH_NONE"]
OP = {k: v for k, v in ((N[f"RXF_OP_{n}"], n) for n in (
    "CALL", "MOVE", "MAP", "FIELD", "CONSTRUCT", "LESS", "INCREMENT", "GOTO", "BRANCH",
    "SWITCH", "RETURN", "REFUSE"))}  # fmt: skip
FRAME_AT = 0x100000  # where the frame of a run lies in the memory

OPD_LITERAL = N["RXF_OPERAND_LITERAL"]
OPD_SLOT = N["RXF_OPERAND_SLOT"]
MODE_ZERO = N["RXF_MODE_ZERO"]
OP_CALL = N["RXF_OP_CALL"]
OP_MOVE = N["RXF_OP_MOVE"]
OP_GOTO = N["RXF_OP_GOTO"]
OP_BRANCH = N["RXF_OP_BRANCH"]
OP_SWITCH = N["RXF_OP_SWITCH"]
OP_RETURN = N["RXF_OP_RETURN"]
OP_REFUSE = N["RXF_OP_REFUSE"]


class Refused(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class Memory:
    """Bytes at addresses, sparse by page, zero until written."""

    PAGE = 1 << 16

    def __init__(self) -> None:
        self.pages: dict[int, bytearray] = {}

    def page(self, number: int) -> bytearray:
        found = self.pages.get(number)
        if found is None:
            found = self.pages[number] = bytearray(self.PAGE)
        return found

    def read(self, address: int, size: int) -> bytes:
        page, at = divmod(address, self.PAGE)
        if at + size <= self.PAGE:
            return bytes(self.page(page)[at : at + size])
        out = bytearray()
        while size:
            page, at = divmod(address, self.PAGE)
            take = min(size, self.PAGE - at)
            out += self.page(page)[at : at + take]
            address += take
            size -= take
        return bytes(out)

    def write(self, address: int, data: bytes) -> None:
        page, at = divmod(address, self.PAGE)
        if at + len(data) <= self.PAGE:
            self.page(page)[at : at + len(data)] = data
            return
        offset = 0
        while offset < len(data):
            page, at = divmod(address + offset, self.PAGE)
            take = min(len(data) - offset, self.PAGE - at)
            self.page(page)[at : at + take] = data[offset : offset + take]
            offset += take

    def u64(self, address: int) -> int:
        return struct.unpack("<Q", self.read(address, 8))[0]


def _leaf_add(args, memory):
    return 0, (args[0] + args[1]) & MASK


def _leaf_subtract(args, memory):
    return 0, (args[0] - args[1]) & MASK


def _leaf_and(args, memory):
    return 0, args[0] & args[1]


def _leaf_or(args, memory):
    return 0, args[0] | args[1]


def _leaf_shift_left(args, memory):
    return 0, (args[0] << (args[1] & 0xFFFFFFFF)) & MASK


def _leaf_shift_right(args, memory):
    return 0, args[0] >> (args[1] & 0xFFFFFFFF)


def _leaf_equal(args, memory):
    return 0, int(args[0] == args[1])


def _leaf_less(args, memory):
    return 0, int(args[0] < args[1])


# The multiply, divide and remainder leaves of native/graph.h, as native/basics.c has them: a
# division by zero refuses 2 (DIVISION_BY_ZERO), the signed forms refuse 1 (OVERFLOW) at
# INT_MIN / -1, the checked multiplies refuse 1 where the product leaves the type, the
# wrapping multiply wraps, and the signed division and remainder truncate toward zero.
REFUSE_OVERFLOW = 1
REFUSE_DIVISION_BY_ZERO = 2


def _signed(value: int, width: int) -> int:
    value &= (1 << width) - 1
    return value - (1 << width) if value >> (width - 1) else value


def _leaf_multiply_wrapping_uint64_t(args, memory):
    return 0, (args[0] * args[1]) & MASK


def _leaf_checked_multiply_uint64_t(args, memory):
    product = args[0] * args[1]
    return (REFUSE_OVERFLOW, 0) if product > MASK else (0, product)


def _leaf_checked_multiply_int64_t(args, memory):
    product = _signed(args[0], 64) * _signed(args[1], 64)
    if not (-(1 << 63) <= product < (1 << 63)):
        return REFUSE_OVERFLOW, 0
    return 0, product & MASK


def _divide(width: int, signed: bool, args):
    mask = (1 << width) - 1
    low = -(1 << (width - 1)) if signed else 0
    a = _signed(args[0], width) if signed else args[0] & mask
    b = _signed(args[1], width) if signed else args[1] & mask
    if b == 0:
        return REFUSE_DIVISION_BY_ZERO, 0
    if signed and a == low and b == -1:
        return REFUSE_OVERFLOW, 0
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    return 0, quotient & mask


def _remainder(width: int, signed: bool, args):
    mask = (1 << width) - 1
    low = -(1 << (width - 1)) if signed else 0
    a = _signed(args[0], width) if signed else args[0] & mask
    b = _signed(args[1], width) if signed else args[1] & mask
    if b == 0:
        return REFUSE_DIVISION_BY_ZERO, 0
    if signed and a == low and b == -1:
        return REFUSE_OVERFLOW, 0
    quotient = abs(a) // abs(b)
    if (a < 0) != (b < 0):
        quotient = -quotient
    return 0, (a - quotient * b) & mask


def _leaf_divide_uint64_t(args, memory):
    return _divide(64, False, args)


def _leaf_remainder_uint64_t(args, memory):
    return _remainder(64, False, args)


def _leaf_divide_int64_t(args, memory):
    return _divide(64, True, args)


def _leaf_remainder_int64_t(args, memory):
    return _remainder(64, True, args)


def _leaf_remainder_uint32_t(args, memory):
    return _remainder(32, False, args)


def _leaf_divide_int32_t(args, memory):
    return _divide(32, True, args)


def _leaf_remainder_int32_t(args, memory):
    return _remainder(32, True, args)


def _leaf_memory_copy(args, memory):
    dst, dst_len, src, src_len, count = args
    if count > dst_len or count > src_len:
        return 6, 0
    memory.write(dst, memory.read(src, count))
    return 0, 0


def _leaf_memory_fill(args, memory):
    dst, dst_len, byte, count = args
    if count > dst_len:
        return 6, 0
    memory.write(dst, bytes([byte & 0xFF]) * count)
    return 0, 0


LEAF_RUN = {
    LEAF["wrapping_add_uint64_t"]: _leaf_add,
    LEAF["wrapping_subtract_uint64_t"]: _leaf_subtract,
    LEAF["bit_and_uint64_t"]: _leaf_and,
    LEAF["bit_or_uint64_t"]: _leaf_or,
    LEAF["shift_left_uint64_t"]: _leaf_shift_left,
    LEAF["shift_right_uint64_t"]: _leaf_shift_right,
    LEAF["equal_uint64_t"]: _leaf_equal,
    LEAF["less_uint64_t"]: _leaf_less,
    LEAF["wrapping_multiply_uint64_t"]: _leaf_multiply_wrapping_uint64_t,
    LEAF["checked_multiply_uint64_t"]: _leaf_checked_multiply_uint64_t,
    LEAF["checked_multiply_int64_t"]: _leaf_checked_multiply_int64_t,
    LEAF["divide_uint64_t"]: _leaf_divide_uint64_t,
    LEAF["remainder_uint64_t"]: _leaf_remainder_uint64_t,
    LEAF["divide_int64_t"]: _leaf_divide_int64_t,
    LEAF["remainder_int64_t"]: _leaf_remainder_int64_t,
    LEAF["remainder_uint32_t"]: _leaf_remainder_uint32_t,
    LEAF["divide_int32_t"]: _leaf_divide_int32_t,
    LEAF["remainder_int32_t"]: _leaf_remainder_int32_t,
    LEAF["memory_copy"]: _leaf_memory_copy,
    LEAF["memory_fill"]: _leaf_memory_fill,
}


def run(
    program: bytes,
    args: bytes,
    memory: Memory,
    limit: int = 50_000_000,
    objects: dict[int, bytes] | None = None,
) -> tuple[int, int]:
    """(status, value) of a run of the program on arguments of whole words.

    ``objects`` are the programs a call card names, by the id of their words: a card's
    callee is a program of its own (graph.h's RXF_GRAPH_CALL), its parameters the
    call's arguments as words of the caller's frame, its own locals in the next
    variable-length cell of the run cell -- carved (base, size) at the end of the
    chain that is open, its size the frame bytes the callee declares. Depth costs
    words of the cell (at most RXF_GRAPH_CALL_DEPTH of them -- a call past the budget
    refuses RXF_REFUSE_CALL_DEPTH), never stack here either; a Return answers the
    call at any depth above 0 and ends the run at 0, and a Refuse gives the call its
    status (with no status slot to catch it, it ends the run).
    """
    objects = objects or {}
    code = struct.unpack(f"<{len(program) // 8}Q", program)
    assert code[0] == N["RXF_GRAPH_MAGIC"]
    frame_bytes, parameters, words, pc = code[1], code[2], code[3], code[4]
    assert len(args) == words * 8
    if (
        frame_bytes > N["RXF_GRAPH_CELL_BYTES"]
    ):  # the chain of one must fit the cell too
        return N["RXF_REFUSE_CELL_BUDGET"], 0
    frame = FRAME_AT
    memory.write(frame, bytes(frame_bytes))
    used = 0
    for i in range(parameters):
        slot, width = code[5 + 2 * i], code[6 + 2 * i]
        memory.write(frame + slot, args[used : used + width])
        used += (width + 7) // 8 * 8

    depth = 0
    frame_size = frame_bytes
    stack: list[tuple[tuple[int, ...], int, int, int, int, int, int]] = []
    # a saved frame: (the caller's words, the pc to go on at, its frame, its frame
    # bytes, the call's out slot, its width, its status slot)
    steps = 0
    while True:
        steps += 1
        if steps > limit:
            raise RuntimeError("the program does not end")
        op = code[pc]
        if op == OP_CALL:
            leaf, count, out, out_width, status = (
                code[pc + 1],
                code[pc + 2],
                code[pc + 3],
                code[pc + 4],
                code[pc + 5],
            )
            at = pc + 6
            if leaf == N["RXF_GRAPH_CALL"]:  # a call card: a program of its own
                kind, named, zero = code[at], code[at + 1], code[at + 2]
                if kind != N["RXF_OPERAND_OBJECT"] or zero != 0:
                    return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                at += 3
                if depth + 1 >= N["RXF_GRAPH_CALL_DEPTH"]:
                    return N["RXF_REFUSE_CALL_DEPTH"], 0
                made = objects.get(named)
                if made is None:
                    return N["RXF_REFUSE_UNBOUND"], 0
                callee = struct.unpack(f"<{len(made) // 8}Q", made)
                if len(callee) < 5 or callee[0] != N["RXF_GRAPH_MAGIC"]:
                    return N["RXF_REFUSE_TYPE_MISMATCH"], 0
                callee_frame, callee_parameters = callee[1], callee[2]
                entry = callee[4]
                if callee_frame & 7 or callee_parameters != count:
                    return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                # the next variable-length cell of the run cell: carved at the end of
                # the chain that is open -- a chain whose frames do not fit the cell is
                # refused the budget (docs/20 S6)
                dst = frame + frame_size
                spent = dst - FRAME_AT
                if (
                    spent > N["RXF_GRAPH_CELL_BYTES"]
                    or callee_frame > N["RXF_GRAPH_CELL_BYTES"] - spent
                ):
                    return N["RXF_REFUSE_CELL_BUDGET"], 0
                memory.write(dst, bytes(callee_frame))
                for i in range(callee_parameters):
                    slot, width = callee[5 + 2 * i], callee[6 + 2 * i]
                    if slot + width > callee_frame:
                        return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                    kind, a, b = code[at], code[at + 1], code[at + 2]
                    present = min(b, width)
                    if kind == OPD_LITERAL:
                        data = a.to_bytes(8, "little")[:present]
                    elif kind == N["RXF_OPERAND_OBJECT"]:
                        # a handle argument: the bound object's payload words (the
                        # run-entry ARGS shape of a handle is its three words)
                        payload = objects.get(a)
                        if payload is None:
                            return N["RXF_REFUSE_UNBOUND"], 0
                        data = payload[:present]
                    else:
                        data = memory.read(frame + a, present)
                    memory.write(dst + slot, data)
                    at += 3
                if out != NONE:
                    memory.write(frame + out, bytes(out_width))
                stack.append((code, at, frame, frame_size, out, out_width, status))
                depth += 1
                code, pc, frame, frame_size = callee, entry, dst, callee_frame
                continue
            args_ = []
            for _ in range(count):
                kind = code[at]
                if kind == OPD_LITERAL:
                    args_.append(code[at + 1])
                elif code[at + 2] > 8:
                    args_.append(frame + code[at + 1])
                else:
                    args_.append(
                        int.from_bytes(
                            memory.read(frame + code[at + 1], code[at + 2]), "little"
                        )
                    )
                at += 3
            if out != NONE:
                memory.write(frame + out, bytes(out_width))
            result, output = LEAF_RUN[leaf](args_, memory)
            if out != NONE:
                memory.write(frame + out, output.to_bytes(8, "little")[:out_width])
            if status != NONE:
                memory.write(frame + status, struct.pack("<I", result))
            pc = at
        elif op == OP_MOVE:
            slot, width = code[pc + 1], code[pc + 2]
            have = min(code[pc + 5], width)
            kind = code[pc + 3]
            if kind == OPD_LITERAL:
                data = code[pc + 4].to_bytes(8, "little")[:have]
            else:
                data = memory.read(frame + code[pc + 4], have)
            memory.write(frame + slot, data.ljust(width, b"\0"))
            pc += 6
        elif op == OP_GOTO:
            assert code[pc + 2] == 0, "an edge with copies"
            pc = code[pc + 1]
        elif op == OP_BRANCH:
            kind, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
            if kind == OPD_LITERAL:
                v = a
            else:
                v = int.from_bytes(memory.read(frame + a, min(b, 8)), "little")
            same = (v == 0) == (code[pc + 4] == MODE_ZERO)
            at = pc + 5 if same else pc + 7
            assert code[at + 1] == 0, "an edge with copies"
            pc = code[at]
        elif op == OP_SWITCH:
            kind, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
            if kind == OPD_LITERAL:
                v = a
            else:
                v = int.from_bytes(memory.read(frame + a, min(b, 8)), "little")
            n = code[pc + 4]
            chosen = n
            for i in range(n):
                if code[pc + 5 + i] == v:
                    chosen = i
                    break
            at = pc + 5 + n + 2 * chosen
            assert code[at + 1] == 0, "an edge with copies"
            pc = code[at]
        elif op == OP_RETURN:
            kind, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
            width = code[pc + 4]
            if depth == 0:
                if kind == OPD_LITERAL:
                    return 0, a
                return 0, int.from_bytes(memory.read(frame + a, min(b, 8)), "little")
            # a callee's out word to the caller-named destination word, and the caller
            # goes on after its call step (the answer of a call, graph.c's `answer`)
            if kind == OPD_LITERAL:
                if width > 8:
                    return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                data = a.to_bytes(8, "little")
            else:
                data = memory.read(frame + a, width if width > 8 else b)
            code, pc, frame, frame_size, out, out_width, status = stack.pop()
            depth -= 1
            if width > 8:
                if width > out_width:
                    return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                if out != NONE:
                    memory.write(frame + out, data[:width])
            else:
                if 8 > out_width:
                    return N["RXF_REFUSE_OUT_OF_RANGE"], 0
                if out != NONE:
                    memory.write(frame + out, data.ljust(8, b"\0")[:8])
            if status != NONE:
                memory.write(frame + status, struct.pack("<I", 0))
        elif op == OP_REFUSE:
            kind, a, b = code[pc + 1], code[pc + 2], code[pc + 3]
            if kind == OPD_LITERAL:
                value = a & 0xFFFFFFFF
            else:
                value = (
                    int.from_bytes(memory.read(frame + a, min(b, 8)), "little")
                    & 0xFFFFFFFF
                )
            if depth == 0:
                return value, 0
            # the call gets its status; with no status slot to catch it, the run ends
            # with the refusal (the uncaught shape)
            code, pc, frame, frame_size, out, out_width, status = stack.pop()
            depth -= 1
            if status != NONE:
                memory.write(frame + status, struct.pack("<I", value))
            else:
                return value, 0
        else:
            raise NotImplementedError(OP.get(op, op))
