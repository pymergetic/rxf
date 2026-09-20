ENTRY(_start)
PHDRS {
  text PT_LOAD FLAGS(5);
  rodata PT_LOAD FLAGS(4);
  data PT_LOAD FLAGS(6);
  stack PT_GNU_STACK FLAGS(6);
}
SECTIONS {
  . = 0x401000;
  .text : ALIGN(0x1000) { *(.text .text.*) } :text
  . = 0x402000;
  .rodata : ALIGN(0x1000) { *(.rodata .rodata.*) } :rodata
  . = 0x403000;
  .data : ALIGN(0x1000) { BYTE(0); *(.data .data.*) } :data
  .bss : ALIGN(16) { *(.bss .bss.* COMMON) } :data
  /DISCARD/ : { *(.comment .note .eh_frame*) }
}
