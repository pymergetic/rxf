/* The Uart capability's fill for aarch64: the PL011 in memory (QEMU virt's
 * UART0). The capability's generation word names the device (native/graph.h:
 * PL011 registers are byte offsets, read and written as words); a slot that
 * names anything else is unbound here and the byte is not emitted. */
#include "uart.h"

#define DR 0x00U /* the byte offset uart.c's PL011_DR names */
#define FR 0x18U
#define TXFF 0x20U

static rxf_u32 mmio_read(rxf_u64 at) {
    return *(volatile rxf_u32 *)(unsigned long)at;
}

static void mmio_write(rxf_u64 at, rxf_u8 value) {
    *(volatile rxf_u32 *)(unsigned long)at = value;
}

void rxf_boot_uart_putc(const rxf_native_slot *capability, rxf_u8 byte) {
    const rxf_u32 kind = (rxf_u32)(capability->generation & 0xFFU);
    /* 0 or MACHINE is the machine's own device (uart.c's device_kind): here the
     * PL011 in memory; anything else this fill cannot drive */
    if (kind != 0 && kind != RXF_UART_DEVICE_MACHINE && kind != RXF_UART_DEVICE_PL011_MMIO)
        return;
    const rxf_u32 shift = (rxf_u32)((capability->generation >> RXF_UART_SHIFT_AT) & 0xFU);
    const rxf_u64 base = capability->address;
    for (rxf_u32 spins = 0; spins != 0x1000000U; spins++) /* a bounded moment, never a wait */
        if ((mmio_read(base + ((rxf_u64)FR << shift)) & TXFF) == 0) {
            mmio_write(base + ((rxf_u64)DR << shift), byte);
            return;
        }
}
