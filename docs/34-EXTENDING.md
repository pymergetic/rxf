# 34 — Extending RXF

The normal path to add functionality to a running world. This is the guide the
[one-arena object model](32-ONE-ARENA-OBJECT-MODEL.md) makes possible: after the
rework, a started RXF is a world that extends itself from inside, and extending
it is a defined sequence of five levels — each with exactly one form. This
document names those forms and where the line sits between *extending* the
world and *changing the substrat* it runs on.

## 1. The build is not the interface

The thesis the whole design aims at: once RXF has started, it is a world that
can be changed from within, and the image is never built again from outside to
gain functionality. That claim is only true when nothing outside the arena
hides meaning. The [one-arena model](32-ONE-ARENA-OBJECT-MODEL.md) delivers
that: even the system tables (`sys/objects`, `sys/functions`, `sys/caps`,
`sys/journal`) are objects in the name tree, so a new capability is a set of
objects and a delta, not a new magic offset and a relink.

Exactly three seams stay outside, and none of them carries world meaning:

1. **The glue** (a few hundred lines of asm/C per arch: reset vector, VBAR/IDT,
   trampoline) — the CPU contract, not a format.
2. **The seat fills** (UART/timer/disk, POSIX, `js.fetch`) — the hardware line,
   not the world. They dock at one mechanism: the arena's growth/commit hook
   ([the model](32-ONE-ARENA-OBJECT-MODEL.md) §3.1).
3. **The superblock** (magic + version) — the one thing that is not an object.

Everything else is a delta at run time.

## 2. The five levels, each with one form

### 2.1 Model — what it *is*

A `.rxfd` delta in `starter/`, one file per module: Type, Field, Enumerator and
Role objects. The meaning lives in the graph, never in the encoding ("the graph
is the meaning and stays clear", [the sealed-areas doc](33-SEALED-AND-COMPRESSED-AREAS.md)).
After the one-arena rework this is literal: the system tables are themselves
objects in the name tree, so describing a new kind of thing is adding objects
where a table used to be a meta structure.

Prove: `rxf core object check`, the delta digest.

### 2.2 Behavior — what it *does*

A `.rxfs` program as a Function object — the payload is the slot table of the
compiled body, built by the Seed ([the seed compiler](29-THE-SEED-COMPILER.md)).
Where speed matters, a Leaf in its **one** language next to it (`impl = rs`,
`impl = c`) — never a second implementation so a test run can skip a toolchain.
Provenance as always:

```
Source ──(build)──▶ Code ──(graph_run)──▶ effect
```

every arrow digest-wired; the image carries its own build history
([the self-hosting plan](31-SELF-HOSTING.md) §3).

### 2.3 Capability + seat fill — *where* it runs

The capability is an object in the tree (`io/`, `net/`, `time/`), and the fills
per seat dock at **one** mechanism: the arena's growth/commit hook. One hook
for all seats instead of one provisioning policy per seat — that is what keeps
"the same face everywhere" cheap. A fill that a seat does not have is a stub
that fails the prove, not a dark port.

### 2.4 Host entry — *who* starts it

The image carries one Host Entry per host shape: an entry plus the outermost
capability set ([`test_host_entries`](../tests/test_host_entries.py)). A new
host shape is a Host Entry object, not a new build.

### 2.5 Prove

Every card or face change wires **every** seat in the same change: host C, unix
µPy, the emcc browser seat, BIOS + UEFI firmware, the wasmmod seat. The module
and its Python/C face do not vary per seat; only the fill does. This is the
rule that keeps the five levels honest — a level that only works on one seat is
a level that is not done.

## 3. Landing code: modules, Funcs, words

The shape of the tree ([the model](32-ONE-ARENA-OBJECT-MODEL.md) §2) is the
landing procedure:

- A **module** is a named object in the tree — `pymergetic/`, `io/`, `net/`.
  Adding a module is adding a name, not a path in a format.
- A **function** is a named object whose payload is the slot table (the compiled
  body). Callers hold handles, never addresses.
- **Anonymous bodies** live under their stem with variant names (`__mul_x64`,
  `__mul_asm`): one function, several machine bodies, one per seat.
- **Names are interned** (`sys/syms`); lookup by name is a walk into a module,
  and resolution caches handles. The hot path never walks the tree.

Adding a word to the run-cell machine is the same shape: a Function object with
its slot table, named where the machine looks words up.

## 4. Self-editing is a transaction

`rxf console` is the working example: a `.rxfs` program goes through a link
(`shm:`, `pty:`, `tty:`, `fd:`), the running image compiles it with its **own**
front-end, and a delta installs the Function — no restart, no host compiler in
the path ([`console/cli.py`](../src/pymergetic/rxftk/console/cli.py),
[`test_console`](../tests/test_console.py)).

What makes that safe is not discipline but the model:

- **The journal** makes every install a transaction. An apply that is refused
  rolls back byte-exactly; a crash mid-write leaves exactly the superblock plus
  the committed state of the last commit (the journal work of Wave 7, docs/32
  §3). Two console operations at one world serialize as two intact installs.
- **Handles are `(index, gen)`.** A stale handle fails its check visibly
  instead of corrupting quietly — self-modification is only safe when a hold on
  the past is *structurally* refused.
- **The world knows what it did to itself.** The journal is not a log about the
  world; it is part of it (`sys/journal`), so introspection covers the history
  too.

## 5. Extend vs change the substrat

The litmus test for a change: *does it need a new magic, a new format version,
or an update to a layout mirror?* If yes, it is not extending — it is a format
change and takes the full cutover discipline (every mirror, every seat, the
starter regenerated, the digest pins repinned).

**Extending** (the normal path, no format touched):

- objects, modules, Functions, words into the arena,
- `.rxfd` deltas and `.rxfs` programs,
- capability objects and seat fills,
- Host Entries.

**Changing the substrat** (rare, versioned, fully proven):

- the superblock or the object header,
- the handle shape or the journal granularity,
- the growth/commit hook contract.

The version byte of the superblock is the gate
([the model](32-ONE-ARENA-OBJECT-MODEL.md) §1.1): a format change bumps it and
every reader refuses what it does not know. That is why the normal path can
stay so quiet — the loud path is fenced.

## 6. Related

- [The self-hosting plan](31-SELF-HOSTING.md) — the waves this path lands in.
- [One arena, one object model](32-ONE-ARENA-OBJECT-MODEL.md) — the model this
  path extends.
- [Sealed and compressed areas](33-SEALED-AND-COMPRESSED-AREAS.md) — what may
  be opaque inside the arena (weights, media, corpora) and how it stays clear.
- [The seed compiler](29-THE-SEED-COMPILER.md) — where `.rxfs` bodies come from.
