#ifndef RXF_NATIVE_BASICS_H
#define RXF_NATIVE_BASICS_H

typedef unsigned char rxf_u8;
typedef signed char rxf_i8;
typedef unsigned short rxf_u16;
typedef signed short rxf_i16;
typedef unsigned int rxf_u32;
typedef signed int rxf_i32;
typedef unsigned long long rxf_u64;
typedef signed long long rxf_i64;
typedef float rxf_f32;
typedef double rxf_f64;

_Static_assert(sizeof(rxf_u8) == 1, "u8");
_Static_assert(sizeof(rxf_u16) == 2, "u16");
_Static_assert(sizeof(rxf_u32) == 4, "u32");
_Static_assert(sizeof(rxf_u64) == 8, "u64");
_Static_assert(sizeof(rxf_f32) == 4, "f32");
_Static_assert(sizeof(rxf_f64) == 8, "f64");

/*
 * Multiplication with overflow detection. A 64-bit multiply that overflows
 * calls __multi3 in the wasm32 runtime library; a snippet cannot call out, so
 * wasm32 gets the same result from 32-bit halves. Every other target keeps the
 * compiler builtin.
 */
#if defined(__wasm__)
static inline int rxf_mul_overflow_u64(rxf_u64 a, rxf_u64 b, rxf_u64 *out) {
    rxf_u64 ah = a >> 32, al = (rxf_u32)a, bh = b >> 32, bl = (rxf_u32)b;
    rxf_u64 cross = ah * bl + al * bh;
    rxf_u64 low = al * bl;
    rxf_u64 sum = low + (cross << 32);
    *out = a * b;
    return (ah != 0 && bh != 0) || (cross >> 32) != 0 || sum < low;
}

static inline int rxf_mul_overflow_i64(rxf_i64 a, rxf_i64 b, rxf_i64 *out) {
    rxf_u64 ua = a < 0 ? 0 - (rxf_u64)a : (rxf_u64)a;
    rxf_u64 ub = b < 0 ? 0 - (rxf_u64)b : (rxf_u64)b;
    rxf_u64 magnitude;
    int overflow = rxf_mul_overflow_u64(ua, ub, &magnitude);
    int negative = (a < 0) != (b < 0);
    *out = (rxf_i64)((rxf_u64)a * (rxf_u64)b);
    return overflow || magnitude > (negative ? (1ULL << 63) : (1ULL << 63) - 1);
}

#define RXF_MUL_OVERFLOW(a, b, out)                                                                \
    (sizeof(*(out)) == 8                                                                           \
         ? ((__typeof__(*(out)))-1 < 0                                                             \
                ? rxf_mul_overflow_i64((rxf_i64)(a), (rxf_i64)(b), (rxf_i64 *)(out))               \
                : rxf_mul_overflow_u64((rxf_u64)(a), (rxf_u64)(b), (rxf_u64 *)(out)))              \
         : __builtin_mul_overflow(a, b, out))
#else
#define RXF_MUL_OVERFLOW(a, b, out) __builtin_mul_overflow(a, b, out)
#endif

enum rxf_status {
    RXF_OK = 0,
    RXF_REFUSE_OVERFLOW = 1,
    RXF_REFUSE_DIVISION_BY_ZERO = 2,
    RXF_REFUSE_SHIFT_COUNT = 3,
    RXF_REFUSE_NON_FINITE = 4,
    RXF_REFUSE_INEXACT = 5,
    RXF_REFUSE_OUT_OF_RANGE = 6
};

#endif
