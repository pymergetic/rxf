// A wasm32 machine that stays up between requests, for tests/live_world.py.
// stdin, one JSON object per line; stdout, one JSON object per line:
//   {"cmd": "init", "module": hex, "pages": n, "base": n, "image": base64, "table": {"<index>": "<export>"}}
//   {"cmd": "write", "at": n, "data": base64}
//   {"cmd": "read", "at": n, "size": n}                  -> {"data": base64}
//   {"cmd": "module", "name": n, "module": hex}           an instance over the same memory and table
//   {"cmd": "call", "fn": name, "args": [[type, bits]...], "module": n?} -> {"status": n}
"use strict";

const readline = require("readline");

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

let instance = null;
let heap = null;
let env = null;
const modules = {};

async function handle(request) {
  switch (request.cmd) {
    case "init": {
      const memory = new WebAssembly.Memory({ initial: request.pages });
      const table = new WebAssembly.Table({ element: "anyfunc", initial: 64 });
      env = { memory, table };
      ({ instance } = await WebAssembly.instantiate(
        Buffer.from(request.module, "hex"),
        { env },
      ));
      for (const [index, name] of Object.entries(request.table || {})) {
        table.set(Number(index), instance.exports[name]);
      }
      heap = new Uint8Array(memory.buffer);
      heap.set(Buffer.from(request.image, "base64"), request.base);
      return {};
    }
    case "write":
      heap.set(Buffer.from(request.data, "base64"), request.at);
      return {};
    case "read":
      return {
        data: Buffer.from(
          heap.subarray(request.at, request.at + request.size),
        ).toString("base64"),
      };
    case "module": {
      const { instance: another } = await WebAssembly.instantiate(
        Buffer.from(request.module, "hex"),
        { env },
      );
      modules[request.name] = another;
      return {};
    }
    case "call": {
      const args = request.args.map(([type, bits]) => argument(type, bits));
      const target = request.module ? modules[request.module] : instance;
      return { status: target.exports[request.fn](...args) >>> 0 };
    }
    default:
      throw new Error(`unknown command ${request.cmd}`);
  }
}

const lines = readline.createInterface({ input: process.stdin });
let queue = Promise.resolve();
lines.on("line", (line) => {
  queue = queue.then(async () => {
    let answer;
    try {
      answer = await handle(JSON.parse(line));
    } catch (error) {
      answer = { error: String(error && error.stack ? error.stack : error) };
    }
    process.stdout.write(JSON.stringify(answer) + "\n");
  });
});
