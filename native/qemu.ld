ENTRY(_start)
SECTIONS {
  . = 0x40200000;
  .text : { *(.text.start) *(.text .text.*) }
  .rodata : { *(.rodata .rodata.*) }
  .data : { *(.data .data.*) }
  .bss (NOLOAD) : { *(.bss .bss.*) *(COMMON) . = ALIGN(16); . += 0x10000; __stack_top = .; }
}
