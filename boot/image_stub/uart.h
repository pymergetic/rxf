/* The Uart capability's face for the boot world: one byte out, one capability
 * slot in. The slot is the contract (native/graph.h: id, generation word, device
 * base); every seat fills the same one function with its own device model, the
 * way native/uart.c fills one pump per machine. */
#ifndef RXF_BOOT_UART_H
#define RXF_BOOT_UART_H

#include "graph.h" /* the capability model's device words live here */
#include "stdlib_runtime.h"

/* One byte toward the line behind the capability. The fill must interpret the
 * capability's generation word exactly as native/uart.c's device_of does; a
 * capability that names a device this machine cannot have is refused (nothing
 * is emitted) and the smoke fails loudly rather than printing on a dark port. */
void rxf_boot_uart_putc(const rxf_native_slot *capability, rxf_u8 byte);

#endif
