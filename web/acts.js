/* web/acts.js -- the console's modifications acts, over the channel's write slice.
 *
 * An act is a small description of a change (rename an object, change a value,
 * add an entry). The delta's BYTES are compiled on the host (web/acts.py behind
 * `POST /compile` - one defining language, never a packer in the page): this
 * module only carries them. It asks the seat for its digest (the base the
 * delta must be applied at), has the host compile the act against that base,
 * stages the bytes through STAGE_RESET/STAGE_WRITE and commits; a commit
 * answers the digest of the result - the word the console prints.
 *
 * The same module serves the page (web/index.html) and the headless chain
 * (tests/web_console.mjs), so what the tests prove is what the page does.
 */
import * as Frames from "./frames.js";

/* The seat's RX ring holds 256 bytes (native/graph.h): a staged chunk plus its
 * 20 bytes of frame must fit it, so a delta travels in chunks of this size. */
export const CHUNK = 192;

let sequence = 0;

/** One request through the seat's own pump: the answer frame (bytes in, bytes out). */
export function exchange(rxf, op, argument = 0, payload = new Uint8Array()) {
  sequence = (sequence + 1) >>> 0;
  rxf.send(Frames.encode(op, sequence, argument, payload));
  rxf.turn();
  const { frames } = Frames.decode(rxf.drain());
  const answer = frames.find((f) => f.sequence === sequence);
  if (!answer) throw new Error(`the image answered no frame to ${op}`);
  return answer;
}

export function hex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function unhex(text) {
  const out = new Uint8Array(text.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(text.slice(2 * i, 2 * i + 2), 16);
  return out;
}

/** The digest the image answers now (docs/32 5.4), as hex. */
export function digest(rxf) {
  const answer = exchange(rxf, Frames.OP.DIGEST);
  if (answer.status !== 0) throw new Refusal(Frames.OP.DIGEST, answer);
  return hex(answer.payload);
}

/** COMPACT (live.h): the arena compacted in place. The words it answers are
 *  the counts and the digest of the compacted image; a seat whose pump does
 *  not carry the op yet answers `unknown op`, which the console shows. */
export function compact(rxf) {
  const answer = exchange(rxf, Frames.OP.COMPACT);
  if (answer.status !== 0) throw new Refusal(Frames.OP.COMPACT, answer);
  const view = new DataView(answer.payload.buffer, answer.payload.byteOffset);
  return {
    handles: view.getBigUint64(0, true),
    body: view.getBigUint64(8, true),
    digest: hex(answer.payload.subarray(16, 48)),
    reclaimed: view.getBigUint64(48, true),
  };
}

/** The empty delta's wire form (live.h's header, no operations): the bytes the
 *  stage actions write when the console stages no change of its own. */
export function emptyDelta() {
  const head = new Uint8Array(64);
  const view = new DataView(head.buffer);
  view.setUint32(0, 0x44465852, true); // "RXFD" (live.h's delta magic)
  view.setUint32(4, 1, true); // version
  return head;
}

/** An answer that is not OK: the status, and for a refused delta the reason. */
export class Refusal extends Error {
  constructor(op, answer) {
    const word = Frames.STATUS[answer.status] ?? `status ${answer.status}`;
    let why = "";
    if (answer.status === 7 && answer.payload.length >= 8) {
      const reason = new DataView(answer.payload.buffer, answer.payload.byteOffset).getUint32(4, true);
      why = `: ${Frames.REASON[reason] ?? `reason ${reason}`}`;
    }
    super(`${word}${why}`);
    this.status = answer.status;
  }
}

/** One visible stage action: the console shows what the seat answered about it. */
function step(onStep, name, detail, failure) {
  if (onStep) onStep({ name, ok: !failure, detail, failure: failure ? failure.message : "" });
}

/** Stage `delta` and commit it with the host's SHA-256 of it; the digest of
 *  the result. `onStep` (optional) sees every stage action - STAGE_RESET, one
 *  STAGE_WRITE per chunk, STAGE_COMMIT - with its result, so the page shows
 *  each action and never fails silently. */
export function commit(rxf, delta, sha256, onStep) {
  let answer = exchange(rxf, Frames.OP.STAGE_RESET);
  if (answer.status !== 0) {
    const refusal = new Refusal(Frames.OP.STAGE_RESET, answer);
    step(onStep, "stage_reset", "the stage is cleared", refusal);
    throw refusal;
  }
  step(onStep, "stage_reset", "the stage is cleared");
  for (let at = 0; at < delta.length; at += CHUNK) {
    const chunk = delta.subarray(at, at + CHUNK);
    answer = exchange(rxf, Frames.OP.STAGE_WRITE, at, chunk);
    if (answer.status !== 0) {
      const refusal = new Refusal(Frames.OP.STAGE_WRITE, answer);
      step(onStep, "stage_write", `${chunk.length} bytes at ${at}`, refusal);
      throw refusal;
    }
    step(onStep, "stage_write", `${chunk.length} bytes at ${at}`);
  }
  const ask = new Uint8Array(36); // the commit's body: length, then SHA-256
  new DataView(ask.buffer).setUint32(0, delta.length, true);
  ask.set(unhex(sha256), 4);
  answer = exchange(rxf, Frames.OP.STAGE_COMMIT, 0, ask);
  if (answer.status !== 0) {
    const refusal = new Refusal(Frames.OP.STAGE_COMMIT, answer);
    step(onStep, "stage_commit", `${delta.length} bytes committed`, refusal);
    throw refusal;
  }
  const digest = hex(answer.payload.subarray(0, 32));
  step(onStep, "stage_commit", `${delta.length} bytes committed, digest ${digest}`);
  return digest;
}

/** The host's compiler: the delta of `act` after the acts already committed. */
export async function compile(base, history, act, from = "") {
  const reply = await fetch(`${from}/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ base, history, act }),
  });
  if (!reply.ok) throw new Error(`the host refuses the act: ${await reply.text()}`);
  return {
    delta: new Uint8Array(await reply.arrayBuffer()),
    sha256: reply.headers.get("X-Rxf-Sha256"),
  };
}

/** One act, whole: its delta compiled on the host, staged, committed. The
 *  answer is {before, after, bytes, delta, sha256}; `history` gains the act
 *  only when the seat took it, so a refused act leaves nothing behind.
 *  `onStep` sees every stage action (web/index.html shows them). */
export async function perform(rxf, history, act, from = "", onStep) {
  const before = digest(rxf);
  const { delta, sha256 } = await compile(before, history, act, from);
  const after = commit(rxf, delta, sha256, onStep);
  history.push(act);
  return { before, after, bytes: delta.length, delta, sha256 };
}

/** The acts the console offers, as the commands that carry them out: name,
 *  what it does, and the act its arguments make. `defaults(history)` are the
 *  arguments of a click in the side panel - chosen so that clicking again does
 *  a new change (the next value, the next name, the way back). */
export const ACTS = [
  {
    name: "rename",
    what: "give a leaf object a new name",
    usage: "rename PATH NAME",
    defaults: (history) => {
      const last = history.findLast((a) => a.act === "rename");
      if (!last) return ["pymergetic.rxf.application.shipping", "freight"];
      const at = last.path.lastIndexOf(".");
      return [`${last.path.slice(0, at)}.${last.name}`, last.path.slice(at + 1)];
    },
    make: ([path, name]) => ({ act: "rename", path, name }),
  },
  {
    name: "set",
    what: "write a value into an object's payload",
    usage: "set PATH VALUE",
    defaults: (history) => [
      "pymergetic.rxf.application.discount",
      String(45 + history.filter((a) => a.act === "set").length),
    ],
    make: ([path, value]) => ({ act: "set", path, value: Number(value) }),
  },
  {
    name: "add",
    what: "a new entry under an existing object",
    usage: "add PARENT NAME [VALUE]",
    defaults: (history) => {
      const n = history.filter((a) => a.act === "add").length;
      return ["pymergetic.rxf.application", `surcharge${n}`, String(7 + n)];
    },
    make: ([parent, name, value = "0"]) => ({ act: "add", parent, name, value: Number(value) }),
  },
];
