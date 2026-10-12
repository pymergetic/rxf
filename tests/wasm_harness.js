// Runs wasm32 snippets for tests/test_native_wasm.py.
// stdin:  {"module": hex, "calls": [{"fn", "args": [[type, bits]...], "out": bytes}]}
// stdout: [{"status": n, "out": hex}] in the order of the calls.
"use strict";

const OUT = 1024;
const FILL = 0xaa;

function argument(type, text) {
  const bits = BigInt(text);
  const view = new DataView(new ArrayBuffer(8));
  switch (type) {
    case "i32":
      return Number(BigInt.asIntN(32, bits));
    case "i64":
      return BigInt.asIntN(64, bits);
    case "f32":
      view.setUint32(0, Number(BigInt.asUintN(32, bits)), true);
      return view.getFloat32(0, true);
    case "f64":
      view.setBigUint64(0, BigInt.asUintN(64, bits), true);
      return view.getFloat64(0, true);
    default:
      throw new Error(`unknown value type ${type}`);
  }
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", async () => {
  const request = JSON.parse(input);
  const memory = new WebAssembly.Memory({ initial: 2 });
  const bytes = Buffer.from(request.module, "hex");
  const table = new WebAssembly.Table({ element: "anyfunc", initial: 64 });
  const { instance } = await WebAssembly.instantiate(bytes, {
    env: { memory, table },
  });
  const heap = new Uint8Array(memory.buffer);
  const results = request.calls.map((call) => {
    heap.fill(FILL, OUT, OUT + 64);
    const args = call.args.map(([type, bits]) => argument(type, bits));
    if (call.out !== null) args.push(OUT);
    const status = instance.exports[call.fn](...args);
    return {
      status: status >>> 0,
      out: Buffer.from(heap.slice(OUT, OUT + (call.out ?? 0))).toString("hex"),
    };
  });
  process.stdout.write(JSON.stringify(results));
});
