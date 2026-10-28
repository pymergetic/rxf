/* web/rxf.js -- the wasm32 seat in a browser tab.
 *
 * The same three acts boot/wasm_shim.js performs on a machine: instantiate the
 * image, lay its bytes at the map's base, call the entry. The imports are the
 * empty set on purpose (boot/wasm_shim.js: on wasm32 the Uart capability is a
 * FIFO block in linear memory, not a host call), so a tab carries the image
 * exactly the way a machine does.
 *
 * This is the live half of the preview page: nothing here is drawn from a
 * fixture. Every byte the page shows comes out of the instance.
 */

const ANSWER = 0x0000004b4f465852n; // the "RXFOK" word the program answers

export class Rxf {
  static async open(wasmUrl, imageUrl) {
    const [code, image] = await Promise.all([
      fetch(wasmUrl).then((r) => r.arrayBuffer()),
      fetch(imageUrl).then((r) => r.arrayBuffer()),
    ]);
    const { instance } = await WebAssembly.instantiate(code, {});
    return new Rxf(instance, new Uint8Array(image));
  }

  constructor(instance, image) {
    this.instance = instance;
    this.memory = instance.exports.memory;
    this.run = instance.exports.run;

    // The map: seven 32-bit words of layout. Read before the growth fill --
    // memory.grow detaches the buffer and every later view is a fresh one.
    const [base, context, handle, handleCopy, args, argc, out] = new Uint32Array(
      this.memory.buffer,
      instance.exports.map_at(),
      7,
    );
    this.base = base;
    this.context = context;
    this.out = out;

    // The seat's growth fill (docs/32 3.1): grow past the promised room, then
    // lay the image's bytes at the base.
    const promised = BigInt(base + image.length + 65536);
    if (instance.exports.grow(0, promised) !== 1) {
      throw new Error(`grow to ${promised} bytes did not extend the memory`);
    }
    new Uint8Array(this.memory.buffer, base, image.length).set(image);

    // wasm passes the 24-byte handle by address of a copy the caller makes.
    new Uint8Array(this.memory.buffer, handleCopy, 24).set(
      new Uint8Array(this.memory.buffer, handle, 24),
    );
    this.handleCopy = handleCopy;
    this.args = args;
    this.argc = argc;
    this.image = image;
  }

  /** One turn of the image's own program; the word it answers. */
  turn() {
    return this.run(this.context, this.handleCopy, BigInt(this.args), BigInt(this.argc), this.out);
  }

  /** The bytes the program wrote to out -- the "RXFOK" word when it agrees. */
  answer() {
    return new BigUint64Array(this.memory.buffer, this.out, 1)[0];
  }

  /** The UART capability as the console FIFO: drain the TX ring into a string. */
  drain() {
    const fifo = this.instance.exports.fifo_at();
    const ring = new Uint8Array(this.memory.buffer, fifo, 576);
    const words = new Uint32Array(this.memory.buffer, fifo, 4);
    let text = "";
    for (let tail = words[3]; tail !== words[2]; tail++) {
      text += String.fromCharCode(ring[320 + (tail >>> 0) % 256]);
      words[3] = tail + 1;
    }
    return text;
  }

  /** The whole image as it stands in memory right now. */
  snapshot() {
    return new Uint8Array(this.memory.buffer).slice(0);
  }

  /** Fork: a new instance that carries this memory where this one left it. */
  static async forkFrom(wasmUrl, saved) {
    const code = await fetch(wasmUrl).then((r) => r.arrayBuffer());
    const { instance } = await WebAssembly.instantiate(code, {});
    const rxf = Object.create(Rxf.prototype);
    rxf.instance = instance;
    rxf.memory = instance.exports.memory;
    rxf.run = instance.exports.run;

    const [base, context, handle, handleCopy, args, argc, out] = new Uint32Array(
      rxf.memory.buffer,
      instance.exports.map_at(),
      7,
    );
    rxf.base = base;
    rxf.context = context;
    rxf.out = out;
    rxf.handleCopy = handleCopy;
    rxf.args = args;
    rxf.argc = argc;

    // The fork carries the whole memory, so grow to it and lay it back down at
    // zero -- not the image at base: the snapshot is past the point of loading.
    const promised = BigInt(saved.length + 65536);
    if (instance.exports.grow(0, promised) !== 1) {
      throw new Error(`grow to ${promised} bytes did not extend the memory`);
    }
    new Uint8Array(rxf.memory.buffer, 0, saved.length).set(saved);
    rxf.image = saved;
    return rxf;
  }
}
