/* slip_line_tap.c -- the unix seat's tap fill of the SLIP line (docs/25 13): see the header. */
#include "slip_line_tap.h"

#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <unistd.h>

#ifdef __linux__
#include <linux/if.h>
#include <linux/if_tun.h>
#include <sys/ioctl.h>
#endif

#include "slip_line_fill.h"

void rxf_slip_line_tap_init(struct rxf_slip_line_tap *tap) {
    memset(tap, 0, sizeof(*tap));
    tap->fd = -1;
}

int rxf_slip_line_tap_adopt(struct rxf_slip_line_tap *tap, int fd) {
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) != 0) {
        return -1;
    }
    tap->fd = fd;
    return 0;
}

int rxf_slip_line_tap_open(struct rxf_slip_line_tap *tap, const char *name) {
#ifdef __linux__
    int fd = open("/dev/net/tun", O_RDWR | O_NONBLOCK);
    if (fd < 0) {
        return -1;
    }
    struct ifreq request;
    memset(&request, 0, sizeof(request));
    request.ifr_flags = IFF_TUN | IFF_NO_PI;
    strncpy(request.ifr_name, name, IFNAMSIZ - 1);
    if (ioctl(fd, TUNSETIFF, &request) != 0) {
        int saved = errno;
        close(fd);
        errno = saved;
        return -1;
    }
    tap->fd = fd;
    return 0;
#else
    (void)tap;
    (void)name;
    errno = ENOSYS;
    return -1;
#endif
}

void rxf_slip_line_tap_deinit(struct rxf_slip_line_tap *tap) {
    if (tap->fd >= 0) {
        close(tap->fd);
    }
    tap->fd = -1;
}

/* one decoded TX byte of the packet in flight */
static void tx_append(struct rxf_slip_line *line, struct rxf_slip_line_tap *tap, uint8_t byte) {
    if (tap->tx_overrun) {
        return;
    }
    if (tap->tx_length >= line->mtu || tap->tx_length >= RXF_SLIP_TAP_PACKET) {
        tap->tx_overrun = 1; /* dropped whole at its END, never cut short */
        return;
    }
    tap->tx_packet[tap->tx_length++] = byte;
}

/* the END of a TX frame: the packet goes to the device, or is dropped and counted */
static int tx_end(struct rxf_slip_line *line, struct rxf_slip_line_tap *tap) {
    int sent = 0;
    if (tap->tx_overrun) {
        line->overrun++;
    } else if (tap->tx_length != 0) { /* an empty frame is the flush END, not a packet */
        ssize_t wrote = write(tap->fd, tap->tx_packet, tap->tx_length);
        if (wrote < 0 && errno != EAGAIN && errno != EINTR) {
            return -1;
        }
        if (wrote < 0) {
            line->overrun++; /* the device would block: the packet is dropped and counted */
        } else {
            sent = 1;
        }
    }
    tap->tx_length = 0;
    tap->tx_overrun = 0;
    tap->tx_escaped = 0;
    return sent;
}

/* the RX packet in rx_packet as one SLIP frame at the RX head; 0 when the ring has no room */
static int rx_publish(struct rxf_slip_line *line, struct rxf_slip_line_tap *tap) {
    uint32_t worst = 2u * tap->rx_length + 2u;
    if (RXF_SLIP_RING_BYTES - (line->rx.head - line->rx.tail) < worst) {
        return 0;
    }
    uint32_t head = line->rx.head;
#define RXF_PUT(byte) (line->rx.data[head++ & (RXF_SLIP_RING_BYTES - 1u)] = (uint8_t)(byte))
    RXF_PUT(RXF_SLIP_END); /* the flush END RFC 1055 opens a frame with */
    for (uint32_t i = 0; i < tap->rx_length; i++) {
        uint8_t c = tap->rx_packet[i];
        if (c == RXF_SLIP_END) {
            RXF_PUT(RXF_SLIP_ESC);
            RXF_PUT(RXF_SLIP_ESC_END);
        } else if (c == RXF_SLIP_ESC) {
            RXF_PUT(RXF_SLIP_ESC);
            RXF_PUT(RXF_SLIP_ESC_ESC);
        } else {
            RXF_PUT(c);
        }
    }
    RXF_PUT(RXF_SLIP_END);
#undef RXF_PUT
    line->rx.head = head;
    tap->rx_length = 0;
    return 1;
}

int64_t rxf_slip_line_tap_pump(struct rxf_slip_line *line, struct rxf_slip_line_tap *tap) {
    int64_t moved = 0;
    while (line->tx.tail != line->tx.head) {
        uint8_t c = line->tx.data[line->tx.tail & (RXF_SLIP_RING_BYTES - 1u)];
        line->tx.tail++;
        if (tap->tx_escaped) {
            tap->tx_escaped = 0;
            if (c == RXF_SLIP_ESC_END) {
                c = RXF_SLIP_END;
            } else if (c == RXF_SLIP_ESC_ESC) {
                c = RXF_SLIP_ESC;
            } else {
                line->framing++; /* RFC 1055's best bet: the byte stands alone */
            }
            tx_append(line, tap, c);
        } else if (c == RXF_SLIP_END) {
            int sent = tx_end(line, tap);
            if (sent < 0) {
                return -1;
            }
            moved += sent;
        } else if (c == RXF_SLIP_ESC) {
            tap->tx_escaped = 1;
        } else {
            tx_append(line, tap, c);
        }
    }
    for (;;) {
        if (tap->rx_length == 0) {
            ssize_t got = read(tap->fd, tap->rx_packet, sizeof(tap->rx_packet));
            if (got < 0) {
                if (errno == EAGAIN || errno == EINTR) {
                    break;
                }
                return -1;
            }
            if (got == 0) {
                break;
            }
            if ((uint32_t)got > line->mtu) {
                line->overrun++; /* a packet over the MTU never enters the line */
                continue;
            }
            tap->rx_length = (uint32_t)got;
        }
        if (!rx_publish(line, tap)) {
            break; /* no room: the packet waits in rx_packet, the next pump publishes it */
        }
        rxf_slip_fill_wake(line); /* the frame arrived: wake a parked consumer */
        moved++;
    }
    return moved;
}
