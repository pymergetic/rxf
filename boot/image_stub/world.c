/* The smoke's world: the tiniest object world that is still an object world.
 *
 * A runtime context (144 bytes, stdlib_runtime.h), an object table with one
 * program object, the program words (MOVE + RETURN, native/graph.h), one
 * argument word, the answer slot, the 24-byte handle the glue passes, the
 * TEXT and AREA rows, and the capability table with the Uart capability of
 * the seat.  That is the whole payload: no leaves, no heap traffic, no
 * journal use -- the boot smoke proves the glue's four jobs, not the
 * interpreter's breadth.
 *
 * Every row lands on a tests/graph_world.py offset (world.h, image.ld).  The
 * addresses inside the world are link-time relocations, so one source serves
 * every seat; the base is the only moving part.  The seat's Uart capability
 * is chosen by RXF_BOOT_SEAT_* (the Makefile's -D), not by the compiler's
 * machine macros: the wasm32 world is data, and the host toolchain links it.
 */
#include "graph.h" /* the world's own layouts: magic, opcodes, capability ids */
#include "world.h"

/* The world's own base, from the link (image.ld): the wasm capability needs a
 * real address for its FIFO block, and the block sits at base + RXF_BOOT_FIFO. */
extern rxf_u8 _world_base[];

/* The FIFO block of the wasm32 capability (graph.h: rx_head, rx_tail, tx_head,
 * tx_tail, then the two rings).  A zero row (BSS in spirit: the glue zeroes it,
 * job 3, and the image carries only zeros there): only wasm32's fill and host
 * move bytes through it. */
rxf_u8 rxf_boot_fifo[RXF_BOOT_FIFO_SIZE] __attribute__((section(".world.fifo")));

/* ---- the program words: one MOVE + RETURN that answers the argument word ----
 *
 *  0 magic, 1 frame bytes, 2 parameters, 3 argument words, 4 entry word
 *  5.. one (slot, width) per parameter, then the instructions.
 * The run takes one word of argument at slot 0, moves it to slot 8 and returns
 * it: the answer is the constant "RXFOK" only because the glue passed it as
 * the argument -- every leg of the entry convention (handle, args, argc, out)
 * is on the path of the magic bytes. */
static rxf_u64 rxf_boot_program[] __attribute__((section(".world.program"))) = {
    RXF_GRAPH_MAGIC, 64, 1, 1, 7, /* 7 = 5 + 2 parameters */
    0, 8, /* parameter 0 lives at frame slot 0, one word */
    RXF_OP_MOVE, 8, 8, RXF_OPERAND_SLOT, 0, 8, /* frame[8] = frame[0] */
    RXF_OP_RETURN, RXF_OPERAND_SLOT, 8, 8, 8, /* answer frame[8..16) */
};

/* The argument the glue passes: "RXFOK" as one little-endian word (the string's
 * bytes R,X,F,O,K low to high: 0x4B4F465852). */
rxf_u64 rxf_boot_args[] __attribute__((section(".world.args"))) = {0x0000004B4F465852ULL};

/* Where the answer lands (graph_world.py's OUT: 256 bytes of caller's room). */
rxf_u64 rxf_boot_out[32] __attribute__((section(".world.out")));

/* The TEXT row of the map: the world's identity line, like the one every
 * graph_world.py world carries. */
static const rxf_u8 rxf_boot_text[64] __attribute__((section(".world.text"), used)) =
    "boot-smoke world";

/* The handle the glue passes: (object id, generation, offset) of the program
 * object -- tests/graph_world.py's handle (900001, 1, 0), and the same words
 * its host writes at HANDLE_COPY for the wasm32 call. */
rxf_u64 rxf_boot_handle[] __attribute__((section(".world.handle"))) = {
    RXF_BOOT_PROGRAM_ID, RXF_BOOT_GENERATION, 0};

/* The object table: one entry, sorted by id (stdlib_runtime.h's halving).
 * The payload of the entry is the program words above -- graph_world.py's
 * entry(PROGRAM_ID, PROGRAM, len(case.program)). */
static rxf_object_entry rxf_boot_objects[] __attribute__((section(".world.objects"))) = {
    {RXF_BOOT_PROGRAM_ID, RXF_BOOT_GENERATION, 0, (rxf_u64)(unsigned long)rxf_boot_program,
     sizeof(rxf_boot_program), sizeof(rxf_boot_program), RXF_OBJECT_LIVE, 0, 0, 0},
};

/* The capability table: the Uart capability of the seat.  The generation word
 * and the base are the only per-machine bytes in the whole world (graph.h:
 * the kind in the low byte, the register shift in bits 8..11, RXF_UART_WIDE
 * when registers are 32-bit words). */
static rxf_native_slot rxf_boot_capabilities[] __attribute__((section(".world.capabilities"))) = {
#if defined(RXF_BOOT_SEAT_X86)
    /* the 16550 behind I/O ports: COM1, registers a port apart, bytes */
    {RXF_CAPABILITY_UART, RXF_UART_DEVICE_NS16550_PORT, 0x3F8},
#elif defined(RXF_BOOT_SEAT_A64)
    /* the PL011 of QEMU virt: registers four bytes apart (shift 2), words */
    {RXF_CAPABILITY_UART, RXF_UART_DEVICE_PL011_MMIO | (2ULL << RXF_UART_SHIFT_AT) | RXF_UART_WIDE,
     0x09000000},
#elif defined(RXF_BOOT_SEAT_WASM)
    /* no device to trap a load: the two FIFOs of graph.h's block in memory */
    {RXF_CAPABILITY_UART, 0, (rxf_u64)(unsigned long)&_world_base[RXF_BOOT_FIFO]},
#else
#error "the boot world needs a seat: RXF_BOOT_SEAT_X86, _A64 or _WASM"
#endif
};

/* The regions the context names but the smoke never writes: journal, live
 * area, heap.  They exist so the context is a real one (size 144, every
 * pointer inside the world) and so the map matches graph_world.py's row for
 * row. */
static rxf_u8 rxf_boot_journal[0x300] __attribute__((section(".world.journal"), used));
static rxf_u8 rxf_boot_area[272] __attribute__((section(".world.area"), used));
rxf_u8 rxf_boot_heap[RXF_BOOT_HEAP_SIZE] __attribute__((section(".world.heap")));

/* The context itself (stdlib_runtime.h: version 2, size 144). */
rxf_runtime_context rxf_boot_context __attribute__((section(".world.context"))) = {
    RXF_RUNTIME_ABI_VERSION,
    sizeof(rxf_runtime_context),
    0,
    0, /* no leaves: a MOVE + RETURN program calls none */
    sizeof(rxf_boot_objects) / sizeof(rxf_boot_objects[0]),
    (rxf_u64)(unsigned long)rxf_boot_objects,
    sizeof(rxf_boot_capabilities) / sizeof(rxf_boot_capabilities[0]),
    (rxf_u64)(unsigned long)rxf_boot_capabilities,
    (rxf_u64)(unsigned long)rxf_boot_heap,
    sizeof(rxf_boot_heap),
    sizeof(rxf_boot_heap),
    0,
    0,
    4,
    (rxf_u64)(unsigned long)rxf_boot_journal,
    0,
    0,
    0,
};
