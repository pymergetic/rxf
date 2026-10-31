/* slip_line.h -- the SLIP line of the lwIP card: its cells and the contract of its fills.
 *
 * docs/25 13: the line is a BYTE line (RFC 1055 END/ESC stuffing happens in lwIP's slipif on
 * the guest side and in the peer on the far side; VJ header compression is not in this
 * contract and slipif is built without it). The line contract of a seat's fill:
 *
 *   open / close   sio_open claims the one line (device 0) and answers its handle; close
 *                  releases it
 *   TX byte        sio_send appends one byte to the TX ring; a full ring drops the byte and
 *                  counts it in ``overrun`` (a void send has no way to push back)
 *   RX byte source sio_tryread takes bytes from the RX ring; ``rxf_slip_line_ready`` is the
 *                  readiness word (the bytes waiting) the run's wait/notify watches
 *   MTU            ``mtu`` is the largest datagram a peer may send, 1000 by default
 *                  (docs/25 13; lwIP's SLIP_MAX_SIZE is set to it by lwipopts.h)
 *   counters       ``overrun`` (bytes the TX ring dropped, frames the fill dropped for
 *                  exceeding the MTU) and ``framing`` (a bad escape sequence seen on RX) are
 *                  words of the line cell, i.e. image words -- never state in a fill's C
 *
 * The two rings and the counters are ONE cell of the image (``rxf_slip_line``). The face that
 * lwIP calls (slip_line_face.c) is imported with the card: its bodies read and write the cell.
 * A seat's FILL is native C that moves bytes between the cell and the seat's real line (a pty,
 * a tap device, a WebSocket, a UART) -- slip_line_host.c, slip_line_tap.c, slip_line_js.c,
 * slip_line_uart.c -- and touches the cell only through this layout. The layout is plain
 * 32-bit words and bytes, the same on every seat and in every language that fills it.
 */
#ifndef RXF_SLIP_LINE_H
#define RXF_SLIP_LINE_H

#include <stdint.h>

#define RXF_SLIP_RING_BYTES 4096u /* a power of two: an index is masked, never divided */
#define RXF_SLIP_MTU 1000u

#define RXF_SLIP_END 0xC0u
#define RXF_SLIP_ESC 0xDBu
#define RXF_SLIP_ESC_END 0xDCu
#define RXF_SLIP_ESC_ESC 0xDDu

/* one ring: ``head`` and ``tail`` count bytes for ever (wrapping at 2^32); the bytes waiting
 * are head - tail, the slot of a byte is its count masked by the size. The producer owns head,
 * the consumer owns tail: TX is produced by the guest and consumed by the fill, RX the other
 * way round. */
struct rxf_slip_ring {
    uint32_t head;
    uint32_t tail;
    uint8_t data[RXF_SLIP_RING_BYTES];
};

struct rxf_slip_line {
    uint32_t open;    /* 1 between sio_open and close */
    uint32_t mtu;     /* the settable MTU word */
    uint32_t overrun; /* bytes or frames dropped for want of room or over the MTU */
    uint32_t framing; /* bad escape sequences seen on RX (counted by the fill) */
    struct rxf_slip_ring tx;
    struct rxf_slip_ring rx;
};

#define RXF_SLIP_DEVICE 0u /* the one line: sio_open(0) */

#endif /* RXF_SLIP_LINE_H */
