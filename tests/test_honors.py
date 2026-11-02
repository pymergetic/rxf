"""The honors face (docs/36): who made this system, and the ledger that stays open.

The founding core is answered like the image answers it (`who`, `about`), the one
write the ledger takes is `append`, and the laws are enforced in the face: the
Genesis is read-only as data and the ledger only ever grows.
"""

import io
from typing import Any

import arena_export
import pytest

from pymergetic.rxftk.console.session import Console
from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.arena import (
    HANDLE32_NONE,
    MODULE_SIZE,
    OBJ_BLOB,
    OBJ_MODULE,
    OBJ_SPEC,
    OFLAG_RO,
    ObjectArena,
)
from pymergetic.rxftk.honors import Honors, HonorsError
from pymergetic.rxftk.honors import cli as honors_cli

from .snippets import STARTER

WROTE = "wrote"
TERRY = "terry_davis"
ROUVEN = "rouven_raudzus"


@pytest.fixture(scope="module")
def world():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def honors(world):
    return Honors(arena_export.export_world(world))


@pytest.fixture
def bare():
    """A small image laid out as the writer lays one out (docs/32 2, docs/36 3):
    the honors tree and room for relation tables, nothing else."""
    arena = ObjectArena.create(1 << 20)
    arena.bootstrap("boot", 32)
    arena.journal_commit()  # the skeleton is ground, not history
    sys_ = arena.find(arena.root_handle, "sys")
    assert sys_ != HANDLE32_NONE, "bootstrap lays out sys/"
    arena.new_object(sys_, "relations", OBJ_MODULE, MODULE_SIZE)
    root = arena.new_object(arena.root_handle, "honors", OBJ_MODULE, MODULE_SIZE)
    groups = {
        group: arena.new_object(root, group, OBJ_MODULE, MODULE_SIZE)
        for group in ("roles", "people", "works", "ledger")
    }
    role = arena.new_object(groups["roles"], WROTE, OBJ_SPEC, 16)
    arena.set_payload(role, b"who wrote it")
    person = arena.new_object(groups["people"], TERRY, OBJ_BLOB, 24)
    arena.set_payload(person, b"wrote TempleOS alone")
    for handle in (groups["roles"], groups["people"], groups["works"], role, person):
        arena.set_flags(handle, OFLAG_RO)  # the flag states the intent as data
    return Honors(arena)


def down(honors: Honors, *path: str) -> int:
    node = honors.arena.root_handle
    for segment in path:
        node = honors.arena.find(node, segment)
        assert node != HANDLE32_NONE, f"no {segment!r} under {'/'.join(path)}"
    return node


def table_of(honors: Honors, key: str) -> bytes:
    relations = down(honors, "sys", "relations")
    handle = honors.arena.find(relations, key)
    assert handle != HANDLE32_NONE, f"no relation table {key!r}"
    return honors.arena.payload(handle)


class StubFront:
    """The image the terminal talks to here answers no programs; only its words."""

    def installed(self) -> list[Any]:
        return []


def console_writes(honors: Honors | None, *words: str) -> str:
    out = io.StringIO()
    stub: Any = StubFront()
    Console(stub, out, honors=honors).repl([f"{word}\n" for word in words])
    return out.getvalue()


def test_who_answers_the_founding_core(honors):
    report = honors.who()
    assert [member["name"] for member in report["roles"]] == [
        "embodies",
        "inspired",
        "invented",
        WROTE,
    ]
    assert TERRY in [member["name"] for member in report["people"]]
    assert [member["name"] for member in report["works"]] == [
        "blake2s",
        "curve25519",
        "micropython",
        "templeos",
    ]
    assert report["ledger"] == []


def test_the_author_of_this_system_is_on_it(honors):
    found = honors.about(ROUVEN)
    assert found["kind"] == "person"
    assert "Built this system" in found["text"]
    assert found["refs"] == [
        {"role": "invented", "target": "pymergetic", "index": 0},
        {"role": WROTE, "target": "pymergetic", "index": 1},
    ]


def test_about_resolves_refs_and_vouches(honors):
    found = honors.about(TERRY)
    assert found["kind"] == "person"
    assert found["refs"] == [
        {"role": WROTE, "target": "honors/works/templeos", "index": 0},
        {"role": "inspired", "target": "pymergetic", "index": 1},
    ]
    assert found["vouched"] == []


def test_about_refuses_a_name_the_core_does_not_carry(honors):
    with pytest.raises(HonorsError, match="no person or work named 'nobody'"):
        honors.about("nobody")


def test_the_ledger_only_ever_grows(bare):
    first = bare.append("Thank you for TempleOS.", about=TERRY, role=WROTE, by="rouven")
    second = bare.append("And for the dream.", about=TERRY, role=WROTE, by="rouven")
    assert (first, second) == (
        "honors/ledger/entry_000001",
        "honors/ledger/entry_000002",
    )
    assert bare.arena.payload(down(bare, *first.split("/"))).rstrip(b"\0") == (
        b"by rouven\nThank you for TempleOS."
    )
    assert bare.about(TERRY)["vouched"] == ["entry_000001", "entry_000002"]
    with pytest.raises(HonorsError, match="the ledger never rewrites"):
        bare.append("nothing", about=TERRY, role=WROTE, name="entry_000001")


def test_an_entry_ref_names_its_person_and_its_role(bare):
    entry = bare.append("Thank you.", about=TERRY, role=WROTE, by="rouven", name="vouch")
    target, role, kind, binding, index = arena_export.REF_RECORD.unpack(
        table_of(bare, "honors.ledger.vouch")
    )
    assert (target, role, kind, binding, index) == (
        down(bare, "honors", "people", TERRY),
        down(bare, "honors", "roles", WROTE),
        0,
        0,
        0,
    )
    written = down(bare, *entry.split("/"))
    assert bare.arena.load(written)[2] & OFLAG_RO
    assert bare.verify() == {"ok": True, "problems": []}


def test_the_console_words_answer_and_append(bare):
    shown = console_writes(
        bare,
        ":honors",
        f":honor {TERRY} {WROTE} Thank you for TempleOS.",
        f":about {TERRY}",
    )
    assert "people" in shown and TERRY in shown
    assert f"appended honors/ledger/entry_000001 about {TERRY} with {WROTE}" in shown
    assert "vouched by entry_000001" in shown


def test_the_console_says_the_truth_about_an_image_without_honors():
    assert "this image carries no honors" in console_writes(None, ":honors")


def test_verify_names_a_ref_that_lands_outside_the_image(bare):
    relations = down(bare, "sys", "relations")
    table = bare.arena.new_object(relations, f"honors.people.{TERRY}", OBJ_SPEC, 24)
    bare.arena.set_payload(
        table,
        arena_export.REF_RECORD.pack(
            0x7FFFFFFF, down(bare, "honors", "roles", WROTE), 0, 0, 0
        ),
    )
    report = bare.verify()
    assert not report["ok"]
    assert "honors/people/terry_davis: a ref whose target is gone" in report["problems"]


def test_verify_names_a_genesis_without_the_flag(bare):
    people = down(bare, "honors", "people")
    bare.arena.new_object(people, "stallman", OBJ_BLOB, 8)
    report = bare.verify()
    assert "honors/people/stallman: the Genesis carries no read-only flag" in report[
        "problems"
    ]


def test_the_cli_appends_to_the_image_file(honors, tmp_path):
    image = tmp_path / "image"
    image.write_bytes(honors.arena.to_bytes())
    outcome = honors_cli.append(
        str(image), "Thank you.", about=ROUVEN, role=WROTE, by="rouven"
    )
    assert outcome == {"ok": True, "entry": "honors/ledger/entry_000001"}
    again = Honors.attach(image.read_bytes())
    assert [member["name"] for member in again.who()["ledger"]] == ["entry_000001"]
    assert again.about(ROUVEN)["vouched"] == ["entry_000001"]
    assert honors_cli.verify(str(image)) == {"ok": True, "problems": []}
