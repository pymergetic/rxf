"""Hostile input: nothing the core is given may crash it or half-apply."""

import os
import random

import pytest

from pymergetic.rxftk.core.arena import ArenaError
from pymergetic.rxftk.core.delta import DeltaError
from pymergetic.rxftk.core.object import Image

Rejected = (DeltaError, ArenaError)
# RXF_FUZZ=20 runs twenty times as many cases (the sanitizer build does this in CI).
ROUNDS = int(os.environ.get("RXF_FUZZ", "1"))

DELTA = """<?xml version="1.0" encoding="UTF-8"?>
<delta>
  <Type name="Point" form="Form.Struct" size="24" align="8" flags="TypeFlags.Variable">
    <Field name="x" value_type="uint64_t"/>
    <Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
    <Field name="n" value_type="uint32_t" offset="16"/>
  </Type>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="uses"/>
  <Point name="a" x="1" next="b" n="7" rxf:data="00ff"/>
  <Point name="b" x="2">
    <rxf:ref role="uses" index="2" kind="RefKind.Data" target="a"/>
  </Point>
  <rxf:set path="b" x="9"/>
  <rxf:entry path="a"/>
</delta>
"""

ALPHABET = list('<>/="&;#_ \n\tabcxyz0123456789-.') + [
    "<Point ",
    "<rxf:ref ",
    "</delta>",
    "name=",
    "&amp;",
    "&#",
    "path=",
]


def mutate(rng, text):
    out = list(text)
    for _ in range(rng.randint(1, 6)):
        kind = rng.randrange(4)
        at = rng.randrange(len(out) + 1)
        if kind == 0 and out:
            del out[min(at, len(out) - 1) : min(at, len(out) - 1) + rng.randint(1, 8)]
        elif kind == 1:
            out.insert(at, rng.choice(ALPHABET))
        elif kind == 2 and out:
            out[min(at, len(out) - 1)] = rng.choice(ALPHABET)
        elif out:
            start = rng.randrange(len(out))
            out[at:at] = out[start : start + rng.randint(1, 40)]
    return "".join(out)


def test_mutated_deltas_are_applied_or_refused_whole():
    rng = random.Random(6 + ROUNDS)
    base = Image.empty()
    applied = refused = 0
    for _ in range(1500 * ROUNDS):
        text = mutate(rng, DELTA)
        image = Image.empty()
        before = image.digest()
        try:
            image.apply(text)
        except Rejected:
            refused += 1
            assert image.digest() == before and image.export() == Image.empty().export()
            continue
        applied += 1
        # Whatever was accepted links and is its own canonical delta.
        assert image.check() == [], text
        again = Image.empty()
        again.apply(image.export())
        assert again.digest() == image.digest()
    assert applied > 20 and refused > 200
    assert base.check() == []


def test_truncated_and_flipped_images_are_refused_or_verify():
    image = Image.empty()
    image.apply(DELTA)
    data = image.compact().to_bytes()
    rng = random.Random(7 + ROUNDS)
    for cut in list(range(0, 400, 7)) + [len(data) - 1, len(data) - 16]:
        try:
            Image.from_bytes(data[:cut])
        except Rejected:
            pass
    for _ in range(1500 * ROUNDS):
        flipped = bytearray(data)
        for _ in range(rng.randint(1, 4)):
            flipped[rng.randrange(len(flipped))] ^= 1 << rng.randrange(8)
        try:
            loaded = Image.from_bytes(bytes(flipped))
        except Rejected:
            continue
        # An image the loader accepts may be read without a fault.
        loaded.check()
        loaded.export() if not loaded.check() else None
        for handle in range(min(loaded.handle_count, 64)):
            if loaded.live(handle):
                loaded.get(handle)


@pytest.mark.parametrize("depth", [200, 300])
def test_deep_nesting_is_bounded(depth):
    text = "<delta>" + '<Point name="p">' * depth + "</Point>" * depth + "</delta>"
    with pytest.raises(Rejected):
        Image.empty().apply(text)


def test_huge_and_odd_attribute_values():
    for value in ["9" * 400, "-1", "0x10", "1e3", "", " 1", "\u00e9", "\x00"]:
        for field in ("x", "n", "next", "data"):
            text = (
                f'<delta><Type name="Point" form="Form.Struct" size="24" align="8" flags="TypeFlags.Variable"><Field name="x" value_type="uint64_t"/>'
                f'<Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>'
                f'<Field name="n" value_type="uint32_t" offset="16"/></Type><Point name="p" {field}="{value}"/></delta>'
            )
            image = Image.empty()
            try:
                image.apply(text)
            except Rejected:
                continue
            assert image.check() == []
