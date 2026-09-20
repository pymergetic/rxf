OUTPUT_FORMAT(elf64-x86-64)
ENTRY(stage2_start)
SECTIONS {
 . = 0x8000;
 .text : { *(.start) *(.text .text.*) }
 .rodata : { *(.rodata .rodata.*) }
 .data : { *(.data .data.*) }
 .bss : { *(.bss .bss.*) *(COMMON) }
 /DISCARD/ : { *(.comment) *(.eh_frame*) *(.note*) }
}
ASSERT(. <= 0x9000, "stage2 exceeds 4096 bytes")
