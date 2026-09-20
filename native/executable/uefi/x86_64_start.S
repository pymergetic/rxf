/* x86-64 EFIAPI entry to SysV shared ExecutableImage. */
.globl efi_main
.text
efi_main:
    pushq %rbp
    movq %rsp, %rbp
    pushq %r12
    pushq %r13
    subq $272, %rsp
    movq %rcx, %r12
    movq %rdx, %r13
    movq runtime_context_ptr(%rip), %rsi
    leaq 32(%rsp), %rdi
    movl $18, %ecx
    rep movsq
    movl $0xa5df0c3e, 176(%rsp)
    leaq 32(%rsp), %rdi
    leaq 176(%rsp), %rsi
    movq entry_pointer(%rip), %rax
    call *%rax
    testl %eax, %eax
    jne .refuse
    movl 176(%rsp), %eax
    leaq 256(%rsp), %rsi
    movw $0, (%rsi)
    movw $10, -2(%rsi)
    movw $13, -4(%rsi)
    subq $4, %rsi
    movl $10, %r8d
.digit:
    xorl %edx, %edx
    divl %r8d
    addl $48, %edx
    subq $2, %rsi
    movw %dx, (%rsi)
    testl %eax, %eax
    jne .digit
    movq 64(%r13), %rcx
    movq 8(%rcx), %rax
    movq %rsi, %rdx
    subq $32, %rsp
    call *%rax
    addq $32, %rsp
    xorl %eax, %eax
    movl $16, %edx
    jmp .terminate
.refuse:
    movl $17, %edx
    testl %eax, %eax
    jne .terminate
    movl $1, %eax
.terminate:
    movl %eax, 24(%rsp)
    movl %edx, %eax
    movw $0x501, %dx
    outl %eax, %dx
    movl 24(%rsp), %eax
    movq 96(%r13), %rcx
    movq 216(%rcx), %r11
    movq %r12, %rcx
    movl %eax, %edx
    xorl %r8d, %r8d
    xorl %r9d, %r9d
    subq $32, %rsp
    call *%r11
    addq $32, %rsp
    addq $272, %rsp
    popq %r13
    popq %r12
    popq %rbp
    retq
.align 8
runtime_context_ptr: .quad 0
entry_pointer: .quad 0
