"""Sized primitive validators for canonical RXF wire values."""

from __future__ import annotations

import math
import struct
from typing import Any, ClassVar

from pydantic_core import core_schema as _core


class RXFObject:
    """Organisational root for every RXF type."""


class Primitive(RXFObject):
    """Base for fixed-width primitive validators; not a numeric subclass."""

    __width__: ClassVar[int] = 0
    __wire_format__: ClassVar[str]

    @classmethod
    def _validate(cls, value: object) -> object:
        raise NotImplementedError

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: Any
    ) -> _core.CoreSchema:
        return _core.no_info_plain_validator_function(cls._validate)


class Integer(Primitive):
    """Base for fixed-width integer validators."""

    __signed__: ClassVar[bool] = False
    _value: int

    def __init__(self, value: int = 0):
        object.__setattr__(self, "_value", type(self)._validate(value))

    def __int__(self) -> int:
        return self._value

    def __index__(self) -> int:
        return self._value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Integer):
            return self._value == other._value
        if isinstance(other, int) and not isinstance(other, bool):
            return self._value == other
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)

    @classmethod
    def _validate(cls, value: object) -> int:
        if isinstance(value, cls):
            return value._value
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"expected int or {cls.__name__}, got {type(value).__name__}"
            )
        lo, hi = cls.__lo__(), cls.__hi__()
        if not lo <= value <= hi:
            raise ValueError(f"{value} out of range [{lo}, {hi}] for {cls.__name__}")
        return value

    @classmethod
    def min_value(cls) -> int:
        return -(1 << (cls.__width__ * 8 - 1)) if cls.__signed__ else 0

    @classmethod
    def max_value(cls) -> int:
        bits = cls.__width__ * 8
        return (1 << (bits - 1)) - 1 if cls.__signed__ else (1 << bits) - 1

    @classmethod
    def __lo__(cls) -> int:
        return cls.min_value()

    @classmethod
    def __hi__(cls) -> int:
        return cls.max_value()

    @property
    def value(self) -> int:
        return self._value


class Unsigned(Integer):
    __signed__ = False


class Signed(Integer):
    __signed__ = True


class uint8_t(Unsigned):
    __width__, __wire_format__ = 1, "B"


class uint16_t(Unsigned):
    __width__, __wire_format__ = 2, "H"


class uint32_t(Unsigned):
    __width__, __wire_format__ = 4, "I"


class uint64_t(Unsigned):
    __width__, __wire_format__ = 8, "Q"


class int8_t(Signed):
    __width__, __wire_format__ = 1, "b"


class int16_t(Signed):
    __width__, __wire_format__ = 2, "h"


class int32_t(Signed):
    __width__, __wire_format__ = 4, "i"


class int64_t(Signed):
    __width__, __wire_format__ = 8, "q"


class Floating(Primitive):
    """Base for finite IEEE-754 floating-point validators."""

    _value: float

    def __init__(self, value: float = 0.0):
        object.__setattr__(self, "_value", type(self)._validate(value))

    def __float__(self) -> float:
        return self._value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._value!r})"

    @classmethod
    def _validate(cls, value: object) -> float:
        if isinstance(value, cls):
            return value._value
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(
                f"expected int, float, or {cls.__name__}, got {type(value).__name__}"
            )
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"non-finite value is invalid for {cls.__name__}")
        try:
            packed = struct.pack(f"<{cls.__wire_format__}", result)
        except OverflowError as error:
            raise ValueError(f"{result} out of range for {cls.__name__}") from error
        canonical = struct.unpack(f"<{cls.__wire_format__}", packed)[0]
        if not math.isfinite(canonical):
            raise ValueError(f"{result} out of range for {cls.__name__}")
        return canonical

    @property
    def value(self) -> float:
        return self._value


class float32_t(Floating):
    """Validator for the semantic RXF primitive ``float`` (binary32)."""

    __width__, __wire_format__ = 4, "f"


class float64_t(Floating):
    """Validator for the semantic RXF primitive ``double`` (binary64)."""

    __width__, __wire_format__ = 8, "d"
