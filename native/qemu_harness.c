#include "basics.h"

#define DECLARE_BINARY(NAME, T) extern rxf_u32 blob_##NAME(T, T, T *);
#define DECLARE_SHIFT(NAME, T) extern rxf_u32 blob_##NAME(T, rxf_u32, T *);
DECLARE_BINARY(checked_add_int32_t, rxf_i32)
DECLARE_BINARY(divide_int32_t, rxf_i32)
DECLARE_BINARY(remainder_int32_t, rxf_i32)
DECLARE_SHIFT(shift_left_int32_t, rxf_i32)
DECLARE_SHIFT(shift_right_int32_t, rxf_i32)
extern rxf_u32 blob_checked_add_float(rxf_f32, rxf_f32, rxf_f32 *);
extern rxf_u32 blob_minimum_float(rxf_f32, rxf_f32, rxf_f32 *);
extern rxf_u32 blob_convert_double_to_int32_t(rxf_f64, rxf_i32 *);
extern rxf_u32 blob_convert_uint64_t_to_double(rxf_u64, rxf_f64 *);

static void uart_puts(const char *text) {
    volatile rxf_u32 *uart = (volatile rxf_u32 *)0x09000000;
    while (*text) *uart = (rxf_u32)*text++;
}
static void finish(rxf_u32 success) {
    static rxf_u64 block[2];
    block[0] = success ? 0x20026 : 0x20023;
    block[1] = 0;
    register rxf_u64 x0 __asm__("x0") = 0x20;
    register void *x1 __asm__("x1") = block;
    __asm__ volatile("hlt #0xf000" : : "r"(x0), "r"(x1) : "memory");
    for (;;) __asm__ volatile("wfe");
}
#define REQUIRE(C) do { if (!(C)) { uart_puts("RXF-QEMU-FAIL\n"); finish(0); } } while (0)
int rxf_qemu_main(void) {
    rxf_i32 i32 = 0x12345678;
    REQUIRE(blob_checked_add_int32_t(2147483647, 1, &i32) == RXF_REFUSE_OVERFLOW);
    REQUIRE(i32 == 0x12345678);
    REQUIRE(blob_checked_add_int32_t(20, 22, &i32) == RXF_OK && i32 == 42);
    i32 = 77; REQUIRE(blob_divide_int32_t((-2147483647 - 1), -1, &i32) == RXF_REFUSE_OVERFLOW && i32 == 77);
    i32 = 77; REQUIRE(blob_remainder_int32_t((-2147483647 - 1), -1, &i32) == RXF_REFUSE_OVERFLOW && i32 == 77);
    REQUIRE(blob_divide_int32_t(-7, 3, &i32) == RXF_OK && i32 == -2);
    REQUIRE(blob_remainder_int32_t(-7, 3, &i32) == RXF_OK && i32 == -1);
    REQUIRE(blob_shift_left_int32_t(-1, 1, &i32) == RXF_OK && i32 == -2);
    REQUIRE(blob_shift_right_int32_t(-4, 1, &i32) == RXF_OK && i32 == -2);
    i32 = 77; REQUIRE(blob_shift_right_int32_t(-4, 32, &i32) == RXF_REFUSE_SHIFT_COUNT && i32 == 77);
    rxf_f32 f32 = 99.0f;
    REQUIRE(blob_checked_add_float(1.25f, 2.5f, &f32) == RXF_OK && f32 == 3.75f);
    union { rxf_u32 u; rxf_f32 f; } infinity = {0x7f800000U};
    f32 = 99.0f; REQUIRE(blob_checked_add_float(infinity.f, 1.0f, &f32) == RXF_REFUSE_NON_FINITE && f32 == 99.0f);
    union { rxf_u32 u; rxf_f32 f; } negative_zero = {0x80000000U}, positive_zero = {0};
    REQUIRE(blob_minimum_float(negative_zero.f, positive_zero.f, &f32) == RXF_OK);
    union { rxf_u32 u; rxf_f32 f; } minimum = {0}; minimum.f = f32; REQUIRE(minimum.u == 0x80000000U);
    i32 = 77; REQUIRE(blob_convert_double_to_int32_t(42.0, &i32) == RXF_OK && i32 == 42);
    i32 = 77; REQUIRE(blob_convert_double_to_int32_t(42.5, &i32) == RXF_REFUSE_INEXACT && i32 == 77);
    i32 = 77; REQUIRE(blob_convert_double_to_int32_t(2147483648.0, &i32) == RXF_REFUSE_OUT_OF_RANGE && i32 == 77);
    rxf_f64 f64 = 7.0; REQUIRE(blob_convert_uint64_t_to_double(9007199254740992ULL, &f64) == RXF_OK && f64 == 9007199254740992.0);
    f64 = 7.0; REQUIRE(blob_convert_uint64_t_to_double(9007199254740993ULL, &f64) == RXF_REFUSE_INEXACT && f64 == 7.0);
    uart_puts("RXF-QEMU-PASS\n"); finish(1); return 0;
}
