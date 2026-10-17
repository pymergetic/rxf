"""The certify command group end to end: `rxf certify issue|verify|revoke`.

The command path is the module path (the CLI rule): the group is `certify`,
its commands are the public functions of `certify/cli.py`. What is proven here
is the round trip — issue against the core's words, verify as the boot path
would, revoke as a deployment would — and the exit statuses of the CLI rule:
0 done, 1 the certification does not hold, 2 the input could not be used.
"""

from __future__ import annotations

import json
from pathlib import Path

from pymergetic.rxftk.cli import main

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

AT = "1700000000"
BLOCK = "ef" * 32  # the linked bytes' word a statement binds (--block)


def run(capsys, *argv: str) -> tuple[int, str, str]:
    status = main(list(argv))
    captured = capsys.readouterr()
    return status, captured.out, captured.err


def source_of(tmp_path: Path) -> Path:
    path = tmp_path / "small.rxfd"
    path.write_text(DELTA, encoding="utf-8")
    return path


def issue(capsys, tmp_path: Path, key_id: str, name: str, *extra: str) -> tuple[int, dict]:
    src = source_of(tmp_path)
    key, out = tmp_path / f"{name}.key.json", tmp_path / f"{name}.json"
    status, text, _ = run(
        capsys,
        "certify",
        "issue",
        str(src),
        str(key),
        str(out),
        f"--key-id={key_id}",
        f"--at={AT}",
        f"--block={BLOCK}",
        *extra,
    )
    return status, json.loads(text)


def reissue(rewritten: Path, field: str) -> Path:
    """The statement file with one hex digit of one field flipped (still hex)."""
    wire = json.loads(rewritten.read_text(encoding="utf-8"))
    value = wire[field]
    wire[field] = ("0" if value[0] != "0" else "1") + value[1:]
    rewritten.write_text(json.dumps(wire), encoding="utf-8")
    return rewritten


def test_issue_makes_the_key_and_the_statement(capsys, tmp_path):
    status, report = issue(capsys, tmp_path, "release-1", "first", f"--store={tmp_path/'store.json'}")
    assert status == 0
    assert report["target"] == str(tmp_path / "first.json")
    key = json.loads((tmp_path / "first.key.json").read_text(encoding="utf-8"))
    assert key["format"] == "rxf-certify-key-v1" and key["key_id"] == "release-1"
    assert report["signature"] and report["format"] == "rxf-certify-statement-v2"
    assert report["store"] is not None


def test_the_statement_binds_the_core_s_words_and_the_named_block(capsys, tmp_path):
    """The words a statement carries are the core's reports plus the named block."""
    src = source_of(tmp_path)
    status, text, _ = run(capsys, "core", "object", "digest", str(src))
    digests = json.loads(text)
    assert status == 0
    _, report = issue(capsys, tmp_path, "release-1", "first")
    assert report["image_digest"] == digests["digest"]
    assert report["text_digest"] == digests["delta_digest"]
    assert report["block_digest"] == BLOCK  # --block states it, the statement binds it
    assert report["issued"] == int(AT)


def test_issue_and_verify_round_trip(capsys, tmp_path):
    store = tmp_path / "store.json"
    status, _report = issue(capsys, tmp_path, "release-1", "first", f"--store={store}")
    assert status == 0
    src = source_of(tmp_path)
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tmp_path / "first.json"),
        f"--block={BLOCK}",
    )
    assert status == 0
    assert json.loads(text) == {"ok": True, "voices": ["release-1"], "statements": 1}


def test_verify_refuses_a_tampered_statement(capsys, tmp_path):
    store = tmp_path / "store.json"
    issue(capsys, tmp_path, "release-1", "first", f"--store={store}")
    src = source_of(tmp_path)
    tampered = reissue(tmp_path / "first.json", "signature")
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tampered),
        f"--block={BLOCK}",
    )
    report = json.loads(text)
    assert status == 1 and report["refusal"] == "NotEnoughVoices"
    assert report["refused"][0]["refusal"] == "BadSignature"  # the voice's name
    tampered = reissue(tmp_path / "first.json", "image_digest")
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tampered),
        f"--block={BLOCK}",
    )
    report = json.loads(text)
    assert status == 1 and report["refused"][0]["refusal"] == "WrongDigest"
    tampered = reissue(tmp_path / "first.json", "block_digest")
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tampered),
        f"--block={BLOCK}",
    )
    report = json.loads(text)
    assert status == 1 and report["refused"][0]["refusal"] == "WrongDigest"


def test_verify_refuses_a_statement_that_could_not_be_used(capsys, tmp_path):
    store = tmp_path / "store.json"
    issue(capsys, tmp_path, "release-1", "first", f"--store={store}")
    src = source_of(tmp_path)
    other = tmp_path / "other.json"
    other.write_text(json.dumps({"format": "rxf-certify-statement-v0"}), encoding="utf-8")
    status, _, err = run(
        capsys, "certify", "verify", str(src), str(store), str(other), f"--block={BLOCK}"
    )
    assert status == 2 and "MalformedStatement" in err


def test_revoke_ends_the_voice(capsys, tmp_path):
    store = tmp_path / "store.json"
    issue(capsys, tmp_path, "release-1", "first", f"--store={store}")
    src = source_of(tmp_path)
    status, text, _ = run(capsys, "certify", "revoke", str(store), "release-1")
    assert status == 0 and json.loads(text) == {
        "store": str(store),
        "revoked": ["release-1"],
    }
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tmp_path / "first.json"),
        f"--block={BLOCK}",
    )
    report = json.loads(text)
    assert status == 1 and report["refused"][0]["refusal"] == "RevokedKey"
    status, _, err = run(capsys, "certify", "revoke", str(store), "release-1")
    assert status == 2 and "already revoked" in err


def test_revoke_an_unknown_voice_exits_2(capsys, tmp_path):
    store = tmp_path / "store.json"
    issue(capsys, tmp_path, "release-1", "first", f"--store={store}")
    status, _, err = run(capsys, "certify", "revoke", str(store), "nobody")
    assert status == 2 and "UnknownKey" in err


def test_issue_refuses_a_key_id_the_key_file_denies(capsys, tmp_path):
    """The key file names its voice: `--key-id` names a new one, never renames it."""
    status, _ = issue(capsys, tmp_path, "release-1", "first")
    assert status == 0
    status, _, err = run(
        capsys,
        "certify",
        "issue",
        str(source_of(tmp_path)),
        str(tmp_path / "first.key.json"),
        str(tmp_path / "again.json"),
        "--key-id=release-2",
        f"--at={AT}",
        f"--block={BLOCK}",
    )
    assert status == 2 and "MalformedKey" in err


def test_a_quorum_decides_and_one_voice_short_exits_1(capsys, tmp_path):
    store = tmp_path / "store.json"
    src = source_of(tmp_path)
    issue(capsys, tmp_path, "release-1", "first", f"--store={store}", "--quorum=2")
    issue(capsys, tmp_path, "release-2", "second", f"--store={store}")
    both = (str(tmp_path / "first.json"), str(tmp_path / "second.json"))
    status, text, _ = run(capsys, "certify", "verify", str(src), str(store), *both, f"--block={BLOCK}")
    assert status == 0
    assert json.loads(text) == {"ok": True, "voices": ["release-1", "release-2"], "statements": 2}
    status, text, _ = run(
        capsys,
        "certify",
        "verify",
        str(src),
        str(store),
        str(tmp_path / "first.json"),
        f"--block={BLOCK}",
    )
    assert status == 1
    assert json.loads(text) == {
        "ok": False,
        "refusal": "NotEnoughVoices",
        "message": "the decision needs 2 voices and 1 spoke",
        "valid": ["release-1"],
        "refused": [],
    }
