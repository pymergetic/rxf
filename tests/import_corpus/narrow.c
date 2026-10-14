#include <stdint.h>

/* The narrow widths (i8/i16/i32) of the flat profile, as clang really writes them: narrow
   compares and casts, narrow loads and stores through volatile locals, the wrapping
   arithmetic of each width, and one narrow value that lives in a register through a loop.
   The pass keeps a narrow value in the low bits of its word (canonical form); these answer
   one canonical 64-bit word. Shift counts stay inside the width of the value (a count at
   or past it is undefined in C and outside the contract). */

uint64_t add8(uint64_t x, uint64_t y) {
    volatile uint8_t a = (uint8_t)x, b = (uint8_t)y;
    volatile uint8_t s = a + b;
    return s;
}

uint64_t sub16(uint64_t x, uint64_t y) {
    volatile uint16_t a = (uint16_t)x, b = (uint16_t)y;
    volatile uint16_t s = a - b;
    return s;
}

uint64_t add32(uint64_t x, uint64_t y) {
    volatile uint32_t a = (uint32_t)x, b = (uint32_t)y;
    volatile uint32_t s = a + b;
    return s;
}

uint64_t and8(uint64_t x, uint64_t y) {
    volatile uint8_t a = (uint8_t)x, b = (uint8_t)y;
    volatile uint8_t s = a & b;
    return s;
}

uint64_t or16(uint64_t x, uint64_t y) {
    volatile uint16_t a = (uint16_t)x, b = (uint16_t)y;
    volatile uint16_t s = a | b;
    return s;
}

uint64_t xor32(uint64_t x, uint64_t y) {
    volatile uint32_t a = (uint32_t)x, b = (uint32_t)y;
    volatile uint32_t s = a ^ b;
    return s;
}

uint64_t shl8(uint64_t x, uint64_t n) {
    volatile uint8_t a = (uint8_t)x, k = (uint8_t)n;
    volatile uint8_t s = a << k;
    return s;
}

uint64_t lshr16(uint64_t x, uint64_t n) {
    volatile uint16_t a = (uint16_t)x, k = (uint16_t)n;
    volatile uint16_t s = a >> k;
    return s;
}

uint64_t ashr32(uint64_t x, uint64_t n) {
    volatile int32_t a = (int32_t)x, k = (int32_t)n;
    volatile int32_t s = a >> k;
    return (uint32_t)s;
}

uint64_t ashr8(uint64_t x, uint64_t n) {
    volatile int8_t a = (int8_t)x, k = (int8_t)n;
    volatile int8_t s = a >> k;
    return (uint8_t)s;
}

uint64_t sext8(uint64_t x) {
    volatile int8_t a = (int8_t)x;
    return (uint64_t)a;
}

uint64_t sext16(uint64_t x) {
    volatile int16_t a = (int16_t)x;
    return (uint64_t)a;
}

uint64_t sext32(uint64_t x) {
    volatile int32_t a = (int32_t)x;
    return (uint64_t)a;
}

uint64_t trunc32(uint64_t x, uint64_t y) {
    volatile uint32_t a = (uint32_t)(x + y);
    return (uint64_t)a;
}

uint64_t mix(uint64_t x, uint64_t y) {
    volatile uint8_t a = (uint8_t)x;
    volatile uint16_t b = (uint16_t)y;
    volatile uint32_t c = (uint32_t)(a + b);
    return (uint64_t)(uint16_t)(c ^ (uint32_t)a);
}

uint64_t ult8(uint64_t x, uint64_t y) {
    volatile uint8_t a = (uint8_t)x, b = (uint8_t)y;
    return a < b;
}

uint64_t slt8(uint64_t x, uint64_t y) {
    volatile int8_t a = (int8_t)x, b = (int8_t)y;
    return a < b;
}

uint64_t sge16(uint64_t x, uint64_t y) {
    volatile int16_t a = (int16_t)x, b = (int16_t)y;
    return a >= b;
}

uint64_t ugt32(uint64_t x, uint64_t y) {
    volatile uint32_t a = (uint32_t)x, b = (uint32_t)y;
    return a > b;
}

uint64_t sle32(uint64_t x, uint64_t y) {
    volatile int32_t a = (int32_t)x, b = (int32_t)y;
    return a <= b;
}

uint64_t eq8(uint64_t x, uint64_t y) {
    volatile uint8_t a = (uint8_t)x, b = (uint8_t)y;
    return a == b;
}

uint64_t lfsr8(uint64_t n, uint64_t x) {
    uint8_t acc = 0;  /* stays in a register: a narrow phi and narrow shifts */
    for (uint64_t i = 0; i < n; i++) {
        acc = (acc << 1) ^ (acc >> 7) ^ (uint8_t)x;  /* an 8-bit LFSR step */
    }
    return acc;
}

/* The funnel shifts: rotate left and right of an i8 word fold to llvm.fshl.i8 and
   llvm.fshr.i8 (the count is taken modulo the width, inside the C contract). */
uint64_t rotl8(uint64_t x, uint64_t n) {
    return __builtin_rotateleft8((uint8_t)x, (unsigned)n);
}

uint64_t rotr8(uint64_t x, uint64_t n) {
    return __builtin_rotateright8((uint8_t)x, (unsigned)n);
}
