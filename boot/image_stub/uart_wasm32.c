/* The Uart capability's fill for wasm32: the two FIFOs of the block in linear
 * memory (native/graph.h: rx_head, rx_tail, tx_head, tx_tail, then the rings).
 * There is no device to trap a load and no host import to call: the host is the
 * line (boot/wasm_shim.js drains the TX ring into its console FIFO), so the
 * fill writes the ring exactly as native/uart.c's wasm32 put does. */
#include "uart.h"

#define RX_HEAD 0U
#define RX_TAIL 4U
#define TX_HEAD 8U
#define TX_TAIL 12U

static volatile rxf_u32 *fifo_word(rxf_u64 base, rxf_u32 offset) {
    return (volatile rxf_u32 *)(unsigned long)(base + offset);
}

static volatile rxf_u8 *fifo_byte(rxf_u64 base, rxf_u32 offset) {
    return (volatile rxf_u8 *)(unsigned long)(base + offset);
}

void rxf_boot_uart_putc(const rxf_native_slot *capability, rxf_u8 byte) {
    /* on wasm32 the generation is not looked at (graph.h): the address is the
     * FIFO block, and a slot that does not name one is not this fill's */
    const rxf_u64 base = capability->address;
    if (!base)
        return;
    for (rxf_u32 spins = 0; spins != 0x1000000U; spins++) {
        /* the host drains; a ring the host has not emptied yet is a full device */
        const rxf_u32 head = *fifo_word(base, TX_HEAD);
        if (head - *fifo_word(base, TX_TAIL) < RXF_UART_FIFO_BYTES) {
            *fifo_byte(base, RXF_UART_FIFO_TX + head % RXF_UART_FIFO_BYTES) = byte;
            *fifo_word(base, TX_HEAD) = head + 1;
            return;
        }
    }
}
