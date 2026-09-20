"""Executable reference models for stdlib contracts, not an RXF interpreter."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import cmp_to_key


class ComparatorRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class IteratorToken:
    collection_id: int
    generation: int
    index: int = 0


class GenerationVector[T]:
    def __init__(self, collection_id: int, values: Iterable[T] = ()):
        self.collection_id = collection_id
        self.generation = 0
        self._values = list(values)

    def iterator(self) -> IteratorToken:
        return IteratorToken(self.collection_id, self.generation)

    def next(self, token: IteratorToken) -> tuple[T | None, IteratorToken]:
        if (
            token.collection_id != self.collection_id
            or token.generation != self.generation
        ):
            raise ValueError("iterator invalidated by collection mutation")
        if token.index == len(self._values):
            return None, token
        return self._values[token.index], IteratorToken(
            token.collection_id, token.generation, token.index + 1
        )

    def push(self, value: T) -> None:
        self._values.append(value)
        self.generation += 1


class CanonicalMap[K, V]:
    """Canonical iteration/serialization independent of insertion order."""

    def __init__(self, key_bytes: Callable[[K], bytes]):
        self._key_bytes = key_bytes
        self._values: dict[K, V] = {}
        self.generation = 0

    def set(self, key: K, value: V) -> None:
        self._values[key] = value
        self.generation += 1

    def items(self) -> tuple[tuple[K, V], ...]:
        return tuple(
            sorted(self._values.items(), key=lambda item: self._key_bytes(item[0]))
        )

    def serialize(self, value_bytes: Callable[[V], bytes]) -> bytes:
        parts = []
        for key, value in self.items():
            encoded_key, encoded_value = self._key_bytes(key), value_bytes(value)
            parts.append(len(encoded_key).to_bytes(8, "little") + encoded_key)
            parts.append(len(encoded_value).to_bytes(8, "little") + encoded_value)
        return b"".join(parts)


def stable_sort[T](values: list[T], compare: Callable[[T, T], int]) -> None:
    """Publish sorted output only after every comparator call succeeds."""
    prepared = sorted(values, key=cmp_to_key(compare))
    values[:] = prepared


def utf8_scalar_boundaries(payload: bytes) -> tuple[int, ...]:
    text = payload.decode("utf-8", "strict")
    result = [0]
    offset = 0
    for scalar in text:
        offset += len(scalar.encode("utf-8"))
        result.append(offset)
    return tuple(result)


def checked_utf8_slice(payload: bytes, start: int, end: int) -> str:
    if start < 0 or end < start or end > len(payload):
        raise ValueError("UTF-8 slice outside byte extent")
    boundaries = utf8_scalar_boundaries(payload)
    if start not in boundaries or end not in boundaries:
        raise ValueError("UTF-8 slice is not on scalar boundaries")
    return payload[start:end].decode("utf-8")


RECEIPT_EXPECTED = b"Receipt total: 570\n"


def build_receipt_reference(items: list[tuple[str, int]]) -> bytes:
    """Reference result for the starter semantic receipt workflow."""
    ordered = sorted(items, key=lambda item: item[0].encode("utf-8"))
    total = sum(value for _name, value in ordered)
    payload = f"Receipt total: {total}\n".encode()
    utf8_scalar_boundaries(payload)
    return payload
