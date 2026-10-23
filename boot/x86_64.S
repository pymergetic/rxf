/* boot/x86_64.S -- the BIOS boot sector and the 32/64-bit trampoline.
 *
 * The glue's four jobs (docs/115): reset vector with stack and BSS, exception
 * vectors (the GDT/IDT era on x86_64: the GDT is what protected and long mode
 * are built on), and the entry trampoline that hands control to the image's
 * rxf_graph_run per System V. The image is a separate flat binary this sector
 * loads from a fixed LBA: the object world is payload, not glue.
 *
 * The memory map is boot/image_stub/world.h's (commented at each use). Two
 * languages carry it -- this file and image.ld -- and the Makefile's check-map
 * compares the linked image against these constants, so they cannot drift.
 *
 * Disk layout (the Makefile's build/disk.img):
 *   LBA 0        this sector (512 bytes)
 *   LBA 1..4     stage2 (the 32/64-bit trampoline, loaded at 0x8000)
 *   LBA 5..108   the image (104 sectors, loaded at 0x10000)
 */

/* The constants of world.h's map, in asm. The .equ values must match the
 * #defines there and the ASSERTs of image.ld; `make check-map` proves it. */
    .equ IMAGE_BASE, 0x10000    /* the world: scenario.BASE of tests/graph_world.py */
    .equ ENTRY, IMAGE_BASE + 0xB000     /* rxf_graph_run, past the mapped rows */
    .equ CONTEXT, IMAGE_BASE + 0x0000   /* struct rxf_runtime_context (144 bytes) */
    .equ ARGS, IMAGE_BASE + 0x5800      /* the argument words the program takes */
    .equ OUT, IMAGE_BASE + 0x5900       /* where the answer lands */
    .equ HANDLE, IMAGE_BASE + 0x8600    /* the 3-word handle, passed by value */
    .equ BSS_START, IMAGE_BASE + 0x8800 /* FRAME and FIFO rows: never on disk */
    .equ BSS_END, IMAGE_BASE + 0xAB00
    .equ ARGC, 1                /* the smoke's program takes one word */
    .equ IMAGE_LBA, 5           /* after the sector and the 4-sector stage2 */
    .equ IMAGE_SECTORS, 104     /* 52KB: world rows + entry code + slack */
    .equ STAGE2, 0x8000
    .equ STACK_TOP, 0x7c00      /* below the sector: the loader's and entry's stack */
    .equ MAGIC_PORT, 0xF4       /* wrap.h: the coarse answer QEMU turns into an exit */
    .equ MAGIC_OK, 0x10
    .equ MAGIC_FAIL, 0x11

    .code16
    .section .bootsector, "ax"

/* The reset vector: the BIOS jumps here with DL = the boot drive and no usable
 * segment setup. Everything below exists because the CPU starts in real mode
 * with interrupts on and the world's code needs 64-bit mode. */
    .globl _start16
_start16:
    cli                         /* no interrupt may run while segments are unset */
    xorw %ax, %ax
    movw %ax, %ds               /* flat real mode: data and code at 0 */
    movw %ax, %es               /* es is where INT 13h writes (through the DAP) */
    movw %ax, %ss
    movw $STACK_TOP, %sp        /* the stack grows down below the sector itself */
    sti                         /* the BIOS disk services run with interrupts on */
    movb %dl, drive             /* the BIOS drive number is the one contract it gives */

    movw $dap_stage2, %si       /* INT 13h AH=42h takes its request in DS:SI */
    call read
    movw $dap_image, %si
    call read

    cli                         /* the mode change must not be interrupted */
    lgdt gdt_desc               /* long mode needs descriptors before it needs paging */
    movl %cr0, %eax
    orl $1, %eax                /* CR0.PE: protected mode */
    movl %eax, %cr0
    ljmp $0x08, $STAGE2         /* reload CS in the 32-bit code segment, land in stage2 */

/* One EDD read of the request at DS:SI. The BIOS is the only disk driver the
 * smoke needs; a real bootloader may want retry/CHS fallback (boot/README.md). */
read:
    movb drive, %dl
    movb $0x42, %ah
    int $0x13
    jc disk_error               /* a boot that cannot read its image must not run */
    ret

/* The failure is on the serial line: the smoke watches serial, not the VGA. */
disk_error:
    movw $0x3f8, %dx            /* COM1 */
    movb $'E', %al
    outb %al, %dx
1:  hlt
    jmp 1b

    .align 4                    /* the DAP must not cross a 4-byte boundary */
dap_stage2:
    .byte 0x10, 0               /* the packet's size, and its reserved byte */
    .word 4                     /* sectors: stage2's 4 */
    .word 0x0000, 0x0800        /* segment:offset -> 0x8000 */
    .quad 1                     /* LBA 1 */
dap_image:
    .byte 0x10, 0
    .word IMAGE_SECTORS         /* sectors: the image's (the Makefile checks it fits) */
    .word 0x0000, 0x1000        /* -> 0x10000 */
    .quad IMAGE_LBA             /* LBA 5: past the sector and stage2 */
drive:
    .byte 0

/* The GDT: null, 32-bit code, data (also long mode's), 64-bit code. Flat 0-4G
 * because the world's addresses are physical ones and the smoke identity-maps
 * only what it uses. */
gdt:
    .quad 0x0000000000000000
    .quad 0x00CF9A000000FFFF    /* 0x08: code, 32-bit, ring 0 */
    .quad 0x00CF92000000FFFF    /* 0x10: data, ring 0 */
    .quad 0x00AF9A000000FFFF    /* 0x18: code, 64-bit, ring 0 */
gdt_end:
gdt_desc:
    .word gdt_end - gdt - 1     /* the descriptor register wants size minus one */
    .long gdt                   /* and the linear address of the table */

    .org 510                    /* pad the sector to its last two bytes */
    .word 0xaa55                /* the boot signature the BIOS checks */

/* ---- stage2: protected mode to long mode, then the entry call ------------- */

    .section .stage2, "ax"
    .code32
    .globl _start32
_start32:
    movw $0x10, %ax             /* the data segments the mode change left stale */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    movl $STACK_TOP, %esp

    /* Long mode needs paging, and paging needs tables: one PML4, one PDPT and
     * one page directory of 2 MiB pages identity-mapping the first 1 GiB. They
     * live below the loader (0x2000..) so no load or stack can overwrite them. */
    xorl %eax, %eax
    movl $0x2000, %edi
    movl $(0x3000 / 4), %ecx    /* the three pages must start zero */
    rep stosl
    movl $0x3003, 0x2000        /* PML4[0] -> the PDPT, present and writable */
    movl $0x4003, 0x3000        /* PDPT[0] -> the page directory */
    movl $0x4000, %edi
    movl $0x83, %eax            /* present, writable, 2 MiB page */
    movl $512, %ecx
1:  movl %eax, (%edi)           /* 512 entries of 2 MiB: the first GiB */
    addl $0x200000, %eax
    addl $8, %edi
    loop 1b

    movl %cr4, %eax
    orl $(1 << 5), %eax         /* CR4.PAE: long mode pages through 4 levels */
    movl %eax, %cr4
    movl $0x2000, %eax
    movl %eax, %cr3             /* the page tables are what paging walks */
    movl $0xC0000080, %ecx
    rdmsr                       /* EFER is a model-specific register, not a control one */
    orl $(1 << 8), %eax         /* EFER.LME: the mode long mode runs in */
    wrmsr
    movl %cr0, %eax
    orl $(1 << 31), %eax        /* CR0.PG: paging on -- with LME set, this is long mode */
    movl %eax, %cr0
    ljmp $0x18, $start64        /* reload CS as 64-bit code */

    .code64
start64:
    movw $0x10, %ax             /* data segments again: they must have D/B clear here */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    movq $STACK_TOP, %rsp

    /* The image's C may widen a copy or a clear with SSE (clang can, at -Os);
     * a handoff leaves the callee's ISA usable, so this opens SSE: CR0.MP=1
     * with CR0.EM=0, CR4.OSFXSR|OSXMMEXCPT. An image built -msoft-float needs
     * none of this; one that is not, takes #UD here first otherwise. */
    movq %cr0, %rax
    andq $~(1 << 2), %rax       /* CR0.EM = 0: no emulation excuse */
    orq $(1 << 1), %rax         /* CR0.MP = 1 */
    movq %rax, %cr0
    movq %cr4, %rax
    orq $((1 << 9) | (1 << 10)), %rax /* CR4.OSFXSR | CR4.OSXMMEXCPT */
    movq %rax, %cr4

    /* BSS zeroing: the run cell and the entry's statics are never in the disk
     * image (image.ld marks .bss NOLOAD), so the glue is what makes them zero.
     * C statics are only honest if this happens before the first call. */
    movq $BSS_START, %rdi
    movq $((BSS_END - BSS_START) / 8), %rcx
    xorq %rax, %rax
    rep stosq

    /* The entry call per System V (tests/emulator.py's call_sysv): the 24-byte
     * handle is a MEMORY-class argument and lives on the stack, the integers in
     * rdi, rsi, rdx, rcx. The caller's stack is 16-aligned before the call, so
     * the callee's entry view -- return address, then the handle at [rsp+8] --
     * is the ABI's exactly. */
    subq $32, %rsp              /* 24 bytes of handle and 8 of alignment padding */
    movq $HANDLE, %rax
    movq 0x00(%rax), %rdx
    movq %rdx, 0x00(%rsp)       /* object id, generation, offset */
    movq 0x08(%rax), %rdx
    movq %rdx, 0x08(%rsp)
    movq 0x10(%rax), %rdx
    movq %rdx, 0x10(%rsp)
    movq $CONTEXT, %rdi         /* the world's runtime context */
    movq $ARGS, %rsi            /* what the program reads */
    movq $ARGC, %rdx            /* and how many words of it */
    movq $OUT, %rcx             /* where the answer lands */
    call ENTRY                  /* rxf_graph_run: the image's entry */
    addq $32, %rsp

    /* The coarse answer on the magic port (wrap.h's): the image's own report is
     * the `rxf result` line on the Uart capability; this byte only says whether
     * the run answered 0. */
    xorl %ecx, %ecx
    testl %eax, %eax
    setne %cl
    addb $MAGIC_OK, %cl         /* 0x10 answered, 0x11 refused */
    movw $MAGIC_PORT, %dx
    movb %cl, %al
    outb %al, %dx
1:  hlt                         /* a finished machine waits here */
    jmp 1b
