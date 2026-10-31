/* The web console's chain on the wasm32 seat, headless: the acts
 * web/index.html performs at its prompt, over the page's OWN modules (web/rxf.js
 * and web/frames.js -- nothing here re-implements the seat or the wire form).
 *
 * Usage: node tests/web_console.mjs BASE OP PAYLOAD
 *        node tests/web_console.mjs BASE forks
 *        node tests/web_console.mjs BASE acts '[{"act": "set", ...}, ...]'
 *
 * BASE is where web/serve.py serves the page and the seat's artifacts. OP is
 * the name of a frames.js OP (RESOLVE, PING, ...), PAYLOAD its text: stdout is
 * the answer frames as JSON -- the same facts the page prints at its prompt.
 * `forks` walks the chain the snapshot button makes and reports what each
 * generation carries.
 */
import { Rxf } from "../web/rxf.js";
import * as Frames from "../web/frames.js";
import * as Acts from "../web/acts.js";

const [base, which, payload = ""] = process.argv.slice(2);
const rxf = await Rxf.open(
  `${base}/rxf_boot.wasm`,
  `${base}/image_wasm32.bin`,
  `${base}/rxf_leaves.wasm`
);

if (which === "forks") {
  // the chain the snapshot button makes: a fork of a fork of the image, each
  // generation serving one request first the way the screenshot's windows
  // did. What each carries is its own snapshot -- and a fork carries EXACTLY
  // what its parent had (never the growth headroom, never 256 bytes of one
  // thing or another): the sizes must not grow down the chain.
  const sizes = [];
  let at = rxf;
  for (let i = 0; i < 3; i++) {
    at.send(Frames.encode(Frames.OP.RESOLVE, 1, 0, new TextEncoder().encode("honors")));
    at.turn();
    Frames.decode(at.drain());
    sizes.push(at.snapshot().length);
    if (i < 2) {
      at = await Rxf.forkFrom(
        `${base}/rxf_boot.wasm`,
        `${base}/rxf_leaves.wasm`,
        at.snapshot()
      );
    }
  }
  console.log(JSON.stringify({ sizes }));
} else if (which === "acts") {
  // the console's modifications acts, through the page's own web/acts.js: each
  // act is compiled by the host (POST /compile), staged and committed. Reported
  // per act: the digest before, the digest the commit answered, the delta's
  // bytes (so a test can hand the same delta to the host engine) - or the
  // refusal's words. A fork of the result then answers its digest: what the
  // snapshot button carries after a modification.
  const history = [];
  const steps = [];
  for (const act of JSON.parse(payload)) {
    const before = Acts.digest(rxf);
    try {
      const { delta, sha256 } = await Acts.compile(before, history, act, base);
      const after = Acts.commit(rxf, delta, sha256);
      history.push(act);
      steps.push({ before, after, delta: Acts.hex(delta) });
    } catch (error) {
      steps.push({ before, error: error.message });
    }
  }
  const fork = await Rxf.forkFrom(
    `${base}/rxf_boot.wasm`,
    `${base}/rxf_leaves.wasm`,
    rxf.snapshot()
  );
  console.log(JSON.stringify({ steps, fork: Acts.digest(fork) }));
} else if (which === "commit") {
  // the modification acts' first leg (U1 pre-work): stage a delta through the
  // channel and commit it. The delta here is the empty one - the 64-byte wire
  // form of live.h's header with no operations (a frame is bytes; the page's
  // own codec packs it) - so this proves the seat takes staged bytes and
  // answers a commit, whatever the image then says about the delta itself.
  const { createHash } = await import("node:crypto");
  const head = new Uint8Array(64);
  const view = new DataView(head.buffer);
  view.setUint32(0, 0x44465852, true); // "RXFD" (live.h PYMERGETIC_RXF_DELTA_MAGIC)
  view.setUint32(4, 1, true); // version
  view.setUint32(8, 0, true); // op_count
  view.setUint32(12, 0, true); // string_bytes
  view.setUint32(16, 0, true); // flags: no base check
  rxf.send(Frames.encode(Frames.OP.STAGE_RESET, 1, 0));
  rxf.turn();
  rxf.drain();
  rxf.send(Frames.encode(Frames.OP.STAGE_WRITE, 1, 0, head));
  rxf.turn();
  rxf.drain();
  const ask = new Uint8Array(36); // the commit's body: length, then SHA-256
  new DataView(ask.buffer).setUint32(0, head.length, true);
  ask.set(createHash("sha256").update(head).digest(), 4);
  rxf.send(Frames.encode(Frames.OP.STAGE_COMMIT, 1, 0, ask));
  rxf.turn();
  const { frames } = Frames.decode(rxf.drain());
  console.log(
    JSON.stringify(
      frames.map((f) => ({ op: f.op, status: f.status, payload: [...f.payload] }))
    )
  );
} else {
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
}
