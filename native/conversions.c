#include "basics.h"
#define RXF_FUNCTION __attribute__((noinline))
#define RXF_INLINE __attribute__((always_inline)) static inline
RXF_INLINE rxf_u32 rxf_f32_finite(rxf_f32 v) { rxf_u32 b; __builtin_memcpy(&b,&v,4); return (b&0x7f800000U)!=0x7f800000U; }
RXF_INLINE rxf_u32 rxf_f64_finite(rxf_f64 v) { rxf_u64 b; __builtin_memcpy(&b,&v,8); return (b&0x7ff0000000000000ULL)!=0x7ff0000000000000ULL; }
RXF_INLINE rxf_u64 rxf_i64_magnitude(rxf_i64 value) { rxf_u64 bits=(rxf_u64)value; return value<0?(rxf_u64)0-bits:bits; }
RXF_INLINE rxf_u32 rxf_u64_to_f32(rxf_u64 value,rxf_u32 negative,rxf_f32*out) {
    if(!value){rxf_u32 bits=negative<<31;__builtin_memcpy(out,&bits,4);return RXF_OK;}
    rxf_u32 top=63U-(rxf_u32)__builtin_clzll(value), shift=top>23U?top-23U:0U;
    if(shift && (value&(((rxf_u64)1<<shift)-1U)))return RXF_REFUSE_INEXACT;
    rxf_u64 sig=shift?value>>shift:value<<(23U-top);
    rxf_u32 bits=(negative<<31)|((top+127U)<<23)|((rxf_u32)sig&0x7fffffU);__builtin_memcpy(out,&bits,4);return RXF_OK;
}
RXF_INLINE rxf_u32 rxf_u64_to_f64(rxf_u64 value,rxf_u32 negative,rxf_f64*out) {
    if(!value){rxf_u64 bits=(rxf_u64)negative<<63;__builtin_memcpy(out,&bits,8);return RXF_OK;}
    rxf_u32 top=63U-(rxf_u32)__builtin_clzll(value), shift=top>52U?top-52U:0U;
    if(shift && (value&(((rxf_u64)1<<shift)-1U)))return RXF_REFUSE_INEXACT;
    rxf_u64 sig=shift?value>>shift:value<<(52U-top);
    rxf_u64 bits=((rxf_u64)negative<<63)|((rxf_u64)(top+1023U)<<52)|(sig&0xfffffffffffffULL);__builtin_memcpy(out,&bits,8);return RXF_OK;
}
RXF_INLINE rxf_u32 rxf_decode_binary(rxf_u64 bits,rxf_u32 exponent_bits,rxf_u32 fraction_bits,rxf_u32 bias,rxf_u64*magnitude,rxf_u32*negative){
    rxf_u64 exponent_mask=((rxf_u64)1<<exponent_bits)-1U, fraction_mask=((rxf_u64)1<<fraction_bits)-1U;
    rxf_u32 exponent=(rxf_u32)((bits>>fraction_bits)&exponent_mask);rxf_u64 fraction=bits&fraction_mask;*negative=(rxf_u32)(bits>>(exponent_bits+fraction_bits));
    if(exponent==(rxf_u32)exponent_mask)return RXF_REFUSE_NON_FINITE;
    if(exponent==0){if(fraction==0){*magnitude=0;return RXF_OK;}return RXF_REFUSE_INEXACT;}
    rxf_i32 power=(rxf_i32)exponent-(rxf_i32)bias-(rxf_i32)fraction_bits;rxf_u64 significand=((rxf_u64)1<<fraction_bits)|fraction;
    if(power>=0){if(power>=64 || significand>(~(rxf_u64)0>>(rxf_u32)power))return RXF_REFUSE_OUT_OF_RANGE;*magnitude=significand<<(rxf_u32)power;return RXF_OK;}
    rxf_u32 right=(rxf_u32)-power;if(right>=64)return RXF_REFUSE_INEXACT;if(significand&(((rxf_u64)1<<right)-1U))return RXF_REFUSE_INEXACT;*magnitude=significand>>right;return RXF_OK;
}
RXF_INLINE rxf_u32 rxf_f32_to_integer(rxf_f32 value,rxf_u64*m,rxf_u32*n){rxf_u32 bits;__builtin_memcpy(&bits,&value,4);return rxf_decode_binary(bits,8,23,127,m,n);}
RXF_INLINE rxf_u32 rxf_f64_to_integer(rxf_f64 value,rxf_u64*m,rxf_u32*n){rxf_u64 bits;__builtin_memcpy(&bits,&value,8);return rxf_decode_binary(bits,11,52,1023,m,n);}
RXF_INLINE rxf_u32 rxf_store_integer(rxf_u64 magnitude,rxf_u32 negative,rxf_u32 width,rxf_u32 signed_value,void*out){
    rxf_u64 positive_limit=signed_value?(((rxf_u64)1<<(width-1U))-1U):(width==64?~(rxf_u64)0:(((rxf_u64)1<<width)-1U));rxf_u64 negative_limit=signed_value?((rxf_u64)1<<(width-1U)):0;
    if((negative&&magnitude>negative_limit)||(!negative&&magnitude>positive_limit)||(negative&&!signed_value&&magnitude))return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 bits=negative?(rxf_u64)0-magnitude:magnitude;__builtin_memcpy(out,&bits,width/8U);return RXF_OK;
}


RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_uint16_t(rxf_u8 a, rxf_u16 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_uint32_t(rxf_u8 a, rxf_u32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_uint64_t(rxf_u8 a, rxf_u64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_int8_t(rxf_u8 a, rxf_i8 *out) {
    if (a > (rxf_u8)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_int16_t(rxf_u8 a, rxf_i16 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_int32_t(rxf_u8 a, rxf_i32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_int64_t(rxf_u8 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_float(rxf_u8 a, rxf_f32 *out) {
    return rxf_u64_to_f32((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint8_t_to_double(rxf_u8 a, rxf_f64 *out) {
    return rxf_u64_to_f64((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_uint8_t(rxf_u16 a, rxf_u8 *out) {
    if (a > (rxf_u16)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_uint32_t(rxf_u16 a, rxf_u32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_uint64_t(rxf_u16 a, rxf_u64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_int8_t(rxf_u16 a, rxf_i8 *out) {
    if (a > (rxf_u16)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_int16_t(rxf_u16 a, rxf_i16 *out) {
    if (a > (rxf_u16)32767) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_int32_t(rxf_u16 a, rxf_i32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_int64_t(rxf_u16 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_float(rxf_u16 a, rxf_f32 *out) {
    return rxf_u64_to_f32((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint16_t_to_double(rxf_u16 a, rxf_f64 *out) {
    return rxf_u64_to_f64((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_uint8_t(rxf_u32 a, rxf_u8 *out) {
    if (a > (rxf_u32)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_uint16_t(rxf_u32 a, rxf_u16 *out) {
    if (a > (rxf_u32)65535) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_uint64_t(rxf_u32 a, rxf_u64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_int8_t(rxf_u32 a, rxf_i8 *out) {
    if (a > (rxf_u32)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_int16_t(rxf_u32 a, rxf_i16 *out) {
    if (a > (rxf_u32)32767) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_int32_t(rxf_u32 a, rxf_i32 *out) {
    if (a > (rxf_u32)2147483647) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_int64_t(rxf_u32 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_float(rxf_u32 a, rxf_f32 *out) {
    return rxf_u64_to_f32((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint32_t_to_double(rxf_u32 a, rxf_f64 *out) {
    return rxf_u64_to_f64((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_uint8_t(rxf_u64 a, rxf_u8 *out) {
    if (a > (rxf_u64)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_uint16_t(rxf_u64 a, rxf_u16 *out) {
    if (a > (rxf_u64)65535) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_uint32_t(rxf_u64 a, rxf_u32 *out) {
    if (a > (rxf_u64)4294967295) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_int8_t(rxf_u64 a, rxf_i8 *out) {
    if (a > (rxf_u64)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_int16_t(rxf_u64 a, rxf_i16 *out) {
    if (a > (rxf_u64)32767) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_int32_t(rxf_u64 a, rxf_i32 *out) {
    if (a > (rxf_u64)2147483647) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_int64_t(rxf_u64 a, rxf_i64 *out) {
    if (a > (rxf_u64)9223372036854775807ULL) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_float(rxf_u64 a, rxf_f32 *out) {
    return rxf_u64_to_f32((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_uint64_t_to_double(rxf_u64 a, rxf_f64 *out) {
    return rxf_u64_to_f64((rxf_u64)a, 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_uint8_t(rxf_i8 a, rxf_u8 *out) {
    if (a < (rxf_i8)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_uint16_t(rxf_i8 a, rxf_u16 *out) {
    if (a < (rxf_i8)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_uint32_t(rxf_i8 a, rxf_u32 *out) {
    if (a < (rxf_i8)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_uint64_t(rxf_i8 a, rxf_u64 *out) {
    if (a < (rxf_i8)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_int16_t(rxf_i8 a, rxf_i16 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_int32_t(rxf_i8 a, rxf_i32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_int64_t(rxf_i8 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_float(rxf_i8 a, rxf_f32 *out) {
    return rxf_u64_to_f32(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int8_t_to_double(rxf_i8 a, rxf_f64 *out) {
    return rxf_u64_to_f64(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_uint8_t(rxf_i16 a, rxf_u8 *out) {
    if (a < (rxf_i16)0 || a > (rxf_i16)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_uint16_t(rxf_i16 a, rxf_u16 *out) {
    if (a < (rxf_i16)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_uint32_t(rxf_i16 a, rxf_u32 *out) {
    if (a < (rxf_i16)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_uint64_t(rxf_i16 a, rxf_u64 *out) {
    if (a < (rxf_i16)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_int8_t(rxf_i16 a, rxf_i8 *out) {
    if (a < (rxf_i16)-128 || a > (rxf_i16)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_int32_t(rxf_i16 a, rxf_i32 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_int64_t(rxf_i16 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_float(rxf_i16 a, rxf_f32 *out) {
    return rxf_u64_to_f32(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int16_t_to_double(rxf_i16 a, rxf_f64 *out) {
    return rxf_u64_to_f64(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_uint8_t(rxf_i32 a, rxf_u8 *out) {
    if (a < (rxf_i32)0 || a > (rxf_i32)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_uint16_t(rxf_i32 a, rxf_u16 *out) {
    if (a < (rxf_i32)0 || a > (rxf_i32)65535) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_uint32_t(rxf_i32 a, rxf_u32 *out) {
    if (a < (rxf_i32)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_uint64_t(rxf_i32 a, rxf_u64 *out) {
    if (a < (rxf_i32)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_int8_t(rxf_i32 a, rxf_i8 *out) {
    if (a < (rxf_i32)-128 || a > (rxf_i32)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_int16_t(rxf_i32 a, rxf_i16 *out) {
    if (a < (rxf_i32)-32768 || a > (rxf_i32)32767) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_int64_t(rxf_i32 a, rxf_i64 *out) {
    if (0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_float(rxf_i32 a, rxf_f32 *out) {
    return rxf_u64_to_f32(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int32_t_to_double(rxf_i32 a, rxf_f64 *out) {
    return rxf_u64_to_f64(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_uint8_t(rxf_i64 a, rxf_u8 *out) {
    if (a < (rxf_i64)0 || a > (rxf_i64)255) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_uint16_t(rxf_i64 a, rxf_u16 *out) {
    if (a < (rxf_i64)0 || a > (rxf_i64)65535) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_uint32_t(rxf_i64 a, rxf_u32 *out) {
    if (a < (rxf_i64)0 || a > (rxf_i64)4294967295) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_uint64_t(rxf_i64 a, rxf_u64 *out) {
    if (a < (rxf_i64)0) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_u64)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_int8_t(rxf_i64 a, rxf_i8 *out) {
    if (a < (rxf_i64)-128 || a > (rxf_i64)127) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i8)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_int16_t(rxf_i64 a, rxf_i16 *out) {
    if (a < (rxf_i64)-32768 || a > (rxf_i64)32767) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i16)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_int32_t(rxf_i64 a, rxf_i32 *out) {
    if (a < (rxf_i64)-2147483648 || a > (rxf_i64)2147483647) return RXF_REFUSE_OUT_OF_RANGE;
    *out = (rxf_i32)a;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_float(rxf_i64 a, rxf_f32 *out) {
    return rxf_u64_to_f32(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_int64_t_to_double(rxf_i64 a, rxf_f64 *out) {
    return rxf_u64_to_f64(rxf_i64_magnitude((rxf_i64)a), a < 0, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_uint8_t(rxf_f32 a, rxf_u8 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 8U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_uint16_t(rxf_f32 a, rxf_u16 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 16U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_uint32_t(rxf_f32 a, rxf_u32 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 32U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_uint64_t(rxf_f32 a, rxf_u64 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 64U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_int8_t(rxf_f32 a, rxf_i8 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 8U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_int16_t(rxf_f32 a, rxf_i16 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 16U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_int32_t(rxf_f32 a, rxf_i32 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 32U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_int64_t(rxf_f32 a, rxf_i64 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f32_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 64U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_float_to_double(rxf_f32 a, rxf_f64 *out) {
    if (!rxf_f32_finite(a)) return RXF_REFUSE_NON_FINITE;
    rxf_f64 value = (rxf_f64)a;
    if (!rxf_f64_finite(value)) return RXF_REFUSE_OUT_OF_RANGE;
    if ((rxf_f32)value != a) return RXF_REFUSE_INEXACT;
    *out = value;
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_uint8_t(rxf_f64 a, rxf_u8 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 8U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_uint16_t(rxf_f64 a, rxf_u16 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 16U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_uint32_t(rxf_f64 a, rxf_u32 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 32U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_uint64_t(rxf_f64 a, rxf_u64 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 64U, 0U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_int8_t(rxf_f64 a, rxf_i8 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 8U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_int16_t(rxf_f64 a, rxf_i16 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 16U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_int32_t(rxf_f64 a, rxf_i32 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 32U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_int64_t(rxf_f64 a, rxf_i64 *out) {
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = rxf_f64_to_integer(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, 64U, 1U, out);
}

RXF_FUNCTION rxf_u32 rxf_convert_double_to_float(rxf_f64 a, rxf_f32 *out) {
    if (!rxf_f64_finite(a)) return RXF_REFUSE_NON_FINITE;
    rxf_f32 value = (rxf_f32)a;
    if (!rxf_f32_finite(value)) return RXF_REFUSE_OUT_OF_RANGE;
    if ((rxf_f64)value != a) return RXF_REFUSE_INEXACT;
    *out = value;
    return RXF_OK;
}
