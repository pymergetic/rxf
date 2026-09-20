/* Linux x86-64 SysV process adapter for a shared composed entry. */
.global _start
.type _start,@function
.section .text.startup,"ax",@progbits
_start:
    sub $208, %rsp
    lea runtime_context_ptr(%rip), %rsi
    mov (%rsi), %rsi
    lea (%rsp), %rdi
    mov $18, %ecx
0:  mov (%rsi), %rax
    mov %rax, (%rdi)
    add $8, %rsi
    add $8, %rdi
    dec %ecx
    jne 0b
    movl $0xa5df0c3e, 144(%rsp)
    lea (%rsp), %rdi
    lea 144(%rsp), %rsi
    mov entry_pointer(%rip), %rax
    call *%rax
    test %eax, %eax
    jne .refuse
    mov 144(%rsp), %eax
    lea 207(%rsp), %rsi
    movb $10, (%rsi)
    mov $1, %r8d
    mov $10, %ecx
.digit:
    xor %edx, %edx
    div %ecx
    add $'0', %dl
    dec %rsi
    mov %dl, (%rsi)
    inc %r8d
    test %eax, %eax
    jne .digit
    mov %r8d, %edx
    mov $1, %eax
    mov $1, %edi
    syscall
    xor %edi, %edi
    jmp .exit
.refuse:
    mov %eax, %edi
    test %edi, %edi
    jne .exit
    mov $1, %edi
.exit:
    mov $60, %eax
    syscall
.size _start,.-_start
.align 8
runtime_context_ptr: .quad 0
entry_pointer: .quad 0
.section .note.GNU-stack,"",@progbits
