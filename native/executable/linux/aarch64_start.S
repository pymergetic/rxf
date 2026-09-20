/* Linux AArch64 AAPCS64 process adapter for a shared composed entry. */
.global _start
.type _start,%function
.section .text.startup,"ax",%progbits
_start:
    sub sp, sp, #208
    adrp x9, runtime_context_ptr
    ldr x9, [x9, :lo12:runtime_context_ptr]
    mov x10, sp
    mov w11, #18
0:  ldr x12, [x9], #8
    str x12, [x10], #8
    subs w11, w11, #1
    b.ne 0b
    mov w12, #0x0c3e
    movk w12, #0xa5df, lsl #16
    str w12, [sp, #144]
    mov x0, sp
    add x1, sp, #144
    adrp x9, entry_pointer
    ldr x9, [x9, :lo12:entry_pointer]
    blr x9
    cbnz w0, .refuse
    ldr w9, [sp, #144]
    add x1, sp, #207
    mov w10, #10
    strb w10, [x1]
    mov x2, #1
.digit:
    udiv w11, w9, w10
    msub w12, w11, w10, w9
    add w12, w12, #'0'
    strb w12, [x1, #-1]!
    add x2, x2, #1
    mov w9, w11
    cbnz w9, .digit
    mov x0, #1
    mov x8, #64
    svc #0
    mov x0, #0
    b .exit
.refuse:
    uxtw x0, w0
    cbnz x0, .exit
    mov x0, #1
.exit:
    mov x8, #93
    svc #0
.size _start,.-_start
.align 3
runtime_context_ptr: .quad 0
entry_pointer: .quad 0
.section .note.GNU-stack,"",%progbits
