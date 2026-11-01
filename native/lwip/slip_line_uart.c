/* slip_line_uart.c -- the firmware seat's fill of the SLIP line: see the header.
 *
 * The register dance is the two devices' own (the 16550 of the x86 seats, the PL011 of the
 * aarch64 seat); the ring moves and counters of the line are slip_line_fill.h's, and the
 * register access is the seat's pair of calls (the fill's one I/O seam).
 */
#include "slip_line_uart.h"

#include <errno.h>
#include <stddef.h>
#include <string.h>

#include "../graph.h"
#include "slip_line_fill.h"

/* the 16550's registers by number, and the bits this fill reads and writes */
enum {
    RXF_UART_RBR_THR = 0,
    RXF_UART_IER = 1,
    RXF_UART_FCR = 2,
    RXF_UART_LCR = 3,
    RXF_UART_MCR = 4,
    RXF_UART_LSR = 5,
};
#define RXF_UART_LSR_DR 0x01u   /* a byte waits in RBR */
#define RXF_UART_LSR_THRE 0x20u /* THR takes a byte */

/* the PL011's registers by number (its byte offset is n << 2) */
enum {
    RXF_UART_PL011_DR = 0,
    RXF_UART_PL011_FR = 6,
    RXF_UART_PL011_IBRD = 9,
    RXF_UART_PL011_FBRD = 10,
    RXF_UART_PL011_LCR_H = 11,
    RXF_UART_PL011_CR = 12,
    RXF_UART_PL011_IMSC = 14,
};
#define RXF_UART_PL011_FR_RXFE 0x10u /* the receive FIFO is empty */
#define RXF_UART_PL011_FR_TXFF 0x20u /* the transmit FIFO is full */

/* the memory-mapped register access: the device's words in place (the default pair) */
static uint32_t mmio_in(void *ctx, uintptr_t at, uint32_t wide) {
    (void)ctx;
    return wide != 0 ? *(volatile uint32_t *)at : *(volatile uint8_t *)at;
}

static void mmio_out(void *ctx, uintptr_t at, uint32_t value, uint32_t wide) {
    (void)ctx;
    if (wide != 0) {
        *(volatile uint32_t *)at = value;
    } else {
        *(volatile uint8_t *)at = (uint8_t)value;
    }
}

static uint32_t reg_read(struct rxf_slip_line_uart *u, uint32_t reg) {
    return u->in(u->ctx, u->base + ((uintptr_t)reg << u->shift), u->wide);
}

static void reg_write(struct rxf_slip_line_uart *u, uint32_t reg, uint32_t value) {
    u->out(u->ctx, u->base + ((uintptr_t)reg << u->shift), value, u->wide);
}

/* the line up: 115200 8N1, FIFO on, the RX interrupt on (the park's wake source on a machine
 * whose consumer sleeps in WFI) */
static void configure(struct rxf_slip_line_uart *u) {
    if (u->kind == RXF_UART_DEVICE_PL011_MMIO) {
        reg_write(u, RXF_UART_PL011_CR, 0);    /* the UART down while it is set up */
        reg_write(u, RXF_UART_PL011_IBRD, 13); /* 115200 at the 24 MHz clock of virt */
        reg_write(u, RXF_UART_PL011_FBRD, 1);
        reg_write(u, RXF_UART_PL011_LCR_H, 0x70u); /* FEN, 8 bits, one stop, no parity */
        reg_write(u, RXF_UART_PL011_IMSC, 0x10u);  /* RXIM: the receive interrupt */
        reg_write(u, RXF_UART_PL011_CR, 0x301u);   /* UARTEN, TXE, RXE */
        return;
    }
    reg_write(u, RXF_UART_LCR, 0x80u);      /* DLAB on */
    reg_write(u, RXF_UART_RBR_THR, 3);      /* divisor 3: 115200 baud */
    reg_write(u, RXF_UART_IER, 0);          /* divisor high */
    reg_write(u, RXF_UART_LCR, 0x03u);      /* 8 bits, no parity, one stop */
    reg_write(u, RXF_UART_FCR, 0xC7u);      /* FIFO on and cleared */
    reg_write(u, RXF_UART_IER, 0x01u);      /* the RX data-available interrupt */
}

int rxf_slip_line_uart_init(struct rxf_slip_line_uart *u, uintptr_t base, uint32_t device,
                            rxf_slip_uart_in_fn in, rxf_slip_uart_out_fn out, void *ctx) {
    memset(u, 0, sizeof(*u));
    u->kind = device & 0xFFu;
    u->shift = (device >> RXF_UART_SHIFT_AT) & 0x0Fu;
    u->wide = (device & RXF_UART_WIDE) != 0 ? 1u : 0u;
    if (u->kind != RXF_UART_DEVICE_NS16550_MMIO && u->kind != RXF_UART_DEVICE_PL011_MMIO &&
        u->kind != RXF_UART_DEVICE_NS16550_PORT) {
        errno = EINVAL; /* DEVICE_MACHINE is the seat's to resolve before the fill sees it */
        return -1;
    }
    u->base = base;
    u->in = in != NULL ? in : mmio_in;
    u->out = out != NULL ? out : mmio_out;
    u->ctx = ctx;
    configure(u);
    return 0;
}

void rxf_slip_line_uart_deinit(struct rxf_slip_line_uart *u) {
    u->in = NULL;
    u->out = NULL;
    u->ctx = NULL;
}

/* one TX byte when the transmit register takes one (the hardware clears its ready bit on the
 * write, so the next byte waits for the next readiness) */
static int64_t tx_out(struct rxf_slip_line *line, struct rxf_slip_line_uart *u) {
    int64_t moved = 0;
    for (;;) {
        const uint8_t *out;
        if (rxf_slip_fill_tx_run(line, &out) == 0) {
            return moved;
        }
        if (u->kind == RXF_UART_DEVICE_PL011_MMIO) {
            if ((reg_read(u, RXF_UART_PL011_FR) & RXF_UART_PL011_FR_TXFF) != 0) {
                return moved;
            }
            reg_write(u, RXF_UART_PL011_DR, out[0]);
        } else {
            if ((reg_read(u, RXF_UART_LSR) & RXF_UART_LSR_THRE) == 0) {
                return moved;
            }
            reg_write(u, RXF_UART_RBR_THR, out[0]);
        }
        rxf_slip_fill_tx_took(line, 1);
        moved++;
    }
}

/* every byte the receive register holds, into the line's RX ring: published and waking */
static int64_t rx_in(struct rxf_slip_line *line, struct rxf_slip_line_uart *u) {
    int64_t moved = 0;
    for (;;) {
        uint8_t *in;
        size_t run = rxf_slip_fill_rx_room(line, &in);
        if (run == 0) {
            return moved;
        }
        size_t got = 0;
        while (got < run) {
            uint32_t ready;
            if (u->kind == RXF_UART_DEVICE_PL011_MMIO) {
                ready = (reg_read(u, RXF_UART_PL011_FR) & RXF_UART_PL011_FR_RXFE) == 0;
            } else {
                ready = (reg_read(u, RXF_UART_LSR) & RXF_UART_LSR_DR) != 0;
            }
            if (!ready) {
                break;
            }
            in[got++] = (uint8_t)(u->kind == RXF_UART_DEVICE_PL011_MMIO
                                      ? reg_read(u, RXF_UART_PL011_DR)
                                      : reg_read(u, RXF_UART_RBR_THR));
        }
        if (got == 0) {
            return moved;
        }
        rxf_slip_fill_rx_gave(line, got, &u->escaped, &u->frame);
        moved += (int64_t)got;
    }
}

int64_t rxf_slip_line_uart_pump(struct rxf_slip_line *line, struct rxf_slip_line_uart *u) {
    return tx_out(line, u) + rx_in(line, u);
}
