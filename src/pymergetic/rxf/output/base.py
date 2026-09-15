"""output/base.py — organisational base model + Struct wire base.

All output models inherit from BaseRXFModel, which is an RXFObject.
Struct extends BaseRXFModel with fixed-size wire serialisation.

Wire protocol:
    instance.to_wire()  -> bytes
    cls.from_wire(data, offset=0) -> instance
"""

import enum
import struct as _stdlib_struct
from typing import ClassVar, Self, get_args, get_origin, get_type_hints

from pydantic import BaseModel

from pymergetic.rxf.output.types import Primitive, RXFObject
from pymergetic.rxf.ty.align import align_up


def _is_bytes_type(fid) -> bool:
    tp = fid.annotation
    if tp is bytes:
        return True
    return get_origin(tp) is bytes


def _max_length(fid) -> int:
    for m in fid.metadata:
        ml = getattr(m, "max_length", None)
        if ml is not None:
            return ml
    return 0


def _primitive_type(annotation) -> type[Primitive] | None:
    origin = get_origin(annotation)
    if origin is int:
        return None
    if origin is not None:
        args = get_args(annotation)
        if args and args[0] in (int, float):
            for arg in args[1:]:
                if isinstance(arg, type) and issubclass(arg, Primitive):
                    return arg
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  BaseRXFModel
# ══════════════════════════════════════════════════════════════════════════════


class BaseRXFModel(RXFObject, BaseModel):
    """Root for all .rxf binary shape models.

    Subclasses MAY override these class-level constants:

        __align__       int   alignment boundary for this model's wire bytes.
                              Default: 8 (standard x86-64 struct alignment).

        __pad_after__   bool  if True (default), insert padding after writing
                              this model so the next object starts on __align__.
                              Set False for contiguous packed regions.
    """

    __align__: ClassVar[int] = 8
    __pad_after__: ClassVar[bool] = True


# ══════════════════════════════════════════════════════════════════════════════
#  Struct  — fixed-size wire-format base
# ══════════════════════════════════════════════════════════════════════════════


class Struct(BaseRXFModel):
    """Fixed-size wire-format struct.

    Fields packed in declaration order (Pydantic model_fields order).
    Each field: ``Annotated[int | float, PrimitiveSubclass]`` or fixed bytes.
    """

    @classmethod
    def _field_widths(cls) -> dict[str, int]:
        widths: dict[str, int] = {}
        raw_hints = get_type_hints(cls, include_extras=True)
        for name, fid in cls.model_fields.items():
            tp = fid.annotation
            raw = raw_hints.get(name)
            primitive = _primitive_type(raw) if raw is not None else None
            if primitive is not None:
                widths[name] = primitive.__width__
            elif tp is bytes or _is_bytes_type(fid):
                ml = _max_length(fid)
                if ml:
                    widths[name] = ml
                else:
                    raise TypeError(
                        f"bytes field {name!r} on {cls.__name__} needs max_length"
                    )
            elif isinstance(tp, type) and (
                issubclass(tp, enum.IntFlag) or issubclass(tp, enum.IntEnum)
            ):
                widths[name] = 4  # enums pack as u32
            else:
                # fallback: check raw hint for Primitive
                if raw is not None:
                    primitive = _primitive_type(raw)
                    if primitive is not None:
                        widths[name] = primitive.__width__
                        continue
                raise TypeError(
                    f"unsupported type {tp!r} for {name!r} on {cls.__name__}"
                )
        return widths

    @classmethod
    def body_size(cls) -> int:
        """Exact declared field width before trailing alignment."""
        return sum(cls._field_widths().values())

    @classmethod
    def padding_size(cls) -> int:
        """Trailing alignment bytes in the wire representation."""
        return cls.wire_size() - cls.body_size()

    @classmethod
    def wire_size(cls) -> int:
        raw = cls.body_size()
        return align_up(raw, cls.__align__) if cls.__pad_after__ else raw

    def to_wire(self) -> bytes:
        widths = self._field_widths()
        raw_hints = get_type_hints(type(self), include_extras=True)
        padded = self.wire_size()
        buf = bytearray(padded)
        offset = 0
        for name in type(self).model_fields:
            w = widths[name]
            val = getattr(self, name)
            if isinstance(val, bytes):
                buf[offset : offset + w] = val[:w].ljust(w, b"\x00")
            else:
                primitive = _primitive_type(raw_hints[name])
                if primitive is None:
                    _stdlib_struct.pack_into("<I", buf, offset, int(val))
                else:
                    _stdlib_struct.pack_into(
                        f"<{primitive.__wire_format__}", buf, offset, val
                    )
            offset += w
        return bytes(buf)

    @classmethod
    def from_wire(cls, data: bytes, offset: int = 0) -> Self:
        size = cls.wire_size()
        if offset < 0 or offset > len(data) or size > len(data) - offset:
            raise ValueError(
                f"{cls.__name__} wire bytes are out of bounds: "
                f"offset={offset}, size={size}, data_size={len(data)}"
            )
        widths = cls._field_widths()
        raw_hints = get_type_hints(cls, include_extras=True)
        values: dict[str, object] = {}
        off = offset
        for name, fid in cls.model_fields.items():
            w = widths[name]
            if _is_bytes_type(fid):
                values[name] = data[off : off + w]
            else:
                primitive = _primitive_type(raw_hints[name])
                fmt = primitive.__wire_format__ if primitive is not None else "I"
                values[name] = _stdlib_struct.unpack_from(f"<{fmt}", data, off)[0]
            off += w
        return cls(**values)
