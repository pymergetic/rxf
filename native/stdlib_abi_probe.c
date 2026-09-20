#include "stdlib_memory.h"
#include "stdlib_runtime.h"

/* Compile-only ABI probes. Calls deliberately survive in emitted assembly so
 * target argument placement can be inspected without executing substitute code. */
__attribute__((noinline)) rxf_u32 rxf_probe_allocate(
    rxf_runtime_context *ctx, rxf_u64 id, rxf_u64 size, rxf_u64 alignment,
    rxf_transaction *transaction, rxf_object_handle *out) {
    return rxf_runtime_allocate_prepare(ctx, id, size, alignment, transaction, out);
}
__attribute__((noinline)) rxf_u32 rxf_probe_write(
    rxf_runtime_context *ctx, rxf_object_handle handle, rxf_u64 offset,
    rxf_u64 width, rxf_u64 value) {
    return rxf_runtime_write(ctx, handle, offset, width, value);
}
__attribute__((noinline)) rxf_u32 rxf_probe_format(
    rxf_u32 value, rxf_u64 destination, rxf_u64 capacity, rxf_u64 *out) {
    return rxf_runtime_format_u32(value, destination, capacity, out);
}
__attribute__((noinline)) rxf_u32 rxf_probe_search(
    rxf_runtime_context *ctx, rxf_object_handle haystack, rxf_u64 length,
    rxf_object_handle needle, rxf_u64 needle_length, rxf_transaction *transaction,
    rxf_u64 *out) {
    return rxf_runtime_search(ctx, haystack, length, needle, needle_length, transaction, out);
}
__attribute__((noinline)) rxf_u32 rxf_probe_memory_compare(
    const rxf_u8 *a, rxf_u64 an, const rxf_u8 *b, rxf_u64 bn, rxf_u64 count,
    rxf_i32 *out) {
    return rxf_memory_compare(a, an, b, bn, count, out);
}
__attribute__((noinline)) rxf_u64 rxf_probe_overflow(
    rxf_u64 a0, rxf_u64 a1, rxf_u64 a2, rxf_u64 a3, rxf_u64 a4,
    rxf_u64 a5, rxf_u64 a6, rxf_u64 a7, rxf_u64 a8) {
    return a0+a1+a2+a3+a4+a5+a6+a7+a8;
}
