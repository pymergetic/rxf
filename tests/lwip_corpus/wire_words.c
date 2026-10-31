/* wire_words.c -- the corpus of the second lwIP differential: the bodies the byte-swap and
 * min/max/abs lowerings of tools/native/rximport.py brought into the import (docs/35).
 *
 * Before those lowerings every body below was refused for an LLVM intrinsic: lwip_htons /
 * lwip_htonl and the Internet checksum (llvm.bswap.i16/i32), and pbuf_alloc and
 * pbuf_copy_partial (llvm.umin.i16, the LWIP_MIN of a u16_t length against the room of a
 * pbuf). These are the words every header of the UDP echo path carries, so each is imported
 * next to the card's own bodies and compared against the same C compiled natively
 * (tests/test_lwip_import.py), on the card's real options.
 *
 * The pbufs come from the pbuf pool and from references: the heap (mem_init) is still refused
 * for its constant expression (docs/35), so no PBUF_RAM body is run here.
 */
#include "../../native/lwip/core_slice.c"

/* the pools: pbuf_alloc of PBUF_POOL takes its cells from them */
uint64_t wire_boot(void) {
    memp_init();
    return 0;
}

uint64_t wire_htons(uint64_t value) {
    return lwip_htons((u16_t)value);
}

uint64_t wire_htonl(uint64_t value) {
    return lwip_htonl((u32_t)value);
}

/* the Internet checksum of a flat buffer: the swap of the odd trailing byte and of the folded
 * sum is what lwip_standard_chksum spells with llvm.bswap.i16 */
uint64_t wire_chksum(const uint8_t *data, uint16_t length) {
    return inet_chksum((void *)data, length);
}

/* the checksum over a chain: the two pbufs of a split buffer, the way a driver hands a frame
 * that arrived in two pieces (the odd split exercises the carried byte between the pieces) */
uint64_t wire_chksum_chain(const uint8_t *data, uint16_t length, uint16_t split) {
    struct pbuf *head = pbuf_alloc_reference((void *)data, split, PBUF_REF);
    struct pbuf *tail =
        pbuf_alloc_reference((void *)(data + split), (u16_t)(length - split), PBUF_REF);
    if (head == NULL || tail == NULL) {
        return ~(uint64_t)0;
    }
    pbuf_cat(head, tail);
    uint64_t sum = inet_chksum_pbuf(head);
    pbuf_free(head);
    return sum;
}

/* pbuf_alloc from the pool: the length of the first pbuf, the total, and the chain length
 * (each pbuf takes the smaller of the rest and the room of one pool cell: llvm.umin.i16) */
uint64_t wire_alloc(uint16_t length) {
    struct pbuf *p = pbuf_alloc(PBUF_RAW, length, PBUF_POOL);
    if (p == NULL) {
        return ~(uint64_t)0;
    }
    uint64_t answer =
        (uint64_t)p->len | ((uint64_t)p->tot_len << 16) | ((uint64_t)pbuf_clen(p) << 32);
    pbuf_free(p);
    return answer;
}

/* a datagram stored into a pool chain, then read back partially: ``want`` bytes from
 * ``offset``. The answer is the count pbuf_copy_partial copied (llvm.umin.i16 against the
 * bytes left in each pbuf) and the chain length; the bytes land in ``out`` */
uint64_t wire_chain_partial(const uint8_t *in, uint16_t length, uint8_t *out, uint16_t want,
                            uint16_t offset) {
    struct pbuf *p = pbuf_alloc(PBUF_RAW, length, PBUF_POOL);
    if (p == NULL) {
        return ~(uint64_t)0;
    }
    err_t err = pbuf_take(p, in, length);
    u16_t copied = pbuf_copy_partial(p, out, want, offset);
    uint64_t answer =
        (uint64_t)copied | ((uint64_t)pbuf_clen(p) << 16) | ((uint64_t)(uint8_t)err << 24);
    pbuf_free(p);
    return answer;
}
