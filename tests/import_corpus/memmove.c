#include <stdint.h>

/* The overlap-safe move (llvm.memmove): clang keeps the intrinsic exactly where the
   ranges may meet -- a shift of bytes inside one buffer (the shape of lwIP's lwip_itoa
   and netif_index_to_name). The length and the shift the compiler cannot fold keep the
   call real (a constant copy lowers to loads and stores at -O1); the bytes travel inside
   one 16-byte buffer of the flat memory, so the overlap of the differential is real on
   both sides. The importer lowers the move as one byte per step of a direction-aware
   loop: forward where the copy cannot reach its own source ahead of itself, backward
   (the far end first) where it can. */

/* shift up inside the buffer: memmove(buf + shift, buf, count). The ranges overlap
   wherever shift < count and the move walks backward or the bytes it writes are the
   bytes it is about to read. */
uint64_t move_up(uint8_t *buf, uint64_t n, uint64_t shift) {
    uint64_t count = (n & 7) + 1; /* 1..8 bytes, inside the 16-byte test buffer */
    uint64_t from = shift & 7;    /* 0..7: up to buf[15] with count 8 */
    __builtin_memmove(buf + from, buf, count);
    return (uint64_t)buf[0] + (uint64_t)buf[15];
}

/* shift down inside the buffer: memmove(buf, buf + shift, count). The copy walks
   forward here (its destination is behind its source); shift zero is the identity. */
uint64_t move_down(uint8_t *buf, uint64_t n, uint64_t shift) {
    uint64_t count = (n & 7) + 1;
    uint64_t from = shift & 7;
    __builtin_memmove(buf, buf + from, count);
    return (uint64_t)buf[0] + (uint64_t)buf[15];
}

/* two halves of the buffer, no overlap: memmove(buf + 8, buf + shift, count), the plain
   forward walk of a copy that cannot overwrite its own source */
uint64_t move_pair(uint8_t *buf, uint64_t n, uint64_t shift) {
    uint64_t count = (n & 7) + 1;
    uint64_t from = shift & 7;
    __builtin_memmove(buf + 8, buf + from, count);
    return (uint64_t)buf[0] + (uint64_t)buf[15];
}
