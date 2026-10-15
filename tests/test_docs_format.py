"""The documentation and human-intent format (docs/22).

Docs are a graph of recorded facts inside the image: Doc objects whose text sits in the
payload tail behind a codec flag, placed by RefKind.Doc references whose role says why.
This pins the format itself: doc object creation, one payload behind many placements, both
codec states readable, a docstring that survives the text round trip, todo status as an
image mutation, and a doc edge that never occupies a runtime ref slot.
"""

import struct

import pytest

from pymergetic.rxf.core.delta import DeltaError
from pymergetic.rxf.core.kinds import DocCodec, DocKind, DocRole, DocStatus, RefKind
from pymergetic.rxf.core.object import Image
from pymergetic.rxf.server.inspector import Inspector

from .support import resolved

NS = 'xmlns:rxf="urn:pymergetic:rxf"'

GUIDE = f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="guide">
    <rxf:doc name="intro" role="DocRole.Narrates">This explains the guide's purpose &amp; scope.</rxf:doc>
    <rxf:doc name="plan" kind="DocKind.Todo" role="DocRole.Narrates" codec="DocCodec.Raw">Expand the guide with examples.</rxf:doc>
  </Thing>
  <Thing name="topic">
    <rxf:ref role="DocRole.References" kind="RefKind.Doc" target="guide.intro"/>
  </Thing>
</delta>"""

BODY = "The quick brown fox jumps over the lazy dog, and the lazy dog sleeps."


def fresh(delta=GUIDE):
    image = Image.empty()
    image.apply(delta)
    return image


def refuses(image, text, fragment):
    before = image.digest(), image.export()
    with pytest.raises(DeltaError, match=fragment):
        image.apply(text)
    assert (image.digest(), image.export()) == before  # the image did not change


def words(image, path):
    """The three words of a doc payload: kind, status, codec (ground.h)."""
    return struct.unpack("<III", bytes(image.get(image.handle(path))["payload"])[:12])


def test_a_doc_is_an_object_of_the_built_in_doc_type():
    image = fresh()
    assert image.check() == []
    intro = resolved(image, "guide.intro")
    assert image.type_name(intro) == "Doc"
    assert image.doc_text(intro) == "This explains the guide's purpose & scope."
    kind, status, codec = words(image, "guide.intro")
    assert (kind, status) == (int(DocKind.Narrates), int(DocStatus.Open))
    assert codec in (int(DocCodec.Lz4), int(DocCodec.Raw))  # stored where it pays


def test_a_placement_is_a_doc_edge_with_a_role():
    image = fresh()
    intro, plan = image.handle("guide.intro"), image.handle("guide.plan")
    narrates, references = image.handle("DocRole.Narrates"), image.handle("DocRole.References")
    guide = image.get(image.handle("guide"))["refs"]
    assert guide == [
        (intro, narrates, 0, int(RefKind.Doc), 0),
        (plan, narrates, 0, int(RefKind.Doc), 0),
    ]
    topic = image.get(image.handle("topic"))["refs"]
    assert topic == [(intro, references, 0, int(RefKind.Doc), 0)]


def test_one_payload_sits_behind_many_placements():
    text = BODY + " " + BODY  # long enough to be worth storing once
    image = fresh(
        f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="guide">
    <rxf:doc name="intro" role="DocRole.Narrates" codec="DocCodec.Raw">{text}</rxf:doc>
  </Thing>
  <Thing name="topic"><rxf:ref role="DocRole.References" kind="RefKind.Doc" target="guide.intro"/></Thing>
  <Thing name="topic2"><rxf:ref role="DocRole.References" kind="RefKind.Doc" target="guide.intro"/></Thing>
</delta>"""
    )
    assert image.check() == []
    placements = Inspector(image).placements("guide.intro")
    assert sorted(row["source"] for row in placements) == ["guide", "topic", "topic2"]
    # One stored copy: the text is in the doc's payload tail and nowhere else.
    stored = [
        image.path(handle)
        for handle in range(image.handle_count)
        if image.live(handle) and text.encode() in bytes(image.get(handle)["payload"])
    ]
    assert stored == ["guide.intro"]
    assert image.export().count(text) == 1


def test_the_codec_flag_decides_the_stored_state_and_both_states_read():
    compressible = "TODO: expand the section on deltas. " * 30
    image = fresh(
        f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="guide">
    <rxf:doc name="plain" role="DocRole.Narrates" codec="DocCodec.Raw">{BODY}</rxf:doc>
    <rxf:doc name="packed" role="DocRole.Narrates">{compressible}</rxf:doc>
  </Thing>
</delta>"""
    )
    assert image.check() == []
    plain = image.get(image.handle("guide.plain"))["payload"]
    packed = image.get(image.handle("guide.packed"))["payload"]
    assert struct.unpack_from("<I", plain, 8)[0] == int(DocCodec.Raw)
    assert plain[12:] == BODY.encode()  # raw: the text is the tail, verbatim
    assert struct.unpack_from("<I", packed, 8)[0] == int(DocCodec.Lz4)
    assert packed[12:] != compressible.encode() and len(packed[12:]) < len(compressible.encode())
    # Both states read through the same text face; the reader never sees which.
    assert image.doc_text(image.handle("guide.plain")) == BODY
    assert image.doc_text(image.handle("guide.packed")) == compressible
    fields = dict(image.fields(image.handle("guide.plain")))
    assert fields["codec"] == "DocCodec.Raw"
    assert "codec" not in dict(image.fields(image.handle("guide.packed")))  # Lz4 is the zero word
    text = image.export()
    assert 'codec="DocCodec.Raw"' in text and BODY in text and compressible in text
    again = Image.empty()
    again.apply(text)
    assert again.digest() == image.digest() and again.check() == []


def test_a_doc_with_an_empty_body_is_not_written_in_raw():
    image = fresh()
    refuses(
        image,
        f'<delta {NS}><Thing name="t">'
        '<rxf:doc name="e" role="DocRole.Narrates" codec="DocCodec.Raw"/></Thing></delta>',
        "not written in DocCodec.Raw",
    )
    # The empty body a doc may have is stored compressed, never raw (docs/22).
    image.apply(f'<delta {NS}><Thing name="t"><rxf:doc name="e" role="DocRole.Narrates"/></Thing></delta>')
    payload = bytes(image.get(image.handle("t.e"))["payload"])
    assert len(payload) == 12 and struct.unpack_from("<I", payload, 8)[0] == int(DocCodec.Lz4)
    assert image.doc_text(image.handle("t.e")) == "" and image.check() == []
    again = Image.empty()
    again.apply(image.export())
    assert again.digest() == image.digest()


def test_check_validates_the_codec_flag():
    image = fresh()
    doc = image.handle("guide.plan")
    payload = bytearray(bytes(image.get(doc)["payload"]))
    struct.pack_into("<I", payload, 8, 99)  # a codec word the format does not name
    image.set_payload(doc, bytes(payload))
    assert any("does not decode" in message for _, message in image.check())
    image = fresh()  # a fresh one: the mutation above left that image unlinked
    image.apply(f'<delta {NS}><Thing name="t"><rxf:doc name="e" role="DocRole.Narrates"/></Thing></delta>')
    empty = image.handle("t.e")
    payload = bytearray(bytes(image.get(empty)["payload"]))
    struct.pack_into("<I", payload, 8, int(DocCodec.Raw))  # an empty body, now written raw
    image.set_payload(empty, bytes(payload))
    assert any("not written in DocCodec.Raw" in message for _, message in image.check())


def test_a_docstring_round_trips_through_the_text_form():
    image = fresh()
    text = image.export()
    assert "This explains the guide's purpose &amp; scope." in text  # the comment is written
    again = Image.empty()
    again.apply(text)
    assert again.doc_text(again.handle("guide.intro")) == image.doc_text(image.handle("guide.intro"))
    assert again.export() == text
    assert again.digest() == image.digest()
    assert again.check() == []


def test_a_todo_is_a_fact_with_a_status_word():
    image = fresh()
    plan = image.handle("guide.plan")
    assert words(image, "guide.plan") == (int(DocKind.Todo), int(DocStatus.Open), int(DocCodec.Raw))
    inspector = Inspector(image)
    assert [row["path"] for row in inspector.todos()] == ["guide.plan"]
    image.apply(f'<delta {NS}><rxf:set path="guide.plan" status="DocStatus.Resolved"/></delta>')
    assert image.check() == []
    assert words(image, "guide.plan") == (int(DocKind.Todo), int(DocStatus.Resolved), int(DocCodec.Raw))
    fields = dict(image.fields(plan))
    fields.pop("data")  # the stored text: the fields face names it, doc_text reads it
    assert fields == {
        "kind": "DocKind.Todo",
        "status": "DocStatus.Resolved",
        "codec": "DocCodec.Raw",
    }
    assert Inspector(image).todos() == []  # resolved todos leave the open list
    # The resolution is a fact like any other: it survives the text form.
    again = Image.empty()
    again.apply(image.export())
    assert words(again, "guide.plan") == (int(DocKind.Todo), int(DocStatus.Resolved), int(DocCodec.Raw))
    image.apply(f'<delta {NS}><rxf:set path="guide.plan" status="DocStatus.Open"/></delta>')
    assert words(image, "guide.plan") == (int(DocKind.Todo), int(DocStatus.Open), int(DocCodec.Raw))
    assert [row["path"] for row in Inspector(image).todos()] == ["guide.plan"]


def test_a_doc_edge_never_occupies_a_runtime_ref_slot():
    base = f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="a"/>
  <Thing name="guide">
    <rxf:ref index="3" kind="RefKind.Data" target="a"/>
    %s
  </Thing>
</delta>"""
    plain = Image.empty()
    plain.apply(base % "")
    documented = Image.empty()
    documented.apply(base % f'<rxf:doc name="intro" role="DocRole.Narrates">{BODY}</rxf:doc>')
    assert documented.check() == []
    # A doc placement does not move data: the data edge is the same reference,
    # and the doc edge itself sits at index 0 — it has no runtime slot.
    before = plain.get(plain.handle("guide"))["refs"]
    after = documented.get(documented.handle("guide"))["refs"]
    assert after[1:] == before and after[0][2] == 0 and after[0][3] == int(RefKind.Doc)
    assert bytes(plain.get(plain.handle("a"))["payload"]) == bytes(documented.get(documented.handle("a"))["payload"])
    # A doc edge that claims a runtime slot is refused: a placement is editorial only.
    refuses(
        documented,
        f'<delta {NS}><Thing name="t"><rxf:doc name="d" role="DocRole.Narrates">{BODY}</rxf:doc></Thing>'
        '<rxf:set path="t"><rxf:ref role="DocRole.Narrates" index="2" kind="RefKind.Doc" target="t.d"/></rxf:set></delta>',
        "runtime slot",
    )


def test_the_inspector_faces_read_docs_and_refuse_to_write_them():
    image = fresh()
    inspector = Inspector(image)
    rows = inspector.docs("guide")
    assert [(row["doc"], row["role"], row["kind"], row["status"]) for row in rows] == [
        ("guide.intro", "DocRole.Narrates", "DocKind.Narrates", "DocStatus.Open"),
        ("guide.plan", "DocRole.Narrates", "DocKind.Todo", "DocStatus.Open"),
    ]
    assert rows[0]["text"] == "This explains the guide's purpose & scope."
    placed = inspector.placements("guide.intro")
    assert sorted((row["source"], row["role"]) for row in placed) == [
        ("guide", "DocRole.Narrates"),
        ("topic", "DocRole.References"),
    ]
    todo = Inspector(image).todos()[0]
    assert todo["path"] == "guide.plan" and todo["placed_on"] == ["guide"]
    with pytest.raises(NotImplementedError, match="read-only"):
        inspector.set_doc_text("guide.intro", "a wrong docstring is a wrong fact")


# ---- provenance (docs/22): the per-import tier, the floor, and the locator seam ----

REPO = "https://example.org/repo.git"
COMMIT = "a" * 40
SHA = "b" * 64
UPSTREAM = "https://example.org/"
IMPORTER = "rximport 0.1"


def provenance_image():
    """Three imported objects, the shape an import lane leaves behind."""
    return fresh(
        f"""<delta {NS}>
  <Type name="Thing" form="Form.Struct" size="0" align="1"/>
  <Thing name="one"/><Thing name="two"/><Thing name="three"/>
</delta>"""
    )


def origin_of(image, tier="cite"):
    return image.doc_origin(
        0, "origin", tier, upstream=UPSTREAM, repository=REPO, commit=COMMIT, license="MIT", importer=IMPORTER
    )


def test_provenance_full_records_the_origin_the_citations_and_the_source_itself():
    image = provenance_image()
    one, two = image.handle("one"), image.handle("two")
    source = "int main(void) { return 0; }\n" * 8  # compressible: stored lz4 where it pays
    origin = origin_of(image, "full")
    stored = image.doc_source(0, "source", "full", source, origin=origin)
    citation = image.doc_cite(
        0, "citation", "full", [one, two], [origin, stored], path="src/main.c", first_line=1, last_line=1, sha256=SHA
    )
    assert image.check() == []
    # full = origin + citations + the imported source itself, a lz4 Doc of its own.
    assert words(image, "origin") == (int(DocKind.Topic), int(DocStatus.Open), int(DocCodec.Lz4))
    assert words(image, "source") == (int(DocKind.Narrates), int(DocStatus.Open), int(DocCodec.Lz4))
    assert image.doc_text(stored) == source
    # The facts land as RefKind.Doc placements on the imported objects ...
    references = image.handle("DocRole.References")
    for imported in (one, two):
        assert (citation, references, 0, int(RefKind.Doc), 0) in image.get(imported)["refs"]
    # ... and the citation cites the facts it was made from.
    assert (origin, references, 0, int(RefKind.Doc), 0) in image.get(citation)["refs"]
    assert (stored, references, 0, int(RefKind.Doc), 0) in image.get(citation)["refs"]


def test_provenance_cite_records_the_origin_and_citations_only():
    image = provenance_image()
    one = image.handle("one")
    origin = origin_of(image, "cite")
    citation = image.doc_cite(
        0, "citation", "cite", [one], [origin], path="src/main.c", first_line=1, last_line=9, sha256=SHA
    )
    assert image.check() == []
    assert image.doc_locator(citation)["path"] == "src/main.c"
    # cite is the default and stores no source payload: the cited facts are the origin,
    # and nothing in the image holds source text.
    assert [row["doc"] for row in Inspector(image).docs("citation")] == ["origin"]
    assert image.doc_text(citation).startswith("path=")  # the locator, not the code
    docs = [handle for handle in range(image.handle_count) if image.live(handle) and image.type_name(handle) == "Doc"]
    assert sorted(image.path(handle) for handle in docs) == ["citation", "origin"]


def test_provenance_hash_records_content_shas_only():
    image = provenance_image()
    sha_doc = image.doc_hash(0, "sha", "hash", SHA)
    assert image.check() == []
    assert words(image, "sha") == (int(DocKind.Narrates), int(DocStatus.Open), int(DocCodec.Raw))
    assert image.doc_text(sha_doc) == f"tier=hash\nsha256={SHA}"
    # At the floor the tiers above write nothing: no origin record, no citation, no source.
    one = image.handle("one")
    with pytest.raises(DeltaError, match="hash is identity without location"):
        origin_of(image, "hash")
    with pytest.raises(DeltaError, match="hash carries no location"):
        image.doc_cite(0, "citation", "hash", [one], [], path="src/main.c", first_line=1, last_line=1)
    with pytest.raises(DeltaError, match="written only at full"):
        image.doc_source(0, "source", "hash", "int main(void) {}\n")
    assert image.check() == []


def test_the_provenance_floor_is_hash_and_below_it_is_refused():
    image = provenance_image()
    one = image.handle("one")
    before = image.digest(), image.export()
    for tier in ("none", "off", "no", "", "anonymous"):
        with pytest.raises(DeltaError, match="the provenance floor is hash"):
            origin_of(image, tier)
        with pytest.raises(DeltaError, match="the provenance floor is hash"):
            image.doc_hash(0, "sha", tier, SHA)
        with pytest.raises(DeltaError, match="the provenance floor is hash"):
            image.doc_cite(0, "citation", tier, [one], [], path="src/main.c", first_line=1, last_line=1)
        with pytest.raises(DeltaError, match="the provenance floor is hash"):
            image.doc_source(0, "source", tier, "int main(void) {}\n")
    assert (image.digest(), image.export()) == before  # the image did not change


def test_the_tier_word_of_a_fact_matches_the_fact_it_grants():
    image = provenance_image()
    origin = origin_of(image, "full")
    # A hash record is written only at hash: from cite up the hash rides in the citation's locator.
    with pytest.raises(DeltaError, match="written only at hash"):
        image.doc_hash(0, "sha", "full", SHA)
    with pytest.raises(DeltaError, match="written only at hash"):
        image.doc_hash(0, "sha", "cite", SHA)
    # The source itself is written only at full.
    with pytest.raises(DeltaError, match="written only at full"):
        image.doc_source(0, "source", "cite", "int main(void) {}\n", origin=origin)
    assert image.check() == []


def test_one_stored_payload_sits_behind_every_placement():
    image = provenance_image()
    one, two, three = image.handle("one"), image.handle("two"), image.handle("three")
    source = "static int answer = 42; // the one definition\n" * 4
    origin = origin_of(image, "full")
    stored = image.doc_source(0, "source", "full", source, origin=origin)
    for imported in (one, two, three):
        image.doc_place(imported, stored, int(DocRole.References))
    citation = image.doc_cite(
        0,
        "citation",
        "full",
        [one, two, three],
        [origin, stored],
        path="src/answer.c",
        first_line=1,
        last_line=16,
        sha256=SHA,
    )
    assert image.check() == []
    # One stored copy of each payload, however many placements it sits behind.
    assert image.export().count(source) == 1
    assert image.export().count(image.doc_text(citation)) == 1
    assert sorted(row["source"] for row in Inspector(image).placements("citation")) == ["one", "three", "two"]
    # ... and the source payload sits behind the three placements plus the citation that cites it.
    assert sorted(row["source"] for row in Inspector(image).placements("source")) == ["citation", "one", "three", "two"]
    # Dedup: an edge is said once; placing the same doc twice is refused.
    with pytest.raises(DeltaError, match="already recorded"):
        image.doc_place(one, citation, int(DocRole.References))
    with pytest.raises(DeltaError, match="already recorded"):
        image.doc_place(two, stored, int(DocRole.References))
    assert image.check() == []


def test_a_locator_lives_in_the_citing_docs_text_tail():
    image = provenance_image()
    one = image.handle("one")
    origin = origin_of(image, "cite")
    citation = image.doc_cite(
        0, "citation", "cite", [one], [origin], path="src/main.c", first_line=7, last_line=12, sha256=SHA
    )
    # The seam: RefSpec carries role/index/kind/binding and no locator payload; the
    # locator is the citing Doc's text tail, key=value lines.
    assert image.doc_text(citation) == f"path=src/main.c\nlines=7-12\nsha256={SHA}"
    assert image.doc_locator(citation) == {"path": "src/main.c", "first_line": 7, "last_line": 12, "sha256": SHA}
    assert image.doc_permalink(citation) == f"{REPO}/blob/{COMMIT}/src/main.c#L7-L12"
    # The locator survives the text form (parse/export round-trip).
    again = Image.empty()
    again.apply(image.export())
    assert again.doc_text(again.handle("citation")) == image.doc_text(citation)
    assert again.doc_locator(again.handle("citation")) == image.doc_locator(citation)
    assert again.doc_permalink(again.handle("citation")) == image.doc_permalink(citation)
    assert again.digest() == image.digest()


def test_a_locator_is_a_record_and_a_record_only():
    image = provenance_image()
    one = image.handle("one")
    origin = origin_of(image, "cite")
    prose = image.doc_add(0, "prose", "This explains a module, not a range of lines.")
    with pytest.raises(DeltaError, match="key=value"):
        image.doc_locator(prose)
    with pytest.raises(DeltaError, match="locator path"):
        image.doc_cite(0, "nokey", "cite", [one], [origin], path="", first_line=1, last_line=1)
    # A locator names its path and its lines, or it is refused at the write.
    with pytest.raises(DeltaError, match="line range"):
        image.doc_cite(0, "badlines", "cite", [one], [origin], path="src/main.c", first_line=0, last_line=1)
    with pytest.raises(DeltaError, match="line range"):
        image.doc_cite(0, "backwards", "cite", [one], [origin], path="src/main.c", first_line=9, last_line=2)
    with pytest.raises(DeltaError, match="64 lowercase hex digits"):
        image.doc_cite(0, "badsha", "cite", [one], [origin], path="src/main.c", first_line=1, last_line=1, sha256="AB")
    with pytest.raises(DeltaError, match="64 lowercase hex digits"):
        image.doc_hash(0, "sha", "hash", "not-a-sha")
    assert image.check() == []


def test_lift_comment_turns_a_comment_into_a_narrates_doc():
    image = provenance_image()
    one = image.handle("one")
    comment = "The answer, as this code remembers it. " * 4  # compressible: stored lz4 where it pays
    doc = image.lift_comment(one, "docstring", comment)
    assert image.check() == []
    # A Narrates Doc object (lz4), placed (RefKind.Doc) on the target, under its name.
    assert words(image, "one.docstring") == (int(DocKind.Narrates), int(DocStatus.Open), int(DocCodec.Lz4))
    assert image.doc_text(doc) == comment
    assert image.path(doc) == "one.docstring"
    narrates = image.handle("DocRole.Narrates")
    assert (doc, narrates, 0, int(RefKind.Doc), 0) in image.get(one)["refs"]
    # The words are the caller's to choose; the placement follows the role it is given.
    todo = image.lift_comment(one, "todo", "TODO: rename this.", kind=int(DocKind.Todo), role=int(DocRole.References))
    kind, status, codec = words(image, "one.todo")
    assert (kind, status) == (int(DocKind.Todo), int(DocStatus.Open))
    assert codec in (int(DocCodec.Lz4), int(DocCodec.Raw))  # stored where it pays
    assert (todo, image.handle("DocRole.References"), 0, int(RefKind.Doc), 0) in image.get(one)["refs"]
    # An empty comment is not a fact, and the comment survives the text form.
    with pytest.raises(DeltaError, match="not empty"):
        image.lift_comment(one, "empty", "")
    again = Image.empty()
    again.apply(image.export())
    assert again.doc_text(again.handle("one.docstring")) == comment
    assert again.check() == []
