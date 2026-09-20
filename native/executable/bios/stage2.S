.section .start,"ax"
.code16
.global stage2_start
.extern bios_runtime
stage2_start:
 cli
 cld
 movb %dl,boot_drive
 movb $0x02,%al
 outb %al,$0x92
 /* Stage 1 loaded eight stage and eight manifest sectors, not the graph.
    BIOS reads use a 32 KiB bounce buffer; protected mode copies to high RAM.
    No CHS geometry, segment wrap, or firmware-dependent high-memory DAP. */
 movw 0x7dec,%ax
 subw $16,%ax
 movw %ax,remaining
load_payload:
 cmpw $0,remaining
 je payload_loaded
 movw remaining,%ax
 cmpw $64,%ax
 jbe 1f
 movw $64,%ax
1:
 movw %ax,chunk
 movw %ax,dap_count
 movw $payload_dap,%si
 movb boot_drive,%dl
 movb $0x42,%ah
 sti
 int $0x13
 cli
 jc disk_failed
 lgdt gdt_desc
 movl %cr0,%eax
 orl $1,%eax
 movl %eax,%cr0
 ljmp $0x08,$copy_payload
.code32
copy_payload:
 movw $0x10,%ax
 movw %ax,%ds
 movw %ax,%es
 movl $0x10000,%esi
 movl destination,%edi
 movzwl chunk,%ecx
 shll $7,%ecx
 cld
 rep movsl
 movl %edi,destination
 ljmp $0x28,$leave_copy
.code16
leave_copy:
 movl %cr0,%eax
 andl $0xfffffffe,%eax
 movl %eax,%cr0
 ljmp $0,$real_return
real_return:
 xorw %ax,%ax
 movw %ax,%ds
 movw %ax,%es
 movw chunk,%ax
 subw %ax,remaining
 movzwl %ax,%eax
 addl %eax,payload_lba
 jmp load_payload
disk_failed:
 movw $0x3f8,%dx
 movb $0x45,%al
 outb %al,%dx
1: hlt
 jmp 1b
payload_loaded:
 lgdt gdt_desc
 movl %cr0,%eax
 orl $1,%eax
 movl %eax,%cr0
 ljmp $0x08,$pmode
.code32
pmode:
 movw $0x10,%ax
 movw %ax,%ds
 movw %ax,%es
 movw %ax,%ss
 movl $0x70000,%esp
 movl $0x1000,%edi
 xorl %eax,%eax
 movl $3072,%ecx
 rep stosl
 movl $0x2003,0x1000
 movl $0x3003,0x2000
 movl $0x3000,%edi
 xorl %ebx,%ebx
 movl $512,%ecx
1:
 movl %ebx,%eax
 orl $0x83,%eax
 movl %eax,(%edi)
 addl $0x200000,%ebx
 addl $8,%edi
 loop 1b
 movl %cr4,%eax
 orl $0x20,%eax
 movl %eax,%cr4
 movl $0x1000,%eax
 movl %eax,%cr3
 movl $0xc0000080,%ecx
 rdmsr
 orl $0x100,%eax
 wrmsr
 movl %cr0,%eax
 orl $0x80000000,%eax
 movl %eax,%cr0
 ljmp $0x18,$lmode
.code64
lmode:
 movw $0x20,%ax
 movw %ax,%ds
 movw %ax,%es
 movw %ax,%ss
 movq $0x70000,%rsp
 xorq %rbp,%rbp
 call bios_runtime
1: cli
 hlt
 jmp 1b
.align 8
gdt:
 .quad 0
 .quad 0x00cf9a000000ffff
 .quad 0x00cf92000000ffff
 .quad 0x00af9a000000ffff
 .quad 0x00af92000000ffff
 .quad 0x00009a000000ffff
gdt_end:
gdt_desc:
 .word gdt_end-gdt-1
 .long gdt
.align 4
payload_dap:
 .byte 0x10,0
dap_count: .word 0
 .word 0,0x1000
payload_lba: .quad 17
destination: .long 0x04000000
remaining: .word 0
chunk: .word 0
boot_drive: .byte 0
