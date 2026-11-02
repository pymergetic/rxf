/* web/tree.js -- the browser-side view of the image the seat carries.
 *
 * The view reads the canonical bytes the DIGEST covers: the channel's write
 * slice dumps them (live.h's emit region) whenever the digest is asked, so
 * what the panes beside the console show IS the state the digest names. The
 * pin (web/digest.js) checks that itself: the SHA-256 of those bytes is
 * computed in the browser and compared with the word the seat answered, and
 * bytes that do not hash to their digest are refused instead of drawn.
 *
 * The parse is the one reader there is (rxf-reader.js, the world's client
 * face, served beside this page): this module never re-implements the arena -
 * it hands the pinned bytes over and turns what the reader answers into the
 * rows the panes draw.
 */
import { RxfArena } from "./rxf-reader.js";
import * as Digest from "./digest.js";

/** The digest and the view of the image the seat carries now: the pin of
 *  web/digest.js (the EMIT word and the canonical bytes that hash to it),
 *  parsed as the arena the panes draw. */
export async function view(rxf) {
  const seen = await Digest.view(rxf);
  return { ...seen, arena: new RxfArena(seen.bytes) };
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
