/* The interpreter of native/graph.c on the host: one shared library the tests run programs with.
 *
 * rxf_sim interprets the words of a program a step at a time in Python; this is the same run on
 * the real interpreter, natively. The library is rxf_graph_run (native/graph.c) with the leaves
 * the starter ships -- the C of native/basics.c, stdlib_memory.c, stdlib_runtime.c and safe.c,
 * linked as they are. Nothing here interprets a word; the interpreter is graph.c.
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

/* The types of native/basics.h name the shapes of the leaves below; the headers are reached
 * relative to this file so any compiler finds them the same way (with or without -I native). */
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

/* The leaves of native/basics.c (their shapes are the ones native/graph.h gives their ids). */
rxf_u32 rxf_wrapping_add_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_wrapping_subtract_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_and_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_or_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_bit_not_uint64_t(rxf_u64, rxf_u64 *);
rxf_u32 rxf_equal_uint64_t(rxf_u64, rxf_u64, rxf_u8 *);
rxf_u32 rxf_less_uint64_t(rxf_u64, rxf_u64, rxf_u8 *);
rxf_u32 rxf_shift_left_uint64_t(rxf_u64, rxf_u32, rxf_u64 *);
rxf_u32 rxf_shift_right_uint64_t(rxf_u64, rxf_u32, rxf_u64 *);
rxf_u32 rxf_checked_add_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_checked_subtract_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_checked_multiply_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_divide_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_remainder_uint32_t(rxf_u32, rxf_u32, rxf_u32 *);
rxf_u32 rxf_divide_int32_t(rxf_i32, rxf_i32, rxf_i32 *);
rxf_u32 rxf_remainder_int32_t(rxf_i32, rxf_i32, rxf_i32 *);
rxf_u32 rxf_wrapping_multiply_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_checked_multiply_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_divide_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_remainder_uint64_t(rxf_u64, rxf_u64, rxf_u64 *);
rxf_u32 rxf_checked_multiply_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
rxf_u32 rxf_divide_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
rxf_u32 rxf_remainder_int64_t(rxf_i64, rxf_i64, rxf_i64 *);
/* The safe point of compiled code (native/safe.c). */
rxf_u32 rxf_safe_point(rxf_runtime_context *, void *);

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
    rxf_u64 budget; /* steps, or 0 when the caller keeps no budget */
} host;

/* The pump in the slot of RXF_LEAF_LIVE_PUMP: there is no live layer on the host. The runner
 * keeps its step budget at this doorbell -- the safe point of the interpreter calls it before
 * every instruction while the rx ring has something in it. */
static rxf_u32 rxf_host_pump(rxf_runtime_context *c, void *area) {
    (void)c;
    (void)area;
    if (host.budget && ++host.steps > host.budget)
        longjmp(host.escape, 1);
    return RXF_OK;
}

/* The doorbell of the step budget: something in the rx ring makes the safe point of the
 * interpreter call the pump before every instruction. */
static void rxf_host_arm(rxf_u64 on) {
    volatile rxf_u32 *head = (volatile rxf_u32 *)(host.area + RXF_AREA_RX_HEAD);
    volatile rxf_u32 *tail = (volatile rxf_u32 *)(host.area + RXF_AREA_RX_TAIL);
    *tail = 0;
    *head = on ? 1 : 0;
}

void rxf_host_init(void) {
    /* The function table of the context: every leaf finds its C by its id. find_leaf walks the
     * table, so the pump and the leaves a program calls most come first. Not here: the uart of a
     * machine (native/uart.c) and the compiler of native/compile.c -- a program that calls them
     * is refused UNBOUND, as rxf_sim refuses the leaves it does not run. */
    static const struct {
        rxf_u64 id;
        void *address;
    } leaves[] = {
        {RXF_LEAF_LIVE_PUMP, (void *)rxf_host_pump},
        {RXF_LEAF_WRAPPING_ADD_UINT64_T, (void *)rxf_wrapping_add_uint64_t},
        {RXF_LEAF_MEMORY_COPY, (void *)rxf_memory_copy},
        {RXF_LEAF_WRAPPING_SUBTRACT_UINT64_T, (void *)rxf_wrapping_subtract_uint64_t},
        {RXF_LEAF_BIT_AND_UINT64_T, (void *)rxf_bit_and_uint64_t},
        {RXF_LEAF_BIT_OR_UINT64_T, (void *)rxf_bit_or_uint64_t},
        {RXF_LEAF_SHIFT_LEFT_UINT64_T, (void *)rxf_shift_left_uint64_t},
        {RXF_LEAF_SHIFT_RIGHT_UINT64_T, (void *)rxf_shift_right_uint64_t},
        {RXF_LEAF_EQUAL_UINT64_T, (void *)rxf_equal_uint64_t},
        {RXF_LEAF_LESS_UINT64_T, (void *)rxf_less_uint64_t},
        {RXF_LEAF_MEMORY_FILL, (void *)rxf_memory_fill},
        {RXF_LEAF_MEMORY_COMPARE, (void *)rxf_memory_compare},
        {RXF_LEAF_BIT_NOT_UINT64_T, (void *)rxf_bit_not_uint64_t},
        {RXF_LEAF_HASH_U64, (void *)rxf_hash_u64},
        {RXF_LEAF_CHECKED_ADD_UINT32_T, (void *)rxf_checked_add_uint32_t},
        {RXF_LEAF_CHECKED_SUBTRACT_UINT32_T, (void *)rxf_checked_subtract_uint32_t},
        {RXF_LEAF_CHECKED_MULTIPLY_UINT32_T, (void *)rxf_checked_multiply_uint32_t},
        {RXF_LEAF_DIVIDE_UINT32_T, (void *)rxf_divide_uint32_t},
        {RXF_LEAF_REMAINDER_UINT32_T, (void *)rxf_remainder_uint32_t},
        {RXF_LEAF_DIVIDE_INT32_T, (void *)rxf_divide_int32_t},
        {RXF_LEAF_REMAINDER_INT32_T, (void *)rxf_remainder_int32_t},
        {RXF_LEAF_WRAPPING_MULTIPLY_UINT64_T, (void *)rxf_wrapping_multiply_uint64_t},
        {RXF_LEAF_CHECKED_MULTIPLY_UINT64_T, (void *)rxf_checked_multiply_uint64_t},
        {RXF_LEAF_CHECKED_MULTIPLY_INT64_T, (void *)rxf_checked_multiply_int64_t},
        {RXF_LEAF_DIVIDE_UINT64_T, (void *)rxf_divide_uint64_t},
        {RXF_LEAF_REMAINDER_UINT64_T, (void *)rxf_remainder_uint64_t},
        {RXF_LEAF_DIVIDE_INT64_T, (void *)rxf_divide_int64_t},
        {RXF_LEAF_REMAINDER_INT64_T, (void *)rxf_remainder_int64_t},
        {RXF_LEAF_RUNTIME_HASH_MAP_LOOKUP, (void *)rxf_runtime_hash_map_lookup},
        {RXF_LEAF_RUNTIME_HASH_SET_LOOKUP, (void *)rxf_runtime_hash_set_lookup},
        {RXF_LEAF_RUNTIME_BEGIN_PRIVATE, (void *)rxf_runtime_begin_private},
        {RXF_LEAF_RUNTIME_RESOLVE, (void *)rxf_runtime_resolve},
        {RXF_LEAF_RUNTIME_ALLOCATE_PREPARE, (void *)rxf_runtime_allocate_prepare},
        {RXF_LEAF_RUNTIME_PUBLISH, (void *)rxf_runtime_publish},
        {RXF_LEAF_RUNTIME_ROLLBACK, (void *)rxf_runtime_rollback},
        {RXF_LEAF_RUNTIME_BORROW, (void *)rxf_runtime_borrow},
        {RXF_LEAF_RUNTIME_RELEASE_BORROW, (void *)rxf_runtime_release_borrow},
        {RXF_LEAF_RUNTIME_PIN, (void *)rxf_runtime_pin},
        {RXF_LEAF_RUNTIME_UNPIN, (void *)rxf_runtime_unpin},
        {RXF_LEAF_RUNTIME_READ, (void *)rxf_runtime_read},
        {RXF_LEAF_RUNTIME_WRITE, (void *)rxf_runtime_write},
        {RXF_LEAF_RUNTIME_APPEND, (void *)rxf_runtime_append},
        {RXF_LEAF_RUNTIME_FORMAT_U32, (void *)rxf_runtime_format_u32},
        {RXF_LEAF_RUNTIME_SEARCH, (void *)rxf_runtime_search},
        {RXF_LEAF_RUNTIME_CLEANUP, (void *)rxf_runtime_cleanup},
        {RXF_LEAF_SAFE_POINT, (void *)rxf_safe_point},
    };
    memset(&host, 0, sizeof(host));
    for (rxf_u64 i = 0; i < sizeof(leaves) / sizeof(leaves[0]); i++) {
        host.functions[i].id = leaves[i].id;
        host.functions[i].generation = RXF_HOST_GENERATION;
        host.functions[i].address = (rxf_u64)(unsigned long long)leaves[i].address;
    }
    host.capabilities[0].id = RXF_CAPABILITY_CHANNEL;
    host.capabilities[0].generation = RXF_HOST_GENERATION;
    host.capabilities[0].address = (rxf_u64)(unsigned long long)host.area;
    host.context.version = RXF_RUNTIME_ABI_VERSION;
    host.context.size = sizeof(rxf_runtime_context);
    host.context.function_count = sizeof(leaves) / sizeof(leaves[0]);
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
    rxf_u32 status = rxf_graph_run(&host.context, program, (rxf_u64)(unsigned long long)args,
                                   args_bytes / 8, host.out);
    host.budget = 0;
    rxf_host_arm(0);
    *out = host.out[0];
    return status;
}
