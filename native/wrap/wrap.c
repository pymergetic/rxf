/*
 * The one wrapper body: the entry convention of native/wrap/wrap.h, written once for
 * every seat. A seat finds the package and a block at its base and calls
 * rxf_wrap_enter; everything else a wrapper does is reporting.
 *
 * Nothing here is libc: the seats have none (a boot, a firmware application, a static
 * process). The copy is a loop so no builtins are needed.
 */
#include "wrap.h"

#if !defined(_WIN32)
#include "../graph.h"
/* The run of the image is rxf_graph_run (native/graph.h); the wrapper names its type
 * with the ABI of the machine (wrap.h), and on a seat that compiles for that ABI the
 * two types have to be one. */
_Static_assert(__builtin_types_compatible_p(__typeof__(&rxf_graph_run), rxf_wrap_run_fn),
               "the run signature is the one of native/graph.h");
#endif

static void copy(rxf_u8 *to, const rxf_u8 *from, rxf_u64 bytes) {
    while (bytes--)
        *to++ = *from++;
}

static rxf_u32 same(const void *left, const void *right, rxf_u64 bytes) {
    const rxf_u8 *a = (const rxf_u8 *)left;
    const rxf_u8 *b = (const rxf_u8 *)right;
    while (bytes--)
        if (*a++ != *b++)
            return 0;
    return 1;
}

static rxf_u32 inside(rxf_u64 address, rxf_u64 bytes, rxf_u64 base, rxf_u64 limit) {
    return address >= base && address <= limit && bytes <= limit - address;
}

rxf_u32 rxf_wrap_enter(const void *package, rxf_u64 package_bytes, void *block, rxf_u64 block_bytes,
                       rxf_wrap_result *result) {
    const rxf_wrap_package *p = (const rxf_wrap_package *)package;
    rxf_object_handle entry;
    rxf_wrap_run_fn run;
    rxf_u64 limit, i;

    if (!package || !result || package_bytes < RXF_WRAP_PACKAGE_BYTES)
        return RXF_WRAP_ANSWER_BAD_PACKAGE;
    if (!same(p->magic, RXF_WRAP_MAGIC, 4) || p->version != RXF_WRAP_VERSION ||
        p->total != package_bytes)
        return RXF_WRAP_ANSWER_BAD_PACKAGE;
    for (i = 0; i < sizeof p->reserved / sizeof p->reserved[0]; i++)
        if (p->reserved[i])
            return RXF_WRAP_ANSWER_BAD_PACKAGE;
    limit = p->base + p->image_bytes;
    if (p->image < RXF_WRAP_PACKAGE_BYTES || p->image_bytes < sizeof(rxf_runtime_context) ||
        p->image + p->image_bytes > p->total)
        return RXF_WRAP_ANSWER_BAD_PACKAGE;
    if (!inside(p->run, 8, p->base, limit) || !inside(p->args, 8, p->base, limit) ||
        !inside(p->out, 8, p->base, limit) || !inside(p->frame, 8, p->base, limit))
        return RXF_WRAP_ANSWER_BAD_PACKAGE;
    if ((rxf_u64)(unsigned long long)block != p->base || block_bytes < p->image_bytes)
        return RXF_WRAP_ANSWER_NO_BLOCK;
    /* Load the image: the block is the memory of the run, nothing in it is relocated. */
    if ((const rxf_u8 *)package + p->image != (const rxf_u8 *)block)
        copy((rxf_u8 *)block, (const rxf_u8 *)package + p->image, p->image_bytes);
    if (rxf_activate_check(block, p->image_bytes))
        return RXF_WRAP_ANSWER_ACTIVATION;
    entry.object_id = p->entry.object_id;
    entry.generation = p->entry.generation;
    entry.offset = p->entry.offset;
    run = (rxf_wrap_run_fn)(unsigned long long)p->run;
    /* The entry of the starter takes no arguments (the application.main shape); its
     * result is the word at the output address the package names. */
    result->out = 0;
    result->status =
        run((rxf_runtime_context *)block, entry, p->args, 0, (rxf_u64 *)(unsigned long long)p->out);
    result->out = *(const rxf_u64 *)(unsigned long long)p->out;
    return RXF_WRAP_ANSWER_OK;
}

static void hex(char *at, rxf_u64 value, rxf_u32 digits) {
    static const char table[] = "0123456789abcdef";
    while (digits--) {
        at[digits] = table[value & 0xF];
        value >>= 4;
    }
}

rxf_u32 rxf_wrap_report_line(char *report, const rxf_wrap_result *result) {
    static const char head[] = "rxf result ";
    rxf_u32 i;

    for (i = 0; i < sizeof head - 1; i++)
        report[i] = head[i];
    hex(report + 11, result->status, 8);
    report[19] = ' ';
    hex(report + 20, result->out, 16);
    report[36] = '\n';
    report[37] = 0;
    return 37;
}
