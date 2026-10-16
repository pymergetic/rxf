/*
 * The loader of a BIOS image, linked at 0x8000 where the boot sector leaves it
 * (native/wrap/wrap_boot.S). It is all of the machine work this wrapper needs, and the
 * classic sequence: read the rest, set up a stack, protected mode, long mode, call the
 * entry.
 *
 * Real mode cannot address the memory a run lives in, so the package is streamed to
 * 0x01000000 through a 32 KiB bounce buffer at 0x10000, in the one trick that keeps the
 * BIOS disk service available: big real mode (a 4 GB limit left in the segment
 * registers after a round trip through protected mode). The round trip is taken before
 * every copy, because the disk service restores the segments as real mode knows them
 * (base of the selector, a 16-bit limit) when it answers -- so the segments sit at 0
 * for the call and go back to 4 GB for the copy that follows it. Then the machine goes
 * to long mode for good -- an identity map of 0..4 GiB in 2 MiB pages at 0x1000, PAE,
 * LME, paging -- and the wrapper body (native/wrap/wrap_bios.c) is called with the
 * staged package. It does not return.
 *
 * Memory map: 0x1000..0x6FFF the page tables, 0x7C00 the boot sector, 0x8000 this
 * loader (and the C of the seat behind it), 0x10000..0x18000 the bounce buffer,
 * 0x9F000 the stack, 0x01000000 the staged package, 0x04000000 the block.
 */
    .section .stage2, "ax"
    .code16
    .global rxf_wrap_stage2_start

rxf_wrap_stage2_start:
    cli
    xor %ax, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %ss
    mov $0x7C00, %sp

    /* Where the package is on disk, and how much of it there is (linker symbols). */
    mov $rxf_wrap_package_start, %ebp
    mov %ebp, %eax
    sub $0x7C00, %eax
    shr $9, %eax
    mov %eax, rxf_wrap_stage_lba
    mov $rxf_wrap_package_end, %ebx
    sub %ebp, %ebx              /* ebx: the bytes of the package */
    xor %ebp, %ebp              /* ebp: the bytes staged so far */

rxf_wrap_stage_loop:
    cmp %ebx, %ebp
    jae rxf_wrap_staged
    /* How much of what is left this read takes: 32 KiB at most. */
    mov %ebx, %ecx
    sub %ebp, %ecx
    mov %ecx, %eax
    add $511, %eax
    shr $9, %eax
    cmp $64, %eax
    jbe 1f
    mov $64, %eax
1:  mov %ax, rxf_wrap_stage_count
    /* Where on disk: the start of the package plus the sectors staged so far. */
    mov %ebp, %eax
    shr $9, %eax
    add rxf_wrap_stage_lba, %eax
    mov %eax, rxf_wrap_stage_lba_at
    mov $rxf_wrap_stage_dap, %si
    mov $0x42, %ah
    mov rxf_wrap_boot_drive, %dl
    int $0x13
    jc rxf_wrap_stage_failed

    /* Big real mode for the copy: the segments take their 4 GB limits in protected
     * mode and keep them on the way back. */
    lgdt rxf_wrap_gdt_desc
    mov %cr0, %eax
    or $1, %eax
    mov %eax, %cr0
    ljmp $0x08, $rxf_wrap_stage_copy_pm

    .code16
rxf_wrap_stage_copy_pm:
    mov $0x10, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %fs
    mov %ax, %gs
    mov %cr0, %eax
    and $~1, %eax
    mov %eax, %cr0
    ljmp $0x0000, $rxf_wrap_stage_copy

    .code16
rxf_wrap_stage_copy:
    /* Bounce buffer to extended memory: a32 rep movsd (the operand and address size
     * prefixes the assembler does not spell in 16-bit mode). */
    mov $0x8000, %ecx           /* the bytes of this chunk */
    mov %ebp, %eax
    add %ecx, %eax
    cmp %ebx, %eax
    jbe 2f
    mov %ebx, %ecx
    sub %ebp, %ecx              /* the tail of the package */
2:  shr $2, %ecx
    mov $0x10000, %esi
    mov %ebp, %edi
    add $0x01000000, %edi
    cld
    .byte 0x67, 0x66, 0xF3, 0xA5
    /* Back to the segments the disk service expects to restore: base 0. */
    xor %ax, %ax
    mov %ax, %ds
    mov %ax, %es
    add $0x8000, %ebp
    jmp rxf_wrap_stage_loop

rxf_wrap_staged:
    /* The identity map: one PML4, one PDPT, four page directories of 2 MiB pages,
     * 0..4 GiB (the block at 0x04000000 is inside it). */
    xor %eax, %eax
    mov $0x1000, %di
    mov $0x1800, %cx            /* 6 pages: 0x1000..0x7000 */
    cld
    rep stosl
    movl $0x2003, 0x1000        /* PML4[0] */
    movl $0x3003, 0x2000        /* PDPT[0..3] */
    movl $0x4003, 0x2008
    movl $0x5003, 0x2010
    movl $0x6003, 0x2018
    xor %eax, %eax              /* the physical address of the next 2 MiB page */
    mov $0x3000, %di
    mov $0x0800, %cx            /* 4 * 512 entries */
rxf_wrap_fill:
    mov %eax, %edx
    or $0x83, %edx              /* present, writable, 2 MiB */
    movl %edx, (%di)
    movl $0, 4(%di)
    add $8, %di
    add $0x200000, %eax
    loop rxf_wrap_fill

    /* Long mode: the GDT, PAE, the map, LME, paging. */
    lgdt rxf_wrap_gdt_desc
    mov %cr4, %eax
    or $0x20, %eax
    mov %eax, %cr4
    mov $0x1000, %eax
    mov %eax, %cr3
    mov $0xC0000080, %ecx
    rdmsr
    or $0x100, %eax             /* LME */
    wrmsr
    mov %cr0, %eax
    or $0x80000001, %eax        /* PG | PE */
    mov %eax, %cr0
    ljmp $0x18, $rxf_wrap_long_mode

    .code64
    .p2align 4
rxf_wrap_long_mode:
    /* The C of the seat moves memory with SSE; give it the state it needs. */
    mov %cr0, %rax
    and $~0x4, %rax             /* EM = 0 */
    mov %rax, %cr0
    mov %cr4, %rax
    or $0x600, %rax             /* OSFXSR | OSXMMEXCPT */
    mov %rax, %cr4
    mov $0x20, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %ss
    mov $0x0009F000, %rsp
    mov $0x01000000, %rdi       /* the staged package */
    mov $rxf_wrap_package_end, %rsi
    mov $rxf_wrap_package_start, %rax
    sub %rax, %rsi              /* and its bytes */
    call rxf_wrap_bios_main
rxf_wrap_long_hang:
    cli
    hlt
    jmp rxf_wrap_long_hang

    .code16
rxf_wrap_stage_failed:
    /* 'D' on the debug console of an emulator, then stop. */
    mov $'D', %al
    out %al, $0xE9
rxf_wrap_stage_hang:
    cli
    hlt
    jmp rxf_wrap_stage_hang

    /* The disk address packet of the streaming read: 64 sectors to 0x1000:0000. */
    .p2align 4
rxf_wrap_stage_dap:
    .byte 0x10, 0
rxf_wrap_stage_count:
    .word 64
    .word 0x0000, 0x1000
rxf_wrap_stage_lba_at:
    .quad 0

    .p2align 3
rxf_wrap_gdt:
    .quad 0x0000000000000000    /* 0x00: null */
    .quad 0x00009A000000FFFF    /* 0x08: 16-bit code, base 0 */
    .quad 0x00CF92000000FFFF    /* 0x10: data, 4 GB */
    .quad 0x00209A0000000000    /* 0x18: 64-bit code */
    .quad 0x00CF92000000FFFF    /* 0x20: data, 4 GB */
rxf_wrap_gdt_end:
rxf_wrap_gdt_desc:
    .word rxf_wrap_gdt_end - rxf_wrap_gdt - 1
    .long rxf_wrap_gdt

    /* The base LBA of the package, kept from the first computation above. */
rxf_wrap_stage_lba:
    .long 0
