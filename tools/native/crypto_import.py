"""The import lane of the cap.crypto card: the card's Rust source onto image words.

One definition, one set of vectors, two seating paths (docs/25 §11). The card is
``native/crypto/src/*.rs`` (``impl = rs``) and this tool lowers THE SAME source
through the LLVM-IR subset of ``tools/native/rximport.py`` onto the semantic
graphs of the image, then ``tools/native/compose.py`` composes those into the
program words that run on the interpreter of ``native/graph.c`` (the words
``tests/rxf_native.py`` executes). The other seating path of the same source is
the native C ABI (``src/pymergetic/rxf/net/crypto.py`` over ``libpm_crypto.so``);
``tests/test_crypto_import.py`` pins both seats against the published vectors of
``tests/test_crypto_card.py``'s set.

    python tools/native/crypto_import.py             # the per-function import report
    python tools/native/crypto_import.py --json      # the same report as JSON
    python tools/native/crypto_import.py --words DIR # the composed words of every imported function

The compile is the importer's own ``.rs`` door (``rustc --emit=llvm-ir
--crate-type=lib -C opt-level=0 -C panic=abort``, the flags of
``rximport.compile_rs``) plus the crate's own edition (``native/crypto/Cargo.toml``:
``edition = "2021"``; the door predates the card and passes none). One text
conditioning runs on the IR before rximport sees it: inside quoted string
payloads the byte 0x5C is respelled from the ``\\\\`` form to the ``\\5C`` form
of the same byte, because ``rximport._unescape`` reads only the hex form and the
module does not parse at all without it (rustc emits the ``\\\\`` form in the
panic machinery's strings). Both spellings are the same bytes; this is not a
rewrite of the program. A ``.ll`` input is the entrance format of docs/22 and is
read as it stands, with no conditioning at all.

``rximport.py`` and ``rxemit.py`` stay read-only to this lane: a construct their
subset refuses is REPORTED BY NAME (the refusal message and the function the
construct sits in), never patched around. The report is the refusal ledger of
the card -- what imports, what refuses, and the IR construct behind each
refusal.

No entry shim is needed and none ships: ``native/crypto/src/lib.rs`` already
carries the eleven ``pm_crypto_*`` C ABI entries, so the import compiles that
file as the crate root (its ``pub mod`` tree pulls the card's other sources).
The imported set also carries three KERNELS of the primitives (the report names
them); they are private to their modules, so the card's C ABI cannot reach them
and no call-through shim can name them either (rustc refuses the card files'
``//!`` inner docs inside an ``include!`` -- E0753 -- and gives private
functions internal linkage, so neither a wrapper nor a relink seats them). That
is why the kernels' differential seat is a named gap of the report instead of a
second build: one definition, and it stays one.

Deterministic: the same sources answer the same reports and the same word
bytes. The reports sort by face, the words are composed by one composer run,
and nothing here reads a clock, a network, or a random source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import compose
import rximport

from pymergetic.rxf.core import tree

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
CARD = ROOT / "native" / "crypto" / "src" / "lib.rs"
CRATE = ROOT / "native" / "crypto"
STARTER = ROOT / "starter"
MODULE = "crypto"
RUSTC = rximport.RUSTC

# The refusal vocabulary this lane reports: rximport.Unsupported and
# rximport.ParseError are the subset's own refusals (policy -- the construct is
# outside the subset by design); anything else that escapes the lowering is an
# importer gap and is reported as one, by its exception name.
POLICY_REFUSALS = (rximport.Unsupported, rximport.ParseError)

# The named gaps of this lane, pinned BY NAME in tests/test_crypto_import.py.
# They are not refusals of the subset: they are places where the honest lane
# ends and the report says so out loud instead of staying silent.
NAMED_GAPS: tuple[str, ...] = (
    (
        "differential seat of the imported kernels: quarter_round, xtime and g are "
        "private module fns of the card's own files (rustc E0603 on any wrapper's "
        "path to them) and the card files carry `//!` inner docs (rustc E0753 inside "
        "include!), so no C ABI call-through shim compiles and the image words have "
        "no native face to differ against; their seats are the published vectors of "
        "the primitives' families (RFC 8439, RFC 7693, FIPS 197) and the interpreter "
        "runs of tests/test_crypto_import.py"
    ),
)

_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_EDITION = re.compile(r'^edition\s*=\s*"([^"]+)"', re.MULTILINE)
# rustc mangles a symbol as <path><hash-suffix>, the hash always "…h<16 hex>E"
_MANGLED = re.compile(r"^(?P<kind>_ZN|_R)(?P<rest>.*?)\d+h[0-9a-f]+E$")


@dataclass(frozen=True)
class FunctionReport:
    """One function of the card source and what the subset made of it.

    ``name`` is the IR symbol, ``face`` the readable name of the same function
    (the report's alphabet), ``entry`` marks the card's C ABI entries
    (``pm_crypto_*``), ``status`` is ``imported`` or ``refused``, ``site`` is
    the function the refusal's construct sits in (the head of rximport's
    message) and ``refusal`` the message itself, verbatim BY NAME.
    """

    name: str
    face: str
    entry: bool
    status: str
    site: str | None
    refusal: str | None
    kind: str | None  # "policy" (outside the subset) or "gap" (the importer's own)


@dataclass(frozen=True)
class CardImport:
    """The whole import: the report of every card function and the words of the
    imported ones (``words`` maps the IR symbol to its composed program bytes;
    ``word_key`` is the composer's key of the same program)."""

    source: Path
    ir_sha256: str
    reports: tuple[FunctionReport, ...]
    words: dict[str, bytes]

    def imported(self) -> tuple[FunctionReport, ...]:
        return tuple(row for row in self.reports if row.status == "imported")

    def refused(self) -> tuple[FunctionReport, ...]:
        return tuple(row for row in self.reports if row.status == "refused")

    def report(self, face: str) -> FunctionReport:
        rows = [row for row in self.reports if row.face == face]
        if len(rows) != 1:
            raise KeyError(f"{face}: {len(rows)} rows of this face in the report")
        return rows[0]

    def word_key(self, face: str) -> str:
        return f"{MODULE}.{self.report(face).name}"


def crate_edition(crate: Path = CRATE) -> str:
    """The edition of the card's own crate (``native/crypto/Cargo.toml``)."""
    found = _EDITION.search((crate / "Cargo.toml").read_text())
    return found.group(1) if found else "2021"


def respell_escapes(text: str) -> str:
    """Spell the byte 0x5C as ``\\5C`` inside quoted payloads (was ``\\\\``).

    Both spellings are one 0x5C byte of the string constant; only
    ``rximport._unescape``'s hex reader is in play, and without this the module
    raises ``ValueError`` at parse time before any function is even looked at.
    """

    def one(match: re.Match[str]) -> str:
        return '"' + match.group(1).replace("\\\\", "\\5C") + '"'

    return _STRING.sub(one, text)


def compile_card(source: Path = CARD) -> str:
    """The card's LLVM IR: the importer's ``.rs`` door plus the crate's edition.

    A ``.ll`` source is the entrance format of docs/22 and is read as it stands.
    """
    if source.suffix == ".ll":
        return source.read_text()
    command = [
        RUSTC,
        "--emit=llvm-ir",
        "--crate-type=lib",
        "--edition",
        crate_edition(),
        "-C",
        "opt-level=0",
        "-C",
        "panic=abort",
        str(source.resolve()),
        "-o",
        "-",
    ]
    # a working directory of its own (the reason is rximport.compile_rs's): rustc
    # drops codegen halves next to the output name, and two runs at once collide
    with tempfile.TemporaryDirectory(prefix="crypto-import-") as own:
        ran = subprocess.run(
            command, capture_output=True, text=True, check=False, cwd=own
        )
    if ran.returncode:
        raise RuntimeError(f"{RUSTC} could not compile {source}:\n{ran.stderr.strip()}")
    return respell_escapes(ran.stdout)


def demangle(symbol: str) -> str:
    """The readable face of a rustc symbol (the legacy and the v0 spelling)."""
    if symbol.startswith("pm_crypto_"):
        return symbol
    found = _MANGLED.match(symbol)
    if found is None:
        return symbol
    legacy = found.group("kind") == "_ZN"
    parts: list[str] = []
    rest = found.group("rest")
    while rest:
        sized = re.match(r"(\d+)(.*)$", rest, re.DOTALL)
        if sized is not None:
            size = int(sized.group(1))
            parts.append(sized.group(2)[:size])
            rest = sized.group(2)[size :]
        elif not legacy and rest.startswith("Cs"):
            rest = re.sub(r"^Cs[0-9a-f]+_[0-9a-f]+", "", rest)  # the crate root's hash
        elif not legacy and rest[0] == "N" and len(rest) > 1:
            rest = rest[2:]  # a path tag: Nv (in value), Nt (type), ...
        else:
            break
    face = "::".join(parts).replace("$LT$", "<").replace("$GT$", ">")
    for spelled, char in (("$u20$", " "), ("$u7b$", "{"), ("$u7d$", "}")):
        face = face.replace(spelled, char)
    return face.replace("..", "::") or symbol


def face_of(symbol: str) -> str:
    """The card's alphabet: the readable name of a symbol without the crate's
    own ``lib::`` root (``lib::aead::xtime`` reports as ``aead::xtime``)."""
    return demangle(symbol).removeprefix("lib::")


def card_functions(text: str) -> list[str]:
    """The IR symbols the card's own source defines (its entries, kernels and
    helpers), not the monomorphized ``core::`` bodies around them."""
    parsed = rximport.parse_module(text)[0]
    names = [function.name for function in parsed]
    return sorted(
        name
        for name in names
        if name.startswith("pm_crypto_") or demangle(name).startswith(("lib::", "<lib::"))
    )


def classify(text: str, name: str) -> FunctionReport:
    """Lower one function; the refusal (if any) BY NAME, verbatim."""
    face = face_of(name)
    entry = name.startswith("pm_crypto_")
    try:
        rximport.graphs_of(text, MODULE, only={name})
    except POLICY_REFUSALS as why:
        # the subset's own refusal: the message names the function it sits in
        message = str(why)
        head = message.partition(": ")[0]
        site = face_of(head) if ": " in message else None
        return FunctionReport(name, face, entry, "refused", site, message, "policy")
    except Exception as why:  # noqa: BLE001 - any importer gap becomes a named refusal
        message = f"{type(why).__name__}: {why}"
        return FunctionReport(name, face, entry, "refused", None, message, "gap")
    return FunctionReport(name, face, entry, "imported", None, None, None)


def import_card(source: Path = CARD, starter: Path = STARTER) -> CardImport:
    """The one pipeline entry: rustc -> rximport -> graphs -> compose -> words.

    Every function of the card source is classified; the imported ones are
    imported into a starter image and composed, and their program words come
    back beside the report.
    """
    text = compile_card(source)
    rows = [classify(text, name) for name in card_functions(text)]
    rows.sort(key=lambda row: (not row.entry, row.face, row.name))
    reports = tuple(rows)
    imported = {row.name for row in reports if row.status == "imported"}
    words: dict[str, bytes] = {}
    if imported:
        image = tree.load(starter)
        rximport.import_into(image, text, module=MODULE, only=set(imported))
        problems = image.check()
        if problems:
            raise RuntimeError(f"the imported deltas do not hold together: {problems}")
        programs = compose.programs(image)
        words = {name: programs[f"{MODULE}.{name}"] for name in sorted(imported)}
    return CardImport(
        source=source,
        ir_sha256=hashlib.sha256(text.encode()).hexdigest(),
        reports=reports,
        words=words,
    )


def report_text(card: CardImport) -> str:
    """The per-function report: the C ABI entries first, then the card's own
    bodies; refusals carry their construct, BY NAME."""
    lines = [
        (
            f"cap.crypto import of {card.source.relative_to(ROOT)} "
            f"(module {MODULE}, ir sha256 {card.ir_sha256[:16]}): "
            f"{len(card.imported())} imported, {len(card.refused())} refused"
        )
    ]
    for title, rows in (
        ("entries (the C ABI face)", [r for r in card.reports if r.entry]),
        ("kernels and helpers", [r for r in card.reports if not r.entry]),
    ):
        lines.append(f"{title}:")
        for row in rows:
            if row.status == "imported":
                lines.append(f"  {row.face:<52s} imported")
            else:
                lines.append(
                    f"  {row.face:<52s} refused ({row.kind}) in {row.site}: {row.refusal}"
                )
    lines.append("named gaps:")
    for gap in NAMED_GAPS:
        lines.append(f"  {gap}")
    return "\n".join(lines)


def report_json(card: CardImport) -> str:
    """The same report as JSON, with a digest per composed word set."""
    return json.dumps(
        {
            "module": MODULE,
            "source": str(card.source.relative_to(ROOT)),
            "ir_sha256": card.ir_sha256,
            "named_gaps": list(NAMED_GAPS),
            "functions": [
                {
                    "name": row.name,
                    "face": row.face,
                    "entry": row.entry,
                    "status": row.status,
                    "kind": row.kind,
                    "site": row.site,
                    "refusal": row.refusal,
                }
                for row in card.reports
            ],
            "words": {
                name: {
                    "key": f"{MODULE}.{name}",
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
                for name, payload in card.words.items()
            },
        },
        indent=2,
        sort_keys=True,
    )


def write_words(card: CardImport, directory: Path) -> list[Path]:
    """The composed words of every imported function, one file each
    (``<module>.<symbol>.words``), sorted."""
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, payload in sorted(card.words.items()):
        path = directory / f"{MODULE}.{name}.words"
        path.write_bytes(payload)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    """The command line of the card's import lane."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--source", type=Path, default=CARD, help="the card's lib.rs or a .ll")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--words", type=Path, help="write the composed words into this directory")
    args = parser.parse_args(argv)

    card = import_card(args.source)
    if args.words is not None:
        for path in write_words(card, args.words):
            print(path)
    print(report_json(card) if args.json else report_text(card))
    return 0


if __name__ == "__main__":
    sys.exit(main())
