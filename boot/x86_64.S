/* boot/x86_64.S -- the BIOS boot sector and the streaming loader (stage2).
 *
 * The glue's four jobs (docs/115): reset vector with stack and BSS, exception
 * vectors (the GDT/IDT era on x86_64: the GDT is what protected and long mode
 * are built on), and the entry trampoline that hands control to the image's
 * rxf_graph_run per System V. The image is a separate flat binary this loader
 * streams in from a fixed LBA: the object world is payload, not glue. The
 * world is the REAL linked image (image_stub/world_v7.h: the
 * CONTEXT/ENTRIES/ARENA rows tools/native/link.py makes of the starter, the
 * v7 arena of docs/32 1), with the carrier room -- the entry code and the rows
 * the glue passes -- below it.
 *
 * Real mode cannot address the memory the image lives in (it runs to tens of
 * megabytes and must sit above the firmware rooms the map names), so the image
 * is streamed through a 64 KiB bounce buffer at 0x10000 in the one trick that
 * keeps the BIOS disk service available -- big real mode, exactly the loader
 * of native/wrap/wrap_stage2.S: the segments take their 4 GB limits in
 * protected mode and keep them on the way back, and the round trip comes
 * before every copy because the disk service restores the segments as real
 * mode knows them when it answers. How much arrives is the head row of the
 * image (world_v7.h): its 64 KiB chunk count, peeked in the bounce buffer
 * before the first copy. Then the machine goes to long mode for good and the
 * entry is called per System V.
 *
 * The memory map is boot/image_stub/world_v7.h's (commented at each use). The
 * carriers of it are this file, image_stub/entry_v7.ld and the rows
 * tools/native/boot_image.py writes; `make check-map` compares them, so they
 * cannot drift.
 *
 * Memory map: 0x2000..0x5000 the page tables, 0x7C00 the boot sector and the
 * stack below it, 0x8000 this loader, 0x10000..0x20000 the bounce buffer,
 * 0x04000000 upward the image (its map: the carrier rows, then the linked
 * world).
 *
 * Disk layout (the Makefile's build/disk.img):
 *   LBA 0        this sector (512 bytes)
 *   LBA 1..4     stage2 (the loader above, loaded at 0x8000)
 *   LBA 5..      the image: as many 64 KiB chunks as its head row carries
 */

/* The constants of world_v7.h's map, in asm. The .equ values must match the
 * #defines there and the ASSERTs of entry_v7.ld; `make check-map` proves it. */
    .equ IMAGE_BASE, 0x04000000 /* the carrier rows below, the world behind */
    .equ ENTRY, IMAGE_BASE + 0x11000    /* rxf_graph_run, in the carrier room */
    .equ CONTEXT, IMAGE_BASE + 0x20000  /* the linked image's context (link.py) */
    .equ ARGS, IMAGE_BASE + 0x5800      /* the argument words the program takes */
    .equ OUT, IMAGE_BASE + 0x5900       /* where the answer lands */
    .equ HANDLE, IMAGE_BASE + 0x8600    /* the 3-word handle, passed by value */
    .equ BSS_START, IMAGE_BASE + 0x8800 /* FRAME and FIFO rows: never on disk */
    .equ BSS_END, IMAGE_BASE + 0x10B00
    .equ ARGC, 1                /* the smoke's program takes one word */
    .equ IMAGE_LBA, 5           /* after the sector and the 4-sector stage2 */
    .equ CHUNK_BYTES, 0x10000   /* one read and one copy: 64 KiB (world_v7.h) */
    .equ CHUNK_SECTORS, 128     /* the same chunk in sectors */
    .equ CHUNK_SEG, 0x1000      /* 64 KiB in real-mode paragraphs */
    .equ BOUNCE, 0x10000        /* the bounce buffer the chunks land in */
    .equ STAGE2, 0x8000
    .equ STACK_TOP, 0x7C00      /* below the sector: the loader's stack */
    /* The RUN's stack: the top of the bounce buffer, 64 KiB of room below it
     * (0x10000..0x20000, the room the aarch64 seat's .boot.bss gives its run).
     * The bounce buffer is dead once the image is staged. It must NOT be the
     * low room below the sector: the page tables own 0x2000..0x5000 and a run
     * whose frames reach past the 11 KiB between them and STACK_TOP overwrites
     * the page directory (PDE[49] at 0x4188) and page-faults the next walk --
     * with no IDT that is a triple fault and the seat hangs before it reports.
     * The migrated image's pump turn (the live_pump of the starter, its delta
     * work) needs past that: its high-water measured past 14 KiB, which is
     * below PDE[49] when the top is 0x7C00. */
    .equ RUN_STACK_TOP, 0x20000
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
    jmp STAGE2                  /* the loader: real mode first, long mode after */

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
drive:
    .byte 0

    .org 510                    /* pad the sector to its last two bytes */
    .word 0xaa55                /* the boot signature the BIOS checks */

/* ---- stage2: the streaming loader, then protected and long mode ---------- */

    .section .stage2, "ax"
    .code16
    .globl _start32
_start32:
    cli
    xorw %ax, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    movw $STACK_TOP, %sp

    /* The image streams through the bounce buffer: one chunk read, one copy
     * into the image's place. Chunk 0 arrives first -- the head row in it says
     * how many chunks the image is (world_v7.h), peeked before it is copied. */
    movw $dap_image, %si
    call read
    pushw %ds
    movw $CHUNK_SEG, %ax
    movw %ax, %ds
    movw 0x0000, %cx            /* the head's chunk count; the chunk read counts */
    popw %ds
    movw %cx, chunks
    xorl %ebp, %ebp             /* the bytes staged so far */

stage_loop:
    /* Big real mode for the copy: the segments take their 4 GB limits in
     * protected mode and keep them on the way back (wrap_stage2.S). */
    lgdt gdt_desc
    movl %cr0, %eax
    orl $1, %eax                /* CR0.PE */
    movl %eax, %cr0
    ljmp $0x08, $stage_copy_pm

    .code16
stage_copy_pm:
    movw $0x10, %ax
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %fs
    movw %ax, %gs
    movl %cr0, %eax
    andl $~1, %eax
    movl %eax, %cr0
    ljmp $0x0000, $stage_copy

    .code16
stage_copy:
    /* Bounce buffer to the image's place: a32 rep movsd (the operand and
     * address size prefixes the assembler does not spell in 16-bit mode). */
    movl $CHUNK_BYTES, %ecx
    shrl $2, %ecx
    movl $BOUNCE, %esi
    movl %ebp, %edi
    addl $IMAGE_BASE, %edi
    cld
    .byte 0x67, 0x66, 0xF3, 0xA5
    /* Back to the segments the disk service expects to restore: base 0. */
    xorw %ax, %ax
    movw %ax, %ds
    movw %ax, %es
    addl $CHUNK_BYTES, %ebp
    subw $1, chunks             /* the head's count, one chunk at a time */
    jz staged
    addl $CHUNK_SECTORS, dap_image+8    /* the next chunk on disk; the bounce
                                         * buffer stays where it is */
    movw $dap_image, %si
    call read
    jmp stage_loop

staged:
    /* Long mode needs paging, and paging needs tables: one PML4, one PDPT and
     * one page directory of 2 MiB pages identity-mapping the first 1 GiB (the
     * image sits at 0x04000000 and runs well inside it). They live below the
     * loader (0x2000..) so no load or stack can overwrite them. */
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
    lgdt gdt_desc               /* the big real mode round trip left it loaded;
                                 * the loader says so again before the last jump */
    movl %cr0, %eax
    orl $0x80000001, %eax       /* CR0.PG | CR0.PE together (wrap_stage2.S): the
                                 * loop leaves real mode behind here -- with LME
                                 * set this is long mode */
    movl %eax, %cr0
    ljmp $0x18, $start64        /* reload CS in the 64-bit code segment */

    .code64
start64:
    movw $0x10, %ax             /* data segments again: they must have D/B clear here */
    movw %ax, %ds
    movw %ax, %es
    movw %ax, %ss
    /* The run's stack, not the loader's: the entry and the machine it runs need
     * the 64 KiB below this top (RUN_STACK_TOP's note), and the loader's room
     * below the sector cannot give them (the page tables sit in it). */
    movq $RUN_STACK_TOP, %rsp

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
     * image (entry_v7.ld marks .bss NOLOAD), so the glue is what makes them
     * zero. C statics are only honest if this happens before the first call. */
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
    movq $CONTEXT, %rdi         /* the linked image's runtime context */
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

    .code16
/* The chunk count of the image (the head row's), kept across the int 13h dance
 * and the copies: one word the loader counts down. */
chunks:
    .word 0

/* The disk address packet of the streaming read: one 64 KiB chunk of the image
 * to the bounce buffer (world_v7.h's CHUNK). */
    .align 4
dap_image:
    .byte 0x10, 0
    .word CHUNK_SECTORS         /* sectors: one chunk */
    .word 0x0000, 0x1000        /* -> the bounce buffer at 0x10000 */
    .quad IMAGE_LBA             /* LBA 5: past the sector and stage2 */

/* The GDT: null, 16-bit code (the big real mode round trip), data with a 4 GB
 * limit (the copy's segments and long mode's), 64-bit code. Flat 0-4G because
 * the world's addresses are physical ones and the smoke identity-maps only
 * what it uses. */
    .align 8
gdt:
    .quad 0x0000000000000000
    .quad 0x00009A000000FFFF    /* 0x08: code, 16-bit, ring 0 */
    .quad 0x00CF92000000FFFF    /* 0x10: data, 4 GB (ring 0) */
    .quad 0x00209A0000000000    /* 0x18: code, 64-bit, ring 0 */
gdt_end:
gdt_desc:
    .word gdt_end - gdt - 1     /* the descriptor register wants size minus one */
    .long gdt                   /* and the linear address of the table */
