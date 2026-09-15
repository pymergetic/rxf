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
