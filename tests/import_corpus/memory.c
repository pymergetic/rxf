#include <stdint.h>

#include <string.h>

#include "../../native/stdlib_memory.h"

/* The flat memory profile: the memory leaves of native/stdlib_memory.c (copy and fill have
   no out: their CallCallback operations have no result), and a load and a store through a
   pointer parameter (the bytes are the address the run gives, or a local's wide slot). */

uint64_t copy8(uint64_t *dst, uint64_t *src, uint64_t tag) {
    rxf_memory_copy((rxf_u8 *)dst, 8, (const rxf_u8 *)src, 8, 8);
    return tag;
}

uint64_t set8(uint64_t *dst, uint64_t value) {
    rxf_memory_fill((rxf_u8 *)dst, 8, (rxf_u8)value, 8);
    return value;
}

uint64_t store64(uint64_t *p, uint64_t value) {
    *p = value;
    return value;
}

uint64_t load64(uint64_t *p) {
    return *p;
}

uint64_t same8(uint64_t *a, uint64_t *b) {
    rxf_i32 out = 0;
    rxf_memory_compare((const rxf_u8 *)a, 8, (const rxf_u8 *)b, 8, 8, &out);
    return (uint64_t)(rxf_u32)out;
}

/* The narrow widths through flat memory: a store writes only the bytes of its type and a
   load reads only those (the rest of the word stays zero on the way out), so the byte
   comparison of the differential sees exactly the bytes both sides write. */

uint64_t store8(uint8_t *p, uint64_t value) {
    *p = (uint8_t)value;
    return *p;
}

uint64_t store16(uint16_t *p, uint64_t value) {
    *p = (uint16_t)value;
    return *p;
}

uint64_t store32(uint32_t *p, uint64_t value) {
    *p = (uint32_t)value;
    return *p;
}

uint64_t load8(uint8_t *p) {
    return *p;
}

uint64_t load16(uint16_t *p) {
    return *p;
}

uint64_t load32(uint32_t *p) {
    return *p;
}

/* --- Pointer arithmetic (getelementptr): bounded array indexing. The index is
       masked in the source so the differential harness can feed any uint64 while
       both sides touch the same in-bounds slot of the 8-byte test cell. --- */
uint64_t idx8(uint8_t *base, uint64_t i) {
    uint64_t j = i & 7;
    uint8_t *slot = base + j; /* getelementptr i8, ptr, i64 %j */
    return *slot;
}

uint64_t idx32(uint32_t *base, uint64_t i) {
    uint64_t j = i & 1;
    uint32_t *slot = base + j; /* getelementptr i32, ptr, i64 %j  (scale 4) */
    return *slot;
}

uint64_t idx8_put(uint8_t *base, uint64_t i, uint64_t v) {
    uint64_t j = i & 7;
    uint8_t *slot = base + j;
    *slot = (uint8_t)v;
    return *slot;
}

/* --- Fixed array inside a local frame exercises GEP with a constant index. --- */
uint64_t local_arr(uint64_t seed) {
    uint64_t a[2];
    a[0] = seed + 1;
    a[1] = seed * 2;
    return a[1] - a[0]; /* two GEPs with constant indices */
}

/* --- Struct field access via GEP. Two constant-offset field loads, combined. --- */
struct pair {
    uint32_t lo;
    uint32_t hi;
};

uint64_t pair_mix(uint8_t *raw) {
    struct pair *sp = (struct pair *)raw;
    uint32_t lo = sp->lo; /* getelementptr %struct.pair, ptr, i64 0, i32 0 */
    uint32_t hi = sp->hi; /* getelementptr %struct.pair, ptr, i64 0, i32 1 */
    return ((uint64_t)lo << 32) | hi;
}

uint64_t pair_pick(uint8_t *raw, uint64_t field) {
    struct pair *sp = (struct pair *)raw;
    uint32_t lo = sp->lo;
    uint32_t hi = sp->hi;
    uint32_t got = (field == 0) ? lo : hi; /* a select on the loaded values */
    return got;
}

/* --- A select or phi of addresses: the chosen pointer is then loaded/stored through.
       ``field == 0 ? &sp->lo : &sp->hi`` lowers to ``select i1, ptr, ptr`` and the load
       resolves to the field the select chose; ``walk`` carries a pointer around a loop
       (``phi ptr``) and reads through it. --- */
uint64_t pick_load(uint8_t *raw, uint64_t field) {
    struct pair *sp = (struct pair *)raw;
    uint32_t *slot = (field == 0) ? &sp->lo : &sp->hi;
    return *slot;
}

uint64_t pick_store(uint8_t *raw, uint64_t field, uint64_t v) {
    struct pair *sp = (struct pair *)raw;
    uint32_t *slot = (field == 0) ? &sp->lo : &sp->hi;
    *slot = (uint32_t)v;
    return *slot;
}

uint64_t walk(uint8_t *base, uint64_t n) {
    uint64_t m = n & 3; /* stay inside the 8-byte test cell */
    uint8_t *p = base;
    uint64_t acc = 0;
    for (uint64_t i = 0; i < m; i++) {
        acc += *p;
        p += 1;
    }
    return acc;
}

/* A pointer round-tripped through an integer: ``ptrtoint`` then an address add, then
   ``inttoptr`` back to a pointer the load reads through (an address is an i64 word). */
uint64_t addr_roundtrip(uint8_t *p, uint64_t v) {
    uintptr_t base = (uintptr_t)p;              /* ptrtoint ptr %p to i64 */
    uint8_t *slot = (uint8_t *)(base + (v & 7)); /* inttoptr i64 ... to ptr */
    return *slot;
}

/* --- memcpy / memset intrinsics, kept inside a single 8-byte test cell. --- */
uint64_t mcopy1(uint8_t *dst, uint8_t *src) {
    __builtin_memcpy(dst, src, 1); /* llvm.memcpy.p0.p0.i64 len 1 */
    return *dst;
}

uint64_t mfill4(uint8_t *dst, uint64_t v) {
    __builtin_memset(dst, (int)v, 4); /* llvm.memset.p0.i64 len 4 */
    return dst[0] + (uint64_t)dst[3];
}

uint64_t mcopy4(uint8_t *dst, uint8_t *src) {
    __builtin_memcpy(dst, src, 4);
    return (uint64_t)dst[0] + (uint64_t)dst[1] + (uint64_t)dst[2] + (uint64_t)dst[3];
}

/* A length the compiler cannot fold keeps the memcpy as a real llvm.memcpy call (a
   small constant copy is inlined into loads and stores at -O1). The count is masked
   to the 8-byte test cell so both sides stay in bounds. */
uint64_t mcopyn(uint8_t *dst, uint8_t *src, uint64_t n) {
    uint64_t count = (n & 7) + 1; /* 1..8 bytes */
    __builtin_memcpy(dst, src, count);
    return (uint64_t)dst[0] + (uint64_t)dst[7];
}

/* Outside the subset on purpose (test_outside_the_subset_is_refused): a local whose join
   lives in memory. volatile keeps clang from promoting it to a register, so the two
   stores of the arms and the load of the join read across blocks -- a value the subset
   refuses to guess (promoting such joins to SSA is a next step). */
uint64_t slot_join(uint64_t x) {
    volatile uint64_t t;
    if (x & 1) {
        t = x + 1;
    } else {
        t = x + 2;
    }
    return t;
}
