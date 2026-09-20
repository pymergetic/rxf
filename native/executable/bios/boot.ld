OUTPUT_FORMAT(elf32-i386)
ENTRY(_start)
SECTIONS { . = 0x7c00; .boot : { *(.boot) } }
ASSERT(SIZEOF(.boot) == 512, "boot sector must be exactly 512 bytes")
