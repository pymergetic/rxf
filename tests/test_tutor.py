"""The tutor (/tutor): the run-cell machine, stepped by hand (the teaching surface of docs/21).

Anti-drift, in three directions — the tutor may never disagree with the machine it teaches:

- the page's ``data-pin`` chips against the C (``tests/host/smp_*.h``, ``smp_sched.c``,
  ``core/ground.h``, ``native/graph.h`` + ``stdlib_runtime.h``) and the S1..S11 headings of
  ``docs/20-EXECUTION-POLICY.md`` — the same sources ``tests/test_model_doc.py`` pins /model to;
- the engine's FACTS block (``static/tutor/tutor.js``) against those chips: page, engine and C
  must agree, and the engine may not invent a fact the C does not define;
- the design tokens (``static/tutor/tutor.css``) against the spec block of
  ``docs/21-UNIFIED-WEB-INTERFACE.md``, so the retrofit to ``ui.css`` is a drop-in.

The behavior of the teaching piece is graded headlessly under node: the two bug scenarios must
show their failure in buggy mode and none in fixed mode, the sync/async words must hold the S10
differential, and the exercises must compute — not assert — their answers.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
HOST = Path(__file__).parent / "host"
POLICY = ROOT / "docs" / "20-EXECUTION-POLICY.md"
SPEC = ROOT / "docs" / "21-UNIFIED-WEB-INTERFACE.md"
TUTOR = ROOT / "src" / "pymergetic" / "rxftk" / "server"
TUTOR_PAGE = TUTOR / "templates" / "tutor.html"
TUTOR_JS = TUTOR / "static" / "tutor" / "tutor.js"
TUTOR_CSS = TUTOR / "static" / "tutor" / "tutor.css"
GROUND = ROOT / "src" / "pymergetic" / "rxftk" / "core" / "ground.h"
GRAPH_H = ROOT / "native" / "graph.h"
RUNTIME_H = ROOT / "native" / "stdlib_runtime.h"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def define(text: str, name: str) -> int:
    """The value of a `#define NAME 123u` (decimal or hex), comment allowed after."""
    found = re.search(
        rf"^#define\s+{re.escape(name)}\s+(0x[0-9A-Fa-f]+|\d+)[uU]?\b",
        text,
        re.MULTILINE,
    )
    assert found, f"{name} is not a #define in the source"
    return int(found.group(1), 0)


def enumerator(text: str, name: str) -> int:
    """The value of a `NAME = 123,` enumerator in a header."""
    found = re.search(rf"\b{re.escape(name)}\s*=\s*(\d+)", text)
    assert found, f"{name} is not an enumerator in the source"
    return int(found.group(1), 0)


def source_constants() -> dict[str, int]:
    """Every constant the tutor may document, read out of the C that defines it."""
    arena = read(HOST / "smp_arena.h")
    sched = read(HOST / "smp_sched.h")
    sched_c = read(HOST / "smp_sched.c")
    arena_c = read(HOST / "smp_arena.c")
    ground = read(GROUND)
    graph = read(GRAPH_H)
    runtime = read(RUNTIME_H)

    expected: dict[str, int] = {}
    for name in (
        "RXF_SMP_CELL_BYTES",
        "RXF_SMP_C_TYPE",
        "RXF_SMP_C_PAYLOAD_SIZE",
        "RXF_SMP_C_GENERATION",
        "RXF_SMP_C_FLAGS",
        "RXF_SMP_C_FORWARD",
        "RXF_SMP_C_INDEX",
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
    # the layout pins of the header (ground.h, RXFB v6)
    pins = {
        field: (int(offset), int(size))
        for field, offset, size in re.findall(
            r"PYMERGETIC_RXF_PIN\(pymergetic_rxf_cell,\s*(\w+),\s*(\d+),\s*(\d+)\)",
            ground,
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
    expected["RXFB_KIND_DEAD"] = define(ground, "PYMERGETIC_RXF_KIND_DEAD")
    # the refusal codes and the depth budget of the interpreter (native/graph.h, stdlib_runtime.h)
    for name in (
        "RXF_GRAPH_CALL_DEPTH",
        "RXF_REFUSE_UNSUPPORTED",
        "RXF_REFUSE_CALL_DEPTH",
    ):
        expected[name] = define(graph, name)
    for name in (
        "RXF_REFUSE_STALE_GENERATION",
        "RXF_REFUSE_UNBOUND",
        "RXF_REFUSE_TYPE_MISMATCH",
    ):
        expected[name] = define(runtime, name)
    return expected


def documented_pins() -> dict[str, tuple[int, str]]:
    """The `data-pin` chips of the tutor page: name -> (documented value, the chip's text)."""
    found = re.findall(
        r'data-pin="([A-Z0-9_]+)"\s+data-value="([^"]+)"[^>]*>([^<]*)<',
        read(TUTOR_PAGE),
    )
    assert found, "the tutor page documents no pinned constants"
    return {name: (int(value, 0), text) for name, value, text in found}


def engine_facts() -> dict:
    """The FACTS block of the engine (tutor.js) — strict JSON between the two markers."""
    found = re.search(r"/\*FACTS\*/(\{.*?\})/\*END_FACTS\*/", read(TUTOR_JS), re.DOTALL)
    assert found, "tutor.js no longer carries its FACTS block"
    return json.loads(found.group(1))


def documented_policy() -> dict[str, str]:
    """The `data-policy` chips: section -> the name the page gives it."""
    found = re.findall(r'data-policy="(S\d+)"[^>]*>([^<]*)<', read(TUTOR_PAGE))
    assert found, "the tutor page documents no policy names"
    return dict(found)


def policy_headings() -> dict[str, str]:
    """docs/20's own section names: S1 -> the heading text."""
    return {
        number: name.strip()
        for number, name in re.findall(
            r"^## (S\d+)\. (.+)$", read(POLICY), re.MULTILINE
        )
    }


def spec_tokens() -> dict[str, str]:
    """The design tokens of docs/21's spec block: --name -> the value it sets."""
    block = re.search(r"```css(.*?)```", read(SPEC), re.DOTALL)
    assert block, "docs/21 no longer spells its token block in css"
    return {
        name: re.sub(r"/\*.*?\*/", "", value).strip()
        for name, value in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block.group(1))
    }


def css_tokens() -> dict[str, str]:
    """The tokens the tutor's page CSS defines (named exactly as docs/21 names them)."""
    block = re.search(r":root\s*\{(.*?)\}", read(TUTOR_CSS), re.DOTALL)
    assert block, "tutor.css no longer carries its token block"
    return {
        name: re.sub(r"/\*.*?\*/", "", value).strip()
        for name, value in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", block.group(1))
    }


def engine_values() -> dict[str, int]:
    """Every FACTS fact that has a chip counterpart, flattened by chip name -> value."""
    facts = engine_facts()
    flat = {
        "RXF_SMP_CELL_BYTES": facts["cell_bytes"],
        "RXF_SMP_HANDLE_SHIFT": facts["handle_shift"],
        "RXF_SMP_CLAIM_MOVING": facts["claim_moving"],
        "RXF_SMP_POISON": facts["poison"],
        "RXFB_KIND_DEAD": facts["kind_dead"],
        "SMP_FNV_PRIME": facts["fnv_prime"],
        "SMP_FNV_BASIS": facts["fnv_basis"],
        "RXF_GRAPH_CALL_DEPTH": facts["call_depth"],
    }
    for group, prefix in (
        (facts["states"], "RXF_SMP_STATE_"),
        (facts["step"], "SMP_STEP_"),
        (facts["cells"], "RXF_SMP_CELL_"),
        (facts["refuse"], "RXF_REFUSE_"),
    ):
        for name, value in group.items():
            chip = prefix + name
            if chip in flat or chip == "RXF_SMP_CELL_BYTES":
                continue  # the flat facts above are the authority for their names
            flat[chip] = value
    return {
        name: int(value, 0) if isinstance(value, str) else value
        for name, value in flat.items()
    }


REQUIRED_PINS = {
    # the cell header the arena strip shows
    "RXF_SMP_C_TYPE",
    "RXFB_C_PARENT",
    "RXF_SMP_C_PAYLOAD_SIZE",
    "RXF_SMP_C_GENERATION",
    "RXFB_C_NAME",
    "RXF_SMP_C_FLAGS",
    "RXFB_C_REFCOUNT",
    "RXF_SMP_C_FORWARD",
    "RXF_SMP_C_INDEX",
    "RXFB_C_RESERVED",
    "RXF_SMP_CELL_BYTES",
    # the machine
    "RXF_SMP_STATE_FREE",
    "RXF_SMP_STATE_READY",
    "RXF_SMP_STATE_RUNNING",
    "RXF_SMP_STATE_PARKED",
    "RXF_SMP_STATE_TERMINAL",
    "SMP_STEP_AGAIN",
    "SMP_STEP_PARK",
    "SMP_STEP_DONE",
    "SMP_STEP_SELFWAKE",
    "RXF_SMP_HANDLE_SHIFT",
    "RXF_SMP_CLAIM_MOVING",
    "RXF_SMP_POISON",
    "RXFB_KIND_DEAD",
    "SMP_FNV_PRIME",
    "SMP_FNV_BASIS",
    # the refusals and the budget
    "RXF_REFUSE_CALL_DEPTH",
    "RXF_REFUSE_UNBOUND",
    "RXF_REFUSE_STALE_GENERATION",
    "RXF_GRAPH_CALL_DEPTH",
}


# ---- anti-drift: the documented facts match the definitions --------------------------------------


def test_every_documented_constant_is_defined_by_the_c():
    expected = source_constants()
    documented = documented_pins()
    unknown = set(documented) - set(expected)
    assert not unknown, f"the tutor pins constants no source defines: {sorted(unknown)}"
    wrong = {
        name: (documented[name][0], expected[name])
        for name in documented
        if documented[name][0] != expected[name]
    }
    assert not wrong, f"the tutor documents stale values (page, source): {wrong}"


def test_the_documented_constants_cover_the_teaching_core():
    assert REQUIRED_PINS <= set(documented_pins())


def test_every_pin_chip_shows_its_value():
    for name, (value, text) in documented_pins().items():
        shown = hex(value) if value > 9 else str(value)
        assert shown.lower() in text.lower() or str(value) in text, (
            f"the chip for {name} hides its value {value}"
        )


def test_the_engine_facts_equal_the_page_chips():
    """Page, engine and C: the sim may not run on numbers the page does not teach."""
    facts = engine_values()
    documented = documented_pins()
    missing = set(facts) - set(documented)
    assert not missing, (
        f"the engine knows facts the page does not pin: {sorted(missing)}"
    )
    wrong = {
        name: (facts[name], documented[name][0])
        for name in facts
        if facts[name] != documented[name][0]
    }
    assert not wrong, f"the engine and the page disagree (engine, page): {wrong}"


def test_the_engine_facts_are_the_c():
    facts = engine_values()
    expected = source_constants()
    wrong = {
        name: (facts[name], expected[name])
        for name in facts
        if name in expected and facts[name] != expected[name]
    }
    assert not wrong, f"the engine runs on stale numbers (engine, C): {wrong}"


def test_the_policy_names_are_the_policy_headings():
    names = documented_policy()
    headings = policy_headings()
    assert set(names) == set(headings), (
        "the tutor's S1..S11 names and docs/20's headings disagree: "
        f"{sorted(set(names) ^ set(headings))}"
    )
    wrong = {s: (names[s], headings[s]) for s in names if names[s] != headings[s]}
    assert not wrong, f"the tutor renames policy sections (page, docs/20): {wrong}"
    assert engine_facts()["policy"] == names, (
        "the engine's policy names drifted from the page"
    )


def test_every_cited_section_exists_in_the_policy():
    cited = {f"S{n}" for n in re.findall(r'data-cite="S(\d+)"', read(TUTOR_PAGE))}
    headings = set(policy_headings())
    assert cited, "the tutor cites no section of docs/20"
    assert cited <= headings, (
        f"the tutor cites sections docs/20 lacks: {sorted(cited - headings)}"
    )
    assert {f"S{n}" for n in range(1, 12)} <= cited, (
        "the tutor forgets sections of docs/20"
    )


# ---- the design system (docs/21): the tutor is born in it ----------------------------------------


def test_the_design_tokens_are_the_spec_tokens():
    spec = spec_tokens()
    page = css_tokens()
    assert set(spec) == set(page), (
        "tutor.css and docs/21 define different tokens "
        f"(only spec: {sorted(set(spec) - set(page))}, only page: {sorted(set(page) - set(spec))})"
    )
    wrong = {
        name: (page[name], spec[name]) for name in spec if page[name] != spec[name]
    }
    assert not wrong, f"tutor.css forked the spec's token values (page, spec): {wrong}"


def test_the_semantic_colors_keep_their_meaning():
    css = read(TUTOR_CSS)
    refusal = re.search(r"\.refusal\s*\{(.*?)\}", css, re.DOTALL)
    assert refusal, (
        "the tutor has no .refusal block (docs/21: refusals are first-class UI)"
    )
    assert "--bad" in refusal.group(1), "a refusal is not red"
    assert ".header-row" in css, "the header-word rows do not use the .header-row style"
    assert "header-row" in read(TUTOR_JS) and 'class="refusal"' in read(TUTOR_JS)


def test_the_shell_is_the_spec_shell():
    page = read(TUTOR_PAGE)
    assert '<header class="shell">' in page
    assert '<span class="mark">RXF</span>' in page
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", page, re.DOTALL)
    assert nav, (
        "the tutor page carries no nav (docs/21: a page without the nav is a bug)"
    )
    entries = re.findall(r">([^<]+)</a>", nav.group(1))
    assert entries == ["Inspector", "Model", "Editor", "Tutor", "World"]
    assert '<a href="/tutor" class="active"' in nav.group(1)
    assert "<!--LIVE_SLOT-->" in page, "the tutor fills no live slot"
    assert "Tutor = teach" in page


# ---- the page and its routes ----------------------------------------------------------------------


def test_the_page_ids_are_unique():
    """Two elements may not share an id: getElementById silently wires the wrong one."""
    ids = re.findall(r'id="([^"]+)"', read(TUTOR_PAGE))
    duplicates = sorted({name for name in ids if ids.count(name) > 1})
    assert not duplicates, f"the tutor page repeats ids: {duplicates}"


def test_the_app_serves_the_tutor_page():
    from fastapi.routing import APIRoute

    from pymergetic.rxftk.core.object import Image
    from pymergetic.rxftk.server.app import create_app

    app = create_app(Image.empty())
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    handlers = {route.path: route.endpoint for route in routes}
    assert "/tutor" in handlers
    html = handlers["/tutor"]()
    assert "<title>RXF tutor" in html
    assert 'id="simulator"' in html and 'id="exercises"' in html
    assert "no image" in html, (
        "an unattached tutor must say exactly 'no image' (docs/21)"
    )
    mounts = {getattr(route, "path", ""): route for route in app.routes}
    assert "/tutor/static" in mounts


def test_the_live_slot_names_the_attached_session():
    from typing import cast

    from pymergetic.rxftk.core.object import Image
    from pymergetic.rxftk.server import tutor
    from pymergetic.rxftk.server.live.session import Session

    assert tutor.live_slot({"objects": 3, "issue_count": 0}, None) == "no image"
    quiet = tutor.live_slot({"objects": 3, "issue_count": 0}, "shm:demo")
    assert (
        "live:" in quiet
        and "shm:demo" in quiet
        and "3 cells" in quiet
        and "ok" in quiet
    )
    loud = tutor.live_slot({"objects": 3, "issue_count": 2}, "shm:demo")
    assert "bad" in loud
    session = cast(Session, type("S", (), {"spec": "shm:demo", "world": object()})())
    html = tutor.render(Image.empty(), session)
    assert "shm:demo" in html and "no image" not in html


def test_the_tutor_is_linked_from_the_other_surfaces():
    for name in ("model.html", "inspector.html", "editor.html"):
        page = read(TUTOR / "templates" / name)
        assert 'href="/tutor"' in page, f"{name} does not link the tutor"


# ---- the engine: deterministic by construction ------------------------------------------------------


def test_the_engine_is_deterministic_and_2d_only():
    script = read(TUTOR_JS)
    code = re.sub(
        r"/\*.*?\*/", " ", script, flags=re.DOTALL
    )  # scan the code, not the prose
    code = re.sub(r"//[^\n]*", " ", code)
    assert "Math.random" not in code, "the simulator may not roll dice"
    for forbidden in ("WebGPU", "WebGL", "webgl", "webgpu"):
        assert forbidden not in code, f"the tutor is 2D only, no {forbidden}"


def test_the_tutor_script_parses(tmp_path: Path):
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", "--check", str(TUTOR_JS)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


# ---- the engine's behavior, graded headlessly -------------------------------------------------------

DRIVER = r"""
const T = require(process.env.TUTOR_JS);
const out = {};
const walk = T.makeSim(T.scenarios.walkthrough());
walk.runToEnd();
out.walk = { counters: walk.counters, ops: walk.log.map((e) => e.op), texts: walk.log.map((e) => e.text) };
const fix = T.makeSim(T.scenarios.sealOrder(false));
fix.runToEnd();
const bug = T.makeSim(T.scenarios.sealOrder(true));
bug.runToEnd();
out.seal = { fixed: fix.counters, buggy: bug.counters, buggyTexts: bug.log.map((e) => e.text) };
const ok = T.makeSim(T.scenarios.claimLeak(false));
ok.runToEnd();
const leak = T.makeSim(T.scenarios.claimLeak(true));
leak.runToEnd();
out.claim = { fixed: ok.counters, leaky: leak.counters, leakyTexts: leak.log.map((e) => e.text) };
out.baseline = T.syncBaseline();
out.disc = {};
out.broken = {};
for (const name of T.patternNames) {
  out.disc[name] = T.asyncOutcome(name);
  out.broken[name] = T.brokenOutcome(name);
}
const inter = T.exercises.interleavings();
out.ex = {
  order_right: T.exercises.order().check(["seal", "cas"]).ok,
  order_wrong: T.exercises.order().check(["cas", "seal"]),
  inter_right: inter.check(["A · alternating", "C · C1 burst", "D · C1 first"]),
  inter_wrong: inter.check(["B · C0 burst"]).ok,
  det_right: T.exercises.deterministic().check("own-cell"),
  det_wrong: T.exercises.deterministic().check("shared-acc").ok,
  refusal_right: T.exercises.refusal().check("RXF_REFUSE_CALL_DEPTH").ok,
  refusal_wrong: T.exercises.refusal().check("RXF_REFUSE_UNBOUND").ok,
  position_right: T.exercises.checkPosition().check("boundary").ok,
  position_wrong: T.exercises.checkPosition().check("midway").ok,
};
const machine = T.refusals.machine();
out.refusals = { calls: [] };
for (let i = 0; i < 9; i++) out.refusals.calls.push(T.refusals.call(machine));
out.refusals.depth = machine.depth;
out.refusals.unbound = T.refusals.unbound(T.refusals.machine());
out.refusals.cancel = T.refusals.cancel(T.refusals.machine());
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def engine() -> dict:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        ["node", "-e", DRIVER],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "TUTOR_JS": str(TUTOR_JS)},
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_walkthrough_covers_the_step_loop(engine):
    walk = engine["walk"]
    ops = walk["ops"]
    for needed in (
        "pop",
        "cas",
        "claim",
        "work",
        "seal",
        "verify",
        "park",
        "wake",
        "push",
    ):
        assert needed in ops, f"the walkthrough never shows a {needed}"
    assert "refusal" in ops, "the walkthrough must show a refusal of the machine"
    texts = " ".join(walk["texts"])
    assert "ABA" in texts and "PARKED" in texts
    assert walk["counters"]["checksum_fails"] == 0, (
        "the clean walkthrough must not race"
    )


def test_the_seal_order_scenario_replays_the_bug(engine):
    seal = engine["seal"]
    assert seal["fixed"]["checksum_fails"] == 0, "the fixed ordering must be clean"
    assert seal["buggy"]["checksum_fails"] >= 1, (
        "the buggy ordering must show the CHECKSUM_BAD race"
    )
    assert any("CHECKSUM_BAD" in text for text in seal["buggyTexts"])
    assert any("BEFORE the seal" in text for text in seal["buggyTexts"])


def test_the_claim_leak_scenario_replays_the_s4_leak(engine):
    claim = engine["claim"]
    assert claim["fixed"]["claim_wedged"] == 0, "the fixed partner path must not leak"
    assert claim["leaky"]["claim_wedged"] >= 1, (
        "the leaky path must wedge the compactor"
    )
    assert any("wedges the compactor" in text for text in claim["leakyTexts"])


def test_sync_is_the_async_answer_and_broken_leaks(engine):
    baseline = engine["baseline"]
    for name, words in engine["disc"].items():
        assert words == baseline, (
            f"disciplined schedule {name} changed the answer: {words}"
        )
    answers = {tuple(words) for words in engine["broken"].values()}
    assert len(answers) > 1, (
        "the shared accumulator must leak the interleaving into the answer"
    )


def test_the_exercises_compute_their_answers(engine):
    ex = engine["ex"]
    assert ex["order_right"] is True
    assert (
        ex["order_wrong"]["ok"] is False and ex["order_wrong"]["code"] == "CHECKSUM_BAD"
    )
    assert ex["inter_right"]["ok"] is True
    assert ex["inter_wrong"] is False
    assert ex["det_right"]["ok"] is True and ex["det_right"]["correct"] == ["own-cell"]
    assert ex["det_wrong"] is False
    assert ex["refusal_right"] is True and ex["refusal_wrong"] is False
    assert ex["position_right"] is True and ex["position_wrong"] is False


def test_the_refusal_demos_name_the_codes(engine):
    refusals = engine["refusals"]
    depth = [
        call for call in refusals["calls"] if "RXF_REFUSE_CALL_DEPTH" in call["text"]
    ]
    assert len(depth) == 1, (
        "the depth budget must refuse exactly once, at the ninth call"
    )
    assert refusals["depth"] == 8, "a refused call must not push a frame"
    assert "RXF_REFUSE_UNBOUND" in refusals["unbound"]["text"]
    assert "CANCELED" in refusals["cancel"]["text"]
    assert "safe point" in refusals["cancel"]["text"]
