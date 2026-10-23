/* The Uart capability's fill for x86_64: the 16550 behind I/O ports (COM1).
 * The capability's generation word names the device (native/graph.h); a slot
 * that names anything else is unbound here and the byte is not emitted -- the
 * smoke fails loudly instead of printing on a dark port. */
#include "uart.h"

#define DATA 0U
#define LINE_STATUS 5U
#define THR_EMPTY 0x20U

static void port_write(rxf_u64 port, rxf_u8 value) {
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"((rxf_u16)port));
}

static rxf_u8 port_read(rxf_u64 port) {
    rxf_u8 value;
    __asm__ volatile("inb %1, %0" : "=a"(value) : "Nd"((rxf_u16)port));
    return value;
}

void rxf_boot_uart_putc(const rxf_native_slot *capability, rxf_u8 byte) {
    const rxf_u32 kind = (rxf_u32)(capability->generation & 0xFFU);
    /* 0 or MACHINE is the machine's own device (uart.c's device_kind): here the
     * 16550 behind ports; anything else this fill cannot drive */
    if (kind != 0 && kind != RXF_UART_DEVICE_MACHINE && kind != RXF_UART_DEVICE_NS16550_PORT)
        return;
    const rxf_u32 shift = (rxf_u32)((capability->generation >> RXF_UART_SHIFT_AT) & 0xFU);
    const rxf_u64 base = capability->address;
    for (rxf_u32 spins = 0; spins != 0x1000000U; spins++) /* a bounded moment, never a wait */
        if (port_read(base + ((rxf_u64)LINE_STATUS << shift)) & THR_EMPTY) {
            port_write(base + ((rxf_u64)DATA << shift), byte);
            return;
        }
}
