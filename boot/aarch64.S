/* boot/aarch64.S -- the QEMU virt entry: exception vectors, EL setup, and the
 * trampoline that hands control to the image's rxf_graph_run per AAPCS64.
 *
 * The glue's four jobs (docs/115) in the order the architecture wants them:
 * VBAR_EL1 (a fault must land somewhere before anything else can be trusted),
 * the EL check and drop (QEMU's virt starts in EL1 or EL2 depending on its
 * virtualization setting), MMU off (the world's addresses are physical, as
 * tests/graph_world.py's flat memory is), stack and BSS zeroing, then the
 * entry call.
 *
 * The image is a flat binary linked at 0x40100000 (boot/image_stub/image.ld)
 * and carried in this ELF's .image section (aarch64.ld): qemu -kernel loads
 * one ELF here, and the bytes are build output, not source.
 *
 * The entry call follows tests/emulator.py's call_aapcs64 exactly: x0 the
 * context, x1 the address of the 24-byte handle (a struct over 16 bytes is
 * replaced by a pointer to a caller-made copy -- here the image's own
 * HANDLE_COPY row, the row tests/graph_world.py's host writes for the wasm32
 * call), x2 args, x3 argc, x4 out.
 *
 * The constants below are world.h's map, in asm (as on x86_64); `make
 * check-map` compares them against the header so they cannot drift.
 */
    .equ IMAGE_BASE, 0x40100000         /* QEMU virt DRAM: where the world runs */
    .equ ENTRY, IMAGE_BASE + 0xB000     /* rxf_graph_run */
    .equ CONTEXT, IMAGE_BASE + 0x0000   /* struct rxf_runtime_context (144 bytes) */
    .equ ARGS, IMAGE_BASE + 0x5800      /* the argument words the program takes */
    .equ OUT, IMAGE_BASE + 0x5900       /* where the answer lands */
    .equ HANDLE, IMAGE_BASE + 0x8600    /* the 3-word handle copy */
    .equ BSS_START, IMAGE_BASE + 0x8800 /* FRAME and FIFO rows: never on disk */
    .equ BSS_END, IMAGE_BASE + 0xAB00
    .equ ARGC, 1                        /* the smoke's program takes one word */

/* The exception vectors: 16 entries of 128 bytes, the layout VBAR_EL1 expects.
 * Every one of them is a halt: the smoke has no exception story to tell, and a
 * fault that spins is honest about it. A real boot installs handlers here. */
    .section .vectors, "ax"
    .align 11                   /* VBAR_EL1 keeps bits [10:0] zero */
vectors:
    .rept 16
    b .                         /* spin: this vector is never supposed to run */
    .space 128 - 4
    .endr

    .section .boot.text, "ax"
    .globl _start
_start:
    mrs x9, CurrentEL           /* which exception level the loader left us in */
    lsr x9, x9, #2              /* CurrentEL's field is bits [3:2] */
    cmp x9, #2
    b.ne el1

    /* EL2 -> EL1, the drop QEMU's virtualization=on path needs. The contract of
     * the drop: EL1 must execute AArch64 (HCR_EL2.RW), and the eret must land
     * in EL1 with interrupts masked (SPSR_EL2 = EL1h | DAIF). */
    mov x9, #(1 << 31)
    msr hcr_el2, x9
    mov x9, #0x3c5
    msr spsr_el2, x9
    adr x9, el1
    msr elr_el2, x9
    eret

el1:
    adr x9, vectors
    msr vbar_el1, x9             /* job 2: from here on a fault has a vector */
    mrs x9, sctlr_el1
    bic x9, x9, #1               /* job 3: MMU off -- the world is physical memory */
    msr sctlr_el1, x9

    /* The image's C may widen a copy or a clear with FP/SIMD registers (the
     * compiler may, at -Os); a handoff leaves the callee's ISA usable, so FPEN
     * opens FP at EL1. An image built -mgeneral-regs-only needs none of this;
     * one that is not, traps here first otherwise. */
    mov x9, #(3 << 20)
    msr cpacr_el1, x9

    adrp x9, boot_stack_top      /* job 1: the stack, before anything uses it */
    add x9, x9, :lo12:boot_stack_top
    mov sp, x9

    /* job 1 again: BSS zeroing -- the FRAME and FIFO rows of the map (world.h):
     * the run cell and the wasm seat's device block are never in any file, so
     * what zeroes them here is what makes the image's runtime rows honest. */
    ldr x9, =BSS_START
    ldr x10, =BSS_END
    bl zero

    /* The entry call per AAPCS64: x1 takes the address of the handle copy --
     * the image's HANDLE_COPY row, at the offset tests/graph_world.py's host
     * writes its copy to. */
    ldr x0, =CONTEXT             /* the world's runtime context */
    ldr x1, =HANDLE              /* the 24-byte handle, by copy address */
    ldr x2, =ARGS                /* what the program reads */
    mov x3, #ARGC                /* and how many words of it */
    ldr x4, =OUT                 /* where the answer lands */
    ldr x9, =ENTRY               /* the image's entry: an absolute address */
    blr x9                       /* rxf_graph_run: the image's entry */

    /* PSCI SYSTEM_OFF: the coarse answer of this seat (wrap.h's magic port is
     * x86_64's). The PSCI conduit is a real boot's discovery problem (the
     * device tree's /psci method); this QEMU's virt wires PSCI on SMC and
     * exposes no conduit knob, so the smoke fixes SMC here and says so
     * (boot/README.md's real-bootloader gaps). */
    movz x0, #0x8400, lsl #16
    movk x0, #0x0008
    smc #0
1:  wfe                          /* a finished machine waits here */
    b 1b

/* zero [x9, x10): the loop both zeroings share. */
zero:
    cmp x9, x10
    b.hs 2f
1:  str xzr, [x9], #8
    cmp x9, x10
    b.lo 1b
2:  ret

    .section .boot.bss, "aw", @nobits
    .align 4
    .space 16384                 /* the stack: an 8 KiB run cell may sit on it */
boot_stack_top:

    /* The image travels as bytes (build output) at its map base. */
    .section .image, "a"
    .incbin "build/image_aarch64.bin"
