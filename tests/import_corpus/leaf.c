/* The C -> RXF import corpus: leaf-shaped calls -- the ABI of native/basics.c: a
 * status, the arguments, and a trailing pointer to the out slot. The leaves are
 * declared exactly as native/basics.c defines them (its own header types them), and
 * every out slot starts zero, so a leaf that refuses (shift counts of 64 and up do)
 * leaves the zero behind on both sides: the frame of a run is zero the same way.
 */

#include "../../native/basics.h"

extern rxf_u32 rxf_bit_and_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_wrapping_add_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_shift_left_uint64_t(rxf_u64 a, rxf_u32 count, rxf_u64 *out);

rxf_u64 leaf_and(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_bit_and_uint64_t(a, b, &out);
    return out;
}

rxf_u64 leaf_add(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_wrapping_add_uint64_t(a, b, &out);
    return out;
}

rxf_u64 leaf_shift(rxf_u64 a, rxf_u64 count) {
    rxf_u64 out = 0;
    rxf_shift_left_uint64_t(a, (rxf_u32)count, &out);
    return out;
}

rxf_u64 leaf_mix(rxf_u64 a, rxf_u64 b) {
    rxf_u64 x = 0, y = 0;
    rxf_bit_and_uint64_t(a, b, &x);
    rxf_wrapping_add_uint64_t(x, a, &y);
    return y;
}

/* the integer leaves of the flat profile (native/graph.h ids 39-45) called straight from
 * the corpus: every out slot starts zero, so a leaf that refuses leaves the zero behind
 * on both sides -- a checked multiply of a product that leaves the type, and a division
 * by zero or INT64_MIN / -1, are zeros to compare here, not wrong numbers */

extern rxf_u32 rxf_wrapping_multiply_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_checked_multiply_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_checked_multiply_int64_t(rxf_i64 a, rxf_i64 b, rxf_i64 *out);
extern rxf_u32 rxf_divide_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_remainder_uint64_t(rxf_u64 a, rxf_u64 b, rxf_u64 *out);
extern rxf_u32 rxf_divide_int64_t(rxf_i64 a, rxf_i64 b, rxf_i64 *out);
extern rxf_u32 rxf_remainder_int64_t(rxf_i64 a, rxf_i64 b, rxf_i64 *out);

rxf_u64 leaf_mul(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_wrapping_multiply_uint64_t(a, b, &out);
    return out;
}

rxf_u64 leaf_mulu(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_checked_multiply_uint64_t(a, b, &out);
    return out; /* zero where the product leaves the type: the leaf refused */
}

rxf_u64 leaf_muls(rxf_u64 a, rxf_u64 b) {
    rxf_i64 out = 0;
    rxf_checked_multiply_int64_t((rxf_i64)a, (rxf_i64)b, &out);
    return (rxf_u64)out;
}

rxf_u64 leaf_div(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_divide_uint64_t(a, b, &out);
    return out; /* zero where the divisor is zero: the leaf refused */
}

rxf_u64 leaf_rem(rxf_u64 a, rxf_u64 b) {
    rxf_u64 out = 0;
    rxf_remainder_uint64_t(a, b, &out);
    return out;
}

rxf_u64 leaf_divs(rxf_u64 a, rxf_u64 b) {
    rxf_i64 out = 0;
    rxf_divide_int64_t((rxf_i64)a, (rxf_i64)b, &out);
    return (rxf_u64)out;
}

rxf_u64 leaf_rems(rxf_u64 a, rxf_u64 b) {
    rxf_i64 out = 0;
    rxf_remainder_int64_t((rxf_i64)a, (rxf_i64)b, &out);
    return (rxf_u64)out;
}
