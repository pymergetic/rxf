"""The memory programs over one cell table (docs/24 §4–§6): remember, link, recall,
consolidate, decay, supersede — and no deletion anywhere.

This is the Python face of the memory module (the C face is ``pm_memory_*``, docs/24 §4):
the graph is the store, every cell lives in one id space, and every write is typed and
gated. The programs are exactly the faces of docs/24 §4, with their fills marked:

- ``recall`` scores the exact hits first, spreads over open links (best-path max-product;
  an edge without a ``weight`` word traverses at half weight — policy fill, docs/24 §10
  leaves the constants open), and writes one ``Trace`` cell per hit. Semantic similarity
  is refused for now (docs/24 §7): the core type set is embedding-free.
- ``consolidate(session)`` promotes the session's ``WORKING`` memories to ``EPISODIC``
  with ``DERIVED_FROM`` lines and repeated episodic patterns to ``SEMANTIC``. The session
  is the unit; a working memory already derived from is left alone.
- ``decay`` halves salience, boosts it from the traces since the last decay, and closes
  the validity interval at zero. The cell stays: forgetting is a closed interval, and
  this class offers no deletion face at all.
- ``supersede`` closes the old interval and writes the ``SUPERSEDES`` line whose cell
  carries the ``by``/``at``/``why`` words of the change — the delta attribution of §9,
  from which ``changes`` derives its ``Change`` views.

Temporal fills (docs/24 §10 leaves ``around=`` open): ``temporal=`` selects the memories
whose validity **overlaps** the interval, ``around=`` the memories that hold **at** that
word — the point case of the same overlap rule, one interval concept throughout. A plain
recall asks about now; a temporal recall reaches back into closed intervals honestly.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Final

from .identity import (
    at_fact,
    author_time,
    by_fact,
    change_of,
    fact_of,
    gate_relation,
    gate_writer,
    is_group,
    name_fact,
    why_fact,
)
from .schema import (
    KEY_SALIENCE,
    KEY_WEIGHT,
    SCORE_MAX,
    AgentSpec,
    Anchor,
    Cell,
    Change,
    Fact,
    Hit,
    Identity,
    IdentityId,
    IdentityKind,
    Interval,
    Link,
    LinkKind,
    Memory,
    MemoryId,
    MemoryKind,
    MemoryRefusal,
    Provenance,
    Recall,
    RelationKind,
    Score,
    Time,
    Trace,
    score_add,
    score_bytes,
    score_from_bytes,
    score_halve,
    score_mul,
)

# An edge without a weight word traverses at half weight (policy fill).
HALF_WEIGHT: Final = 1 << 31

# Provenance never overclaims: a derived memory sits at the lowest tier of its sources.
_TIER_RANK = {Provenance.HASH: 0, Provenance.CITE: 1, Provenance.FULL: 2}


def _with_fact(facts: tuple[Fact, ...], fact: Fact) -> tuple[Fact, ...]:
    """The cell's words with `fact` written: its key is one current word, never two."""
    words = [word for word in facts if word.key != fact.key]
    words.append(fact)
    return tuple(words)


class MemoryStore:
    """One graph of memory, link, identity, and trace cells — the programs of docs/24 §4.

    The store is the honest fill for "the graph is the store": one id space, one clock,
    and writes that are refused before a cell exists. Persistence rides the image/delta
    store (a seam of the core, not of this module); what happens here is typed and
    replayable word for word.
    """

    def __init__(self, clock: Time = 0) -> None:
        self._cells: dict[MemoryId, Cell] = {}
        self._next: MemoryId = 1
        self._clock: Time = clock
        self._decayed: MemoryId = 0  # the trace words decay has consumed
        self._system: IdentityId | None = None

    # -- the clock and the cell table ------------------------------------------

    @property
    def clock(self) -> Time:
        """The word writes stamp when they are given no word of their own."""
        return self._clock

    @property
    def cell_count(self) -> int:
        """How many cells the store holds — the pins read this to see what a write stored."""
        return len(self._cells)

    def advance(self, to: Time) -> None:
        """Moves the clock forward — time does not walk backwards."""
        if to < self._clock:
            raise MemoryRefusal(f"the clock stands at {self._clock}: it never moves back to {to}")
        self._clock = to

    def cell(self, cell_id: MemoryId) -> Cell:
        """The cell behind an id — every memory, link, identity, and trace is one."""
        try:
            return self._cells[cell_id]
        except KeyError:
            raise MemoryRefusal(f"no such cell: {cell_id}") from None

    def memory(self, cell_id: MemoryId) -> Memory:
        """The memory behind an id, refused if the cell is a different kind of thing."""
        cell = self.cell(cell_id)
        if not isinstance(cell, Memory):
            raise MemoryRefusal(f"cell {cell_id} is not a memory")
        return cell

    def link_of(self, cell_id: MemoryId) -> Link:
        """The link behind an id — the reified edge cell, facts and all."""
        cell = self.cell(cell_id)
        if not isinstance(cell, Link):
            raise MemoryRefusal(f"cell {cell_id} is not a link")
        return cell

    def identity_of(self, cell_id: MemoryId) -> Identity:
        """The identity behind an id — a person, agent, group, or the machinery."""
        cell = self.cell(cell_id)
        if not isinstance(cell, Identity):
            raise MemoryRefusal(f"cell {cell_id} is not an identity")
        return cell

    def links(
        self,
        kind: LinkKind | RelationKind | None = None,
        *,
        src: MemoryId | None = None,
        dst: MemoryId | None = None,
    ) -> tuple[Link, ...]:
        """The link cells, in cell order, filtered by kind and either end."""
        out = []
        for cell_id in sorted(self._cells):
            line = self._cells[cell_id]
            if not isinstance(line, Link):
                continue
            if kind is not None and line.kind is not kind:
                continue
            if src is not None and line.src != src:
                continue
            if dst is not None and line.dst != dst:
                continue
            out.append(line)
        return tuple(out)

    def traces(self) -> tuple[Trace, ...]:
        """The trace cells in cell order — the retrieval history forgetting reads."""
        out = []
        for cell_id in sorted(self._cells):
            trace = self._cells[cell_id]
            if isinstance(trace, Trace):
                out.append(trace)
        return tuple(out)

    # -- the write faces ------------------------------------------------------

    def remember(
        self,
        kind: MemoryKind,
        content: bytes,
        *,
        anchors: tuple[Anchor, ...] = (),
        facts: tuple[Fact, ...] = (),
        valid: Interval | None = None,
        provenance: Provenance = Provenance.HASH,
        session: MemoryId | None = None,
        at: Time | None = None,
        by: IdentityId | None = None,
        why: bytes | None = None,
    ) -> Memory:
        """A new memory cell: `kind`, its engram `content`, and what it hangs on.

        The write is a delta like any other: the cell gains `by`/`at`/`why` words (§9) and
        a fresh salience word (§6). `session` hangs the memory on its run cell with a
        `PART_OF` line so the session can consolidate it later.
        """
        when = self._clock if at is None else at
        if session is not None:
            self.cell(session)
        writer = self._writer(by)
        words = _with_fact(facts, by_fact(writer))
        words = _with_fact(words, at_fact(when))
        if why is not None:
            words = _with_fact(words, why_fact(why))
        if fact_of(words, KEY_SALIENCE) is None:
            words = _with_fact(words, Fact(KEY_SALIENCE, score_bytes(SCORE_MAX)))
        memory = Memory(
            id=self._new_id(),
            kind=kind,
            content=content,
            anchors=anchors,
            created=when,
            valid=valid if valid is not None else Interval(when),
            provenance=provenance,
            facts=words,
        )
        self._cells[memory.id] = memory
        if session is not None:
            self.link(memory.id, session, LinkKind.PART_OF, valid=Interval(when), at=when)
        return memory

    def link(
        self,
        src: MemoryId,
        dst: MemoryId,
        kind: LinkKind | RelationKind,
        *,
        facts: tuple[Fact, ...] = (),
        valid: Interval | None = None,
        at: Time | None = None,
    ) -> Link:
        """A reified edge cell from `src` to `dst` — the one line type of docs/24.

        Identity relations go through the same door and are gated before the cell exists
        (authorship is personal, §9). A `SUPERSEDES` line is refused here: supersession
        is a program that also closes the old interval, and only `supersede` does both.
        """
        when = self._clock if at is None else at
        if kind is LinkKind.SUPERSEDES:
            raise MemoryRefusal("a supersession goes through supersede(): it closes the old interval too")
        source = self.cell(src)
        target = self.cell(dst)
        if isinstance(kind, RelationKind):
            gate_relation(kind, source, target)
        self._check_words(facts)
        line = Link(
            id=self._new_id(),
            src=src,
            dst=dst,
            kind=kind,
            facts=facts,
            valid=valid if valid is not None else Interval(when),
        )
        self._cells[line.id] = line
        return line

    def identity(
        self,
        kind: IdentityKind,
        names: tuple[str, ...],
        *,
        spec: AgentSpec | None = None,
        provenance: Provenance = Provenance.HASH,
        at: Time | None = None,
    ) -> Identity:
        """A new identity cell: a person, team, company, collective, agent, or the
        machinery itself — one kind space (§9).

        An `AGENT` identity carries its `AgentSpec` and gains the `OPERATED_BY` line to
        its operator; any other kind carrying a spec is refused.
        """
        when = self._clock if at is None else at
        if kind is IdentityKind.AGENT:
            if spec is None:
                raise MemoryRefusal("an agent identity carries its AgentSpec: model, version, operator")
            self.cell(spec.operator)
        elif spec is not None:
            raise MemoryRefusal(f"an AgentSpec is the payload of an AGENT identity, not of a {kind.value}")
        who = Identity(
            id=self._new_id(),
            kind=kind,
            names=names,
            created=when,
            provenance=provenance,
            facts=tuple(name_fact(name, when) for name in names),
            spec=spec,
        )
        self._cells[who.id] = who
        if spec is not None:
            self.link(who.id, spec.operator, RelationKind.OPERATED_BY, valid=Interval(when), at=when)
        return who

    def rename(self, identity: IdentityId, name: str, *, at: Time | None = None) -> Identity:
        """A new display name for an identity: a new name fact and its interval, never an
        overwrite of the old one (the floor holds here first, §9)."""
        when = self._clock if at is None else at
        who = self.identity_of(identity)
        updated = replace(who, names=who.names + (name,), facts=who.facts + (name_fact(name, when),))
        self._cells[identity] = updated
        return updated

    def supersede(
        self,
        old: MemoryId,
        new: MemoryId,
        *,
        by: IdentityId | None = None,
        at: Time | None = None,
        why: bytes | None = None,
    ) -> Link:
        """`new` replaces `old` without lying about history (docs/24 §6).

        Writes the `SUPERSEDES` line (src=new, dst=old) and the delta compose that closes
        `old.valid`; the step cell carries the `by`/`at`/`why` words of the change (§9),
        from which `changes` derives. The old cell stays — nothing is destroyed.
        """
        when = self._clock if at is None else at
        before = self.memory(old)
        self.memory(new)
        if before.valid.closed:
            raise MemoryRefusal(
                f"the memory {old} closed at {before.valid.valid_to}: superseding it again"
                " would rewrite when it stopped holding"
            )
        writer = self._writer(by)
        closed = before.valid.close(when)  # refused before any cell is written
        words = _with_fact(_with_fact((), by_fact(writer)), at_fact(when))
        if why is not None:
            words = _with_fact(words, why_fact(why))
        step = Link(
            id=self._new_id(),
            src=new,
            dst=old,
            kind=LinkKind.SUPERSEDES,
            facts=words,
            valid=Interval(when),
        )
        self._cells[step.id] = step
        self._cells[old] = replace(before, valid=closed)
        return step

    # -- the programs ---------------------------------------------------------

    def recall(
        self,
        query: bytes,
        *,
        temporal: Interval | None = None,
        around: Time | None = None,
    ) -> Recall:
        """The memories that answer `query`, with scores — and a trace per hit (§5).

        Exact hits score full; the associative tier spreads over open links by best path,
        and `RETRIEVED_WITH` lines make co-recall first class. `temporal=` narrows to the
        validity intervals that overlap it, `around=` to those that hold at that word.
        Retrieval history is written as `Trace` cells: decay reads them later.
        """
        if temporal is not None and around is not None:
            raise MemoryRefusal("a recall names one temporal shape: an interval or a moment")
        when = self._clock

        def selected(valid: Interval) -> bool:
            if around is not None:
                return valid.holds(around)
            if temporal is not None:
                return valid.overlaps(temporal)
            return valid.holds(when)

        candidates = {
            cell_id
            for cell_id, cell in self._cells.items()
            if isinstance(cell, Memory) and selected(cell.valid)
        }
        scores: dict[MemoryId, Score] = {}
        for cell_id in candidates:
            if self.memory(cell_id).content == query:
                scores[cell_id] = SCORE_MAX

        adjacency: dict[MemoryId, list[tuple[MemoryId, Score]]] = {cell_id: [] for cell_id in candidates}
        for line in self.links():
            if not selected(line.valid):
                continue
            if line.src not in candidates or line.dst not in candidates:
                continue
            weight = self._weight(line)
            adjacency[line.src].append((line.dst, weight))
            adjacency[line.dst].append((line.src, weight))

        changed = True
        while changed:
            changed = False
            for cell_id, edges in adjacency.items():
                base = scores.get(cell_id, 0)
                if base == 0:
                    continue
                for other, weight in edges:
                    spread = score_mul(base, weight)
                    if spread > scores.get(other, 0):
                        scores[other] = spread
                        changed = True

        hits = tuple(
            Hit(memory=cell_id, score=score)
            for cell_id, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))
            if score > 0
        )
        traces = []
        for hit in hits:
            trace = Trace(memory=hit.memory, at=when, query=query, score=hit.score)
            self._cells[self._new_id()] = trace
            traces.append(trace)
        return Recall(query=query, hits=hits, traces=tuple(traces))

    def consolidate(self, session: MemoryId, *, at: Time | None = None) -> tuple[Memory, ...]:
        """The session is the unit (docs/24 §6): working memory becomes episodic memory,
        and repeated episodic patterns become semantic memory.

        Each promotion is a new cell with a `DERIVED_FROM` line to its sources — the
        working memory stays, and the derived memory never claims more provenance than
        the lowest of its sources. A working memory already derived from is left alone.
        """
        when = self._clock if at is None else at
        self.cell(session)
        created: list[Memory] = []
        for cell_id in sorted(self._cells):
            cell = self._cells[cell_id]
            if not isinstance(cell, Memory) or cell.kind is not MemoryKind.WORKING:
                continue
            if not self._in_session(cell_id, session, when):
                continue
            if self.links(LinkKind.DERIVED_FROM, dst=cell_id):
                continue
            episodic = self.remember(
                MemoryKind.EPISODIC,
                cell.content,
                anchors=cell.anchors,
                provenance=cell.provenance,
                at=when,
                by=self._system_id(),
            )
            self.link(episodic.id, cell_id, LinkKind.DERIVED_FROM, valid=Interval(when), at=when)
            created.append(episodic)

        patterns: dict[bytes, list[Memory]] = {}
        for cell_id in sorted(self._cells):
            cell = self._cells[cell_id]
            if isinstance(cell, Memory) and cell.kind is MemoryKind.EPISODIC:
                patterns.setdefault(cell.content, []).append(cell)
        for content in sorted(patterns):
            members = patterns[content]
            if len(members) < 2:
                continue
            if any(self.links(LinkKind.DERIVED_FROM, dst=member.id) for member in members):
                continue
            semantic = self.remember(
                MemoryKind.SEMANTIC,
                content,
                provenance=_floor(member.provenance for member in members),
                at=when,
                by=self._system_id(),
            )
            for member in members:
                self.link(semantic.id, member.id, LinkKind.DERIVED_FROM, valid=Interval(when), at=when)
            created.append(semantic)
        return tuple(created)

    def decay(self, *, at: Time | None = None) -> None:
        """Salience from the traces, and a closed interval at zero (docs/24 §6).

        Each open memory's salience word halves and takes the score of every trace taken
        since the last decay. At zero the validity interval closes at this word. The cell
        stays: a forget that frees bytes is a refusal, not a feature.
        """
        when = self._clock if at is None else at
        boost: dict[MemoryId, Score] = {}
        for cell_id in sorted(self._cells):
            if cell_id <= self._decayed:
                continue
            cell = self._cells[cell_id]
            if isinstance(cell, Trace):
                boost[cell.memory] = score_add(boost.get(cell.memory, 0), cell.score)
                self._decayed = cell_id
        for cell_id in sorted(self._cells):
            cell = self._cells[cell_id]
            if not isinstance(cell, Memory) or cell.valid.closed:
                continue
            salience = _salience_of(cell)
            salience = score_add(score_halve(salience), boost.get(cell_id, 0))
            words = _with_fact(cell.facts, Fact(KEY_SALIENCE, score_bytes(salience)))
            if salience == 0:
                self._cells[cell_id] = replace(cell, valid=cell.valid.close(when), facts=words)
            else:
                self._cells[cell_id] = replace(cell, facts=words)

    # -- the derived views (§9): derivable things are views, never stores ------

    def changes(self, subject: MemoryId) -> tuple[Change, ...]:
        """The change history of a thought: the supersession chain, oldest step first.

        Each step is a `Change` view decoded from the step cell's `by`/`at`/`why` words —
        change tracking is the delta chain plus attribution, not a second history engine.
        """
        self.cell(subject)
        out: list[Change] = []
        step = subject
        while True:
            successors = self.links(LinkKind.SUPERSEDES, dst=step)
            if not successors:
                return tuple(out)
            line = successors[0]
            out.append(change_of(line.src, step, line))
            step = line.src

    def collective_authors(
        self, thought: MemoryId, collective: IdentityId, at: Time | None = None
    ) -> tuple[IdentityId, ...]:
        """Who of `collective` authored `thought` — the derived collective credit (§9).

        The members holding `AUTHORED_BY` lines to the thought who held `MEMBER_OF`
        membership when the claim was made (its `at` word, or `at` when given). The view
        stores nothing and reconciles nothing: there is one truth, and this reads it.
        """
        group = self.identity_of(collective)
        if not is_group(group.kind):
            raise MemoryRefusal(f"collective credit derives over a team, company, or collective: not a {group.kind.value}")
        out: list[IdentityId] = []
        seen: set[IdentityId] = set()
        for line in self.links(RelationKind.AUTHORED_BY, src=thought):
            author = self.identity_of(line.dst)
            when = author_time(line) if at is None else at
            if not line.valid.holds(when):
                continue
            if not self._member_at(author.id, collective, when):
                continue
            if author.id in seen:
                continue
            seen.add(author.id)
            out.append(author.id)
        return tuple(out)

    def collective_writers(self, subject: MemoryId, collective: IdentityId) -> tuple[IdentityId, ...]:
        """Who of `collective` wrote the changes in `subject`'s history — the change-side
        twin of `collective_authors`, derived from membership intervals the same way."""
        group = self.identity_of(collective)
        if not is_group(group.kind):
            raise MemoryRefusal(f"collective credit derives over a team, company, or collective: not a {group.kind.value}")
        out: list[IdentityId] = []
        seen: set[IdentityId] = set()
        for change in self.changes(subject):
            if not self._member_at(change.by, collective, change.at):
                continue
            if change.by in seen:
                continue
            seen.add(change.by)
            out.append(change.by)
        return tuple(out)

    # -- internals ------------------------------------------------------------

    def _new_id(self) -> MemoryId:
        cell_id = self._next
        self._next += 1
        return cell_id

    def _system_id(self) -> IdentityId:
        """The machinery's own identity — what consolidate and decay write as."""
        if self._system is None:
            self._system = self.identity(IdentityKind.SYSTEM, ("the memory system",)).id
        return self._system

    def _writer(self, by: IdentityId | None) -> IdentityId:
        if by is None:
            return self._system_id()
        who = self.identity_of(by)
        gate_writer(who)
        return who.id

    def _check_words(self, facts: tuple[Fact, ...]) -> None:
        """Typed words stay typed at the write: a `weight` word is a score word."""
        for fact in facts:
            if fact.key == KEY_WEIGHT:
                try:
                    score_from_bytes(fact.value)
                except MemoryRefusal:
                    raise MemoryRefusal("a weight word is a score word: this payload is not one") from None

    def _weight(self, line: Link) -> Score:
        fact = fact_of(line.facts, KEY_WEIGHT)
        return HALF_WEIGHT if fact is None else score_from_bytes(fact.value)

    def _in_session(self, cell_id: MemoryId, session: MemoryId, when: Time) -> bool:
        for line in self.links(LinkKind.PART_OF, src=cell_id, dst=session):
            if line.valid.holds(when):
                return True
        return False

    def _member_at(self, member: IdentityId, group: IdentityId, when: Time) -> bool:
        for line in self.links(RelationKind.MEMBER_OF, src=member, dst=group):
            if line.valid.holds(when):
                return True
        return False


def _salience_of(memory: Memory) -> Score:
    """The memory's salience word: full for a memory that never decayed (policy fill)."""
    fact = fact_of(memory.facts, KEY_SALIENCE)
    return SCORE_MAX if fact is None else score_from_bytes(fact.value)


def _floor(provenances: Iterable[Provenance]) -> Provenance:
    """The lowest tier among sources: a derived memory never overclaims (docs/22)."""
    return min(provenances, key=lambda tier: _TIER_RANK[tier])
