/* AAPCS64 EFI entry to shared ExecutableImage. */
.globl efi_main
.text
efi_main:
    sub sp, sp, #288
    stp x29, x30, [sp, #256]
    stp x19, x20, [sp, #272]
    add x29, sp, #256
    mov x19, x0
    mov x20, x1
    adrp x9, runtime_context_ptr
    ldr x9, [x9, :lo12:runtime_context_ptr]
    add x10, sp, #32
    mov w11, #18
0:  ldr x12, [x9], #8
    str x12, [x10], #8
    subs w11, w11, #1
    b.ne 0b
    mov w12, #0x0c3e
    movk w12, #0xa5df, lsl #16
    str w12, [sp, #176]
    add x0, sp, #32
    add x1, sp, #176
    adrp x9, entry_pointer
    ldr x9, [x9, :lo12:entry_pointer]
    blr x9
    cbnz w0, .refuse
    ldr w9, [sp, #176]
    add x1, sp, #256
    strh wzr, [x1]
    mov w10, #10
    strh w10, [x1, #-2]!
    mov w10, #13
    strh w10, [x1, #-2]!
    mov w10, #10
.digit:
    udiv w11, w9, w10
    msub w12, w11, w10, w9
    add w12, w12, #48
    strh w12, [x1, #-2]!
    mov w9, w11
    cbnz w9, .digit
    ldr x0, [x20, #64]
    ldr x9, [x0, #8]
    blr x9
    mov w0, #0
    mov w1, #16
    b .terminate
.refuse:
    cbnz w0, .have_status
    mov w0, #1
.have_status:
    mov w1, #17
.terminate:
    str w0, [sp, #16]
    stp xzr, xzr, [sp]
    movz w9, #0x26
    movk w9, #2, lsl #16
    str x9, [sp]
    str x1, [sp, #8]
    mov x0, #0x20
    mov x1, sp
    hlt #0xf000
    ldr w0, [sp, #16]
    ldr x9, [x20, #96]
    ldr x9, [x9, #216]
    mov x1, x0
    mov x0, x19
    mov x2, #0
    mov x3, #0
    blr x9
    ldp x19, x20, [sp, #272]
    ldp x29, x30, [sp, #256]
    add sp, sp, #288
    ret
.align 3
runtime_context_ptr: .quad 0
entry_pointer: .quad 0
