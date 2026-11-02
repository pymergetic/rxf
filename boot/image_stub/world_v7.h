/* boot/image_stub/world_v7.h -- the map every boot seat establishes (docs/32).
 *
 * Every seat boots the REAL linked image: the memory tools/native/link.py makes
 * of the starter (the CONTEXT, ENTRIES and ARENA rows of its docstring), laid
 * out per docs/32 1 -- one arena, a 4 KiB superblock at its byte 0 (magic
 * "RXFA", version 1), and the object index as the first object at offset 4096:
 * a 24-byte record, then the slot head (count, capacity) and 16-byte slot
 * entries (ground.h pins every size). tools/native/boot_image.py packs those
 * bytes per machine with the carrier rows below and the compiled entry code
 * (image_stub/entry.c), and each seat loads the pack its own way: the x86_64
 * BIOS sector streams it from a disk (x86_64.S), the UEFI stub reads it via
 * the file protocol (uefi_stub.c), the aarch64 boot ELF carries its bytes
 * (aarch64.S), and the wasm32 host writes it into linear memory
 * (wasm_shim.js).
 *
 * The rows below the LINKED row are the carrier: what the glue and the loader
 * need beside the image. They keep the row offsets of the map
 * tests/graph_world.py's world established, so the entry convention the smoke
 * asserts -- the handle, the arguments, the answer, the run cell -- is the same
 * one on every map. The rows of the linked image itself are tools/native/
 * link.py's: its first bytes are the runtime context (144), and the walk from
 * there to the arena is the v7 image's own --
 *
 *   context (base + RXF_BOOT_CONTEXT, the linked image's byte 0)
 *     -> capability table (context words 6 and 7)
 *     -> the Channel capability -> the live area (core/live/live.h)
 *     -> the arena (the area's arena_base): superblock at 0, the object
 *        index at PYMERGETIC_RXF_INDEX_OFFSET (4096), and every handle one
 *        hop through the slot table it carries (docs/32 4).
 *
 * The guest takes that walk in image_stub/entry.c: nothing in this map pins a
 * row of the linked image that the linked image does not put there itself.
 *
 *   base + 0x0000  HEAD         the loader's row: the chunk count of the whole
 *                               image (64 KiB chunks), then its byte count
 *   base + 0x5800  ARGS         one argument word ("RXFOK")
 *   base + 0x5900  OUT          0x100 bytes of answer
 *   base + 0x8600  HANDLE_COPY  the 24-byte v7 handle the glue passes
 *   base + 0x8800  FRAME        0x8000: the run cell (RXF_GRAPH_CELL_BYTES)
 *   base + 0x10800 FIFO         0x240: the zero row the glue owns (the wasm
 *                               seat's Uart block is the one the linked
 *                               image's capability names -- see FIFO below)
 *   base + 0x11000 ENTRY        the image's entry code (rxf_graph_run)
 *   base + 0x20000 LINKED       the linked image: CONTEXT at its byte 0
 */
#ifndef RXF_BOOT_WORLD_V7_H
#define RXF_BOOT_WORLD_V7_H

/* The carrier rows. The shared names carry the entry convention; only the
 * CONTEXT row differs from a stub map: it is the linked image's first bytes. */
#define RXF_BOOT_HEAD 0x0000u
#define RXF_BOOT_HEAD_CHUNKS 0x0000u /* u16: 64 KiB chunks the seat loads */
#define RXF_BOOT_HEAD_BYTES 0x0004u  /* u32: the image's size, chunk-aligned */
#define RXF_BOOT_CONTEXT 0x20000u    /* the linked image's runtime context */
#define RXF_BOOT_LINKED RXF_BOOT_CONTEXT
#define RXF_BOOT_ARGS 0x5800u
#define RXF_BOOT_OUT 0x5900u
#define RXF_BOOT_HANDLE_COPY 0x8600u
#define RXF_BOOT_FRAME 0x8800u
#define RXF_BOOT_FRAME_SIZE 0x8000u /* RXF_GRAPH_CELL_BYTES */
#define RXF_BOOT_FIFO 0x10800u
#define RXF_BOOT_FIFO_SIZE 0x240u /* RXF_UART_FIFO_BLOCK */
#define RXF_BOOT_ENTRY 0x11000u
#define RXF_BOOT_BSS RXF_BOOT_FRAME
#define RXF_BOOT_BSS_END 0x10B00u /* FRAME and FIFO: the rows that start zero */
#define RXF_BOOT_ARGC 1u         /* the smoke's program takes one word */

/* The handle the glue passes is the v7 identity (docs/32 4): word 0 is the
 * handle32 of the program's record (index in the low 20 bits, generation in
 * the high 12), word 1 the generation again (the stale check the entry makes
 * against the slot table and the record), word 2 the offset into the object's
 * payload. tools/native/boot_image.py writes the words at this row. */

/* The chunk the loader reads at once: 64 KiB, so one read never crosses the
 * 64 KiB boundary an EDD transfer must not cross (x86_64.S's DAP loop) and the
 * UEFI file reads stay inside one buffer (uefi_stub.c). */
#define RXF_BOOT_CHUNK 0x10000u

/* The bases: where the machine has room, per seat. A flat image that runs to
 * tens of megabytes cannot sit low: the x86_64 seats run it above the firmware
 * rooms -- OVMF hands its own allocations out around and below 16 MiB, so the
 * image starts at 0x4000000 (the same room the package seat runs in,
 * tests/test_boot_qemu.py) -- QEMU virt has its DRAM at 0x40000000, and wasm32
 * keeps the module's own data below the image's room in linear memory. */
#define RXF_BOOT_X86_IMAGE_BASE 0x4000000ULL  /* the flat x86_64 image */
#define RXF_BOOT_A64_IMAGE_BASE 0x40100000ULL /* QEMU virt DRAM */
#define RXF_BOOT_WASM_IMAGE_BASE 0x10000ULL   /* wasm linear memory BASE */

/* The x86_64 seats' absolute addresses (their glue and stub speak in them). */
#define RXF_BOOT_X86_ENTRY (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_ENTRY)
#define RXF_BOOT_X86_BSS (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_BSS)
#define RXF_BOOT_X86_BSS_END (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_BSS_END)
#define RXF_BOOT_X86_HANDLE (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_HANDLE_COPY)
#define RXF_BOOT_X86_CONTEXT (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_CONTEXT)
#define RXF_BOOT_X86_ARGS (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_ARGS)
#define RXF_BOOT_X86_OUT (RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_OUT)
#define RXF_BOOT_X86_ROOM RXF_BOOT_ROOM

/* The disk the BIOS sector reads the image from: after the boot sector and the
 * four sectors of stage2 (x86_64.S's disk layout, Makefile build/disk.img). */
#define RXF_BOOT_LBA 5ULL

/* The room a loader must find at the base: the carrier (128 KiB, two chunks of
 * the rows below) and the linked image behind it. The linked image grows with
 * the world; the loader reads the count the head carries, so no row here pins
 * its size. */
#define RXF_BOOT_ROOM 0x20000u

/* The smoke's magic port (QEMU isa-debug-exit: exit code (byte << 1) | 1). */
#define RXF_BOOT_MAGIC_PORT 0xF4u
#define RXF_BOOT_MAGIC_OK 0x10u
#define RXF_BOOT_MAGIC_FAIL 0x11u

#endif /* RXF_BOOT_WORLD_V7_H */
