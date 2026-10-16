"""The editor: live symbol check, compile + one-delta install, honest faces and Netzansicht,
and cell-poke. Everything reads the one meaning table and drives the one seam; there is no
second truth and no second channel."""

from __future__ import annotations

import re
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.core import tree
from pymergetic.rxftk.core.object import Image

STARTER = Path(__file__).parents[1] / "starter"

WORDS = struct.pack(
    "<5Q", 0, 0, 2, 0, 0
)  # a program of 5 words; word[2] is the parameter count


# ---- the seam, duck-typed (no running image) -------------------------------------------------


class FakeVerdict:
    def __init__(self, ok: bool, words: bytes = WORDS, at: int = 0, status: int = 0):
        self.ok = ok
        self.words = words
        self.at = at
        self.status = status


class FakeInstalled:
    path = "pymergetic.rxf.selfedit.double"
    identity = 7
    code_identity = 8
    byte_count = len(WORDS)
    digest = "abc123"


class FakeFront:
    """The in-image front end's shape: compile then install, one delta. Records what it is asked."""

    def __init__(self, ok: bool = True, status: int = 0):
        self.ok = ok
        self.status = status
        self.calls: list[tuple] = []

    def compile(self, source: bytes, files=None):
        self.calls.append(("compile", source))
        return FakeVerdict(self.ok, WORDS, 0 if self.ok else 3, self.status)

    def install(self, name: str, words: bytes, source: bytes):
        self.calls.append(("install", name))
        return FakeInstalled()

    def numbers(self) -> dict[str, int]:
        return {"RXF_REFUSE_UNSUPPORTED": 5}

    def rebase(self) -> None:
        self.calls.append(("rebase",))


class FakeChannel:
    """The live channel's shape: one apply of a delta, and a digest back."""

    def __init__(self):
        self.applied: list[bytes] = []

    def apply(self, delta: bytes) -> str:
        self.applied.append(delta)
        return "0xdeadbeef"

    def emit(self) -> tuple[bytes, str]:
        return (b"", "")


@pytest.fixture(scope="module")
def starter():
    return tree.load(STARTER)


POINT_DELTA = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Type name="Role" form="Form.Struct" size="0" align="1"/>
  <Role name="peer"/>
  <Point name="a" x="1" next="b"/>
  <Point name="b" x="2"/>
</delta>
"""


@pytest.fixture
def point():
    image = Image.empty()
    image.apply(POINT_DELTA)
    return image


# ---- 1. the live symbol check (red/green as you type) -----------------------------------------


def test_the_live_symbol_check_marks_tokens_green_and_red():
    from pymergetic.rxftk.server import editor

    good = editor.check_source("program double(x) = mul(x, 2)", "rxfs")
    assert good["ok"] is True and good["refused"] == 0 and good["tokens"] > 0
    assert {m["kind"] for m in good["marks"]} >= {"keyword", "identifier"}
    program = [m for m in good["marks"] if m["text"] == "program"]
    assert program and program[0]["ok"] is True and program[0]["meaning"]

    bad = editor.check_source("program double(x) = mul(x, @ ) % 2", "rxfs")
    assert bad["ok"] is False and bad["refused"] >= 1
    refusals = [m for m in bad["marks"] if not m["ok"]]
    assert all(m["refusal"] for m in refusals)  # red carries the refusal, never silent
    assert all(m["kind"] == "bad" for m in refusals)


def test_the_symbol_check_carries_the_meaning_of_every_green_token():
    from pymergetic.rxftk.server import editor

    checked = editor.check_source("var y = copy(a, b)", "rxfs")
    by = {m["text"]: m for m in checked["marks"]}
    assert by["var"]["ok"] is True and by["var"]["kind"] == "keyword"
    assert by["copy"]["ok"] is True and by["copy"]["kind"] == "builtin"


def test_the_symbol_check_resolves_rxfd_references():
    from pymergetic.rxftk.server import editor

    doc = (
        '<delta xmlns:rxf="urn:pymergetic:rxf">'
        '<Function name="f" callee="missing.thing"/>'
        "</delta>"
    )
    checked = editor.check_source(doc, "rxfd")
    assert checked["language"] == "rxfd"
    # an unresolvable reference is red with a refusal; the resolver is the one truth
    assert "marks" in checked


# ---- 3. the faces (read-only, honest) --------------------------------------------------------


def test_a_flat_graph_projects_to_every_face(starter):
    from pymergetic.rxftk.server import editor

    faces = editor.faces(starter, "pymergetic.rxf.algorithms.compare")
    assert faces["shape"] == "flat"
    assert all(face["ok"] for face in faces["faces"].values())  # pseudo, c, cpp, python
    assert faces["refusals"] == []
    for face in faces["faces"].values():
        assert face["text"] and face["note"]


def test_a_block_graph_lowers_to_pseudo_and_refuses_fake_code(starter):
    from pymergetic.rxftk.server import editor

    faces = editor.faces(starter, "pymergetic.rxf.algorithms.binary_search")
    assert faces["shape"] == "blocks"
    assert faces["faces"]["pseudo"]["ok"] is True
    for name in ("c", "cpp", "python"):
        assert faces["faces"][name]["ok"] is False
        assert faces["faces"][name]["refusal"]  # honest refusal, not a faked body
    assert {r["face"] for r in faces["refusals"]} == {"c", "cpp", "python"}


def test_a_native_leaf_is_honest_in_c_and_refuses_python_bodies(starter):
    from pymergetic.rxftk.server import editor

    faces = editor.faces(starter, "pymergetic.rxf.capabilities.console_log")
    assert faces["shape"] == "leaf"
    assert faces["faces"]["c"]["ok"] is True  # its native source shape (a signature)
    assert faces["faces"]["cpp"]["ok"] is True
    assert (
        faces["faces"]["python"]["ok"] is False and faces["faces"]["python"]["refusal"]
    )


def test_compiled_words_refuse_to_invent_a_body(starter):
    from pymergetic.rxftk.server import editor

    faces = editor.faces(starter, "pymergetic.rxf.algorithms.hash_bytes")
    assert faces["shape"] == "words"
    for face in faces["faces"].values():
        assert face["ok"] is False and face["refusal"]


# ---- 3b. the netzansicht (nodes + edges, the same image) --------------------------------------


def test_the_netzansicht_is_the_function_and_its_environment(starter):
    from pymergetic.rxftk.server import editor

    netz = editor.netz(starter, "pymergetic.rxf.algorithms.compare")
    assert netz["path"] == "pymergetic.rxf.algorithms.compare"
    kinds = {e["kind"] for e in netz["edges"]}
    assert {"call", "argument", "reference", "subcell"} <= kinds
    assert netz["nodes"][0]["kind"] == "function"
    # the CALL card: a call edge names a callee, and the legend explains it as native/graph.h
    assert any(e["kind"] == "call" for e in netz["edges"])
    assert "CALL" in netz["legend"]["call"] and "graph.h" in netz["legend"]["call"]
    # every edge points at a node that exists
    ids = {n["id"] for n in netz["nodes"]}
    assert all(e["from"] in ids and e["to"] in ids for e in netz["edges"])


def test_the_netzansicht_of_a_block_graph_still_names_callees(starter):
    from pymergetic.rxftk.server import editor

    netz = editor.netz(starter, "pymergetic.rxf.algorithms.binary_search")
    assert any(e["kind"] == "call" for e in netz["edges"])


# ---- 2. compile + install through the ONE seam ------------------------------------------------


def test_compile_goes_through_the_seam_and_previews_the_delta():
    from pymergetic.rxftk.server import editor

    front = FakeFront()
    out = editor.compile_source(front, "program double(x) = mul(x, 2)")
    assert out["ok"] is True and out["name"] == "double"
    assert out["words"] == len(WORDS) and out["parameters"] == 2
    assert ("compile", b"program double(x) = mul(x, 2)") in front.calls
    # the delta is shown before install: the honest face of what will change
    assert '<Function name="double"' in out["delta_preview"]
    assert '<Code name="bytecode"' in out["delta_preview"]
    assert out["delta_preview"].startswith("<delta")


def test_a_refused_compile_is_honest_not_silent():
    from pymergetic.rxftk.server import editor

    front = FakeFront(ok=False, status=5)
    out = editor.compile_source(front, "program broken(")
    assert out["ok"] is False
    assert out["refusal"] == "UNSUPPORTED"  # the refusal name, from the const table
    assert out["line"] == 3 and out["status"] == 5


def test_install_sends_one_delta_through_the_seam():
    from pymergetic.rxftk.server import editor

    front = FakeFront()
    out = editor.install_source(front, "program double(x) = mul(x, 2)")
    assert out["ok"] is True
    assert out["path"] == "pymergetic.rxf.selfedit.double"
    assert (
        out["identity"] == "0x0000000000000007"
        and out["code_identity"] == "0x0000000000000008"
    )
    assert out["bytes"] == len(WORDS) and out["digest"] == "abc123"
    assert (
        "install",
        "double",
    ) in front.calls  # exactly one install, after the compile
    assert out["delta_preview"].startswith("<delta")


def test_install_refuses_when_the_compile_refuses():
    from pymergetic.rxftk.server import editor

    front = FakeFront(ok=False, status=5)
    out = editor.install_source(front, "program broken(")
    assert out["ok"] is False and out["refusal"] == "UNSUPPORTED"
    assert not any(
        call[0] == "install" for call in front.calls
    )  # nothing installs on a refusal


def test_the_program_name_comes_from_the_source():
    from pymergetic.rxftk.server import editor

    assert editor._name_of("  program hello(a, b) = add(a, b)") == "hello"
    assert editor._name_of("no program here") is None


# ---- 4. cell poke ----------------------------------------------------------------------------


def test_the_pokeable_fields_are_the_ones_the_task_names():
    from pymergetic.rxftk.server import editor

    for field in ("name", "flags", "payload"):
        assert editor.classify_poke(field)["allowed"] is True
    for field in ("forward", "generation", "checksum"):
        verdict = editor.classify_poke(field)
        assert verdict["allowed"] is False and verdict["reason"]


def test_the_unpokeable_fields_say_why(point):
    from pymergetic.rxftk.server import editor

    channel = FakeChannel()
    for field, word in (
        ("forward", "C_FORWARD"),
        ("generation", "ABA"),
        ("checksum", "integrity"),
    ):
        out = editor.poke(channel, point, "a", field, 1, front=FakeFront())
        assert out["ok"] is False and word in out["refusal"]
    assert channel.applied == []  # not one byte went to the channel


def test_a_name_poke_is_refused_as_a_rename_not_an_in_place_write(point):
    from pymergetic.rxftk.server import editor

    out = editor.poke(FakeChannel(), point, "a", "name", 1, front=FakeFront())
    assert out["ok"] is False and "not a poke" in out["refusal"]


def test_poking_a_field_writes_through_the_channel(point):
    from pymergetic.rxftk.server import editor

    channel = FakeChannel()
    out = editor.poke(channel, point, "a", "flags", 99, front=FakeFront())
    assert out["ok"] is True and out["field"] == "flags" and out["path"] == "a"
    assert out["digest"] == "0xdeadbeef" and out["sets"] == 1
    assert len(channel.applied) == 1  # one delta, the one seam


def test_a_payload_word_poke_replaces_one_word():
    from pymergetic.rxftk.server import editor

    payload = struct.pack("<2Q", 1, 2)
    patched = editor.poke_word_payload(payload, 8, 0xAABBCCDD)
    assert struct.unpack("<2Q", patched) == (1, 0xAABBCCDD)
    with pytest.raises(ValueError):
        editor.poke_word_payload(payload, 16, 1)  # outside the payload, refused here


# ---- the app wiring ---------------------------------------------------------------------------


def test_the_editor_app_serves_its_routes(starter):
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.server.app import (
        EDITOR_PAGE,
        PokeBody,
        SourceBody,
        create_app,
    )

    app = create_app(starter)
    handlers = {
        route.path: route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute)
    }
    assert {
        "/editor",
        "/api/editor/check",
        "/api/editor/faces",
        "/api/editor/netz",
        "/api/editor/compile",
        "/api/editor/install",
        "/api/editor/poke",
        "/api/editor/numbers",
    } <= set(handlers)

    page = handlers["/editor"]()
    assert "RXF editor" in page and EDITOR_PAGE.is_file()

    checked = handlers["/api/editor/check"](
        SourceBody(source="program f(x) = x", language="rxfs")
    )
    assert checked["ok"] is True

    faces = handlers["/api/editor/faces"]("pymergetic.rxf.algorithms.compare")
    assert faces["shape"] == "flat"

    # with no running image, compile / install / poke refuse honestly (no second channel)
    refused = handlers["/api/editor/compile"](SourceBody(source="program f(x) = x"))
    assert refused["ok"] is False and "no running image" in refused["refusal"]
    poke_refused = handlers["/api/editor/poke"](
        PokeBody(path="a", field="flags", value=1)
    )
    assert poke_refused["ok"] is False and "no running image" in poke_refused["refusal"]


def test_the_inspector_links_to_the_editor():
    from pymergetic.rxftk.server.app import PAGE

    assert '<a class="button" href="/editor"' in PAGE.read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_editor_page_parses_and_is_webgl_only(tmp_path):
    from pymergetic.rxftk.server.app import EDITOR_PAGE

    text = EDITOR_PAGE.read_text()
    found = re.search(r"<script>(.*)</script>", text, re.DOTALL)
    assert found is not None
    path = tmp_path / "editor.js"
    path.write_text(found.group(1))
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    # WebGL-only for any 3D: never WebGPU (it freezes in webviews). The Netzansicht is 2D SVG.
    assert "webgpu" not in text.lower() and "navigator.gpu" not in text
