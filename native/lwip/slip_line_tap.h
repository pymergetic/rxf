/* slip_line_tap.h -- the unix seat's tap fill of the SLIP line: a tun device (docs/25 13).
 *
 * The device speaks IP packets, one per read and write; the line speaks RFC 1055 bytes. The fill
 * is the bridge: it SLIP-encodes each packet the device delivers into the RX ring and
 * SLIP-decodes the TX ring into packets for the device. It owns the codec state the bridge needs
 * across calls (an escape half seen, a packet half decoded, a packet read that the RX ring had no
 * room for yet) and nothing else; the line cell keeps the counters.
 *
 * The device open is Linux's (/dev/net/tun, TUNSETIFF, IFF_TUN | IFF_NO_PI) and needs
 * CAP_NET_ADMIN; ``rxf_slip_line_tap_adopt`` takes any other packet-oriented descriptor (a
 * datagram socketpair stands in for the device in the contract suite, a utun on macOS is a
 * later open of the same bridge).
 */
#ifndef RXF_SLIP_LINE_TAP_H
#define RXF_SLIP_LINE_TAP_H

#include <stdint.h>

#include "slip_line.h"

#define RXF_SLIP_TAP_PACKET 2048u /* the largest packet the bridge holds */

struct rxf_slip_line_tap {
    int fd;              /* the packet descriptor: a tun device, or any datagram stand-in */
    uint32_t tx_escaped; /* the last TX byte was ESC */
    uint32_t tx_length;  /* bytes of the TX packet decoded so far */
    uint32_t tx_overrun; /* the TX packet in flight exceeded the MTU: dropped at its END */
    uint32_t rx_length;  /* bytes of a device packet waiting for room in the RX ring */
    uint8_t tx_packet[RXF_SLIP_TAP_PACKET];
    uint8_t rx_packet[RXF_SLIP_TAP_PACKET];
};

/* a bridge with no descriptor yet */
void rxf_slip_line_tap_init(struct rxf_slip_line_tap *tap);

/* open the tun device ``name`` (non-blocking); 0 on success, -1 with errno set (EPERM without
 * CAP_NET_ADMIN, ENOSYS where the seat has no tun open) */
int rxf_slip_line_tap_open(struct rxf_slip_line_tap *tap, const char *name);

/* take a packet descriptor (made non-blocking); the bridge closes it in deinit */
int rxf_slip_line_tap_adopt(struct rxf_slip_line_tap *tap, int fd);

/* close the descriptor */
void rxf_slip_line_tap_deinit(struct rxf_slip_line_tap *tap);

/* move what is waiting: TX ring bytes decoded into packets for the device, device packets
 * encoded into the RX ring (a packet the ring has no room for waits, never dropped for it).
 * Answers the packets moved in both directions, -1 on an I/O error. */
int64_t rxf_slip_line_tap_pump(struct rxf_slip_line *line, struct rxf_slip_line_tap *tap);

#endif /* RXF_SLIP_LINE_TAP_H */
