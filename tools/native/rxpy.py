"""The Python -> RXF compiler: a Python subset as semantic graphs of the image.

The second import strand of docs/23 4.3, beside ``rximport``: a ``.py`` module arrives,
its functions lower to the same object vocabulary (a ``Function`` with a
``SemanticGraph``), and the one shared emitter (``rxemit.emit_delta``) writes the delta --
no second writer of the delta exists, so the vocabulary cannot drift between the strands
(one-defining-lang).  This pass fills the model ``rxemit`` owns (``LValue``, ``LOp``,
``LBlock``, ``LGraph``, ``Global``) the way ``rximport`` fills it from LLVM IR; the
compiler's muscle is this Python file (source-in-its-lang: the Python parser is here, not
pasted into a C string, and no runtime carries one).

The compile path (the mapping the model takes):

    Python                          RXF (the model of rxemit)          the image
    ----------------------------    ---------------------------------  ---------------------
    a module                        one delta: a Module of Functions   <Module>
    def f(a, b)                     LGraph f: Parameter values a,      <Function> +
                                    b, blocks of operations, and one    <SemanticGraph>
                                    result value per return
    x = a + b                       one CallCallback of the leaf       OP_CALL of the leaf
                                    wrapping_add_uint64_t of the       at run time
                                    starter, its result x
    if / while / and / or           SemanticBlocks: Branch and Goto    OP_BRANCH / edges
                                    terminators, block parameters      carrying the join
                                    (Bound values) carrying the join
    return expr                     Terminator.Return of one           OP_RETURN: the run
                                    ReturnValue move (the answer       answers that word
                                    word)
    a call of another def           one CallCallback naming the        RXF_GRAPH_CALL: the
                                    callee's Function path (a call     callee is a program
                                    card, never an inline)             of its own
    return a, b  /  lo, hi = f(x)   the out cells of docs/23 4.1: an   the Object handle
                                    Object handle per answer word      resolves the cell
                                    (its three words), the callee as   the callee's writes
                                    a call card whose out parameters   land in and the
                                    carry the handles, the answer      caller reads back
                                    word at the handle's offset
    a module-level name X           a Global (one named cell of the    <uint64_t name=X>
                                    delta) and one Object value that   cell; the Object
                                    names it by ``object_id``          resolves to its
                                                                       payload address

Every arithmetic, compare and move lowers to the leaves of the starter through the same
``CallCallback``/``ReturnValue`` opcodes ``rximport`` writes (the leaf paths are read from
its ``LEAVES`` table, never copied); the delta vocabulary itself is written by
``rxemit`` alone.

The subset (anything else is refused by name, never lowered to wrong words):

    module      a docstring, ``def`` of positional parameters (whole words), and
                ``NAME = <integer constant>``: a module-level binding is a named cell of
                the import unit (its payload the constant); a function assigns it
                through ``global``
    statements  ``pass``, a string (a docstring, ignored), assignment (``x = expr``,
                ``lo, hi = f(x)`` and ``x, y = expr1, expr2``), augmented assignment
                (``x += expr`` and every binary form), ``global``, ``if``/``elif``/``else``,
                ``while`` with ``break``/``continue``, ``return expr`` and
                ``return a, b`` (a tuple return: the out cells), and a call statement
    expressions integer literals, ``True``/``False``, names, ``+ - * // % & | ^ << >>``
                and unary ``- + ~``, comparisons (``== != < <= > >=``, chained forms
                included), ``and``/``or``/``not`` (Python's short circuit and value
                semantics), and calls of functions of the module (or of faces a prior
                import names in ``known``)

What is refused, by name (the message names the reason and the line):

    print and the builtins  a call of ``print`` is a host card this slice has no face for
                            (a seam request -- see the report of the lane); any other
                            builtin is a call of a face the subset does not compile
    closures/classes        a ``def`` inside a function, a lambda, a comprehension, a
                            class body, an attribute, ``nonlocal``
    exceptions              ``try``/``except``/``finally``/``raise``/``assert``/``with``
                            (the nlr seam of S11 is not in this slice), ``async``, and
                            ``yield`` (generators need the coroutine frame of 4.5)
    imports/for/match       ``import``/``from`` (the image is the module tree, 4.6: one
                            module is one source), ``for`` (the subset loops with while),
                            ``match``
    floats and strings      ``/``, ``**``, ``@``, a float literal, a string that is not a
                            docstring, a subscript, a format call: the subset's values
                            are whole words
    shapes it cannot bind   a name it cannot place (a read the paths do not all bind --
                            MicroPython raises UnboundLocalError there on some input; a
                            loop that assigns a name nothing binds before it), a
                            parameter that is not a plain name (a default, ``*args``,
                            ``**kwargs``, a keyword argument at a call), an annotation or
                            a decorator, a chained assignment (``x = y = expr``), a
                            starred or annotated assignment, ``del``, a walrus, a bare
                            return (MicroPython answers ``None`` there: not a word), a
                            return that mixes arities, a path that falls off the end of a
                            function, ``while``/``else``, a global that names no
                            module-level binding, and a literal or frame that does not
                            fit the run cell

The semantics contract (docs/23 6, decision 10: **MicroPython is the reference**):

    For every input whose MicroPython computation stays inside the word domain -- every
    intermediate value fits a signed 64-bit word, every shift count is 0..63, no division
    by zero, no INT64_MIN // -1 or INT64_MIN % -1, and no call chain past the call-card
    budget the landed run cell carries (RXF_GRAPH_CALL_DEPTH) -- the compiled program has
    the same observable behavior as the same source run by MicroPython: the same answer
    word per call (a bool is its 0/1 word), and the same words in the module's cells.  The
    word is the two's complement reading of the run's 64-bit answer; MicroPython's small
    int of that value is the same number.

    Outside the domain the behavior differs and is pinned one-sidedly (each side to what
    it really does, never a guess): ``+ - *`` wrap the word where MicroPython computes a
    bignum; ``<<``/``>>`` past the count refuse the run with RXF_REFUSE_SHIFT_COUNT where
    MicroPython shifts (or raises on a negative count); ``//``/``%`` by zero refuse the
    run with RXF_REFUSE_DIVISION_BY_ZERO where MicroPython raises ZeroDivisionError, and
    INT64_MIN paired with -1 refuses RXF_REFUSE_OVERFLOW where MicroPython answers
    2^63 or 0; a call chain past the budget refuses RXF_REFUSE_CALL_DEPTH where
    MicroPython recurses.  A refusal is the run ending with the status, never a wrong
    number: the division and shift leaves of the starter keep their own refusals, and
    this pass wires each division call's status as a status branch (the emitter's
    ``status_value`` slot -- the starter's ``<op>_status``): the leaf's own refusal lands
    as a Refuse carrying its status word, where MicroPython raises or answers a bignum.
    The shift count is guarded ahead of the call (a Refuse terminator per guarded path):
    the leaf takes a u32 count where the domain is a word, so its own check is narrower
    than the contract.

Out parameters (docs/23 4.1) have a Python shape: a function answers a tuple
(``return a, b``) and its callers unpack it (``lo, hi = f(x)``).  That is the out-cell
model exactly -- the callee writes each answer word through the Object handle its caller
passes (the handle its three words; the word at the handle's offset), the call is a call
card crossing a card boundary, and the caller reads the words back from the same cells.
The cell is named after the callee and its out position (``m.f.out_0``), so the callee's
Function and every caller carry one object id -- what lands is the caller's, not a copy.
Every compiled call reads its out words back before it makes the next call, so recursion
through a tuple return shares the cells safely (the differential pins it).

Where this pass reads the machine's numbers it reads them from ``graph_header`` and
``rximport`` (the leaf table and the object-id rule), and it writes its graphs through
``rxemit`` alone.  It edits neither importer: the capabilities this pass wanted are the
emitter and provenance seams its report named, landed where they belong -- a
``status_value`` field on ``LOp`` (the status branch above), a ``was once Python``
marker in the provenance record (``Provenance.was_once_python``, set for every module
this strand compiles), and the narrow-integer and ashr helpers in ``rxemit`` -- one
home both lowerings take, no mirror here.
"""

from __future__ import annotations

import argparse
import ast
import struct
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import graph_header
import rxemit
import rximport
from rxemit import (
    U32,
    U64,
    WIDE,
    Global,
    LBlock,
    LGraph,
    LOp,
    LValue,
    MOperand,
    emit_delta,
)

# ---- the vocabulary ----------------------------------------------------------------------------

FULL = rxemit.FULL  # the word's mask, one home (the emitter's narrow-integer helpers)
LOW_SIGNED = -(1 << 63)  # the word 0x8000000000000000 read as signed
NUMBERS = graph_header.numbers()
DIVISION_BY_ZERO = NUMBERS["RXF_REFUSE_DIVISION_BY_ZERO"]
OVERFLOW = NUMBERS["RXF_REFUSE_OVERFLOW"]
SHIFT_COUNT = NUMBERS["RXF_REFUSE_SHIFT_COUNT"]
CELL_BYTES = NUMBERS["RXF_GRAPH_CELL_BYTES"]
HANDLE_WORDS = 3  # an Object handle: (identity, generation, offset)

# the refusal of a source this pass does not lower; one exception type for both strands
Unsupported = rximport.Unsupported

# the leaf paths of the starter the model's CallCallback operations name (the LEAVES
# table of the C strand is read, never copied -- one leaf vocabulary)
LEAVES = rximport.LEAVES


def leaf(key: str) -> str:
    """The Function path of the starter's leaf this operation calls."""
    return LEAVES[key][0]


# the human name of a Python operator for a refusal message
BINOP_NAMES = {
    ast.Add: "the + operator",
    ast.Sub: "the - operator",
    ast.Mult: "the * operator",
    ast.Div: "true division (/ floats are not in the subset)",
    ast.FloorDiv: "the // operator",
    ast.Mod: "the % operator",
    ast.Pow: "the ** operator (the subset has no power)",
    ast.LShift: "the << operator",
    ast.RShift: "the >> operator",
    ast.BitAnd: "the & operator",
    ast.BitOr: "the | operator",
    ast.BitXor: "the ^ operator",
    ast.MatMult: "the @ operator",
}
BINOP_LOWERED = {
    ast.Add: "add",
    ast.Sub: "sub",
    ast.Mult: "mul",
    ast.FloorDiv: "floordiv",
    ast.Mod: "mod",
    ast.LShift: "shl",
    ast.RShift: "shr",
    ast.BitAnd: "and",
    ast.BitOr: "or",
    ast.BitXor: "xor",
}
CMP_KEYS = {
    ast.Eq: "eq",
    ast.NotEq: "ne",
    ast.Lt: "lt",
    ast.LtE: "le",
    ast.Gt: "gt",
    ast.GtE: "ge",
}

# the node kinds this pass refuses before it reaches one it knows: the walk of a def
# refuses them wherever they stand (a subset of the source, not of one path). ``Tuple``
# is not here: it is legal in a return and an unpacking and refused where it is a value
REFUSED_NODES: dict[type, str] = {
    ast.ClassDef: "a class (the subset has no objects)",
    ast.Lambda: "a lambda (a closure)",
    ast.ListComp: "a comprehension (a closure)",
    ast.SetComp: "a comprehension (a closure)",
    ast.DictComp: "a comprehension (a closure)",
    ast.GeneratorExp: "a generator expression (generators are not in this slice)",
    ast.Yield: "a yield (a generator: the coroutine frame is not in this slice)",
    ast.YieldFrom: "a yield from (a generator)",
    ast.Await: "an await (the uasyncio seam is not in this slice)",
    ast.Try: "a try/except (the exception seam is not in this slice)",
    ast.Raise: "a raise (the exception seam is not in this slice)",
    ast.Assert: "an assert (the panic seam is not in this slice)",
    ast.With: "a with statement (the context-manager face is not in this slice)",
    ast.AsyncWith: "a with statement (the context-manager face is not in this slice)",
    ast.AsyncFor: "a for loop (the subset loops with while)",
    ast.AsyncFunctionDef: "an async def (the uasyncio seam is not in this slice)",
    ast.For: "a for loop (the subset loops with while)",
    ast.Import: "an import (the image is the module tree: one module is one source)",
    ast.ImportFrom: "an import (the image is the module tree: one module is one source)",
    ast.Nonlocal: "a nonlocal (a closure)",
    ast.Delete: "a del (the subset's names are words of one binding)",
    ast.AnnAssign: "an annotated assignment (annotations are not in this slice)",
    ast.IfExp: "a conditional expression (the subset branches with if)",
    ast.Starred: "a starred expression (the subset passes words)",
    ast.Subscript: "a subscript (the subset has no sequences)",
    ast.Attribute: "an attribute (the subset has no objects)",
    ast.List: "a list (the subset's values are whole words)",
    ast.Dict: "a dict (the subset's values are whole words)",
    ast.Set: "a set (the subset's values are whole words)",
    ast.JoinedStr: "an f-string (the subset has no strings)",
    ast.FormattedValue: "an f-string (the subset has no strings)",
    ast.NamedExpr: "an assignment expression (the subset binds at statements)",
    ast.Match: "a match statement (the subset branches with if)",
}


class Refused(Unsupported):
    """A source outside the subset: the message names the reason and the line."""


REFUSED_KINDS = tuple(REFUSED_NODES)  # the kinds as one tuple (isinstance's shape)


def node_name(node: ast.AST) -> str:
    """What a node is, in the words of the refusal table, else its class."""
    for kind, words in REFUSED_NODES.items():
        if isinstance(node, kind):
            return words
    return f"a {node.__class__.__name__}"


def is_string(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def word_bytes(word: int) -> bytes:
    """One word as the bytes of a cell's payload (little endian, two's complement)."""
    return (word & FULL).to_bytes(8, "little")


def handle_bytes(identity: int) -> bytes:
    """The Object handle the payload opens with -- its three words -- and the answer
    word at the offset the handle names (docs/23 4.1)."""
    return struct.pack(f"<{HANDLE_WORDS}Q", identity, 1, 24) + bytes(8)


# ---- parsing: the subset's grammar, before anything lowers ------------------------------------

@dataclass
class Sig:
    """What a def answers and takes: the words of the run's frame."""

    name: str
    params: int  # the positional parameters of the def
    outs: int  # the out cells of a tuple return (0: the def answers one word)

    @property
    def arguments(self) -> int:
        """The words a run entry takes: one per parameter, two per out handle."""
        return self.params + 2 * self.outs


@dataclass
class Source:
    """A parsed module: its cells, its signatures, and the syntax behind them."""

    module: str
    cells: dict[str, int] = field(default_factory=dict)  # name -> the initial word
    signatures: dict[str, Sig] = field(default_factory=dict)
    functions: dict[str, ast.FunctionDef] = field(default_factory=dict)


def parse(text: str, module: str = "imported") -> Source:
    """The module's syntax as a ``Source``: every def's signature and every module-level
    binding, refusing what the subset does not take (by name and line)."""
    try:
        tree = ast.parse(text)
    except SyntaxError as problem:
        raise Refused(
            f"{module}: the source does not parse: {problem.msg} (line {problem.lineno})"
        ) from problem
    src = Source(module)
    if tree.body and isinstance(tree.body[0], ast.Expr) and is_string(tree.body[0].value):
        tree.body = tree.body[1:]  # the module's docstring: it documents, it does not run
    for statement in tree.body:
        where = f"line {statement.lineno}"
        if isinstance(statement, ast.FunctionDef):
            declare(src, statement)
        elif isinstance(statement, ast.Assign):
            cell_assignment(src, statement)
        elif isinstance(statement, ast.Expr) and is_string(statement.value):
            continue  # a string between statements: it documents, it does not run
        else:
            raise Refused(
                f"{module}: {where}: {node_name(statement)} at module level "
                "(a module carries defs and NAME = constant bindings)"
            )
    return src


def declare(src: Source, fn: ast.FunctionDef) -> None:
    """One def's signature: positional words in, one word or a tuple out."""
    if fn.name in src.signatures:
        raise Refused(f"{src.module}: line {fn.lineno}: {fn.name} is defined twice")
    if fn.decorator_list:
        raise Refused(
            f"{src.module}: line {fn.lineno}: a decorator on {fn.name} "
            "(the subset takes a def as it stands)"
        )
    if fn.returns is not None:
        raise Refused(f"{src.module}: line {fn.lineno}: an annotation on {fn.name}'s return")
    args = fn.args
    if args.vararg or args.kwarg or args.kwonlyargs or args.defaults or args.kw_defaults:
        raise Refused(
            f"{src.module}: line {fn.lineno}: {fn.name} takes a default, *args, **kwargs or "
            "a keyword parameter (the subset passes words positionally)"
        )
    if args.posonlyargs:
        raise Refused(
            f"{src.module}: line {fn.lineno}: {fn.name} takes a positional-only parameter "
            "(the subset passes words positionally)"
        )
    for arg in args.args:
        if arg.annotation is not None:
            raise Refused(
                f"{src.module}: line {fn.lineno}: an annotation on {fn.name}'s "
                f"parameter {arg.arg}"
            )
    src.signatures[fn.name] = Sig(fn.name, len(args.args), return_arity(src, fn))
    src.functions[fn.name] = fn


def returns_of(body: Sequence[ast.stmt]) -> list[ast.Return]:
    """The returns of one body, not those of a nested def or class (each has its own)."""
    found: list[ast.Return] = []
    stack: list[ast.AST] = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # a closure is refused where the def lowers; its returns are its own
        if isinstance(node, ast.Return):
            found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def return_arity(src: Source, fn: ast.FunctionDef) -> int:
    """The out cells of the def's answer: 0 where it answers one word, N for an N-tuple.

    A def answers one arity on every path (the run entry's frame is fixed at its door):
    returns of a tuple take the same length everywhere, and a bare return is refused --
    MicroPython answers ``None`` there, and ``None`` is not a word."""
    found: int | None = None
    for node in returns_of(fn.body):
        assert isinstance(node, ast.Return)  # returns_of answers only returns
        if node.value is None:
            raise Refused(
                f"line {node.lineno}: a bare return in {fn.name} "
                "(a return answers None, not a word)"
            )
        width = len(node.value.elts) if isinstance(node.value, ast.Tuple) else 1
        if isinstance(node.value, ast.Tuple):
            if not node.value.elts:
                raise Refused(f"line {node.lineno}: an empty return in {fn.name}")
            if len(node.value.elts) == 1:
                raise Refused(
                    f"line {node.lineno}: a return of a one-tuple in {fn.name} "
                    "(one word is what the def answers; return the value itself)"
                )
            for item in node.value.elts:
                if isinstance(item, ast.Starred):
                    raise Refused(f"line {node.lineno}: a starred return in {fn.name}")
        if found is None:
            found = width
        elif found != width:
            raise Refused(
                f"line {node.lineno}: {fn.name} answers {found} on one path and {width} "
                "on another (the run entry takes one shape)"
            )
    return 0 if found is None or found == 1 else found


def cell_assignment(src: Source, statement: ast.Assign) -> None:
    """A module-level binding: ``NAME = <integer constant>`` -- the cell's initial word.

    The cell is the module state the functions read and (under ``global``) write; its
    initializer folds here, so an initializer the subset cannot fold is refused rather
    than lowered to words that would run wrong."""
    if len(statement.targets) != 1 or not isinstance(statement.targets[0], ast.Name):
        raise Refused(
            f"{src.module}: line {statement.lineno}: a module-level assignment that is not "
            "NAME = <constant> (a chained or unpacking target needs words this pass would guess)"
        )
    name = statement.targets[0].id
    if name in src.cells:
        raise Refused(f"{src.module}: line {statement.lineno}: {name} is bound twice")
    src.cells[name] = fold_constant(src, statement.value, name)


def fold_constant(src: Source, node: ast.AST, name: str) -> int:
    """The one word a constant expression stands for, or a refusal naming the shape."""
    where = f"{src.module}: line {getattr(node, 'lineno', '?')}: the initializer of {name}"
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        value = 1 if node.value else 0
    elif isinstance(node, ast.Constant) and isinstance(node.value, int):
        value = node.value
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = fold_constant(src, node.operand, name)
        value = -inner if isinstance(node.op, ast.USub) else inner
    elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult)):
        a = fold_constant(src, node.left, name)
        b = fold_constant(src, node.right, name)
        value = {ast.Add: a + b, ast.Sub: a - b, ast.Mult: a * b}[type(node.op)]
    else:
        raise Refused(
            f"{where} is not an integer constant (a module-level binding is the one "
            "word its constant folds to)"
        )
    if not LOW_SIGNED <= value <= FULL // 2:
        raise Refused(f"{where} does not fit a 64-bit word")
    return value


# ---- the function compiler ----------------------------------------------------------------------

class Lowering:
    """One def of the module as an ``LGraph`` of blocks, operations and values.

    The statements lower to blocks of ``CallCallback`` (the starter's leaves) and
    ``ReturnValue`` moves, with block parameters carrying every join (the mem2reg the
    LLVM strand gets from clang, done here over the Python syntax itself).  Names are
    words threaded through the blocks: an assignment rebinds the name in the environment
    of its path, a join takes one block parameter per name whose incoming words differ,
    and a loop's carried names are the header's parameters."""

    def __init__(self, src: Source, fn: ast.FunctionDef, known: dict[str, str]) -> None:
        self.src = src
        self.module = src.module
        self.fn = fn
        self.sig = src.signatures[fn.name]
        self.known = dict(known)
        self.path = f"{self.module}.{fn.name}"
        self.graph = f"{self.path}.{fn.name}_graph"
        self.signature = f"{self.path}.signature"
        self.values: list[LValue] = []
        self.literals: dict[int, LValue] = {}
        self.blocks: list[LBlock] = []
        self.results: list[MOperand] = []
        self.globals: dict[str, Global] = {
            f"@{name}": Global(name, "i64", word_bytes(word), constant=False)
            for name, word in src.cells.items()
        }
        self.cell_values: dict[str, MOperand] = {}
        self.out_values: dict[str, LValue] = {}
        self.op_count = 0
        self.frame = 0  # the bytes the placed values take, kept under the run cell
        self.params: list[LValue] = []
        self.conditions: set[str] = set()
        self.entry: LBlock | None = None
        self.current: LBlock | None = None
        self.terminated = False  # the block being lowered ends in a Return or a Refuse
        self.assigned: set[str] = set()  # every name some statement assigns (locals)
        self.declared: set[str] = set()  # the names the def declares global
        self.env: dict[str, MOperand] = {}
        self.loops: list[Loop] = []  # the loops the statements are inside of
        self.load_scratch: MOperand | None = None

    # ---- names and values ------------------------------------------------------------------------

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
        if kind in ("Parameter", "Bound", "OperationResult"):
            self.frame += width + -width % 8
        return made

    def literal(self, constant: int) -> MOperand:
        constant &= FULL
        if constant not in self.literals:
            self.literals[constant] = self.value(
                "Literal", self.graph, f"const_{constant:x}", literal=constant
            )
        return ("literal", constant)

    def new_block(self) -> LBlock:
        made = LBlock(f"{self.graph}.block[{len(self.blocks)}]", len(self.blocks))
        self.blocks.append(made)
        return made

    def call(
        self,
        key: str,
        inputs: list[MOperand],
        input_types: list[str] | None = None,
    ) -> MOperand:
        """One leaf call that writes an out slot, as one CallCallback operation."""
        _, arity, has_out, _ = LEAVES[key]
        if not has_out or len(inputs) != arity:
            raise Refused(f"{self.fn.name}: {key} takes {arity} values and writes an out slot")
        target = self.current
        assert target is not None
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        result = self.value(
            "OperationResult", op, f"op{self.op_count}_result",
            owner_block=target.path, owner_op=op,
        )  # fmt: skip
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", inputs,
                input_types or [U64] * arity, result, leaf(key))
        )
        return ("value", result.path)

    def call_void(self, key: str, inputs: list[MOperand]) -> None:
        """One leaf call with no out slot (the memory leaves), as one CallCallback."""
        _, arity, has_out, _ = LEAVES[key]
        if has_out or len(inputs) != arity:
            raise Refused(f"{self.fn.name}: {key} takes {arity} values and writes no out slot")
        target = self.current
        assert target is not None
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", inputs, [U64] * arity, None, leaf(key))
        )

    def call_status(
        self, key: str, inputs: list[MOperand], input_types: list[str] | None = None
    ) -> MOperand:
        """A leaf call whose own refusal is the domain's, caught as a status branch.

        The call carries the emitter's ``status_value`` seam: the leaf writes its status
        (rxf_u32, native/basics.c) into the value the op names -- the starter's
        ``<op>_status`` slot -- and the block splits on it (``status_branch``). This is
        the wiring that replaces the pre-lowered guards where the leaf's own check IS
        the domain: a zero divisor and the pair INT64_MIN/-1 refuse with exactly the
        statuses the guards spelled by hand (the leaf keeps its own refusals)."""
        made = self.call(key, inputs, input_types)
        target = self.current
        assert target is not None
        node = target.ops[-1]
        status = self.value(
            "OperationResult", node.path, f"{node.path.rsplit('.', 1)[-1]}_status",
            owner_block=target.path, owner_op=node.path, width=4, type_path=U32,
        )  # fmt: skip
        node.status_value = status
        self.status_branch(("value", status.path))
        return made

    def status_branch(self, status: MOperand) -> None:
        """The status branch of a call (the starter's status blocks): the leaf's status
        is the block's condition -- a status value is typed ``uint32_t``, so the composer
        reads the branch as MODE_ZERO: status 0 takes the first edge (the run goes on)
        and anything else the second -- a Refuse carrying the same status word (the run
        answers it: the uncaught shape of a leaf's own refusal)."""
        current = self.current
        assert current is not None
        refused = self.new_block()
        refused.terminator = "Refuse"
        refused.terminator_value = status
        onward = self.new_block()
        current.terminator = "Branch"
        current.condition = self.own_condition(status, current)
        current.successors = [(onward.path, []), (refused.path, [])]
        self.current = onward

    def card(
        self, callee: str, inputs: list[MOperand], outs: int
    ) -> MOperand | None:
        """A call of a program: one CallCallback naming the callee's Function path (the
        call card of native/graph.h -- the callee's words at their object id, never an
        inline).  A callee that answers a tuple takes its out handles here and answers
        no word of its own; a scalar callee answers one."""
        target = self.current
        assert target is not None
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        result = None
        if not outs:
            result = self.value(
                "OperationResult", op, f"op{self.op_count}_result",
                owner_block=target.path, owner_op=op,
            )  # fmt: skip
        target.ops.append(
            LOp(op, len(target.ops), "CallCallback", inputs, [U64] * len(inputs), result, callee)
        )
        return ("value", result.path) if result is not None else None

    def move(self, source: MOperand, width: int = 8, at: LBlock | None = None) -> MOperand:
        """A ReturnValue operation: the bytes of a value into a fresh slot (the pattern of
        the starter's returns -- one edge, one place, the link check's rule).  The op
        lands in ``at`` (default: the block being lowered): a copy runs between the
        value it copies and the place that reads it."""
        target = at or self.current
        assert target is not None
        self.op_count += 1
        op = f"{target.path}.op{self.op_count}"
        result = self.value(
            "OperationResult", op, f"op{self.op_count}_result",
            owner_block=target.path, owner_op=op,
            width=width, type_path=WIDE if width > 8 else U64,
        )  # fmt: skip
        target.ops.append(LOp(op, len(target.ops), "ReturnValue", [source], [U64], result))
        return ("value", result.path)

    # ---- cells (a module-level binding) and out positions ---------------------------------------

    def cell(self, name: str) -> MOperand:
        """The Object value naming the module-level cell: the handle the run resolves to
        the cell's payload address (every graph that touches the name carries the value)."""
        found = self.cell_values.get(name)
        if found is None:
            made = self.value(
                "Object", self.graph, f"global_{name}",
                type_path=U64,
                object_id=rximport.object_id_of(f"{self.module}.{name}"),
                payload=word_bytes(self.src.cells[name]),
            )  # fmt: skip
            self.cell_values[name] = ("value", made.path)
            found = ("value", made.path)
        return found

    def out_cell(self, key: str) -> MOperand:
        """The out cell of the position ``key`` names (docs/23 4.1): the Object whose
        handle the call passes and whose out word the callee's return writes.  The cell
        is named after the callee and its out position, so the callee's Function and
        every caller carry one object id -- what lands is the caller's, not a copy."""
        found = self.out_values.get(key)
        if found is None:
            identity = rximport.object_id_of(key)
            made = self.value(
                "Object", self.graph, key.replace(".", "_"),
                width=HANDLE_WORDS * 8, type_path=WIDE,
                object_id=identity, payload=handle_bytes(identity),
            )  # fmt: skip
            self.out_values[key] = made
            found = made
        return ("value", found.path)

    def out_word(self, key: str) -> MOperand:
        """Where the out cell's answer word sits: the Object resolved at each use (the
        run resolves the operand to the payload address) plus the offset its handle
        carries -- the landing of the callee's writes and the caller's reads."""
        return self.call("wrapping_add_uint64_t", [self.out_cell(key), self.literal(24)])

    def load_scratch_of(self) -> MOperand:
        """The shared scratch wide slot a load of flat memory stages its bytes in.

        OP_MOVE reads operands, not the memory an address names, so a load copies the
        memory's bytes into a wide slot and reads the word out of the slot.  The staging
        is filled right before it is read, so the whole graph shares one such slot."""
        if self.load_scratch is None:
            self.load_scratch = self.move(self.literal(0), width=16)
        return self.load_scratch

    def load(self, address: MOperand) -> MOperand:
        """A word read from flat memory, staged in the shared wide scratch (the bytes
        move through the ``memory_copy`` leaf; the scratch is a slot, so the leaf is
        given its own address, where the address word is the flat address it carries)."""
        scratch = self.load_scratch_of()
        self.call_void(
            "memory_copy",
            [scratch, self.literal(16), address, self.literal(8), self.literal(8)],
        )
        return self.move(scratch)

    def store(self, address: MOperand, value: MOperand) -> None:
        """A word written to flat memory: the value staged wide first (a leaf reads its
        pointer positions as addresses), then the bytes over."""
        scratch = self.move(value, width=16)
        self.call_void(
            "memory_copy",
            [address, self.literal(8), scratch, self.literal(8), self.literal(8)],
        )

    # ---- expressions -----------------------------------------------------------------------------

    def operand(self, env: dict[str, MOperand], node: ast.AST) -> MOperand:
        """One expression as a word of the frame.  The subset's values are whole words:
        an integer or a bool is its 64-bit two's complement word (a bool is 0 or 1)."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool):
                return self.literal(1 if node.value else 0)
            if isinstance(node.value, int):
                if not LOW_SIGNED <= node.value <= FULL // 2:
                    raise Refused(
                        f"line {node.lineno}: a literal that does not fit a 64-bit word"
                    )
                return self.literal(node.value)
            if isinstance(node.value, str):
                raise Refused(
                    f"line {node.lineno}: a string as a value (the subset's values are "
                    "whole words)"
                )
            raise Refused(
                f"line {node.lineno}: a {type(node.value).__name__} as a value "
                "(the subset's values are whole words)"
            )
        if isinstance(node, ast.Name):
            return self.name_ref(env, node)
        if isinstance(node, ast.BinOp):
            return self.binop(env, node)
        if isinstance(node, ast.UnaryOp):
            return self.unary(env, node)
        if isinstance(node, ast.Compare):
            return self.compare(env, node)
        if isinstance(node, ast.BoolOp):
            return self.boolean(env, node)
        if isinstance(node, ast.Call):
            return self.call_expression(env, node)
        raise Refused(f"line {getattr(node, 'lineno', '?')}: {node_name(node)} as a value")

    def name_ref(self, env: dict[str, MOperand], node: ast.Name) -> MOperand:
        where = f"line {node.lineno}: {node.id}"
        if node.id in env:
            return env[node.id]
        if node.id in self.declared:
            return self.load(self.cell(node.id))
        if node.id in self.assigned:
            raise Refused(
                f"{where} is read where the subset cannot see it bound on every path "
                "(MicroPython raises UnboundLocalError where no path reached an "
                "assignment; the subset refuses instead of guessing a word)"
            )
        if node.id in self.src.cells:
            return self.load(self.cell(node.id))
        raise Refused(f"{where} is a name the subset cannot bind")

    def binop(self, env: dict[str, MOperand], node: ast.BinOp) -> MOperand:
        op = BINOP_LOWERED.get(type(node.op))
        if op is None:
            raise Refused(
                f"line {node.lineno}: {BINOP_NAMES.get(type(node.op), 'this operator')} "
                "is not of the subset"
            )
        a = self.operand(env, node.left)
        b = self.operand(env, node.right)
        return self.arith(op, a, b, node.lineno)

    def arith(self, op: str, a: MOperand, b: MOperand, lineno: int) -> MOperand:
        if op == "add":
            return self.call("wrapping_add_uint64_t", [a, b])
        if op == "sub":
            return self.call("wrapping_subtract_uint64_t", [a, b])
        if op == "mul":
            return self.call("wrapping_multiply_uint64_t", [a, b])
        if op == "and":
            return self.call("bit_and_uint64_t", [a, b])
        if op == "or":
            return self.call("bit_or_uint64_t", [a, b])
        if op == "xor":
            # no xor leaf exists: (a | b) - (a & b) is the same bits (rximport's rule)
            return self.call(
                "wrapping_subtract_uint64_t",
                [self.call("bit_or_uint64_t", [a, b]), self.call("bit_and_uint64_t", [a, b])],
            )
        if op == "shl":
            self.guard_shift(b)
            return self.call("shift_left_uint64_t", [a, b], input_types=[U64, U32])
        if op == "shr":
            self.guard_shift(b)
            return rxemit.ashr_value(self, a, b)
        if op in ("floordiv", "mod"):
            return self.divmod(op, a, b)
        raise Refused(f"line {lineno}: {op} is not of the subset")

    def divmod(self, op: str, a: MOperand, b: MOperand) -> MOperand:
        """Python's ``//`` and ``%``: floor semantics over the truncating signed leaves.

        The leaves keep their own refusals (native/basics.c: a zero divisor is
        RXF_REFUSE_DIVISION_BY_ZERO, INT64_MIN paired with -1 is RXF_REFUSE_OVERFLOW),
        and each call catches one as its status now (the emitter's ``status_value``
        seam): the status branch refuses the run with the leaf's own status where
        MicroPython raises or answers a bignum, never a wrong word. The guards this
        replaces pre-lowered were exactly these leaf domains -- ``guard`` stays the
        shape where the leaf's own check is narrower than the contract (a shift leaf
        takes a u32 count where the domain is a word)."""
        quotient = self.call_status("divide_int64_t", [a, b])
        remainder = self.call_status("remainder_int64_t", [a, b])
        # the floor adjustment: the quotient takes one off and the remainder the
        # divisor's sign where the remainder is not zero and the signs differ (Python's
        # // is the floor, % takes the sign of the divisor)
        sign_a = self.call(
            "shift_right_uint64_t", [a, self.literal(63)], input_types=[U64, U32]
        )
        sign_b = self.call(
            "shift_right_uint64_t", [b, self.literal(63)], input_types=[U64, U32]
        )
        adjust = self.call(
            "bit_and_uint64_t",
            [
                self.call(
                    "wrapping_subtract_uint64_t",
                    [self.literal(1), self.call("equal_uint64_t", [remainder, self.literal(0)])],
                ),
                self.call(
                    "wrapping_subtract_uint64_t",
                    [self.literal(1), self.call("equal_uint64_t", [sign_a, sign_b])],
                ),
            ],
        )
        if op == "floordiv":
            return self.call("wrapping_subtract_uint64_t", [quotient, adjust])
        into = self.call(
            "bit_and_uint64_t",
            [b, self.call("wrapping_subtract_uint64_t", [self.literal(0), adjust])],
        )
        return self.call("wrapping_add_uint64_t", [remainder, into])

    def guard(self, condition: MOperand, status: int) -> None:
        """A domain check: the block splits at the check and the refusing path ends in a
        Refuse terminator (the run answers the status there -- the uncaught shape)."""
        current = self.current
        assert current is not None
        refused = self.new_block()
        refused.terminator = "Refuse"
        refused.terminator_value = self.literal(status)
        onward = self.new_block()
        current.terminator = "Branch"
        current.condition = self.own_condition(condition, current)
        current.successors = [(refused.path, []), (onward.path, [])]
        self.current = onward

    def guard_shift(self, count: MOperand) -> None:
        """A shift count of 0..63 is the domain (MicroPython shifts by any count and
        raises on a negative one): past it the run refuses RXF_REFUSE_SHIFT_COUNT.  The
        unsigned count word is under 64 exactly when the count is in range."""
        self.guard(
            self.call(
                "wrapping_subtract_uint64_t",
                [self.literal(1), self.call("less_uint64_t", [count, self.literal(64)])],
            ),
            SHIFT_COUNT,
        )

    def unary(self, env: dict[str, MOperand], node: ast.UnaryOp) -> MOperand:
        made = self.operand(env, node.operand)
        if isinstance(node.op, ast.USub):
            return self.call("wrapping_subtract_uint64_t", [self.literal(0), made])
        if isinstance(node.op, ast.UAdd):
            return made
        if isinstance(node.op, ast.Invert):
            return self.call("bit_not_uint64_t", [made])
        if isinstance(node.op, ast.Not):
            return self.call("equal_uint64_t", [made, self.literal(0)])
        raise Refused(f"line {node.lineno}: a {node.op.__class__.__name__} is not of the subset")

    def compare(self, env: dict[str, MOperand], node: ast.Compare) -> MOperand:
        """Comparisons of words (signed for the orders, word equality for == and !=), and
        chained forms with Python's short circuit: ``a < b < c`` is ``a < b and b < c``
        with b evaluated once and c only where the first holds."""
        if not node.ops:
            raise Refused(f"line {node.lineno}: an empty comparison")
        left = self.operand(env, node.left)
        prior = self.operand(env, node.comparators[0])
        made = self.one_compare(node.ops[0], left, prior, node.lineno)
        for op, comparator in zip(node.ops[1:], node.comparators[1:], strict=True):
            def when_true(
                op: ast.cmpop = op,
                comparator: ast.expr = comparator,
                prior: MOperand = prior,
            ) -> list[MOperand]:
                new = self.operand(env, comparator)  # inside the arm: lazily evaluated
                return [self.one_compare(op, prior, new, node.lineno), new]

            def when_false(made: MOperand = made, prior: MOperand = prior) -> list[MOperand]:
                return [made, prior]

            made, prior = self.lazy_join(made, when_true, when_false, 2)
        return made

    def one_compare(self, op: ast.cmpop, a: MOperand, b: MOperand, lineno: int) -> MOperand:
        key = CMP_KEYS.get(type(op))
        if key is None:
            raise Refused(f"line {lineno}: a comparison the subset does not take")
        one = self.literal(1)
        if key == "eq":
            return self.call("equal_uint64_t", [a, b])
        if key == "ne":
            return self.call(
                "wrapping_subtract_uint64_t", [one, self.call("equal_uint64_t", [a, b])]
            )
        # the orders are signed: 0x8000.. is the top of the signed words and the bottom
        # of the unsigned ones, so the add of the sign bit orders the signed as unsigned
        signed = self.call(
            "less_uint64_t",
            [
                self.call("wrapping_add_uint64_t", [a, self.literal(1 << 63)]),
                self.call("wrapping_add_uint64_t", [b, self.literal(1 << 63)]),
            ],
        )
        if key == "lt":
            return signed
        if key == "ge":
            return self.call("wrapping_subtract_uint64_t", [one, signed])
        swapped = self.call(
            "less_uint64_t",
            [
                self.call("wrapping_add_uint64_t", [b, self.literal(1 << 63)]),
                self.call("wrapping_add_uint64_t", [a, self.literal(1 << 63)]),
            ],
        )
        if key == "gt":
            return swapped
        return self.call("wrapping_subtract_uint64_t", [one, swapped])

    def lazy_join(
        self,
        condition: MOperand,
        when_true: Callable[[], list[MOperand]],
        when_false: Callable[[], list[MOperand]],
        count: int,
    ) -> list[MOperand]:
        """``condition`` picks an arm whose operands are evaluated only there: the join
        takes one block parameter per value the arms hand it (Python's ``and``/``or``
        value semantics and the chained comparison's shared middle operand)."""
        entry = self.current
        assert entry is not None
        join = self.new_block()
        chosen = [
            self.value(
                "Bound", join.path, f"blkparam[{len(join.params) + at}]", owner_block=join.path,
            )
            for at in range(count)
        ]
        join.params.extend(chosen)
        starts = []
        for arm in (when_true, when_false):
            start = self.new_block()
            self.current = start
            self.terminated = False
            made = arm()
            if len(made) != count:
                raise Refused(f"{self.fn.name}: a lazy arm of {len(made)} values for {count}")
            at = self.current
            at.terminator = "Goto"
            at.successors = [(join.path, made)]
            starts.append(start)
        self.current = entry
        entry.terminator = "Branch"
        entry.condition = self.own_condition(condition, entry)
        entry.successors = [(starts[0].path, []), (starts[1].path, [])]
        self.terminated = False
        self.current = join
        return [("value", value.path) for value in chosen]

    def lazy_and(self, condition: MOperand, get: Callable[[], MOperand]) -> MOperand:
        """``condition and get()``: Python's value is the second operand where the first
        is truthy, else the first itself."""
        return self.lazy_join(condition, lambda: [get()], lambda: [condition], 1)[0]

    def lazy_or(self, condition: MOperand, get: Callable[[], MOperand]) -> MOperand:
        """``condition or get()`` with the same value semantics."""
        return self.lazy_join(condition, lambda: [condition], lambda: [get()], 1)[0]

    def boolean(self, env: dict[str, MOperand], node: ast.BoolOp) -> MOperand:
        if len(node.values) < 2:
            raise Refused(f"line {node.lineno}: a boolean of one operand")
        made = self.operand(env, node.values[0])
        for right in node.values[1:]:
            if isinstance(node.op, ast.And):
                made = self.lazy_and(made, lambda right=right: self.operand(env, right))
            elif isinstance(node.op, ast.Or):
                made = self.lazy_or(made, lambda right=right: self.operand(env, right))
            else:
                raise Refused(
                    f"line {node.lineno}: a boolean operator the subset does not take"
                )
        return made

    def call_expression(self, env: dict[str, MOperand], node: ast.Call) -> MOperand:
        """A call of a function of the module (or a face ``known`` of another import): one
        call card, its answer one word.  A callee that answers a tuple is refused here --
        a tuple call is unpacked (``lo, hi = f(x)``) or discarded as a statement."""
        callee, outs = self.callee_of(node)
        if outs:
            raise Refused(
                f"line {node.lineno}: {callee} answers a tuple of {outs}: unpack it "
                "(lo, hi = f(x)) or call it as a statement"
            )
        return self.call_card(env, node, callee, 0) or self.literal(0)

    def call_card(
        self, env: dict[str, MOperand], node: ast.Call, callee: str, outs: int
    ) -> MOperand | None:
        arguments = self.arguments_of(env, node, callee)
        sig = self.sig_of(callee)
        if sig.params >= 0 and len(arguments) != sig.params:
            raise Refused(
                f"line {node.lineno}: {callee} takes {sig.params} values and the call "
                f"passes {len(arguments)}"
            )
        inputs = list(arguments) + [self.out_cell(f"{callee}.out_{index}") for index in range(outs)]
        return self.card(callee, inputs, outs)

    def sig_of(self, callee: str) -> Sig:
        bare = callee.rsplit(".", 1)[-1]
        if callee.startswith(f"{self.module}.") and bare in self.src.signatures:
            return self.src.signatures[bare]
        return Sig(bare, -1, 0)  # a face of another import: its arity is not this module's

    def arguments_of(
        self, env: dict[str, MOperand], node: ast.Call, callee: str
    ) -> list[MOperand]:
        if node.keywords:
            raise Refused(
                f"line {node.lineno}: a keyword argument at a call of {callee} "
                "(the subset passes words positionally)"
            )
        for argument in node.args:
            if isinstance(argument, ast.Starred):
                raise Refused(f"line {node.lineno}: a starred argument at a call of {callee}")
        return [self.operand(env, argument) for argument in node.args]

    def callee_of(self, node: ast.Call) -> tuple[str, int]:
        if not isinstance(node.func, ast.Name):
            raise Refused(f"line {node.lineno}: {node_name(node.func)} as the callee of a call")
        name = node.func.id
        if name in self.src.signatures:
            sig = self.src.signatures[name]
            return f"{self.module}.{name}", sig.outs
        if name in self.known:
            return self.known[name], 0
        if name in self.src.cells:
            raise Refused(f"line {node.lineno}: {name} is a cell of the module, not a function")
        raise Refused(
            f"line {node.lineno}: a call of {name} (the subset calls the functions it "
            "compiles, or faces a prior import names in known)"
        )

    # ---- statements -------------------------------------------------------------------------------

    def compile(self) -> LGraph:
        self.prescan(self.fn.body)
        entry = self.new_block()
        self.entry = entry
        self.current = entry
        for index, arg in enumerate(self.fn.args.args):
            made = self.value("Parameter", self.graph, f"parameter[{index}]")
            self.params.append(made)
            self.env[arg.arg] = ("value", made.path)
        for _ in range(self.sig.outs):
            # an out position of the tuple return: the handle its three words (docs/23
            # 4.1), the shape a run entry passes; the writes land through the Object the
            # cell carries
            made = self.value(
                "Parameter", self.graph, f"parameter[{len(self.params)}]",
                width=HANDLE_WORDS * 8, type_path=WIDE,
            )  # fmt: skip
            self.params.append(made)
        self.statements(self.fn.body, self.env)
        if not self.terminated:
            raise Refused(
                f"line {self.fn.end_lineno}: a path of {self.fn.name} falls off the end "
                "(MicroPython answers None there: not a word) -- end the function with a return"
            )
        if not self.results:
            raise Refused(f"{self.fn.name}: no return of a value (the subset's functions answer)")
        self.own_terminus()
        self.blocks = self.reachable(self.blocks)
        if self.frame > CELL_BYTES:
            raise Refused(
                f"{self.fn.name}: a frame of {self.frame} bytes is too large "
                f"(RXF_GRAPH_CELL_BYTES is {CELL_BYTES})"
            )
        return LGraph(
            self.fn.name, self.module, self.path, self.graph, self.signature,
            self.params, self.blocks, self.results, self.values, {},
            globals=self.globals,
        )  # fmt: skip

    def prescan(self, body: list[ast.stmt]) -> None:
        """The names the def assigns (its locals), the ones it declares global, and the
        shapes the subset refuses wherever they stand -- before anything lowers."""
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, ast.Global):
                self.declared.update(node.names)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self.collect_target(target)
            elif isinstance(node, ast.AugAssign):
                self.collect_target(node.target)
            elif isinstance(node, ast.FunctionDef):
                raise Refused(
                    f"line {node.lineno}: a nested def ({node.name}): closures are not "
                    "in this slice"
                )
            elif isinstance(node, ast.Tuple):
                continue  # legal in a return and an unpacking; refused where it is a value
            elif isinstance(node, REFUSED_KINDS):
                raise Refused(f"line {getattr(node, 'lineno', '?')}: {node_name(node)}")
        overlap = self.declared & {arg.arg for arg in self.fn.args.args}
        if overlap:
            raise Refused(
                f"line {self.fn.lineno}: {sorted(overlap)} is both a parameter and a "
                f"global of {self.fn.name}"
            )
        for name in sorted(self.declared):
            if name not in self.src.cells:
                raise Refused(
                    f"line {self.fn.lineno}: global {name} names no module-level binding"
                )

    def collect_target(self, target: ast.AST) -> None:
        if isinstance(target, ast.Name):
            self.assigned.add(target.id)
        elif isinstance(target, ast.Tuple):
            for item in target.elts:
                self.collect_target(item)
        elif isinstance(target, ast.Starred):
            raise Refused(f"line {getattr(target, 'lineno', '?')}: a starred target")
        else:
            raise Refused(
                f"line {getattr(target, 'lineno', '?')}: {node_name(target)} as a target"
            )

    def statements(self, body: list[ast.stmt], env: dict[str, MOperand]) -> None:
        for statement in body:
            if self.terminated:
                return  # dead code of the path: MicroPython never runs it either
            self.statement(statement, env)

    def statement(self, node: ast.stmt, env: dict[str, MOperand]) -> None:
        where = f"line {node.lineno}"
        if isinstance(node, ast.Pass):
            return
        if isinstance(node, ast.Expr):
            if is_string(node.value):
                return  # a docstring: it documents, it does not run
            if isinstance(node.value, ast.Call):
                callee, outs = self.callee_of(node.value)
                self.call_card(env, node.value, callee, outs)
                return
            raise Refused(
                f"{where}: {node_name(node.value)} as a statement (a call is the only "
                "expression whose statement the subset lowers)"
            )
        if isinstance(node, ast.Assign):
            self.assign(node, env)
            return
        if isinstance(node, ast.AugAssign):
            self.augassign(node, env)
            return
        if isinstance(node, ast.Return):
            self.return_statement(node, env)
            return
        if isinstance(node, ast.Global):
            return  # the prescan took the names; a declaration lowers to nothing
        if isinstance(node, ast.If):
            self.if_statement(node, env)
            return
        if isinstance(node, ast.While):
            self.while_statement(node, env)
            return
        if isinstance(node, ast.Break):
            self.jump(node, env, "break")
            return
        if isinstance(node, ast.Continue):
            self.jump(node, env, "continue")
            return
        raise Refused(f"{where}: {node_name(node)} as a statement")

    def assign(self, node: ast.Assign, env: dict[str, MOperand]) -> None:
        if len(node.targets) != 1:
            raise Refused(
                f"line {node.lineno}: a chained assignment (x = y = expr): the subset "
                "binds one name at a time"
            )
        target = node.targets[0]
        if isinstance(target, ast.Name):
            made = self.operand(env, node.value)
            if target.id in self.declared:
                self.store(self.cell(target.id), made)
            else:
                env[target.id] = made
            return
        if isinstance(target, ast.Tuple):
            self.unpack(node, target, env)
            return
        raise Refused(f"line {node.lineno}: {node_name(target)} as an assignment target")

    def unpack(self, node: ast.Assign, target: ast.Tuple, env: dict[str, MOperand]) -> None:
        """``lo, hi = f(x)`` (the out cells of the callee's tuple) and ``x, y = a, b``."""
        names = []
        for item in target.elts:
            if not isinstance(item, ast.Name):
                raise Refused(f"line {node.lineno}: {node_name(item)} as an unpacking target")
            if item.id in self.declared:
                raise Refused(
                    f"line {node.lineno}: global {item.id} cannot be bound by an unpacking"
                )
            names.append(item.id)
        value = node.value
        if isinstance(value, ast.Tuple):
            if len(value.elts) != len(names):
                raise Refused(
                    f"line {node.lineno}: {len(names)} names for {len(value.elts)} values"
                )
            made = [self.operand(env, item) for item in value.elts]
        elif isinstance(value, ast.Call):
            callee, outs = self.callee_of(value)
            if outs != len(names):
                raise Refused(
                    f"line {node.lineno}: {callee} answers {outs} and {len(names)} names take it"
                )
            self.call_card(env, value, callee, outs)
            # the words read back before the next call: recursion through a tuple return
            # shares the cells (one cell per callee and out position), and what is read
            # here is this call's answer
            made = [self.load(self.out_word(f"{callee}.out_{index}")) for index in range(outs)]
        else:
            raise Refused(
                f"line {node.lineno}: an unpacking of {node_name(value)} (a tuple of "
                "values or a call that answers a tuple)"
            )
        for name, item in zip(names, made, strict=True):
            env[name] = item

    def augassign(self, node: ast.AugAssign, env: dict[str, MOperand]) -> None:
        if not isinstance(node.target, ast.Name):
            raise Refused(
                f"line {node.lineno}: an augmented assignment of {node_name(node.target)}"
            )
        op = BINOP_LOWERED.get(type(node.op))
        if op is None:
            raise Refused(
                f"line {node.lineno}: {BINOP_NAMES.get(type(node.op), 'this operator')} "
                "in an augmented assignment"
            )
        name = node.target
        if name.id in self.declared:
            made = self.arith(
                op, self.load(self.cell(name.id)), self.operand(env, node.value), node.lineno
            )
            self.store(self.cell(name.id), made)
            return
        if name.id not in env:
            raise Refused(
                f"line {node.lineno}: {name.id} is augmented where the subset cannot see "
                "it bound on every path"
            )
        env[name.id] = self.arith(op, env[name.id], self.operand(env, node.value), node.lineno)

    def return_statement(self, node: ast.Return, env: dict[str, MOperand]) -> None:
        assert node.value is not None  # the prescan refused a bare return
        if isinstance(node.value, ast.Tuple):
            values = [self.operand(env, item) for item in node.value.elts]
            for index, made in enumerate(values):
                self.store(self.out_word(f"{self.path}.out_{index}"), made)
            self.finish_return(self.literal(0))
            return
        self.finish_return(self.operand(env, node.value))

    def finish_return(self, made: MOperand) -> None:
        """The Return terminator and the answer it carries: one ReturnValue move per
        return (the graph has one result value per Return, the emitter's rule)."""
        target = self.current
        assert target is not None
        answered = self.move(made)
        target.terminator = "Return"
        target.terminator_value = answered
        self.results.append(answered)
        self.terminated = True

    def jump(self, node: ast.stmt, env: dict[str, MOperand], kind: str) -> None:
        loop = self.loops[-1] if self.loops else None
        if loop is None:
            raise Refused(f"line {node.lineno}: {kind} outside a loop")
        loop.jump(node, env, kind)

    def if_statement(self, node: ast.If, env: dict[str, MOperand]) -> None:
        condition = self.operand(env, node.test)
        entry = self.current
        assert entry is not None
        join = self.new_block()
        then_block = self.new_block()
        else_block = self.new_block()
        entry.terminator = "Branch"
        entry.condition = self.own_condition(condition, entry)
        entry.successors = [(then_block.path, []), (else_block.path, [])]
        incoming: list[tuple[LBlock, int, dict[str, MOperand]]] = []
        self.current = then_block
        self.terminated = False
        then_env = dict(env)
        self.statements(node.body, then_env)
        if not self.terminated:
            self.current.terminator = "Goto"
            self.current.successors = [(join.path, [])]
            incoming.append((self.current, 0, then_env))
        self.current = else_block
        self.terminated = False
        else_env = dict(env)
        self.statements(node.orelse, else_env)
        if not self.terminated:
            self.current.terminator = "Goto"
            self.current.successors = [(join.path, [])]
            incoming.append((self.current, 0, else_env))
        if not incoming:
            self.terminated = True
            self.current = join
            return
        self.finish_join(join, incoming, env)

    def while_statement(self, node: ast.While, env: dict[str, MOperand]) -> None:
        if node.orelse:
            raise Refused(f"line {node.lineno}: an else on a while loop")
        Loop(self, node, env).run()

    def finish_join(
        self,
        join: LBlock,
        incoming: list[tuple[LBlock, int, dict[str, MOperand]]],
        env: dict[str, MOperand],
    ) -> None:
        """The merge of paths: one block parameter per name whose incoming words differ
        (a name every path hands the same operand keeps that operand).

        The edges were opened with their target and no arguments; the parameters this
        makes are what each edge hands the join, in the order the names sort."""
        names = sorted({name for _, _, taken in incoming for name in taken})
        merged: dict[str, MOperand] = {}
        handed: list[list[MOperand]] = []
        for name in names:
            present = [taken.get(name) for _, _, taken in incoming]
            if any(hand is None for hand in present):
                raise Refused(
                    f"{name} is bound on one path of a join and not another (the subset "
                    "needs every path to hand the merge the same names; MicroPython "
                    "answers UnboundLocalError where a path reaches the name unbound)"
                )
            hands = [hand for hand in present if hand is not None]
            first = self.operand_path(hands[0])
            if all(self.operand_path(hand) == first for hand in hands[1:]):
                merged[name] = hands[0]
                continue
            made = self.value(
                "Bound", join.path, f"blkparam[{len(join.params)}]", owner_block=join.path
            )
            join.params.append(made)
            merged[name] = ("value", made.path)
            handed.append(hands)
        for index, (block, successor, _) in enumerate(incoming):
            target, _ = block.successors[successor]
            block.successors[successor] = (target, [hands[index] for hands in handed])
        env.clear()
        env.update(merged)
        self.current = join
        self.terminated = False

    def operand_path(self, operand: MOperand) -> str:
        return operand[1] if operand[0] == "value" else self.literals[operand[1]].path

    def own_condition(self, condition: MOperand, target: LBlock) -> MOperand:
        """A branch's condition as its own reference (the image binds one value to one
        field: a flag a second branch takes copies)."""
        if condition[0] == "value" and condition[1] in self.conditions:
            condition = self.move(condition)
        if condition[0] == "value":
            self.conditions.add(condition[1])
        return condition

    @staticmethod
    def reachable(blocks: list[LBlock]) -> list[LBlock]:
        """The blocks the entry can reach, in the order they were made.

        An ``if`` whose arms all return leaves its join block unreachable -- an empty
        block no edge names.  The emitter would write it and the composer would walk
        its empty terminator, so what no edge names is not part of the graph."""
        if not blocks:
            return blocks
        by_path = {block.path: block for block in blocks}
        seen = {blocks[0].path}
        wave = [blocks[0].path]
        while wave:
            at = by_path[wave.pop()]
            for target, _ in at.successors:
                if target not in seen:
                    seen.add(target)
                    wave.append(target)
        return [block for block in blocks if block.path in seen]

    def own_terminus(self) -> None:
        """The condition and the answer of every block, each its own reference.

        One edge, one place (the link check's rule): the ``condition_value`` and
        ``terminator_value`` fields name a value on the block itself, so that value may
        not also stand among the block's own references -- a parameter it takes in or a
        value it hands a successor.  Both take a copy of the same bits here, as a return
        already does, so the branch goes the same way."""
        for block in self.blocks:
            held = {parameter.path for parameter in block.params}
            for _, arguments in block.successors:
                held.update(self.operand_path(argument) for argument in arguments)
            if block.condition is not None and self.operand_path(block.condition) in held:
                block.condition = self.move(block.condition, at=block)
            if (
                block.terminator_value is not None
                and self.operand_path(block.terminator_value) in held
            ):
                block.terminator_value = self.move(block.terminator_value, at=block)


class Loop:
    """A while statement: the header's parameters carry the names the loop rebinds.

    The loop-carried names are the ones bound before the loop and assigned inside it (a
    name the loop assigns and nothing binds before it is refused where a path could read
    it unbound -- the join and name rules say so, MicroPython would raise
    UnboundLocalError there on some input).  ``break`` edges and the header's own exit
    hand their words to one exit block; ``continue`` and the body's end hand theirs to
    the header."""

    def __init__(self, lowering: Lowering, node: ast.While, env: dict[str, MOperand]) -> None:
        self.fn = lowering
        self.node = node
        self.given = env  # the environment the statements hold: updated in place at the exit
        self.outer = dict(env)
        self.carried = sorted(
            name
            for name in lowering.assigned
            if name in self.outer and name not in lowering.declared and self.assigns(node.body, name)
        )
        self.header = lowering.new_block()
        self.exit = lowering.new_block()
        self.breaks: list[tuple[LBlock, int, dict[str, MOperand]]] = []

    def assigns(self, body: list[ast.stmt], name: str) -> bool:
        """Whether the loop body assigns the name (on some path of some nesting)."""
        for node in ast.walk(ast.Module(body=body, type_ignores=[])):
            if isinstance(node, ast.Assign):
                if any(self.names_of(target, name) for target in node.targets):
                    return True
            elif isinstance(node, ast.AugAssign) and self.names_of(node.target, name):
                return True
        return False

    def names_of(self, target: ast.AST, name: str) -> bool:
        if isinstance(target, ast.Name):
            return target.id == name
        if isinstance(target, ast.Tuple):
            return any(self.names_of(item, name) for item in target.elts)
        return False

    def run(self) -> None:
        fn = self.fn
        outer_block = fn.current
        assert outer_block is not None and fn.entry is not None
        env: dict[str, MOperand] = {}
        for name in self.carried:
            made = fn.value(
                "Bound", self.header.path, f"blkparam[{len(self.header.params)}]",
                owner_block=self.header.path,
            )  # fmt: skip
            self.header.params.append(made)
            env[name] = ("value", made.path)
        for name, value in self.outer.items():
            if name not in env:
                env[name] = value
        # the edge that opens the loop hands the header the words before it
        outer_block.terminator = "Goto"
        outer_block.successors = [
            (self.header.path, [self.outer[name] for name in self.carried])
        ]
        fn.current = self.header
        fn.terminated = False
        condition = fn.operand(env, self.node.test)
        # the condition may have walked through guard blocks; the branch is the last one
        testing = fn.current
        body = fn.new_block()
        testing.terminator = "Branch"
        testing.condition = fn.own_condition(condition, testing)
        testing.successors = [(body.path, []), (self.exit.path, [])]
        header_exit = (testing, len(testing.successors) - 1, env)
        fn.current = body
        fn.terminated = False
        body_env = dict(env)
        fn.loops.append(self)
        fn.statements(self.node.body, body_env)
        fn.loops.pop()
        if not fn.terminated:
            at = fn.current
            at.terminator = "Goto"
            at.successors = [
                (self.header.path, [body_env[name] for name in self.carried])
            ]
        # the exit: the header's own words and every break's
        fn.finish_join(self.exit, [header_exit, *self.breaks], self.outer)
        self.given.clear()  # the words after the loop are the exit's, not the ones before
        self.given.update(self.outer)
        fn.terminated = False

    def jump(self, node: ast.stmt, env: dict[str, MOperand], kind: str) -> None:
        fn = self.fn
        if kind == "break":
            at = fn.current
            assert at is not None
            at.terminator = "Goto"
            at.successors = [(self.exit.path, [])]
            self.breaks.append((at, 0, dict(env)))
            fn.terminated = True
            return
        at = fn.current
        assert at is not None
        at.terminator = "Goto"
        at.successors = [(self.header.path, [env[name] for name in self.carried])]
        fn.terminated = True


# ---- the module as graphs ------------------------------------------------------------------------

def graphs_of(
    text: str,
    module: str = "imported",
    only: set[str] | None = None,
    known: dict[str, str] | None = None,
) -> dict[str, LGraph]:
    """The functions of a Python module as graphs; ``only`` names the ones wanted.

    A function outside the subset raises ``Refused`` when it is asked for (in ``only``)
    and is skipped when it is not: a corpus file may carry helpers this pass cannot
    lower.  ``known`` maps the name of a function of another module (already imported
    into the image) to the Function path it is at, so a call of one of them is a call
    card naming that path."""
    src = parse(text, module)
    found: dict[str, LGraph] = {}
    for name, fn in src.functions.items():
        if only is not None and name not in only:
            continue
        found[name] = Lowering(src, fn, known or {}).compile()
    if only is not None and set(found) != only:
        missing = ", ".join(sorted(only - set(found)))
        raise Refused(f"not functions of the module: {missing}")
    return {name: found[name] for name in sorted(found)}


def import_py_file(
    image,
    source: str | Path,
    module: str = "imported",
    only: set[str] | None = None,
    known: dict[str, str] | None = None,
    provenance=None,
) -> dict[str, str]:
    """One delta onto the image; the name of every compiled function to its Function path.

    The same shape as ``rximport.import_source_file``: ``image.apply`` is the only thing
    done to the image, the source's comments are lifted whatever the run claims, and the
    claim is the same ``Provenance`` machine (docs/22, docs/23 5) -- one origin record
    and one citation per file, the locators from the source's own lines.  The marker
    *was once Python* of 5 rides every record this strand makes (the module was Python
    before it was graphs): ``was_once_python`` is set here whatever the tier and the
    report speaks it, while every C, C++ and Rust import leaves it False."""
    path = Path(source)
    text = path.read_text()
    graphs = list(graphs_of(text, module, only, known=known).values())
    if not graphs:
        return {}
    if provenance is not None and not isinstance(provenance, rximport.Provenance):
        provenance = rximport.Provenance.parse(str(provenance))
    if provenance is not None and provenance.importer == "rximport":
        provenance.importer = "rxpy"  # the fact this strand stands on
    prov = provenance if provenance is not None else rximport.Provenance(tier=None)
    prov.was_once_python = True  # the marker of docs/23 5: this module was Python
    comments = rximport.scan_comments(text, ("#",))
    targets = rximport.comment_targets(text, comments, [graph.name for graph in graphs])
    prov.open(image)  # the claim's first write: the floor speaks before anything is imported
    image.apply(emit_delta(graphs, module))
    mapping = {graph.name: graph.path for graph in graphs}
    prov.record(image, module, mapping, rximport.relative(path), text, list(zip(comments, targets)))
    return mapping


def main(argv: Sequence[str] | None = None) -> int:
    """The command line of the Python strand: compile the sources, import them, print the
    report.  ``rxpy.py source.py --provenance full,repository=...,commit=...,license=...``
    -- the same claim shape as ``rximport`` (docs/23 5)."""
    parser = argparse.ArgumentParser(
        prog="rxpy",
        description="Import .py sources into an RXF image; the report says what the run claimed.",
    )
    parser.add_argument("sources", nargs="+", metavar="SOURCE", help="a .py file")
    parser.add_argument("--module", help="the module the one source imports to (default: its name)")
    parser.add_argument("--provenance", metavar="SPEC", help="tier,repository=...,commit=...,license=...")
    parser.add_argument(
        "--starter",
        default=str(Path(__file__).resolve().parents[2] / "starter"),
        help="the starter directory the run begins from (the leaves the graphs call)",
    )
    parser.add_argument("--output", "-o", metavar="DELTA", help="where the image's delta is written")
    args = parser.parse_args(argv)
    from pymergetic.rxftk.core import tree

    image = tree.load(args.starter)
    prov = rximport.Provenance.parse(args.provenance) if args.provenance else None
    if prov is not None and prov.importer == "rximport":
        prov.importer = "rxpy"
    known: dict[str, str] = {}
    for source in args.sources:
        known.update(
            import_py_file(
                image, source, module=args.module or Path(source).stem,
                known=known, provenance=prov,
            )
        )
    if args.output:
        Path(args.output).write_text(image.export())
    sys.stdout.write(f"imported {', '.join(sorted(known))}\n")
    if prov is not None:
        sys.stdout.write(prov.report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
