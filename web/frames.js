/* web/frames.js -- the live protocol's wire form, in the browser.
 *
 * The same bytes src/pymergetic/rxftk/live/frames.py packs: a 16-byte head
 * (magic, op, flags, sequence, argument, length, status) and a CRC32 of the
 * head and body. Nothing here is a translation of the Python into JS - the
 * format is the two structs and the polynomial, and both languages spell
 * those the same way.
 *
 * The console talks this, so what it shows is what a client of the running
 * image sees and not what a page invented.
 */

export const MAGIC = 0x5852;
export const MAX_PAYLOAD = 1024;
export const HEADER_SIZE = 16;
export const TRAILER_SIZE = 4;
export const ANSWER = 0x80;

export const OP = {
  PING: 0x01,
  HELLO: 0x02,
  HEADER_OP: 0x03,
  RESOLVE: 0x04,
  PATH: 0x05,
  GET: 0x06,
  READ: 0x07,
  CHILDREN: 0x08,
  REFS: 0x09,
  DIGEST: 0x20,
  EMIT: 0x21,
  EMIT_READ: 0x22,
};

export const STATUS = {
  0: "ok",
  1: "bad frame",
  2: "unknown op",
  3: "not found",
  4: "bad request",
  5: "no room",
  6: "digest mismatch",
  7: "delta refused",
  8: "image not usable",
  9: "request refused",
};

/* CRC32, the reflected form zlib uses: same polynomial, same table. */
const TABLE = new Uint32Array(256);
for (let n = 0; n < 256; n++) {
  let c = n;
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  TABLE[n] = c >>> 0;
}

export function crc32(bytes) {
  let c = 0xffffffff;
  for (const b of bytes) c = TABLE[(c ^ b) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}

/** One frame on the wire: head, payload, and the CRC of head + payload. */
export function encode(op, sequence, argument = 0, payload = new Uint8Array()) {
  if (payload.length > MAX_PAYLOAD) {
    throw new Error(`a frame carries at most ${MAX_PAYLOAD} bytes`);
  }
  const out = new Uint8Array(HEADER_SIZE + payload.length + TRAILER_SIZE);
  const view = new DataView(out.buffer);
  view.setUint16(0, MAGIC, true);
  view.setUint8(2, op);
  view.setUint8(3, 0);
  view.setUint32(4, sequence >>> 0, true);
  view.setUint32(8, argument >>> 0, true);
  view.setUint16(12, payload.length, true);
  view.setUint16(14, 0, true);
  out.set(payload, HEADER_SIZE);
  view.setUint32(HEADER_SIZE + payload.length, crc32(out.subarray(0, HEADER_SIZE + payload.length)), true);
  return out;
}

/** Every whole frame in `bytes`, plus what is left over. */
export function decode(bytes) {
  const frames = [];
  let at = 0;
  while (bytes.length - at >= HEADER_SIZE + TRAILER_SIZE) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + at);
    if (view.getUint16(0, true) !== MAGIC) {
      at += 1;
      continue;
    }
    const length = view.getUint16(12, true);
    const whole = HEADER_SIZE + length + TRAILER_SIZE;
    if (bytes.length - at < whole) break;
    const body = bytes.subarray(at, at + HEADER_SIZE + length);
    const want = view.getUint32(HEADER_SIZE + length, true);
    if (crc32(body) !== want) {
      at += 1;
      continue;
    }
    frames.push({
      op: view.getUint8(2),
      flags: view.getUint8(3),
      sequence: view.getUint32(4, true),
      argument: view.getUint32(8, true),
      status: view.getUint16(14, true),
      payload: bytes.slice(at + HEADER_SIZE, at + HEADER_SIZE + length),
    });
    at += whole;
  }
  return { frames, rest: bytes.slice(at) };
}
