#!/usr/bin/env python3
"""Generate 90 named conversions using integer-defined IEEE-754 bit algorithms."""

from pathlib import Path

ROOT = Path(__file__).parents[1]
TYPES = [
    ("rxf_u8", "uint8_t", "uint", 8),
    ("rxf_u16", "uint16_t", "uint", 16),
    ("rxf_u32", "uint32_t", "uint", 32),
    ("rxf_u64", "uint64_t", "uint", 64),
    ("rxf_i8", "int8_t", "sint", 8),
    ("rxf_i16", "int16_t", "sint", 16),
    ("rxf_i32", "int32_t", "sint", 32),
    ("rxf_i64", "int64_t", "sint", 64),
    ("rxf_f32", "float", "float", 32),
    ("rxf_f64", "double", "float", 64),
]


def bounds(k, w):
    return (0, (1 << w) - 1) if k == "uint" else (-(1 << (w - 1)), (1 << (w - 1)) - 1)


def int_int(s, d):
    st, sn, sk, sw = s
    dt, dn, dk, dw = d
    slo, shi = bounds(sk, sw)
    dlo, dhi = bounds(dk, dw)
    checks = []
    if dlo > slo:
        checks.append(f"a < ({st}){dlo}")
    if dhi < shi:
        checks.append(f"a > ({st}){dhi}{'ULL' if dhi > 0xFFFFFFFF else ''}")
    guard = " || ".join(checks) or "0"
    return f"""RXF_FUNCTION rxf_u32 rxf_convert_{sn}_to_{dn}({st} a, {dt} *out) {{
    if ({guard}) return RXF_REFUSE_OUT_OF_RANGE;
    *out = ({dt})a;
    return RXF_OK;
}}"""


def int_float(s, d):
    st, sn, sk, _sw = s
    dt, dn, _dk, dw = d
    sign = "a < 0" if sk == "sint" else "0"
    mag = "rxf_i64_magnitude((rxf_i64)a)" if sk == "sint" else "(rxf_u64)a"
    helper = "rxf_u64_to_f32" if dw == 32 else "rxf_u64_to_f64"
    return f"""RXF_FUNCTION rxf_u32 rxf_convert_{sn}_to_{dn}({st} a, {dt} *out) {{
    return {helper}({mag}, {sign}, out);
}}"""


def float_int(s, d):
    st, sn, _sk, sw = s
    dt, dn, dk, dw = d
    helper = "rxf_f32_to_integer" if sw == 32 else "rxf_f64_to_integer"
    signed = 1 if dk == "sint" else 0
    return f"""RXF_FUNCTION rxf_u32 rxf_convert_{sn}_to_{dn}({st} a, {dt} *out) {{
    rxf_u64 magnitude; rxf_u32 negative;
    rxf_u32 status = {helper}(a, &magnitude, &negative);
    if (status != RXF_OK) return status;
    return rxf_store_integer(magnitude, negative, {dw}U, {signed}U, out);
}}"""


def float_float(s, d):
    st, sn, _sk, sw = s
    dt, dn, _dk, dw = d
    finite = "rxf_f32_finite" if sw == 32 else "rxf_f64_finite"
    resultfinite = "rxf_f32_finite" if dw == 32 else "rxf_f64_finite"
    return f"""RXF_FUNCTION rxf_u32 rxf_convert_{sn}_to_{dn}({st} a, {dt} *out) {{
    if (!{finite}(a)) return RXF_REFUSE_NON_FINITE;
    {dt} value = ({dt})a;
    if (!{resultfinite}(value)) return RXF_REFUSE_OUT_OF_RANGE;
    if (({st})value != a) return RXF_REFUSE_INEXACT;
    *out = value;
    return RXF_OK;
}}"""


header = r"""#include "basics.h"
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
"""
parts = [header]
for s in TYPES:
    for d in TYPES:
        if s[1] == d[1]:
            continue
        fn = (
            int_int
            if s[2] != "float" and d[2] != "float"
            else int_float
            if s[2] != "float"
            else float_int
            if d[2] != "float"
            else float_float
        )
        parts.append(fn(s, d))
(ROOT / "native/conversions.c").write_text("\n\n".join(parts) + "\n")
