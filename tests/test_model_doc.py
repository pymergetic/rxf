"""The Model documentation of the inspector (templates/model.html, served at /model).

Anti-drift: every byte offset, marker, and section reference the page documents is pinned here
against the real definitions — the constants read out of ``tests/host/smp_*.h`` and
``smp_sched.c`` by regex, the layout pins of ``core/ground.h``, and the S1..S11 headings of
``docs/20-EXECUTION-POLICY.md``. Changing the C (or the policy) breaks these tests until the
page is updated; the page may not invent a constant the C does not define.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pymergetic.rxftk.server.inspector import _model_links

ROOT = Path(__file__).parents[1]
HOST = Path(__file__).parent / "host"
POLICY = ROOT / "docs" / "20-EXECUTION-POLICY.md"
MODEL_PAGE = ROOT / "src" / "pymergetic" / "rxftk" / "server" / "templates" / "model.html"
GROUND = ROOT / "src" / "pymergetic" / "rxftk" / "core" / "ground.h"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def define(text: str, name: str) -> int:
    """The value of a `#define NAME 123u` (decimal or hex) in a header, comment allowed after."""
    found = re.search(
        rf"^#define\s+{re.escape(name)}\s+(0x[0-9A-Fa-f]+|\d+)[uU]?\b", text, re.MULTILINE
    )
    assert found, f"{name} is not a #define in the source"
    return int(found.group(1), 0)


def enumerator(text: str, name: str) -> int:
    """The value of a `NAME = 123,` enumerator in a header."""
    found = re.search(rf"\b{re.escape(name)}\s*=\s*(\d+)", text)
    assert found, f"{name} is not an enumerator in the source"
    return int(found.group(1), 0)


def source_constants() -> dict[str, int]:
    """Every documented constant, read out of the C that defines it."""
    arena = read(HOST / "smp_arena.h")
    sched = read(HOST / "smp_sched.h")
    mpmodel = read(HOST / "smp_mpmodel.h")
    witness = read(HOST / "smp_witness.h")
    sched_c = read(HOST / "smp_sched.c")
    arena_c = read(HOST / "smp_arena.c")
    ground = read(GROUND)
    policy = read(POLICY)

    expected: dict[str, int] = {}
    for name in (
        "RXF_SMP_CELL_BYTES",
        "RXF_SMP_C_TYPE",
        "RXF_SMP_C_PAYLOAD_SIZE",
        "RXF_SMP_C_GENERATION",
        "RXF_SMP_C_FLAGS",
        "RXF_SMP_C_FORWARD",
        "RXF_SMP_C_INDEX",
        "RXF_SMP_FLAG_PINNED",
        "RXF_SMP_CLAIM_MOVING",
    ):
        expected[name] = define(arena, name)
    for name in (
        "RXF_SMP_CELL_RUN",
        "RXF_SMP_CELL_DATA",
        "RXF_SMP_CELL_CHANNEL",
        "RXF_SMP_CELL_RING",
        "RXF_SMP_CELL_DELTA",
        "RXF_SMP_CELL_SCRATCH",
    ):
        expected[name] = enumerator(arena, name)
    for name in (
        "RXF_SMP_STATE_FREE",
        "RXF_SMP_STATE_READY",
        "RXF_SMP_STATE_RUNNING",
        "RXF_SMP_STATE_PARKED",
        "RXF_SMP_STATE_TERMINAL",
        "SMP_STEP_AGAIN",
        "SMP_STEP_PARK",
        "SMP_STEP_DONE",
        "SMP_STEP_SELFWAKE",
    ):
        expected[name] = enumerator(sched, name)
    expected["RXF_SMP_MAX_CARRIERS"] = define(sched, "RXF_SMP_MAX_CARRIERS")
    for name in ("SMP_MP_NONE", "SMP_MP_RAISE", "SMP_MP_SCHED"):
        expected[name] = define(mpmodel, name)
    expected["WIT_OUT_WORDS"] = define(witness, "WIT_OUT_WORDS")

    # the checksum of smp_sched.c: FNV-1a, one prime and one basis
    prime = re.search(r"\(h \^ b\[i\]\) \* (0x[0-9A-Fa-f]+)ULL", sched_c)
    basis = re.search(r"fnv\(&r->self,.*?(0x[0-9A-Fa-f]+)ULL\)", sched_c, re.DOTALL)
    assert prime and basis, "smp_sched.c no longer spells the FNV-1a checksum"
    expected["SMP_FNV_PRIME"] = int(prime.group(1), 0)
    expected["SMP_FNV_BASIS"] = int(basis.group(1), 0)

    # the poison byte of the vacated tail (smp_arena.c compaction finish)
    poison = re.search(r"memset\(a->base \+ a->frontier, (0x[0-9A-Fa-f]+),", arena_c)
    assert poison, "smp_arena.c no longer poisons the vacated tail"
    expected["RXF_SMP_POISON"] = int(poison.group(1), 0)

    # the handle word: index | generation << shift
    shift = re.search(r"\(uint32_t\)\(generation\) << (\d+)\)", arena)
    assert shift, "smp_arena.h no longer spells the handle as index | generation"
    expected["RXF_SMP_HANDLE_SHIFT"] = int(shift.group(1), 0)

    # the layout pins of the header the inspector reads (ground.h, RXFB v6)
    pins = {
        field: (int(offset), int(size))
        for field, offset, size in re.findall(
            r"PYMERGETIC_RXF_PIN\(pymergetic_rxf_cell,\s*(\w+),\s*(\d+),\s*(\d+)\)", ground
        )
    }
    for field, name in (
        ("parent", "RXFB_C_PARENT"),
        ("name", "RXFB_C_NAME"),
        ("ref_count", "RXFB_C_REFCOUNT"),
        ("reserved", "RXFB_C_RESERVED"),
    ):
        assert field in pins, f"ground.h no longer pins pymergetic_rxf_cell.{field}"
        expected[name] = pins[field][0]
    ref_bytes = re.search(r"sizeof\(pymergetic_rxf_ref\) == (\d+)", ground)
    assert ref_bytes, "ground.h no longer pins the reference record size"
    expected["RXFB_REF_BYTES"] = int(ref_bytes.group(1), 0)
    expected["RXFB_KIND_DEAD"] = define(ground, "PYMERGETIC_RXF_KIND_DEAD")

    # REPR A is decided in the policy (docs/20, S11), not in a header
    smallint = re.search(r"bit (\d+) set is a small int", policy)
    repr_shift = re.search(r"handle << (\d+)", policy)
    assert smallint and repr_shift, "docs/20 no longer states the REPR A rule"
    expected["REPR_A_SMALLINT_BIT"] = int(smallint.group(1), 0)
    expected["REPR_A_HANDLE_SHIFT"] = int(repr_shift.group(1), 0)
    return expected


def documented_pins() -> dict[str, tuple[int, str]]:
    """The `data-pin` chips of the Model page: name -> (documented value, the chip's text)."""
    found = re.findall(
        r'data-pin="([A-Z0-9_]+)"\s+data-value="([^"]+)"[^>]*>([^<]*)<', read(MODEL_PAGE)
    )
    assert found, "the Model page documents no pinned constants"
    return {name: (int(value, 0), text) for name, value, text in found}


# The core the brief names, and the rest of what the page may not get wrong.
REQUIRED_PINS = {
    "RXF_SMP_CELL_BYTES",
    "RXF_SMP_C_TYPE",
    "RXF_SMP_C_PAYLOAD_SIZE",
    "RXF_SMP_C_GENERATION",
    "RXF_SMP_C_FLAGS",
    "RXF_SMP_C_FORWARD",
    "RXF_SMP_C_INDEX",
    "RXFB_C_PARENT",
    "RXFB_C_NAME",
    "RXFB_C_REFCOUNT",
    "RXFB_C_RESERVED",
    "RXFB_REF_BYTES",
    "RXF_SMP_FLAG_PINNED",
    "RXF_SMP_CLAIM_MOVING",
    "RXF_SMP_POISON",
    "RXFB_KIND_DEAD",
    "RXF_SMP_HANDLE_SHIFT",
    "REPR_A_SMALLINT_BIT",
    "REPR_A_HANDLE_SHIFT",
    "SMP_FNV_PRIME",
    "SMP_FNV_BASIS",
    "RXF_SMP_STATE_FREE",
    "RXF_SMP_STATE_READY",
    "RXF_SMP_STATE_RUNNING",
    "RXF_SMP_STATE_PARKED",
    "RXF_SMP_STATE_TERMINAL",
    "SMP_STEP_AGAIN",
    "SMP_STEP_PARK",
    "SMP_STEP_DONE",
    "SMP_STEP_SELFWAKE",
    "RXF_SMP_CELL_RUN",
    "RXF_SMP_CELL_DATA",
    "RXF_SMP_CELL_CHANNEL",
    "RXF_SMP_CELL_RING",
    "RXF_SMP_CELL_DELTA",
    "RXF_SMP_CELL_SCRATCH",
    "SMP_MP_NONE",
    "SMP_MP_RAISE",
    "SMP_MP_SCHED",
    "WIT_OUT_WORDS",
    "RXF_SMP_MAX_CARRIERS",
}


# ---- anti-drift: the documented facts match the definitions ---------------------------------------


def test_every_documented_constant_is_defined_by_the_c():
    expected = source_constants()
    documented = documented_pins()
    unknown = set(documented) - set(expected)
    assert not unknown, f"the Model page pins constants no source defines: {sorted(unknown)}"
    wrong = {
        name: (documented[name][0], expected[name])
        for name in documented
        if documented[name][0] != expected[name]
    }
    assert not wrong, f"the Model page documents stale values (page, source): {wrong}"


def test_the_documented_constants_cover_the_core():
    assert REQUIRED_PINS <= set(documented_pins())


def test_every_pin_chip_shows_its_value():
    for name, (value, text) in documented_pins().items():
        shown = hex(value) if value > 9 else str(value)
        assert (
            shown.lower() in text.lower() or str(value) in text
        ), f"the chip for {name} hides its value {value}"


def test_the_documented_offsets_agree_across_the_arena_headers():
    """The proof's header words (smp_arena.h) and the image's (ground.h) are the same 64 bytes."""
    arena = read(HOST / "smp_arena.h")
    ground = read(GROUND)
    pins = {
        field: (int(offset), int(size))
        for field, offset, size in re.findall(
            r"PYMERGETIC_RXF_PIN\(pymergetic_rxf_cell,\s*(\w+),\s*(\d+),\s*(\d+)\)", ground
        )
    }
    for name, field in (
        ("RXF_SMP_C_TYPE", "type"),
        ("RXF_SMP_C_PAYLOAD_SIZE", "payload_size"),
        ("RXF_SMP_C_GENERATION", "generation"),
        ("RXF_SMP_C_FLAGS", "flags"),
        ("RXF_SMP_C_FORWARD", "forward"),
        ("RXF_SMP_C_INDEX", "index"),
    ):
        assert define(arena, name) == pins[field][0], f"{name} and ground.h {field} disagree"
    header = re.search(r"sizeof\(pymergetic_rxf_cell\) == (\d+)", ground)
    assert header is not None
    assert int(header.group(1)) == define(arena, "RXF_SMP_CELL_BYTES") == 64
    assert max(offset + size for offset, size in pins.values()) == 64


# ---- anti-drift: the citations match the policy ----------------------------------------------------


def test_every_cited_section_exists_in_the_policy():
    policy = read(POLICY)
    cited = {int(n) for n in re.findall(r'data-section="S(\d+)"', read(MODEL_PAGE))}
    headings = {int(n) for n in re.findall(r"^## S(\d+)\.", policy, re.MULTILINE)}
    assert cited, "the Model page cites no section of docs/20"
    assert cited <= headings, f"the Model page cites sections docs/20 does not define: {sorted(cited - headings)}"
    assert set(range(1, 12)) <= cited, f"the Model page forgets sections: {sorted(set(range(1, 12)) - cited)}"
    assert set(range(1, 12)) <= headings, "docs/20 no longer carries its S1..S11 headings"


# ---- the page: diagrams, anchors, cross-links -------------------------------------------------------


def test_every_diagram_has_an_accessible_name():
    page = read(MODEL_PAGE)
    figures = re.findall(r"<svg\b[^>]*>", page)
    assert len(figures) >= 4, "the Model page must draw at least its four diagrams"
    for tag in figures:
        assert 'role="img"' in tag, f"diagram without role=img: {tag}"
        label = re.search(r'aria-label="([^"]+)"', tag)
        assert label and len(label.group(1)) > 20, f"diagram without an accessible name: {tag}"
    for needed in ("cell anatomy", "step loop", "aba", "compaction"):
        assert any(needed in tag.lower() for tag in figures), f"no diagram for {needed}"


def test_the_four_diagram_topics_are_drawn():
    page = read(MODEL_PAGE)
    for figure in ("Cell anatomy", "run-cell payload", "handle/ABA story", "compaction slide"):
        assert figure in page, f"the Model page is missing the figure: {figure}"


def test_the_cross_link_anchors_exist_on_the_page():
    page = read(MODEL_PAGE)
    ids = set(re.findall(r'id="([a-z0-9-]+)"', page))
    for anchor in (
        "cell-anatomy",
        "run-cells",
        "run-payload",
        "object-cells",
        "named-cells",
        "handles",
        "repr-a",
        "aba",
        "step-loop",
        "state-machine",
        "park-wake",
        "migration",
        "compaction",
        "micropython",
        "pending-protocol",
        "proof",
    ):
        assert anchor in ids, f"the Model page has no #{anchor} for the cross-links"


def test_the_detail_model_links_land_on_the_page():
    page = read(MODEL_PAGE)
    ids = set(re.findall(r'id="([a-z0-9-]+)"', page))
    cases = [
        (("Fn", 0, 0), "run-cells"),
        (("Module", 0, 0), "named-cells"),
        (("Channel", 0, 0), "park-wake"),
        (("Data", 0, 0), "object-cells"),
    ]
    for (kind, generation, forward), face in cases:
        links = _model_links("some.path", 7, kind, 1, generation, forward)
        assert links[0]["anchor"] == face, (kind, links[0])
        for link in links:
            assert link["anchor"] in ids, f"link to missing section #{link['anchor']}"
            assert link["href"] == f"/model?path=some.path#{link['anchor']}"
            assert link["cite"] and link["why"] and link["label"]
    stale = {link["anchor"] for link in _model_links("some.path", 7, "Data", 1, 3, 384)}
    assert {"aba", "compaction"} <= stale
    fresh = {link["anchor"] for link in _model_links("some.path", 7, "Data", 1, 0, 0)}
    assert "aba" not in fresh and "compaction" not in fresh
    root = _model_links("", 0, "Root", None, 0, 0)
    assert root[0]["anchor"] == "named-cells"


def test_the_model_page_and_its_script_parse(tmp_path: Path):
    from pymergetic.rxftk.server.app import MODEL_PAGE as served

    assert MODEL_PAGE == served and MODEL_PAGE.is_file()
    page = read(MODEL_PAGE)
    assert "<title>RXF model" in page
    assert 'href="/"' in page  # the page links back to the inspector
    found = re.search(r"<script>(.*)</script>", page, re.DOTALL)
    assert found is not None
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    path = tmp_path / "page.js"
    path.write_text(found.group(1))
    result = subprocess.run(
        ["node", "--check", str(path)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_the_app_serves_the_model_page():
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.core.object import Image
    from pymergetic.rxftk.server.app import create_app

    app = create_app(Image.empty())
    handlers = {
        route.path: route.endpoint for route in app.routes if isinstance(route, APIRoute)
    }
    assert "/model" in handlers
    html = handlers["/model"]()
    assert "<title>RXF model" in html and 'id="step-loop"' in html
