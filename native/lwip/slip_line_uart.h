/* slip_line_uart.h -- the firmware seat's fill of the SLIP line (docs/25 13): a 16550 or a
 * PL011 UART, one register at a time.
 *
 * The device descriptor is the Uart capability's own (native/graph.h): the base of the device
 * and its generation word -- the kind in the low byte (NS16550_MMIO, PL011_MMIO or
 * NS16550_PORT; a seat resolves DEVICE_MACHINE to its kind before the fill sees it), the
 * distance of the registers as a shift (register n is at base + (n << shift)), and
 * RXF_UART_WIDE when a register is read and written as a 32-bit word (the byte is the low
 * one). The three firmware carriers -- the 16550 behind I/O ports on x86_64 BIOS and UEFI,
 * the PL011 in memory on aarch64 virt -- are one fill over this word.
 *
 * The register access is the seat's own pair of calls (the one I/O seam of this fill): the
 * memory-mapped kinds read and write the device's words in place, an NS16550_PORT seat plugs
 * its inb/outb (the wrap seat's serial face) in the same two calls, and a prove plugs a
 * register-level emulation of the device. The config this fill performs is the seat's line
 * bring-up (115200 8N1, FIFO on, the RX interrupt on -- the park's wake source on a machine
 * whose consumer sleeps in WFI).
 */
#ifndef RXF_SLIP_LINE_UART_H
#define RXF_SLIP_LINE_UART_H

#include <stdint.h>

#include "slip_line.h"

typedef uint32_t (*rxf_slip_uart_in_fn)(void *ctx, uintptr_t at, uint32_t wide);
typedef void (*rxf_slip_uart_out_fn)(void *ctx, uintptr_t at, uint32_t value, uint32_t wide);

struct rxf_slip_line_uart {
    uintptr_t base;
    uint32_t kind;    /* the device word's low byte */
    uint32_t shift;   /* register n is at base + (n << shift) */
    uint32_t wide;    /* RXF_UART_WIDE: a register is one 32-bit word */
    uint32_t escaped; /* the fill's SLIP stream state across calls (fill.h) */
    uint32_t frame;
    rxf_slip_uart_in_fn in;
    rxf_slip_uart_out_fn out;
    void *ctx;
};

/* claim the device: validates the device word, sets the register access (NULL pair: the
 * memory-mapped kinds in place) and brings the line up (115200 8N1, the RX interrupt on).
 * Answers 0, or -1 with errno EINVAL for a device word this fill cannot claim. */
int rxf_slip_line_uart_init(struct rxf_slip_line_uart *u, uintptr_t base, uint32_t device,
                            rxf_slip_uart_in_fn in, rxf_slip_uart_out_fn out, void *ctx);

/* release the device: nothing is read or written until it is claimed again */
void rxf_slip_line_uart_deinit(struct rxf_slip_line_uart *u);

/* move bytes both ways between the line cell and the device: the TX ring out (one byte per
 * empty transmit register, the way the hardware takes them), the RX ring in (published and
 * waking). Answers the bytes moved. */
int64_t rxf_slip_line_uart_pump(struct rxf_slip_line *line, struct rxf_slip_line_uart *u);

#endif /* RXF_SLIP_LINE_UART_H */
