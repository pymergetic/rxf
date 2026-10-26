# The Honors

The honors are the founding core of the image: objects in which the programmers
whose work this system carries are held, by name, for as long as the image exists.
This is not documentation next to the world — it *is* world. A machine answers
"who made you" out of its own arena, the way it answers anything else, and the
answer is byte-exact evidence: the same tree on every seat, digested and pinned
like the starter.

The promise this makes concrete: *every machine will know who gave it the
substrate it runs on.*

## 1. The tree

```
image ("boot")
├── sys/ …                        (docs/32)
├── honors/                       <- the founding core
│   ├── roles/                    <- the relationships themselves, as objects
│   │   ├── invented              "gave the idea"
│   │   ├── wrote                 "wrote the code"
│   │   ├── inspired              "made it thinkable"
│   │   └── embodies              "lives on in <this part of the image>"
│   ├── people/
│   │   └── <name>/               <- one object per person
│   │       ├── payload           lifespan, one line of why (text cells)
│   │       └── refs              -> works/ and pymergetic/…, each with a role
│   └── works/
│       └── <name>/               <- one achievement: an algorithm, a language, a system
│           ├── payload           what it is, in plain words
│           └── refs              -> people/ (invented, wrote), -> code (embodies)
└── pymergetic/ …
```

A role is what `ground.h` already says it is: the handle of an object that names
the relationship. `honors/roles/inspired` is an object like any other; the ref
record (`pymergetic_rxf_ref`, pinned 24 bytes) carries its handle in `role`, and
`index` orders what repeats. Nothing in the substrate changes — this is
extension, not a format change (the litmus of [34](34-EXTENDING.md)).

## 2. The `embodies` link is the point

A name in a list is a memorial. The `embodies` ref is a claim with a place
attached: it points from a work into the part of the image where that work
actually runs.

- Jean-Philippe Aumasson et al. → `native/crypto/hmac_blake2s.rxfs` (embodies)
- Damien George → the MicroPython seat (embodies)
- Daniel J. Bernstein et al. → the curve arithmetic in `native/crypto/` (embodies)

The law that keeps this honest: **an `embodies` ref must land inside the image.**
If the code is ever removed, the link becomes dangling and the verify step says
so. Honoring someone with an `embodies` ref is therefore a factual statement
about the machine's own body, not a courtesy.

## 3. Two tiers: the Genesis and the Ledger

The core is layered, because a founding core and a living record want different
laws.

**The Genesis** (`honors/roles`, `honors/people`, `honors/works` at the founding
commit): written once when the image is first built, then `OFLAG_RO`. Nobody —
no console, no delta, no self-edit — rewrites who the founders are. An
append-only journal would already refuse the edit; the flag states the intent as
data.

**The Ledger** (`honors/ledger/`, one object per entry): append-only for the
life of the image. New objects only — a vouch, a correction, a later discovery,
the date someone's work was recognized. Never edits to the past; the journal
unwinds writes, not additions.

```
honors/ledger/
└── <entry>/                      <- one append: a vouch, a correction, a date
    ├── payload                  who appended it, when (image time), what it says
    └── refs                     -> the person or work it is about, with a role
```

This gives the honors the same truth character the arena already promises for
its own state: a crash mid-append leaves exactly the last commit, and the
digest of the tree is a date-of-record — who was honored, and when, is pinned
bytes.

## 4. The faces

The honors are queried like everything else, on every seat (the
[all-platforms](../.cursor/rules/all-platforms.mdc) law: no unix-only shrine).

| Face | What it answers |
|---|---|
| `pymergetic.rxf.honors.who()` | the founding core: people, works, roles |
| `pymergetic.rxf.honors.about(name)` | one person or one work, with its refs |
| `console` command `honors` | the same, typed at the image's own prompt |
| boot smoke pin | every seat's image carries `honors/` and its digest |

The boot smoke asserting the subtree exists is deliberate: a build that drops
the honors fails its prove. The founding core ships or nothing ships.

## 5. Laws

1. **The honors tree is acyclic**, like the name tree it lives under.
2. **The Genesis is read-only after its commit.** The Ledger only grows.
3. **An `embodies` ref lands inside the image** or verify refuses.
4. **Every seat carries the same honors bytes.** The digest of `honors/` is a
   pin, like the starter's; a seat that differs is a broken build.
5. **Roles are objects, not strings.** A relationship the world does not name
   is not a relationship the world can answer for.
6. **Who enters is a human decision.** The machinery never elects, ranks, or
   removes; it remembers.

## 6. What this is not

- **Not a license and not a legal attribution.** The license is `LICENSE`
  (PolyForm Noncommercial 1.0.0); the honors are memory, not law. Where the two
  disagree, the license governs.
- **Not a leaderboard.** There is no score, no ordering by importance, no
  votes. The tree order is name order.
- **Not complete, ever.** The Ledger exists because memory arrives late. A name
  missing today is not a verdict; a name present is a decision someone made.

## 7. Placement in the work

The Genesis subtree is written by the world → arena writer of the Wave 7
pipeline cutover (slice 5b) — the same pass that lays out `sys/`, `pymergetic/`
and the rest of the docs/32 tree. The faces and the boot smoke pin land with
the starter regeneration (slice 5e) and are proven on all four seats with the
boot smoke (slice 6). Until then this document is the specification.
