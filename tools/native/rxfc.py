"""The compiler of the small language `.rxfs`: source text to a program of the interpreter.

A program of ``native/graph.h`` is a list of 64-bit words that only a tool can write. This is the
tool for programs that are written by a person, in a language of statements over 64-bit
variables, so that a program as long as a compiler can be kept as source and not as words. It
produces exactly the format the composer produces (the same header, the same instructions), so
the interpreter, the native compiler (``native/compile.c``) and the compiler that is itself a
program run it like any other.

    include "file.rxfs";                         the tokens of another file, once
    const NAME = expression;                    a number, known when the program is made
                                                (every number of native/graph.h is one already)
    macro name(a, b) { ... }                    statements that are put in where it is used
    fn name(a, b) = expression;                 an expression that is put in where it is used
    program name(p, q) { ... }                  the one program; its parameters are 64-bit
    proc name { ... }                           statements that `call name;` runs, after the program

    var a, b;                                   64-bit slots
    x = expression;
    store8|16|32|64(address, value);            bytes to memory
    put(pointer, value, n);                     n bytes at *pointer, the pointer moves on by n
    copy(destination, source, n);   fill(destination, byte, n);
    if cond { } else { }     while cond { }     break;  continue;
    switch value { case 1, 2: { } default: { } }
    return value;  refuse status;
    call name;  ret;                            a proc has no parameters and no result: it works on
                                                the variables of the program. The way back is a
                                                stack of tags in memory that starts at the variable
                                                `sp`, which the program declares and sets itself

An expression is numbers, constants, variables, ``load8|16|32|64(address)`` and the operators
``+ - & | << >> == != < <= > >=`` and ``! ~ -`` on unsigned 64-bit values, which are leaves of the
function table (``wrapping_add_uint64_t`` ...). ``x * 8`` with a constant power of two is a shift.
Nothing is hidden: every operator is a call and every ``if`` a branch, so what the program does
is what its words say.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

import graph_header

N = graph_header.numbers()
LEAF = graph_header.leaves()
NONE = N["RXF_GRAPH_NONE"]
MAGIC = N["RXF_GRAPH_MAGIC"]
FRAME = N["RXF_GRAPH_FRAME"]
SLOT, LITERAL = N["RXF_OPERAND_SLOT"], N["RXF_OPERAND_LITERAL"]
ZERO, NONZERO = N["RXF_MODE_ZERO"], N["RXF_MODE_NONZERO"]
OP = {
    name: N[f"RXF_OP_{name}"]
    for name in ("CALL", "MOVE", "GOTO", "BRANCH", "SWITCH", "RETURN", "REFUSE")
}
MASK = (1 << 64) - 1
BIG = 1 << 40  # a length that no pointer here is checked against

BINARY = {
    "+": "wrapping_add_uint64_t",
    "-": "wrapping_subtract_uint64_t",
    "&": "bit_and_uint64_t",
    "|": "bit_or_uint64_t",
    "<<": "shift_left_uint64_t",
    ">>": "shift_right_uint64_t",
    "==": "equal_uint64_t",
    "<": "less_uint64_t",
}
PRECEDENCE = [
    ["|"],
    ["&"],
    ["==", "!="],
    ["<", "<=", ">", ">="],
    ["<<", ">>"],
    ["+", "-"],
    ["*"],
]
LOADS = {"load8": 1, "load16": 2, "load32": 4, "load64": 8}
STORES = {"store8": 1, "store16": 2, "store32": 4, "store64": 8}
TOKEN = re.compile(
    r"\s+|#[^\n]*|(?P<number>0[xX][0-9a-fA-F_]+|\d[\d_]*)|(?P<name>[A-Za-z_]\w*)"
    r'|(?P<string>"[^"\n]*")'
    r"|(?P<op><<|>>|==|!=|<=|>=|[-+&|<>=!~*(){},;:])"
)


class Error(Exception):
    """The source is not a program."""


@dataclass(frozen=True)
class Token:
    kind: str  # number, name, op, string
    text: str
    line: int
    file: str = ""  # the file of a token that an `include` brought in

    def __str__(self) -> str:
        return self.text


def tokens(text: str, file: str = "") -> list[Token]:
    out: list[Token] = []
    at, line = 0, 1
    while at < len(text):
        found = TOKEN.match(text, at)
        if not found:
            raise Error(f"line {line}: cannot read {text[at : at + 12]!r}")
        if found.lastgroup:
            out.append(Token(found.lastgroup, found.group(), line, file))
        line += found.group().count("\n")
        at = found.end()
    return out


# ---- the program being made -------------------------------------------------------------------


@dataclass
class Label:
    name: str


@dataclass
class Program:
    """Words, with the word index of every label, and the slots of the frame."""

    words: list[int | Label] = field(default_factory=list)
    starts: list[int] = field(default_factory=list)  # where each instruction begins
    labels: dict[str, int] = field(default_factory=dict)
    frame: int = 0
    params: list[tuple[int, int]] = field(default_factory=list)

    def emit(self, *words: int | Label) -> None:
        """One whole instruction."""
        self.starts.append(len(self.words))
        self.words.extend(words)


class Maker:
    """Parses the tokens of one program and writes its words."""

    def __init__(self, source: str, path: str = "<source>") -> None:
        self.path = path
        self.base = Path(path).parent if path != "<source>" else Path.cwd()
        self.included: set[Path] = set()
        self.tokens = tokens(source)
        self.at = 0
        self.consts: dict[str, int] = dict(
            N
        )  # every number of graph.h and the refusals
        self.macros: dict[str, tuple[list[str], list[Token]]] = {}
        self.fns: dict[str, tuple[list[str], list[Token]]] = {}
        self.variables: dict[str, int] = {}
        self.program = Program()
        self.next_label = 0
        self.loops: list[tuple[str, str]] = []
        self.temps: list[int] = []  # free temporary slots
        self.live_temps: list[int] = []
        self.wide: int | None = None
        self.program_name = ""
        self.slot_end = 0
        self.leaf_used: set[str] = set()
        self.procs: dict[str, list[tuple[int, str]]] = {}  # the way back of each proc
        self.defined: set[str] = set()
        self.proc_name = ""
        self.back: int | None = None  # the slot the way back is read into

    # -- tokens
    def fail(self, message: str) -> Error:
        token = self.tokens[min(self.at, len(self.tokens) - 1)] if self.tokens else None
        where = (token.file if token else "") or self.path
        return Error(f"{where}:{token.line if token else 0}: {message}")

    def peek(self, ahead: int = 0) -> Token | None:
        i = self.at + ahead
        return self.tokens[i] if i < len(self.tokens) else None

    def next(self) -> Token:
        token = self.peek()
        if token is None:
            raise self.fail("the source ends too early")
        self.at += 1
        return token

    def accept(self, text: str) -> bool:
        token = self.peek()
        if token and token.text == text and token.kind != "number":
            self.at += 1
            return True
        return False

    def expect(self, text: str) -> None:
        if not self.accept(text):
            got = self.peek()
            raise self.fail(
                f"expected {text!r}, found {got.text if got else 'the end'!r}"
            )

    def name(self) -> str:
        token = self.next()
        if token.kind != "name":
            raise self.fail(f"expected a name, found {token.text!r}")
        return token.text

    # -- slots
    def slot(self, width: int) -> int:
        align = 8
        self.slot_end = (self.slot_end + align - 1) // align * align
        at = self.slot_end
        self.slot_end += (width + 7) // 8 * 8
        if self.slot_end > FRAME:
            raise self.fail(f"the frame is full ({FRAME} bytes)")
        return at

    def temp(self) -> int:
        slot = self.temps.pop() if self.temps else self.slot(8)
        self.live_temps.append(slot)
        return slot

    def free(self, value: tuple[str, int]) -> None:
        """A temporary that has been read is free for the next result (the call that reads its
        operands before it writes the output makes this safe)."""
        if value[0] == "slot" and value[1] in self.live_temps:
            self.live_temps.remove(value[1])
            self.temps.append(value[1])

    def release(self) -> None:
        self.temps.extend(self.live_temps)
        self.live_temps = []

    def wide_slot(self) -> int:
        if self.wide is None:
            self.wide = self.slot(16)
        return self.wide

    # -- labels
    def new_label(self) -> str:
        self.next_label += 1
        return f"L{self.next_label}"

    def place(self, label: str) -> None:
        self.program.labels[label] = len(self.program.words)

    # -- items
    def parse(self) -> Program:
        while self.peek():
            word = self.name()
            if word == "include":
                self.include()
            elif word == "const":
                name = self.name()
                self.expect("=")
                self.consts[name] = self.constant()
                self.expect(";")
            elif word == "macro":
                self.macro()
            elif word == "fn":
                self.fn()
            elif word == "program":
                if self.program_name:
                    raise self.fail("there is one program")
                self.main()
            elif word == "proc":
                if not self.program_name:
                    raise self.fail("the program comes before its procs")
                self.proc()
            else:
                raise self.fail(f"expected const, macro or program, found {word!r}")
        if not self.program_name:
            raise self.fail("there is no program")
        self.ways_back()
        self.program.frame = (self.slot_end + 7) // 8 * 8
        return self.program

    def include(self) -> None:
        token = self.next()
        if token.kind != "string":
            raise self.fail("include takes a file name in quotes")
        self.expect(";")
        path = self.base / token.text[1:-1]
        if path in self.included:
            return
        self.included.add(path)
        self.tokens[self.at : self.at] = tokens(path.read_text(), str(path))

    def macro(self) -> None:
        name = self.name()
        self.expect("(")
        params: list[str] = []
        while not self.accept(")"):
            params.append(self.name())
            self.accept(",")
        self.expect("{")
        body = self.take_block()
        self.macros[name] = (params, body)

    def fn(self) -> None:
        name = self.name()
        self.expect("(")
        params: list[str] = []
        while not self.accept(")"):
            params.append(self.name())
            self.accept(",")
        self.expect("=")
        body = []
        while not self.accept(";"):
            body.append(self.next())
        self.fns[name] = (params, body)

    def take_block(self) -> list[Token]:
        """The tokens up to the closing brace of a block whose opening brace is read."""
        depth, body = 1, []
        while depth:
            token = self.next()
            depth += token.text == "{" and token.kind == "op"
            depth -= token.text == "}" and token.kind == "op"
            if depth:
                body.append(token)
        return body

    def main(self) -> None:
        self.program_name = self.name()
        self.expect("(")
        params: list[str] = []
        while not self.accept(")"):
            params.append(self.name())
            self.accept(",")
        for param in params:
            self.variables[param] = self.slot(8)
            self.program.params.append((self.variables[param], 8))
        self.expect("{")
        self.block()
        self.program.emit(OP["RETURN"], LITERAL, 0, 8, 8)

    def proc(self) -> None:
        name = self.name()
        if name in self.defined:
            raise self.fail(f"proc {name!r} is defined twice")
        self.defined.add(name)
        self.procs.setdefault(name, [])
        self.expect("{")
        self.place(f"proc:{name}")
        self.proc_name = name
        self.block()
        self.proc_name = ""
        self.place(f"end:{name}")
        sp = self.stack_pointer()
        self.call("wrapping_subtract_uint64_t", [("slot", sp), ("lit", 8)], sp)
        value = self.load(8, ("slot", sp))
        self.move(self.back_slot(), value)
        self.release()
        self.jump(f"back:{name}")

    def stack_pointer(self) -> int:
        if "sp" not in self.variables:
            raise self.fail(
                "`call` needs the variable `sp`, the stack of the ways back"
            )
        return self.variables["sp"]

    def back_slot(self) -> int:
        if self.back is None:
            self.back = self.slot(8)
        return self.back

    def call_statement(self) -> None:
        name = self.name()
        self.expect(";")
        sp = self.stack_pointer()
        sites = self.procs.setdefault(name, [])
        tag, label = len(sites) + 1, self.new_label()
        sites.append((tag, label))
        self.store(8, ("slot", sp), ("lit", tag))
        self.call("wrapping_add_uint64_t", [("slot", sp), ("lit", 8)], sp)
        self.jump(f"proc:{name}")
        self.place(label)

    def ways_back(self) -> None:
        """After everything: what each proc does with the tag it finds."""
        for name, sites in self.procs.items():
            if name not in self.defined:
                raise self.fail(f"proc {name!r} is called and not defined")
        if not self.procs:
            return
        for name, sites in self.procs.items():
            self.place(f"back:{name}")
            words: list[int | Label] = [
                OP["SWITCH"],
                SLOT,
                self.back_slot(),
                8,
                len(sites),
            ]
            words += [tag for tag, _ in sites]
            for _, label in sites:
                words += [Label(label), 0]
            words += [Label("trap"), 0]
            self.program.emit(*words)
        self.place("trap")
        self.program.emit(OP["REFUSE"], LITERAL, 255, 8)

    def block(self) -> None:
        """Statements up to the closing brace."""
        while not self.accept("}"):
            if self.peek() is None:
                raise self.fail("a block is not closed")
            self.statement()

    # -- expressions
    def constant(self) -> int:
        value = self.expression(fold=True)
        if value[0] != "lit":
            raise self.fail("this must be a number known when the program is made")
        return int(value[1])

    def expression(self, fold: bool = False, level: int = 0) -> tuple[str, int]:
        """('lit', value) or ('slot', offset): the operand that holds the value."""
        if level == len(PRECEDENCE):
            return self.unary(fold)
        left = self.expression(fold, level + 1)
        while True:
            token = self.peek()
            if not (token and token.kind == "op" and token.text in PRECEDENCE[level]):
                return left
            self.at += 1
            right = self.expression(fold, level + 1)
            left = self.binary(token.text, left, right)

    def unary(self, fold: bool) -> tuple[str, int]:
        token = self.next()
        if token.kind == "number":
            return ("lit", int(token.text.replace("_", ""), 0))
        if token.text == "(":
            value = self.expression(fold)
            self.expect(")")
            return value
        if token.text == "!":
            return self.binary("==", self.unary(fold), ("lit", 0))
        if token.text == "-":
            return self.binary("-", ("lit", 0), self.unary(fold))
        if token.text == "~":
            return self.binary("-", ("lit", MASK), self.unary(fold))
        if token.kind != "name":
            raise self.fail(f"cannot start an expression with {token.text!r}")
        name = token.text
        if name in LOADS:
            self.expect("(")
            address = self.expression(fold)
            self.expect(")")
            return self.load(LOADS[name], address)
        if name in self.consts:
            return ("lit", self.consts[name])
        if name in self.variables:
            return ("slot", self.variables[name])
        if name in self.fns:
            return self.apply(name, fold)
        raise self.fail(f"{name!r} is not defined")

    def apply(self, name: str, fold: bool) -> tuple[str, int]:
        params, body = self.fns[name]
        self.expect("(")
        replaced = self.substitute(name, params, body, self.arguments())
        saved = (self.tokens, self.at)
        self.tokens, self.at = replaced, 0
        try:
            value = self.expression(fold)
            if self.at != len(self.tokens):
                raise self.fail(f"{name} is more than one expression")
        finally:
            self.tokens, self.at = saved
        return value

    def fold(self, op: str, a: int, b: int) -> int:
        match op:
            case "+":
                return (a + b) & MASK
            case "-":
                return (a - b) & MASK
            case "&":
                return a & b
            case "|":
                return a | b
            case "<<":
                return (a << b) & MASK
            case ">>":
                return a >> b
            case "==":
                return int(a == b)
            case "!=":
                return int(a != b)
            case "<":
                return int(a < b)
            case "<=":
                return int(a <= b)
            case ">":
                return int(a > b)
            case ">=":
                return int(a >= b)
            case "*":
                return (a * b) & MASK
        raise self.fail(f"no operator {op!r}")

    def binary(
        self, op: str, a: tuple[str, int], b: tuple[str, int], dest: int | None = None
    ) -> tuple[str, int]:
        if a[0] == "lit" and b[0] == "lit":
            return ("lit", self.fold(op, a[1], b[1]))
        if op == "*":
            for x, y in ((a, b), (b, a)):
                if y[0] == "lit" and y[1] and y[1] & (y[1] - 1) == 0:
                    return self.binary("<<", x, ("lit", y[1].bit_length() - 1), dest)
            raise self.fail("`*` takes a constant power of two")
        if op == "!=":
            return self.binary("==", self.binary("==", a, b), ("lit", 0), dest)
        if op == ">":
            return self.binary("<", b, a, dest)
        if op == "<=":
            return self.binary("==", self.binary("<", b, a), ("lit", 0), dest)
        if op == ">=":
            return self.binary("==", self.binary("<", a, b), ("lit", 0), dest)
        self.free(a)
        self.free(b)
        out = dest if dest is not None else self.temp()
        self.call(BINARY[op], [a, b], out)
        return ("slot", out)

    def load(self, size: int, address: tuple[str, int]) -> tuple[str, int]:
        wide = self.wide_slot()
        if size < 8:
            self.move(wide, ("lit", 0))
        self.call(
            "memory_copy",
            [("wide", wide), ("lit", BIG), address, ("lit", BIG), ("lit", size)],
            None,
        )
        self.free(address)
        out = self.temp()
        self.move(out, ("slot", wide))
        return ("slot", out)

    # -- instructions
    def operand(self, value: tuple[str, int], pointer: bool = False) -> list[int]:
        kind, x = value
        if kind == "lit":
            return [LITERAL, x & MASK, 8]
        if kind == "wide":
            return [SLOT, x, 16]
        return [SLOT, x, 8]

    def move(self, slot: int, value: tuple[str, int]) -> None:
        self.program.emit(OP["MOVE"], slot, 8, *self.operand(value))

    def call(self, leaf: str, operands: list[tuple[str, int]], out: int | None) -> None:
        if leaf not in LEAF:
            raise self.fail(f"no leaf {leaf!r}")
        self.leaf_used.add(leaf)
        words: list[int] = [OP["CALL"], LEAF[leaf], len(operands)]
        words += [NONE if out is None else out, 8, NONE]
        for value in operands:
            words += self.operand(value)
        self.program.emit(*words)

    # -- statements
    def statement(self) -> None:
        token = self.next()
        word = token.text
        if token.kind != "name":
            raise self.fail(f"a statement cannot start with {word!r}")
        if word == "var":
            while True:
                name = self.name()
                if name in self.variables:
                    raise self.fail(f"{name!r} is defined twice")
                self.variables[name] = self.slot(8)
                if not self.accept(","):
                    break
            self.expect(";")
        elif word == "if":
            self.if_statement()
        elif word == "while":
            self.while_statement()
        elif word == "switch":
            self.switch_statement()
        elif word in ("break", "continue"):
            if not self.loops:
                raise self.fail(f"{word} outside of a loop")
            top, end = self.loops[-1]
            self.jump(top if word == "continue" else end)
            self.expect(";")
        elif word == "call":
            self.call_statement()
        elif word == "ret":
            if not self.proc_name:
                raise self.fail("`ret` is for a proc")
            self.expect(";")
            self.jump(f"end:{self.proc_name}")
        elif word in ("return", "refuse"):
            value = self.expression()
            self.expect(";")
            if word == "return":
                self.program.emit(OP["RETURN"], *self.operand(value), 8)
            else:
                self.program.emit(OP["REFUSE"], *self.operand(value))
        elif word in STORES:
            self.expect("(")
            address = self.expression()
            self.expect(",")
            value = self.expression()
            self.expect(")")
            self.expect(";")
            self.store(STORES[word], address, value)
        elif word == "put":
            self.expect("(")
            pointer = self.name()
            if pointer not in self.variables:
                raise self.fail(f"{pointer!r} is not a variable")
            self.expect(",")
            value = self.expression()
            self.expect(",")
            size = self.constant()
            self.expect(")")
            self.expect(";")
            self.store(size, ("slot", self.variables[pointer]), value)
            self.call(
                "wrapping_add_uint64_t",
                [("slot", self.variables[pointer]), ("lit", size)],
                self.variables[pointer],
            )
        elif word == "copy":
            self.expect("(")
            dst = self.expression()
            self.expect(",")
            src = self.expression()
            self.expect(",")
            count = self.expression()
            self.expect(")")
            self.expect(";")
            self.call("memory_copy", [dst, count, src, count, count], None)
        elif word == "fill":
            self.expect("(")
            dst = self.expression()
            self.expect(",")
            byte = self.constant()
            self.expect(",")
            count = self.expression()
            self.expect(")")
            self.expect(";")
            self.call("memory_fill", [dst, count, ("lit", byte), count], None)
        elif word in self.macros:
            self.expand(word)
        elif word in self.variables:
            self.expect("=")
            self.assign_statement(self.variables[word])
        else:
            raise self.fail(f"{word!r} is not a statement")
        self.release()

    def assign_statement(self, target: int) -> None:
        """`target = expression ;`: when the value is the output of the last call, that call
        writes into the target and nothing is moved."""
        before = len(self.program.starts)
        value = self.expression()
        self.expect(";")
        words, starts = self.program.words, self.program.starts
        if (
            value[0] == "slot"
            and value[1] in self.live_temps
            and len(starts) > before
            and words[starts[-1]] == OP["CALL"]
            and words[starts[-1] + 3] == value[1]
        ):
            words[starts[-1] + 3] = target
        else:
            self.move(target, value)

    def store(
        self, size: int, address: tuple[str, int], value: tuple[str, int]
    ) -> None:
        wide = self.wide_slot()
        self.move(wide, value)
        self.call(
            "memory_copy",
            [address, ("lit", BIG), ("wide", wide), ("lit", BIG), ("lit", size)],
            None,
        )

    def jump(self, label: str) -> None:
        self.program.emit(OP["GOTO"], Label(label), 0)

    def branch(self, cond: tuple[str, int], mode: int, taken: str, other: str) -> None:
        self.program.emit(
            OP["BRANCH"], *self.operand(cond), mode, Label(taken), 0, Label(other), 0
        )

    def if_statement(self) -> None:
        cond = self.expression()
        self.expect("{")
        then, other, end = self.new_label(), self.new_label(), self.new_label()
        self.branch(cond, NONZERO, then, other)
        self.release()
        self.place(then)
        self.block()
        if self.accept("else"):
            self.jump(end)
            self.place(other)
            if self.accept("if"):
                self.if_statement()
            else:
                self.expect("{")
                self.block()
            self.place(end)
        else:
            self.place(other)

    def while_statement(self) -> None:
        top, body, end = self.new_label(), self.new_label(), self.new_label()
        self.place(top)
        cond = self.expression()
        self.expect("{")
        self.branch(cond, NONZERO, body, end)
        self.release()
        self.place(body)
        self.loops.append((top, end))
        self.block()
        self.loops.pop()
        self.jump(top)
        self.place(end)

    def switch_statement(self) -> None:
        value = self.expression()
        self.expect("{")
        cases: list[tuple[list[int], str]] = []
        default = self.new_label()
        end = self.new_label()
        bodies: list[tuple[str, list[Token]]] = []
        has_default = False
        while not self.accept("}"):
            if self.accept("default"):
                self.expect(":")
                self.expect("{")
                bodies.append((default, self.take_block()))
                has_default = True
                continue
            self.expect("case")
            tags = [self.constant()]
            while self.accept(","):
                tags.append(self.constant())
            self.expect(":")
            self.expect("{")
            label = self.new_label()
            cases.append((tags, label))
            bodies.append((label, self.take_block()))
        flat = [(tag, label) for tags, label in cases for tag in tags]
        words: list[int | Label] = [OP["SWITCH"], *self.operand(value), len(flat)]
        words += [tag for tag, _ in flat]
        for _, label in flat:
            words += [Label(label), 0]
        words += [Label(default), 0]
        self.program.emit(*words)
        self.release()
        for label, body in bodies:
            self.place(label)
            self.run_tokens(body)
            self.jump(end)
        if not has_default:
            self.place(default)
        self.place(end)

    def run_tokens(self, body: list[Token]) -> None:
        """Statements from tokens that were set aside (a block of a switch, a macro)."""
        saved = (self.tokens, self.at)
        self.tokens, self.at = (
            [*body, Token("op", "}", body[-1].line if body else 0)],
            0,
        )
        try:
            self.block()
        finally:
            self.tokens, self.at = saved

    def arguments(self) -> list[list[Token]]:
        """The arguments of a use, up to the closing parenthesis (the opening one is read)."""
        args: list[list[Token]] = []
        current: list[Token] = []
        depth = 0
        while True:
            token = self.next()
            if token.kind == "op" and token.text == "(":
                depth += 1
            if token.kind == "op" and token.text == ")":
                if depth == 0:
                    break
                depth -= 1
            if token.kind == "op" and token.text == "," and depth == 0:
                args.append(current)
                current = []
                continue
            current.append(token)
        if current or args:
            args.append(current)
        return args

    def substitute(
        self, name: str, params: list[str], body: list[Token], args: list[list[Token]]
    ) -> list[Token]:
        if len(args) != len(params):
            raise self.fail(f"{name} takes {len(params)} arguments")
        replaced: list[Token] = []
        for token in body:
            if token.kind == "name" and token.text in params:
                arg = args[params.index(token.text)]
                if len(arg) == 1:
                    replaced.extend(arg)
                else:
                    replaced += [
                        Token("op", "(", token.line),
                        *arg,
                        Token("op", ")", token.line),
                    ]
            else:
                replaced.append(token)
        return replaced

    def expand(self, name: str) -> None:
        params, body = self.macros[name]
        self.expect("(")
        args = self.arguments()
        self.expect(";")
        self.run_tokens(self.substitute(name, params, body, args))


# ---- the words ------------------------------------------------------------------------------


def words_of(program: Program) -> list[int]:
    header = [
        MAGIC,
        program.frame,
        len(program.params),
        sum((width + 7) // 8 for _, width in program.params),
        5 + 2 * len(program.params),
    ]
    for slot, width in program.params:
        header += [slot, width]
    base = len(header)
    out: list[int] = []
    for word in program.words:
        if isinstance(word, Label):
            out.append(base + program.labels[word.name])
        else:
            out.append(word)
    return [*header, *out]


def compile_source(source: str, path: str = "<source>") -> bytes:
    """The program (bytes of 64-bit words) that the source says."""
    maker = Maker(source, path)
    program = maker.parse()
    words = words_of(program)
    return struct.pack(f"<{len(words)}Q", *words)


def compile_file(path: Path) -> bytes:
    return compile_source(path.read_text(), str(path))
