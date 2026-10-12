/*
 * The UART as a provider of the Channel (docs/19): rxf_uart_pump(context, area) moves bytes between
 * a serial device and the two rings of the live area. It does not serve requests; the live pump
 * does. A loop that wants a console runs: uart, live, uart.
 *
 *   x86_64   the 16550 at the I/O port base of the Uart capability (COM1 is 0x3F8); or, as the
 *            generation word of the capability says (native/graph.h), a 16550 in memory with its
 *            registers a shift apart and read as bytes or words, or a PL011 in memory
 *   aarch64  the PL011 at the memory address base of the Uart capability; or a 16550 in memory
 *            (the mini UART, the 8250 of many boards), likewise spaced and wide
 *   wasm32   two FIFOs in linear memory at the address of the Uart capability (native/graph.h); the
 *            host is the line: it puts bytes in the first and takes them out of the second
 *
 * Nothing waits: a byte that the device cannot take or give now is left for the next call. The
 * words of the area are those of core/live/live.h.
 */
#include "graph.h"

#define RXF_FUNCTION __attribute__((noinline))
#define RXF_INLINE static __attribute__((always_inline)) inline
#define RXF_AREA_RX_BASE 88U
#define RXF_AREA_RX_CAPACITY 96U
#define RXF_AREA_TX_BASE 104U
#define RXF_AREA_TX_CAPACITY 112U
#define RXF_AREA_RX_HEAD 120U
#define RXF_AREA_RX_TAIL 124U
#define RXF_AREA_TX_HEAD 128U
#define RXF_AREA_TX_TAIL 132U
#define RXF_UART_BUDGET 65536U /* bytes a call moves in each direction at most */

typedef unsigned long rxf_uptr;

RXF_INLINE volatile rxf_u32 *word(rxf_u8 *area, rxf_u64 offset) {
    return (volatile rxf_u32 *)(area + offset);
}

RXF_INLINE rxf_u64 quad(const rxf_u8 *area, rxf_u64 offset) {
    return *(const rxf_u64 *)(area + offset);
}

/* A device: where it is, what it is, and how its registers lie (the generation word of the
 * capability). */
typedef struct {
    rxf_u64 base;
    rxf_u32 kind;
    rxf_u32 shift;
    rxf_u32 wide;
} rxf_uart_device;

#define RXF_NS16550_DATA 0U
#define RXF_NS16550_LINE_STATUS 5U
#define RXF_NS16550_DATA_READY 0x01U
#define RXF_NS16550_THR_EMPTY 0x20U
#define RXF_PL011_DR 0x00U
#define RXF_PL011_FR 0x18U
#define RXF_PL011_RXFE 0x10U
#define RXF_PL011_TXFF 0x20U

#if defined(__x86_64__) || defined(__aarch64__)

/* A register in memory: n-th of a 16550 (spaced by the shift), or a PL011 offset (shift 0). */
RXF_INLINE rxf_u32 mmio_read(const rxf_uart_device *d, rxf_u32 reg) {
    const rxf_uptr at = (rxf_uptr)(d->base + ((rxf_u64)reg << d->shift));
    return d->wide ? *(volatile rxf_u32 *)at : *(volatile rxf_u8 *)at;
}

RXF_INLINE void mmio_write(const rxf_uart_device *d, rxf_u32 reg, rxf_u8 value) {
    const rxf_uptr at = (rxf_uptr)(d->base + ((rxf_u64)reg << d->shift));
    if (d->wide)
        *(volatile rxf_u32 *)at = value;
    else
        *(volatile rxf_u8 *)at = value;
}

#if defined(__x86_64__)
RXF_INLINE rxf_u8 port_read(const rxf_uart_device *d, rxf_u32 reg) {
    rxf_u8 value;
    __asm__ volatile("inb %1, %0" : "=a"(value) : "Nd"((rxf_u16)(d->base + reg)));
    return value;
}

RXF_INLINE void port_write(const rxf_uart_device *d, rxf_u32 reg, rxf_u8 value) {
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"((rxf_u16)(d->base + reg)));
}
#endif

/* The kind of the machine, made explicit; 0 when this machine cannot have it. */
RXF_INLINE rxf_u32 device_kind(rxf_u64 generation) {
    const rxf_u32 kind = (rxf_u32)(generation & 0xFFU);
    if (kind == 0 || kind == RXF_UART_DEVICE_MACHINE)
#if defined(__x86_64__)
        return RXF_UART_DEVICE_NS16550_PORT;
#else
        return RXF_UART_DEVICE_PL011_MMIO;
#endif
    if (kind == RXF_UART_DEVICE_NS16550_MMIO || kind == RXF_UART_DEVICE_PL011_MMIO)
        return kind;
#if defined(__x86_64__)
    if (kind == RXF_UART_DEVICE_NS16550_PORT)
        return kind;
#endif
    return 0;
}

RXF_INLINE int device_has_byte(const rxf_uart_device *d) {
    switch (d->kind) {
    case RXF_UART_DEVICE_PL011_MMIO:
        return (mmio_read(d, RXF_PL011_FR) & RXF_PL011_RXFE) == 0;
    case RXF_UART_DEVICE_NS16550_MMIO:
        return (mmio_read(d, RXF_NS16550_LINE_STATUS) & RXF_NS16550_DATA_READY) != 0;
#if defined(__x86_64__)
    default:
        return (port_read(d, RXF_NS16550_LINE_STATUS) & RXF_NS16550_DATA_READY) != 0;
#else
    default:
        return 0;
#endif
    }
}

RXF_INLINE int device_can_send(const rxf_uart_device *d) {
    switch (d->kind) {
    case RXF_UART_DEVICE_PL011_MMIO:
        return (mmio_read(d, RXF_PL011_FR) & RXF_PL011_TXFF) == 0;
    case RXF_UART_DEVICE_NS16550_MMIO:
        return (mmio_read(d, RXF_NS16550_LINE_STATUS) & RXF_NS16550_THR_EMPTY) != 0;
#if defined(__x86_64__)
    default:
        return (port_read(d, RXF_NS16550_LINE_STATUS) & RXF_NS16550_THR_EMPTY) != 0;
#else
    default:
        return 0;
#endif
    }
}

RXF_INLINE rxf_u8 device_get(const rxf_uart_device *d) {
    switch (d->kind) {
    case RXF_UART_DEVICE_PL011_MMIO:
        return (rxf_u8)mmio_read(d, RXF_PL011_DR);
    case RXF_UART_DEVICE_NS16550_MMIO:
        return (rxf_u8)mmio_read(d, RXF_NS16550_DATA);
#if defined(__x86_64__)
    default:
        return port_read(d, RXF_NS16550_DATA);
#else
    default:
        return 0;
#endif
    }
}

RXF_INLINE void device_put(const rxf_uart_device *d, rxf_u8 value) {
    switch (d->kind) {
    case RXF_UART_DEVICE_PL011_MMIO:
        mmio_write(d, RXF_PL011_DR, value);
        return;
    case RXF_UART_DEVICE_NS16550_MMIO:
        mmio_write(d, RXF_NS16550_DATA, value);
        return;
#if defined(__x86_64__)
    default:
        port_write(d, RXF_NS16550_DATA, value);
        return;
#else
    default:
        return;
#endif
    }
}

/* The device a capability describes; 0 when this machine has no such device. */
RXF_INLINE rxf_u32 device_of(const rxf_native_slot *capability, rxf_uart_device *d) {
    d->base = capability->address;
    d->kind = device_kind(capability->generation);
    d->shift = (rxf_u32)((capability->generation >> RXF_UART_SHIFT_AT) & 0xFU);
    d->wide = (capability->generation & RXF_UART_WIDE) != 0;
    return d->kind != 0;
}

#elif defined(__wasm__)

RXF_INLINE volatile rxf_u32 *fifo_word(rxf_u64 base, rxf_u32 offset) {
    return (volatile rxf_u32 *)(rxf_uptr)(base + offset);
}

RXF_INLINE int device_has_byte(const rxf_uart_device *d) {
    return *fifo_word(d->base, 0) != *fifo_word(d->base, 4);
}

RXF_INLINE int device_can_send(const rxf_uart_device *d) {
    return *fifo_word(d->base, 8) - *fifo_word(d->base, 12) < RXF_UART_FIFO_BYTES;
}

RXF_INLINE rxf_u8 device_get(const rxf_uart_device *d) {
    const rxf_u32 tail = *fifo_word(d->base, 4);
    const rxf_u8 value =
        *(volatile rxf_u8 *)(rxf_uptr)(d->base + RXF_UART_FIFO_RX + tail % RXF_UART_FIFO_BYTES);
    *fifo_word(d->base, 4) = tail + 1;
    return value;
}

RXF_INLINE void device_put(const rxf_uart_device *d, rxf_u8 value) {
    const rxf_u32 head = *fifo_word(d->base, 8);
    *(volatile rxf_u8 *)(rxf_uptr)(d->base + RXF_UART_FIFO_TX + head % RXF_UART_FIFO_BYTES) = value;
    *fifo_word(d->base, 8) = head + 1;
}

RXF_INLINE rxf_u32 device_of(const rxf_native_slot *capability, rxf_uart_device *d) {
    d->base = capability->address;
    d->kind = 0;
    d->shift = 0;
    d->wide = 0;
    return 1;
}

#endif

/* The area and the base of the device in the capability table of the context. */
RXF_INLINE rxf_u32 find(const rxf_runtime_context *c, rxf_u8 **area, rxf_uart_device *device) {
    const rxf_native_slot *table = (const rxf_native_slot *)(rxf_uptr)c->capabilities;
    rxf_u32 found = *area ? 1 : 0; /* the caller named the area */
    for (rxf_u64 i = 0; i < c->capability_count; i++) {
        if (table[i].id == RXF_CAPABILITY_CHANNEL && !*area) {
            *area = (rxf_u8 *)(rxf_uptr)table[i].address;
            found |= 1;
        }
        if (table[i].id == RXF_CAPABILITY_UART) {
            if (device_of(&table[i], device))
                found |= 2;
        }
    }
    return found == 3;
}

RXF_FUNCTION rxf_u32 rxf_uart_pump(rxf_runtime_context *c, void *area_in) {
#if defined(__x86_64__) || defined(__aarch64__) || defined(__wasm__)
    rxf_u8 *area = (rxf_u8 *)area_in;
    rxf_uart_device device = {0, 0, 0, 0};
    if (!c || !find(c, &area, &device))
        return RXF_REFUSE_UNBOUND;
    /* the device to the ring the image reads */
    rxf_u8 *rx = (rxf_u8 *)(rxf_uptr)quad(area, RXF_AREA_RX_BASE);
    const rxf_u32 rx_mask = (rxf_u32)quad(area, RXF_AREA_RX_CAPACITY) - 1;
    rxf_u32 budget = RXF_UART_BUDGET;
    while (budget && device_has_byte(&device) &&
           *word(area, RXF_AREA_RX_HEAD) - *word(area, RXF_AREA_RX_TAIL) <= rx_mask) {
        rx[*word(area, RXF_AREA_RX_HEAD) & rx_mask] = device_get(&device);
        *word(area, RXF_AREA_RX_HEAD) = *word(area, RXF_AREA_RX_HEAD) + 1;
        budget--;
    }
    /* the ring the image writes to the device */
    const rxf_u8 *tx = (const rxf_u8 *)(rxf_uptr)quad(area, RXF_AREA_TX_BASE);
    const rxf_u32 tx_mask = (rxf_u32)quad(area, RXF_AREA_TX_CAPACITY) - 1;
    budget = RXF_UART_BUDGET;
    while (budget && *word(area, RXF_AREA_TX_TAIL) != *word(area, RXF_AREA_TX_HEAD) &&
           device_can_send(&device)) {
        device_put(&device, tx[*word(area, RXF_AREA_TX_TAIL) & tx_mask]);
        *word(area, RXF_AREA_TX_TAIL) = *word(area, RXF_AREA_TX_TAIL) + 1;
        budget--;
    }
    return RXF_OK;
#else
    (void)c;
    (void)area_in;
    return RXF_REFUSE_UNBOUND;
#endif
}
