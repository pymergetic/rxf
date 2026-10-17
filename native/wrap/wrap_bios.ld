/*
 * The image of a BIOS seat: the boot sector the firmware starts at 0x7C00, the loader
 * and the C of the seat at 0x8000 (the 48 sectors the boot sector reads), and the
 * package as the sectors the loader streams to its staging area. What objcopy turns
 * this into is the image a disk or an emulator boots.
 */
ENTRY(rxf_wrap_stage2_start)

SECTIONS
{
    . = 0x7C00;
    .head : { KEEP(*(.head)) }
    . = 0x8000;
    .loader : {
        KEEP(*(.stage2))
        *(.text*)
        *(.rodata*)
        *(.data*)
        *(.bss*)
        *(COMMON)
        *(.rxftrst)
    }
    . = ALIGN(512);
    .package : { KEEP(*(.rxfpkg)) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}

ASSERT(SIZEOF(.head) == 512, "the boot sector is 512 bytes")
ASSERT(rxf_wrap_package_start - 0x8000 <= 0x6000, "the loader is under 48 sectors")
