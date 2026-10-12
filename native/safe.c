/*
 * The safe point of compiled code (docs/19): rxf_safe_point(context, area) is what the compiler
 * (native/compile.c, native/tiny) calls at every edge of a program that goes back, between its
 * copies and its jump. There the state of the program is whole, as it is between two instructions
 * of the interpreter (native/graph.c), so a peer may be served: the leaf looks at the doorbell of
 * the Channel, runs the live pump while a request waits, and stays in it while the peer holds the
 * break, like the interpreter does.
 *
 * What the pump changed under the code that called is the one difference. The interpreter reads its
 * program again afterwards; compiled code holds the addresses of its moment (leaves, objects). If
 * the epoch of the area moved (a commit, a compaction, a relink), the leaf answers
 * RXF_REFUSE_STALE_GENERATION, which the compiled code returns as its own status: the caller
 * compiles the program again. Nothing else is cut short.
 *
 * `in_run` is set while the pump is served from here: no code of the arena may move under a program
 * whose caller is still on the stack. The words of the area are those of core/live/live.h.
 */
#include "graph.h"

#define RXF_FUNCTION __attribute__((noinline))
#define RXF_INLINE static __attribute__((always_inline)) inline
#define RXF_AREA_RX_HEAD 120U
#define RXF_AREA_RX_TAIL 124U
#define RXF_AREA_BREAK_REQUEST 176U
#define RXF_AREA_BREAK_ACK 180U
#define RXF_AREA_IN_RUN 256U
#define RXF_AREA_EPOCH 268U

typedef unsigned long rxf_uptr;
typedef rxf_u32 (*rxf_pump_p)(void *, void *);

RXF_INLINE volatile rxf_u32 *word(rxf_u8 *area, rxf_u64 offset) {
    return (volatile rxf_u32 *)(area + offset);
}

/* The address of the pump in the function table of the context, or 0. */
RXF_INLINE rxf_u64 pump_of(const rxf_runtime_context *c) {
    const rxf_native_slot *table = (const rxf_native_slot *)(rxf_uptr)c->functions;
    for (rxf_u64 i = 0; i < c->function_count; i++)
        if (table[i].id == RXF_LEAF_LIVE_PUMP)
            return table[i].address;
    return 0;
}

RXF_FUNCTION rxf_u32 rxf_safe_point(rxf_runtime_context *c, void *area_in) {
    rxf_u8 *area = (rxf_u8 *)area_in;
    if (!c || !area)
        return RXF_REFUSE_UNBOUND;
    if (*word(area, RXF_AREA_RX_HEAD) == *word(area, RXF_AREA_RX_TAIL) &&
        !*word(area, RXF_AREA_BREAK_REQUEST))
        return RXF_OK;
    const rxf_u64 pump = pump_of(c);
    if (!pump)
        return RXF_OK;
    const rxf_u32 epoch = *word(area, RXF_AREA_EPOCH);
    const rxf_u32 outer = *word(area, RXF_AREA_IN_RUN);
    *word(area, RXF_AREA_IN_RUN) = 1;
    for (;;) {
        *word(area, RXF_AREA_BREAK_ACK) = *word(area, RXF_AREA_BREAK_REQUEST) ? 1 : 0;
        ((rxf_pump_p)(rxf_uptr)pump)(c, area);
        if (!*word(area, RXF_AREA_BREAK_REQUEST))
            break;
    }
    *word(area, RXF_AREA_BREAK_ACK) = 0;
    *word(area, RXF_AREA_IN_RUN) = outer;
    return *word(area, RXF_AREA_EPOCH) == epoch ? RXF_OK : RXF_REFUSE_STALE_GENERATION;
}
