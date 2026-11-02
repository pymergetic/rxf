/* slip_line_host.h -- the host seat's fill of the SLIP line (a socketpair to the harness). */
#ifndef RXF_SLIP_LINE_HOST_H
#define RXF_SLIP_LINE_HOST_H

#include <stdint.h>

#include "slip_line.h"

struct rxf_slip_line_host {
    int near_fd; /* the guest's end: the fill reads and writes it */
    int far_fd;  /* the peer's end: the harness (the other stack) reads and writes it */
    uint32_t escaped;
    uint32_t frame;
};

/* open the pair; 0 on success, -1 on failure (errno set). The near end is non-blocking. */
int rxf_slip_line_host_init(struct rxf_slip_line_host *host);

/* close both ends */
void rxf_slip_line_host_deinit(struct rxf_slip_line_host *host);

/* move what is waiting: the TX ring to the near end, the near end to the RX ring. Answers the
 * number of bytes moved in both directions (0: the line was idle), -1 on an I/O error. */
int64_t rxf_slip_line_host_pump(struct rxf_slip_line *line, struct rxf_slip_line_host *host);

#endif /* RXF_SLIP_LINE_HOST_H */
