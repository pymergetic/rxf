/* The tutor engine: the run-cell machine, stepped by hand.
 *
 * Plain DOM/SVG only (no WebGPU/WebGL) and no Math.random anywhere: every scenario is a fixed
 * schedule, so a screen always shows the same thing. The facts in FACTS are the page's pinned
 * constants; tests/test_tutor.py checks them against tests/host/smp_*.h, core/ground.h and
 * docs/20-EXECUTION-POLICY.md, and the page's data-pin chips against this same block.
 *
 * Sources of truth (docs/20): S1 the state machine and the seal-before-handoff rule, S3 the
 * ready-ring and its ABA refusal, S4 park/wake and the claim release of the channel pair,
 * S5 ownership and the handoff, S10 determinism, S11 the seam; docs/19 for the safe points
 * and the honest refusals of the interpreter (native/graph.h).
 */
(function (root) {
  "use strict";

  const FACTS = /*FACTS*/{
    "states": { "FREE": 0, "READY": 1, "RUNNING": 2, "PARKED": 3, "TERMINAL": 4 },
    "step": { "AGAIN": 0, "PARK": 1, "DONE": 2, "SELFWAKE": 3 },
    "cells": { "RUN": 1, "DATA": 2, "CHANNEL": 3, "RING": 4, "DELTA": 5, "SCRATCH": 6 },
    "refuse": { "CALL_DEPTH": 17, "UNBOUND": 13, "STALE_GENERATION": 12 },
    "call_depth": 8,
    "cell_bytes": 64,
    "handle_shift": 32,
    "claim_moving": "0x80000000",
    "poison": "0xA5",
    "kind_dead": 254,
    "fnv_prime": "0x100000001B3",
    "fnv_basis": "0xcbf29ce484222325",
    "policy": {
      "S1": "The run-cell state machine",
      "S2": "Suspension is a data property",
      "S3": "The ready-ring protocol",
      "S4": "Park and wake on channel frames",
      "S5": "Ownership and the handoff",
      "S6": "The programs a run cell carries",
      "S7": "Handles, claims, and no raw address across a safe point",
      "S8": "Concurrent delta and compaction",
      "S9": "The edge shim",
      "S10": "Determinism and the differential",
      "S11": "The MicroPython seam (C3: no GIL)"
    }
  }/*END_FACTS*/;

  const CLAIM_MOVING = Number(BigInt(FACTS.claim_moving));
  const FNV_PRIME = BigInt(FACTS.fnv_prime);
  const FNV_BASIS = BigInt(FACTS.fnv_basis);

  // ---- the checksum (FNV-1a over the program state, smp_sched.c) --------------------------------

  function fnv(text) {
    let h = FNV_BASIS;
    for (let i = 0; i < text.length; i++) {
      h ^= BigInt(text.charCodeAt(i) & 0xff);
      h = (h * FNV_PRIME) & BigInt("0xFFFFFFFFFFFFFFFF");
    }
    return h;
  }

  function checksum(payload) {
    // The program state of a run cell: what the seal writes and the next owner verifies (S1, S5).
    return fnv([payload.pc, payload.acc_sum, payload.acc_xor, payload.steps].join("|"));
  }

  // ---- the machine ------------------------------------------------------------------------------

  function cell(slot, kind, label, extra) {
    return Object.assign(
      {
        slot: slot,
        generation: 1,
        kind: kind,
        label: label,
        claim: 0,
        claim_by: null,
        state: null,
        payload: null,
      },
      extra || {}
    );
  }

  function runCell(slot, label, program) {
    return cell(slot, FACTS.cells.RUN, label, {
      state: FACTS.states.READY,
      payload: {
        pc: 0,
        acc_sum: 0,
        acc_xor: 0,
        steps: 0,
        checksum: 0n,
        sealed: true,
        token: 0,
        program: program || "worker",
      },
    });
  }

  function makeSim(scenario) {
    const sim = {
      scenario: scenario,
      tick: 0,
      cursor: 0,
      cells: scenario.cells(),
      carriers: scenario.carriers(),
      pattern: scenario.pattern || null,
      shared: scenario.shared ? { acc: 0, writes: [] } : null,
      counters: {
        checksum_fails: 0,
        state_refused: 0,
        ring_stale_refused: 0,
        claim_spins: 0,
        claim_wedged: 0,
        wakes: 0,
        races: 0,
      },
      ring: { slots: scenario.ring ? scenario.ring.slice() : [], pushed: 0, popped: 0 },
      log: [],
      done: false,
    };

    sim.cell = (slot) => sim.cells.find((c) => c.slot === slot);

    sim.say = (carrier, opName, text, sev) => {
      sim.log.push({ tick: sim.tick, carrier: carrier, op: opName, text: text, sev: sev || "plain" });
      return sim.log[sim.log.length - 1];
    };

    sim.next = () => {
      const ready = (c) => Boolean(c) && c.pc < c.ops.length;
      if (sim.pattern && sim.tick < sim.pattern.length) {
        const chosen = sim.carriers[sim.pattern[sim.tick]];
        if (ready(chosen)) return chosen;
      }
      for (let tries = 0; tries < sim.carriers.length; tries++) {
        const c = sim.carriers[sim.cursor];
        sim.cursor = (sim.cursor + 1) % sim.carriers.length;
        if (ready(c)) return c;
      }
      return null;
    };

    sim.step = () => {
      if (sim.done) return null;
      const carrier = sim.next();
      if (!carrier) {
        sim.done = true;
        return null;
      }
      const nextOp = carrier.ops[carrier.pc++];
      sim.tick++;
      const event = nextOp.run(sim, carrier) || sim.say(carrier.id, nextOp.name, nextOp.text || nextOp.name);
      if (sim.carriers.every((c) => c.pc >= c.ops.length)) sim.done = true;
      return event;
    };

    sim.runToEnd = () => {
      const events = [];
      for (let guard = 0; guard < 10000 && !sim.done; guard++) events.push(sim.step());
      return events;
    };

    sim.runToEvent = () => {
      // The "run to the next event" control: claim, CAS, seal, park, wake, refusal, race —
      // the events the machine acts on, not the prose between them.
      const hot = ["claim", "cas", "seal", "refusal", "race", "wake", "park", "verify", "release"];
      for (let guard = 0; guard < 10000 && !sim.done; guard++) {
        const event = sim.step();
        if (event && hot.indexOf(event.op) >= 0) return event;
      }
      return null;
    };

    return sim;
  }

  // ---- the ops (one micro-event each; the scenarios compose them into schedules) ------------------

  const op = (name, run, text) => ({ name: name, run: run, text: text });

  function popOp() {
    return op("pop", (sim, carrier) => {
      const handle = sim.ring.slots.shift();
      if (handle === undefined) {
        return sim.say(carrier.id, "pop", "pop: the ring is empty (1)", "muted");
      }
      sim.ring.popped++;
      const run = sim.cell(handle);
      if (!run || run.freed) {
        sim.counters.ring_stale_refused++;
        return sim.say(
          carrier.id,
          "refusal",
          "pop refused the stale handle " + handle + " — ABA: the slot is free, the name names nothing (S3)",
          "bad"
        );
      }
      carrier.run = handle;
      return sim.say(carrier.id, "pop", "pop " + run.label + " out of the ready-ring (S3)", "plain");
    });
  }

  function resolveOp(slot) {
    return op("claim", (sim, carrier) => {
      const c = sim.cell(slot !== null ? slot : carrier.data);
      if (c.claim === CLAIM_MOVING) {
        sim.counters.claim_spins++;
        return sim.say(carrier.id, "claim", "resolve refused: the compactor holds " + c.label + " (CLAIM_MOVING); the caller tries again at its next step (S7)", "bad");
      }
      c.claim++;
      c.claim_by = carrier.id;
      return sim.say(carrier.id, "claim", "resolve " + c.label + ": claim " + c.claim + " — the address is valid until the safe point (S7)", "claim");
    });
  }

  function releaseOp(slot) {
    return op("release", (sim, carrier) => {
      const c = sim.cell(slot !== null ? slot : carrier.data);
      if (c.claim > 0 && c.claim !== CLAIM_MOVING) c.claim--;
      if (c.claim === 0) c.claim_by = null;
      return sim.say(carrier.id, "release", "release " + c.label + ": claim " + c.claim + " — nothing is held across the safe point (S7)", "muted");
    });
  }

  function workOp(slot, value) {
    return op("work", (sim, carrier) => {
      const run = sim.cell(slot !== null ? slot : carrier.run);
      const p = run.payload;
      p.acc_sum += value;
      p.acc_xor ^= value;
      p.pc++;
      p.sealed = false;
      return sim.say(carrier.id, "work", run.label + " works: acc_sum " + p.acc_sum + ", pc " + p.pc + " — one writer between two safe points (S5)", "plain");
    });
  }

  function sealOp(slot) {
    return op("seal", (sim, carrier) => {
      const run = sim.cell(slot !== null ? slot : carrier.run);
      const p = run.payload;
      p.steps++;
      p.checksum = checksum(p);
      p.sealed = true;
      return sim.say(carrier.id, "seal", "seal " + run.label + ": steps " + p.steps + ", checksum rewritten — the cell is whole before it changes hands (S5)", "seal");
    });
  }

  function verifyOp(slot) {
    return op("verify", (sim, carrier) => {
      const run = sim.cell(slot !== null ? slot : carrier.run);
      const p = run.payload;
      const good = p.sealed && p.checksum === checksum(p);
      if (!good) {
        sim.counters.checksum_fails++;
        sim.counters.races++;
        return sim.say(carrier.id, "race", "CHECKSUM_BAD at the step boundary of " + run.label + ": the previous owner is still writing the seal — the check races the seal, and the flake is real", "race");
      }
      return sim.say(carrier.id, "verify", "verify " + run.label + ": checksum ok at the step boundary — the one position the check belongs in (S8)", "verify");
    });
  }

  function parkOp(slot) {
    return op("park", (sim, carrier) => {
      const run = sim.cell(slot !== null ? slot : carrier.run);
      const p = run.payload;
      if (p.token === 1) {
        p.token = 0;
        run.state = FACTS.states.READY;
        sim.ring.slots.push(run.slot);
        return sim.say(carrier.id, "park", "park finds the wake token: " + run.label + " does not sleep at all (SMP_STEP_SELFWAKE, S4)", "wake");
      }
      run.state = FACTS.states.PARKED;
      return sim.say(carrier.id, "park", run.label + " parks: a cell in state PARKED is the whole of suspension (S2, S4)", "park");
    });
  }

  function wakeOp(slot) {
    return op("wake", (sim, carrier) => {
      const run = sim.cell(slot);
      const p = run.payload;
      p.token = 1;
      if (run.state === FACTS.states.PARKED) {
        run.state = FACTS.states.READY;
        sim.ring.slots.push(run.slot);
        sim.counters.wakes++;
        return sim.say(carrier.id, "wake", "wake " + run.label + ": PARKED to READY is ours, the enqueue commits it — the run is in the ring (S4)", "wake");
      }
      return sim.say(carrier.id, "wake", "wake " + run.label + ": not parked, the token stays for its next park — nothing is enqueued twice (S4)", "muted");
    });
  }

  function pushOp(slot) {
    return op("push", (sim, carrier) => {
      const run = sim.cell(slot !== null ? slot : carrier.run);
      sim.ring.slots.push(run.slot);
      sim.ring.pushed++;
      return sim.say(carrier.id, "push", "requeue " + run.label + " into the ready-ring — the handoff is the ring, no handshake (S5)", "plain");
    });
  }

  // ---- part 1: the step-through walkthrough -------------------------------------------------------

  function scenarioWalkthrough() {
    // Two runs, two carriers, one waker: claims, step seals, handoffs, a park, a wake, and the two
    // refusals the machine counts instead of retrying (a stale pop, a losing claim CAS).
    return {
      title: "the step loop, tick by tick",
      cells: () => [
        cell(0, FACTS.cells.RING, "ring"),
        runCell(1, "R1", "worker"),
        runCell(2, "R2", "channel pair"),
        cell(3, FACTS.cells.DATA, "D1"),
        cell(4, FACTS.cells.CHANNEL, "frame"),
      ],
      carriers: () => [
        {
          id: 0,
          label: "C0",
          pc: 0,
          run: 1,
          data: 3,
          ops: [
            popOp(),
            op("refusal", (sim, carrier) => {
              sim.counters.ring_stale_refused++;
              return sim.say(carrier.id, "refusal", "pop's next entry is a stale handle: refused (ring_stale_refused) and the pop goes on — a recycled slot is never resumed under its old name (ABA, S3)", "bad");
            }),
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.RUNNING;
              run.owner = "C0";
              return sim.say(carrier.id, "cas", "CAS READY to RUNNING: C0 owns R1 until its next safe point — ownership is the only mutex (S5)", "cas");
            }),
            resolveOp(3),
            workOp(1, 7),
            sealOp(1),
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.READY;
              return sim.say(carrier.id, "cas", "handoff CAS RUNNING to READY — after the seal; the state CAS is the last thing that happens to the cell (S5)", "cas");
            }),
            pushOp(1),
            releaseOp(3),
            popOp(),
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.RUNNING;
              run.owner = "C0";
              return sim.say(carrier.id, "cas", "CAS READY to RUNNING again: C0 steps R1 one more time — migration is observed, not performed (S2)", "cas");
            }),
            verifyOp(1),
            workOp(1, 5),
            sealOp(1),
            parkOp(1),
            releaseOp(3),
          ],
        },
        {
          id: 1,
          label: "C1",
          pc: 0,
          run: 2,
          data: 4,
          ops: [
            op("cas", (sim, carrier) => {
              const run = sim.cell(2);
              run.state = FACTS.states.RUNNING;
              run.owner = "C1";
              return sim.say(carrier.id, "cas", "C1 pops R2 and claims it: two carriers step two runs at once — the arena is shared, the program states are not (S5)", "cas");
            }),
            resolveOp(4),
            workOp(2, 3),
            sealOp(2),
            wakeOp(1),
            op("cas", (sim, carrier) => {
              const run = sim.cell(2);
              run.state = FACTS.states.PARKED;
              return sim.say(carrier.id, "cas", "R2 hands off RUNNING to PARKED after its seal: a waker may requeue a PARKED run at once, so the seal comes first (S4, S5)", "cas");
            }),
            releaseOp(4),
          ],
        },
      ],
    };
  }

  // ---- part 2a: the seal-after-CAS bug ------------------------------------------------------------

  function scenarioSealOrder(buggy) {
    // The historical bug (the CHECKSUM_BAD flake — a real leak, smp: root-cause the CHECKSUM_BAD
    // flake): the park handoff ran before the step seal. A waker requeues a PARKED run at once and
    // the next owner verifies the cell while its old owner is still rewriting the checksum.
    return {
      title: buggy ? "buggy ordering: the seal after the CAS" : "fixed ordering: the seal before the CAS",
      cells: () => [cell(0, FACTS.cells.RING, "ring"), runCell(1, "R1", "worker"), cell(3, FACTS.cells.DATA, "D1")],
      carriers: () => [
        {
          id: 0,
          label: "C0 (owner)",
          pc: 0,
          run: 1,
          data: 3,
          ops: [
            popOp(),
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.RUNNING;
              run.owner = "C0";
              return sim.say(carrier.id, "cas", "CAS READY to RUNNING: C0 owns R1 (S5)", "cas");
            }),
            resolveOp(3),
            workOp(1, 7),
            ...(buggy
              ? [
                  op("cas", (sim, carrier) => {
                    const run = sim.cell(1);
                    run.state = FACTS.states.PARKED;
                    return sim.say(carrier.id, "cas", "the buggy handoff: RUNNING to PARKED lands BEFORE the seal — the moment this CAS lands a waker may requeue the run and the next owner may step the cell (S4)", "race");
                  }),
                  sealOp(1),
                ]
              : [
                  sealOp(1),
                  op("cas", (sim, carrier) => {
                    const run = sim.cell(1);
                    run.state = FACTS.states.PARKED;
                    return sim.say(carrier.id, "cas", "the fixed handoff: RUNNING to PARKED lands after the seal — the state CAS is the last thing that happens to the cell (S5)", "cas");
                  }),
                ]),
            releaseOp(3),
          ],
        },
        {
          id: 1,
          label: "waker",
          pc: 0,
          ops: [
            op("wake", (sim, carrier) => {
              const run = sim.cell(1);
              run.payload.token = 1;
              run.state = FACTS.states.READY;
              sim.ring.slots.push(1);
              sim.counters.wakes++;
              return sim.say(carrier.id, "wake", "a waker wins PARKED to READY the moment the run is PARKED and enqueues it: another carrier takes it at once (S4)", "wake");
            }),
          ],
        },
        {
          id: 2,
          label: "C1 (next owner)",
          pc: 0,
          run: 1,
          data: 3,
          ops: [
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.RUNNING;
              run.owner = "C1";
              return sim.say(carrier.id, "cas", "C1 claims the requeued run: CAS READY to RUNNING — the cell must be whole here (S5)", "cas");
            }),
            verifyOp(1),
            workOp(1, 2),
            sealOp(1),
            op("cas", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.TERMINAL;
              return sim.say(carrier.id, "cas", "RUNNING to TERMINAL after the seal: TERMINAL is absorbing (S1)", "cas");
            }),
          ],
        },
      ],
      // The same three actors in both modes; only the owner's seal/cas order differs, so the
      // difference on screen is the bug and nothing else. The waker fires and the next owner
      // verifies while the buggy owner still has its seal queued — the race, on schedule.
      pattern: buggy
        ? [0, 0, 0, 0, 0, 1, 2, 2, 0, 2, 2, 2, 0]
        : [0, 0, 0, 0, 0, 0, 1, 2, 2, 2, 2, 2, 0],
    };
  }

  // ---- part 2b: the S4 compactor claim leak ---------------------------------------------------------

  function scenarioClaimLeak(leaky) {
    // The S4 claim leak: the channel sender resolves the frame and its data, finds its partner's
    // cell CLAIM_MOVING, and answers early. Every claim it took must go back before it answers —
    // the channel frame as well as the data cell. A claim leaked here wedges the compactor's
    // claim_cell wait for good (tests/host/smp_witness.c, the comment on the early answer).
    return {
      title: leaky ? "buggy partner path: the S4 claim leak" : "fixed partner path: claims released on every answer",
      cells: () => [
        cell(0, FACTS.cells.RING, "ring"),
        runCell(1, "R1 sender", "channel"),
        runCell(2, "R2 receiver", "channel"),
        cell(3, FACTS.cells.DATA, "D1"),
        cell(4, FACTS.cells.CHANNEL, "frame"),
      ],
      carriers: () => [
        {
          id: 0,
          label: "C0 (sender)",
          pc: 0,
          run: 1,
          data: 3,
          ops: [
            op("claim", (sim, carrier) => {
              const run = sim.cell(1);
              run.state = FACTS.states.RUNNING;
              return sim.say(carrier.id, "cas", "C0 claims R1 and resolves what the fill reads: the frame and the data cell (S7)", "cas");
            }),
            op("claim", (sim, carrier) => {
              const frame = sim.cell(4);
              const data = sim.cell(3);
              const partner = sim.cell(2);
              frame.claim++;
              frame.claim_by = "C0";
              data.claim++;
              data.claim_by = "C0";
              partner.claim = CLAIM_MOVING;
              return sim.say(carrier.id, "claim", "the partner cell R2 is CLAIM_MOVING — the fill is retried at the next step; every claim this step took goes back before the answer (S4)", "claim");
            }),
            op("release", (sim, carrier) => {
              const frame = sim.cell(4);
              const data = sim.cell(3);
              if (leaky) {
                sim.counters.claim_wedged++;
                return sim.say(carrier.id, "race", "the leak: the step answers AGAIN without releasing the frame and the data cell — a claim leaked here wedges the compactor's claim wait for good (the S4 claim leak)", "race");
              }
              frame.claim = Math.max(0, frame.claim - 1);
              data.claim = Math.max(0, data.claim - 1);
              if (frame.claim === 0) frame.claim_by = null;
              if (data.claim === 0) data.claim_by = null;
              return sim.say(carrier.id, "release", "the early answer releases both claims before it returns — the compactor waits at most one step (S5, S7)", "verify");
            }),
          ],
        },
        {
          id: 1,
          label: "compactor",
          pc: 0,
          ops: [
            op("claim", (sim, carrier) => {
              const frame = sim.cell(4);
              if (frame.claim > 0) {
                sim.counters.claim_spins++;
                if (sim.counters.claim_wedged > 0) {
                  return sim.say(carrier.id, "race", "CLAIM_MOVING waits on the frame's claim word — and the claim leaked, so the wait is no longer bounded by one safe point: the compactor is wedged for good (the S4 claim leak)", "race");
                }
                return sim.say(carrier.id, "claim", "CLAIM_MOVING waits on the frame's claim word — bounded by one safe point (S5)", "claim");
              }
              frame.claim = CLAIM_MOVING;
              return sim.say(carrier.id, "claim", "the compactor claims the frame exclusively and copies it down (S8)", "claim");
            }),
            op("release", (sim, carrier) => {
              const frame = sim.cell(4);
              if (sim.counters.claim_wedged > 0 && frame.claim > 0 && frame.claim !== CLAIM_MOVING) {
                return sim.say(carrier.id, "race", "the pass is still waiting: the leaked claim never came back — it never reaches its copy, and the arena stops moving (the S4 claim leak)", "race");
              }
              frame.claim = 0;
              frame.claim_by = null;
              return sim.say(carrier.id, "release", "the pass writes C_FORWARD before the copy and releases the claim (S8)", "verify");
            }),
          ],
        },
      ],
      pattern: [0, 0, 0, 1, 1],
    };
  }

  // ---- part 3: sync vs async ------------------------------------------------------------------------

  const PATTERNS = {
    "A · alternating": [0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1],
    "B · C0 burst": [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1],
    "C · C1 burst": [1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0],
    "D · C1 first": [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0],
  };
  const PATTERN_NAMES = Object.keys(PATTERNS);

  function scenarioSyncAsync(broken, pattern) {
    // The same step sequence, run with one carrier and with two racing. Disciplined (S6, S10):
    // every run touches its own cell and the answer words are the same in every interleaving.
    // `broken` on purpose: both runs write one shared accumulator and the interleaving leaks
    // into the answer — the race detector of part 3 names exactly that.
    const value = (run, i) => ((run.slot * 53 + i * 131 + 7) % 97) + 1;
    const carrierOps = (id, slot) => {
      const ops = [];
      for (let i = 0; i < 4; i++) {
        ops.push(
          op("work", (sim, carrier) => {
            const run = sim.cell(slot);
            const p = run.payload;
            const v = value(run, p.pc);
            if (sim.scenario.broken) {
              sim.shared.acc = (sim.shared.acc * 3 + v) % 65521;
              sim.shared.writes.push(run.label);
              p.acc_sum = sim.shared.acc;
              p.pc++;
              p.steps++;
              return sim.say(carrier.id, "work", run.label + " writes the SHARED accumulator: " + sim.shared.acc + " — the interleaving is now in the answer (S10 broken on purpose)", "race");
            }
            p.acc_sum += v;
            p.acc_xor ^= v;
            p.pc++;
            p.steps++;
            return sim.say(carrier.id, "work", run.label + " step " + p.steps + ": value " + v + " into its own cell only (S6)", "plain");
          })
        );
        ops.push(
          op("seal", (sim, carrier) => {
            const run = sim.cell(slot);
            run.payload.checksum = checksum(run.payload);
            return sim.say(carrier.id, "seal", "seal " + run.label + ": checksum rewritten before any handoff (S5)", "seal");
          })
        );
      }
      return ops;
    };
    return {
      title: broken
        ? "two carriers, one shared accumulator — the interleaving leaks"
        : "the same steps, disciplined: the answer is a function of the seed",
      broken: broken,
      shared: broken,
      cells: () => [cell(0, FACTS.cells.RING, "ring"), runCell(1, "R1", "worker"), runCell(2, "R2", "worker")],
      carriers: () => [
        { id: 0, label: "C0", pc: 0, run: 1, ops: carrierOps(0, 1) },
        { id: 1, label: "C1", pc: 0, run: 2, ops: carrierOps(1, 2) },
      ],
      pattern: pattern || PATTERNS["A · alternating"],
    };
  }

  function outcome(scenario, pattern) {
    const sim = makeSim(scenario(pattern));
    sim.runToEnd();
    return [sim.cell(1).payload.acc_sum, sim.cell(1).payload.acc_xor, sim.cell(2).payload.acc_sum, sim.cell(2).payload.acc_xor];
  }

  function syncBaseline() {
    // The one-carrier reference of S10: the same program, no interleaving at all.
    return outcome((p) => scenarioSyncAsync(false, p), [0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1]);
  }

  function asyncOutcome(name) {
    return outcome((p) => scenarioSyncAsync(false, p), PATTERNS[name]);
  }

  function brokenOutcome(name) {
    return outcome((p) => scenarioSyncAsync(true, p), PATTERNS[name]);
  }

  // ---- part 4: the refusals --------------------------------------------------------------------------

  function makeRefusalMachine() {
    return {
      depth: 0,
      budget: FACTS.call_depth,
      frames: [],
      run_state: FACTS.states.RUNNING,
      raise_slot: 0,
      refusals: [],
      callee: "graph.fn#31337",
    };
  }

  function refusalCall(m) {
    // A CALL past the depth budget is refused RXF_REFUSE_CALL_DEPTH — the Refuse-on-budget shape
    // (native/graph.h): depth costs words of the run cell, never native stack.
    if (m.depth >= m.budget) {
      m.refusals.push({ code: "RXF_REFUSE_CALL_DEPTH", value: FACTS.refuse.CALL_DEPTH });
      return {
        sev: "bad",
        text: "the call is refused: the depth budget of " + m.budget + " frame regions is spent — RXF_REFUSE_CALL_DEPTH = " + FACTS.refuse.CALL_DEPTH + " (the run refuses on budget; it never stacks)",
      };
    }
    m.depth++;
    m.frames.push({ at: m.depth });
    return { sev: "cas", text: "call: frame region " + m.depth + " of " + m.budget + " — the run cell carries the frames, not the C stack (native/graph.h)" };
  }

  function refusalReturn(m) {
    if (m.depth === 0) {
      m.run_state = FACTS.states.TERMINAL;
      return { sev: "plain", text: "RETURN at depth 0: the run's output is written and the run ends (RUNNING to TERMINAL, S1)" };
    }
    m.frames.pop();
    m.depth--;
    return { sev: "plain", text: "return to depth " + m.depth };
  }

  function refusalUnbound(m) {
    // An unknown callee: the call names a program that is not bound — the machine answers
    // RXF_REFUSE_UNBOUND and the caller decides (native/graph.c, the honest-refusal model).
    m.refusals.push({ code: "RXF_REFUSE_UNBOUND", value: FACTS.refuse.UNBOUND });
    return {
      sev: "bad",
      text: "the callee " + m.callee + " names no bound program: RXF_REFUSE_UNBOUND = " + FACTS.refuse.UNBOUND + " — an honest refusal, and the run cell is whole when it lands",
    };
  }

  function refusalCancel(m) {
    // Cancel at a safe point (docs/19): a break is a flag; the interpreter stops at the next safe
    // point — never mid-step — and the run ends there. Suspension is a data property (S2).
    m.run_state = FACTS.states.TERMINAL;
    return {
      sev: "park",
      text: "the cancel flag is data the poll finds at the next safe point: the step finishes, the seal lands, and the run ends CANCELED there — no signal ever touched the run (docs/19, S2)",
    };
  }

  // ---- part 5: exercise mode --------------------------------------------------------------------------

  function exerciseOrder() {
    // The user orders two operations; the check is the machine itself, not a memorized sentence.
    return {
      slots: ["seal", "cas"],
      check(order) {
        const fixed = order[0] === "seal" && order[1] === "cas";
        return {
          ok: fixed,
          code: fixed ? null : "CHECKSUM_BAD",
          text: fixed
            ? "right: the seal closes the cell inside the ownership window and the CAS hands it on whole — the next owner's check is a formality (S5)."
            : "wrong order: the CAS hands the run on before the seal, so the next owner verifies a cell its old owner is still writing — the CHECKSUM_BAD race appears (the real flake; S5).",
        };
      },
    };
  }

  function exerciseInterleavings() {
    // Which schedules of the shared-accumulator program change the answer? Computed by running
    // the sim, not stated: a schedule "breaks" the run when its answer words differ from the
    // sequential reference (all of C0's steps, then all of C1's).
    const reference = brokenOutcome("B · C0 burst").join();
    const outcomes = PATTERN_NAMES.map((name) => ({
      name: name,
      words: brokenOutcome(name),
      breaks: brokenOutcome(name).join() !== reference,
    }));
    return {
      reference: reference,
      outcomes: outcomes,
      names: PATTERN_NAMES,
      check(picked) {
        const correct = outcomes.filter((o) => o.breaks).map((o) => o.name);
        const same = picked.length === correct.length && correct.every((name) => picked.indexOf(name) >= 0);
        return {
          ok: same,
          text: same
            ? "right: these are exactly the schedules where the shared accumulator lets the interleaving into the answer — with the discipline of S6 every schedule would give the reference words (S10)."
            : "not quite — the words under each schedule are on screen; a schedule breaks the run exactly when its words differ from the sequential reference (B · C0 burst).",
          correct: correct,
        };
      },
    };
  }

  function exerciseDeterministic() {
    const programs = [
      {
        id: "own-cell",
        words: (name) => asyncOutcome(name).join(),
        note: "two runs, each stepping its own data cell",
      },
      {
        id: "shared-acc",
        words: (name) => brokenOutcome(name).join(),
        note: "two runs writing one shared accumulator",
      },
      {
        id: "wake-timed",
        words: (name) => {
          // An answer that reads the wake timing: attempts are timing, and timing is evidence,
          // never the answer (S10) — so this one is not deterministic.
          const p = PATTERNS[name];
          let switches = 0;
          for (let i = 1; i < p.length; i++) if (p[i] !== p[i - 1]) switches++;
          return String(switches * 7 + p.length);
        },
        note: "a run whose answer words record how often the resume changed carrier",
      },
    ];
    return {
      programs: programs,
      check(id) {
        const stable = programs.filter((p) => PATTERN_NAMES.every((name) => p.words(name) === p.words(PATTERN_NAMES[0])));
        const ok = stable.length === 1 && stable[0].id === id;
        return {
          ok: ok,
          text: ok
            ? "right: only the run that touches its own cell gives the same words in every interleaving — suspension is a data property and the answer is a function of the seed (S2, S10)."
            : "look at the words each program produces under schedules A to D: determinism is not a promise, it is the same answer every time.",
          correct: stable.map((p) => p.id),
        };
      },
    };
  }

  function exerciseRefusal() {
    return {
      check(code) {
        const ok = code === "RXF_REFUSE_CALL_DEPTH";
        return {
          ok: ok,
          text: ok
            ? "right: a call past the depth budget is refused RXF_REFUSE_CALL_DEPTH = " + FACTS.refuse.CALL_DEPTH + " — the run refuses on budget, it never stacks (native/graph.h)."
            : "the state to read is the call-depth counter: the budget of " + FACTS.call_depth + " frame regions is spent, and the refusal is named for exactly that (RXF_REFUSE_CALL_DEPTH).",
        };
      },
    };
  }

  function exerciseCheckPosition() {
    return {
      check(position) {
        const ok = position === "boundary";
        return {
          ok: ok,
          text: ok
            ? "right: the check belongs at the step boundary, with the run owned and claimed — the one position where the program state must be whole (S8; smp_sched.c says never move it)."
            : "anywhere else the check itself races: while a cell is legitimately mid-update it reports noise, and after the safe point nothing of the run is ours to read (S7, S8).",
        };
      },
    };
  }

  // ---- public surface ------------------------------------------------------------------------------

  const Tutor = {
    FACTS: FACTS,
    fnv: fnv,
    checksum: checksum,
    makeSim: makeSim,
    scenarios: {
      walkthrough: scenarioWalkthrough,
      sealOrder: scenarioSealOrder,
      claimLeak: scenarioClaimLeak,
      syncAsync: scenarioSyncAsync,
    },
    syncBaseline: syncBaseline,
    asyncOutcome: asyncOutcome,
    brokenOutcome: brokenOutcome,
    patterns: PATTERNS,
    patternNames: PATTERN_NAMES,
    refusals: {
      machine: makeRefusalMachine,
      call: refusalCall,
      ret: refusalReturn,
      unbound: refusalUnbound,
      cancel: refusalCancel,
    },
    exercises: {
      order: exerciseOrder,
      interleavings: exerciseInterleavings,
      deterministic: exerciseDeterministic,
      refusal: exerciseRefusal,
      checkPosition: exerciseCheckPosition,
    },
  };

  root.Tutor = Tutor;
  if (typeof module !== "undefined" && module.exports) module.exports = Tutor;

  // ---- the UI (plain DOM; every scenario above is driven by these controls) ----------------------

  if (typeof document === "undefined") return;

  const $ = (id) => document.getElementById(id);
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const stateName = (v) => Object.keys(FACTS.states).find((k) => FACTS.states[k] === v) || String(v);

  // ---- the tabs (docs/21 component .tabs): one component, two levels — the six parts of the
  // page and the second-level rows inside a long part (exercises, facts). Deterministic plain
  // DOM: hidden panels stay in the DOM (the pins of tests/test_tutor.py read them), the hash is
  // the deep link both ways — location.hash shows the tab that contains the target (any of the
  // old anchors, #bugs or #ex-order alike), and clicking a tab writes its hash.

  function wireTabs() {
    const bars = Array.from(document.querySelectorAll(".tabs"));
    const entries = (bar) =>
      Array.from(bar.querySelectorAll("[data-tab]"))
        .map((tab) => ({ tab: tab, panel: document.getElementById(tab.getAttribute("data-tab")) }))
        .filter((e) => e.panel);
    const show = (bar, id) => {
      entries(bar).forEach((e) => {
        const on = e.tab.getAttribute("data-tab") === id;
        e.tab.classList.toggle("active", on);
        e.tab.setAttribute("aria-selected", on ? "true" : "false");
        e.panel.hidden = !on;
      });
    };
    const activate = (hash) => {
      const target = hash ? document.getElementById(hash) : null;
      bars.forEach((bar) => {
        const all = entries(bar);
        if (!all.length) return;
        const found = target ? all.find((e) => e.panel === target || e.panel.contains(target)) : null;
        const current = all.find((e) => e.tab.classList.contains("active"));
        show(bar, (found || current || all[0]).tab.getAttribute("data-tab"));
      });
      if (target && !target.hidden) target.scrollIntoView();
    };
    bars.forEach((bar) => {
      bar.addEventListener("click", (ev) => {
        const tab = ev.target.closest("[data-tab]");
        if (tab && bar.contains(tab)) location.hash = tab.getAttribute("data-tab");
      });
      bar.addEventListener("keydown", (ev) => {
        // the tablist keyboard floor (docs/21): arrows walk the bar, Home/End to its ends
        const tabs = Array.from(bar.querySelectorAll("[data-tab]"));
        const at = tabs.indexOf(document.activeElement);
        if (at < 0) return;
        let to = -1;
        if (ev.key === "ArrowRight") to = (at + 1) % tabs.length;
        else if (ev.key === "ArrowLeft") to = (at - 1 + tabs.length) % tabs.length;
        else if (ev.key === "Home") to = 0;
        else if (ev.key === "End") to = tabs.length - 1;
        if (to < 0) return;
        ev.preventDefault();
        tabs[to].focus();
        tabs[to].click();
      });
    });
    window.addEventListener("hashchange", () => activate(location.hash.slice(1)));
    activate(location.hash.slice(1));
  }

  function renderArena(sim, mount) {
    const rows = sim.cells
      .map((c) => {
        const cls =
          c.claim === CLAIM_MOVING ? "moving" : c.state === FACTS.states.RUNNING ? "running" : c.state === FACTS.states.PARKED ? "parked" : "";
        const rowsOf = c.payload
          ? [
              ["state", stateName(c.state)],
              ["steps", c.payload.steps],
              ["acc_sum", c.payload.acc_sum],
              ["checksum", c.payload.checksum === 0n ? "0" : "sealed"],
              ["token", c.payload.token],
            ]
          : [["claim", c.claim === CLAIM_MOVING ? "CLAIM_MOVING" : c.claim]];
        const words = rowsOf
          .map(
            ([role, value]) =>
              `<div class="header-row"><span class="mono">0x${(FACTS.cell_bytes * c.slot).toString(16).padStart(2, "0")}</span><span>${esc(role)}</span><span class="mono">${esc(value)}</span></div>`
          )
          .join("");
        return `<div class="arena-cell ${cls}"><div class="arena-head"><span class="badge">${esc(c.label)}</span><span class="mono muted">slot ${c.slot} &middot; gen ${c.generation}</span></div>${words}</div>`;
      })
      .join("");
    mount.innerHTML = `<div class="arena-strip">${rows}</div>`;
  }

  function renderCarriers(sim, mount) {
    mount.innerHTML = sim.carriers
      .map((c) => {
        const last = sim.log.filter((e) => e.carrier === c.id).slice(-1)[0];
        return `<div class="card"><h3>${esc(c.label)}</h3><div class="body"><div class="muted">op ${Math.min(c.pc, c.ops.length)}/${c.ops.length}</div><div>${last ? esc(last.text) : "waiting"}</div></div></div>`;
      })
      .join("");
  }

  function renderLog(sim, mount) {
    mount.innerHTML = sim.log
      .slice(-60)
      .map(
        (e) =>
          `<div class="log-line ${esc(e.sev)}"><span class="mono muted">#${e.tick}</span> <span class="badge">${esc(
            (sim.carriers.find((c) => c.id === e.carrier) || {}).label || "?"
          )}</span> ${esc(e.text)}</div>`
      )
      .join("");
    mount.scrollTop = mount.scrollHeight;
  }

  function renderCounters(sim, mount) {
    const c = sim.counters;
    mount.innerHTML =
      `<span class="badge">checksum_fails ${c.checksum_fails}</span> ` +
      `<span class="badge">state_refused ${c.state_refused}</span> ` +
      `<span class="badge">ring_stale_refused ${c.ring_stale_refused}</span> ` +
      `<span class="badge">claim_spins ${c.claim_spins}</span> ` +
      `<span class="badge">claim_wedged ${c.claim_wedged}</span> ` +
      `<span class="badge">wakes ${c.wakes}</span>`;
  }

  function wireStage(stageId, make) {
    const stage = $(stageId);
    if (!stage) return;
    let sim = make(stage.querySelector("[data-mode].on") ? stage.querySelector("[data-mode].on").getAttribute("data-mode") : undefined);
    let timer = null;
    const arena = stage.querySelector(".arena");
    const carriers = stage.querySelector(".carriers");
    const log = stage.querySelector(".log");
    const counters = stage.querySelector(".counters");
    const draw = () => {
      renderArena(sim, arena);
      renderCarriers(sim, carriers);
      renderLog(sim, log);
      renderCounters(sim, counters);
    };
    const stop = () => {
      if (timer) {
        clearInterval(timer);
        timer = null;
        const play = stage.querySelector("[data-act=play]");
        if (play) play.textContent = "run";
      }
    };
    stage.querySelectorAll("[data-act]").forEach((button) => {
      button.addEventListener("click", () => {
        const act = button.getAttribute("data-act");
        if (act === "step") sim.step();
        else if (act === "event") sim.runToEvent();
        else if (act === "end") sim.runToEnd();
        else if (act === "reset") {
          stop();
          sim = make(stage.querySelector("[data-mode].on") ? stage.querySelector("[data-mode].on").getAttribute("data-mode") : undefined);
        } else if (act === "play") {
          if (timer) stop();
          else {
            const speed = Number((stage.querySelector("input[type=range]") || { value: 4 }).value) || 4;
            button.textContent = "pause";
            timer = setInterval(
              () => {
                if (sim.step() === null) stop();
                draw();
              },
              260 - speed * 40
            );
          }
        }
        draw();
      });
    });
    stage.querySelectorAll("[data-mode]").forEach((button) => {
      button.addEventListener("click", () => {
        stop();
        stage.querySelectorAll("[data-mode]").forEach((b) => b.classList.remove("on"));
        button.classList.add("on");
        sim = make(button.getAttribute("data-mode"));
        draw();
      });
    });
    draw();
  }

  function wireRefusals() {
    const mount = $("refusal-out");
    if (!mount) return;
    let m = Tutor.refusals.machine();
    const draw = () => {
      mount.innerHTML =
        `<div class="card"><h3>machine state</h3><div class="body">
          <div class="header-row"><span class="mono">depth</span><span>call depth / budget</span><span class="mono">${m.depth} / ${m.budget}</span></div>
          <div class="header-row"><span class="mono">state</span><span>run state word</span><span class="mono">${esc(stateName(m.run_state))}</span></div>
          <div class="header-row"><span class="mono">raise</span><span>mp_raise_slot (S11)</span><span class="mono">${m.raise_slot ? "payload + 1" : "0 (empty)"}</span></div>
        </div></div>` +
        m.refusals
          .map(
            (r) =>
              `<div class="refusal"><b>${esc(r.code)}</b> = <span class="mono">${r.value}</span> — refused and counted, the caller decides; the machine does not improvise.</div>`
          )
          .join("");
    };
    document.querySelectorAll("[data-refusal]").forEach((button) => {
      button.addEventListener("click", () => {
        const act = button.getAttribute("data-refusal");
        const out =
          act === "call" ? Tutor.refusals.call(m) : act === "return" ? Tutor.refusals.ret(m) : act === "unbound" ? Tutor.refusals.unbound(m) : Tutor.refusals.cancel(m);
        const line = document.createElement("div");
        line.className = "log-line " + out.sev;
        line.textContent = out.text;
        $("refusal-log").appendChild(line);
        draw();
      });
    });
    $("refusal-reset").addEventListener("click", () => {
      m = Tutor.refusals.machine();
      $("refusal-log").innerHTML = "";
      draw();
    });
    draw();
  }

  function verdict(host, result) {
    host.innerHTML = `<div class="${result.ok ? "ok" : "refusal"}">${esc(result.text)}</div>`;
  }

  function wireExercises() {
    const order = Tutor.exercises.order();
    const orderHost = $("ex-order");
    if (orderHost) {
      const chosen = [];
      orderHost.querySelectorAll("[data-order]").forEach((button) => {
        button.addEventListener("click", () => {
          if (chosen.length >= 2) return;
          chosen.push(button.getAttribute("data-order"));
          $("ex-order-seq").innerHTML = chosen.map((s) => `<span class="badge mono">${esc(s)}</span>`).join(" &rarr; ");
          if (chosen.length === 2) verdict($("ex-order-out"), order.check(chosen));
        });
      });
      $("ex-order-reset").addEventListener("click", () => {
        chosen.length = 0;
        $("ex-order-seq").innerHTML = "";
        $("ex-order-out").innerHTML = "";
      });
    }

    const inter = Tutor.exercises.interleavings();
    const interHost = $("ex-inter-list");
    if (interHost) {
      interHost.innerHTML = inter.outcomes
        .map(
          (o) =>
            `<label><input type="checkbox" value="${esc(o.name)}"> ${esc(o.name)} — words <span class="mono">${esc(o.words.join(","))}</span></label>`
        )
        .join("");
      $("ex-inter-check").addEventListener("click", () => {
        const picked = Array.from(interHost.querySelectorAll("input:checked")).map((i) => i.value);
        verdict($("ex-inter-out"), inter.check(picked));
      });
    }

    const det = Tutor.exercises.deterministic();
    const detHost = $("ex-det-list");
    if (detHost) {
      detHost.innerHTML = det.programs
        .map((p) => `<label><input type="radio" name="ex-det" value="${esc(p.id)}"> <span class="mono">${esc(p.id)}</span> — ${esc(p.note)}</label>`)
        .join("");
      $("ex-det-check").addEventListener("click", () => {
        const picked = detHost.querySelector("input:checked");
        verdict($("ex-det-out"), det.check(picked ? picked.value : ""));
      });
    }

    const refusalEx = Tutor.exercises.refusal();
    const refusalHost = $("ex-refusal");
    if (refusalHost) {
      $("ex-refusal-check").addEventListener("click", () => {
        const picked = refusalHost.querySelector("input:checked");
        verdict($("ex-refusal-out"), refusalEx.check(picked ? picked.value : ""));
      });
    }

    const position = Tutor.exercises.checkPosition();
    const positionHost = $("ex-position");
    if (positionHost) {
      $("ex-position-check").addEventListener("click", () => {
        const picked = positionHost.querySelector("input:checked");
        verdict($("ex-position-out"), position.check(picked ? picked.value : ""));
      });
    }
  }

  function wireRaceTable() {
    const mount = $("race-table");
    if (!mount) return;
    const reference = Tutor.syncBaseline().join();
    const rows = Tutor.patternNames
      .map((name) => {
        const disciplined = Tutor.asyncOutcome(name).join();
        const broken = Tutor.brokenOutcome(name).join();
        const verdictCell = (words) =>
          `<td class="${words === reference ? "ok" : ""}" style="${words === reference ? "" : "color:var(--bad)"}">${words === reference ? "identical" : "differs"}</td>`;
        return `<tr><td>${esc(name)}</td><td class="mono">${esc(disciplined)}</td>${verdictCell(disciplined)}<td class="mono">${esc(broken)}</td>${verdictCell(broken)}</tr>`;
      })
      .join("");
    mount.innerHTML =
      `<table><tr><th>schedule</th><th>disciplined words</th><th>vs reference</th><th>broken words</th><th>vs reference</th></tr>` +
      `<tr><td>sync reference (C0 then C1)</td><td class="mono">${esc(reference)}</td><td class="ok">baseline</td><td class="mono">—</td><td>—</td></tr>` +
      rows +
      `</table>`;
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireTabs();
    wireStage("stage-walk", () => makeSim(Tutor.scenarios.walkthrough()));
    wireStage("stage-seal", (mode) => makeSim(Tutor.scenarios.sealOrder(mode === "buggy")));
    wireStage("stage-claim", (mode) => makeSim(Tutor.scenarios.claimLeak(mode === "leaky")));
    wireStage("stage-sync", (mode) => makeSim(Tutor.scenarios.syncAsync(mode === "broken", Tutor.patterns["A · alternating"])));
    wireRaceTable();
    wireRefusals();
    wireExercises();
  });
})(typeof window !== "undefined" ? window : globalThis);
