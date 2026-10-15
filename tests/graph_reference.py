"""A second interpreter for the programs of ``native/graph.h``, in Python.

It is written from the format comment of the header, not from ``graph.c``, and
it calls the leaves as x86_64 machine code in an emulator, like the real one
does. A program that both interpreters run on the same memory must end with the
same status, output and memory: that is what ``tests/test_graph.py`` asks.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import graph_header

from .emulator import Argument, Blob, Emulator, Integer

MASK = (1 << 64) - 1
WORD = 8

# What each leaf takes after the context, as letters of tests/scenario.SPEC, and whether the
# last one is an output the leaf fills. The numeric leaves take no context.
OUTPUTS = frozenset(
    {
        "wrapping_add_uint64_t",
        "less_uint64_t",
        "bit_not_uint64_t",
        "hash_u64",
        "runtime_hash_map_lookup",
        "runtime_hash_set_lookup",
        "runtime_begin_private",
        "runtime_resolve",
        "runtime_allocate_prepare",
        "runtime_read",
        "runtime_search",
        "checked_add_uint32_t",
        "checked_subtract_uint32_t",
        "checked_multiply_uint32_t",
        "divide_uint32_t",
        "memory_compare",
        "wrapping_subtract_uint64_t",
        "bit_and_uint64_t",
        "bit_or_uint64_t",
        "shift_left_uint64_t",
        "shift_right_uint64_t",
        "equal_uint64_t",
        "wrapping_multiply_uint64_t",
        "checked_multiply_uint64_t",
        "checked_multiply_int64_t",
        "divide_uint64_t",
        "remainder_uint64_t",
        "divide_int64_t",
        "remainder_int64_t",
        "remainder_uint32_t",
        "divide_int32_t",
        "remainder_int32_t",
    }
)


class Done(Exception):
    """The run ended with this status."""

    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


def little(data: bytes) -> int:
    return int.from_bytes(data, "little")


class Reference:
    def __init__(
        self,
        emulator: Emulator,
        context: int,
        frame: int,
        addresses: Mapping[int, int],
        letters: Mapping[str, str],
    ) -> None:
        numbers = graph_header.numbers()
        self.n = numbers
        self.emulator = emulator
        self.context = context
        self.frame = frame
        self.addresses = addresses
        self.letters = letters
        self.names = {value: key for key, value in graph_header.leaves().items()}
        self.refuse_range = 6
        self.refuse_unbound = 13
        self.refuse_type = 14
        self.refuse_depth = numbers["RXF_REFUSE_CALL_DEPTH"]
        self.calls: list[tuple[str, int]] = []  # each leaf called, with its status
        self.words: Sequence[int] = ()
        self.frame_bytes = 0
        self.depth = 0  # the call frames of a run: each costs words of the cell, not stack
        self.frames: list[tuple[Sequence[int], int, int, int, int, int, int]] = []
        # a saved frame: (the caller's words, its frame bytes, the pc to go on at, its
        # frame address, the call's out slot, its width, its status slot)

    # ---- memory ---------------------------------------------------------------------------------

    def word_at(self, address: int) -> int:
        return little(self.emulator.read(address, WORD))

    def entry(self, identity: int) -> tuple[int, int] | None:
        """The payload and size of the object entry with this id; the table is sorted by id."""
        count, objects = (
            self.word_at(self.context + 32),
            self.word_at(self.context + 40),
        )
        low, high = 0, count
        while low < high:
            middle = (low + high) // 2
            if self.word_at(objects + middle * 80) < identity:
                low = middle + 1
            else:
                high = middle
        if low < count and self.word_at(objects + low * 80) == identity:
            at = objects + low * 80
            return self.word_at(at + 24), self.word_at(at + 32)
        return None

    # ---- the program ----------------------------------------------------------------------------

    def have(self, at: int, count: int) -> bool:
        return at <= len(self.words) and count <= len(self.words) - at

    def in_frame(self, offset: int, width: int) -> bool:
        return width <= self.frame_bytes and offset <= self.frame_bytes - width

    def refuse(self, status: int) -> None:
        raise Done(status)

    def need(self, condition: bool) -> None:
        if not condition:
            raise Done(self.refuse_range)

    def read(self, kind: int, a: int, b: int, size: int) -> bytes:
        """The first ``size`` bytes of an operand."""
        if kind == self.n["RXF_OPERAND_SLOT"]:
            self.need(self.in_frame(a, size))
            return self.emulator.read(self.frame + a, size)
        if kind == self.n["RXF_OPERAND_LITERAL"]:
            self.need(size <= 8)
            return a.to_bytes(8, "little")[:size]
        if kind == self.n["RXF_OPERAND_OBJECT"]:
            found = self.entry(a)
            if found is None:
                raise Done(self.refuse_unbound)
            self.need(size <= found[1])
            return self.emulator.read(found[0], size)
        raise Done(self.refuse_range)

    def operand(self, at: int) -> tuple[int, int, int]:
        self.need(self.have(at, 3))
        return self.words[at], self.words[at + 1], self.words[at + 2]

    def value(self, at: int) -> int:
        """A slot or literal as an integer, an object as the address of its payload."""
        kind, a, b = self.operand(at)
        if kind == self.n["RXF_OPERAND_OBJECT"]:
            found = self.entry(a)
            if found is None:
                raise Done(self.refuse_unbound)
            return found[0]
        if kind == self.n["RXF_OPERAND_LITERAL"]:
            self.need(b <= 8)
            return a
        data = self.read(kind, a, b, min(b, 8))
        return little(data)

    def pointer(self, at: int) -> int:
        kind, a, b = self.operand(at)
        if kind == self.n["RXF_OPERAND_SLOT"] and b > 8:
            self.read(kind, a, b, b)
            return self.frame + a
        return self.value(at)

    def put(self, slot: int, data: bytes) -> None:
        self.need(self.in_frame(slot, len(data)))
        self.emulator.write(self.frame + slot, data)

    def move(self, slot: int, width: int, at: int) -> None:
        kind, a, b = self.operand(at)
        present = min(b, width)
        self.need(self.in_frame(slot, width))
        data = self.read(kind, a, b, present)
        self.put(slot, data.ljust(width, b"\0"))

    def take(self, at: int) -> int:
        self.need(self.have(at, 2))
        count = self.words[at + 1]
        limit = self.n["RXF_GRAPH_COPY"]
        self.need(count <= limit and self.have(at + 2, count * 5))
        staged = b""
        for index in range(count):
            copy = at + 2 + index * 5
            kind, a, b = self.operand(copy)
            width = self.words[copy + 4]
            data = self.read(kind, a, b, min(b, width))
            self.need(width <= limit - len(staged))
            staged += data.ljust(width, b"\0")
        used = 0
        for index in range(count):
            copy = at + 2 + index * 5
            slot, width = self.words[copy + 3], self.words[copy + 4]
            self.put(slot, staged[used : used + width])
            used += width
        return self.words[at]

    def edge_words(self, at: int) -> int:
        return 2 + self.words[at + 1] * 5

    # ---- the leaves -----------------------------------------------------------------------------

    def call(self, at: int) -> int:
        w = self.words
        self.need(self.have(at, 6))
        leaf, count, out_slot, out_width = w[at + 1], w[at + 2], w[at + 3], w[at + 4]
        if leaf == self.n["RXF_GRAPH_CALL"]:
            return self.call_program(at)
        name = self.names.get(leaf)
        if name is None:
            raise Done(self.refuse_range)
        letters = self.letters[name]
        has_context = name.startswith("runtime_")
        has_out = name in OUTPUTS
        parameters = letters[1:] if has_context else letters
        self.need(len(parameters) - (1 if has_out else 0) == count)
        self.need(self.have(at, 6 + count * 3))
        if leaf not in self.addresses:
            raise Done(self.refuse_unbound)
        arguments: list[Argument] = [Integer(self.context)] if has_context else []
        for index in range(count):
            where = at + 6 + index * 3
            if parameters[index] == "H":
                kind, a, b = self.operand(where)
                arguments.append(Blob(self.read(kind, a, b, 24)))
            else:
                arguments.append(Integer(self.pointer(where)))
        out = 0
        if has_out:
            self.need(
                out_slot != self.n["RXF_GRAPH_NONE"]
                and self.in_frame(out_slot, out_width)
            )
            out = self.frame + out_slot
            self.emulator.write(out, bytes(out_width))
            arguments.append(Integer(out))
        else:
            self.need(out_slot == self.n["RXF_GRAPH_NONE"])
        status = self.emulator.call(self.addresses[leaf], arguments)
        self.calls.append((name, status))
        if w[at + 5] != self.n["RXF_GRAPH_NONE"]:
            self.put(w[at + 5], status.to_bytes(4, "little"))
        return at + 6 + count * 3

    def call_program(self, at: int) -> int:
        """A call card: word 1 is RXF_GRAPH_CALL, then the callee's program operand
        (an Object handle of the callee's program words) and the arguments. The callee
        gets the next frame region of the run cell (one RXF_GRAPH_FRAME per level, at
        most RXF_GRAPH_CALL_DEPTH of them), its parameters bound from the caller's
        words (a scalar one word, a handle its three), and the run goes on at its entry.
        The interpreter iterates over the frames it saves: depth costs words of the
        cell, never native stack (graph.c's ``call_program``)."""
        w, n = self.words, self.n
        self.need(self.have(at, 6 + 3))
        count, out_slot, out_width = w[at + 2], w[at + 3], w[at + 4]
        kind, named, zero = self.operand(at + 6)
        if kind != n["RXF_OPERAND_OBJECT"] or zero != 0:
            raise Done(self.refuse_range)
        self.need(self.have(at, 9 + count * 3))
        if self.depth + 1 >= n["RXF_GRAPH_CALL_DEPTH"]:  # the entry's frame is one of the budget
            raise Done(self.refuse_depth)
        found = self.entry(named)
        if found is None:
            raise Done(self.refuse_unbound)
        payload, size = found[0], found[1]
        if payload & 7:
            raise Done(self.refuse_range)
        words = [
            little(self.emulator.read(payload + 8 * index, 8))
            for index in range(size // 8)
        ]
        if len(words) < 5 or words[0] != n["RXF_GRAPH_MAGIC"]:
            raise Done(self.refuse_type)
        callee_frame, parameters = words[1], words[2]
        entry = words[4]
        if (
            parameters != count
            or parameters > len(words)
            or 5 + parameters * 2 > len(words)
            or callee_frame > n["RXF_GRAPH_FRAME"]
            or callee_frame & 7
        ):
            raise Done(self.refuse_range)
        dst = self.frame + n["RXF_GRAPH_FRAME"]  # the next region of the run cell
        self.emulator.write(dst, bytes(callee_frame))
        for index in range(parameters):
            slot, width = words[5 + 2 * index], words[6 + 2 * index]
            if width > callee_frame or slot > callee_frame - width:
                raise Done(self.refuse_range)
            where = at + 9 + index * 3
            operand_kind, a, b = self.operand(where)
            present = min(b, width)
            data = self.read(operand_kind, a, b, present)
            self.emulator.write(dst + slot, data)  # the rest of the slot was zeroed
        if out_slot != n["RXF_GRAPH_NONE"]:
            self.need(self.in_frame(out_slot, out_width))
            self.emulator.write(self.frame + out_slot, bytes(out_width))
        self.frames.append(
            (self.words, self.frame_bytes, at + 9 + count * 3, self.frame,
             out_slot, out_width, w[at + 5])
        )  # fmt: skip
        self.words, self.frame_bytes, self.frame = words, callee_frame, dst
        self.depth += 1
        return entry

    def answer(self, status: int, data: bytes | None, width: int) -> int:
        """The end of a callee: its out word to the caller-named destination word, its
        status to the call's status slot, and the caller goes on after its call step. A
        refusal with no status slot to catch it ends the run with it (only called with
        a call above: depth 0 answers the run)."""
        self.words, self.frame_bytes, pc, self.frame, out_slot, out_width, status_slot = (
            self.frames.pop()
        )
        self.depth -= 1
        if data is not None and out_slot != self.n["RXF_GRAPH_NONE"]:
            if width > out_width:
                raise Done(self.refuse_range)
            self.need(self.in_frame(out_slot, out_width))
            self.emulator.write(self.frame + out_slot, data[:width])
        if status_slot != self.n["RXF_GRAPH_NONE"]:
            self.need(self.in_frame(status_slot, 4))
            self.emulator.write(self.frame + status_slot, status.to_bytes(4, "little"))
        elif status:
            raise Done(status)
        return pc

    # ---- a run ----------------------------------------------------------------------------------

    def run(self, program: bytes, args: bytes, argc: int) -> tuple[int, bytes]:
        """The status, and the output the program wrote (at most 256 bytes)."""
        self.words = [little(program[i : i + 8]) for i in range(0, len(program) - 7, 8)]
        out = bytearray(256)
        try:
            self.execute(args, argc, out)
        except Done as done:
            return done.status, bytes(out)
        raise AssertionError("a run ends with a status")

    def execute(self, args: bytes, argc: int, out: bytearray) -> None:
        w, n = self.words, self.n
        if not self.have(0, 5) or w[0] != n["RXF_GRAPH_MAGIC"]:
            raise Done(self.refuse_type)
        self.frame_bytes = w[1]
        parameters, words, pc = w[2], w[3], w[4]
        self.need(
            self.frame_bytes <= n["RXF_GRAPH_FRAME"]
            and self.frame_bytes % 8 == 0
            and words == argc
            and parameters <= len(w)
            and self.have(5, parameters * 2)
        )
        self.emulator.write(self.frame, bytes(self.frame_bytes))
        used = 0
        for index in range(parameters):
            slot, width = w[5 + 2 * index], w[6 + 2 * index]
            self.need(self.in_frame(slot, width) and width <= words * 8 - used)
            self.emulator.write(self.frame + slot, args[used : used + width])
            used += (width + 7) & ~7
        while True:
            pc = self.step(pc, out)

    def step(self, pc: int, out: bytearray) -> int:
        w, n = self.words, self.n
        self.need(self.have(pc, 1))
        op = w[pc]
        if op == n["RXF_OP_CALL"]:
            return self.call(pc)
        if op == n["RXF_OP_MOVE"]:
            self.need(self.have(pc, 6))
            self.move(w[pc + 1], w[pc + 2], pc + 3)
            return pc + 6
        if op == n["RXF_OP_MAP"]:
            self.need(self.have(pc, 8))
            pairs = w[pc + 7]
            self.need(pairs <= len(w) and self.have(pc, 8 + pairs * 2))
            dst, width, offset = w[pc + 1], w[pc + 2], w[pc + 6]
            self.need(self.in_frame(dst, width) and self.in_frame(dst + offset, 4))
            self.move(dst, width, pc + 3)
            found = little(self.emulator.read(self.frame + dst + offset, 4))
            for index in range(pairs):
                if w[pc + 8 + 2 * index] == found:
                    mapped = w[pc + 9 + 2 * index]
                    self.emulator.write(
                        self.frame + dst + offset,
                        (mapped & 0xFFFFFFFF).to_bytes(4, "little"),
                    )
                    break
            return pc + 8 + pairs * 2
        if op == n["RXF_OP_FIELD"]:
            self.need(self.have(pc, 8))
            dst, width, offset, size = w[pc + 1], w[pc + 2], w[pc + 6], w[pc + 7]
            self.need(size <= width and self.in_frame(dst, width))
            self.emulator.write(self.frame + dst, bytes(width))
            self.copy_field(dst, size, pc + 3, offset)
            return pc + 8
        if op == n["RXF_OP_CONSTRUCT"]:
            self.need(self.have(pc, 10))
            dst, width, tag_offset, tag = w[pc + 1], w[pc + 2], w[pc + 3], w[pc + 4]
            payload_offset, payload_size = w[pc + 8], w[pc + 9]
            self.need(
                self.in_frame(dst, width)
                and self.in_frame(dst + tag_offset, 4)
                and payload_size <= width
                and payload_offset <= width - payload_size
            )
            self.emulator.write(self.frame + dst, bytes(width))
            self.emulator.write(
                self.frame + dst + tag_offset, (tag & 0xFFFFFFFF).to_bytes(4, "little")
            )
            _, _, b = self.operand(pc + 5)
            self.copy_field(dst + payload_offset, min(payload_size, b), pc + 5, 0)
            return pc + 10
        if op == n["RXF_OP_LESS"]:
            self.need(self.have(pc, 8) and self.in_frame(w[pc + 1], 1))
            left, right = self.value(pc + 2), self.value(pc + 5)
            self.emulator.write(self.frame + w[pc + 1], bytes([int(left < right)]))
            return pc + 8
        if op == n["RXF_OP_INCREMENT"]:
            self.need(self.have(pc, 6) and self.in_frame(w[pc + 1], w[pc + 2]))
            value = self.value(pc + 3)
            width = w[pc + 2]
            data = ((value + 1) & MASK).to_bytes(8, "little")[: min(width, 8)]
            self.emulator.write(self.frame + w[pc + 1], data)
            return pc + 6
        if op == n["RXF_OP_GOTO"]:
            return self.take(pc + 1)
        if op == n["RXF_OP_BRANCH"]:
            self.need(self.have(pc, 5 + 2))
            value = self.value(pc + 1)
            first = pc + 5
            self.need(self.have(first, self.edge_words(first) + 2))
            zero = w[pc + 4] == n["RXF_MODE_ZERO"]
            return self.take(
                first if (value == 0) == zero else first + self.edge_words(first)
            )
        if op == n["RXF_OP_SWITCH"]:
            self.need(
                self.have(pc, 5)
                and w[pc + 4] <= len(w)
                and self.have(pc, 5 + w[pc + 4])
            )
            value = self.value(pc + 1)
            count = w[pc + 4]
            chosen = next((i for i in range(count) if w[pc + 5 + i] == value), count)
            at = pc + 5 + count
            for _ in range(chosen):
                self.need(self.have(at, 2))
                at += self.edge_words(at)
            return self.take(at)
        if op == n["RXF_OP_RETURN"]:
            self.need(self.have(pc, 5))
            width = w[pc + 4]
            if width > 8:
                kind, a, b = self.operand(pc + 1)
                data = self.read(kind, a, b, width)
            else:
                data, width = self.value(pc + 1).to_bytes(8, "little"), 8
            if self.depth == 0:
                out[:width] = data
                raise Done(0)
            # a callee's out words to the caller-named destination words, and the
            # caller goes on after its call step
            return self.answer(0, data, width)
        if op == n["RXF_OP_REFUSE"]:
            self.need(self.have(pc, 4))
            status = self.value(pc + 1) & 0xFFFFFFFF
            if self.depth == 0:
                raise Done(status)
            # the call gets its status; with no status slot to catch it, the run ends
            # with the refusal (the uncaught shape)
            return self.answer(status, None, 0)
        raise Done(self.refuse_range)

    def copy_field(self, slot: int, size: int, at: int, offset: int) -> None:
        kind, a, b = self.operand(at)
        data = self.read(kind, a, b, offset + size)[offset:]
        self.need(self.in_frame(slot, size))
        self.emulator.write(self.frame + slot, data)
