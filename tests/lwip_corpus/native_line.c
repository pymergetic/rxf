/* native_line.c -- the native TEST REFERENCE's SLIP line (never a seat fill).
 *
 * The differential compiles the corpus natively to compare answers (docs/23 3). The card's
 * slipif calls the line provider of sio.h; the reference needs symbols to link, and nothing
 * of the differential reaches them (the echo's payload path never touches the line). A seat's
 * line is a fill of its own (docs/25 13) and does not come from this file.
 */
#include <stddef.h>
#include <stdint.h>

#include "lwip/sio.h"

sio_fd_t sio_open(uint8_t devnum) {
    (void)devnum;
    return NULL;
}

void sio_send(uint8_t c, sio_fd_t fd) {
    (void)c;
    (void)fd;
}

uint32_t sio_tryread(sio_fd_t fd, uint8_t *data, uint32_t len) {
    (void)fd;
    (void)data;
    (void)len;
    return 0;
}
