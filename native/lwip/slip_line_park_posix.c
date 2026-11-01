/* slip_line_park_posix.c -- the park/wake pair of the POSIX edges (docs/25 13).
 *
 * A semaphore is a park/wake pair: this file is that pair over a mutex and a condvar, the
 * edge of the host, tap and unix seats (and of every prove that drives a fill from a host
 * harness). A wasm32 seat binds the JS event loop and a firmware seat the RX interrupt and
 * WFI under the same two names (slip_line_fill.h); the protocol over the cell words is the
 * one the contract suite pins on every seat.
 *
 * The token protocol (docs/20 S4), over the cell's ``waiting`` word:
 *
 *   park   set the token, then re-check the readiness word (the token is consumed at the
 *          park, so a wake that arrived before it is never lost -- the bytes a premature wake
 *          announced are the readiness word itself), then sleep
 *   wake   the publish, then this pair under the lock: a token set means a sleeping consumer,
 *          count the wake and signal it. A wake is a hint and the readiness word is the
 *          condition (the binding re-checks it after every resume), so a signal to nobody and
 *          a spurious resume are both no-ops.
 *
 * The pair is state of the seat's edge, not of the image: the words it maintains (``waiting``,
 * ``wakes``) are words of the line cell like every other observable word.
 */
#include <pthread.h>
#include <stdint.h>

#include "slip_line.h"

/* the one line (sio_open claims device 0 and nothing else): one pair */
static pthread_mutex_t rxf_slip_park_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t rxf_slip_park_cond = PTHREAD_COND_INITIALIZER;

uint32_t rxf_slip_line_wait(struct rxf_slip_line *line) {
    pthread_mutex_lock(&rxf_slip_park_lock);
    line->waiting = 1; /* the token goes up before the re-check: no wake is lost to it */
    while (line->open != 0 && line->rx.head == line->rx.tail) {
        pthread_cond_wait(&rxf_slip_park_cond, &rxf_slip_park_lock);
    }
    line->waiting = 0;
    pthread_mutex_unlock(&rxf_slip_park_lock);
    return line->rx.head - line->rx.tail;
}

void rxf_slip_line_notify(struct rxf_slip_line *line) {
    pthread_mutex_lock(&rxf_slip_park_lock);
    if (line->waiting != 0) {
        line->wakes++;
        pthread_cond_broadcast(&rxf_slip_park_cond);
    }
    pthread_mutex_unlock(&rxf_slip_park_lock);
}
