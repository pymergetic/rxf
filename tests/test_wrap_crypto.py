"""The activation seam's primitives: the pinned vectors, through the C seat itself.

The gate of ``native/wrap/wrap_crypto.c`` is its own probe
(``native/wrap/wrap_crypto_probe.c``): the C runs the published vectors of FIPS
180-4 and RFC 8032 and answers 0 only when every one passed (docs/26 Recipe 1:
if a vector disagrees with the code, the code is wrong). This test compiles that
probe with the pinned Clang of the repo, runs it, and cross-checks the one
self-pinned vector against the Python seat -- both seats, one definition.
"""

from __future__ import annotations

import subprocess

import pytest

from pymergetic.rxftk.binary import builder
from pymergetic.rxftk.certify import ed25519

# The self-pinned vector of wrap_crypto.c: generated once by the Python seat and
# pinned in the C (docs/26 Recipe 1: labeled honestly where no published vector
# exists).
SELF_KEY = bytes.fromhex(
    "d04ab232742bb4ab3a1368bd4615e4e6d0224ab71a016baf8520a332c9778737"
)
SELF_MESSAGE = b"rxf wrap crypto"
SELF_SIGNATURE = bytes.fromhex(
    "ca05334f3ce257f6b6bf3472d61cfb561738765dbe13da543d5cb2a50c44afaf"
    "2e86948316fd10f72df967f6cd8625665204e63b595be2ee2c0d9d31939eb102"
)


@pytest.fixture(scope="module")
def probe(tmp_path_factory):
    """The compiled probe binary: the C seat and its vectors."""
    work = tmp_path_factory.mktemp("wrap-crypto")
    binary = work / "wrap-crypto-probe"
    subprocess.run(
        [
            builder.CLANG,
            "-std=c11",
            "-O2",
            "-o",
            str(binary),
            str(builder.WRAP / "wrap_crypto.c"),
            str(builder.WRAP / "wrap_crypto_probe.c"),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return binary


def test_every_pinned_vector_passes_in_the_c_seat(probe):
    # FIPS 180-4 for the hashes, RFC 8032 section 7.1 for Ed25519, and the one
    # self-pinned vector: the probe answers 0 only when all of them passed and
    # every tampered signature was refused.
    ran = subprocess.run([str(probe)], check=False)
    assert ran.returncode == 0


def test_the_self_pinned_vector_is_the_python_seats_signature():
    # the same bytes both sides: the C pins what the Python seat signs, and the
    # Python seat verifies what the C accepts.
    assert ed25519.public_key(bytes.fromhex("11" * 32)) == SELF_KEY
    assert ed25519.sign(bytes.fromhex("11" * 32), SELF_MESSAGE) == SELF_SIGNATURE
    assert ed25519.verify(SELF_KEY, SELF_MESSAGE, SELF_SIGNATURE)
