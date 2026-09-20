#include <stdint.h>
#include "stdlib_runtime.h"
#include "../generated/semantic_cfg_qemu_bindings.h"

typedef uint32_t u32;
typedef uint64_t u64;

typedef u32 (*semantic_blob)(rxf_runtime_context *, u64, u64 *);
extern u32 semantic_cfg_blob_raw(rxf_runtime_context *, u64, u64 *);
extern u32 semantic_cfg_blob_optimized(rxf_runtime_context *, u64, u64 *);
static rxf_object_handle begin_handle = {101, 1, 0};
static rxf_object_handle validate_handle = {202, 1, 0};
static rxf_transaction_member journal[2];
static u32 stage;
static u32 allocate_status;

static u32 begin_private(rxf_runtime_context *context, rxf_object_handle *handle,
                         u64 identity, rxf_transaction *transaction) {
    stage = 1;
    if (!context || !handle || handle->object_id != begin_handle.object_id || identity != 0xf06e)
        return 81;
    if (allocate_status) return allocate_status;
    transaction->root_object_id = 0xabc;
    transaction->old_frontier = context->heap_frontier;
    transaction->identity = identity;
    transaction->active = 1;
    return 0;
}
static u32 allocate(rxf_runtime_context *context, u64 object_id, u64 type_id,
                    u64 size, u64 capacity, u64 alignment,
                    rxf_transaction *transaction, rxf_object_handle *output) {
    stage = 2;
    if (!context || object_id != 9 || type_id != 8 || size != 8 || capacity != 1 ||
        alignment != 1 || !transaction || transaction->root_object_id != 0xabc)
        return 82;
    if (allocate_status) return allocate_status;
    output->object_id = 11; output->generation = 12; output->offset = 13;
    return 0;
}
static u32 validate(rxf_runtime_context *context, rxf_object_handle handle,
                    u64 generation, u64 *output) {
    stage = 3;
    if (!context || handle.object_id != validate_handle.object_id || generation != 1)
        return 83;
    *output = 0x55;
    return 0;
}
static u32 publish(rxf_runtime_context *context, rxf_transaction *transaction) {
    stage = 4;
    if (!context || !transaction) return 84;
    return 0;
}
static u32 rollback(rxf_runtime_context *context, rxf_transaction *transaction) {
    stage = 5;
    if (!context || !transaction) return 85;
    return 0;
}
static u32 cleanup(rxf_runtime_context *context, rxf_transaction *transaction) {
    stage = 6;
    if (!context || !transaction) return 86;
    return 0;
}
static rxf_native_slot functions[] = {
    {RXF_ARRAY_BEGIN_FUNCTION_ID, 1, (u64)(uintptr_t)begin_private},
    {RXF_ARRAY_VALIDATE_FUNCTION_ID, 1, (u64)(uintptr_t)validate},
    {RXF_ARRAY_ALLOCATE_FUNCTION_ID, 1, (u64)(uintptr_t)allocate},
    {RXF_ARRAY_PUBLISH_FUNCTION_ID, 1, (u64)(uintptr_t)publish},
    {RXF_ARRAY_ROLLBACK_FUNCTION_ID, 1, (u64)(uintptr_t)rollback},
    {RXF_ARRAY_CLEANUP_FUNCTION_ID, 1, (u64)(uintptr_t)cleanup},
};
static rxf_object_entry objects[] = {
    {RXF_ARRAY_BEGIN_OBJECT_ID, 1, 12, (u64)(uintptr_t)&begin_handle, sizeof(begin_handle), sizeof(begin_handle), RXF_OBJECT_LIVE, 0, 0, 0},
    {RXF_ARRAY_VALIDATE_OBJECT_ID, 1, 12, (u64)(uintptr_t)&validate_handle, sizeof(validate_handle), sizeof(validate_handle), RXF_OBJECT_LIVE, 0, 0, 0},
};
static void puts(const char *s) { volatile u32 *uart=(volatile u32*)0x09000000; while (*s) *uart=(u32)*s++; }
static void put_u32(u32 value) {
    char digits[10]; u32 count=0; volatile u32 *uart=(volatile u32*)0x09000000;
    do { digits[count++]=(char)('0'+value%10); value/=10; } while (value);
    while (count) *uart=(u32)digits[--count];
}
static void exit_qemu(u32 ok) {
    static u64 block[2]; block[0] = ok ? 0x20026 : 0x20023; block[1] = 0;
    register u64 x0 __asm__("x0")=0x20; register void *x1 __asm__("x1")=block;
    __asm__ volatile("hlt #0xf000"::"r"(x0),"r"(x1):"memory"); for (;;) __asm__ volatile("wfe");
}
static rxf_runtime_context context(void) {
    rxf_runtime_context value = {0};
    value.version = RXF_RUNTIME_ABI_VERSION; value.size = sizeof(value);
    value.function_count = sizeof(functions)/sizeof(functions[0]); value.functions = (u64)(uintptr_t)functions;
    value.object_count = sizeof(objects)/sizeof(objects[0]); value.objects = (u64)(uintptr_t)objects;
    value.journal_capacity = sizeof(journal)/sizeof(journal[0]); value.journal = (u64)(uintptr_t)journal;
    return value;
}
static u32 run_blob(semantic_blob blob) {
    rxf_runtime_context runtime = context(); u64 output = 0xaaaaaaaaaaaaaaaaULL;
    stage = 0; allocate_status = 0;
    u32 status = blob(&runtime, 9, &output);
    if (status != 0) { puts("RXF-ARRAY-STATUS-FAIL status="); put_u32(status); puts(" stage="); put_u32(stage); puts("\n"); return 0; }
    if (output != 0xabc || stage != 6) { puts("RXF-ARRAY-SUCCESS-OUTPUT-FAIL stage="); put_u32(stage); puts("\n"); return 0; }
    runtime = context(); output = 0xbbbbbbbbbbbbbbbbULL; stage = 0; allocate_status = 31;
    status = blob(&runtime, 9, &output);
    if (status != 31) { puts("RXF-ARRAY-REFUSAL-STATUS-FAIL stage="); put_u32(stage); puts("\n"); return 0; }
    if (output != 0xbbbbbbbbbbbbbbbbULL || stage != 1) { puts("RXF-ARRAY-REFUSAL-OUTPUT-FAIL stage="); put_u32(stage); puts("\n"); return 0; }
    return 1;
}
int rxf_qemu_main(void) {
    if (!run_blob(semantic_cfg_blob_raw)) { puts("RXF-ARRAY-RAW-FAIL\n"); exit_qemu(0); }
    if (!run_blob(semantic_cfg_blob_optimized)) { puts("RXF-ARRAY-OPTIMIZED-FAIL\n"); exit_qemu(0); }
    puts("RXF-SEMANTIC-CFG-PASS\n"); exit_qemu(1); return 0;
}
