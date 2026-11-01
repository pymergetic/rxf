/* web/digest.js -- the browser digest: the canonical bytes the digest names,
 * checked in the browser, and what changed between two of them.
 *
 * The page never draws the seat's bytes on the seat's word alone. The seat
 * answers (length, digest) with EMIT; the browser finds that dump in the memory
 * it holds, hashes what it found ITSELF, and only the bytes that hash to the
 * word are the canonical form the digest names (docs/32 5.4). Bytes that do not
 * hash to their digest are refused instead of drawn.
 *
 * This is the browser's fill of the same read face every seat has - the face
 * `live/client.py`'s `emit()` fills on the host and unix seats and the pump
 * answers on the firmware seats: the canonical bytes and their digest, checked
 * against the bytes (there a DIGEST_MISMATCH is refused; here the bytes are
 * never drawn). No second card and no second truth: the words are the channel's
 * DIGEST/EMIT ops, and the check is over the bytes the browser already holds.
 *
 * The byte diff of two pins says which bytes of the canonical form a change
 * moved - computed over the bytes, never taken on an act's word about itself.
 */
import * as Frames from "./frames.js";
import * as Acts from "./acts.js";

/* The dump lives in the seat's memory beside the live arena; its superblock
 * (docs/32 1) is the only "RXFA" region whose committed size is the length
 * the EMIT answer names. */
const MAGIC = [0x52, 0x58, 0x46, 0x41]; // "RXFA"

/* The runs a diff reports: the first ones, in order - a diff that carried
 * every run would be the dump again. */
const RUNS = 12;

export async function sha256(bytes) {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) return null; // no secure context: the hash cannot be checked here
  const done = await subtle.digest("SHA-256", bytes);
  return Acts.hex(new Uint8Array(done));
}

/** The canonical bytes in `bytes` that hash to `digest`: the words only
 *  nominate candidate windows, the SHA-256 the browser computes itself is what
 *  says which window IS the canonical form the digest names. */
export async function canonical(bytes, length, digest) {
  const found = [];
  for (let at = 0; at + 24 <= bytes.length; at++) {
    if (
      bytes[at] !== MAGIC[0] ||
      bytes[at + 1] !== MAGIC[1] ||
      bytes[at + 2] !== MAGIC[2] ||
      bytes[at + 3] !== MAGIC[3]
    ) {
      continue;
    }
    const view = new DataView(bytes.buffer, bytes.byteOffset + at);
    if (view.getUint32(4, true) !== 1) continue; // the superblock's version
    const committed = Number(view.getBigUint64(8, true));
    /* The EMIT length and the superblock's committed word name the same dump and can
     * disagree by the emit region's own tail - the words only nominate candidates,
     * the SHA-256 is what says which window IS the canonical form the digest names. */
    const windows = [[at, at + length]];
    if (committed > 0 && committed !== length) windows.push([at, at + committed]);
    const over = length - committed;
    if (over > 0 && at - over >= 0) windows.push([at - over, at + committed]);
    for (const [from, to] of windows) {
      if (to > bytes.length || from < 0) continue;
      const form = bytes.subarray(from, to);
      const hashed = await sha256(form);
      if (hashed !== null && hashed !== digest) continue; // not the bytes the word names
      return { bytes: form, verified: hashed !== null };
    }
    found.push(at);
  }
  const seen = `${found.length} region(s), none hashes to the digest`;
  throw new Error(`the canonical bytes are not found: ${seen}`);
}

/** The pin, whole: the word the seat answers (EMIT's length + digest) and the
 *  canonical bytes the browser holds for it - hashed here, against that word. */
export async function view(rxf) {
  const answer = Acts.exchange(rxf, Frames.OP.EMIT);
  if (answer.status !== 0) throw new Acts.Refusal(Frames.OP.EMIT, answer);
  const head = new DataView(answer.payload.buffer, answer.payload.byteOffset);
  const length = Number(head.getBigUint64(0, true));
  const digest = Acts.hex(answer.payload.subarray(8, 40));
  const { bytes, verified } = await canonical(rxf.snapshot(), length, digest);
  return { digest, length, verified, bytes };
}

/** The byte diff of two pins: how many bytes of the canonical form moved and
 *  where. `runs` is the first spans in order, `spans` counts them all, and
 *  `grown` is what the form's length did. */
export function diff(was, now) {
  const common = Math.min(was.length, now.length);
  const runs = [];
  let changed = 0;
  let spans = 0;
  let at = 0;
  while (at < common) {
    if (was[at] === now[at]) {
      at += 1;
      continue;
    }
    const start = at;
    while (at < common && was[at] !== now[at]) at += 1;
    changed += at - start;
    spans += 1;
    if (runs.length < RUNS) runs.push({ at: start, count: at - start });
  }
  return { changed, spans, runs, grown: now.length - was.length };
}
