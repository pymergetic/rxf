// Runs a script of wasm32 snippet calls on one shared memory (tests/test_native_stdlib.py,
// tests/test_graph.py).
// stdin:  {"module": hex, "base": n, "image": hex, "snapshot": n,
//          "table": {"<index>": "<export>"},   the function table the leaves are called through
//          "pages": n,                          pages of memory (4 when absent)
//          "restore": true,                     the image again before every step
//          "digest": [[from, to]...],           the ranges of the image to hash, not to return
//          "steps": [{"fn", "args": [[type, bits]...], "writes": [[address, hex]...],
//                     "read": [address, size]}]}
// stdout: [{"status": n, "memory": hex}] after each call: `snapshot` bytes from `base`; with
//         "digest" instead {"status", "sha256", "out": hex of "read"} over the ranges. A step with
//         "reads": [[address, size]...] gives "outs", the hex of each, without "digest".
"use strict";

const crypto = require("crypto");

function argument(type, text) {
  const bits = BigInt(text);
  switch (type) {
    case "i32":
      return Number(BigInt.asIntN(32, bits));
    case "i64":
      return BigInt.asIntN(64, bits);
    default:
      throw new Error(`unknown value type ${type}`);
  }
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", async () => {
  const request = JSON.parse(input);
  const memory = new WebAssembly.Memory({ initial: request.pages || 4 });
  const table = new WebAssembly.Table({ element: "anyfunc", initial: 64 });
  const { instance } = await WebAssembly.instantiate(
    Buffer.from(request.module, "hex"),
    { env: { memory, table } },
  );
  for (const [index, name] of Object.entries(request.table || {})) {
    table.set(Number(index), instance.exports[name]);
  }
  const heap = new Uint8Array(memory.buffer);
  const image = Buffer.from(request.image, "hex");
  heap.set(image, request.base);
  const results = request.steps.map((step) => {
    if (request.restore) heap.set(image, request.base);
    for (const [address, hex] of step.writes) {
      heap.set(Buffer.from(hex, "hex"), address);
    }
    const args = step.args.map(([type, bits]) => argument(type, bits));
    const status = instance.exports[step.fn](...args);
    if (request.digest) {
      const hash = crypto.createHash("sha256");
      for (const [from, to] of request.digest) hash.update(heap.subarray(from, to));
      const [at, size] = step.read;
      return {
        status: status >>> 0,
        sha256: hash.digest("hex"),
        out: Buffer.from(heap.slice(at, at + size)).toString("hex"),
      };
    }
    const result = {
      status: status >>> 0,
      memory: Buffer.from(
        heap.slice(request.base, request.base + request.snapshot),
      ).toString("hex"),
    };
    if (step.reads) {
      result.outs = step.reads.map(([at, size]) =>
        Buffer.from(heap.slice(at, at + size)).toString("hex"),
      );
    }
    return result;
  });
  process.stdout.write(JSON.stringify(results));
});
