"""The import lane's provenance and comment lifting (docs/22).

``--provenance`` on ``tools/native/rximport.py`` is one flag carrying the run's tier and its
origin facts: one origin record per run, one citation per file the run compiles, and every
captured comment lifted -- a task marker its own managed kind -- and cited through the file's
citation so its locator and the permalink it derives are resolvable in the image. The floor
is honest: a run that claims nothing still lifts its comments and says ``provenance: none``;
a run that claims an anonymous word is refused at the floor before anything is imported.

These pins read the image itself (``doc_locator``/``doc_permalink``) and the run's report; the
18 refusal pins of ``test_outside_the_subset_is_refused`` and its xfail seam case are the
importer's own and are not touched here.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path

import compose
import pytest
import rximport

from pymergetic.rxf.core import tree
from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.kinds import DocKind, RefKind

from .snippets import STARTER

SOURCE = Path(__file__).parent / "import_corpus" / "provenance.c"
REPO = "https://example.org/repo.git"
COMMIT = "a" * 40
SPEC = f"full,repository={REPO},commit={COMMIT},license=MIT"

# The fixture's comments, pinned by hand: the file comment (free-floating, on the module),
# the comment at each definition (on the function it introduces), the marker its own kind.
FILE_COMMENT = (3, 9, "provenance", "DocKind.Narrates")
ADD_COMMENT = (11, 11, "provenance.provenance_add", "DocKind.Narrates")
XOR_COMMENT = (14, 14, "provenance.provenance_xor", "DocKind.Todo")


def fresh():
    return tree.load(STARTER)


def words(image, path):
    """The three words of a doc payload: kind, status, codec (ground.h)."""
    return struct.unpack("<III", bytes(image.get(image.handle(path))["payload"])[:12])


def locator_of_run(image, report):
    """The report's own words back at the image: the citation it names, located and linked."""
    cite = report.split("cite: ", 1)[1].split()[0]
    return image.doc_locator(image.handle(cite)), image.doc_permalink(image.handle(cite))


def test_an_import_with_provenance_records_the_origin_the_citation_and_the_comments():
    image = fresh()
    text = SOURCE.read_text()
    report = rximport.import_run(image, [SOURCE], module="provenance", provenance=SPEC)
    assert image.check() == []
    sha256 = hashlib.sha256(text.encode()).hexdigest()
    path = rximport.relative(SOURCE)
    last = len(text.splitlines())

    # one origin record of the run, at the tier it declared (a Topic Doc at root)
    origin = image.handle("origin")
    assert image.type_name(origin) == "Doc"
    assert words(image, "origin")[0] == int(DocKind.Topic)

    # per file one citation: the locator is path/lines/sha256 of the file it hashes, and the
    # permalink is derived from that record and the origin it cites (docs/22's locator seam)
    citation = image.handle("provenance_cite")
    assert image.doc_locator(citation) == {
        "path": path,
        "first_line": 1,
        "last_line": last,
        "sha256": sha256,
    }
    assert image.doc_permalink(citation) == f"{REPO}/blob/{COMMIT}/{path}#L1-L{last}"
    references = image.handle("DocRole.References")
    assert (origin, references, 0, int(RefKind.Doc), 0) in image.get(citation)["refs"]

    # every captured comment is lifted at its own line span, on the object it documents
    for first, end, target, kind in (FILE_COMMENT, ADD_COMMENT, XOR_COMMENT):
        doc = image.handle(f"{target}.comment_{first}_{end}")
        assert words(image, f"{target}.comment_{first}_{end}")[0] == int(
            DocKind.Todo if kind == "DocKind.Todo" else DocKind.Narrates
        )
        # ... and rides on the file's citation as a placed_on document: the comment carries
        # the locator facts (path/lines/sha256) and its permalink derives from the citation
        assert (citation, references, 0, int(RefKind.Doc), 0) in image.get(doc)["refs"]
    assert image.doc_text(image.handle(f"{ADD_COMMENT[2]}.comment_11_11")) == (
        "provenance_add adds its two words (a comment at a definition)."
    )
    assert image.doc_text(image.handle(f"{XOR_COMMENT[2]}.comment_14_14")).startswith("FIXME:")
    assert image.doc_text(image.handle(f"{FILE_COMMENT[2]}.comment_3_9")).startswith(
        "tests/import_corpus/provenance.c -- the comment-lifting fixture"
    )

    # at full the source itself is stored once and cited, never copied per placement
    stored = image.handle("provenance_source")
    assert image.doc_text(stored) == text
    assert (stored, references, 0, int(RefKind.Doc), 0) in image.get(citation)["refs"]

    # the report says what the run recorded -- locator and permalink read back from the image
    assert report.startswith("provenance: full\n")
    assert f"origin: origin repository={REPO}" in report
    assert f"file: {path} sha256={sha256}" in report
    assert f"cite: provenance_cite lines=1-{last} {REPO}/blob/{COMMIT}/{path}#L1-L{last}" in report
    locator, permalink = locator_of_run(image, report)
    assert locator == image.doc_locator(citation) and permalink == image.doc_permalink(citation)
    assert "comment: provenance.provenance_xor.comment_14_14 lines=14-14 kind=DocKind.Todo" in report


def test_an_import_without_provenance_lifts_its_comments_and_says_none():
    image = fresh()
    report = rximport.import_run(image, [SOURCE], module="provenance")
    assert image.check() == []
    # the honest floor: nothing is claimed -- no origin, no citation, no hash record ...
    assert report.startswith("provenance: none\n")
    assert image.resolve("origin") is None and image.resolve("provenance_cite") is None
    assert "cite: " not in report and "origin: " not in report
    # ... and the comments are still lifted, uncited, at their own spans
    assert image.doc_text(image.handle("provenance.provenance_add.comment_11_11")) == (
        "provenance_add adds its two words (a comment at a definition)."
    )
    assert words(image, "provenance.provenance_xor.comment_14_14")[0] == int(DocKind.Todo)
    assert (
        "comment: provenance.comment_3_9 lines=3-9 kind=DocKind.Narrates target=provenance"
        in report
    )
    # the report output of the command line says the same (the CLI prints the run's report)
    code = rximport.main([str(SOURCE), "--module", "provenance"])
    assert code == 0


def test_the_report_output_names_the_tier_the_run_claimed(capsys):
    assert rximport.main([str(SOURCE), "--module", "provenance", "--provenance", SPEC]) == 0
    report = capsys.readouterr().out
    assert report.startswith("provenance: full\n")
    path = rximport.relative(SOURCE)
    assert f"cite: provenance_cite lines=1-15 {REPO}/blob/{COMMIT}/{path}#L1-L15" in report


def test_the_hash_tier_records_the_file_hash_and_no_location():
    image = fresh()
    report = rximport.import_run(image, [SOURCE], module="provenance", provenance="hash")
    assert image.check() == []
    assert report.startswith("provenance: hash\n")
    # the floor's own payload: identity without location -- the hash record, and nothing else
    sha256 = hashlib.sha256(SOURCE.read_text().encode()).hexdigest()
    assert image.doc_text(image.handle("provenance_sha")) == f"tier=hash\nsha256={sha256}"
    assert image.resolve("origin") is None and image.resolve("provenance_cite") is None
    assert words(image, "provenance_sha")[0] == int(DocKind.Narrates)
    # the comments are lifted there too; a hash record cites nothing (it carries no location)
    assert "comment: provenance.comment_3_9 lines=3-9" in report and "cite: " not in report
    with pytest.raises(ValueError, match="identity without location"):
        rximport.Provenance.parse("hash,repository=x,commit=y,license=z")


def test_the_floor_refuses_an_anonymous_word_honestly():
    for word in ("none", "off", "", "anonymous"):
        image = fresh()
        before = image.digest(), image.export()
        spec = f"{word},repository={REPO},commit={COMMIT},license=MIT"
        with pytest.raises(DeltaError, match="the provenance floor is hash"):
            rximport.import_run(image, [SOURCE], module="provenance", provenance=spec)
        # refused at the floor, before the run imports or lifts anything
        assert (image.digest(), image.export()) == before
        assert image.resolve("origin") is None and image.check() == []


def test_the_provenance_claim_changes_no_program_word():
    claimed = fresh()
    plain = fresh()
    rximport.import_run(claimed, [SOURCE], module="provenance", provenance=SPEC)
    rximport.import_run(plain, [SOURCE], module="provenance")
    # the composer's mirror keeps in step because its face does not move: docs are editorial
    # (zero runtime refs), so a run's claim is invisible in every program word it composes
    assert compose.programs(claimed) == compose.programs(plain)
    assert claimed.check() == [] and plain.check() == []
