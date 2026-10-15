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
- **An identity is a cell too** (§9): people, teams, companies, and AI agents are nodes of
  the same graph, so an agent can be an author exactly like a user — no special case.
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

## 9. Identity, authorship, and change tracking

The combined system: identities live in the same cell world as memories, so **authors of
thoughts and participants in changes are nodes too** — a person, a team, a company, or
an AI agent. An agent is not a special case bolted onto "users"; it is an identity of
kind `AGENT` with the same faces.

### The identity types

```python
IdentityId = MemoryId   # an identity IS a cell

class IdentityKind(StrEnum):
    PERSON  = "person"
    TEAM    = "team"
    COMPANY = "company"
    AGENT   = "agent"      # an AI author, exactly like a user
    SYSTEM  = "system"     # the machinery itself (consolidate, decay write too)

class RelationKind(StrEnum):
    AUTHORED_BY = "authored_by"   # a thought/change came from
    MEMBER_OF   = "member_of"     # person/agent -> team -> company
    OPERATED_BY = "operated_by"   # agent -> whoever runs it
    SPEAKS_FOR  = "speaks_for"    # delegation of voice
    REVIEWED_BY = "reviewed_by"

@dataclass(frozen=True)
class Identity:
    id: IdentityId
    kind: IdentityKind
    names: tuple[str, ...]        # display names over time (history in facts)
    created: Time
    provenance: Provenance        # how we know this identity is who it claims

@dataclass(frozen=True)
class AgentSpec:                  # payload of an AGENT identity
    model: str                    # e.g. "xiaomi/mimo-v2.6-pro-ultraspeed"
    version: str
    operator: IdentityId          # who runs it (OPERATED_BY link)

@dataclass(frozen=True)
class Change:                     # a VIEW over the delta chain, not a new store
    id: MemoryId                  # the delta/successor cell
    subject: MemoryId             # what changed (a memory or a link)
    by: IdentityId                # who
    at: Time
    why: bytes | None             # a fact on the change
```

### Two different attributions, kept apart

1. **Intellectual authorship** — whose thought this is. A claim, carried by an
   `AUTHORED_BY` link cell: multiple authors are natural, disputes are honest (two
   conflicting `AUTHORED_BY` links with their own validity intervals), and the claim's
   own provenance tier (docs/22) says how well it is established.
2. **Change attribution** — who physically wrote which delta. Factual, not a claim: the
   storage already is a delta chain (docs: successors are produced by deterministic,
   non-mutating copying compaction in path order). Every delta cell gains `by`
   (identity), `at` (time), and an optional `why` fact. **Change tracking is the delta
   chain plus attribution — not a second history engine.**

The history of a thought is then a single readable structure: the supersession chain
(§6) with an `AUTHORED_BY` link at each step and each step's delta attributed — who
thought what, who changed it, when, and why.

### Identity mapping

| concept | graph form |
|---|---|
| identity | a named cell (`*.id.<name>`) with an `Identity`/`AgentSpec` payload |
| authorship | `AUTHORED_BY` link cell, facts: `at`, `role`, `weight` |
| membership | `MEMBER_OF` link cells (person/agent → team → company) |
| agent provenance | `AgentSpec` payload + `OPERATED_BY` link to its operator |
| change | existing delta/successor cells + `by`/`at`/`why` attribution |
| change history of a thought | supersession chain + per-step attribution |

Collective authorship needs no new machinery: the author is a person or agent, the
collective appears through `MEMBER_OF`; "who speaks for this team" is a `SPEAKS_FOR`
link with its own interval, so delegation expires instead of haunting.

### Honest refusals (identity layer)

| Refused | Why |
|---|---|
| cryptographic signing / non-repudiation | a later card; until then authorship is a *claim* with a provenance tier, and the tier says so |
| impersonation or anomaly detection | refused — we record claims, we do not police them |
| reputation or trust scores | refused for now; a scoring policy would belong in a docs/20-style statement first |
| mutable identity records | the floor holds here first: a renamed person is a new `names` fact and an interval, never an overwrite |

## 10. Decisions and open questions

Decided 2026-10-15:

- **Edges are cells** (reified link cells carrying facts) — not pointer edges plus a
  side table.
- **Nothing is deleted** — intervals close and `SUPERSEDES` replaces.
- **Recall is a run cell** with a trace; decay reads traces.
- **The core type set is embedding-free** — semantic similarity is a later card.
- **`pymergetic.memory` / `pm_memory_*`**, distinct from the `util.mem` arena.
- **Identities are cells** (§9): person, team, company, agent — one kind space, and an
  AI agent is an author exactly like a user.
- **Authorship is a link** (`AUTHORED_BY`, a claim with a provenance tier); **change
  attribution is a fact on the existing delta chain** (`by`/`at`/`why`). Two different
  things, kept apart; `Change` is a view, not a store.

Open:

- the salience formula and decay constants (they are policy, so they belong in a
  docs/20-style policy statement once the first traces exist);
- where the `mem` root sits in the quad tree (`pm.mem` vs `py.mem`) and what the
  `mp_import_stat` answer for a memory namespace is — module, package, or neither;
- schema evolution for `Fact` keys (who may define keys, and how a key's payload type
  is declared);
- the working-memory budget: the run-cell budget (`RXF_GRAPH_CELL_BYTES`) vs a
  dedicated working arena;
- temporal query shape (`around=` semantics: overlap, containment, or nearest);
- identity-layer open points: how agent identities are minted and retired (lifecycle of
  an `AGENT` cell), whether `Change` stays a view or gets a materialized cell for
  audit export, team consensus semantics behind `SPEAKS_FOR`, and when the signing
  card arrives (what it replaces in the provenance tiers).
