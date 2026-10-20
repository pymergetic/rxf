// The browser seat runs the .rxfs stdlib: instantiate the wasm32 module the seed produced and call
// each primitive through the status-out ABI (context, args, argc, out) -> status, the value words
// in linear memory. Parity with the C semantics the stdlib promises (wrapping, shift guard,
// unsigned). Usage: node native/seed/run_wasm.mjs /tmp/rxf_stdlib.wasm
import { readFileSync } from "node:fs";

const bytes = readFileSync(process.argv[2]);
const { instance } = await WebAssembly.instantiate(bytes, {});
const mem = new BigUint64Array(instance.exports.memory.buffer);

// [fn, a, b, want] -- the same vectors the x86_64 parity proof checks.
const MAX = (1n << 64n) - 1n;
const tests = [
  ["wrapping_add_uint64_t", 5n, 3n, 8n],
  ["wrapping_add_uint64_t", MAX, 1n, 0n],            // wrapping
  ["wrapping_subtract_uint64_t", 0n, 1n, MAX],       // wrapping
  ["wrapping_multiply_uint64_t", 3n, 4n, 12n],
  ["wrapping_multiply_uint64_t", MAX, 2n, MAX - 1n], // wrapping
  ["bit_and_uint64_t", 0xf0n, 0x0fn, 0x00n],
  ["bit_or_uint64_t", 0xf0n, 0x0fn, 0xffn],
  ["shift_left_uint64_t", 1n, 4n, 16n],
  ["shift_left_uint64_t", 1n, 64n, 0n],              // count >= 64 -> 0
  ["shift_right_uint64_t", 256n, 4n, 16n],
  ["shift_right_uint64_t", 1n, 64n, 0n],             // count >= 64 -> 0
  ["equal_uint64_t", 7n, 7n, 1n],
  ["equal_uint64_t", 7n, 8n, 0n],
  ["less_uint64_t", 3n, 5n, 1n],
  ["less_uint64_t", 1n << 63n, 1n, 0n],              // unsigned
];

let fails = 0;
let off = 64; // a bump allocator inside the wasm memory (leaves the zero page alone)
for (const [name, a, b, want] of tests) {
  const argsPtr = off;
  const outPtr = off + 16;
  off += 24;
  mem[argsPtr / 8] = a;
  mem[argsPtr / 8 + 1] = b;
  const f = instance.exports[name];
  const status = f(0, argsPtr, 2, outPtr);
  const got = mem[outPtr / 8];
  if (status !== 0 || got !== want) {
    console.log(`FAIL wasm/${name}(${a},${b}) got=${got} want=${want} status=${status}`);
    fails++;
  } else {
    console.log(`ok   wasm/${name}`);
  }
}

console.log(fails ? `\n${fails} WASM FAILURES` : "\nthe .rxfs stdlib runs on the browser seat (wasm32)");
process.exit(fails ? 1 : 0);
