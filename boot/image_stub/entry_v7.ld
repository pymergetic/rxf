/* boot/image_stub/entry_v7.ld -- the entry code of the v7 image, at its row.
 *
 * The x86_64-bios seat's image is tools/native/link.py's linked image (the
 * CONTEXT/ENTRIES/ARENA rows of docs/32 1) with the entry code in the carrier
 * room below it: this script links that code where the map says it lands
 * (world_v7.h, image_stub/entry.c), and the glue calls it there. The base
 * moves with --defsym=IMAGE_BASE (0x10000 on x86_64); the rows never do.
 *
 * The .bss below is zero because the carrier row starts zero (the glue also
 * zeroes the FRAME and FIFO rows, the third of its four jobs): a static of the
 * entry must land in the room between the entry row and the linked image.
 */

SECTIONS
{
    . = IMAGE_BASE + 0xB000;
    _entry_base = .;

    .text.entry        : { KEEP(*(.text.entry)) }
    .text              : { *(.text*) }
    .rodata            : { *(.rodata*) }
    .data              : { *(.data*) }
    .bss               : { *(.bss*) *(COMMON) }

    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) *(.ARM.attributes) }
}

/* The entry sits at its row and stays clear of the linked image behind it
 * (world_v7.h: ENTRY 0xB000, LINKED 0x10000). */
ASSERT(_entry_base == IMAGE_BASE + 0xB000, "the entry code does not start at its row")
ASSERT(SIZEOF(.bss) + ADDR(.bss) <= IMAGE_BASE + 0x10000, "the entry code overlaps the linked image")
