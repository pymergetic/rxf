/* slip_line_host.c -- the host seat's fill of the SLIP line: a unix socketpair (docs/25 13).
 *
 * The guest's end is read and written by the fill; the other end belongs to the harness, which
 * plays the peer stack (the Python SlipLine of src/pymergetic/rxftk/net/link/slip.py, or any
 * program speaking RFC 1055). A socketpair is a byte stream with the semantics of a serial
 * line, which is all the contract asks of it. The ring moves and the counters are
 * slip_line_fill.h's; this file is only the I/O.
 */
#include "slip_line_host.h"

#include <errno.h>
#include <sys/socket.h>
#include <unistd.h>

#include "slip_line_fill.h"

int rxf_slip_line_host_init(struct rxf_slip_line_host *host) {
    int ends[2];
    if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK, 0, ends) != 0) {
        return -1;
    }
    host->near_fd = ends[0];
    host->far_fd = ends[1];
    host->escaped = 0;
    host->frame = 0;
    return 0;
}

void rxf_slip_line_host_deinit(struct rxf_slip_line_host *host) {
    if (host->near_fd >= 0) {
        close(host->near_fd);
    }
    if (host->far_fd >= 0) {
        close(host->far_fd);
    }
    host->near_fd = -1;
    host->far_fd = -1;
}

int64_t rxf_slip_line_host_pump(struct rxf_slip_line *line, struct rxf_slip_line_host *host) {
    int64_t moved = 0;
    const uint8_t *out;
    size_t run;
    while ((run = rxf_slip_fill_tx_run(line, &out)) != 0) {
        ssize_t sent = write(host->near_fd, out, run);
        if (sent < 0) {
            if (errno == EAGAIN || errno == EINTR) {
                break;
            }
            return -1;
        }
        rxf_slip_fill_tx_took(line, (size_t)sent);
        moved += sent;
    }
    uint8_t *in;
    while ((run = rxf_slip_fill_rx_room(line, &in)) != 0) {
        ssize_t got = read(host->near_fd, in, run);
        if (got < 0) {
            if (errno == EAGAIN || errno == EINTR) {
                break;
            }
            return -1;
        }
        if (got == 0) {
            break;
        }
        rxf_slip_fill_rx_gave(line, (size_t)got, &host->escaped, &host->frame);
        moved += got;
    }
    return moved;
}
