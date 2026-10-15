# 24 — Memory: linked AI memories as graph cells

Status: **design**. Types proposed 2026-10-15; nothing of this is implemented yet. This
document is the ledger for the memory subsystem: how an AI's memories — episodic,
semantic, procedural — are represented as RXF cells, how they are linked, recalled,
consolidated, and forgotten.

## 1. Overview

A complete AI memory system needs nodes (memories), lines (links between memories),
facts on those lines (weight, validity, why), temporal reach, and a forgetting story.
The thesis of this document: **the system needs no new engine and no second graph.**
It is a vocabulary on the cells that already exist:

- **A memory is a cell.** One identity, one cell — the same invariant as everywhere.
- **A link is a reified edge cell** — not a fleeting pointer `a → b`, but its own cell
  with `src`/`dst` references. Only an edge that is a cell can *carry facts* (the
  link-fact decision of docs/23 §6). A pointer-graph would need a side table for edge
  data; a side table is a second heart.
- **Recall is a program** (a run cell, docs/20 S3). Remembering is an action with a
  trace, not a dictionary lookup — the trace is what decay reads later.
- **Nothing is ever destroyed.** The provenance floor (docs/22, docs/23 §5) applies to
  memory first and foremost: forgetting closes a validity interval or supersedes; it
  never deletes.

## 2. The type set

Proposed shape (one muscle language; this is the type register, not an implementation):

```python
# pymergetic/memory/schema.py

MemoryId = int   # cell object id (i2 form: one memory = one cell)
Time     = int   # 64-bit value word (the dual-width policy, docs/23 §6 q1)
Score    = int   # fixed point 0..2^32-1 — no floats in the core

class MemoryKind(StrEnum):
    WORKING    = "working"     # context of the current run (S3 arena)
    EPISODIC   = "episodic"    # what happened
    SEMANTIC   = "semantic"    # what holds
    PROCEDURAL = "procedural"  # how it is done
    AFFECTIVE  = "affective"   # how it was (an evaluation)

class LinkKind(StrEnum):
    ABOUT          = "about"
    PART_OF        = "part_of"
    BEFORE         = "before"          # temporal
    CAUSED_BY      = "caused_by"       # causal
    SUPPORTS       = "supports"        # epistemic
    CONTRADICTS    = "contradicts"
    SUPERSEDES     = "supersedes"      # replacement, not deletion
    DERIVED_FROM   = "derived_from"    # consolidation
    GROUNDED_IN    = "grounded_in"     # anchor to the system
    SIMILAR_TO     = "similar_to"
    RETRIEVED_WITH = "retrieved_with"  # co-recall (associative)

@dataclass(frozen=True)
class Interval:
    valid_from: Time
    valid_to: Time | None        # None = holds until now

@dataclass(frozen=True)
class Fact:                      # link-borne assertion (docs/23 §6)
    key: str                     # "weight", "why", "confidence", "salience"
    value: bytes                 # typed word payload

@dataclass(frozen=True)
class Anchor:                    # what the memory hangs on
    cell: MemoryId | None        # in the graph (named cell)
    locator: str | None          # commit:path:lines (docs/22 form)
    tier: Provenance             # full | cite | hash (the k1 floor)

@dataclass(frozen=True)
class Memory:                    # THE NODE
    id: MemoryId
    kind: MemoryKind
    content: bytes               # the engram payload
    anchors: tuple[Anchor, ...]
    created: Time
    valid: Interval
    provenance: Provenance

@dataclass(frozen=True)
class Link:                      # THE LINE — itself a cell
    id: MemoryId
    src: MemoryId
    dst: MemoryId
    kind: LinkKind
    facts: tuple[Fact, ...]
    valid: Interval

@dataclass(frozen=True)
class Trace:                     # recall trace — decay/salience read THIS
    memory: MemoryId
    at: Time
    query: bytes
    score: Score
```

## 3. The graph mapping

| Memory type | existing graph form |
|---|---|
| `Memory` | named cell under a `mem` root — the same `mp_import_stat` mirror (docs/23 §4.6) |
| `Link` | link cell: `src`/`dst` as refs, `kind` as a word (the `Line` ref kind exists) |
| `Fact` | link-borne word (docs/23 §6) |
| `Anchor` | doc bindings `doc_cite` / `doc_locator` / `doc_hash` — landed (docs/22) |
| `Interval` | two 64-bit value words |
| `Trace` | run-cell trace (recall *is* a run, docs/20 S3) |
| supersession | `delta` compose — a successor carries the closed interval and the `SUPERSEDES` link |

## 4. The programs and faces

```python
remember(kind, content, anchors=...) -> Memory     # pm_memory_remember
link(src, dst, kind, facts=..., valid=...) -> Link # pm_memory_link
recall(query, *, temporal=None, around=None) -> Recall  # pm_memory_recall
consolidate(session) -> tuple[Memory, ...]         # working -> episodic -> semantic
decay() -> None                                    # salience from traces; closes intervals
supersede(old, new) -> Link                        # SUPERSEDES + delta compose
```

Naming: the module is `pymergetic.memory`, the C face `pm_memory_*`. **Not** `util.mem` —
that card is the arena (`impl = c`), a different thing. Lifecycle obeys the C ABI rule:
`pm_memory_init`/`pm_memory_deinit` for the module, `create`/`destroy` for heap objects,
never `fini`. The memory module rides the graph core; it is not a second defining
language of its own (one-defining-lang).

## 5. Retrieval

In honest order of build:

1. **Exact** — handles and facts. Available with the type set alone.
2. **Associative** — weighted link traversal over `Link` cells (the same Dijkstra form
   the world's rail cables use). `RETRIEVED_WITH` edges make co-recall first class.
3. **Temporal** — interval index over `BEFORE`/`AFTER` and `valid`.
4. **Semantic** (embedding similarity) — **refused for now**: a separate card later
   (computation = program, vectors = cells), not smeared into the core type set.

Every `recall` returns scores *and* writes `Trace` cells; retrieval history is the
input of forgetting, so it cannot be free of record.

## 6. Consolidation and forgetting

- **Consolidation** promotes `WORKING` memories (the current run's arena) to
  `EPISODIC` with `DERIVED_FROM` links, and repeated episodic patterns to `SEMANTIC`.
  The session is the unit: `consolidate(session)`.
- **Decay** adjusts a salience fact from `Trace` cells and, when it reaches zero,
  **closes the validity interval**. The cell stays. The provenance floor forbids the
  delete path outright; a "forget" that frees bytes is a refusal, not a feature.
- **Supersession** replaces a belief without lying about history: `supersede(old, new)`
  writes a `SUPERSEDES` link and a delta compose that closes `old.valid`. Contradiction
  that is *not* resolved stays as `CONTRADICTS` with both intervals honest.

## 7. Honest refusals (first build)

| Refused | Why |
|---|---|
| embeddings / semantic similarity | separate card; vectors are computed cells, not core types |
| cross-image or distributed recall | no transport story yet; the SLIP/lwIP card (docs/23 §4.4) is net, not memory |
| destructive forgetting | provenance floor: intervals close, cells stay |
| float scores in the core | fixed-point `Score`; floats live in callers if at all |
| a second storage engine | the graph *is* the store (one-defining-lang) |

## 8. Seat notes

The type set and the Python/C faces are identical on every seat (all-platforms rule);
fills may differ: persistence of `Trace` cells on firmware may be a bounded ring where
the host keeps everything. A missing fill is a stub that fails the prove, not a dark
port.

## 9. Decisions and open questions

Decided 2026-10-15:

- **Edges are cells** (reified link cells carrying facts) — not pointer edges plus a
  side table.
- **Nothing is deleted** — intervals close and `SUPERSEDES` replaces.
- **Recall is a run cell** with a trace; decay reads traces.
- **The core type set is embedding-free** — semantic similarity is a later card.
- **`pymergetic.memory` / `pm_memory_*`**, distinct from the `util.mem` arena.

Open:

- the salience formula and decay constants (they are policy, so they belong in a
  docs/20-style policy statement once the first traces exist);
- where the `mem` root sits in the quad tree (`pm.mem` vs `py.mem`) and what the
  `mp_import_stat` answer for a memory namespace is — module, package, or neither;
- schema evolution for `Fact` keys (who may define keys, and how a key's payload type
  is declared);
- the working-memory budget: the run-cell budget (`RXF_GRAPH_CELL_BYTES`) vs a
  dedicated working arena;
- temporal query shape (`around=` semantics: overlap, containment, or nearest).
