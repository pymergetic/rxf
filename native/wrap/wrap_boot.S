/*
 * The boot sector of a BIOS image: 512 bytes the firmware loads at 0x7C00 and starts.
 * It is the whole of real mode this image needs -- set the machine up, read the loader
 * (the first 32 sectors behind the sector, to 0x8000) with the extended read of int
 * 13h, and jump to it. The loader (native/wrap/wrap_stage2.S) does the rest: streaming
 * the package, long mode, and the call of the wrapper body.
 */
    .section .head, "ax"
    .code16
    .global rxf_wrap_boot_start
    /* The one thing the loader needs from here: the drive the firmware booted from. */
    .global rxf_wrap_boot_drive

rxf_wrap_boot_start:
    cli
    xor %ax, %ax
    mov %ax, %ds
    mov %ax, %es
    mov %ax, %ss
    mov $0x7C00, %sp
    sti
    mov %dl, rxf_wrap_boot_drive

    /* The loader: 32 sectors from LBA 2 (the sector behind this one is a gap the link
     * keeps) to 0x8000, where the link puts it. */
    mov $rxf_wrap_dap, %si
    mov $0x42, %ah
    mov rxf_wrap_boot_drive, %dl
    int $0x13
    jc rxf_wrap_boot_failed
    ljmp $0x0000, $0x8000

rxf_wrap_boot_failed:
    /* 'B' on the debug console of an emulator, then stop. */
    mov $'B', %al
    out %al, $0xE9
rxf_wrap_boot_hang:
    cli
    hlt
    jmp rxf_wrap_boot_hang

    /* The disk address packet of int 13h: 32 sectors, 0000:8000, LBA 2. */
    .p2align 4
rxf_wrap_dap:
    .byte 0x10, 0
    .word 32
    .word 0x8000, 0x0000
    .quad 2
rxf_wrap_boot_drive:
    .byte 0

    .org 510
    .word 0xAA55
