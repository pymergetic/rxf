# 26 — Recipes: how work gets into the object world

This is the cookbook. Each recipe is one walk through a real change, with the commands that
ran, the gates that pinned it, and the honest boundary where it stopped. The first recipe is
the path a body of external source takes into the image — the path the crypto card took.

## Recipe 1: one definition, two seats (the crypto card)

**The shape of the problem.** A primitive (X25519, ChaCha20-Poly1305, AES-GCM, BLAKE2s,
SHA-256, HKDF) needs to run on a host tool *and* inside the image. The tree's rule is **one
defining language**: one implementation, ever. A
Python twin so tests can skip the build, or a C stand-in for a card's own language, is
refused. So the work is not "write it twice" — it is **write it once, seat it twice**.

The card: `cap.crypto` ([25](25-NETWORK-ORCHESTRATION.md) §11, one defining lang (the words
of `native/crypto`), one set of test vectors). Both TLS and WireGuard sit on it.

**Status.** All four steps are in the tree with their gates green. The card's defining
source is the words themselves (`native/crypto/*.rxfs`) and the Rust tree and the import
lane that once measured its shape are retired (Recipe 2, below).

### Step 1 — the card in its language, with its vectors

The implementation lives in the language it is written in: `native/crypto/*.rxfs`, the
programs of the graph words (`tools/native/rxfc.py` compiles them; `native/graph.c` runs
them). The **C ABI is the one face** between the card and everything else
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
.venv/bin/python -m pytest tests/test_crypto_rxfs.py -q     # the words, every machine
.venv/bin/python -m pytest tests/test_crypto_card.py -q     # the vectors, through the ABI
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

### Step 3 — the image seat: the words ARE the seat

The image seat is not a second copy that must be made to agree — it is where the card was
written all along. The words of `native/crypto/*.rxfs` are the programs every machine runs
(`native/graph.h`): the interpreter of `native/graph.c`, and the in-image compiler of
[18](18-NATIVE-SNIPPETS.md) that turns the same words into the native code of the machine
for the calls that need tempo (the C fill hands each program to it at
`pm_crypto_init`; X25519 runs as machine code from then on). Same words, same answer,
whatever executes them.

An earlier path here measured the other route — lowering the retired Rust through
`tools/native/rximport.py` — and its result is the honest note of why the words were
authored instead: 4 of 71 functions imported (`pm_crypto_deinit` and three kernels), and
the 67 refusals were **subset policy**, not gaps (aggregate returns, indirect calls,
relocation globals). The ledger was truthful, but the path to whole primitives led through
growing the subset; the words' language already had every construct the kernels needed.

### Step 4 — retire the scaffolding, keep the record

Once the words answer the vectors on every seat, the scaffolding stops being load-bearing:

- the card's words live in the image (and its modules as `.rxfd` in the tree, like everything
  else — the image is [editable in place](19-SELF-EDITING-PATH.md));
- the provenance record keeps **what was written from where** ([22](22-DOCUMENTATION-AND-INTENT.md))
  — where a source has no published vector (HKDF-BLAKE2s) one triple is pinned and labeled
  "generated once by the implementation"; where a record was quoted from a `.rsp` corpus
  (the NIST CAVP AES-GCM cases), the quote is cited in the test;
- the retired Rust tree and the import lane leave the tree (git keeps history); the vector
  tests stand on the words and stay byte-exact (`tests/test_crypto_rxfs.py`,
  `tests/test_crypto_card.py`);
- the fast seat is emitted from the same words (machine code through
  [18](18-NATIVE-SNIPPETS.md)), never from a second implementation.

The digest pins (`tests/test_starter_delta.py`) catch drift the moment the words change.

### What the recipe is really saying

1. Write the thing **once**, in its language, with published vectors.
2. Freeze **one face** (a C ABI) before any consumer exists.
3. Seat it where it is needed — a binding is a fill, not a copy.
4. Seat it in the image through the words themselves; prove the seats agree.
5. Retire the scaffolding; keep provenance and vectors.

The same recipe covers the TLS card and the WireGuard card ([25](25-NETWORK-ORCHESTRATION.md)
§11): one crypto card, two protocol cards on top, and no second implementation anywhere.

## Recipe 2: retiring `native/crypto/` — done

The Rust tree of the card (`native/crypto/src/*.rs`), its crate files, its vector file, and
the import lane (`tools/native/crypto_import.py`, `tests/test_crypto_import.py`) are out of
the tree; what remains in `native/crypto/` is the card itself — the words, the C fill of the
ABI (`pm_crypto.c`), the header (`include/pm_crypto.h`), and the one build helper
(`tools/native/crypto_build.py`, which now compiles the fill and embeds the words instead of
running cargo).

The honest order of the retirement, and what changed it: the import lane's ledger said 4 of
71 functions import and the 67 refusals were subset policy (sret and aggregate returns, i128
returns, indirect calls, relocation globals, values carried through memory) — truthful, but
the vector-bearing faces sat behind growing that subset. The words of the kernels were then
authored directly in the words' own language (`native/crypto/*.rxfs`), which had every
construct the algorithms needed, and the vectors moved to them byte-exact. The scaffolding
went the day the words passed `tests/test_crypto_card.py` through the ABI — deleting it did
not delete the truth, because the words had become it.

## Recipe 3: the activation seam — *ok means it is written for THIS image*

The wrapper's seam ([17](17-OPERATING-SYSTEM-PATH.md), the quorum shape of
[11](11-BOOTSTRAP-AND-QUORUM.md)) used to be a pass-through: `ok` meant *the wrapper ran*.
The claim it makes now is the one a boot can answer honestly (07's principle: activation
validates, failure changes nothing): the bytes about to run are the bytes the voices spoke
for. The measured shape of that claim, before any code:

* `Image.digest()` (SHA-256 of the canonical bytes) is **not** the digest of the linked image
  a wrapper loads — for the starter the two words differ (7 MB of link output). A signature
  over the semantic words alone could therefore be relinked onto other bytes and still
  verify. The binding had to name the **block**.
* A boot reads no clock and no network (docs/11: policy is typed metadata). Everything the
  check needs must be in three places: the block (the bytes), the cert block (the voices),
  the anchor (the deployment's words).

### Step 1 — one canonical text, five bound words

`certify.Statement` binds `image_digest`, `text_digest` and `block_digest` (the SHA-256 of
the linked image — `binary.package.block_digest`, the word `rxf binary build` reports as
`block_sha256`) to a key id and a stamp. The canonical bytes are pinned in
`certify.schema.canonical` (header `rxf-certify-statement-v2`, six tagged lines) and golden
in `tests/test_certify_schema.py`; the C reconstructs exactly these bytes
(`native/wrap/wrap_activate.c`, `statement_text`). One change of one line breaks the golden
pin and every signature — that is the point of a canonical serialization.

The block word is a property of an *artifact*, not a source tree: the CLI names it
(`--package` / `--block`) and nothing is inferred by hidden linking.

### Step 2 — the words travel in the tail

The statements ride in the cert block of the package's tail (header words `reserved[2..3]`
as `(offset, bytes)`), one fixed record each (`binary.package.CERT_RECORD_BYTES` = 232:
three raw digests, the key id zero-padded to 64, the stamp as one word, the 64-byte
signature). They never sit inside the block a wrapper loads and runs. The Python codec
(`package.cert_block` / `package.statements`) and the C reader parse the *same* bytes — one
wire, two readers.

### Step 3 — the anchor is wrapper content

The trust store becomes `rxf_wrap_trust.bin` beside the package (`binary.builder.trust_block`,
the layout `native/wrap/wrap_trust.h` documents) and is embedded by `wrap_trust.S` the same
way `wrap_package.S` embeds the package: **deployment words in the wrapper, never in the
image** (docs/11). A wrapper with no voices admits nothing to sign and passes only what
carries nothing to check.

### Step 4 — the check, cheap words first

`rxf_activate_check` (the seam, one function, the two lanes meet nowhere else) hashes the
loaded block and, per admitted key, finds its record: fresh against the policy words
(`now`, `max_age`, `skew` — no clock), speaking for these bytes, agreeing with the voices
already credited on what it says about the image — and only then verifies the signature
(`native/wrap/wrap_crypto.c`, RFC 8032). One key counts once however often it speaks; the
quorum is over distinct voices. Named answers (`RXF_WRAP_ACTIVATE_*`), not bare codes.

`tests/test_wrap_activate.py` runs the whole matrix through the probe: the C accepts what
the Python seat signed and refuses every tamper — wrong block, tampered signature, expired
stamp, revoked voice, one key speaking twice, records not whole.

### What the recipe is really saying

1. Ask what the check can **honestly** know at that point, and bind exactly that (here: the
   bytes, not the idea of the bytes).
2. One canonical text, pinned and golden, with a reader in every language that touches it.
3. Facts of the deployment live in the wrapper; facts of the program live in the image.
4. Cheap words refuse first; the expensive one (the signature) is last.
5. Named refusals all the way up — the C's words, the Python exceptions, the CLI statuses.

Honest limits, written down rather than hidden: the C cannot recompute the semantic digests
(`image`, `text`) — it checks the voices agreed on them and that the builder checked them at
build time against the image; only the `block` word is re-derived at boot. And there is no
"session key": the anchor's keys *are* the deployment's words for the lifetime of the
wrapper binary; rotation means rebuilding the wrapper with a new anchor.
