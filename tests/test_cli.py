"""The `rxf` command line: the command path is the module path."""

import json
from pathlib import Path

import pytest

from pymergetic.rxf import source
from pymergetic.rxf.cli import main, root
from pymergetic.rxf.core.object import Image

STARTER = Path(__file__).parents[1] / "starter"

DELTA = """<?xml version="1.0" encoding="UTF-8"?>
<delta xmlns:rxf="urn:pymergetic:rxf">
  <Type name="Point" form="Form.Struct" size="16" align="8">
    <Field name="x" value_type="uint64_t"/>
    <Field name="next" value_type="uint64_t" offset="8" flags="FieldFlags.Optional|Ref"/>
  </Type>
  <Point name="a" x="1" next="b"/>
  <Point name="b" x="2"/>
  <rxf:entry path="a"/>
</delta>
"""


@pytest.fixture
def delta_file(tmp_path):
    path = tmp_path / "small.xml"
    path.write_text(DELTA, encoding="utf-8")
    return path


def run(capsys, *argv):
    status = main(list(argv))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def test_the_command_tree_is_the_module_tree():
    commands = root()
    assert set(commands) == {"binary", "certify", "console", "core", "live", "server"}
    assert set(commands["certify"]) == {
        "issue",
        "verify",
        "revoke",
        "activation",
        "problems",
        "unusable",
    }
    assert set(commands["core"]) == {"object", "delta"}
    assert set(commands["core"]["object"]) == {"info", "check", "digest", "save"}
    assert set(commands["core"]["delta"]) == {
        "show",
        "apply",
        "compile",
        "inspect",
        "patch",
    }
    assert set(commands["live"]) == {
        "info",
        "digest",
        "emit",
        "apply",
        "patch",
        "halt",
        "go",
        "ping",
        "console",
    }
    assert set(commands["server"]) == {"browse", "serve"}


def test_every_command_sits_in_the_cli_file_of_what_it_operates_on():
    base = Path(__file__).parents[1] / "src/pymergetic/rxf"
    assert sorted(
        p.relative_to(base).parent.as_posix() for p in base.rglob("cli.py")
    ) == [
        ".",
        "binary",
        "certify",
        "console",
        "core/delta",
        "core/object",
        "live",
        "server",
    ]


def test_a_group_lists_its_commands(capsys):
    status, out, _ = run(capsys)
    assert status == 0
    assert "rxf core object check" in out and "rxf core delta apply" in out
    assert "rxf server browse" in out and "rxf live console" in out
    status, out, _ = run(capsys, "core", "delta")
    assert status == 0 and "rxf core delta show" in out and "rxf core object" not in out


def test_info_check_and_digest_of_a_delta(capsys, delta_file):
    status, out, _ = run(capsys, "core", "object", "info", str(delta_file))
    report = json.loads(out)
    assert status == 0 and report["ok"] and report["entry"] == "a"
    assert report["objects"] == 125  # 120 ground + Point, x, next, a, b
    status, out, _ = run(capsys, "core", "object", "check", str(delta_file))
    assert status == 0 and json.loads(out) == {"ok": True, "issues": []}
    _, out, _ = run(capsys, "core", "object", "digest", str(delta_file))
    assert json.loads(out) == {
        "digest": report["digest"],
        "delta_digest": report["delta_digest"],
    }


def test_show_is_canonical_and_can_be_one_module(capsys, delta_file, tmp_path):
    _, text, _ = run(capsys, "core", "delta", "show", str(delta_file))
    again = tmp_path / "again.xml"
    again.write_text(text, encoding="utf-8")
    assert run(capsys, "core", "delta", "show", str(again))[1] == text
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    _, root_only, _ = run(capsys, "core", "delta", "show", str(delta_file), "--module=")
    assert root_only == text  # no sub-modules here, so the root holds everything


def test_save_writes_each_format_and_all_read_back_equal(capsys, delta_file, tmp_path):
    digest = core_digest(delta_file)
    for name in ("copy.xml", "copy.rxfb", "copy"):
        target = tmp_path / name
        status, out, _ = run(
            capsys, "core", "object", "save", str(delta_file), str(target)
        )
        assert status == 0 and json.loads(out)["target"] == str(target)
        assert (
            json.loads(run(capsys, "core", "object", "digest", str(target))[1])[
                "digest"
            ]
            == digest
        )
    assert (tmp_path / "copy" / "__init__.xml").is_file()
    assert (tmp_path / "copy.rxfb").read_bytes() == Image.from_bytes(
        (tmp_path / "copy.rxfb").read_bytes()
    ).compact().to_bytes()


def core_digest(path):
    image = Image.empty()
    image.apply(Path(path).read_text(encoding="utf-8"))
    return image.digest()


def test_save_does_not_overwrite_a_used_directory(capsys, delta_file, tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    (target / "keep.txt").write_text("mine")
    status, _, err = run(capsys, "core", "object", "save", str(delta_file), str(target))
    assert status == 2 and "not empty" in err
    assert (target / "keep.txt").read_text() == "mine"
    status, _, _ = run(
        capsys, "core", "object", "save", str(delta_file), str(target), "--overwrite"
    )
    assert status == 0 and (target / "keep.txt").exists()


def test_apply_replays_deltas_atomically(capsys, delta_file, tmp_path):
    patch = tmp_path / "patch.xml"
    patch.write_text('<delta><Point name="c" x="3" next="a"/></delta>')
    out = tmp_path / "result.rxfb"
    status, text, _ = run(
        capsys, "core", "delta", "apply", str(delta_file), str(patch), f"--to={out}"
    )
    report = json.loads(text)
    assert status == 0 and report["ok"] and report["format"] == "image"
    image = Image.from_bytes(out.read_bytes())
    assert image.check() == [] and image.resolve("c") is not None


def test_apply_reports_a_broken_delta_and_writes_nothing(capsys, delta_file, tmp_path):
    patch = tmp_path / "bad.xml"
    patch.write_text('<delta><Point name="c" next="nowhere"/></delta>')
    out = tmp_path / "never.xml"
    status, _, err = run(
        capsys, "core", "delta", "apply", str(delta_file), str(patch), f"--to={out}"
    )
    assert status == 2 and "does not resolve" in err and not out.exists()
    status, _, err = run(
        capsys, "core", "object", "check", str(tmp_path / "missing.xml")
    )
    assert status == 2 and "missing.xml" in err


def test_check_exits_1_with_the_report_when_the_image_has_problems(
    capsys, tmp_path, monkeypatch
):
    broken = {"ok": False, "issues": [{"path": "a", "message": "broken"}]}
    monkeypatch.setattr(source, "report", lambda image: broken)
    path = tmp_path / "x.xml"
    path.write_text("<delta/>")
    status, out, _ = run(capsys, "core", "object", "check", str(path))
    assert status == 1 and json.loads(out) == broken  # ok and issues, nothing else


def test_a_module_tree_of_the_starter_is_a_source(capsys):
    status, out, _ = run(capsys, "core", "object", "info", str(STARTER))
    report = json.loads(out)
    assert (
        status == 0
        and report["ok"]
        and report["entry"] == ("pymergetic.rxf.application.main")
    )
