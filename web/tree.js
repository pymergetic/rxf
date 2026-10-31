/* web/tree.js -- the browser-side view of the image the seat carries.
 *
 * The view reads the canonical bytes the DIGEST covers: the channel's write
 * slice dumps them (live.h's emit region) whenever the digest is asked, so
 * what the panes beside the console show IS the state the digest names. The
 * page checks that itself: the SHA-256 of those bytes is computed here and
 * compared with the word the seat answered, and bytes that do not hash to
 * their digest are refused instead of drawn.
 *
 * The parse is the one reader there is (rxf-reader.js, the world's client
 * face, served beside this page): this module never re-implements the arena -
 * it finds the dump in the seat's memory and hands the bytes over.
 */
import * as Frames from "./frames.js";
import * as Acts from "./acts.js";
import { RxfArena } from "./rxf-reader.js";

/* The dump lives in the seat's memory beside the live arena; its superblock
 * (docs/32 1) is the only "RXFA" region whose committed size is the length
 * the EMIT answer names. */
const MAGIC = [0x52, 0x58, 0x46, 0x41]; // "RXFA"

async function sha256(bytes) {
  const subtle = globalThis.crypto && globalThis.crypto.subtle;
  if (!subtle) return null; // no secure context: the hash cannot be checked here
  const done = await subtle.digest("SHA-256", bytes);
  return Acts.hex(new Uint8Array(done));
}

/** The canonical bytes in `bytes` that hash to `digest`, as the arena. */
async function arenaOf(bytes, length, digest) {
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
      const arena = new RxfArena(form);
      return { arena, verified: hashed !== null };
    }
    found.push(at);
  }
  const seen = `${found.length} region(s), none hashes to the digest`;
  throw new Error(`the canonical bytes are not found: ${seen}`);
}

/** The digest and the view of the image the seat carries now: the channel's
 *  EMIT answer (length + digest, one word with DIGEST's) and the arena the
 *  canonical bytes form, refreshed after every act. */
export async function view(rxf) {
  const answer = Acts.exchange(rxf, Frames.OP.EMIT);
  if (answer.status !== 0) throw new Acts.Refusal(Frames.OP.EMIT, answer);
  const head = new DataView(answer.payload.buffer, answer.payload.byteOffset);
  const length = Number(head.getBigUint64(0, true));
  const digest = Acts.hex(answer.payload.subarray(8, 40));
  const { arena, verified } = await arenaOf(rxf.snapshot(), length, digest);
  return { digest, length, verified, arena };
}

/** One row of the panes: the node's words as the canonical bytes spell them. */
function row(arena, handle) {
  const record = arena.cell(handle);
  const size = record === null ? 0 : record.payloadSize - record.head;
  const value =
    record !== null && [1, 2, 4, 8].includes(size) ? arena.payloadUint(record, 0, size) : null;
  return {
    handle,
    name: arena.name(handle),
    path: arena.path(handle),
    size,
    value, // the payload as one little-endian integer, or null
    open: false,
    children: arena.childrenChain(handle),
  };
}

/** The children of one node, as the object list draws them. */
export function children(arena, handle) {
  return arena.childrenChain(handle).map((child) => row(arena, child));
}

/** The tree root: the image's own root node. */
export function root(arena) {
  return row(arena, arena.root);
}
