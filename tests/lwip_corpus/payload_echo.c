/* payload_echo.c -- the corpus of the lwIP differential: the card's cells and one datagram.
 *
 * The UDP echo of the gate (docs/23 4.4) is slipif -> netif_input -> ip4_input -> udp_input
 * -> recv -> udp_sendto -> ip4_output -> slipif_output. The importer refuses ip4_input,
 * udp_input and udp_sendto (docs/35: llvm.bswap.i16), so that path does not run as imported
 * graphs yet and this file does not pretend it does. What it runs is the part of the echo
 * that DOES import, end to end through the card's own cells:
 *
 *   the boot      the module's wires (the memp pool table), the pools, the clock, the pcb
 *                 the echo service owns: udp_new + udp_bind to port 7
 *   the datagram  the payload of one UDP datagram is referenced by a pbuf (the way a driver
 *                 hands a received buffer up), copied into a second pbuf (the echo), compared
 *                 through the pbuf chain walk and read back byte by byte, both freed
 *   the close     the pcb is removed and the pools hold what they held before
 *
 * Every body below is imported next to the card's own and compared against the same C
 * compiled natively (tests/test_lwip_import.py), on the card's real options.
 */
#include "../../native/lwip/core_slice.c"

enum { ECHO_PORT = 7 };

static struct udp_pcb *echo_pcb;

/* the service's setup: answers the bind's err_t in the low byte and the port the pcb holds
 * in the next 16 bits (all ones when the pools gave no pcb) */
uint64_t echo_boot(void) {
    memp_init();
    udp_init();
    echo_pcb = udp_new();
    if (echo_pcb == NULL) {
        return ~(uint64_t)0;
    }
    err_t err = udp_bind(echo_pcb, IP4_ADDR_ANY, ECHO_PORT);
    return (uint64_t)(uint8_t)err | ((uint64_t)echo_pcb->local_port << 8);
}

/* one datagram's payload through two reference pbufs: copy ``in`` to ``out`` as the echo
 * does, answer the copy's err_t (byte 0), the compare of the two (byte 1: 0 when equal),
 * the chain length (byte 2) and the total length the copy holds (bytes 3-4) */
uint64_t echo_payload(const uint8_t *in, uint16_t length, uint8_t *out) {
    struct pbuf *received = pbuf_alloc_reference((void *)in, length, PBUF_REF);
    struct pbuf *echoed = pbuf_alloc_reference(out, length, PBUF_REF);
    if (received == NULL || echoed == NULL) {
        return ~(uint64_t)0;
    }
    err_t err = pbuf_copy(echoed, received);
    uint16_t differ = pbuf_memcmp(echoed, 0, in, length);
    uint8_t chain = pbuf_clen(echoed);
    uint16_t total = echoed->tot_len;
    pbuf_free(received);
    pbuf_free(echoed);
    return (uint64_t)(uint8_t)err | ((uint64_t)(differ != 0) << 8) | ((uint64_t)chain << 16) |
           ((uint64_t)total << 24);
}

/* the byte of a pbuf's payload at ``index`` (the read-back of what the echo wrote) */
uint64_t echo_byte(const uint8_t *buffer, uint16_t index) {
    struct pbuf *p = pbuf_alloc_reference((void *)buffer, (uint16_t)(index + 1), PBUF_REF);
    if (p == NULL) {
        return ~(uint64_t)0;
    }
    uint64_t value = pbuf_get_at(p, index);
    pbuf_free(p);
    return value;
}

/* the close: the pcb goes back to its pool; the answer is whether the next udp_new gets the
 * same cell back (1) -- the pool's free list is the observable state */
uint64_t echo_close(void) {
    struct udp_pcb *gone = echo_pcb;
    udp_remove(gone);
    struct udp_pcb *again = udp_new();
    uint64_t same = again == gone;
    if (again != NULL) {
        udp_remove(again);
    }
    echo_pcb = NULL;
    return same;
}
