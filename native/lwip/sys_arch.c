/* sys_arch.c -- the lwIP port face behind the VM seam, NO_SYS=1 (docs/23 4.4).
 *
 * With NO_SYS there is no mailbox and no semaphore; the whole port is the clock:
 * ``sys_now`` is the edge timer (S9) -- a millisecond word the host of the run sets
 * through ``rxf_lwip_set_now`` before it polls the stack. The word lives in a cell of
 * the image like any other module global, never in a thread or a libc clock.
 */
#include <stdint.h>

#include "lwip/sys.h"

static uint32_t rxf_lwip_now_ms;

void rxf_lwip_set_now(uint32_t now_ms) {
    rxf_lwip_now_ms = now_ms;
}

uint32_t sys_now(void) {
    return rxf_lwip_now_ms;
}
