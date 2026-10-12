"""The physical delta and the XML: both give the same image, and the commands convert both ways."""

import hashlib
import random
from pathlib import Path

import pytest

from pymergetic.rxf import source
from pymergetic.rxf.cli import main
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.live import harness, physical

from .snippets import STARTER
from .test_live import edited

XML = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Module name="demo" rxf:parent="pymergetic.rxf" category="ModuleCategory.Application">
    <uint32_t name="seven" rxf:data="07000000" />
    <uint32_t name="eight" rxf:data="08000000" />
  </Module>
  <rxf:set path="pymergetic.rxf.application.discount" rxf:data="2d000000"/>
</delta>
"""


@pytest.fixture(scope="module")
def base() -> Image:
    return source.load(STARTER)


@pytest.fixture
def xml_file(tmp_path: Path) -> Path:
    path = tmp_path / "change.xml"
    path.write_text(XML, encoding="utf-8")
    return path


def test_xml_to_physical_to_image_to_xml_is_the_same_text(base: Image):
    reference = Image.from_bytes(base.to_bytes())
    reference.apply(XML)
    compiled = physical.from_xml(base, ("change.xml", XML))
    assert (compiled.creates, compiled.sets, compiled.deletes) == (3, 1, 0)
    result = harness.replay(base.to_bytes(), compiled.data)
    assert result.digest() == reference.digest()
    assert result.export() == reference.export()
    assert result.delta_digest() == reference.delta_digest()


def test_the_physical_form_is_one_form(base: Image):
    first = physical.from_xml(base, ("a.xml", XML))
    second = physical.from_xml(base, ("b.xml", XML))
    assert first.data == second.data
    assert hashlib.sha256(first.data).hexdigest() == physical.digest_of(second.data)
    # it is decoded without the image: no handle in it, every object by its path
    operations = physical.decode(first.data)
    assert operations[0]["operations"] == len(operations) - 1
    assert {o["path"] for o in operations[1:] if o["op"] == "create"} == {
        "pymergetic.rxf.demo",
        "pymergetic.rxf.demo.seven",
        "pymergetic.rxf.demo.eight",
    }


@pytest.mark.parametrize("seed", range(6))
def test_random_edits_give_the_same_image_both_ways(base: Image, seed: int):
    rng = random.Random(100 + seed)
    before = Image.from_bytes(base.to_bytes())
    for _ in range(2):
        after = edited(before, rng)
        compiled = physical.compile(before, after)
        # the delta, with and without the check of the base it was made for
        loose = physical.compile(before, after, check_base=False)
        for data in (compiled.data, loose.data):
            result = harness.replay(before.to_bytes(), data)
            assert result.digest() == after.digest()
            assert result.compact().to_bytes() == after.compact().to_bytes()
        before = after


def test_a_delta_for_another_base_is_refused(base: Image):
    from pymergetic.rxf.live import Refused

    other = Image.from_bytes(base.to_bytes())
    other.apply(XML)
    data = physical.from_xml(other, ("again.xml", XML.replace("demo", "second"))).data
    with pytest.raises(Refused):
        harness.replay(base.to_bytes(), data)


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str]:
    status = main(list(argv))
    return status, capsys.readouterr().out


def test_the_commands_convert_both_ways(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, xml_file: Path, base: Image
):
    binary = tmp_path / "change.rxfd"
    status, out = run(
        capsys,
        "core",
        "delta",
        "compile",
        str(STARTER),
        str(xml_file),
        "--to",
        str(binary),
    )
    assert status == 0 and '"creates": 3' in out and '"deletes": 0' in out
    status, out = run(capsys, "core", "delta", "inspect", str(binary))
    assert status == 0 and "pymergetic.rxf.demo.seven" in out
    result = tmp_path / "result.xml"
    status, out = run(
        capsys, "core", "delta", "patch", str(STARTER), str(binary), "--to", str(result)
    )
    assert status == 0 and '"ok": true' in out
    reference = Image.from_bytes(base.to_bytes())
    reference.apply(XML)
    assert result.read_text(encoding="utf-8") == reference.export()
    assert source.load(result).digest() == reference.digest()
