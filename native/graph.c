#include "graph.h"

#define RXF_FUNCTION __attribute__((noinline))
#define RXF_INLINE static __attribute__((always_inline)) inline
/* A loop stays a loop: a vectorized one reads constants from memory, and a snippet has none. */
#define RXF_SCALAR _Pragma("clang loop vectorize(disable) interleave(disable) unroll(disable)")
#define RXF_SLOT_WORDS 5U
#define RXF_EDGE_HEADER 2U
#define RXF_EDGE_COPY_WORDS 5U
#define RXF_OPERAND_WORDS 3U
#define RXF_CALL_HEADER 6U
#define RXF_CALL_OPERANDS 6U

typedef unsigned long rxf_uptr;

typedef struct {
    rxf_runtime_context *c;
    const rxf_u64 *code;
    rxf_u64 count;
    rxf_u8 *frame;
    rxf_u64 frame_bytes;
} rxf_graph;

typedef rxf_u32 (*rxf_call_qo)(rxf_u64, void *);
typedef rxf_u32 (*rxf_call_qqo)(rxf_u64, rxf_u64, void *);
typedef rxf_u32 (*rxf_call_hpqqo)(rxf_runtime_context *, rxf_object_handle, void *, rxf_u64,
                                  rxf_u64, void *);
typedef rxf_u32 (*rxf_call_hqo)(rxf_runtime_context *, rxf_object_handle, rxf_u64, void *);
typedef rxf_u32 (*rxf_call_qqqqqpo)(rxf_runtime_context *, rxf_u64, rxf_u64, rxf_u64, rxf_u64,
                                    rxf_u64, void *, void *);
typedef rxf_u32 (*rxf_call_p)(rxf_runtime_context *, void *);
typedef rxf_u32 (*rxf_call_hq)(rxf_runtime_context *, rxf_object_handle, rxf_u64);
typedef rxf_u32 (*rxf_call_h)(rxf_runtime_context *, rxf_object_handle);
typedef rxf_u32 (*rxf_call_hpqq)(rxf_runtime_context *, rxf_object_handle, void *, rxf_u64,
                                 rxf_u64);
typedef rxf_u32 (*rxf_call_hpqqq)(rxf_runtime_context *, rxf_object_handle, void *, rxf_u64,
                                  rxf_u64, rxf_u64);
typedef rxf_u32 (*rxf_call_hpw)(rxf_runtime_context *, rxf_object_handle, void *, rxf_u32);
typedef rxf_u32 (*rxf_call_wwo)(rxf_u32, rxf_u32, void *);
typedef rxf_u32 (*rxf_call_qwo)(rxf_u64, rxf_u32, void *);
typedef rxf_u32 (*rxf_call_pqpqq)(void *, rxf_u64, void *, rxf_u64, rxf_u64);
typedef rxf_u32 (*rxf_call_pqpqqo)(void *, rxf_u64, void *, rxf_u64, rxf_u64, void *);
typedef rxf_u32 (*rxf_call_pqbq)(void *, rxf_u64, rxf_u8, rxf_u64);
typedef rxf_u32 (*rxf_call_hqhqpo)(rxf_runtime_context *, rxf_object_handle, rxf_u64,
                                   rxf_object_handle, rxf_u64, void *, void *);

RXF_INLINE void copy_bytes(rxf_u8 *dst, const rxf_u8 *src, rxf_u64 count) {
    RXF_SCALAR
    for (rxf_u64 i = 0; i < count; i++)
        dst[i] = src[i];
}

RXF_INLINE void zero_bytes(rxf_u8 *dst, rxf_u64 count) {
    RXF_SCALAR
    for (rxf_u64 i = 0; i < count; i++)
        dst[i] = 0;
}

RXF_INLINE rxf_u64 read_bytes(const rxf_u8 *src, rxf_u64 width) {
    rxf_u64 value = 0;
    RXF_SCALAR
    for (rxf_u64 i = 0; i < width && i < 8; i++)
        value |= (rxf_u64)src[i] << (8 * i);
    return value;
}

RXF_INLINE void write_bytes(rxf_u8 *dst, rxf_u64 width, rxf_u64 value) {
    RXF_SCALAR
    for (rxf_u64 i = 0; i < width && i < 8; i++)
        dst[i] = (rxf_u8)(value >> (8 * i));
}

RXF_INLINE rxf_u32 have(const rxf_graph *g, rxf_u64 at, rxf_u64 words) {
    return at <= g->count && words <= g->count - at;
}

RXF_INLINE rxf_u32 in_frame(const rxf_graph *g, rxf_u64 offset, rxf_u64 width) {
    return width <= g->frame_bytes && offset <= g->frame_bytes - width;
}

/* Where the bytes of an operand are, and that at least `need` of them are there. */
RXF_INLINE rxf_u32 locate(const rxf_graph *g, const rxf_u64 *operand, rxf_u64 need,
                          rxf_u8 **where) {
    switch (operand[0]) {
    case RXF_OPERAND_SLOT:
        if (!in_frame(g, operand[1], need))
            return RXF_REFUSE_OUT_OF_RANGE;
        *where = g->frame + operand[1];
        return RXF_OK;
    case RXF_OPERAND_LITERAL:
        if (need > 8)
            return RXF_REFUSE_OUT_OF_RANGE;
        *where = (rxf_u8 *)&operand[1];
        return RXF_OK;
    case RXF_OPERAND_OBJECT: {
        rxf_object_entry *e = entry(g->c, operand[1]);
        if (!e)
            return RXF_REFUSE_UNBOUND;
        if (need > e->size)
            return RXF_REFUSE_OUT_OF_RANGE;
        *where = (rxf_u8 *)(rxf_uptr)e->payload;
        return RXF_OK;
    }
    default:
        return RXF_REFUSE_OUT_OF_RANGE;
    }
}

/* The value of an operand: a slot or literal as an integer, an object as its payload address. */
RXF_INLINE rxf_u32 load(const rxf_graph *g, const rxf_u64 *operand, rxf_u64 *value) {
    rxf_u8 *where = 0;
    rxf_u32 status;
    if (operand[0] == RXF_OPERAND_OBJECT) {
        status = locate(g, operand, 0, &where);
        *value = (rxf_u64)(rxf_uptr)where;
        return status;
    }
    if (operand[0] == RXF_OPERAND_LITERAL) {
        *value = operand[1];
        return operand[2] > 8 ? RXF_REFUSE_OUT_OF_RANGE : RXF_OK;
    }
    status = locate(g, operand, operand[2] < 8 ? operand[2] : 8, &where);
    *value = status ? 0 : read_bytes(where, operand[2]);
    return status;
}

/* What a pointer parameter receives: the address of a wide slot, else the value. */
RXF_INLINE rxf_u32 pointer(const rxf_graph *g, const rxf_u64 *operand, rxf_u64 *value) {
    rxf_u8 *where = 0;
    rxf_u32 status;
    if (operand[0] == RXF_OPERAND_SLOT && operand[2] > 8) {
        status = locate(g, operand, operand[2], &where);
        *value = (rxf_u64)(rxf_uptr)where;
        return status;
    }
    return load(g, operand, value);
}

/* Copies the bytes of an operand to a destination slot. */
RXF_INLINE rxf_u32 store(const rxf_graph *g, rxf_u64 slot, rxf_u64 width, const rxf_u64 *operand,
                         rxf_u64 offset) {
    rxf_u8 *from;
    rxf_u32 status = locate(g, operand, offset + width, &from);
    if (status)
        return status;
    if (!in_frame(g, slot, width))
        return RXF_REFUSE_OUT_OF_RANGE;
    copy_bytes(g->frame + slot, from + offset, width);
    return RXF_OK;
}

/* A value into a slot: the bytes the source has, the rest of the slot zero. */
RXF_INLINE rxf_u32 move(const rxf_graph *g, rxf_u64 slot, rxf_u64 width, const rxf_u64 *operand) {
    rxf_u64 have_bytes = operand[2] < width ? operand[2] : width;
    if (!in_frame(g, slot, width))
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u8 *from;
    rxf_u32 status = locate(g, operand, have_bytes, &from);
    if (status)
        return status;
    zero_bytes(g->frame + slot, width);
    copy_bytes(g->frame + slot, from, have_bytes);
    return RXF_OK;
}

/* Takes an edge: the copies of its arguments, then the word it jumps to. */
RXF_INLINE rxf_u32 take(const rxf_graph *g, rxf_u64 at, rxf_u64 *next) {
    rxf_u8 staged[RXF_GRAPH_COPY];
    rxf_u64 used = 0;
    if (!have(g, at, RXF_EDGE_HEADER))
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 count = g->code[at + 1];
    if (count > RXF_GRAPH_COPY || !have(g, at + RXF_EDGE_HEADER, count * RXF_EDGE_COPY_WORDS))
        return RXF_REFUSE_OUT_OF_RANGE;
    const rxf_u64 *copy = g->code + at + RXF_EDGE_HEADER;
    for (rxf_u64 i = 0; i < count; i++, copy += RXF_EDGE_COPY_WORDS) {
        rxf_u64 width = copy[4], present = copy[2] < width ? copy[2] : width;
        rxf_u8 *from;
        rxf_u32 status = locate(g, copy, present, &from);
        if (status)
            return status;
        if (width > RXF_GRAPH_COPY - used)
            return RXF_REFUSE_OUT_OF_RANGE;
        zero_bytes(staged + used, width);
        copy_bytes(staged + used, from, present);
        used += width;
    }
    used = 0;
    copy = g->code + at + RXF_EDGE_HEADER;
    for (rxf_u64 i = 0; i < count; i++, copy += RXF_EDGE_COPY_WORDS) {
        if (!in_frame(g, copy[3], copy[4]))
            return RXF_REFUSE_OUT_OF_RANGE;
        copy_bytes(g->frame + copy[3], staged + used, copy[4]);
        used += copy[4];
    }
    *next = g->code[at];
    return RXF_OK;
}

RXF_INLINE rxf_u64 edge_words(const rxf_graph *g, rxf_u64 at) {
    return RXF_EDGE_HEADER + g->code[at + 1] * RXF_EDGE_COPY_WORDS;
}

/* The leaf with this id from the function table of the context. */
RXF_INLINE rxf_u64 find_leaf(const rxf_graph *g, rxf_u64 leaf) {
    const rxf_native_slot *slots = (const rxf_native_slot *)(rxf_uptr)g->c->functions;
    for (rxf_u64 i = 0; i < g->c->function_count; i++)
        if (slots[i].id == leaf)
            return slots[i].address;
    return 0;
}

RXF_INLINE rxf_u32 call(const rxf_graph *g, rxf_u64 at, rxf_u64 *next) {
    const rxf_u64 *word = g->code + at;
    if (!have(g, at, RXF_CALL_HEADER))
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 leaf = word[1], count = word[2], out_slot = word[3], out_width = word[4];
    rxf_u32 handles, has_out;
    rxf_u32 shape = shape_of(leaf);
    if (shape == RXF_SHAPE_NONE || shape_layout(shape, &handles, &has_out) != count ||
        count > RXF_CALL_OPERANDS || !have(g, at, RXF_CALL_HEADER + count * RXF_OPERAND_WORDS))
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 function = find_leaf(g, leaf);
    if (!function)
        return RXF_REFUSE_UNBOUND;
    rxf_u64 value[RXF_CALL_OPERANDS];
    rxf_object_handle handle[RXF_CALL_OPERANDS];
    for (rxf_u64 i = 0; i < count; i++) {
        const rxf_u64 *operand = word + RXF_CALL_HEADER + i * RXF_OPERAND_WORDS;
        rxf_u32 status;
        if (handles >> i & 1) {
            rxf_u8 *from;
            status = locate(g, operand, sizeof(rxf_object_handle), &from);
            if (!status)
                copy_bytes((rxf_u8 *)&handle[i], from, sizeof(rxf_object_handle));
        } else {
            status = pointer(g, operand, &value[i]);
        }
        if (status)
            return status;
    }
    void *out = 0;
    if (has_out) {
        if (out_slot == RXF_GRAPH_NONE || !in_frame(g, out_slot, out_width))
            return RXF_REFUSE_OUT_OF_RANGE;
        out = g->frame + out_slot;
        zero_bytes((rxf_u8 *)out, out_width);
    } else if (out_slot != RXF_GRAPH_NONE) {
        return RXF_REFUSE_OUT_OF_RANGE;
    }
    rxf_runtime_context *c = g->c;
    rxf_u32 result;
    switch (shape) {
    case RXF_SHAPE_QO:
        result = ((rxf_call_qo)(rxf_uptr)function)(value[0], out);
        break;
    case RXF_SHAPE_QQO:
        result = ((rxf_call_qqo)(rxf_uptr)function)(value[0], value[1], out);
        break;
    case RXF_SHAPE_HPQQO:
        result = ((rxf_call_hpqqo)(rxf_uptr)function)(c, handle[0], (void *)(rxf_uptr)value[1],
                                                      value[2], value[3], out);
        break;
    case RXF_SHAPE_HQO:
        result = ((rxf_call_hqo)(rxf_uptr)function)(c, handle[0], value[1], out);
        break;
    case RXF_SHAPE_QQQQQPO:
        result = ((rxf_call_qqqqqpo)(rxf_uptr)function)(c, value[0], value[1], value[2], value[3],
                                                        value[4], (void *)(rxf_uptr)value[5], out);
        break;
    case RXF_SHAPE_P:
        result = ((rxf_call_p)(rxf_uptr)function)(c, (void *)(rxf_uptr)value[0]);
        break;
    case RXF_SHAPE_HQ:
        result = ((rxf_call_hq)(rxf_uptr)function)(c, handle[0], value[1]);
        break;
    case RXF_SHAPE_H:
        result = ((rxf_call_h)(rxf_uptr)function)(c, handle[0]);
        break;
    case RXF_SHAPE_HPQQ:
        result = ((rxf_call_hpqq)(rxf_uptr)function)(c, handle[0], (void *)(rxf_uptr)value[1],
                                                     value[2], value[3]);
        break;
    case RXF_SHAPE_HPQQQ:
        result = ((rxf_call_hpqqq)(rxf_uptr)function)(c, handle[0], (void *)(rxf_uptr)value[1],
                                                      value[2], value[3], value[4]);
        break;
    case RXF_SHAPE_HPW:
        result = ((rxf_call_hpw)(rxf_uptr)function)(c, handle[0], (void *)(rxf_uptr)value[1],
                                                    (rxf_u32)value[2]);
        break;
    case RXF_SHAPE_WWO:
        result = ((rxf_call_wwo)(rxf_uptr)function)((rxf_u32)value[0], (rxf_u32)value[1], out);
        break;
    case RXF_SHAPE_QWO:
        result = ((rxf_call_qwo)(rxf_uptr)function)(value[0], (rxf_u32)value[1], out);
        break;
    case RXF_SHAPE_PQPQQ:
        result = ((rxf_call_pqpqq)(rxf_uptr)function)(
            (void *)(rxf_uptr)value[0], value[1], (void *)(rxf_uptr)value[2], value[3], value[4]);
        break;
    case RXF_SHAPE_PQPQQO:
        result = ((rxf_call_pqpqqo)(rxf_uptr)function)((void *)(rxf_uptr)value[0], value[1],
                                                       (void *)(rxf_uptr)value[2], value[3],
                                                       value[4], out);
        break;
    case RXF_SHAPE_PQBQ:
        result = ((rxf_call_pqbq)(rxf_uptr)function)((void *)(rxf_uptr)value[0], value[1],
                                                     (rxf_u8)value[2], value[3]);
        break;
    default:
        result = ((rxf_call_hqhqpo)(rxf_uptr)function)(c, handle[0], value[1], handle[2], value[3],
                                                       (void *)(rxf_uptr)value[4], out);
        break;
    }
    if (word[5] != RXF_GRAPH_NONE) {
        if (!in_frame(g, word[5], 4))
            return RXF_REFUSE_OUT_OF_RANGE;
        write_bytes(g->frame + word[5], 4, result);
    }
    *next = at + RXF_CALL_HEADER + count * RXF_OPERAND_WORDS;
    return RXF_OK;
}

/* ---- safe points (docs/19, control)
 * -------------------------------------------------------------- Between two instructions the state
 * of a run is whole: the frame and the word index. The run looks at the doorbell of the Channel
 * capability there. A request that waits is served by the live pump (the leaf RXF_LEAF_LIVE_PUMP),
 * and while the peer holds the break the run stays in the pump, like a debugger stub, until the
 * peer clears it. What the peer changed (even the program itself) is looked at again afterwards.
 * These are the words of the live area (core/live/live.h). */
#define RXF_AREA_RX_HEAD 120U
#define RXF_AREA_RX_TAIL 124U
#define RXF_AREA_BREAK_REQUEST 176U
#define RXF_AREA_BREAK_ACK 180U
/* the interpreter is on the stack below the pump: no code may move */
#define RXF_AREA_IN_RUN 256U

RXF_INLINE volatile rxf_u32 *area_word(rxf_u8 *area, rxf_u64 offset) {
    return (volatile rxf_u32 *)(area + offset);
}

/* The area of the first Channel in the capability table of the context, or 0. */
RXF_INLINE rxf_u8 *channel_area(const rxf_runtime_context *c) {
    const rxf_native_slot *table = (const rxf_native_slot *)(rxf_uptr)c->capabilities;
    for (rxf_u64 i = 0; i < c->capability_count; i++)
        if (table[i].id == RXF_CAPABILITY_CHANNEL)
            return (rxf_u8 *)(rxf_uptr)table[i].address;
    return 0;
}

/* Serves what waits at the doorbell; 1 when it did (the run reads its program again). */
RXF_INLINE rxf_u32 safe_point(const rxf_graph *g) {
    rxf_u8 *area = channel_area(g->c);
    if (!area)
        return 0;
    if (*area_word(area, RXF_AREA_RX_HEAD) == *area_word(area, RXF_AREA_RX_TAIL) &&
        !*area_word(area, RXF_AREA_BREAK_REQUEST))
        return 0;
    rxf_u64 pump = find_leaf(g, RXF_LEAF_LIVE_PUMP);
    if (!pump)
        return 0;
    *area_word(area, RXF_AREA_IN_RUN) = 1;
    for (;;) {
        *area_word(area, RXF_AREA_BREAK_ACK) = *area_word(area, RXF_AREA_BREAK_REQUEST) ? 1 : 0;
        ((rxf_call_p)(rxf_uptr)pump)(g->c, area);
        if (!*area_word(area, RXF_AREA_BREAK_REQUEST))
            break;
    }
    *area_word(area, RXF_AREA_BREAK_ACK) = 0;
    *area_word(area, RXF_AREA_IN_RUN) = 0;
    return 1;
}

/* The program object of a run: its words, or why it cannot be run. */
RXF_INLINE rxf_u32 bind(rxf_graph *g, rxf_object_handle program) {
    rxf_object_entry *e = entry(g->c, program.object_id);
    if (!e)
        return RXF_REFUSE_UNBOUND;
    if (e->generation != program.generation)
        return RXF_REFUSE_STALE_GENERATION;
    if (e->state != RXF_OBJECT_LIVE)
        return RXF_REFUSE_UNBOUND;
    if (program.offset > e->size || ((e->payload + program.offset) & 7))
        return RXF_REFUSE_OUT_OF_RANGE;
    g->code = (const rxf_u64 *)(rxf_uptr)(e->payload + program.offset);
    g->count = (e->size - program.offset) / 8;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_graph_run(rxf_runtime_context *c, rxf_object_handle program, rxf_u64 args,
                                   rxf_u64 argc, rxf_u64 *out) {
    if (!valid(c) || !out)
        return RXF_REFUSE_UNBOUND;
    rxf_u8 frame[RXF_GRAPH_FRAME];
    rxf_graph g;
    g.c = c;
    g.frame = frame;
    rxf_u32 bound = bind(&g, program);
    if (bound)
        return bound;
    if (!have(&g, 0, RXF_SLOT_WORDS) || g.code[0] != RXF_GRAPH_MAGIC)
        return RXF_REFUSE_TYPE_MISMATCH;
    g.frame_bytes = g.code[1];
    rxf_u64 parameters = g.code[2], words = g.code[3], pc = g.code[4];
    if (g.frame_bytes > RXF_GRAPH_FRAME || (g.frame_bytes & 7) || words != argc ||
        parameters > g.count || !have(&g, RXF_SLOT_WORDS, parameters * 2))
        return RXF_REFUSE_OUT_OF_RANGE;
    zero_bytes(frame, g.frame_bytes);
    rxf_u64 used = 0;
    for (rxf_u64 i = 0; i < parameters; i++) {
        rxf_u64 slot = g.code[RXF_SLOT_WORDS + 2 * i], width = g.code[RXF_SLOT_WORDS + 2 * i + 1];
        if (!in_frame(&g, slot, width) || width > words * 8 - used)
            return RXF_REFUSE_OUT_OF_RANGE;
        copy_bytes(frame + slot, (const rxf_u8 *)(rxf_uptr)args + used, width);
        used += (width + 7) & ~(rxf_u64)7;
    }

    for (;;) {
        if (safe_point(&g)) {
            bound = bind(&g, program);
            if (bound)
                return bound;
        }
        if (!have(&g, pc, 1))
            return RXF_REFUSE_OUT_OF_RANGE;
        const rxf_u64 *w = g.code + pc;
        rxf_u32 status = RXF_OK;
        rxf_u64 next = 0, value = 0;
        switch (w[0]) {
        case RXF_OP_CALL:
            status = call(&g, pc, &next);
            break;
        case RXF_OP_MOVE:
            if (!have(&g, pc, 6))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = move(&g, w[1], w[2], w + 3);
            next = pc + 6;
            break;
        case RXF_OP_MAP:
            if (!have(&g, pc, 8) || w[7] > g.count || !have(&g, pc, 8 + w[7] * 2) ||
                !in_frame(&g, w[1], w[2]) || !in_frame(&g, w[1] + w[6], 4))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = move(&g, w[1], w[2], w + 3);
            if (!status) {
                rxf_u8 *field = frame + w[1] + w[6];
                rxf_u64 found = read_bytes(field, 4);
                for (rxf_u64 i = 0; i < w[7]; i++)
                    if (w[8 + 2 * i] == found) {
                        write_bytes(field, 4, w[9 + 2 * i]);
                        break;
                    }
            }
            next = pc + 8 + w[7] * 2;
            break;
        case RXF_OP_FIELD:
            if (!have(&g, pc, 8) || w[7] > w[2] || !in_frame(&g, w[1], w[2]))
                return RXF_REFUSE_OUT_OF_RANGE;
            zero_bytes(frame + w[1], w[2]);
            status = store(&g, w[1], w[7], w + 3, w[6]);
            next = pc + 8;
            break;
        case RXF_OP_CONSTRUCT:
            if (!have(&g, pc, 10) || !in_frame(&g, w[1], w[2]) || !in_frame(&g, w[1] + w[3], 4) ||
                w[9] > w[2] || w[8] > w[2] - w[9])
                return RXF_REFUSE_OUT_OF_RANGE;
            zero_bytes(frame + w[1], w[2]);
            write_bytes(frame + w[1] + w[3], 4, w[4]);
            status = store(&g, w[1] + w[8], w[9] < w[7] ? w[9] : w[7], w + 5, 0);
            next = pc + 10;
            break;
        case RXF_OP_LESS: {
            rxf_u64 left, right;
            if (!have(&g, pc, 8) || !in_frame(&g, w[1], 1))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = load(&g, w + 2, &left);
            if (!status)
                status = load(&g, w + 5, &right);
            if (!status)
                frame[w[1]] = left < right;
            next = pc + 8;
            break;
        }
        case RXF_OP_INCREMENT:
            if (!have(&g, pc, 6) || !in_frame(&g, w[1], w[2]))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = load(&g, w + 3, &value);
            if (!status)
                write_bytes(frame + w[1], w[2], value + 1);
            next = pc + 6;
            break;
        case RXF_OP_GOTO:
            status = take(&g, pc + 1, &next);
            break;
        case RXF_OP_BRANCH: {
            if (!have(&g, pc, 5 + RXF_EDGE_HEADER))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = load(&g, w + 1, &value);
            rxf_u64 first = pc + 5;
            if (!status && !have(&g, first, edge_words(&g, first) + RXF_EDGE_HEADER))
                return RXF_REFUSE_OUT_OF_RANGE;
            if (!status)
                status = take(
                    &g,
                    (value == 0) == (w[4] == RXF_MODE_ZERO) ? first : first + edge_words(&g, first),
                    &next);
            break;
        }
        case RXF_OP_SWITCH: {
            if (!have(&g, pc, 5) || w[4] > g.count || !have(&g, pc, 5 + w[4]))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = load(&g, w + 1, &value);
            rxf_u64 chosen = w[4], at = pc + 5 + w[4];
            for (rxf_u64 i = 0; i < w[4] && chosen == w[4]; i++)
                if (w[5 + i] == value)
                    chosen = i;
            for (rxf_u64 i = 0; i < chosen && !status; i++) {
                if (!have(&g, at, RXF_EDGE_HEADER))
                    return RXF_REFUSE_OUT_OF_RANGE;
                at += edge_words(&g, at);
            }
            if (!status)
                status = take(&g, at, &next);
            break;
        }
        case RXF_OP_RETURN:
            if (!have(&g, pc, 5))
                return RXF_REFUSE_OUT_OF_RANGE;
            if (w[4] > 8) {
                rxf_u8 *from;
                status = locate(&g, w + 1, w[4], &from);
                if (!status)
                    copy_bytes((rxf_u8 *)out, from, w[4]);
            } else {
                status = load(&g, w + 1, &value);
                if (!status)
                    *out = value;
            }
            return status;
        case RXF_OP_REFUSE:
            if (!have(&g, pc, 4))
                return RXF_REFUSE_OUT_OF_RANGE;
            status = load(&g, w + 1, &value);
            return status ? status : (rxf_u32)value;
        default:
            return RXF_REFUSE_OUT_OF_RANGE;
        }
        if (status)
            return status;
        pc = next;
    }
}
