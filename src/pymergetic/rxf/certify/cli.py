"""Commands on the certification layer: `rxf certify <command> SOURCE ...`.

`issue` signs the two digests an image reports (`Image.digest()` and
`Image.delta_digest()`) with one voice and writes the statement. `verify` is the
activation check the boot path repeats: the statements of an image against a
trust store, M voices of N (docs/11). `revoke` closes one voice's admission.

Exit status: 0 done, 1 the certification does not hold (the typed refusal is
the report), 2 the input could not be used.
"""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pymergetic.rxf import certify, source
from pymergetic.rxf.certify import (
    NotEnoughVoices,
    Policy,
    Refusal,
    TrustKey,
    TrustStore,
)
from pymergetic.rxf.cli import Problems


def _read(path: Path) -> object:
    """The parsed JSON of a file; a file that cannot be read or parsed exits 2."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OSError(f"{path}: {error}") from error


def _write(path: Path, wire: dict[str, Any]) -> None:
    path.write_text(json.dumps(wire, indent=2) + "\n", encoding="utf-8")


def unusable(refusal: Refusal) -> OSError:
    """An input that cannot be used exits 2; the typed name rides in the message."""
    return OSError(f"{type(refusal).__name__}: {refusal}")


def problems(refusal: Refusal) -> Problems:
    """A certification that does not hold exits 1 with its typed report."""
    report: dict[str, Any] = {
        "ok": False,
        "refusal": type(refusal).__name__,
        "message": str(refusal),
    }
    if isinstance(refusal, NotEnoughVoices):
        report["valid"] = list(refusal.valid)
        report["refused"] = [
            {"key_id": key, "refusal": type(each).__name__, "message": str(each)}
            for key, each in refusal.refused
        ]
    return Problems(report)


def activation(
    store: str, statements: Sequence[str]
) -> tuple[TrustStore, list[certify.Statement]]:
    """The seam's parsed inputs: the trust store at STORE and every statement file.

    The packaging lane's build and `verify` both activate through here — one
    reader of the JSON wires, whose refusals are inputs that cannot be used
    (exit 2), never a certification that failed.
    """
    try:
        base = certify.store_of(_read(Path(store)))
        voices = [certify.statement_of(_read(Path(name))) for name in statements]
    except Refusal as refusal:
        raise unusable(refusal) from refusal
    return base, voices


def issue(
    src: str,
    key: str,
    out: str,
    key_id: str = "",
    at: int | None = None,
    store: str = "",
    quorum: int = 1,
    max_age: int = 2592000,
    skew: int = 300,
) -> dict[str, Any]:
    """Signs the two digests of SOURCE with the voice in KEY; writes the statement to OUT.

    KEY is made here when it does not exist yet (`--key-id` names the new voice).
    With `--store` the voice is admitted to the trust store, made with `--at` as
    its now word when absent. `--at` stamps the statement; without it the wall
    clock does. The policy words default to 30 days of age and 300 of skew.
    """
    try:
        image = source.load(src)
        path = Path(key)
        if path.exists():
            held, seed = certify.key_of(_read(path))
            if key_id and key_id != held:
                raise certify.MalformedKey(f"the key file names {held}, not {key_id}")
            key_id = held
        else:
            if not key_id:
                raise certify.MalformedKey("a new voice needs a name: --key-id")
            seed = secrets.token_bytes(certify.SEED_BYTES)
            _write(path, certify.key_wire(key_id, seed))
            path.chmod(0o600)
        issued = int(time.time()) if at is None else at
        statement = certify.issue(
            image.digest(), image.delta_digest(), key_id, seed, issued
        )
        _write(Path(out), certify.statement_wire(statement))
        admitted: str | None = None
        if store:
            place = Path(store)
            base = (
                certify.store_of(_read(place))
                if place.exists()
                else TrustStore(Policy(now=issued, max_age=max_age, skew=skew, quorum=quorum))
            )
            _write(
                place,
                certify.store_wire(
                    certify.admit(base, TrustKey(key_id, certify.public_of(seed)))
                ),
            )
            admitted = store
    except Refusal as refusal:
        raise unusable(refusal) from refusal
    # The statement it wrote and where (the CLI rule: results print as JSON).
    return {"target": out, "store": admitted, **certify.statement_wire(statement)}


def verify(src: str, store: str, *statements: str) -> dict[str, Any]:
    """The activation check over SOURCE: the statements against the trust store (docs/11).

    Every statement file counts as one voice; the policy words at STORE (now,
    max_age, skew, quorum) are the decision's. Exit status 1 with the typed
    refusal when fewer than the quorum speak.
    """
    image = source.load(src)
    base, voices = activation(store, statements)
    try:
        quorum = certify.verify_quorum(image.digest(), image.delta_digest(), voices, base)
    except Refusal as refusal:
        raise problems(refusal) from refusal
    return {"ok": True, "voices": list(quorum.voices), "statements": len(voices)}


def revoke(store: str, key_id: str) -> dict[str, Any]:
    """Revokes one voice of the trust store at STORE; its admissions end here.

    A key id that is no voice of the store (or one already revoked) could not be
    used and exits 2.
    """
    try:
        base = certify.store_of(_read(Path(store)))
        updated = certify.revoke(base, key_id)
    except Refusal as refusal:
        raise unusable(refusal) from refusal
    _write(Path(store), certify.store_wire(updated))
    return {"store": store, "revoked": sorted(updated.revoked)}
