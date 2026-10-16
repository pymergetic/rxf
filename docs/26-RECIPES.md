# 26 — Recipes: how work gets into the object world

This is the cookbook. Each recipe is one walk through a real change, with the commands that
ran, the gates that pinned it, and the honest boundary where it stopped. The first recipe is
the path a body of external source takes into the image — the path the crypto card took.

## Recipe 1: one definition, two seats (the crypto card)

**The shape of the problem.** A primitive (X25519, ChaCha20-Poly1305, AES-GCM, BLAKE2s,
SHA-256, HKDF) needs to run on a host tool *and* inside the image. The tree's rule is **one
defining language**: one implementation, ever. A
Python twin so tests can skip `cargo`, or a C stand-in for an RS card, is refused. So the
work is not "write it twice" — it is **write it once, seat it twice**.

The card: `cap.crypto` ([25](25-NETWORK-ORCHESTRATION.md) §11, one defining lang (RS),
one set of test vectors). Both TLS and WireGuard sit on it.

**Status.** Steps 1–2 are in the tree with their gates green. Step 3 landed with its
measured result (below): 4 of 70 functions import and run, 66 refuse by name — the ledger
is pinned in the tests. Step 4 follows and is spelled out as Recipe 2.

### Step 1 — the card in its language, with its vectors

The implementation lives in the language it is written in: `native/crypto/src/*.rs`, a
dependency-free crate. The **C ABI is the one face** between the card and everything else
(`native/crypto/include/pm_crypto.h`): `pm_crypto_init`/`pm_crypto_deinit` as the module
pair (never `fini`), status out, refusal as a negative status. The ABI is frozen before
any consumer exists.

Vectors travel with the card and are pinned in *both* seating paths:

| Primitive | Vector source |
|---|---|
| X25519 | RFC 7748 §5.2, §6.1 |
| ChaCha20-Poly1305 | RFC 8439 §2.8.2, §A.5 (+ §2.5.2, §A.3, §A.4 inner) |
| AES-256-GCM | NIST CAVP GCMVS (encrypt + decrypt, incl. FAIL cases) |
| BLAKE2s-256 | RFC 7693 Appendix B + E; keyed triples labeled as self-pinned |
| SHA-256 | FIPS 180-4 Appendix B.1, B.2 (+ NIST CAVP empty) |
| HKDF-SHA256 | RFC 5869 Appendix A, cases 1–3 |

The rule at this step: **if a vector disagrees with the code, the code is wrong.** Never
bend a vector. Where no published vector exists (HKDF-BLAKE2s), pin one triple and label it
"generated once by this implementation, pinned" — honesty is the provenance.

```
cd native/crypto && cargo test          # the card's own gate
```

### Step 2 — the host seat

The host seat is a fill, like `io.fetch` has POSIX/Metal/js fills: one thin binding
(`src/pymergetic/rxftk/net/crypto.py`, ctypes over the built `libpm_crypto.so`) with typed
Python, a named refusal hierarchy, and a build helper (`tools/native/crypto_build.py`)
that caches on source mtimes. Consumers (`net.tls`, `net.wg`) call the binding and never
a primitive directly — so swapping the seat later changes nothing above it.

```
.venv/bin/python -m pytest tests/test_crypto_card.py -q    # the vectors, through the ABI
```

### Step 3 — the image seat (import, not rewrite)

The same `.rs` sources lower into the object world through the import pipeline that already
exists for C, C++, and Rust ([22](22-DOCUMENTATION-AND-INTENT.md) entrance format):

```
native/crypto/src/*.rs
  → rustc --emit=llvm-ir -C opt-level=0 -C panic=abort     (the compiler does the work)
  → rximport (tools/native/rximport.py)                    (LLVM-IR as it stands → SemanticGraph)
  → compose (tools/native/compose.py)                      (graph → program words)
  → the interpreter (native/graph.c) runs the words        (tests/rxf_native.py harness)
```

The import is mechanical — `rximport` reads the IR, it does not translate a second time —
but the IR subset is minimal and **refuses by name** what it cannot take. The gate is not
"it parsed": the composed words must *run* and answer with the same vector bytes as the
native card. A differential pin (same inputs, both seats, byte-equal outputs) proves one
definition really sits in two places. Functions the subset refuses stay named refusals in
the test — a named gap, never a silent stub.

**Measured on the crypto card (`tools/native/crypto_import.py`, `tests/test_crypto_import.py`).**
4 of 70 functions import and run on the interpreter, byte-exact on published vectors:
`pm_crypto_deinit` (the C-ABI entry, differential-pinned against the native seat) and three
kernels — `quarter_round` (RFC 8439 §2.1.1, §2.2.1), `xtime` (FIPS 197 §4.2.1/§4.2.2),
`blake2s::g` (RFC 7693 §3.1). The other 66 refuse by name, each pinned in a
`REFUSAL_LEDGER` as (face, site, construct): aggregate/sret returns, indirect calls without
a callee, globals without bytes, `getelementptr` of a local, `unreachable` without a handler.
Whole primitives (SHA-256, X25519, HKDF, full BLAKE2s, the AEADs) import nothing yet —
named, not faked. That ledger is the work list for growing the subset, and it is also the
proof that a refusal is a first-class answer.

### Step 4 — retire the scaffolding, keep the record

Once the image seat answers the vectors, the Rust tree stops being load-bearing:

- the card's words live in the image (and its modules as `.rxfd` in the tree, like everything
  else — the image is [editable in place](19-SELF-EDITING-PATH.md));
- the provenance record keeps **what was imported, from where, with which digest** ([22](22-DOCUMENTATION-AND-INTENT.md))
  — the record is the source of truth, not a checkout of upstream;
- `native/crypto/` can then leave the tree (git keeps history); the vector tests move to the
  image words and stay byte-exact;
- the fast seat is emitted from the same words (machine code on the targets of [18](18-NATIVE-SNIPPETS.md)),
  never from a second implementation.

The digest pins (`tests/test_starter_delta.py`) catch drift the moment the words change.

### What the recipe is really saying

1. Write the thing **once**, in its language, with published vectors.
2. Freeze **one face** (a C ABI) before any consumer exists.
3. Seat it where it is needed — a binding is a fill, not a copy.
4. Import it into the image through the standing pipeline; prove the seats agree.
5. Retire the scaffolding; keep provenance and vectors.

The same recipe covers the TLS card and the WireGuard card ([25](25-NETWORK-ORCHESTRATION.md)
§11): one crypto card, two protocol cards on top, and no second implementation anywhere.

## Recipe 2: retiring `native/crypto/` (status)

Not yet done — and the honest order matters. The measured result of Recipe 1 step 3 shows
*why* not: 66 of 70 functions refuse by name, so the image words cannot answer the card's
vectors yet. `native/crypto/` is still the only seat that passes them; deleting it now would
delete the truth. Recipe 2 runs when:

1. the import tool reports every `pm_crypto_*` function as `imported` (or names what refuses,
   and the refused set is empty for the vector-bearing faces);
2. the vector tests run against the image words (same pins, same bytes);
3. `sync.py` regenerates the starter; `tests/test_starter_delta.py` pins are updated **in the
   same change** (a stale pin is a broken claim);
4. the Rust tree is archived out of the working tree; the provenance record cites the digests;
5. the full suite runs green before the commit lands.

Until step 1 reports, `native/crypto/` stays: it is currently the *only* implementation of
the card, and deleting it before the image seat answers the vectors would delete the truth.
