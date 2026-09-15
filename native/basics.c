#include "basics.h"

#define RXF_FUNCTION __attribute__((noinline))

static rxf_u32 rxf_f32_bits(rxf_f32 value) {
    rxf_u32 bits;
    __builtin_memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static rxf_u64 rxf_f64_bits(rxf_f64 value) {
    rxf_u64 bits;
    __builtin_memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static rxf_u32 rxf_f32_finite(rxf_f32 value) {
    return (rxf_f32_bits(value) & 0x7f800000U) != 0x7f800000U;
}

static rxf_u32 rxf_f64_finite(rxf_f64 value) {
    return (rxf_f64_bits(value) & 0x7ff0000000000000ULL) !=
           0x7ff0000000000000ULL;
}

#define RXF_DEFINE_COMPARE(T, NAME)                                           \
    RXF_FUNCTION rxf_u32 rxf_equal_##NAME(T a, T b, rxf_u8 *out) {           \
        *out = (rxf_u8)(a == b);                                              \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_less_##NAME(T a, T b, rxf_u8 *out) {            \
        *out = (rxf_u8)(a < b);                                               \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_MINMAX(T, NAME)                                            \
    RXF_FUNCTION rxf_u32 rxf_minimum_##NAME(T a, T b, T *out) {              \
        *out = a < b ? a : b;                                                 \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_maximum_##NAME(T a, T b, T *out) {              \
        *out = a > b ? a : b;                                                 \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_UNSIGNED_ARITH(T, NAME, MAXIMUM)                           \
    RXF_FUNCTION rxf_u32 rxf_checked_add_##NAME(T a, T b, T *out) {          \
        T value;                                                              \
        if (__builtin_add_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_subtract_##NAME(T a, T b, T *out) {     \
        T value;                                                              \
        if (__builtin_sub_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_multiply_##NAME(T a, T b, T *out) {     \
        T value;                                                              \
        if (__builtin_mul_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_add_##NAME(T a, T b, T *out) {         \
        *out = (T)(a + b);                                                    \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_subtract_##NAME(T a, T b, T *out) {    \
        *out = (T)(a - b);                                                    \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_multiply_##NAME(T a, T b, T *out) {    \
        *out = (T)(a * b);                                                    \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_add_##NAME(T a, T b, T *out) {       \
        T value;                                                              \
        *out = __builtin_add_overflow(a, b, &value) ? (T)(MAXIMUM) : value;  \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_subtract_##NAME(T a, T b, T *out) {  \
        T value;                                                              \
        *out = __builtin_sub_overflow(a, b, &value) ? (T)0 : value;          \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_multiply_##NAME(T a, T b, T *out) {  \
        T value;                                                              \
        *out = __builtin_mul_overflow(a, b, &value) ? (T)(MAXIMUM) : value;  \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_SIGNED_ARITH(T, U, NAME, MINIMUM, MAXIMUM)                 \
    static void rxf_store_bits_##NAME(U bits, T *out) {                       \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_add_##NAME(T a, T b, T *out) {          \
        T value;                                                              \
        if (__builtin_add_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_subtract_##NAME(T a, T b, T *out) {     \
        T value;                                                              \
        if (__builtin_sub_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_multiply_##NAME(T a, T b, T *out) {     \
        T value;                                                              \
        if (__builtin_mul_overflow(a, b, &value)) {                           \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_add_##NAME(T a, T b, T *out) {         \
        rxf_store_bits_##NAME((U)a + (U)b, out);                              \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_subtract_##NAME(T a, T b, T *out) {    \
        rxf_store_bits_##NAME((U)a - (U)b, out);                              \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_wrapping_multiply_##NAME(T a, T b, T *out) {    \
        rxf_store_bits_##NAME((U)a * (U)b, out);                              \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_add_##NAME(T a, T b, T *out) {       \
        T value;                                                              \
        if (__builtin_add_overflow(a, b, &value)) {                           \
            value = b < 0 ? (T)(MINIMUM) : (T)(MAXIMUM);                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_subtract_##NAME(T a, T b, T *out) {  \
        T value;                                                              \
        if (__builtin_sub_overflow(a, b, &value)) {                           \
            value = b < 0 ? (T)(MAXIMUM) : (T)(MINIMUM);                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_saturating_multiply_##NAME(T a, T b, T *out) {  \
        T value;                                                              \
        if (__builtin_mul_overflow(a, b, &value)) {                           \
            value = ((a < 0) != (b < 0)) ? (T)(MINIMUM) : (T)(MAXIMUM);      \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_INTEGER_COMMON(T, U, NAME, WIDTH, MINIMUM, SIGNED)         \
    RXF_DEFINE_COMPARE(T, NAME)                                               \
    RXF_DEFINE_MINMAX(T, NAME)                                                \
    RXF_FUNCTION rxf_u32 rxf_divide_##NAME(T a, T b, T *out) {               \
        if (b == 0) {                                                         \
            return RXF_REFUSE_DIVISION_BY_ZERO;                               \
        }                                                                     \
        if ((SIGNED) && a == (T)(MINIMUM) && b == (T)-1) {                   \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = (T)(a / b);                                                    \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_remainder_##NAME(T a, T b, T *out) {            \
        if (b == 0) {                                                         \
            return RXF_REFUSE_DIVISION_BY_ZERO;                               \
        }                                                                     \
        if ((SIGNED) && a == (T)(MINIMUM) && b == (T)-1) {                   \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = (T)(a % b);                                                    \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_bit_not_##NAME(T a, T *out) {                   \
        U bits = (U)~(U)a;                                                    \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_bit_and_##NAME(T a, T b, T *out) {              \
        U bits = (U)a & (U)b;                                                 \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_bit_or_##NAME(T a, T b, T *out) {               \
        U bits = (U)a | (U)b;                                                 \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_bit_xor_##NAME(T a, T b, T *out) {              \
        U bits = (U)a ^ (U)b;                                                 \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_shift_left_##NAME(T a, rxf_u32 count, T *out) { \
        if (count >= (WIDTH)) {                                               \
            return RXF_REFUSE_SHIFT_COUNT;                                    \
        }                                                                     \
        U bits = (U)a << count;                                               \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_shift_right_##NAME(T a, rxf_u32 count, T *out) {\
        if (count >= (WIDTH)) {                                               \
            return RXF_REFUSE_SHIFT_COUNT;                                    \
        }                                                                     \
        U bits = (U)a >> count;                                               \
        if ((SIGNED) && a < 0 && count != 0) {                                \
            bits |= (U)(~(U)0 << ((WIDTH)-count));                            \
        }                                                                     \
        __builtin_memcpy(out, &bits, sizeof(bits));                           \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_SIGNED(T, U, NAME, WIDTH, MINIMUM, MAXIMUM)                \
    RXF_DEFINE_SIGNED_ARITH(T, U, NAME, MINIMUM, MAXIMUM)                     \
    RXF_DEFINE_INTEGER_COMMON(T, U, NAME, WIDTH, MINIMUM, 1)                  \
    RXF_FUNCTION rxf_u32 rxf_checked_negate_##NAME(T a, T *out) {            \
        if (a == (T)(MINIMUM)) {                                              \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = (T)-a;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_absolute_##NAME(T a, T *out) {          \
        if (a == (T)(MINIMUM)) {                                              \
            return RXF_REFUSE_OVERFLOW;                                       \
        }                                                                     \
        *out = a < 0 ? (T)-a : a;                                             \
        return RXF_OK;                                                        \
    }

#define RXF_DEFINE_UNSIGNED(T, NAME, WIDTH, MAXIMUM)                          \
    RXF_DEFINE_UNSIGNED_ARITH(T, NAME, MAXIMUM)                               \
    RXF_DEFINE_INTEGER_COMMON(T, T, NAME, WIDTH, 0, 0)

RXF_DEFINE_UNSIGNED(rxf_u8, uint8_t, 8, 255U)
RXF_DEFINE_UNSIGNED(rxf_u16, uint16_t, 16, 65535U)
RXF_DEFINE_UNSIGNED(rxf_u32, uint32_t, 32, 4294967295U)
RXF_DEFINE_UNSIGNED(rxf_u64, uint64_t, 64, 18446744073709551615ULL)
RXF_DEFINE_SIGNED(rxf_i8, rxf_u8, int8_t, 8, -128, 127)
RXF_DEFINE_SIGNED(rxf_i16, rxf_u16, int16_t, 16, -32768, 32767)
RXF_DEFINE_SIGNED(rxf_i32, rxf_u32, int32_t, 32, (-2147483647 - 1), 2147483647)
RXF_DEFINE_SIGNED(rxf_i64, rxf_u64, int64_t, 64,
                  (-9223372036854775807LL - 1), 9223372036854775807LL)

#define RXF_DEFINE_FLOAT(T, U, NAME, FINITE, BITS, SIGN_MASK)                 \
    RXF_FUNCTION rxf_u32 rxf_equal_##NAME(T a, T b, rxf_u8 *out) {           \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = (rxf_u8)(a == b);                                              \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_less_##NAME(T a, T b, rxf_u8 *out) {            \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = (rxf_u8)(a < b);                                               \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_minimum_##NAME(T a, T b, T *out) {              \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        if (a == (T)0 && b == (T)0) {                                        \
            U bits = BITS(a) | BITS(b);                                       \
            __builtin_memcpy(out, &bits, sizeof(bits));                       \
        } else {                                                              \
            *out = a < b ? a : b;                                             \
        }                                                                     \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_maximum_##NAME(T a, T b, T *out) {              \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        if (a == (T)0 && b == (T)0) {                                        \
            U bits = BITS(a) & BITS(b) & (U)(SIGN_MASK);                     \
            __builtin_memcpy(out, &bits, sizeof(bits));                       \
        } else {                                                              \
            *out = a > b ? a : b;                                             \
        }                                                                     \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_add_##NAME(T a, T b, T *out) {          \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        T value = a + b;                                                      \
        if (!FINITE(value)) {                                                 \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_subtract_##NAME(T a, T b, T *out) {     \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        T value = a - b;                                                      \
        if (!FINITE(value)) {                                                 \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_multiply_##NAME(T a, T b, T *out) {     \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        T value = a * b;                                                      \
        if (!FINITE(value)) {                                                 \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_divide_##NAME(T a, T b, T *out) {               \
        if (!(FINITE(a) && FINITE(b))) {                                      \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        if (b == (T)0) {                                                      \
            return RXF_REFUSE_DIVISION_BY_ZERO;                               \
        }                                                                     \
        T value = a / b;                                                      \
        if (!FINITE(value)) {                                                 \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        *out = value;                                                         \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_negate_##NAME(T a, T *out) {            \
        if (!FINITE(a)) {                                                     \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        volatile U bits = BITS(a);                                           \
        bits ^= (U)(SIGN_MASK);                                              \
        U result = bits;                                                     \
        __builtin_memcpy(out, &result, sizeof(result));                           \
        return RXF_OK;                                                        \
    }                                                                         \
    RXF_FUNCTION rxf_u32 rxf_checked_absolute_##NAME(T a, T *out) {          \
        if (!FINITE(a)) {                                                     \
            return RXF_REFUSE_NON_FINITE;                                     \
        }                                                                     \
        volatile U bits = BITS(a);                                           \
        bits &= (U)~(U)(SIGN_MASK);                                          \
        U result = bits;                                                     \
        __builtin_memcpy(out, &result, sizeof(result));                           \
        return RXF_OK;                                                        \
    }

RXF_DEFINE_FLOAT(rxf_f32, rxf_u32, float, rxf_f32_finite, rxf_f32_bits,
                 0x80000000U)
RXF_DEFINE_FLOAT(rxf_f64, rxf_u64, double, rxf_f64_finite, rxf_f64_bits,
                 0x8000000000000000ULL)
