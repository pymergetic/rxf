/* slip_line_face.c -- the sio face lwIP's slipif calls, over the line cell (docs/25 13).
 *
 * This file is part of the card's translation unit (core_slice.c includes it): the bodies
 * below are imported with the card, the way the faces of tests/import_corpus/qstr_faces.c are,
 * and run as graphs on every seat. They own no I/O: sio_send fills the TX ring and sio_tryread
 * drains the RX ring of the one line cell; the seat's fill (slip_line_*.c) moves bytes between
 * those rings and its real line. One face, one cell, a fill per seat.
 */
#include <stddef.h>
#include <stdint.h>

#include "lwip/sio.h"
#include "slip_line.h"

struct rxf_slip_line rxf_slip_line;

/* open the one line: answers its handle (the cell's address), NULL for any other device */
sio_fd_t sio_open(u8_t devnum) {
    if (devnum != RXF_SLIP_DEVICE) {
        return NULL;
    }
    rxf_slip_line.open = 1;
    if (rxf_slip_line.mtu == 0) {
        rxf_slip_line.mtu = RXF_SLIP_MTU;
    }
    return &rxf_slip_line;
}

/* close the line: nothing is sent or read until it is opened again */
void rxf_slip_line_close(void) {
    rxf_slip_line.open = 0;
}

/* one TX byte; a full ring drops it and counts it */
void sio_send(u8_t c, sio_fd_t fd) {
    struct rxf_slip_line *line = (struct rxf_slip_line *)fd;
    if (!line->open) {
        return;
    }
    if (line->tx.head - line->tx.tail >= RXF_SLIP_RING_BYTES) {
        line->overrun++;
        return;
    }
    line->tx.data[line->tx.head & (RXF_SLIP_RING_BYTES - 1u)] = c;
    line->tx.head++;
}

/* up to ``len`` RX bytes into ``data``: how many were there (0: none, not an error) */
u32_t sio_tryread(sio_fd_t fd, u8_t *data, u32_t len) {
    struct rxf_slip_line *line = (struct rxf_slip_line *)fd;
    u32_t count = 0;
    while (count < len && line->rx.tail != line->rx.head) {
        data[count] = line->rx.data[line->rx.tail & (RXF_SLIP_RING_BYTES - 1u)];
        line->rx.tail++;
        count++;
    }
    return count;
}

/* the readiness word of the RX byte source: bytes waiting (the run's wait/notify watches it) */
uint32_t rxf_slip_line_ready(void) {
    return rxf_slip_line.rx.head - rxf_slip_line.rx.tail;
}
