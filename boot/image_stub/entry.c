/* The image's entry: the smoke's `rxf_graph_run`.
 *
 * The glue's fourth job ends here: the boot trampolines call this exactly the
 * way tests/graph_world.py's emulator calls the starter's interpreter --
 *   x86_64   System V: the context in rdi, the 24-byte handle by value on the
 *            stack, args in rsi, argc in rdx, out in rcx
 *   aarch64  AAPCS64: the context in x0, the handle as a copy whose address
 *            takes x1, args in x2, argc in x3, out in x4
 *   wasm32   run(i32, i32, i64, i64, i32): the handle by address
 * -- and the handle resolves to the program words of the object it names, the
 * way native/graph.c's bind() does. The resolve follows the map the image was
 * built for (world_v7.h): the one hop a handle takes through the arena's slot
 * table (docs/32 4) -- the index object at the arena's offset 4096, its 16-byte
 * entries, then the record they name.
 *
 * This is a MINI interpreter on purpose: the smoke's program is a MOVE + RETURN
 * and this runs the subset that program needs (MOVE, RETURN, REFUSE), refusing
 * every other opcode RXF_REFUSE_OUT_OF_RANGE exactly as the reference
 * interpreter (tests/graph_reference.py) refuses a word it cannot dispatch. The
 * refusals, the handle resolution and the entry checks are copied rule for rule
 * from native/graph.c so the smoke cannot pass on a looser contract than the
 * real run. When docs/115's ladder replaces this file with the compiled
 * interpreter blob, the call convention above must not move.
 *
 * One thing here is NOT in the real interpreter: at the end of every run this
 * entry reports through the Uart capability -- the answer's bytes (the magic
 * "RXFOK") and native/wrap/wrap.h's `rxf result <status> <out>` line. The seats
 * report so a boot can be observed on a serial line; the real interpreter is
 * silent and the boot mode's report keeps this seam.
 */
#include "graph.h"
#include "uart.h"
#include "world_v7.h"
/* The layouts the guest reads out of the linked image: the superblock, the
 * object record and the slot table (ground.h pins every size), and the live
 * area whose arena_base is where the arena starts (core/live/live.h -- the
 * layout tools/native/link.py writes into every linked image). */
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/live/live.h>

#define CELL_BYTES RXF_GRAPH_CELL_BYTES   /* the run cell: frames are carved from it */
#define REFUSE_UNBOUND RXF_REFUSE_UNBOUND /* stdlib_runtime.h's refusal codes */
#define REFUSE_STALE RXF_REFUSE_STALE_GENERATION
#define REFUSE_TYPE RXF_REFUSE_TYPE_MISMATCH
#define REFUSE_RANGE RXF_REFUSE_OUT_OF_RANGE /* basics.h */
#define REFUSE_BUDGET RXF_REFUSE_CELL_BUDGET

/* The run cell is the FRAME row of the map, found through the context (the
 * context is the linked image's first row; RXF_BOOT_CONTEXT is its row, so the
 * carrier's rows are one subtraction away): one map on every seat, no symbol
 * the wasm module would have to link. The glue zeroes that row before the entry
 * runs, and the run zeroes its frame again per the cell contract (graph.c
 * zeroes the frame). */
typedef char frame_row_is_the_cell[(CELL_BYTES == RXF_BOOT_FRAME_SIZE) ? 1 : -1];

typedef struct {
    rxf_runtime_context *c;
    const rxf_u64 *code; /* the program words */
    rxf_u64 count;       /* their number */
    rxf_u8 *frame;
    rxf_u64 frame_bytes;
} rxf_boot_run;

/* ---- words and bytes ------------------------------------------------------ */

static rxf_u64 word_at(const void *at) {
    const rxf_u8 *p = (const rxf_u8 *)at;
    rxf_u64 v = 0;
    for (rxf_u32 i = 0; i < 8; i++)
        v |= (rxf_u64)p[i] << (8 * i); /* the world is little-endian on every seat */
    return v;
}

static void put_word(void *at, rxf_u64 v) {
    rxf_u8 *p = (rxf_u8 *)at;
    for (rxf_u32 i = 0; i < 8; i++)
        p[i] = (rxf_u8)(v >> (8 * i));
}

static void copy_bytes(void *to, const void *from, rxf_u64 n) {
    rxf_u8 *t = (rxf_u8 *)to;
    const rxf_u8 *f = (const rxf_u8 *)from;
    for (rxf_u64 i = 0; i < n; i++)
        t[i] = f[i];
}

static void zero_bytes(void *to, rxf_u64 n) {
    rxf_u8 *t = (rxf_u8 *)to;
    for (rxf_u64 i = 0; i < n; i++)
        t[i] = 0;
}

/* ---- the object table ---------------------------------------------------- */

/* ---- the slot table of the v7 arena (docs/32 1.2 and 4) ------------------ */

/* The arena of the image, found the way a cold attach finds it (docs/32 1):
 * the context names the capability table, the Channel capability names the
 * live area, and the area names the arena -- whose superblock ("RXFA",
 * version 1) is the one anchor a cold attach needs. */
static const rxf_u8 *v7_arena(const rxf_runtime_context *c) {
    const rxf_native_slot *table = (const rxf_native_slot *)(unsigned long)c->capabilities;
    for (rxf_u64 i = 0; i < c->capability_count; i++) {
        if (table[i].id != RXF_CAPABILITY_CHANNEL)
            continue;
        const pymergetic_rxf_live_area *area =
            (const pymergetic_rxf_live_area *)(unsigned long)table[i].address;
        if (area->magic != PYMERGETIC_RXF_LIVE_MAGIC ||
            area->version != PYMERGETIC_RXF_LIVE_VERSION)
            return 0;
        return (const rxf_u8 *)(unsigned long)area->arena_base;
    }
    return 0;
}

/* The payload one v7 handle names at `offset`, or why it does not resolve.
 * The handle is the slot table's (index, gen) packed in one word (ground.h's
 * handle32), and the identity is checked against BOTH the slot entry and the
 * record it names before a payload byte is touched (ground.h: a stale handle
 * fails its check instead of resolving). */
static rxf_u32 v7_payload(rxf_runtime_context *c, rxf_u64 identity, rxf_u64 offset,
                          const rxf_u8 **payload, rxf_u64 *room) {
    const rxf_u8 *arena = v7_arena(c);
    if (!arena)
        return REFUSE_UNBOUND;
    const pymergetic_rxf_superblock *super = (const pymergetic_rxf_superblock *)arena;
    if (super->magic[0] != 'R' || super->magic[1] != 'X' || super->magic[2] != 'F' ||
        super->magic[3] != 'A' || super->version != PYMERGETIC_RXF_SUPER_VERSION)
        return REFUSE_UNBOUND;
    const rxf_u64 committed = super->committed;
    if (committed < PYMERGETIC_RXF_INDEX_OFFSET + sizeof(pymergetic_rxf_object) ||
        committed > super->limit)
        return REFUSE_UNBOUND;
    /* the index object: the first object of the arena, the table every handle
     * takes its one hop through (docs/32 4) */
    const rxf_u8 *at = arena + PYMERGETIC_RXF_INDEX_OFFSET;
    const pymergetic_rxf_object *index = (const pymergetic_rxf_object *)at;
    if (pymergetic_rxf_object_size(index) > committed - PYMERGETIC_RXF_INDEX_OFFSET)
        return REFUSE_UNBOUND;
    const pymergetic_rxf_slots *slots =
        (const pymergetic_rxf_slots *)(at + sizeof(pymergetic_rxf_object));
    if (slots->count > slots->capacity)
        return REFUSE_UNBOUND;
    const rxf_u32 where = (rxf_u32)identity & PYMERGETIC_RXF_HANDLE32_INDEX_MASK;
    const rxf_u32 generation = (rxf_u32)((identity >> PYMERGETIC_RXF_HANDLE32_INDEX_BITS) &
                                         PYMERGETIC_RXF_HANDLE32_GEN_MASK);
    if (where >= slots->count)
        return REFUSE_UNBOUND;
    const pymergetic_rxf_slot *slot =
        &((const pymergetic_rxf_slot *)(const void *)(slots + 1))[where];
    if (slot->gen != generation)
        return REFUSE_STALE;
    if (slot->slot < PYMERGETIC_RXF_INDEX_OFFSET ||
        (slot->slot & (PYMERGETIC_RXF_OBJECT_ALIGN - 1)) ||
        slot->slot + sizeof(pymergetic_rxf_object) > committed)
        return REFUSE_UNBOUND;
    const pymergetic_rxf_object *record = (const pymergetic_rxf_object *)(arena + slot->slot);
    if (record->gen != generation)
        return REFUSE_STALE;
    if (record->kind != slot->kind || pymergetic_rxf_object_size(record) > committed - slot->slot)
        return REFUSE_UNBOUND;
    if (offset > record->payload)
        return REFUSE_RANGE;
    *payload = (const rxf_u8 *)record + sizeof(pymergetic_rxf_object) + offset;
    *room = record->payload - offset;
    return RXF_OK;
}

/* The payload of one object identity at `offset`, or why it does not resolve:
 * the packed (index, gen) of the arena's slot table (docs/32 4). */
static rxf_u32 object_payload(rxf_runtime_context *c, rxf_u64 identity, rxf_u64 offset,
                              const rxf_u8 **payload, rxf_u64 *room) {
    return v7_payload(c, identity, offset, payload, room);
}

/* ---- the subset the smoke's program runs --------------------------------- */

static rxf_u32 have(const rxf_boot_run *g, rxf_u64 at, rxf_u64 n) {
    return at <= g->count && n <= g->count - at;
}

static rxf_u32 in_frame(const rxf_boot_run *g, rxf_u64 offset, rxf_u64 width) {
    return width <= g->frame_bytes && offset <= g->frame_bytes - width;
}

/* The first `size` bytes of an operand (graph_reference.read). */
static rxf_u32 read_operand(rxf_boot_run *g, const rxf_u64 *op, rxf_u64 size, rxf_u8 *to) {
    rxf_u64 kind = op[0], a = op[1];
    if (kind == RXF_OPERAND_SLOT) {
        if (!in_frame(g, a, size))
            return REFUSE_RANGE;
        copy_bytes(to, g->frame + a, size);
        return RXF_OK;
    }
    if (kind == RXF_OPERAND_LITERAL) {
        if (size > 8)
            return REFUSE_RANGE;
        put_word(to, a);
        return RXF_OK;
    }
    if (kind == RXF_OPERAND_OBJECT) {
        const rxf_u8 *source;
        rxf_u64 room;
        rxf_u32 found = object_payload(g->c, a, 0, &source, &room);
        if (found)
            return found;
        if (size > room)
            return REFUSE_RANGE;
        copy_bytes(to, source, size);
        return RXF_OK;
    }
    return REFUSE_RANGE;
}

/* A slot or literal as an integer, an object as the address of its payload
 * (graph_reference.value). */
static rxf_u64 operand_value(rxf_boot_run *g, const rxf_u64 *op, rxf_u32 *status) {
    rxf_u64 kind = op[0], a = op[1], b = op[2];
    if (kind == RXF_OPERAND_OBJECT) {
        const rxf_u8 *source;
        rxf_u64 room;
        *status = object_payload(g->c, a, 0, &source, &room);
        return *status ? 0 : (rxf_u64)(unsigned long)source;
    }
    if (kind == RXF_OPERAND_LITERAL) {
        if (b > 8)
            *status = REFUSE_RANGE;
        return a;
    }
    rxf_u8 data[8] = {0, 0, 0, 0, 0, 0, 0, 0};
    *status = read_operand(g, op, b < 8 ? b : 8, data);
    return word_at(data);
}

/* MOVE: the operand's first width bytes into the slot, the rest zero (the
 * reference pads with zeros). */
static rxf_u32 op_move(rxf_boot_run *g, rxf_u64 slot, rxf_u64 width, const rxf_u64 *op) {
    rxf_u64 present = op[2] < width ? op[2] : width;
    rxf_u8 data[8];
    rxf_u32 status;
    if (!in_frame(g, slot, width) || present > 8)
        return REFUSE_RANGE;
    status = read_operand(g, op, present, data);
    if (status)
        return status;
    zero_bytes(g->frame + slot, width);
    copy_bytes(g->frame + slot, data, present);
    return RXF_OK;
}

/* ---- the entry ----------------------------------------------------------- */

/* The program object of a run (graph.c's bind): its words, or why it cannot run.
 * The handle is the v7 identity (docs/32 4): word 0 packs the slot's
 * (index, gen) and the generation word must say the same generation -- the
 * stale check the slot table and the record repeat (ground.h). */
static rxf_u32 bind(rxf_boot_run *g, rxf_object_handle program) {
    const rxf_u64 identity = program.object_id;
    if (program.generation !=
        ((identity >> PYMERGETIC_RXF_HANDLE32_INDEX_BITS) & PYMERGETIC_RXF_HANDLE32_GEN_MASK))
        return REFUSE_STALE;
    const rxf_u8 *source;
    rxf_u64 room;
    rxf_u32 status = object_payload(g->c, identity, program.offset, &source, &room);
    if (status)
        return status;
    if ((unsigned long)source & (PYMERGETIC_RXF_OBJECT_ALIGN - 1))
        return REFUSE_RANGE;
    g->code = (const rxf_u64 *)(const void *)source;
    g->count = room / 8;
    return RXF_OK;
}

/* The run: graph.c's rxf_graph_run's checks, then the subset loop. */
static rxf_u32 execute(rxf_runtime_context *c, rxf_object_handle program, rxf_u64 args,
                       rxf_u64 argc, rxf_u64 *out) {
    rxf_boot_run g;
    if (!c || c->version != RXF_RUNTIME_ABI_VERSION || c->size < sizeof(*c) || !out)
        return REFUSE_UNBOUND;
    g.c = c;
    g.frame = (rxf_u8 *)c - RXF_BOOT_CONTEXT + RXF_BOOT_FRAME;
    g.frame_bytes = 0;
    rxf_u32 status = bind(&g, program);
    if (status)
        return status;
    if (!have(&g, 0, 5) || g.code[0] != RXF_GRAPH_MAGIC)
        return REFUSE_TYPE;
    g.frame_bytes = g.code[1];
    rxf_u64 parameters = g.code[2], words = g.code[3], pc = g.code[4];
    if ((g.frame_bytes & 7) || words != argc || parameters > g.count ||
        !have(&g, 5, parameters * 2))
        return REFUSE_RANGE;
    if (g.frame_bytes > CELL_BYTES) /* the chain of one must fit the cell too */
        return REFUSE_BUDGET;
    zero_bytes(g.frame, g.frame_bytes);
    rxf_u64 used = 0;
    for (rxf_u64 i = 0; i < parameters; i++) {
        rxf_u64 slot = g.code[5 + 2 * i], width = g.code[6 + 2 * i];
        /* the reference refuses when width leaves the words the arguments bring */
        if (!in_frame(&g, slot, width) || used + width > words * 8)
            return REFUSE_RANGE;
        copy_bytes(g.frame + slot, (const void *)(unsigned long)(args + used), width);
        used += (width + 7) & ~(rxf_u64)7;
    }
    for (;;) {
        if (!have(&g, pc, 1))
            return REFUSE_RANGE;
        const rxf_u64 *w = g.code + pc;
        if (w[0] == RXF_OP_MOVE) {
            if (!have(&g, pc, 6))
                return REFUSE_RANGE;
            status = op_move(&g, w[1], w[2], w + 3);
            if (status)
                return status;
            pc += 6;
            continue;
        }
        if (w[0] == RXF_OP_RETURN) {
            /* the answer: width > 8 copies the operand's bytes, else its value
             * as one word (the reference answers at least 8 bytes) */
            if (!have(&g, pc, 5))
                return REFUSE_RANGE;
            rxf_u64 width = w[4];
            if (width > 8) {
                if (width > 256) /* graph_world.py's OUT_BYTES is the caller's room */
                    return REFUSE_RANGE;
                status = read_operand(&g, w + 1, width, (rxf_u8 *)out);
                if (status)
                    return status;
            } else {
                rxf_u64 value = operand_value(&g, w + 1, &status);
                if (status)
                    return status;
                put_word(out, value);
            }
            return RXF_OK;
        }
        if (w[0] == RXF_OP_REFUSE) {
            if (!have(&g, pc, 4))
                return REFUSE_RANGE;
            status = RXF_OK;
            rxf_u64 refused = operand_value(&g, w + 1, &status);
            return status ? status : (rxf_u32)(refused & 0xFFFFFFFFU);
        }
        return REFUSE_RANGE; /* a word this subset cannot dispatch */
    }
}

/* ---- the seat report ----------------------------------------------------- */

/* The Uart capability of the world (uart.h's face): the slot the entry reports
 * through, and the one the wasm32 host serves (wasm_shim.c's map_at resolves
 * the FIFO block from it). One walk, one capability model. */
const rxf_native_slot *rxf_boot_uart_capability(rxf_runtime_context *c) {
    const rxf_native_slot *table = (const rxf_native_slot *)(unsigned long)c->capabilities;
    for (rxf_u64 i = 0; i < c->capability_count; i++)
        if (table[i].id == RXF_CAPABILITY_UART)
            return &table[i];
    return 0;
}

static void put_text(const rxf_native_slot *cap, const char *text) {
    for (; *text; text++)
        rxf_boot_uart_putc(cap, (rxf_u8)*text);
}

static void put_hex(const rxf_native_slot *cap, rxf_u64 value, rxf_u32 digits) {
    while (digits--) {
        rxf_u32 d = (rxf_u32)(value >> (4 * digits)) & 0xFU;
        rxf_boot_uart_putc(cap, (rxf_u8)(d < 10 ? '0' + d : 'a' + d - 10));
    }
}

/* The line of a seat (native/wrap/wrap.h): `rxf result <status> <out>`, and on
 * success the magic before it: the answer's bytes as they are -- the program
 * answered "RXFOK", so the UART FIFO says "RXFOK". */
static void report(rxf_runtime_context *c, rxf_u32 status, rxf_u64 answer) {
    const rxf_native_slot *cap = rxf_boot_uart_capability(c);
    if (!cap) /* no Uart capability: the run answers, it just cannot say so */
        return;
    if (!status) {
        for (rxf_u64 v = answer; v & 0xFFU; v >>= 8)
            rxf_boot_uart_putc(cap, (rxf_u8)v);
        rxf_boot_uart_putc(cap, '\n');
    }
    put_text(cap, "rxf result ");
    put_hex(cap, status, 8);
    rxf_boot_uart_putc(cap, ' ');
    put_hex(cap, answer, 16);
    rxf_boot_uart_putc(cap, '\n');
}

/* The entry the glue calls (native/graph.h's declaration is the contract). It
 * lands at the image's entry row (world_v7.h's RXF_BOOT_ENTRY). */
__attribute__((section(".text.entry"))) rxf_u32 rxf_graph_run(rxf_runtime_context *c,
                                                              rxf_object_handle program,
                                                              rxf_u64 args, rxf_u64 argc,
                                                              rxf_u64 *out) {
    rxf_u32 status = execute(c, program, args, argc, out);
    report(c, status, out ? word_at(out) : 0);
    return status;
}
