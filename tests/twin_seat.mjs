/* The write slice of the wasm32 seat, headless: a script of steps through the
 * page's own modules (web/rxf.js, web/frames.js, web/acts.js).
 *
 * Usage: node tests/twin_seat.mjs BASE STEPS
 *
 * BASE is where web/serve.py serves the seat's artifacts; STEPS is a JSON list:
 *   {"act": {...}}   an act of the console: compiled by the host, staged, committed
 *   {"raw": "<hex>"} a delta as bytes (no base check), staged and committed
 *   {"op": N}        one request of op N through the seat's pump
 *   {"digest": true} the digest the seat answers now
 * Stdout is one JSON object per step: what the seat answered (or refused with).
 */
import { createHash } from "node:crypto";
import { Rxf } from "../web/rxf.js";
import * as Acts from "../web/acts.js";

const [base, script] = process.argv.slice(2);
const rxf = await Rxf.open(
  `${base}/rxf_boot.wasm`,
  `${base}/image_wasm32.bin`,
  `${base}/rxf_leaves.wasm`
);
const history = [];
const out = [];
for (const step of JSON.parse(script)) {
  const before = Acts.digest(rxf);
  try {
    if (step.act) {
      const { delta, sha256 } = await Acts.compile(before, history, step.act, base);
      const after = Acts.commit(rxf, delta, sha256);
      history.push(step.act);
      out.push({ before, after, delta: Acts.hex(delta) });
    } else if (step.raw) {
      const delta = Uint8Array.from(Buffer.from(step.raw, "hex"));
      const sha256 = createHash("sha256").update(delta).digest("hex");
      out.push({ before, after: Acts.commit(rxf, delta, sha256) });
    } else if (step.op) {
      const answer = Acts.exchange(rxf, step.op);
      out.push({ before, status: answer.status, payload: Acts.hex(answer.payload) });
    } else {
      out.push({ before });
    }
  } catch (error) {
    out.push({ before, error: error.message });
  }
}
console.log(JSON.stringify(out));
