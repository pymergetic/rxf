.section .text.start,"ax"
.global _start
_start:
    mrs x0, cpacr_el1
    mov x1, #3
    lsl x1, x1, #20
    orr x0, x0, x1
    msr cpacr_el1, x0
    isb
    ldr x0, =__stack_top
    mov sp, x0
    bl rxf_qemu_main
1:  wfe
    b 1b
