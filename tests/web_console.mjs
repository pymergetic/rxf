/* The web console's chain on the wasm32 seat, headless: the acts
 * web/index.html performs at its prompt, over the page's OWN modules (web/rxf.js
 * and web/frames.js -- nothing here re-implements the seat or the wire form).
 *
 * Usage: node tests/web_console.mjs BASE OP PAYLOAD
 *
 * BASE is where web/serve.py serves the page and the seat's artifacts, OP the
 * name of a frames.js OP (RESOLVE, PING, ...), PAYLOAD its text. stdout is the
 * answer frames as JSON -- the same facts the page prints at its prompt.
 */
import { Rxf } from "../web/rxf.js";
import * as Frames from "../web/frames.js";

const [base, which, payload = ""] = process.argv.slice(2);
const rxf = await Rxf.open(
  `${base}/rxf_boot.wasm`,
  `${base}/image_wasm32.bin`,
  `${base}/rxf_leaves.wasm`
);
// the page's handler: one frame into the line, one turn of the image's own
// pump, the answer frames out of the drain (bytes in, bytes out)
rxf.send(Frames.encode(Frames.OP[which], 1, 0, new TextEncoder().encode(payload)));
rxf.turn();
const { frames } = Frames.decode(rxf.drain());
console.log(
  JSON.stringify(
    frames.map((f) => ({ op: f.op, status: f.status, payload: [...f.payload] }))
  )
);
