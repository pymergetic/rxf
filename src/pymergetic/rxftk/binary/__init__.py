"""The starter as a multi-platform binary: is it complete, and does it agree with itself.

A *target* is a ``RuntimeTarget`` object of the image. A *native* function has
its machine code (or wasm body) for every target, each with the ABI that says
how to call it. A *semantic* function is composed of calls: everything it calls
must exist for every target, so the native functions are the closed set the
rest stands on. It has one ``Code`` of format ``Bytecode`` (the program the
composer made of its graph, the same for every target) and the interpreter that
runs it, a native function that has to exist for every target too. Operations
that call nothing (``ConstructOption``, ``ReadTag``, ``ReturnValue``, ...) are
structure the composer turns into instructions of the program; the report lists
them.

The checks read the image through the C++ core and know the starter's types by
name (``Function``, ``Code``, ``ABISignature``, ``RuntimeTarget``, ...).
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from pymergetic.rxftk.core.object import Image

NATIVE = "Native"
SEMANTIC = "Semantic"
SELF_CONTAINED = {"Capability", "Control"}
# The code format an architecture's targets use.
FORMAT_OF_ARCHITECTURE = {"Wasm32": "Wasm"}
DEFAULT_FORMAT = "Machine"
ABI_OF_ARCHITECTURE = {"Wasm32": "Wasm", "X8664": "Sysv", "Aarch64": "Aapcs64"}


def word(text: str | None) -> str:
    """The last segment of a written path: `...FunctionImplementation.Native` is `Native`."""
    return (text or "").rsplit(".", 1)[-1]


def digest_of(data: str) -> str:
    """The content digest of a hex payload: the sha256 of the bytes it writes."""
    try:
        body = bytes.fromhex(data)
    except ValueError:
        return ""
    return hashlib.sha256(body).hexdigest()


@dataclass
class Binary:
    image: Image
    issues: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])

    def __post_init__(self) -> None:
        self.by_type: dict[str, list[int]] = defaultdict(list)
        self.fields: dict[int, dict[str, str]] = {}
        for handle in range(self.image.handle_count):
            if not self.image.live(handle):
                continue
            name = self.image.type_name(handle)
            if name:
                self.by_type[name].append(handle)
        self._available: dict[int, bool | None] = {}

    # ---- reading --------------------------------------------------------------------------------

    def of(self, handle: int) -> dict[str, str]:
        if handle not in self.fields:
            self.fields[handle] = dict(self.image.fields(handle))
        return self.fields[handle]

    def path(self, handle: int) -> str:
        return self.image.path(handle)

    def children(self, handle: int, type_name: str) -> list[int]:
        return [
            c
            for c in self.image.children(handle)
            if self.image.type_name(c) == type_name
        ]

    def problem(self, handle: int | str, message: str) -> None:
        path = handle if isinstance(handle, str) else self.path(handle)
        self.issues.append((path, message))

    def code_data(self, code: int, values: dict[str, str]) -> str | None:
        """The bytes of a Code: the payload of the CodeBlob its ``body`` names.

        An empty payload is built, not missing: the starter stores the source
        and the recipe of the built code (Wave 4b steps 2 and 3) and the builder
        fills the bytes in, to the pin the blob carries. The length the pin
        names is the length the code promises, stored or built.
        """
        text = values.get("body", "")
        body = self.image.resolve(text) if text else None
        if body is None or self.image.type_name(body) != "CodeBlob":
            return None
        blob = self.of(body)
        data = blob.get("data", "")
        pinned = 2 * int(blob.get("byte_count", "0"))
        if not data:
            if not blob.get("content_digest") or not int(blob.get("byte_count", "0")):
                self.problem(code, "it is built but does not say what to build")
        else:
            if len(data) != pinned:
                self.problem(code, "byte_count is not the length of the data")
            if blob.get("content_digest") != digest_of(data):
                self.problem(code, "the blob is not what its digest says")
        if pinned != 2 * int(values.get("byte_count", "0")):
            self.problem(code, "byte_count is not the length of the data")
        return data

    def role_target(self, handle: int, role: str) -> int | None:
        for target, via, _, _, _ in self.image.get(handle)["refs"]:
            if via is not None and word(self.path(via)) == role:
                return target
        return None

    # ---- targets --------------------------------------------------------------------------------

    def targets(self) -> dict[str, dict[str, str]]:
        """``{target path: {"architecture", "abi"}}`` as the words the kinds use."""
        found: dict[str, dict[str, str]] = {}
        for handle in self.by_type["RuntimeTarget"]:
            values = self.of(handle)
            architecture = self.image.resolve(values.get("architecture", ""))
            abi = self.image.resolve(values.get("abi", ""))
            if architecture is None or abi is None:
                self.problem(handle, "a target needs an architecture and an ABI")
                continue
            found[self.path(handle)] = {
                "architecture": word(self.of(architecture).get("kind")),
                "abi": word(self.of(abi).get("kind")),
            }
        return found

    # ---- natives --------------------------------------------------------------------------------

    def implementation(self, function: int) -> str:
        return word(self.of(function).get("implementation"))

    def check_native(self, function: int, targets: dict[str, dict[str, str]]) -> bool:
        """Code and ABI for every target, consistent with the function. True when complete."""
        values = self.of(function)
        codes: dict[str, int] = {}
        for code in self.children(function, "Code"):
            target = self.of(code).get("runtime_target", "")
            if target in codes:
                self.problem(code, f"a second code for {word(target)}")
            codes[target] = code
        signatures = {
            self.of(s).get("target", ""): s
            for s in self.children(function, "ABISignature")
        }
        complete = True
        for target, info in targets.items():
            code = codes.get(target)
            if code is None:
                self.problem(function, f"no code for {word(target)}")
                complete = False
                continue
            wanted = FORMAT_OF_ARCHITECTURE.get(info["architecture"], DEFAULT_FORMAT)
            cv = self.of(code)
            if word(cv.get("format")) != wanted:
                self.problem(
                    code,
                    f"the format is {word(cv.get('format'))}, {word(target)} wants {wanted}",
                )
            data = self.code_data(code, cv)
            if data is None:
                self.problem(code, "it says no blob for its bytes")
            if cv.get("semantic_digest") != values.get("semantic_digest"):
                self.problem(code, "the semantic digest is not the function's")
            if cv.get("signature") != values.get("signature"):
                self.problem(code, "the signature is not the function's")
            provenance = self.role_target(code, "Provenance")
            if (
                provenance is None
                or self.image.type_name(provenance) != "CompilerProvenance"
            ):
                self.problem(code, "it does not say which compiler recipe made it")
            abi = signatures.get(target)
            linked = self.role_target(code, "AbiSignature")
            if abi is None or linked != abi:
                self.problem(code, f"it is not tied to the ABI of {word(target)}")
                complete = False
            elif word(self.of(abi).get("kind")) != info["abi"]:
                self.problem(abi, f"the ABI kind is not the one of {word(target)}")
        for target in set(codes) - set(targets):
            self.problem(codes[target], "a code for something that is not a target")
        for target, abi in signatures.items():
            if target not in targets:
                self.problem(abi, "an ABI for something that is not a target")
            if int(self.of(abi).get("location_count", "0")) != len(
                self.children(abi, "ABIValueLocation")
            ):
                self.problem(abi, "location_count is not the number of locations")
        return complete

    # ---- composed functions ---------------------------------------------------------------------

    def check_bytecode(self, function: int, native_ok: dict[int, bool]) -> None:
        """A semantic function has one program, and the interpreter for it."""
        values = self.of(function)
        codes = self.children(function, "Code")
        if len(codes) != 1:
            self.problem(
                function, f"it is semantic and has {len(codes)} Code objects, not one"
            )
            return
        code = codes[0]
        cv = self.of(code)
        if word(cv.get("format")) != "Bytecode":
            self.problem(code, f"the format is {word(cv.get('format'))}, not Bytecode")
        if cv.get("runtime_target"):
            self.problem(code, "a program is the same for every target and names none")
        data = cv.get("data", "")
        if data:
            if (
                len(data) != 2 * int(cv.get("byte_count", "0"))
                or len(data) % 16
            ):
                self.problem(
                    code, "byte_count is not the length of the data, in whole words"
                )
        elif not int(cv.get("byte_count", "0")):
            # built from its source and its recipe (Wave 4b step 2): the pin is
            # the byte count, and the builder fills the bytes in.
            self.problem(
                code, "byte_count is not the length of the data, in whole words"
            )
        if cv.get("semantic_digest") != values.get("semantic_digest"):
            self.problem(code, "the semantic digest is not the function's")
        if cv.get("signature") != values.get("signature"):
            self.problem(code, "the signature is not the function's")
        interpreter = self.role_target(code, "Interpreter")
        if interpreter is None or self.image.type_name(interpreter) != "Function":
            self.problem(code, "it does not name its interpreter")
        elif not native_ok.get(interpreter, False):
            self.problem(
                code, f"{self.path(interpreter)} is not complete for every target"
            )

    # ---- closure --------------------------------------------------------------------------------

    def available(self, function: int, native_ok: dict[int, bool]) -> bool:
        """Whether a function can run on every target: native complete, or composed of such."""
        if function in self._available:
            return self._available[function] is not False
        self._available[function] = None  # being decided: a cycle does not fail it
        kind = self.implementation(function)
        if kind == NATIVE:
            ok = native_ok.get(function, False)
        elif kind in SELF_CONTAINED:
            ok = True
        elif kind == SEMANTIC:
            ok = all(self.available(c, native_ok) for c in self.callees(function))
        else:
            ok = False
        self._available[function] = ok
        return ok

    def callees(self, function: int) -> list[int]:
        found = self._callees.get(function)
        return found if found is not None else []

    def index_calls(self) -> None:
        self._callees: dict[int, list[int]] = defaultdict(list)
        for type_name in ("SemanticOperation", "Call"):
            for handle in self.by_type[type_name]:
                text = self.of(handle).get("callee")
                callee = self.image.resolve(text) if text else None
                if callee is None:
                    continue
                owner = self.owner(handle)
                if owner is not None and callee not in self._callees[owner]:
                    self._callees[owner].append(callee)

    def owner(self, handle: int) -> int | None:
        parent = self.image.get(handle)["parent"]
        while parent != 0xFFFFFFFFFFFFFFFF:
            if self.image.type_name(parent) == "Function":
                return parent
            parent = self.image.get(parent)["parent"]
        return None

    # ---- the report -----------------------------------------------------------------------------

    def check(self) -> dict[str, Any]:
        targets = self.targets()
        self.index_calls()
        native_ok: dict[int, bool] = {}
        counts: dict[str, int] = defaultdict(int)
        for function in self.by_type["Function"]:
            kind = self.implementation(function) or "Abstract"
            counts[kind] += 1
            if kind == NATIVE:
                native_ok[function] = self.check_native(function, targets)
        for handle in self.by_type["Signature"]:
            for count, child in (
                ("parameter_count", "Parameter"),
                ("result_count", "Result"),
            ):
                if int(self.of(handle).get(count, "0")) != len(
                    self.children(handle, child)
                ):
                    self.problem(
                        handle, f"{count} is not the number of {child} objects"
                    )
        for function in self.by_type["Function"]:
            if self.implementation(function) != SEMANTIC:
                continue
            self.check_bytecode(function, native_ok)
            for callee in self.callees(function):
                if callee != function and not self.available(callee, native_ok):
                    self.problem(
                        function,
                        f"it calls {self.path(callee)}, which is not complete for every target",
                    )
        structural: dict[str, int] = defaultdict(int)
        for handle in self.by_type["SemanticOperation"]:
            values = self.of(handle)
            if not values.get("callee"):
                structural[word(values.get("opcode"))] += 1
        return {
            "ok": not self.issues,
            "targets": sorted(word(t) for t in targets),
            "functions": [
                {"implementation": k, "count": n} for k, n in sorted(counts.items())
            ],
            # Operations that call nothing: the composer has to make them, no snippet does.
            "structural": [
                {"opcode": op, "count": n} for op, n in sorted(structural.items())
            ],
            "issues": [{"path": p, "message": m} for p, m in sorted(self.issues)],
        }


def check(image: Image) -> dict[str, Any]:
    return Binary(image).check()
