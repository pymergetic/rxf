/* slip_line_js.c -- the browser (emcc / wasm32) seat's fill of the SLIP line: see the header.
 *
 * The far side is the FIFO block of native/graph.h, the host is the line (web/rxf.js drains the
 * block's TX ring and fills its RX ring, the carrier a WebSocket to the rxf net gateway). The
 * block is a byte stream with the semantics of a serial line, which is all the contract asks of
 * it. The ring moves and the counters of the line are slip_line_fill.h's; this file is only
 * the movement between the two rings, one run at a time. */
#include "slip_line_js.h"

#include <string.h>

#include "../graph.h"
#include "slip_line_fill.h"

void rxf_slip_line_js_init(struct rxf_slip_line_js *js, void *block) {
    memset(block, 0, RXF_UART_FIFO_BLOCK);
    js->words = (volatile uint32_t *)block;
    js->rx = (volatile uint8_t *)((uintptr_t)block + RXF_UART_FIFO_RX);
    js->tx = (volatile uint8_t *)((uintptr_t)block + RXF_UART_FIFO_TX);
    js->escaped = 0;
    js->frame = 0;
}

void rxf_slip_line_js_deinit(struct rxf_slip_line_js *js) {
    js->words = NULL;
    js->rx = NULL;
    js->tx = NULL;
}

/* the line's TX ring into the block's tx FIFO (tx_head is the image's word); what the FIFO has
 * no room for stays in the line -- backpressure, never a drop */
static int64_t tx_out(struct rxf_slip_line *line, struct rxf_slip_line_js *js) {
    int64_t moved = 0;
    for (;;) {
        uint32_t queued = js->words[2] - js->words[3];
        if (queued >= RXF_UART_FIFO_BYTES) {
            return moved;
        }
        const uint8_t *out;
        size_t run = rxf_slip_fill_tx_run(line, &out);
        if (run == 0) {
            return moved;
        }
        uint32_t room = RXF_UART_FIFO_BYTES - queued;
        if (run > room) {
            run = room;
        }
        uint32_t head = js->words[2];
        for (size_t i = 0; i < run; i++) {
            js->tx[(head + (uint32_t)i) & (RXF_UART_FIFO_BYTES - 1u)] = out[i];
        }
        js->words[2] = head + (uint32_t)run;
        rxf_slip_fill_tx_took(line, run);
        moved += (int64_t)run;
    }
}

/* the block's rx FIFO into the line's RX ring (rx_tail is the image's word): published and
 * waking */
static int64_t rx_in(struct rxf_slip_line *line, struct rxf_slip_line_js *js) {
    int64_t moved = 0;
    for (;;) {
        uint32_t waiting = js->words[0] - js->words[1];
        if (waiting == 0) {
            return moved;
        }
        uint8_t *in;
        size_t run = rxf_slip_fill_rx_room(line, &in);
        if (run == 0) {
            return moved;
        }
        if (run > waiting) {
            run = waiting;
        }
        uint32_t tail = js->words[1];
        for (size_t i = 0; i < run; i++) {
            in[i] = js->rx[(tail + (uint32_t)i) & (RXF_UART_FIFO_BYTES - 1u)];
        }
        js->words[1] = tail + (uint32_t)run;
        rxf_slip_fill_rx_gave(line, run, &js->escaped, &js->frame);
        moved += (int64_t)run;
    }
}

int64_t rxf_slip_line_js_pump(struct rxf_slip_line *line, struct rxf_slip_line_js *js) {
    return tx_out(line, js) + rx_in(line, js);
}
