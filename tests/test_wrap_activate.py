"""The activation seam end to end: what the C reads, what it refuses, by name.

The probe of ``native/wrap/wrap_activate.c`` (``native/wrap/wrap_activate_probe.c``)
runs the seam against a linked trust anchor and the two things a wrapper hands it: the
block it loaded and the cert block of the package's tail. The cases here are crafted by
the Python seat -- statements signed with ``pymergetic.rxftk.certify``, records written
with the codec of ``binary.package``, the anchor with the generator of
``binary.builder`` -- so the cross-seat claim is the whole matrix: the C answers the
same as the Python seat for what the Python seat wrote, and refuses every tamper.

The answers are the named words of ``native/wrap/wrap.h`` (RXF_WRAP_ACTIVATE_*); the
exit status of the probe is that word.
"""

from __future__ import annotations

import hashlib
import subprocess

import pytest

from pymergetic.rxftk import certify
from pymergetic.rxftk.binary import builder, package

NOW = 1700000000
IMAGE = "ab" * 32
TEXT = "cd" * 32

OK = 0
NO_CERT = 1
BAD_CERT = 2
NO_VOICE = 4
NO_QUORUM = 5

BLOCK = b"the block of the test: what the wrapper loaded"
SEEDS = {"release-1": bytes(range(32)), "release-2": bytes(range(1, 33))}


def store(quorum: int = 2, revoked: tuple[str, ...] = ()) -> certify.TrustStore:
    keys = tuple(
        certify.TrustKey(key_id, certify.public_of(seed)) for key_id, seed in SEEDS.items()
    )
    return certify.TrustStore(
        certify.Policy(now=NOW, max_age=3600, skew=60, quorum=quorum),
        keys,
        frozenset(revoked),
    )


def probe_with(tmp_path_factory, trust: certify.TrustStore | None):
    """The compiled probe: the seam, the primitives and one linked anchor."""
    work = tmp_path_factory.mktemp("wrap-activate")
    (work / "rxf_wrap_trust.bin").write_bytes(builder.trust_block(trust))
    binary = work / "wrap-activate-probe"
    subprocess.run(
        [
            builder.CLANG,
            "-std=c11",
            "-O2",
            "-o",
            str(binary),
            str(builder.WRAP / "wrap_crypto.c"),
            str(builder.WRAP / "wrap_mem.c"),
            str(builder.WRAP / "wrap_activate.c"),
            str(builder.WRAP / "wrap_trust.S"),
            str(builder.WRAP / "wrap_activate_probe.c"),
        ],
        cwd=work,
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """Two voices, the quorum two, none revoked."""
    return probe_with(tmp_path_factory, store())


@pytest.fixture(scope="module")
def open_probe(tmp_path_factory):
    """The wrapper that admits no voice: every unsigned package passes."""
    return probe_with(tmp_path_factory, None)


@pytest.fixture(scope="module")
def strict_probe(tmp_path_factory):
    """One voice, the quorum one, release-2 revoked: the same store with a door shut."""
    return probe_with(tmp_path_factory, store(quorum=1, revoked=("release-2",)))


def speak(key_id: str, issued: int = NOW, block: bytes = BLOCK) -> certify.Statement:
    """One voice over the block: the Python seat signs, the C reads."""
    return certify.issue(
        IMAGE,
        TEXT,
        hashlib.sha256(block).hexdigest(),
        key_id,
        SEEDS[key_id],
        issued,
    )


def records(*statements: certify.Statement) -> bytes:
    return package.cert_block(statements)


def answer(probe, tmp_path, block: bytes = BLOCK, cert: bytes | None = None) -> int:
    block_file = tmp_path / "block.bin"
    block_file.write_bytes(block)
    if cert is None:
        return subprocess.run([str(probe), str(block_file), "-"], check=False).returncode
    cert_file = tmp_path / "cert.bin"
    cert_file.write_bytes(cert)
    return subprocess.run([str(probe), str(block_file), str(cert_file)], check=False).returncode


def test_the_answers_are_the_named_words_of_the_header():
    # What the probe's exit status means: the words native/wrap/wrap.h names.
    assert (OK, NO_CERT, BAD_CERT, NO_VOICE, NO_QUORUM) == (0, 1, 2, 4, 5)


def test_an_unsigned_package_passes_only_where_no_voice_is_admitted(probe, open_probe, tmp_path):
    assert answer(probe, tmp_path, cert=None) == NO_CERT
    assert answer(open_probe, tmp_path, cert=None) == OK


def test_two_voices_over_the_block_pass(probe, tmp_path):
    # The C reconstructs the canonical bytes the Python seat signed: if the two
    # seats disagreed on one byte, this signature would not verify here.
    assert answer(probe, tmp_path, cert=records(speak("release-1"), speak("release-2"))) == OK


def test_one_voice_short_of_the_quorum_is_refused(probe, tmp_path):
    assert answer(probe, tmp_path, cert=records(speak("release-1"),)) == NO_QUORUM


def test_a_block_the_voices_did_not_speak_for_is_refused(probe, tmp_path):
    # 'ok means it is written for THIS image': other bytes than the ones signed.
    signed = records(speak("release-1"), speak("release-2"))
    assert answer(probe, tmp_path, block=BLOCK + b"!", cert=signed) == NO_VOICE


def test_a_tampered_signature_does_not_speak(probe, tmp_path):
    tampered = bytearray(records(speak("release-1"), speak("release-2")))
    tampered[168] ^= 0x01  # the first byte of release-1's signature
    assert answer(probe, tmp_path, cert=bytes(tampered)) == NO_QUORUM


def test_a_revoked_voice_does_not_count(strict_probe, tmp_path):
    # release-2 speaks, but its admission is closed in the anchor: alone it is no
    # voice at all, and with release-1 the quorum of one is met by release-1.
    assert answer(strict_probe, tmp_path, cert=records(speak("release-2"),)) == NO_VOICE
    assert (
        answer(strict_probe, tmp_path, cert=records(speak("release-1"), speak("release-2"))) == OK
    )


def test_an_expired_statement_does_not_count(probe, tmp_path):
    stale = speak("release-2", issued=NOW - 3600 - 1)
    assert answer(probe, tmp_path, cert=records(speak("release-1"), stale)) == NO_QUORUM
    future = speak("release-2", issued=NOW + 60 + 1)
    assert answer(probe, tmp_path, cert=records(speak("release-1"), future)) == NO_QUORUM


def test_one_key_speaks_once(probe, tmp_path):
    # One key signing twice is one voice (docs/11: distinct): three records of one
    # voice must not decide a quorum of two.
    twice = records(speak("release-1"), speak("release-1"), speak("release-1"))
    assert answer(probe, tmp_path, cert=twice) == NO_QUORUM


def test_a_cert_block_that_is_not_whole_records_is_refused(probe, tmp_path):
    whole = records(speak("release-1"), speak("release-2"))
    assert answer(probe, tmp_path, cert=whole[:-1]) == BAD_CERT
    assert answer(probe, tmp_path, cert=b"") == NO_CERT  # no bytes: no cert at all
