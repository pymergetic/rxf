"""The host's door for the console's modifications acts.

The page never packs a delta: bytes are compiled here, on the host, by the one
compiler there is (``pymergetic.rxftk.live.physical``). An act is a small JSON
description of a change (rename an object, change a value, add an entry); this
module replays the acts the tab already committed on a model of the seat's
image, performs the new act on a copy, compiles the difference and bakes in the
base digest the SEAT answered -- the word the seat checks before it applies
(``live.h``'s CHECK_BASE), so a delta compiled for another image is refused by
the image itself and never applied.

The model is the image the seat boots: the starter plus the console's pump
program (``tools/native/boot_image.py``), as ``programs.filled`` leaves it. Paths
are the identity across images (docs/19), so the delta is the same whichever
arena layout the seat's earlier commits left behind.

``POST /compile`` (``web/serve.py``) is the face; the answer is the delta's
bytes, its SHA-256 (the seat's STAGE_COMMIT checks it) in a header.
"""

from __future__ import annotations

import struct
import sys
import threading
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _path in (ROOT / "tools" / "native", ROOT / "src"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import boot_image
import programs

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image
from pymergetic.rxftk.live import physical

ACTS = ("rename", "set", "add")
VALUE_TYPE = "pymergetic.rxf.application.discount"  # a uint32_t leaf: the template of a new entry

_lock = threading.Lock()
_base: bytes | None = None


class ActError(ValueError):
    """An act that cannot be performed on the image: the reason is the message."""


def base_bytes() -> bytes:
    """The seat's image as a model (built once: loading the starter is slow)."""
    global _base
    with _lock:
        if _base is None:
            image = tree.load(boot_image.STARTER)
            parent = image.handle(boot_image.PROGRAM_PARENT)
            image.add(parent, boot_image.PROGRAM_NAME, payload=boot_image.pump_program())
            _base = programs.filled(image).to_bytes()
        return _base


def model() -> Image:
    return Image.from_bytes(base_bytes())


def _handle(image: Image, path: object) -> int:
    if not isinstance(path, str) or not path:
        raise ActError("an act names an object by its path")
    found = image.resolve(path)
    if found is None:
        raise ActError(f"no object at {path}")
    return found


def _name(value: object) -> str:
    if not isinstance(value, str) or not value or "." in value or "/" in value:
        raise ActError("a name is one non-empty segment without dots")
    return value


def _free(image: Image, parent: int, name: object) -> None:
    """The name must be one nothing under `parent` carries yet."""
    taken = f"{image.path(parent)}.{_name(name)}"
    if image.resolve(taken) is not None:
        raise ActError(f"an object is already there: {taken}")


def _payload(value: object, size: int) -> bytes:
    """A value as the little-endian bytes of an object `size` bytes wide."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActError("a value is an integer")
    if not 0 <= value < 1 << (8 * size):
        raise ActError(f"the value does not fit {size} bytes")
    return value.to_bytes(size, "little")


def perform(image: Image, act: dict[str, Any]) -> str:
    """One act on `image`; the path of the object it leaves behind."""
    kind = act.get("act")
    if kind == "set":
        handle = _handle(image, act.get("path"))
        old = image.get(handle)
        if image.children(handle):
            raise ActError(f"{old['path']} has members: only a value is written")
        if not old["payload"]:
            raise ActError(f"{old['path']} carries no value")
        image.set_payload(handle, _payload(act.get("value"), len(old["payload"])))
        return str(old["path"])
    if kind == "add":
        parent = _handle(image, act.get("parent"))
        _free(image, parent, act.get("name"))
        template = image.get(_handle(image, VALUE_TYPE))
        handle = image.add(
            parent,
            _name(act.get("name")),
            template["kind"],
            template["type"],
            _payload(act.get("value", 0), len(template["payload"])),
            [],
            template["state"],
            template["owner_kind"],
        )
        return image.path(handle)
    if kind == "rename":
        handle = _handle(image, act.get("path"))
        old = image.get(handle)
        if image.children(handle):
            raise ActError(f"{old['path']} has members: only a leaf is renamed")
        if old["refs"]:
            raise ActError(f"{old['path']} carries references a rename would have to move")
        parent = old["parent"]
        name = _name(act.get("name"))
        _free(image, parent, name)
        image.remove(handle)
        made = image.add(
            parent,
            name,
            old["kind"],
            old["type"],
            old["payload"],
            [],
            old["state"],
            old["owner_kind"],
        )
        return image.path(made)
    raise ActError(f"no act {kind!r}: the acts are {', '.join(ACTS)}")


def performed(image: Image, act: dict[str, Any]) -> str:
    """`perform`, with the model's own refusals (a name already taken, a handle
    that is gone) said as acts' refusals: the page shows the reason."""
    try:
        return perform(image, act)
    except ActError:
        raise
    except (KeyError, RuntimeError, ValueError, OverflowError, TypeError) as error:
        raise ActError(f"the image refuses the act: {error}") from None


def compile_delta(request: dict[str, Any]) -> bytes:
    """The delta of `request["act"]`, after the acts of `request["history"]`.

    `request["base"]` is the digest the seat answered just now (hex): it is
    baked into the delta as the base the seat must be at to take it.
    """
    history = request.get("history", [])
    act = request.get("act")
    base = request.get("base")
    if not isinstance(history, list) or not isinstance(act, dict):
        raise ActError("a request is {base, history: [act...], act}")
    if not isinstance(base, str) or len(base) != 64:
        raise ActError("base is the seat's digest as 64 hex digits")
    try:
        digest = bytes.fromhex(base)
    except ValueError:
        raise ActError("base is the seat's digest as 64 hex digits") from None
    before = model()
    for earlier in history:
        if not isinstance(earlier, dict):
            raise ActError("a history entry is an act")
        performed(before, earlier)
    after = Image.from_bytes(before.to_bytes())
    performed(after, act)
    try:
        compiled = physical.compile(before, after, check_base=False)
    except ValueError as error:
        raise ActError(str(error)) from None
    if not compiled.creates + compiled.sets + compiled.deletes:
        raise ActError("the act changes nothing")
    data = bytearray(compiled.data)
    flags = struct.unpack_from("<I", data, 16)[0] | physical.CHECK_BASE
    struct.pack_into("<I", data, 16, flags)
    data[24:56] = digest
    return bytes(data)
