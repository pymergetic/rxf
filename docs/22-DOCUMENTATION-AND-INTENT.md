# 22 — Documentation and human intent

**Status: design target for the image format.** Every object is documentable, and documentation
is **a graph of recorded facts inside the image**, never a side tree of generated prose:
a native graph of sources, placements and links, topics that carry the narrative, and recorded
facts instead of generated prose. The translation is not a bolt-on: the image is **already** a
native object graph of typed references, so documentation is
the same shape as everything else.

## The three records (as objects)

- **Source** — any object worth documenting: a `Function`, `Code`, `SemanticGraph`, `Module`,
  `Type`, a run cell, an imported foreign body. Its identity is its handle/path (the identity the
  image already has), its kind is its `NodeKind`, and its **original text** is the docstring or
  comment it carries. Nothing is generated from a template.
- **Placement** — *why* a source sits under a topic: `narrates`, `attaches`, or `references`. One
  source may be placed several times **without copying its text** (a reference, not a duplicate).
  This is the space win that comes *before* compression.
- **Link** — topic-to-topic relations: `parent` (acyclic containment) and `related` (see-also; may
  cross owners). Both are references.

A wrong docstring is a **wrong fact**: fixing it means editing the owning object, not a docs file.

## Topics are objects, docstrings are truth

- A **Topic** is an ordinary object (like `Type` and `Field` are) typed by a built-in `Doc` type. A
  topic carries a real docstring — the narrative — and links `parent`/`related` to other topics.
- An object **attaches** to a topic by a reference with a placement role. Membership is **editorial,
  not proof**: attaching a function to a topic does not claim it calls anything. The image records
  the placement honestly instead of inventing a call graph.
- The **passive** convention: an object's own docstring is a topic of its own without an explicit
  attach. A docstring the code already carries is a fact by default.

## Where the text lives (and why not the header)

- **Not in the 64-byte header.** The cell header is load-bearing (type, parent, name, flags,
  generation, index); adding a doc field breaks the ABI for every existing image and every seat.
  Doc text is the **payload of a `Doc` object** referenced from the documented object.
- The doc payload carries a **codec flag** (`raw` | `lz4`), default compressed where it pays —
  see "The payload codec" below. Placement dedup comes first (no text copies), the codec shrinks
  the single copy second. Do not reach for compression to solve a duplication problem that
  references already solve.

## Import path (comments survive)

The importer reads **LLVM IR**, and `clang -S -emit-llvm` eats comments before we ever see them.
To capture human intent the importer needs the **source beside the IR**:

1. compile with debug info (`-g`) so `!DILocation` / `DIFile` give a source map;
2. read the original file and lift the comments — those that precede a definition **and**
   free-floating ones (a file comment places on the module it documents);
3. create a `Doc` object per captured comment (`DocKind.Todo` for TODO/FIXME, so they stay
   manageable), place it on the imported object (`narrates`), and record the placement with the
   provenance (file + line) the debug info gives.

No source map ⇒ the import **refuses** to invent docs (honest refusal, not a guess). A foreign body
imported this way keeps its intent **inside the object**, so the original `.c`/`.rs` can be discarded
and the image is still documentable and developable — the self-build story, applied to intent.

## Policy is documentation: S1–S11 are topics

The execution policy (docs/20, S1–S11) is currently text: markdown headings, C constants in
`smp_*.h`/`ground.h`, and anti-drift pins that compare strings across three places. With the doc
model the policy becomes **one cast**:

- Each policy statement (S1 the run payload, S5 the seal before the CAS handoff, S7 no raw
  pointer outlives a step, ...) is a **topic object** in the image — e.g. a child of
  `pymergetic.rxf.docs.execution`, its docstring the policy text, `DocKind.Topic`.
- The code that **implements** a policy carries a placement on that topic (`attaches`): the seal
  function places on S5, the claim CAS on S4, `mp_handle_pending`'s boundary on S11. The image
  can then answer "which objects implement S5?" as a graph query, not a grep.
- `docs/20` stays the human-readable rendering; the image is the truth. The pins that today
  compare strings (tutor FACTS ↔ C ↔ docs/20) gain a fourth anchor — the topic object — and
  become a graph fact: the tutor's `data-cite="S5"` chip is a reference to the topic, and the
  anti-drift check is "the topic exists, its text is pinned, and its placements cover the
  implementing objects".
- The run-cell machine documents itself in its own arena: the policy topics are ordinary cells
  the machine steps on. That is the self-description story (docs/16) applied to the policy.

This is in scope for the same format work (`DocKind.Topic` already covers it); the policy texts
and placements land when docs/20 is free to move (it is owned by the call-card work until then).

## Narrative, without the import machinery

Registering topics through decorators — with a passive source scan, a collection policy, and a
"discovery is not import" rule — is machinery to survive a stateful import. The image needs none
of it, and saying so is part of the design:

- **The image is the registry.** A topic object exists or it does not; reading is always passive
  (the inspector reads without running). No decorators, no import side effects, no gap between
  "seen in the tree" and "registered".
- **The symbol rule is automatic.** References carry handles (`RefKind.Doc`), never name strings —
  linking by symbol, never by name, is the only possible way to link here.
- **Editorial is not proof.** A placement records a declared reference; it is never evidence of a
  runtime dependency, and a placement never implies a call edge — already the rule above.

The honesty rules carry over regardless: an import failure is an error, never
missing documentation (the "no source map → refusal" of the import path is exactly this), and
collection never touches what it must not run.

## Standards are topics too

The normative documents — this spec's siblings (docs/01–22), the authoring rules (docs/12), the
all-platforms and C-ABI workspace rules, S1–S11 above — are documented **by the same mechanism**:
each standard's statement is a topic object (`DocKind.Topic` under `pymergetic.rxf.docs`), and the
artifacts it governs carry placements on it. The meta-rule mirrors the model that documents
itself: the documentation system narrates its own rules first. A standard that nothing places on
is a standard nothing implements — the graph shows that honestly.

## Handbooks are topic trees

A handbook is not a format — it is a **tree of topic objects** in the same graph:

- The **root topic** carries the introduction (compressed payload), and its `RefKind.Doc`
  references are the **table of contents**: chapter topics, in order.
- **Chapters** are topics again; their references point at code topics, other chapters, or the
  standards they realize. Reading order is the reference order.
- **Editions are deltas.** A new edition is a `.rxfd` with new payload bytes and changed
  placements; the root topic's object id never moves. Old editions are not deleted — editions
  are history in the graph, like any object.
- **Authoring**: the existing `docs/*.md` stay the human editing format and a build step lifts
  them into topic payloads (bytes, lz4); docstrings and comments arrive through the import;
  the editor's payload edit + placement edit writes directly. **Reading**: the text face in the
  REPL and the inspector text view show the same decompressed bytes — one manual, every seat.

## The seam story

Documentation is a face of the same link: the inspector renders a topic's placements and the sources
under it, the editor edits a docstring as the fact it is, and the world can render the doc graph as
another district (topics as buildings, placements as cables). One look (docs/21), one vocabulary.

## Acceptance

- Every object can carry a `Doc` reference; the header ABI is unchanged (no `:root`-style fork — one
  model, every seat).
- A delta round-trips a docstring: `rxf:doc` (or a `<rxf:doc>` child) becomes a `Doc` object on parse
  and is written back on export — comments survive the round-trip for the first time.
- The importer lifts comments from source-with-debug-info into `Doc` objects and refuses without it.
- Placement dedup: the same source placed under several topics copies no text; a pin asserts one
  payload, many placements.
- Honest membership: a placement never implies a call edge; a test pins that the doc graph and the
  call graph stay separate truths.

## The Doc type shape (one type, distinctions in the right places)

`Doc` is a **built-in type** exactly the way `Module` is: an ordinary object typed by the built-in
`Doc` type, its payload described by FIELD objects under the `Doc` TYPE (the pattern of
`ModuleCategory`/`ModuleFlags`). The payload fields:

- `kind` — the `DocKind` enum (`narrates` | `topic` | `todo` | ...);
- `status` — the state word (for `todo`: `open` | `resolved`; zero otherwise);
- `codec` — the payload codec flag (`raw` | `lz4`);
- the text bytes (the variable tail `rxf:data` carries for a variable type).

**Topic and Todo are not their own classes.** The model distinguishes them where it always does:

- by the **payload field** (`kind`), described by FIELD objects — not by parallel types;
- by **references**: a topic is a Doc object whose references carry `parent` (acyclic containment)
  and `related` (see-also); a placement is a `RefKind.Doc` reference *from* the documented object.
  Relationships live in references, never in strings or side tables.

**NodeKind: DATA, not a new slot — for now.** `Module` has its own NodeKind (19) because the
machine treats modules differently. Nothing in execution treats a doc differently: it is carried,
not run. So Doc objects are ordinary DATA cells, distinguished by their **type word** (the one
every cell already carries — `rxf_smp_arena_alloc(a, type, payload_size, ...)`). If the machine
ever must tell docs apart (a round-trip guarantee, compactor policy), the reserved NodeKind
slot 9 is the natural home — earn it when the machine needs it, not for decoration.

**No fixed handle.** Only root/Type/Field have fixed handles (the allocator and every reader need
those before any lookup works); the Doc type is found by path like every starter type
(`pymergetic.rxf.docs`).

## The payload codec (decided)

The doc payload carries a **codec flag at the same spot**, either/or, **default compressed**:

- Stored is exactly **one** state (one truth): the payload bytes with a codec flag
  (`raw` | `lz4`). LZ4 is the default wherever it pays; a tiny doc may stay `raw`.
- Both states are **reachable**: the object answers a text face (like the projection faces) that
  decompresses on demand. "Both in memory" means *stored compressed + decompressed text view when
  read* — never two stored copies.
- **Search over docs decompresses on demand** (LZ4 on doc sizes is microseconds). There is **no
  parallel index over compressed material**: an index would be a second truth to keep in sync,
  and the model does not keep parallel truths (paths are not kept in a parallel index either).
  If search is ever slow, a **rebuildable cache** is allowed — never a truth.
- Edits go through the text face; install writes back, recompressed.

Placement dedup (one payload, many placements) comes first; the codec shrinks the single copy.

## TODOs are managed, not dead comments

TODO/FIXME comments import as their **own kind** — a `DocKind.Todo` object with a status word
(`open` | `resolved`) beside its text. They are then **actively manageable**: the inspector can
list every open TODO across the image, and the editor can resolve or move one like any other
object state. A TODO is a fact with a state, not a comment that rots.

## Decided design calls (from the review)

1. **Doc is an object**, typed by a built-in `Doc` type with a `DocKind` enum
   (`narrates` | `topic` | `todo` | ...). No header field — no ABI break, doc is addressable.
2. **Placements are their own reference kind, `RefKind.Doc`** — a doc placement is editorial
   (`narrates`/`attaches`/`references` in the role) and never occupies a runtime reference.
   This is the honest card: a doc edge is not a data or call edge, and the format says so.
3. **Compression**: codec flag at the payload, default `lz4`, `raw` allowed; see the payload
   codec section above. Both states reachable, one state stored, search on demand.
4. **Import scope: everything** — comments at definitions *and* free-floating comments; TODOs as
   their own managed kind (above).
