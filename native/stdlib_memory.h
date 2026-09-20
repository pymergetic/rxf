#ifndef RXF_STDLIB_MEMORY_H
#define RXF_STDLIB_MEMORY_H

#include "basics.h"

enum rxf_endian { RXF_ENDIAN_LITTLE = 1, RXF_ENDIAN_BIG = 2 };

rxf_u32 rxf_memory_copy(rxf_u8 *dst, rxf_u64 dst_len, const rxf_u8 *src, rxf_u64 src_len, rxf_u64 count);
rxf_u32 rxf_memory_move(rxf_u8 *dst, rxf_u64 dst_len, const rxf_u8 *src, rxf_u64 src_len, rxf_u64 count);
rxf_u32 rxf_memory_fill(rxf_u8 *dst, rxf_u64 dst_len, rxf_u8 value, rxf_u64 count);
rxf_u32 rxf_memory_zero(rxf_u8 *dst, rxf_u64 dst_len, rxf_u64 count);
rxf_u32 rxf_memory_compare(const rxf_u8 *a, rxf_u64 a_len, const rxf_u8 *b, rxf_u64 b_len, rxf_u64 count, rxf_i32 *out);
rxf_u32 rxf_load_u32(const rxf_u8 *src, rxf_u64 len, rxf_u64 offset, rxf_u32 alignment, rxf_u32 endian, rxf_u32 *out);
rxf_u32 rxf_store_u32(rxf_u8 *dst, rxf_u64 len, rxf_u64 offset, rxf_u32 alignment, rxf_u32 endian, rxf_u32 value);
rxf_u32 rxf_utf8_validate(const rxf_u8 *src, rxf_u64 len, rxf_u64 *out_scalars);
rxf_u32 rxf_hash_bytes(const rxf_u8 *src, rxf_u64 len, rxf_u64 seed, rxf_u64 *out);

#endif
