/* slip_line_js.h -- the browser (emcc / wasm32) seat's fill of the SLIP line (docs/25 13).
 *
 * On wasm32 there is no port and no device to trap a load: the seat's real line is its FIFO
 * block (native/graph.h, the Uart capability of a wasm32 image) and the host is the line --
 * web/rxf.js drains the block's TX ring and fills its RX ring, and the decided carrier for
 * the SLIP bytes is a WebSocket to the ``rxf net gateway`` (docs/23 decision 8). This file is
 * the image-side half: it moves bytes between the line cell (slip_line.h) and the FIFO block.
 * The block is claimed whole as this line (RXF_UART_FIFO_BLOCK bytes, started empty); a seat
 * whose console shares a FIFO block gives the line its own.
 *
 * The register words and ring offsets are native/graph.h's, never copied here.
 */
#ifndef RXF_SLIP_LINE_JS_H
#define RXF_SLIP_LINE_JS_H

#include <stdint.h>

#include "slip_line.h"

struct rxf_slip_line_js {
    volatile uint32_t *words; /* rx_head, rx_tail, tx_head, tx_tail (graph.h's four words) */
    volatile uint8_t *rx;     /* the host -> image ring at RXF_UART_FIFO_RX */
    volatile uint8_t *tx;     /* the image -> host ring at RXF_UART_FIFO_TX */
    uint32_t escaped;         /* the fill's SLIP stream state across calls (fill.h) */
    uint32_t frame;
};

/* claim ``block`` (RXF_UART_FIFO_BLOCK bytes) as the line and start it empty */
void rxf_slip_line_js_init(struct rxf_slip_line_js *js, void *block);

/* release the block: nothing is read or written until it is claimed again */
void rxf_slip_line_js_deinit(struct rxf_slip_line_js *js);

/* move bytes both ways between the line cell and the FIFO block: the TX ring out (a full
 * block leaves the rest in the line, counted never -- backpressure, not a drop), the RX ring
 * in (published and waking). Answers the bytes moved. */
int64_t rxf_slip_line_js_pump(struct rxf_slip_line *line, struct rxf_slip_line_js *js);

#endif /* RXF_SLIP_LINE_JS_H */
