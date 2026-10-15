"""The editor of the inspector: source in, honest faces and refusals out, one seam.

This is the brain behind the EDITOR surface of the inspector. It never owns a second truth and
never invents a second channel:

- **symbol check** delegates to the one meaning table and the resolver (``tools/highlight``), so a
  token is red or green exactly as the table says;
- **faces** (Pseudo, C, C++, Python) are read-only projections of a function/graph already in the
  image, and they refuse to fake what cannot be projected (the C face of a leaf is its native
  source *shape*, never invented C; a graph lowers to pseudo-code);
- **netzansicht** is a network view of a function and its environment as nodes and edges, read from
  the same image the inspector reads — including the graph-to-graph ``CALL`` card of
  ``native/graph.h``;
- **compile + install** drives the one seam of a running image (``live.front.Front``): the edited
  source goes through the in-image front end and one delta installs the Function. The delta is
  shown before it is sent. Nothing here compiles and nothing here sends a second way.
- **cell poke** writes one cell field through the live channel (``live.client.Client``) and refuses
  honestly what is not pokeable (forward, generation, checksum). The front end's mirror of the
  image is rebased around it (``live.front.Front.rebase``), so the poke is part of the state the
  next compile or install is staged against.

The web wiring lives in ``server/app.py``; the front-end logic is pure Python so it can be tested
without a browser and without a running image.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pymergetic.rxf.core.object import Image

# The one meaning table (tools/highlight/meaning.json) and its resolver: reusing them keeps a single
# symbol truth. They import at runtime via the package root on sys.path (pytest's pythonpath); the
# workspace pyrightconfig only reaches tools/native, so *pyright* cannot resolve them from under
# src/. Suppressed here, not duplicated. Suggest adding `packages/rxf` to the root pyrightconfig's
# extraPaths to resolve them properly and drop these two ignores.
from tools.highlight import check as symbol_check
from tools.highlight import resolve
from tools.highlight.meaning import Table

MODULE = "pymergetic.rxf.selfedit"  # where the console installs (live/front.py)
BYTECODE = "bytecode"


# ---- the seam the editor drives (duck-typed so tests need no running image) -------------------


class Front(Protocol):
    """The in-image front end (``live.front.Front``): compile then install, one delta, and
    ``rebase`` — the mirror of the image a delta is staged against, for a change that lands
    outside its sends."""

    def compile(
        self, source: bytes, files: Mapping[str, bytes] | None = None
    ) -> Any: ...

    def install(self, name: str, words: bytes, source: bytes) -> Any: ...

    def numbers(self) -> dict[str, int]: ...

    def rebase(self) -> None: ...


class Channel(Protocol):
    """The live channel (``live.client.Client``): reads and one ``apply`` of a delta."""

    def emit(self) -> tuple[bytes, str]: ...

    def apply(self, delta: bytes) -> str: ...


# ---- 1. the live symbol check -----------------------------------------------------------------


def check_source(
    source: str,
    language: str,
    *,
    known: resolve.World | None = None,
    default_parent: str = "",
    doc_source: str = "",
    table: Table | None = None,
) -> dict[str, Any]:
    """Red/green every token of the edited source. `language` is `rxfs` (the program form) or
    `rxfd` (the delta form). `.rxfd` references resolve through the resolver; `.rxfs` tokens are
    validated against the meaning table. No second vocabulary."""
    return symbol_check.check(
        source,
        language,
        known=known,
        default_parent=default_parent,
        source=doc_source,
        table=table,
    )


# ---- 3. the faces ----------------------------------------------------------------------------


@dataclass(frozen=True)
class _Call:
    name: str
    callee: str | None
    arguments: tuple[tuple[str, str], ...]  # (parameter, value) in order
    result_type: str | None


@dataclass(frozen=True)
class _Value:
    name: str
    target: str | None
    value_type: str | None


@dataclass
class _Fn:
    path: str
    name: str
    implementation: str
    return_type: str
    parameters: tuple[str, ...]
    ops: list[Any]  # _Call | _Value, in the order the graph declares them
    body: str | None  # the op the `body` field names (the value that is returned)
    shape: str  # flat | blocks | leaf | words
    notes: list[str]

    @property
    def calls(self) -> list[_Call]:
        return [op for op in self.ops if isinstance(op, _Call)]

    @property
    def values(self) -> list[_Value]:
        return [op for op in self.ops if isinstance(op, _Value)]


def _short(image: Image, path: str | None) -> str:
    """The last component of a path, for a readable face."""
    return (path or "").rsplit(".", 1)[-1]


def _function(image: Image, handle: int) -> _Fn:
    """One Function of the image read into the small shape the faces project."""
    got = image.get(handle)
    fields = dict(image.fields(handle))
    path, name = got["path"], got["name"]
    implementation = fields.get("implementation", "")
    notes: list[str] = []

    signature_return = "void"
    parameters: list[str] = []
    for child in image.children(handle):
        if image.type_name(child) != "Signature":
            continue
        sig = dict(image.fields(child))
        signature_return = sig.get("return_type", "void")
        for slot in image.children(child):
            slot_type = image.type_name(slot)
            if slot_type in ("Parameter", "Argument"):
                parameters.append(dict(image.fields(slot)).get("value_type", "u64"))
            elif slot_type == "Result":
                signature_return = dict(image.fields(slot)).get(
                    "value_type", signature_return
                )

    ops: list[Any] = []
    shape = "flat"
    has_graph = False
    for child in image.children(handle):
        kind = image.type_name(child)
        child_fields = dict(image.fields(child))
        if kind == "Call":
            arguments = []
            for arg in image.children(child):
                if image.type_name(arg) == "Argument":
                    af = dict(image.fields(arg))
                    arguments.append((af.get("parameter", ""), af.get("value", "")))
            op = _Call(
                str(child_fields.get("name") or image.get(child)["name"]),
                child_fields.get("callee"),
                tuple(arguments),
                child_fields.get("result_type"),
            )
            ops.append(op)
        elif kind == "ValueReference":
            op = _Value(
                str(child_fields.get("name") or image.get(child)["name"]),
                child_fields.get("target"),
                child_fields.get("value_type"),
            )
            ops.append(op)
        elif kind == "SemanticGraph":
            has_graph = True

    if has_graph:
        shape = "blocks"
    elif not ops:
        # No graph children: a native leaf or a compiled (bytecode) Function. The faces lower a
        # graph; the body of these is not a graph in the image.
        code_format = None
        for child in image.children(handle):
            if image.type_name(child) == "Code":
                code_format = dict(image.fields(child)).get("format", "")
        shape = "words" if code_format else "leaf"
        if shape == "words":
            notes.append(
                "this Function carries compiled words (its Code); its graph is not in the image, "
                "so the faces show the signature and refuse to invent a body"
            )
        else:
            notes.append(
                "this Function is a native leaf: its C face is its native source shape (a "
                "signature), and its body lives in native/, not in the image"
            )

    return _Fn(
        path=path,
        name=name,
        implementation=implementation,
        return_type=signature_return,
        parameters=tuple(parameters),
        ops=ops,
        body=fields.get("body"),
        shape=shape,
        notes=notes,
    )


def _signature(fn: _Fn, style: str) -> str:
    params = ", ".join(
        f"{ptype if style != 'python' else 'a'} a{index}"
        if style != "python"
        else f"a{index}"
        for index, ptype in enumerate(fn.parameters)
    )
    if style == "python":
        return (
            f"def {fn.name}({', '.join(f'a{i}' for i in range(len(fn.parameters)))}):"
        )
    if style == "c" or style == "cpp":
        ret = fn.return_type
        return f"{ret} {fn.name}({params})"
    return f"function {fn.name}({params}) -> {fn.return_type}"


def _flat_lines(fn: _Fn, style: str) -> list[str]:
    """A flat graph (a straight list of calls and moves) lowered to one face, in the order the
    graph declares them. Every line is a real call or move of the graph; nothing is invented."""
    lines: list[str] = []
    for op in fn.ops:
        if isinstance(op, _Call):
            args = ", ".join(_short_image(value) for _, value in op.arguments)
            target = _short_image(op.callee)
            if style == "python":
                lines.append(f"    {op.name} = {target}({args})")
            elif style == "pseudo":
                lines.append(f"    {op.name} = call {target}({args})")
            else:
                lines.append(
                    f"    {op.result_type or 'u64'} {op.name} = {target}({args});"
                )
        else:  # _Value
            if op.target is None:
                continue
            if style == "python" or style == "pseudo":
                lines.append(f"    {op.name} = {_short_image(op.target)}")
            else:
                lines.append(
                    f"    {op.value_type or 'u64'} {op.name} = {_short_image(op.target)};"
                )
    returned = (
        _short_image(fn.body) if fn.body else (fn.calls[-1].name if fn.calls else "0")
    )
    if style == "python" or style == "pseudo":
        lines.append(f"    return {returned}")
    else:
        lines.append(f"    return {returned};")
    return lines


def _short_image(path: str | None) -> str:
    return (path or "0").rsplit(".", 1)[-1]


def _project(fn: _Fn, style: str) -> dict[str, Any]:
    """One face of a function/graph: the honest projection, or a refusal of what cannot be faked."""
    refusal: str | None = None
    if fn.shape == "flat":
        header = _signature(fn, style)
        if style == "python":
            body = [header, *_flat_lines(fn, "python")]
        elif style == "pseudo":
            body = [header, *_flat_lines(fn, "pseudo")]
        else:
            body = [header + " {", *_flat_lines(fn, style), "}"]
        text = "\n".join(body)
        ok = True
        note = "a straight-line graph of calls, lowered to this face"
    elif fn.shape == "blocks":
        # A block graph has control flow (Branch/SwitchTag/...). The pseudo face lowers it; a code
        # face refuses rather than fakes the control flow.
        if style == "pseudo":
            text = _pseudo_blocks(fn)
            ok = True
            note = "the block graph, lowered to pseudo-code"
        else:
            text = _signature(fn, style) + (" {" if style != "python" else ":")
            ok = False
            refusal = (
                f"this graph has block control flow; its {style} face would fake the "
                "Branch/SwitchTag terminators. The Pseudo face shows the real structure."
            )
            note = "signature only"
    elif fn.shape == "leaf":
        # A native leaf: its C face is its native source shape (the signature). The body is native.
        if style in ("c", "cpp"):
            text = _signature(fn, style) + ";"
            ok = True
            note = "the native source shape of this leaf (its C signature); the body is native code"
        else:
            text = _signature(fn, style)
            ok = False
            refusal = f"a native leaf has no {style} source in the image; its C face is the honest one"
            note = "signature only"
    else:  # words
        text = _signature(fn, style) + (";" if style in ("c", "cpp") else "")
        ok = False
        refusal = (
            "this Function carries compiled words, not a graph; the faces lower a graph and will "
            "not invent a body from raw words"
        )
        note = "signature only"
    return {"ok": ok, "text": text, "note": note, "refusal": refusal}


def _pseudo_blocks(fn: _Fn) -> str:
    """The pseudo face of a block graph: the signature and the honest note that the block lowering
    reads the SemanticGraph of the image. Kept honest: no fake body is written here."""
    return "\n".join(
        [
            _signature(fn, "pseudo"),
            "    # block graph (SemanticGraph of the image):",
            "    #   see the network view for its blocks, terminators and callees (the CALL card)",
        ]
    )


def faces(image: Image, path: str, table: Table | None = None) -> dict[str, Any]:
    """The read-only faces of one function/graph: Pseudo, C, C++, Python, each an honest
    projection or a refusal. `refusals` collects what no face will fake."""
    handle = _resolve(image, path)
    fn = _function(image, handle)
    projected = {
        "pseudo": _project(fn, "pseudo"),
        "c": _project(fn, "c"),
        "cpp": _project(fn, "cpp"),
        "python": _project(fn, "python"),
    }
    refusals = [
        {"face": face, "refusal": face_result["refusal"]}
        for face, face_result in projected.items()
        if face_result["refusal"]
    ]
    return {
        "path": fn.path,
        "name": fn.name,
        "shape": fn.shape,
        "implementation": fn.implementation,
        "signature": {"return_type": fn.return_type, "parameters": list(fn.parameters)},
        "faces": projected,
        "refusals": refusals,
        "notes": fn.notes,
    }


def _resolve(image: Image, path: str) -> int:
    if path in ("", "."):
        raise KeyError(path)
    handle = image.resolve(path)
    if handle is None:
        raise KeyError(path)
    return handle


# ---- 3b. the netzansicht ----------------------------------------------------------------------


def netz(image: Image, path: str) -> dict[str, Any]:
    """The network view of a function and its environment as nodes and edges, read from the same
    image the inspector reads. Edges carry their meaning: a ``call`` edge is the graph-to-graph
    ``CALL`` card (``native/graph.h``) and names its callee; ``argument`` edges bind an operand to
    a value; ``reference`` and ``subcell`` edges are the rest of the environment."""
    handle = _resolve(image, path)
    got = image.get(handle)
    fn = _function(image, handle)

    nodes: list[dict[str, Any]] = [
        {
            "id": got["path"],
            "label": got["name"],
            "kind": "function",
            "role": "the function",
        }
    ]
    edges: list[dict[str, Any]] = []
    seen: set[str] = {got["path"]}

    def node(node_path: str | None, kind: str, role: str) -> str:
        assert node_path is not None
        if node_path not in seen:
            seen.add(node_path)
            nodes.append(
                {
                    "id": node_path,
                    "label": _short_image(node_path),
                    "kind": kind,
                    "role": role,
                }
            )
        return node_path

    # the signature and code the function points at
    for target, role, _, _, _ in got["refs"]:
        if target is None or target == 0xFFFFFFFFFFFFFFFF:
            continue
        try:
            target_path = image.path(target)
        except KeyError:
            continue
        node(target_path, "reference", role and image.path(role) or "reference")
        edges.append(
            {
                "from": got["path"],
                "to": target_path,
                "kind": "reference",
                "label": "ref",
            }
        )

    # the environment: the ops (calls, values), their callees (CALL card) and their operands
    for call in fn.calls:
        call_node = node(f"{got['path']}.{call.name}", "operation", "call")
        edges.append(
            {
                "from": got["path"],
                "to": call_node,
                "kind": "subcell",
                "label": call.name,
            }
        )
        if call.callee:
            callee_node = node(call.callee, "callee", "callee (CALL card)")
            edges.append(
                {
                    "from": call_node,
                    "to": callee_node,
                    "kind": "call",
                    "label": "CALL callee",
                }
            )
        for parameter, value in call.arguments:
            if value:
                value_node = node(value, "value", "operand")
                edges.append(
                    {
                        "from": call_node,
                        "to": value_node,
                        "kind": "argument",
                        "label": _short_image(parameter) or "operand",
                    }
                )
    for value in fn.values:
        value_node = node(f"{got['path']}.{value.name}", "value", "value")
        edges.append(
            {
                "from": got["path"],
                "to": value_node,
                "kind": "subcell",
                "label": value.name,
            }
        )
        if value.target:
            target_node = node(value.target, "value", "bound to")
            edges.append(
                {
                    "from": value_node,
                    "to": target_node,
                    "kind": "reference",
                    "label": "target",
                }
            )

    return {
        "path": got["path"],
        "nodes": nodes,
        "edges": edges,
        "legend": {
            "call": "the graph-to-graph CALL card (native/graph.h): this operation calls that callee",
            "argument": "an operand bound to a value",
            "reference": "a reference the object holds",
            "subcell": "a child (an op, a value) of the function",
        },
    }


# ---- 2. compile + install through the ONE seam -------------------------------------------------


def install_delta_preview(
    module: str, name: str, words: bytes, source: bytes, parameters: int
) -> str:
    """The honest face of what one install will change: the delta that the seam sends, rendered as
    the XML the host turns into the physical form. This is a *preview* for the user; the real
    install goes through ``Front.install`` on the same seam and sends the same shape."""
    path = f"{module}.{name}"
    digest = hashlib.sha256(source).hexdigest()
    lines = [
        '<delta xmlns:rxf="urn:pymergetic:rxf">',
        (
            f'  <Function name="{name}" rxf:parent="{module}" '
            f'implementation="pymergetic.rxf.kinds.FunctionImplementation.Semantic" '
            f'signature="{path}.signature" semantic_digest="{digest}">'
        ),
        (
            f'    <Signature name="signature" return_type="uint64_t" '
            f'parameter_count="{parameters}" result_count="1">'
        ),
    ]
    for index in range(parameters):
        lines.append(
            f'      <Parameter name="argument[{index}]" value_type="uint64_t"/>'
        )
    lines.append('      <Result name="value" value_type="uint64_t"/>')
    lines.append("    </Signature>")
    lines.append(
        f'    <Code name="{BYTECODE}" owner_function="{path}" '
        f'format="pymergetic.rxf.kinds.CodeFormat.Bytecode" byte_count="{len(words)}" '
        f'rxf:data="{words.hex()}">'
    )
    lines.append(
        '      <rxf:ref role="pymergetic.rxf.roles.call.Interpreter" '
        'kind="RefKind.Data" target="pymergetic.rxf.foundation.graph.run"/>'
    )
    lines.append("    </Code>")
    lines.append("  </Function>")
    lines.append("</delta>")
    return "\n".join(lines)


def compile_source(
    front: Front,
    source: str,
    name: str | None = None,
    files: Mapping[str, bytes] | None = None,
    module: str = MODULE,
) -> dict[str, Any]:
    """One edited program through the in-image front end (the seam): the words it writes, or the
    line it stopped at. Nothing is installed here; `install_source` sends the one delta."""
    wanted = name or _name_of(source)
    if wanted is None:
        return {
            "ok": False,
            "message": "the source names no program (`program NAME(...)`)",
        }
    data = source.encode()
    verdict = front.compile(data, files)
    if not getattr(verdict, "ok", False):
        return {
            "ok": False,
            "refusal": _refusal_name(front, verdict),
            "line": getattr(verdict, "at", 0),
            "status": getattr(verdict, "status", 0),
            "message": f"compile refused at line {getattr(verdict, 'at', 0)}",
        }
    words = getattr(verdict, "words", b"")
    parameters = _parameters(words)
    return {
        "ok": True,
        "name": wanted,
        "words": len(words),
        "parameters": parameters,
        "delta_preview": install_delta_preview(module, wanted, words, data, parameters),
    }


def install_source(
    front: Front,
    source: str,
    name: str | None = None,
    files: Mapping[str, bytes] | None = None,
    module: str = MODULE,
) -> dict[str, Any]:
    """Compile and then install through the one seam: one delta installs the Function. The delta
    is the same shape `install_delta_preview` shows. A refusal is returned honestly."""
    compiled = compile_source(front, source, name, files, module)
    if not compiled.get("ok"):
        return compiled
    wanted = compiled["name"]
    data = source.encode()
    words = front.compile(data, files).words
    installed = front.install(wanted, words, data)
    return {
        "ok": True,
        "path": getattr(installed, "path", f"{module}.{wanted}"),
        "identity": f"0x{getattr(installed, 'identity', 0):016x}",
        "code_identity": f"0x{getattr(installed, 'code_identity', 0):016x}",
        "bytes": getattr(installed, "byte_count", len(words)),
        "digest": getattr(installed, "digest", ""),
        "delta_preview": compiled["delta_preview"],
    }


def _name_of(source: str) -> str | None:
    import re

    found = re.findall(
        r"(?m)^[ \t]*program[ \t]+([A-Za-z_][A-Za-z0-9_]*)[ \t]*\(", source
    )
    return found[0] if found else None


def _parameters(words: bytes) -> int:
    if len(words) >= 24 and len(words) % 8 == 0:
        return struct.unpack_from("<Q", words, 16)[0]
    return 0


def _refusal_name(front: Front, verdict: Any) -> str:
    status = getattr(verdict, "status", 0)
    table = front.numbers() if hasattr(front, "numbers") else {}
    for namevalue, value in table.items():
        if value == status and namevalue.startswith("RXF_REFUSE_"):
            return namevalue[len("RXF_REFUSE_") :]
    return f"status {status}"


# ---- 4. cell poke -----------------------------------------------------------------------------


# The cell fields, and which of them a poke may write. A poke is honest about what it will not do:
# `forward` is the compactor's C_FORWARD record, `generation` is the allocator's ABA word, and
# `checksum` is the integrity word of a run — none of them are a user's to poke.
POKEABLE = ("name", "flags", "payload")
REFUSED_POKE = {
    "forward": "C_FORWARD is written by the compactor when a cell slides; it is a record of a move, "
    "never a poke",
    "generation": "the generation word is the allocator's (a slot's lives); poking it would fake an "
    "ABA history",
    "checksum": "the checksum is the integrity word written only by the run that owns the cell; a "
    "poke would tear it",
}


def classify_poke(field: str) -> dict[str, Any]:
    """Whether one cell field is pokeable, and why not when it is not."""
    if field in POKEABLE:
        return {"field": field, "allowed": True, "reason": None}
    if field in REFUSED_POKE:
        return {"field": field, "allowed": False, "reason": REFUSED_POKE[field]}
    return {
        "field": field,
        "allowed": False,
        "reason": f"{field!r} is not a cell field this editor knows; the pokeable fields are "
        f"{', '.join(POKEABLE)}",
    }


def poke_word_payload(payload: bytes, offset: int, value: int) -> bytes:
    """A scalar payload word poke: one 64-bit word of the payload replaced, everything else the
    same. An offset outside the payload is refused here."""
    if offset < 0 or offset + 8 > len(payload):
        raise ValueError(
            f"offset {offset} is outside a payload of {len(payload)} bytes"
        )
    patched = bytearray(payload)
    struct.pack_into("<Q", patched, offset, value & 0xFFFFFFFFFFFFFFFF)
    return bytes(patched)


def poke(
    channel: Channel,
    image: Image,
    path: str,
    field: str,
    value: Any,
    offset: int = 0,
    *,
    front: Front,
) -> dict[str, Any]:
    """Poke one cell field through the live channel (one delta, the seam). `payload` pokes a
    scalar word at `offset`; `name` and `flags` are honoured; `forward`/`generation`/`checksum`
    are refused. The write goes through `channel.apply` — the same seam every other change uses.

    The poke lands outside the front end's own sends (`live/front.py` `_send`), so `front` — the
    front end of the same image — is rebased here, right after the apply: the poke is part of
    the image state the next compile or install is staged against, and a poke that could skip
    the rebase would leave that compile refused a stale base. `front` is therefore not optional,
    and a refused poke (or one that never applied) rebases nothing: the mirror tracks the
    applies that land, and only those."""
    verdict = classify_poke(field)
    if not verdict["allowed"]:
        return {"ok": False, "field": field, "refusal": verdict["reason"]}
    handle = _resolve(image, path)
    before = Image.from_bytes(image.to_bytes())
    after = Image.from_bytes(image.to_bytes())
    got = after.get(handle)
    if field == "payload":
        payload = poke_word_payload(bytes(got["payload"]), int(offset), int(value))
        after.set_payload(handle, payload)
    elif field == "flags":
        after.set_state(handle, int(value))
    elif field == "name":
        # A name is the last component of the path; a poke of it is a rename, which the physical
        # form carries as the object's own name. Refuse here rather than fake a rename that the
        # channel has no operation for.
        return {
            "ok": False,
            "field": field,
            "refusal": "a name is an object's path; renaming it is a delete+create, not a poke. "
            "Edit the source and reinstall instead.",
        }
    from pymergetic.rxf.live import physical

    compiled = physical.compile(before, after)
    digest = channel.apply(compiled.data)
    front.rebase()  # the poke moved the image past the front end's mirror: track it here
    return {
        "ok": True,
        "field": field,
        "path": image.path(handle),
        "sets": compiled.sets,
        "creates": compiled.creates,
        "deletes": compiled.deletes,
        "digest": digest,
    }
