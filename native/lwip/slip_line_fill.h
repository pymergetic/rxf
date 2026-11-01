/* slip_line_fill.h -- what every native fill of the SLIP line shares (docs/25 13).
 *
 * A fill moves bytes between the line cell (slip_line.h) and its seat's real line. The ring
 * moves, the RX accounting and the wake of a parked consumer are the same on every seat, so
 * they are written once, here, as inline C over the cell's layout: a seat's fill file adds
 * only its own I/O (pty, tap, UART, WebSocket). The names are the module's own:
 * rxf_slip_fill_*.
 *
 * The park/wake pair itself (docs/25 13: a semaphore is a park/wake pair) is the seat's edge,
 * under two names every seat binds: ``rxf_slip_line_wait`` (the consumer's park) and
 * ``rxf_slip_line_notify`` (the wake a publish performs). slip_line_park_posix.c is the pair
 * of the POSIX edges in this tree; a wasm32 seat binds the JS event loop and a firmware seat
 * the RX interrupt and WFI under the same two names. The token protocol over the cell's
 * ``waiting`` word lives in the pair and is pinned by the contract suite on every seat.
 */
#ifndef RXF_SLIP_LINE_FILL_H
#define RXF_SLIP_LINE_FILL_H

#include <stddef.h>
#include <stdint.h>

#include "slip_line.h"

/* the consumer's park: sleep while the readiness word is 0 (a wake that arrived before the
 * park is consumed here -- docs/20 S4 -- and a wake is a hint, the readiness word is the
 * condition). Answers the bytes that wait when the park ends. */
uint32_t rxf_slip_line_wait(struct rxf_slip_line *line);

/* the wake: called when bytes were published. The pair checks the park token and wakes the
 * sleeping consumer; without a parked consumer it does nothing. */
void rxf_slip_line_notify(struct rxf_slip_line *line);

/* the bytes of the TX ring the fill may take: a contiguous run from the tail (0 when empty) */
static inline size_t rxf_slip_fill_tx_run(const struct rxf_slip_line *line, const uint8_t **at) {
    uint32_t queued = line->tx.head - line->tx.tail;
    uint32_t slot = line->tx.tail & (RXF_SLIP_RING_BYTES - 1u);
    uint32_t run = RXF_SLIP_RING_BYTES - slot;
    *at = &line->tx.data[slot];
    return queued < run ? queued : run;
}

/* the fill took ``count`` bytes of the TX ring */
static inline void rxf_slip_fill_tx_took(struct rxf_slip_line *line, size_t count) {
    line->tx.tail += (uint32_t)count;
}

/* the room of the RX ring: a contiguous run from the head the fill may write (0 when full) */
static inline size_t rxf_slip_fill_rx_room(struct rxf_slip_line *line, uint8_t **at) {
    uint32_t used = line->rx.head - line->rx.tail;
    uint32_t slot = line->rx.head & (RXF_SLIP_RING_BYTES - 1u);
    uint32_t run = RXF_SLIP_RING_BYTES - slot;
    uint32_t room = RXF_SLIP_RING_BYTES - used;
    *at = &line->rx.data[slot];
    return room < run ? room : run;
}

/* the wake of a publish: a fill that writes the RX head itself (a packet fill publishes a
 * whole frame at a time) calls this where rx_gave would */
static inline void rxf_slip_fill_wake(struct rxf_slip_line *line) {
    rxf_slip_line_notify(line);
}

/* the fill wrote ``count`` bytes at the RX head: publish them, wake a parked consumer, and
 * account the stream the line carries. ``escaped`` is the fill's one bit of state across
 * calls: the previous byte was ESC.
 * A byte after ESC that is neither ESC_END nor ESC_ESC is a framing error (RFC 1055 leaves the
 * byte as it is, lwIP's slipif keeps it too; the line counts it). A frame longer than the MTU
 * is not cut here -- slipif drops what exceeds SLIP_MAX_SIZE -- but is counted in ``overrun``
 * (once per frame, by its decoded length).
 * ``frame`` is the length of the frame being received, kept by the fill across calls. */
static inline void rxf_slip_fill_rx_gave(struct rxf_slip_line *line, size_t count,
                                         uint32_t *escaped, uint32_t *frame) {
    for (size_t i = 0; i < count; i++) {
        uint8_t c = line->rx.data[(line->rx.head + (uint32_t)i) & (RXF_SLIP_RING_BYTES - 1u)];
        if (c == RXF_SLIP_END) {
            *frame = 0;
            *escaped = 0;
            continue;
        }
        if (*escaped) {
            if (c != RXF_SLIP_ESC_END && c != RXF_SLIP_ESC_ESC) {
                line->framing++;
            }
            *escaped = 0;
        } else if (c == RXF_SLIP_ESC) {
            *escaped = 1;
            continue; /* the escape byte itself is no payload: the byte after it is */
        }
        if (++*frame == line->mtu + 1u) {
            line->overrun++;
        }
    }
    line->rx.head += (uint32_t)count;
    if (count != 0) {
        rxf_slip_fill_wake(line);
    }
}

#endif /* RXF_SLIP_LINE_FILL_H */
