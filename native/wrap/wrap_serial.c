/*
 * The serial channel of a firmware seat: COM1 of x86, one word at a time. The BIOS and
 * UEFI seats report the same line on it, so a machine under an emulator answers the
 * same whatever firmware started it.
 */
#include "wrap.h"

void rxf_wrap_serial_init(void) {
    rxf_wrap_outb(RXF_WRAP_COM1 + 3, 0x80); /* DLAB on */
    rxf_wrap_outb(RXF_WRAP_COM1, 3);        /* divisor 3: 115200 baud */
    rxf_wrap_outb(RXF_WRAP_COM1 + 1, 0);
    rxf_wrap_outb(RXF_WRAP_COM1 + 3, 0x03); /* 8 bits, no parity, one stop */
    rxf_wrap_outb(RXF_WRAP_COM1 + 2, 0xC7); /* FIFO on and cleared */
}

void rxf_wrap_serial_write(const char *text, rxf_u64 bytes) {
    for (rxf_u64 i = 0; i < bytes; i++) {
        while (!(rxf_wrap_inb(RXF_WRAP_COM1 + 5) & 0x20))
            ;
        rxf_wrap_outb(RXF_WRAP_COM1, (rxf_u8)text[i]);
    }
}
