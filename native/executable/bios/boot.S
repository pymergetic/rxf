.code16
.section .boot,"ax"
.global _start
_start:
  cli
  xorw %ax,%ax
  movw %ax,%ds
  movw %ax,%ss
  movw $0x7c00,%sp
  movb %dl,drive
  sti
  /* Only the stage and manifest fit below the real-mode 64 KiB boundary.
     Stage 2 streams the remaining sectors through a bounded bounce buffer. */
  movw $16,%cx
  movw $0x8000,%bx
  movl $1,dap_lba
  movl $0,dap_lba+4
1:
  movw %bx,dap_off
  movb drive,%dl
  movw $dap,%si
  movb $0x42,%ah
  int $0x13
  jc fail
  addw $512,%bx
  incl dap_lba
  loop 1b
  movb drive,%dl
  ljmp $0,$0x8000
fail:
  movw $0x3f8,%dx
  movb $0x45,%al
  outb %al,%dx
2: hlt
  jmp 2b
.align 4
dap: .byte 0x10,0
     .word 1
dap_off: .word 0x8000
     .word 0
dap_lba: .quad 1
drive: .byte 0
.org 492
sectors: .word 0
.org 510
.word 0xaa55
