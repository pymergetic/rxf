/* boot/image_stub/world.h -- the memory map every boot glue establishes.
 *
 * The rows below are the rows of tests/graph_world.py, offset for offset: the
 * smoke's world is placed exactly where the wasm world of the real run lives,
 * so a boot smoke that passes cannot be pointing at a different map than the
 * one tests/graph_world.py compares.  The base moves per machine (a flat
 * binary runs where its machine has room), the offsets never do.
 *
 *   base + 0x0000  CONTEXT     144B runtime context (stdlib_runtime.h)
 *   base + 0x0100  JOURNAL     0x300 journal row
 *   base + 0x0400  OBJECTS     the object table (80B entries)
 *   base + 0x3100  PAYLOADS    the payload bump row (the smoke adds none)
 *   base + 0x4000  PROGRAM     the program words (the one object's payload)
 *   base + 0x5800  ARGS        one argument word ("RXFOK")
 *   base + 0x5900  OUT         0x100 bytes of answer
 *   base + 0x5A00  TEXT        64B identity row
 *   base + 0x5B00  SET_HEADER  56B   (collection rows; the smoke's are empty)
 *   base + 0x5B40  SET_BUCKETS 192B
 *   base + 0x5C00  MAP_HEADER  56B
 *   base + 0x5C40  MAP_BUCKETS 384B
 *   base + 0x5E00  AREA        272B idle live area
 *   base + 0x5F40  CAPABILITIES the Uart capability slot
 *   base + 0x6000  HEAP        0x2000 heap row -- here the compared world ends
 *   base + 0x8000  FUNCTIONS   the function table row (empty here)
 *   base + 0x8600  HANDLE_COPY the 24-byte handle the glue passes
 *   base + 0x8800  FRAME       0x2000: the run cell (RXF_GRAPH_CELL_BYTES)
 *   base + 0xA800  FIFO        0x240: the wasm seat's Uart device block
 *   base + 0xB000  ENTRY       the image's entry code
 *
 * FUNCTION/HANDLE_COPY/FRAME hold the offsets tests/graph_world.py uses
 * (SIZE + 0, SIZE + 0x600, SIZE + 0x800).  FIFO and ENTRY are rows this smoke
 * adds past the mapped end: the wasm Uart needs a device block in memory, and
 * a flat image needs its entry code somewhere.  The glue zeroes the BSS rows
 * (FRAME and FIFO) before the entry runs -- the third of the four jobs.
 */
#ifndef RXF_BOOT_WORLD_H
#define RXF_BOOT_WORLD_H

#define RXF_BOOT_CONTEXT 0x0000u
#define RXF_BOOT_JOURNAL 0x0100u
#define RXF_BOOT_OBJECTS 0x0400u
#define RXF_BOOT_PAYLOADS 0x3100u
#define RXF_BOOT_PROGRAM 0x4000u
#define RXF_BOOT_ARGS 0x5800u
#define RXF_BOOT_OUT 0x5900u
#define RXF_BOOT_TEXT 0x5A00u
#define RXF_BOOT_SET_HEADER 0x5B00u
#define RXF_BOOT_SET_BUCKETS 0x5B40u
#define RXF_BOOT_MAP_HEADER 0x5C00u
#define RXF_BOOT_MAP_BUCKETS 0x5C40u
#define RXF_BOOT_AREA 0x5E00u
#define RXF_BOOT_CAPABILITIES 0x5F40u
#define RXF_BOOT_HEAP 0x6000u
#define RXF_BOOT_HEAP_SIZE 0x2000u
#define RXF_BOOT_WORLD_SIZE 0x8000u /* what tests/graph_world.py compares */
/* The one object: graph_world.py's PROGRAM_ID and the generation its handle
 * carries (tests/graph_world.py: handle = (PROGRAM_ID, 1, 0)). */
#define RXF_BOOT_PROGRAM_ID 900001u
#define RXF_BOOT_GENERATION 1u
#define RXF_BOOT_FUNCTIONS 0x8000u
#define RXF_BOOT_HANDLE_COPY 0x8600u
#define RXF_BOOT_FRAME 0x8800u
#define RXF_BOOT_FRAME_SIZE 0x2000u /* RXF_GRAPH_CELL_BYTES */
#define RXF_BOOT_FIFO 0xA800u
#define RXF_BOOT_FIFO_SIZE 0x240u /* RXF_UART_FIFO_BLOCK */
#define RXF_BOOT_ENTRY 0xB000u
#define RXF_BOOT_BSS RXF_BOOT_FRAME
#define RXF_BOOT_BSS_END 0xAB00u /* FRAME and FIFO: the rows that start zero */
#define RXF_BOOT_ARGC 1u          /* the smoke's program takes one word */

/* The bases: where the machine has room, per seat (base/README.md). */
#define RXF_BOOT_X86_IMAGE_BASE 0x10000ULL /* the flat x86_64 image */
#define RXF_BOOT_A64_IMAGE_BASE 0x40100000ULL /* QEMU virt DRAM */
#define RXF_BOOT_WASM_IMAGE_BASE 0x10000ULL /* wasm linear memory BASE */

/* The x86_64 seat's absolute addresses (its glue and stub speak in them). */
#define RXF_BOOT_X86_ENTRY (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_ENTRY)
#define RXF_BOOT_X86_BSS (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_BSS)
#define RXF_BOOT_X86_BSS_END (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_BSS_END)
#define RXF_BOOT_X86_HANDLE (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_HANDLE_COPY)
#define RXF_BOOT_X86_ROOM RXF_BOOT_ROOM

/* The disk the BIOS sector reads the image from: after the boot sector and the
 * four sectors of stage2 (x86_64.S's disk layout, Makefile build/disk.img). */
#define RXF_BOOT_LBA 5ULL

/* The room a loader must find at the base: the flat image runs past the BSS
 * rows (the entry code sits at +0xB000) and the zero rows sit inside it, so a
 * loader's reservation is the image's size, not the map's end. */
#define RXF_BOOT_ROOM 0x10000u

/* The smoke's magic port (QEMU isa-debug-exit: exit code (byte << 1) | 1). */
#define RXF_BOOT_MAGIC_PORT 0xF4u
#define RXF_BOOT_MAGIC_OK 0x10u
#define RXF_BOOT_MAGIC_FAIL 0x11u

#endif /* RXF_BOOT_WORLD_H */
