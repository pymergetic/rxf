# Sealed and compressed areas

This document is the target design for byte stores that are closed to all but the
holders of chosen certificates (sealed) and for byte stores that are stored small
(compressed). Both are envelopes around the same thing an image already carries:
the variable payload of an opaque object, as `CodeBlob` has it since Wave 4b. The
graph stays open in both cases — sealing closes bytes, not meaning. This is a
design for later; it is specified here so the format grows into it instead of
around it.

The design answers five questions:

1. What is an area, and what does a sealed one look like in the object model?
2. How does a recipient certificate map to the ability to read?
3. How is compression layered so that digests and random access keep working?
4. What happens on rotation, revocation, and growth?
5. What does verification check while the area is closed?

---

## 1. Areas

An **area** is a contiguous byte store carried by one or more opaque objects:
the payload of a `CodeBlob` today; the worlds, media, corpora, and model weights
of later waves tomorrow. (The word is about storage here, not about the runtime
capability areas of the [live format](30-NATIVE-PORT.md) — those are unrelated.)

Three principles, in order:

1. **The graph is the meaning and stays clear.** Every area object's metadata —
   its name, type, sizes, digest, codec, recipients — is readable by anyone
   holding the image. What is sealed is only the payload.
2. **The digest names the clear bytes.** `content_digest` is the sha256 of what
   the object *is*, exactly as [`CodeBlob`](29-THE-SEED-COMPILER.md) defines it.
   A compressed or sealed payload is transport, never identity.
3. **Compression and sealing are envelopes, not properties.** The same clear
   bytes, wrapped in one or both; each envelope is additive and independently
   removable.

```
clear bytes  ->  chunks  ->  compress per chunk  ->  seal per chunk
                (areas)      (envelope 1)            (envelope 2)
```

### 1.1 What becomes an area

Everything in an image that is *content* can become one; everything that is a
*claim about content* stays open.

| Content | Compress | Seal | Why |
|---|---|---|---|
| Worlds and 3D content (meshes, scenes, textures) | 2–4x | licensed packs | the largest future consumer |
| User data and [memories](24-MEMORY-SYSTEM.md) | yes | almost always | private data, wrapped to user certificates |
| Models and weights | 1.2–2x | yes | proprietary or personal |
| Corpora and data sets (the rxpy corpus) | 3–6x with a dictionary | rarely | the shared-dictionary case |
| [Deltas](15-DELTAS-AND-XML.md), snapshots, old generations | 3–8x | optional | text; zstd eats it |
| Machine code blobs (docs/29) | 1.5–2.5x | proprietary modules | clear before execution, always |
| Bytecode programs, seed archives | 2–4x | no | `.rxfs` text compresses very well |
| Media (audio, fonts, images) | 1–1.5x | licensed assets | often already compressed: wrap only |
| Firmware and update bundles | yes | yes | sealed to device certificates |
| Logs and journals | archival | sensitive parts | sealed on rotation |
| Distribution packages and network transport | always | paid modules | compressed in flight, sealed at rest |

Stays open by design: the graph (types, semantics, contracts), the digests and
pins, the wrap metadata, the boot path (interpreter, intrinsics, checker), and
the provenance chains — the proof must never sit behind a lock. The rule:
**content may be closed and shrunk; claims about content (meaning, digest,
origin) never are.**

## 2. Threat model

| Case | Protected? | How |
|---|---|---|
| Image copied, store stolen | yes | payloads are sealed; metadata is open by design |
| One recipient's key leaks | that recipient's areas only | the wrapped master key of each area |
| Payload swapped or corrupted | detectable | `content_digest` over the clear bytes |
| Rollback to older sealed payload | detectable | the pin names the stored bytes; generation names the state |
| Sizes and structure observed | no | leaks by design; `pad_count` exists for mitigation |
| A recipient reads the area it was given | no | the recipient holds the key; this is not DRM |
| Compression oracle (CRIME-style) | yes at rest | sizes are static; the risk is interactive only (see §5.3) |

Non-goals: hiding metadata, steganography, DRM against legitimate recipients,
per-chunk recipient granularity.

## 3. The objects

Three types. `AreaChunk` is deliberately the same card as `CodeBlob` (opaque,
fixed part, variable payload) — one shape for every byte store in the image.

### 3.1 `AreaChunk`

One chunk of an area: compressed and/or sealed bytes, named by what it is when
open.

| offset | size | field | meaning |
|---|---|---|---|
| 0x00 | 8 | `byte_count` | length of the stored (wrapped) payload |
| 0x08 | 32 | `content_digest` | sha256 of the **clear** chunk bytes |
| 0x28 | 8 | `clear_size` | length of the clear chunk bytes |
| 0x30 | 2 | `codec` | `Codec.Stored`, `Codec.Zstd` |
| 0x32 | 2 | `seal` | `Seal.None`, `Seal.Aead` |
| 0x34 | 4 | (pad, zero) | |

### 3.2 `CompressedArea`

The parent of its chunks. Its children *are* the index: seek and verify without
a side table.

| offset | size | field | meaning |
|---|---|---|---|
| 0x00 | 8 | `chunk_size` | logical (clear) bytes per chunk, 1 MiB default |
| 0x08 | 8 | `clear_size` | length of the whole clear stream |
| 0x10 | 32 | `content_digest` | sha256 of the whole clear stream |
| 0x30 | 8 | `dict` | optional Ref to a shared dictionary object |

Children: `AreaChunk`, one per chunk, in order (`rxf:index`).

### 3.3 `SealedArea`

A `CompressedArea` whose chunks are sealed, plus the wraps that name who may
open them. It carries one extra fixed field and its wraps as children:

| offset | size | field | meaning |
|---|---|---|---|
| 0x00 | 8 | `chunk_size` | as above |
| 0x08 | 8 | `clear_size` | as above |
| 0x10 | 32 | `content_digest` | as above (of the clear stream) |
| 0x30 | 8 | `dict` | as above |
| 0x38 | 2 | `suite` | `Suite.HpkeX25519HkdfSha256ChaCha20Poly1305` |
| 0x3a | 2 | (pad, zero) | |
| 0x3c | 4 | `pad_count` | clear padding at the end of the stream |

Children: `AreaChunk` (the sealed chunks) and `KeyWrap` (one per recipient).

```xml
<SealedArea name="worlds.village" chunk_size="1048576" clear_size="100663296"
            content_digest="3a7f…" suite="Suite.HpkeX25519HkdfSha256ChaCha20Poly1305">
  <KeyWrap name="wrap.firmware-signing" recipient="@certs.firmware_signing" … />
  <KeyWrap name="wrap.field-service" recipient="@certs.field_service" … />
  <AreaChunk name="chunk[0]" byte_count="287134" content_digest="9c22…" … />
  <AreaChunk name="chunk[1]" byte_count="281902" content_digest="41ab…" … />
</SealedArea>
```

### 3.4 `KeyWrap`

| offset | size | field | meaning |
|---|---|---|---|
| 0x00 | 2 | `wrap_suite` | `WrapSuite.HpkeBase` |
| 0x02 | 6 | (pad, zero) | |
| 0x08 | 8 | `recipient` | Ref to the certificate object |

Payload: the HPKE sealed box containing the 32-byte area master key, with the
recipient's SPKI as the binding. One wrap is ~100 bytes.

## 4. Certificates and recipients

- **A certificate is an object in the graph**, carried by an area like everything
  else: named by path (`@certs.firmware_signing`), pinned by digest, admitted by
  quorum under [bootstrap and trust](11-BOOTSTRAP-AND-QUORUM.md). The wrap binds
  the certificate's SPKI; the checker verifies that the ref and the wrap agree.
- **"May read" means "holds the private key of the recipient".** The wrap is an
  HPKE (RFC 9180) sealed box to the recipient's public key. The holder of the
  private key unwraps the master key; nobody else can.
- **Roles and groups.** A group certificate (one key, many holders, or one cert
  per machine with the same key material policy) opens the areas designed for
  that group. One wrap per group certificate suffices — this is how "only the
  holders of certificate X" is expressed.
- **Machine-bound access.** A machine certificate is minted at provisioning
  (see the machine's identity in [unified seat design](28-UNIFIED-SEAT-DESIGN.md));
  fleet areas wrap per fleet cert, not per machine, unless per-machine exclusion
  is the requirement.
- The external, boring equivalent is CMS `EnvelopedData` (S/MIME); ours is
  embedded in the object model because the wraps must be graph objects that the
  checker, the pin, and deltas can see.

**Suites.** `Suite.HpkeX25519HkdfSha256ChaCha20Poly1305` is the only suite at
first. The crypto of the image already carries HMAC (RFC 2104), BLAKE2s
(RFC 7693), HKDF (RFC 5869), Ed25519 (RFC 8032) and SHA-2 (FIPS 180-4). The
missing primitives are X25519 (RFC 7748) and one AEAD (RFC 8439); X25519 shares
the 25519 field arithmetic with the Ed25519 code already there, and
ChaCha20-Poly1305 is small next to both. Both land under `native/crypto/` as
`.rxfs` sources like the rest (docs/29), with the RFC as the spec.

## 5. Key hierarchy, compression, and their order

### 5.1 Hierarchy

```
recipient certificates (admitted by quorum)
        |  HPKE wrap per recipient          (~100 B each, see KeyWrap)
        v
area master key (32 random bytes per area)
        |  HKDF-SHA256, info = "rxf chunk" || index
        v
chunk subkeys (one per chunk; unique per chunk)
```

The per-chunk subkeys make deterministic nonces safe (each key is used once) and
make random access possible with one unwrap: open any chunk with the master key
and its index, no per-chunk wraps, no seeking through a stream.

### 5.2 Compression

- **Codec: zstd (RFC 8878)**, level 3 default, declared per `CompressedArea` and
  per chunk (`Codec.Zstd` vs `Codec.Stored` — a chunk that does not shrink is
  stored, the digest is over the clear bytes either way).
- **Chunking is the index.** 1 MiB clear per chunk default; the children of the
  area are the seek table. This is the SquashFS lesson, reduced to objects: no
  side index to keep consistent, parallel open, per-chunk verification.
- **Shared dictionaries** for many small similar areas (one dictionary object,
  `dict` ref) — the corpus case. A dictionary is itself an area: it can be
  sealed, pinned, and versioned like anything else.
- Small areas (< 64 KiB) get one chunk and skip the machinery.

### 5.3 Order: compress, then seal

Encryption hides redundancy; compressing ciphertext does nothing. The order is
fixed: clear -> chunk -> compress -> seal. The CRIME/BREACH family (compression
oracle with attacker-influenced plaintext and size feedback) is an *interactive*
risk; areas are static at rest. The rule for anything interactive built on this:
never expose per-secret size feedback of a compressed-then-sealed stream.

## 6. Random access, worked example

Read bytes [5 MiB, 5.2 MiB) of a 100 MiB sealed world (chunk_size = 1 MiB):

1. `chunks = [5, 6]` — children by `rxf:index`, no scanning.
2. Unwrap the master key once (one `KeyWrap`, one HPKE open).
3. `subkey = HKDF(master, "rxf chunk" || 5)`; open `chunk[5]`; zstd-decompress
   one chunk; slice. `chunk[6]` only if the range crosses.

| Step | Cost |
|---|---|
| unwrap | one HPKE open (~50 µs host-side) |
| chunk open | one AEAD open (~287 KiB) |
| decompress | one zstd frame (~1 MiB clear) |
| verify | one sha256 over the clear chunk (vs `content_digest`) |

## 7. What stays clear, and what verification checks

Clear by design: names, types, sizes, `content_digest`, `codec`, `suite`, the
recipient refs and wraps, the chunk order. What an observer learns is *that* a
100 MiB world exists for these two certificates, not what is in it.

`rxf binary check` on a closed area checks what does not need keys:

- the chunk count and the children agree; sizes and `byte_count` are consistent;
- every `KeyWrap` names a certificate object of the right type and the wrap is
  well-formed for the declared suite;
- no two wraps name the same recipient; the pad fields are zero where reserved.

What needs keys happens at unlock: `rxf area unlock --with KEYFILE` decrypts to
a work area and checks every chunk against its `content_digest` and the area
against `content_digest` of the clear stream. The pin keeps the clear digests,
so an unlock is verifiable against the pin even when the stored bytes are
untrusted: **the store proves nothing, the digests prove the content.**

## 8. Rotation and revocation

| Event | What changes | Cost |
|---|---|---|
| Add a recipient | one new `KeyWrap` | a one-object delta |
| Recipient leaves, old content may stay with it | one `KeyWrap` removed | cheap, honest: it already read what it read |
| Recipient must lose future access | new master key, chunks re-sealed | the price of revocation |
| Batched revocation | per-epoch master keys | re-seal one epoch at a time |
| Certificate expires | nothing automatically | the checker warns on expired recipients |

Rewrapping is cheap because the wraps are leaves; resealing is expensive because
the chunks change. The honest design says so: **revocation is re-encryption.**
Epoch keys are the batching knob — an area that must revoke fast carries a short
epoch; a world that never revokes carries one key.

## 9. Sizes

| Thing | Size |
|---|---|
| `KeyWrap` | ~100 B per recipient |
| `AreaChunk` overhead | 56 B clear + 28 B sealed (nonce, tag) |
| zstd level 3 on worlds/media | 2–4x typical |
| Sealed 100 MiB world, 2 recipients | ~33–48 MiB stored + ~0.5 KiB wraps |

## 10. Waves (after the self-hosting plan, not in it)

- **A. Compression only.** `AreaChunk` + `CompressedArea`, clear payloads,
  zstd. Prove: roundtrip, seek, digest-over-clear, `Codec.Stored` passthrough.
- **B. Sealing.** X25519 + ChaCha20-Poly1305 as `native/crypto/*.rxfs`, then
  `SealedArea` + `KeyWrap`, HPKE host-side. Prove: unwrap with the right
  certificate, refuse with the wrong one, chunk digests after unlock.
- **C. Rotation and quorum.** Wrap add/remove tooling, epoch keys, checker
  warnings for expired recipients, `check --unlocked` in CI with a test key.

## 11. Open questions

- Padding policy: `pad_count` exists; how much to pad by default is a product
  question, not a format one.
- Where private keys live: never in the image; a machine store per seat
  ([unified seat design](28-UNIFIED-SEAT-DESIGN.md)), a keyfile for hosts.
- Whether an append-only transparency log over area digests (merkle over the
  `content_digest`s, Sigstore-style) is wanted for code transparency.
- One-arena interaction ([one arena](32-ONE-ARENA-OBJECT-MODEL.md)): sealed
  payloads are arena bytes like any other; whether a locked area may be paged in
  without unlocking is a later runtime question.
