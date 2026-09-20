#include "stdlib_memory.h"

#define RXF_FUNCTION __attribute__((noinline))
#define RXF_REFUSE_ALIGNMENT 7U
#define RXF_REFUSE_INVALID_UTF8 8U

static rxf_u32 rxf_extent_ok(rxf_u64 len, rxf_u64 offset, rxf_u64 count) {
    return offset <= len && count <= len - offset;
}

RXF_FUNCTION rxf_u32 rxf_memory_copy(rxf_u8 *dst, rxf_u64 dst_len, const rxf_u8 *src, rxf_u64 src_len, rxf_u64 count) {
    if (!rxf_extent_ok(dst_len, 0, count) || !rxf_extent_ok(src_len, 0, count)) return RXF_REFUSE_OUT_OF_RANGE;
    for (rxf_u64 i = 0; i < count; ++i) dst[i] = src[i];
    return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_memory_move(rxf_u8 *dst, rxf_u64 dst_len, const rxf_u8 *src, rxf_u64 src_len, rxf_u64 count) {
    if (!rxf_extent_ok(dst_len, 0, count) || !rxf_extent_ok(src_len, 0, count)) return RXF_REFUSE_OUT_OF_RANGE;
    if (dst < src) for (rxf_u64 i = 0; i < count; ++i) dst[i] = src[i];
    else if (dst > src) for (rxf_u64 i = count; i != 0; --i) dst[i - 1] = src[i - 1];
    return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_memory_fill(rxf_u8 *dst, rxf_u64 dst_len, rxf_u8 value, rxf_u64 count) {
    if (!rxf_extent_ok(dst_len, 0, count)) return RXF_REFUSE_OUT_OF_RANGE;
    for (rxf_u64 i = 0; i < count; ++i) dst[i] = value;
    return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_memory_zero(rxf_u8 *dst, rxf_u64 dst_len, rxf_u64 count) {
    if (!rxf_extent_ok(dst_len, 0, count)) return RXF_REFUSE_OUT_OF_RANGE;
    for (rxf_u64 i = 0; i < count; ++i) dst[i] = 0;
    return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_memory_compare(const rxf_u8 *a, rxf_u64 a_len, const rxf_u8 *b, rxf_u64 b_len, rxf_u64 count, rxf_i32 *out) {
    if (!rxf_extent_ok(a_len, 0, count) || !rxf_extent_ok(b_len, 0, count)) return RXF_REFUSE_OUT_OF_RANGE;
    rxf_i32 result = 0;
    for (rxf_u64 i = 0; i < count; ++i) if (a[i] != b[i]) { result = a[i] < b[i] ? -1 : 1; break; }
    *out = result; return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_load_u32(const rxf_u8 *src, rxf_u64 len, rxf_u64 offset, rxf_u32 alignment, rxf_u32 endian, rxf_u32 *out) {
    if (!alignment || (alignment & (alignment - 1U)) || (offset & (alignment - 1U))) return RXF_REFUSE_ALIGNMENT;
    if (!rxf_extent_ok(len, offset, 4)) return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u32 v = endian == RXF_ENDIAN_LITTLE ? ((rxf_u32)src[offset] | (rxf_u32)src[offset+1]<<8 | (rxf_u32)src[offset+2]<<16 | (rxf_u32)src[offset+3]<<24) : ((rxf_u32)src[offset]<<24 | (rxf_u32)src[offset+1]<<16 | (rxf_u32)src[offset+2]<<8 | src[offset+3]);
    if (endian != RXF_ENDIAN_LITTLE && endian != RXF_ENDIAN_BIG) return RXF_REFUSE_OUT_OF_RANGE;
    *out = v; return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_store_u32(rxf_u8 *dst, rxf_u64 len, rxf_u64 offset, rxf_u32 alignment, rxf_u32 endian, rxf_u32 value) {
    if (!alignment || (alignment & (alignment - 1U)) || (offset & (alignment - 1U))) return RXF_REFUSE_ALIGNMENT;
    if (!rxf_extent_ok(len, offset, 4) || (endian != RXF_ENDIAN_LITTLE && endian != RXF_ENDIAN_BIG)) return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u8 bytes[4] = {(rxf_u8)value, (rxf_u8)(value>>8), (rxf_u8)(value>>16), (rxf_u8)(value>>24)};
    for (rxf_u32 i=0;i<4;++i) dst[offset+i] = endian == RXF_ENDIAN_LITTLE ? bytes[i] : bytes[3-i];
    return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_utf8_validate(const rxf_u8 *s, rxf_u64 len, rxf_u64 *out_scalars) {
    rxf_u64 i=0, n=0;
    while (i<len) { rxf_u32 c=s[i++], need=0, min=0, scalar=c;
        if (c<0x80) { ++n; continue; }
        if ((c&0xe0)==0xc0) { need=1; min=0x80; scalar=c&0x1f; } else if ((c&0xf0)==0xe0) { need=2; min=0x800; scalar=c&0xf; } else if ((c&0xf8)==0xf0) { need=3; min=0x10000; scalar=c&7; } else return RXF_REFUSE_INVALID_UTF8;
        if (need>len-i) return RXF_REFUSE_INVALID_UTF8;
        for (rxf_u32 j=0;j<need;++j) { c=s[i++]; if ((c&0xc0)!=0x80) return RXF_REFUSE_INVALID_UTF8; scalar=(scalar<<6)|(c&0x3f); }
        if (scalar<min || scalar>0x10ffff || (scalar>=0xd800 && scalar<=0xdfff)) return RXF_REFUSE_INVALID_UTF8; ++n;
    } *out_scalars=n; return RXF_OK;
}
RXF_FUNCTION rxf_u32 rxf_hash_bytes(const rxf_u8 *src, rxf_u64 len, rxf_u64 seed, rxf_u64 *out) {
    rxf_u64 h = 14695981039346656037ULL ^ seed;
    for (rxf_u64 i=0;i<len;++i) { h ^= src[i]; h *= 1099511628211ULL; } *out=h; return RXF_OK;
}
