/* boot/image_stub/image.ld -- the flat image: the world's rows at their map
 * offsets, the entry code at its row.  One script for every seat; the base
 * moves with --defsym=IMAGE_BASE (0x10000 on x86_64 and wasm32, 0x40100000 on
 * QEMU virt aarch64) and every row offset is tests/graph_world.py's.
 *
 * Gaps between rows are zeros in the flat binary (objcopy fills them); the
 * BSS rows are not in the binary at all -- the glue zeroes them before the
 * entry runs (the third of its four jobs, boot/image_stub/world.h).
 */
/* The base is the link's choice (--defsym=IMAGE_BASE=...): 0x10000 on x86_64
 * and wasm32, 0x40100000 on QEMU virt aarch64. There is no fallback on
 * purpose: one link, one base, no script-side default to drift from it. */

SECTIONS
{
    . = IMAGE_BASE;
    _world_base = .;

    /* ---- the compared world: tests/graph_world.py, row for row ---- */
    .world.context     : { KEEP(*(.world.context)) }
    . = IMAGE_BASE + 0x0100;
    .world.journal     : { KEEP(*(.world.journal)) }
    . = IMAGE_BASE + 0x0400;
    .world.objects     : { KEEP(*(.world.objects)) }
    . = IMAGE_BASE + 0x4000; /* PAYLOADS (0x3100) stays a zero gap: the smoke adds none */
    .world.program     : { KEEP(*(.world.program)) }
    . = IMAGE_BASE + 0x5800;
    .world.args        : { KEEP(*(.world.args)) }
    . = IMAGE_BASE + 0x5900;
    .world.out         : { KEEP(*(.world.out)) }
    . = IMAGE_BASE + 0x5A00;
    .world.text        : { KEEP(*(.world.text)) }
    /* 0x5B00..0x5E00 stays a zero gap: SET/MAP rows the smoke's run never opens */
    . = IMAGE_BASE + 0x5E00;
    .world.area        : { KEEP(*(.world.area)) }
    . = IMAGE_BASE + 0x5F40;
    .world.capabilities : { KEEP(*(.world.capabilities)) }
    . = IMAGE_BASE + 0x6000;
    .world.heap        : { KEEP(*(.world.heap)) }
    _world_heap_end = .;

    /* ---- behind the compared memory (graph_world.py's tail rows) ---- */
    . = IMAGE_BASE + 0x8600;
    .world.handle      : { KEEP(*(.world.handle)) }
    /* 0x8800 FRAME is a zero gap and 0xA800 FIFO a zero row: BSS in spirit,
     * never loaded as anything but zeros, and zeroed by the glue (world.h) */
    . = IMAGE_BASE + 0xA800;
    .world.fifo        : { KEEP(*(.world.fifo)) }

    /* ---- the image's entry code: the interpreter's seat-side face ---- */
    . = IMAGE_BASE + 0xB000;
    .text.entry        : { KEEP(*(.text.entry)) }
    .text              : { *(.text*) }
    .rodata            : { *(.rodata*) }
    .data              : { *(.data*) }
    /* the compiler's own BSS: the smoke compiles none (the zero rows are
     * FRAME and FIFO, which the glue zeroes); a real image grows world.h's
     * BSS range with whatever its C accumulates here */
    .bss               : { *(.bss*) *(COMMON) }

    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.attributes) }
}

PROVIDE(_world_base = IMAGE_BASE);

/* The rows are the contract: the heap row ends where the compared world ends,
 * and the whole world sits at the base the machine chose. */
ASSERT(_world_heap_end == IMAGE_BASE + 0x8000, "heap row does not end at the world's end")
ASSERT(_world_base == IMAGE_BASE, "the world does not sit at the base")
