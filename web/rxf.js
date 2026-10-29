/* web/rxf.js -- the wasm32 seat in a browser tab.
 *
 * The same acts boot/wasm_shim.js performs on a machine: instantiate the
 * image, lay its bytes at the map's base, call the entry. The one import is
 * the function table (boot/wasm_shim.js: on wasm32 a function slot names its
 * leaf by table index, so the host owns the table and fills it from the
 * leaves module), and the Uart capability is a FIFO block in linear memory,
 * not a host call -- so a tab carries the image exactly the way a machine
 * does.
 *
 * This is the live half of the preview page: nothing here is drawn from a
 * fixture. Every byte the page shows comes out of the instance.
 */

const ANSWER = 0x0000004b4f465852n; // the "RXFOK" word the program answers

/** The function table of one seat: the leaves module's exports at their ids
 *  (table.set(id, leaf{id}) -- the fill tests/wasm_steps.js performs). */
async function fillTable(table, leaves, memory) {
  const { instance } = await WebAssembly.instantiate(leaves, {
    env: { memory, table },
  });
  for (const name of Object.keys(instance.exports)) {
    if (!name.startsWith("leaf")) continue;
    const at = Number(name.slice("leaf".length));
    if (Number.isInteger(at)) table.set(at, instance.exports[name]);
  }
}

export class Rxf {
  static async open(wasmUrl, imageUrl, leavesUrl) {
    const [code, image, leaves] = await Promise.all([
      fetch(wasmUrl).then((r) => r.arrayBuffer()),
      fetch(imageUrl).then((r) => r.arrayBuffer()),
      fetch(leavesUrl).then((r) => r.arrayBuffer()),
    ]);
    const table = new WebAssembly.Table({
      element: "anyfunc",
      initial: 64,
      maximum: 4096,
    });
    const { instance } = await WebAssembly.instantiate(code, {
      env: { __indirect_function_table: table },
    });
    const rxf = new Rxf(instance, new Uint8Array(image));
    await fillTable(table, leaves, rxf.memory);
    return rxf;
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
    // lay the image's bytes at the base. `end` is where the image reaches - a
    // fork carries that and no more.
    this.end = base + image.length;
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

  /** The image as it stands in memory - up to where it reaches, not the whole
   *  buffer. The growth fill adds headroom on top; carrying that into a fork
   *  and growing again on top of it made every fork bigger than its parent. */
  snapshot() {
    return new Uint8Array(this.memory.buffer, 0, this.end).slice();
  }

  /** The UART capability as the console FIFO: write a frame into the RX ring. */
  send(bytes) {
    const fifo = this.instance.exports.fifo_at();
    const words = new Uint32Array(this.memory.buffer, fifo, 4);
    const ring = new Uint8Array(this.memory.buffer, fifo, 576);
    const mask = 255;
    for (let i = 0; i < bytes.length; i++) {
      ring[(words[0] + i) & mask] = bytes[i];
    }
    words[0] = words[0] + bytes.length;
    return words[0] - words[3];
  }

  /** Fork: a new instance that carries this memory where this one left it. */
  static async forkFrom(wasmUrl, leavesUrl, saved) {
    const [code, leaves] = await Promise.all([
      fetch(wasmUrl).then((r) => r.arrayBuffer()),
      fetch(leavesUrl).then((r) => r.arrayBuffer()),
    ]);
    const table = new WebAssembly.Table({
      element: "anyfunc",
      initial: 64,
      maximum: 4096,
    });
    const { instance } = await WebAssembly.instantiate(code, {
      env: { __indirect_function_table: table },
    });
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

    // The fork carries the memory where it stood, so grow to exactly that and
    // lay it back down at zero -- not the image at base: the snapshot is past
    // the point of loading. No headroom on top: the snapshot already had it.
    rxf.end = saved.length;
    const promised = BigInt(saved.length);
    if (instance.exports.grow(0, promised) !== 1) {
      throw new Error(`grow to ${promised} bytes did not extend the memory`);
    }
    new Uint8Array(rxf.memory.buffer, 0, saved.length).set(saved);
    rxf.image = saved;
    await fillTable(table, leaves, rxf.memory);
    return rxf;
  }
}
