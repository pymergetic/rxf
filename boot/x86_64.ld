/* boot/x86_64.ld -- the boot sector and the stage2 of boot/x86_64.S.
 *
 * The sector is exactly one 512-byte block at the BIOS load address (0x7C00)
 * with its boot signature; stage2 sits at 0x8000 where the sector's INT 13h
 * reads land it. The image is linked by image_stub/image.ld, separately: the
 * glue and the object world stay two binaries so the world can be swapped
 * without reassembling the boot path. */
ENTRY(_start16)

SECTIONS
{
    . = 0x7c00;
    .bootsector : { *(.bootsector) }
    . = 0x8000;
    .stage2 : { *(.stage2) }

    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}

ASSERT(SIZEOF(.bootsector) == 512, "the boot sector is exactly one sector");
ASSERT(SIZEOF(.stage2) <= 0x800, "stage2 must fit the 4 sectors the sector reads");
