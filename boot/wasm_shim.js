/* boot/wasm_shim.js -- the wasm32 host: instantiate, lay the image out, call run.
 *
 * Usage: node boot/wasm_shim.js build/rxf_boot.wasm build/image_wasm32.bin
 *
 * This is the wasm32 seat's glue in the host's language. It does what the
 * firmware trampolines do for their machines: it establishes the world_v7
 * memory layout the entry expects and calls the image's entry the way
 * tests/graph_world.py's node harness calls it -- run(i32, i32, i64, i64, i32)
 * with the handle passed BY ADDRESS of a copy the caller makes (the same
 * HANDLE_COPY step tests/graph_world.py performs). The image arrives as bytes
 * (build/image_wasm32.bin, packed by tools/native/boot_image.py: the carrier
 * rows of world_v7.h with the linked image behind them) and lands at the map's
 * base -- the same act as the harness's emulator.write(BASE, memory(case, ...)).
 *
 * The imports are the empty set on purpose: on wasm32 the Uart capability is
 * not a trap but the FIFO block in linear memory (native/graph.h: rx_head,
 * rx_tail, tx_head, tx_tail, then the rings) and the host is the line. The JS
 * console FIFO below is the drain of that TX ring -- the capability's fill
 * (boot/image_stub/uart_wasm32.c) writes it exactly the way native/uart.c's
 * wasm32 fill does, so the face does not change between seats. Where the block
 * lives is the linked image's business (its capability names it); the module's
 * `fifo_at` resolves it after the image is in place.
 *
 * The drained bytes go to stdout; the smoke's script asserts them byte for
 * byte. The shim itself fails on the two things only it can see: the status
 * run answered and the word the program wrote to out.
 */
"use strict";

const fs = require("fs");

const ANSWER = 0x0000004b4f465852n; // the "RXFOK" word the program must answer

async function main() {
  const path = process.argv[2];
  const imagePath = process.argv[3];
  if (!path || !imagePath) {
    console.error("usage: node wasm_shim.js <rxf_boot.wasm> <image_wasm32.bin>");
    process.exit(2);
  }
  const bytes = fs.readFileSync(path);
  const image = fs.readFileSync(imagePath);
  const { instance } = await WebAssembly.instantiate(bytes, {});
  const run = instance.exports.run;
  const mapAt = instance.exports.map_at();
  const memory = instance.exports.memory;

  // The map: seven 32-bit words of layout (boot/wasm_shim.c's struct). The
  // words are read before the growth fill runs -- memory.grow detaches the
  // buffer, and every view below is made after it.
  const [base, context, handle, handleCopy, args, argc, out] = new Uint32Array(
    memory.buffer,
    mapAt,
    7
  );

  // The seat's growth fill (docs/32 3.1): grow linear memory past the promised
  // room. The image's bytes want base..base + image.length materialized, plus a
  // page of headroom. The arena will hook the same export as its commit fill.
  const grow = instance.exports.grow;
  const promised = BigInt(base + image.length + 65536);
  if (grow(0, promised) !== 1 || memory.buffer.byteLength < Number(promised)) {
    throw new Error(`grow to ${promised} bytes did not extend the memory`);
  }

  // The image's bytes land at the base: the carrier rows first, the linked
  // image at the map's LINKED row -- the layout the entry expects.
  if (base + image.length > memory.buffer.byteLength) {
    throw new Error(`image of ${image.length} bytes does not fit the memory`);
  }
  new Uint8Array(memory.buffer, base, image.length).set(image);

  // The caller's copy of the handle: wasm passes the 24-byte struct by address
  // of a copy, exactly what tests/graph_world.py writes at HANDLE_COPY.
  const copy = new Uint8Array(memory.buffer, handleCopy, 24);
  copy.set(new Uint8Array(memory.buffer, handle, 24));

  const status = run(context, handleCopy, BigInt(args), BigInt(argc), out);

  // The UART capability as the console FIFO: drain the TX ring into stdout.
  const fifo = instance.exports.fifo_at();
  const ring = new Uint8Array(memory.buffer, fifo, 576);
  const words = new Uint32Array(memory.buffer, fifo, 4);
  let text = "";
  for (let tail = words[3]; tail !== words[2]; tail++) {
    text += String.fromCharCode(ring[320 + (tail >>> 0) % 256]);
    words[3] = tail + 1;
  }
  process.stdout.write(text);

  const answer = new BigUint64Array(memory.buffer, out, 1)[0];
  if (status !== 0 || answer !== ANSWER) {
    console.error(
      `wasm shim: FAIL status=${status} out=0x${answer.toString(16)}` +
        ` (wanted status=0 out=0x${ANSWER.toString(16)})`
    );
    process.exit(1);
  }
}

main().catch((failure) => {
  console.error(`wasm shim: FAIL ${failure}`);
  process.exit(1);
});
