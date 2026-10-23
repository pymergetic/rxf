/* boot/aarch64.ld -- the boot ELF qemu -kernel loads: the glue's code low, the
 * image's bytes at the map base (image.ld's, 0x40100000). The rows of the
 * world live in the image, built by image.ld; this script only says where the
 * two pieces of the ELF land. */
SECTIONS
{
    . = 0x40080000; /* QEMU virt DRAM: the kernel load address */
    .vectors    : { KEEP(*(.vectors)) }     /* 2 KiB-aligned: VBAR_EL1's table */
    .boot.text  : { KEEP(*(.boot.text)) }
    .boot.bss (NOLOAD) : { KEEP(*(.boot.bss)) } /* the stack */

    . = 0x40100000; /* the world's base (world.h) */
    .image      : { KEEP(*(.image)) }       /* build/image_aarch64.bin, incbin'd */
}
