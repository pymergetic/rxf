/*
 * The layout of the ELF seat: one static executable at 0x400000 with the package in its
 * own section behind the code. The sections start on pages of their own so no two
 * segments of the loader share one (a page that two segments map is the second one's
 * to the kernel, and the first loses its permissions). The seat maps its block at the
 * base the package names (native/wrap/wrap_elf.c), so nothing here carries the run.
 */
ENTRY(_start)

SECTIONS
{
    . = 0x400000;
    .text : { *(.text*) }
    . = ALIGN(0x1000);
    .rodata : { *(.rodata*) *(.rxftrst) }
    . = ALIGN(0x1000);
    .data : { *(.data*) }
    . = ALIGN(0x1000);
    .rxfpkg : { KEEP(*(.rxfpkg)) }
    . = ALIGN(0x1000);
    .bss : { *(.bss*) *(COMMON) }
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
