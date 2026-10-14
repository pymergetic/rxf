/* __builtin_*_overflow lowers to the llvm.*.with.overflow intrinsics plus extractvalue.
   optnone keeps the intrinsic call at -O1 (the optimizer would expand it to add +
   compare, which the arithmetic subset covers anyway); the branchless form keeps the
   function one block, so the local slots the optnone body spills stay in it. */

#include <stdint.h>

__attribute__((noinline, optnone)) uint64_t add_ovf_u64(uint64_t a, uint64_t b) {
    uint64_t out;
    int c = __builtin_add_overflow(a, b, &out);
    return out ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t sub_ovf_u64(uint64_t a, uint64_t b) {
    uint64_t out;
    int c = __builtin_sub_overflow(a, b, &out);
    return out ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t add_ovf_i32(int32_t a, int32_t b) {
    int32_t out;
    int c = __builtin_add_overflow(a, b, &out);
    return ((uint64_t)(uint32_t)out) ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t sub_ovf_i32(int32_t a, int32_t b) {
    int32_t out;
    int c = __builtin_sub_overflow(a, b, &out);
    return ((uint64_t)(uint32_t)out) ^ ((uint64_t)c << 63);
}

/* the signed 64-bit rows: the sign-form overflow bit of lower_with_overflow */
__attribute__((noinline, optnone)) uint64_t add_ovf_i64(int64_t a, int64_t b) {
    int64_t out;
    int c = __builtin_add_overflow(a, b, &out);
    return ((uint64_t)out) ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t sub_ovf_i64(int64_t a, int64_t b) {
    int64_t out;
    int c = __builtin_sub_overflow(a, b, &out);
    return ((uint64_t)out) ^ ((uint64_t)c << 63);
}

/* the multiply forms of the intrinsic: one wrapping leaf and the overflow bit the
   identity gives (tools/native/rximport.py's lower_with_overflow). The differential
   runs every pair -- overflowing ones included -- against __builtin_mul_overflow */
__attribute__((noinline, optnone)) uint64_t mul_ovf_u64(uint64_t a, uint64_t b) {
    uint64_t out;
    int c = __builtin_mul_overflow(a, b, &out);
    return out ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t mul_ovf_i64(int64_t a, int64_t b) {
    int64_t out;
    int c = __builtin_mul_overflow(a, b, &out);
    return ((uint64_t)out) ^ ((uint64_t)c << 63);
}

__attribute__((noinline, optnone)) uint64_t mul_ovf_i32(int32_t a, int32_t b) {
    int32_t out;
    int c = __builtin_mul_overflow(a, b, &out);
    return ((uint64_t)(uint32_t)out) ^ ((uint64_t)c << 63);
}
