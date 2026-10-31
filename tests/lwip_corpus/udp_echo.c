/* udp_echo.c -- the corpus of the real UDP echo differential (docs/35, docs/23 4.4).
 *
 * The echo of the gate, over the SLIP line: a SLIP-framed IPv4/UDP datagram arrives on the
 * line, slipif deframes it and hands it to ip4_input, udp_input finds the pcb of port 7 and
 * calls its recv word, the recv word is ``udp_sendto`` back to the sender, ip4_output_if_src
 * builds the reply, the netif's output word is slipif_output_v4 and SLIP-frames it onto the
 * line. An ICMP echo request takes the other branch of ip4_input (icmp_input answers it).
 *
 * Every body here and every body of the card is imported; the same C compiled natively is the
 * test reference (tests/test_lwip_slip.py), and both sides get their bytes from the same line
 * face (slip_line_face.c) and the same fill (slip_line_host.c). The harness is the peer.
 *
 * The netif is attached by ``slip_echo_boot`` itself, in the order netif_add does it, because
 * netif_add is refused by the importer (docs/35: a write through a pointer its caller gave,
 * out of line -- the out-param call card of docs/23 4.1). When that card lands this body
 * calls netif_add and ``test_the_netif_is_attached_by_hand_until_netif_add_imports`` flips.
 */
#include "../../native/lwip/core_slice.c"

enum { ECHO_PORT = 7 };

static struct netif slip_netif;
static ip4_addr_t slip_ip;
static ip4_addr_t slip_mask;
static ip4_addr_t slip_gw;
static struct udp_pcb *echo_pcb;

/* the service: send the datagram back to where it came from, from the port it came to */
static void echo_recv(void *arg, struct udp_pcb *pcb, struct pbuf *p, const ip_addr_t *addr,
                      u16_t port) {
    (void)arg;
    udp_sendto(pcb, p, addr, port);
    pbuf_free(p);
}

/* the boot of the stack, the attach of the SLIP netif at 10.0.0.2/24 and the echo service on
 * port 7; answers 0, or the err_t of the step that failed (1000 + step) */
uint64_t slip_echo_boot(void) {
    lwip_init();
    ip4_addr_set_u32(&slip_ip, PP_HTONL(0x0A000002u));
    ip4_addr_set_u32(&slip_mask, PP_HTONL(0xFFFFFF00u));
    ip4_addr_set_u32(&slip_gw, PP_HTONL(0x0A000001u));

    /* netif_add, by hand: reset, state (the sio device number), input, addresses, init, list */
    slip_netif.output = netif_null_output_ip4;
    slip_netif.mtu = 0;
    slip_netif.flags = 0;
    slip_netif.state = (void *)(uintptr_t)RXF_SLIP_DEVICE;
    slip_netif.num = 0;
    slip_netif.input = ip4_input;
    netif_set_addr(&slip_netif, &slip_ip, &slip_mask, &slip_gw);
    if (slipif_init(&slip_netif) != ERR_OK) {
        return 1001;
    }
    slip_netif.next = netif_list;
    netif_list = &slip_netif;
    netif_set_default(&slip_netif);
    netif_set_up(&slip_netif);
    netif_set_link_up(&slip_netif);

    echo_pcb = udp_new();
    if (echo_pcb == NULL) {
        return 1002;
    }
    if (udp_bind(echo_pcb, IP4_ADDR_ANY, ECHO_PORT) != ERR_OK) {
        return 1003;
    }
    echo_pcb->recv = echo_recv;
    echo_pcb->recv_arg = NULL;
    return 0;
}

/* drain the line: every frame the peer sent is input, every reply goes out on the line */
uint64_t slip_echo_poll(void) {
    slipif_poll(&slip_netif);
    return 0;
}
