/* boot/aarch64.ld -- the boot ELF qemu -kernel loads: the glue's code low, the
 * packed image's bytes at the map base (world_v7.h's, 0x40100000). The rows of
 * the world live in the image, packed by tools/native/boot_image.py; this
 * script only says where the two pieces of the ELF land. The ELF is the seat's
 * container (like x86_64's disk.img): the pinned body of the glue is the bytes
 * it carries without the image (Makefile build/boot_aarch64.bin). */
SECTIONS
{
    . = 0x40080000; /* QEMU virt DRAM: the kernel load address */
    .vectors    : { KEEP(*(.vectors)) }     /* 2 KiB-aligned: VBAR_EL1's table */
    .boot.text  : { KEEP(*(.boot.text)) }
    .boot.bss (NOLOAD) : { KEEP(*(.boot.bss)) } /* the stack */

    . = 0x40100000; /* the image's base (world_v7.h) */
    .image      : { KEEP(*(.image)) }       /* build/image_aarch64.bin, incbin'd */
}
