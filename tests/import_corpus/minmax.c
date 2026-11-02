#include <stdint.h>

/* llvm.{umin,umax,smin,smax,abs}.iN: the clamps lwIP spells LWIP_MIN / LWIP_MAX over u16_t and
   int, and abs(). C promotes a narrow operand to int before it compares, so clang only keeps
   the narrow intrinsic where the narrow type is a parameter and a result: the helpers below
   are noinline (the natively compiled side really calls them; the importer inlines the
   call), which is exactly where clang writes llvm.umin.i16 in lwIP's pbuf code. The i32
   ones are the promoted forms. tests/test_import.py pins that the compiled IR names each
   intrinsic, so the corpus cannot go vacuous.

   Each function answers one canonical 64-bit word of its type: the unsigned ones
   zero-extended, the signed ones sign-extended, so a wrong sign shows in the high bits. */

#define NOINLINE static __attribute__((noinline))

NOINLINE uint8_t umin8_of(uint8_t a, uint8_t b) {
    return a < b ? a : b;
}

NOINLINE uint16_t umin16_of(uint16_t a, uint16_t b) {
    return a < b ? a : b;
}

NOINLINE uint16_t umax16_of(uint16_t a, uint16_t b) {
    return a > b ? a : b;
}

NOINLINE int8_t smin8_of(int8_t a, int8_t b) {
    return a < b ? a : b;
}

NOINLINE int8_t smax8_of(int8_t a, int8_t b) {
    return a > b ? a : b;
}

NOINLINE int16_t smin16_of(int16_t a, int16_t b) {
    return a < b ? a : b;
}

NOINLINE int16_t smax16_of(int16_t a, int16_t b) {
    return a > b ? a : b;
}

NOINLINE int8_t abs8_of(int8_t a) {
    return a < 0 ? (int8_t)-a : a;
}

NOINLINE int16_t abs16_of(int16_t a) {
    return a < 0 ? (int16_t)-a : a;
}

uint64_t umin8(uint64_t x, uint64_t y) {
    return umin8_of((uint8_t)x, (uint8_t)y);
}

uint64_t umin16(uint64_t x, uint64_t y) {
    return umin16_of((uint16_t)x, (uint16_t)y);
}

uint64_t umax16(uint64_t x, uint64_t y) {
    return umax16_of((uint16_t)x, (uint16_t)y);
}

uint64_t smin8(uint64_t x, uint64_t y) {
    return (uint64_t)(int64_t)smin8_of((int8_t)x, (int8_t)y);
}

uint64_t smax8(uint64_t x, uint64_t y) {
    return (uint64_t)(int64_t)smax8_of((int8_t)x, (int8_t)y);
}

uint64_t smin16(uint64_t x, uint64_t y) {
    return (uint64_t)(int64_t)smin16_of((int16_t)x, (int16_t)y);
}

uint64_t smax16(uint64_t x, uint64_t y) {
    return (uint64_t)(int64_t)smax16_of((int16_t)x, (int16_t)y);
}

uint64_t abs8(uint64_t x) {
    return (uint64_t)(int64_t)abs8_of((int8_t)x);
}

uint64_t abs16(uint64_t x) {
    return (uint64_t)(int64_t)abs16_of((int16_t)x);
}

/* the promoted forms: the same ternaries over int and unsigned int are the i32 intrinsics */
uint64_t umin32(uint64_t x, uint64_t y) {
    uint32_t a = (uint32_t)x, b = (uint32_t)y;
    return a < b ? a : b;
}

uint64_t umax32(uint64_t x, uint64_t y) {
    uint32_t a = (uint32_t)x, b = (uint32_t)y;
    return a > b ? a : b;
}

uint64_t smin32(uint64_t x, uint64_t y) {
    int32_t a = (int32_t)x, b = (int32_t)y;
    return (uint64_t)(int64_t)(a < b ? a : b);
}

/* the shape of sys_timeouts_sleeptime: a signed difference clamped from below at zero */
uint64_t smax32_floor(uint64_t x, uint64_t y) {
    int32_t d = (int32_t)x - (int32_t)y;
    return (uint64_t)(int64_t)(d > 0 ? d : 0);
}

/* the shape of pbuf_alloc / pbuf_copy_partial: a u16_t chosen against a limit, then used */
uint64_t umin16_limit(uint64_t x, uint64_t y) {
    uint16_t take = umin16_of((uint16_t)x, (uint16_t)y);
    return (uint16_t)(take + 1u);
}

/* the shape of lwip_itoa: abs() of an int. INT_MIN has no defined answer in C (the intrinsic
   is the poison form), so the differential stays off that input */
uint64_t abs32(uint64_t x) {
    int32_t v = (int32_t)x;
    return (uint64_t)(int64_t)(v < 0 ? -v : v);
}

uint64_t abs64(uint64_t x) {
    int64_t v = (int64_t)x;
    return (uint64_t)(v < 0 ? -v : v);
}
