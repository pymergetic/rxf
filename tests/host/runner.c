/* The host glue of the starter's interpreter: one shared library the tests run programs with.
 *
 * rxf_sim interprets the words of a program a step at a time in Python; this is the same run on
 * the real interpreter, natively. The interpreter and the leaves are the seed's machine code
 * (native/graph.rxfs and the families of native/), the same bytes the starter ships for
 * x86_64: tests/rxf_native.py builds them with the seed builders and loads them into this
 * process, and this file holds only what a host is -- the tables of a context, the objects of
 * a run, the flat memory of its addresses, and the step budget at the safe point. Nothing here
 * interprets a word and nothing here is a leaf: the code of the seed is.
 *
 * A run answers (status, value) like rxf_sim.run: the header of the program names the frame and
 * the parameter slots, the arguments fill them, and the leaves read and write the flat memory the
 * addresses of the run live in (memory_base .. memory_base + memory_size). The frame of a run is
 * the stack frame of rxf_graph_run: its slot addresses are what the leaves see, and the block
 * rxf_sim keeps at FRAME_AT is left zeroed, as rxf_sim.run leaves it when it starts.
 *
 * The step budget of a run (rxf_sim.run's `limit`) is kept at the doorbell of a Channel: the
 * runner arms the rx ring so the safe point of the interpreter calls the pump of this runner
 * before every instruction, where the steps are counted and the run is stopped at its budget
 * (rxf_host_run answers RXF_HOST_NO_END and the caller raises what rxf_sim raises).
 */
#include <setjmp.h>
#include <string.h>

/* The types and constants of the ABI the seed's leaves are called with (native/basics.h,
 * graph.h, stdlib_runtime.h); the headers are reached relative to this file so any compiler
 * finds them the same way (with or without -I native). */
#include "../../native/basics.h"
#include "../../native/graph.h"
#include "../../native/stdlib_memory.h"

#define RXF_FUNCTION __attribute__((noinline))

/* The answer of a run that did not end within its budget: not a refusal of a program. */
#define RXF_HOST_NO_END 0xFFFFFFFFU
/* Where rxf_sim.run keeps the frame of a run in the flat memory (tests/rxf_sim.py). */
#define RXF_HOST_FRAME_AT 0x100000ULL
/* The object a program is read from (tests/rxf_world.py uses the same id for the same thing). */
#define RXF_HOST_PROGRAM_ID 900001ULL
#define RXF_HOST_GENERATION 1ULL
/* The secondary objects of a run: the programs a call card names (graph.h's
 * RXF_GRAPH_CALL: a callee is a program of its own, bound at the object id its call
 * carries) and the cells an Object handle resolves. Registered before a run,
 * cleared by it -- one run at a time in a process, like the tests run them. The room
 * is what the imports of one world bind: the pools and strings of the qstr corpus
 * (16 cells), the type cells and the arena of the obj corpus (10), and the callee
 * programs of their call cards. */
#define RXF_HOST_OBJECTS 48
/* The area of the Channel capability the step budget is kept at (the rings start at word 30). */
#define RXF_HOST_AREA_BYTES 320U
#define RXF_AREA_RX_HEAD 120U
#define RXF_AREA_RX_TAIL 124U
/* A value of a run is at most the frame of the interpreter -- one run cell of it now that
 * the frames are variable length (native/graph.h); the tests return whole words. */
#define RXF_HOST_OUT_ROOM RXF_GRAPH_CELL_BYTES

/* The interpreter is called the way native/graph.h declares it -- the ABI the boot trampolines
 * use too -- through the address its code was loaded at (tests/rxf_native.py). */
typedef __typeof__(&rxf_graph_run) rxf_graph_run_fn;

/* The state of the runner: the tables of one context and the budget of the run that goes. One
 * run at a time in a process; the tests are one run at a time. */
static struct {
    rxf_runtime_context context;
    rxf_native_slot functions[48];
    rxf_native_slot capabilities[1];
    rxf_object_entry objects[1 + RXF_HOST_OBJECTS];
    rxf_u64 bound; /* the secondary objects registered for the run that goes */
    rxf_u8 area[RXF_HOST_AREA_BYTES];
    rxf_u64 out[RXF_HOST_OUT_ROOM / 8];
    jmp_buf escape; /* where a run that does not end is stopped */
    rxf_u64 steps;
    rxf_u64 budget;       /* steps, or 0 when the caller keeps no budget */
    rxf_graph_run_fn run; /* the seed's graph_run, at the address it was loaded at */
} host;

/* The pump in the slot of RXF_LEAF_LIVE_PUMP: there is no live layer on the host. The runner
 * keeps its step budget at this doorbell -- the safe point of the interpreter calls it before
 * every instruction while the rx ring has something in it. It is the one leaf this file is:
 * the address the caller puts in the table of the run (rxf_host_pump_address). */
static rxf_u32 rxf_host_pump(rxf_runtime_context *c, void *area) {
    (void)c;
    (void)area;
    if (host.budget && ++host.steps > host.budget)
        longjmp(host.escape, 1);
    return RXF_OK;
}

void *rxf_host_pump_address(void) {
    return (void *)rxf_host_pump;
}

/* The doorbell of the step budget: something in the rx ring makes the safe point of the
 * interpreter call the pump before every instruction. */
static void rxf_host_arm(rxf_u64 on) {
    volatile rxf_u32 *head = (volatile rxf_u32 *)(host.area + RXF_AREA_RX_HEAD);
    volatile rxf_u32 *tail = (volatile rxf_u32 *)(host.area + RXF_AREA_RX_TAIL);
    *tail = 0;
    *head = on ? 1 : 0;
}

/* The tables of the run that goes: the interpreter and the leaves are the seed's code, at the
 * addresses the caller loaded them at (tests/rxf_native.py), in the order find_leaf walks the
 * table -- the pump and the leaves a program calls most come first. Not in it: the uart of a
 * machine (the seed's uart_pump) and the compiler (compile_program) -- a program that calls
 * them is refused UNBOUND, as rxf_sim refuses the leaves it does not run. The caller passes at
 * most as many leaves as the table holds. */
void rxf_host_init(const void *graph_run, const rxf_u64 *leaf_ids, const rxf_u64 *leaf_addresses,
                   rxf_u64 leaf_count) {
    memset(&host, 0, sizeof(host));
    host.run = (rxf_graph_run_fn)graph_run;
    for (rxf_u64 i = 0; i < leaf_count; i++) {
        host.functions[i].id = leaf_ids[i];
        host.functions[i].generation = RXF_HOST_GENERATION;
        host.functions[i].address = leaf_addresses[i];
    }
    host.capabilities[0].id = RXF_CAPABILITY_CHANNEL;
    host.capabilities[0].generation = RXF_HOST_GENERATION;
    host.capabilities[0].address = (rxf_u64)(unsigned long long)host.area;
    host.context.version = RXF_RUNTIME_ABI_VERSION;
    host.context.size = sizeof(rxf_runtime_context);
    host.context.function_count = leaf_count;
    host.context.functions = (rxf_u64)(unsigned long long)host.functions;
    host.context.object_count = 1;
    host.context.objects = (rxf_u64)(unsigned long long)host.objects;
    host.context.capability_count = 1;
    host.context.capabilities = (rxf_u64)(unsigned long long)host.capabilities;
}

void rxf_host_deinit(void) {
    memset(&host, 0, sizeof(host));
}

/* A secondary object of the run that goes: a program a call card names (its words at
 * the object id the card's handle carries) or a cell an Object handle resolves. The
 * payload is an address of this process (the flat memory of the run, or the words
 * themselves). The run consumes what is registered: one run at a time. */
rxf_u32 rxf_host_bind(rxf_u64 id, rxf_u64 payload, rxf_u64 size) {
    if (id == RXF_HOST_PROGRAM_ID || host.bound >= RXF_HOST_OBJECTS)
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_object_entry *entry = &host.objects[1 + host.bound++];
    entry->id = id;
    entry->generation = RXF_HOST_GENERATION;
    entry->payload = payload;
    entry->size = size;
    entry->capacity = size;
    entry->state = RXF_OBJECT_LIVE;
    return RXF_OK;
}

/* One run of a program of words on the arguments of whole words: the (status, value) of
 * rxf_sim.run. `memory_base` and `memory_size` are the flat address space the addresses of the
 * run live in; `limit` is the step budget of rxf_sim.run (0: the run may go on). */
RXF_FUNCTION rxf_u32 rxf_host_run(const void *words, rxf_u64 words_bytes, const void *args,
                                  rxf_u64 args_bytes, rxf_u64 memory_base, rxf_u64 memory_size,
                                  rxf_u64 limit, rxf_u64 *out) {
    const rxf_u64 *code = (const rxf_u64 *)words;
    if (!out || !words || !args || (words_bytes & 7) || words_bytes < 5 * 8 || (args_bytes & 7) ||
        !memory_base)
        return RXF_REFUSE_OUT_OF_RANGE;
    /* What rxf_sim.run writes before it starts: the frame block of the flat memory is zero. */
    rxf_u64 frame_bytes = code[1];
    if (frame_bytes <= memory_size && RXF_HOST_FRAME_AT <= memory_size - frame_bytes)
        memset((void *)(unsigned long long)(memory_base + RXF_HOST_FRAME_AT), 0,
               (unsigned long long)frame_bytes);
    host.objects[0].id = RXF_HOST_PROGRAM_ID;
    host.objects[0].generation = RXF_HOST_GENERATION;
    host.objects[0].payload = (rxf_u64)(unsigned long long)words;
    host.objects[0].size = words_bytes;
    host.objects[0].capacity = words_bytes;
    host.objects[0].state = RXF_OBJECT_LIVE;
    /* the table the run sees is sorted by id (the interpreter's lookup is a binary
       search): the program's entry is one of them, the bound secondaries the rest */
    rxf_u64 count = 1 + host.bound;
    host.bound = 0;
    host.context.object_count = count;
    for (rxf_u64 i = 1; i < count; i++)
        for (rxf_u64 j = i; j > 0 && host.objects[j].id < host.objects[j - 1].id; j--) {
            rxf_object_entry swap = host.objects[j];
            host.objects[j] = host.objects[j - 1];
            host.objects[j - 1] = swap;
        }
    host.steps = 0;
    host.budget = limit;
    memset(host.out, 0, sizeof(host.out));
    rxf_host_arm(limit != 0);
    if (setjmp(host.escape)) {
        host.budget = 0;
        rxf_host_arm(0);
        return RXF_HOST_NO_END;
    }
    rxf_object_handle program = {RXF_HOST_PROGRAM_ID, RXF_HOST_GENERATION, 0};
    rxf_u32 status = host.run(&host.context, program, (rxf_u64)(unsigned long long)args,
                              args_bytes / 8, host.out);
    host.budget = 0;
    rxf_host_arm(0);
    *out = host.out[0];
    return status;
}
