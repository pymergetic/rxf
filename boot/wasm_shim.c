/* boot/wasm_shim.c -- the wasm32 seat's trampoline.
 *
 * Every seat has one piece of glue that adapts the host's calling reality to
 * the image's entry: x86_64's BIOS trampoline builds the System V stack,
 * aarch64's builds the AAPCS64 register picture, and wasm32 exports the entry
 * under the name its host calls (`run`, the name tests/graph_world.py's node
 * harness uses) and exports the map the host needs to establish the world_v7
 * memory layout in linear memory.
 *
 * The image travels as bytes (build/image_wasm32.bin, packed by
 * tools/native/boot_image.py from the linked image and the carrier rows of
 * world_v7.h); the host writes them into linear memory at the map's base. This
 * file owns no world symbols. The host does what the firmware trampolines do
 * for the machine: it copies the handle words to the by-address-pass copy (wasm
 * passes a struct over 16 bytes as a pointer to a caller-made copy, AAPCS64
 * style -- the harness's HANDLE_COPY step) and calls
 * run(i32, i32, i64, i64, i32), the exact signature tests/graph_world.py's
 * node harness calls.
 */
#include <stdbool.h>

#include "stdlib_runtime.h"
#include "uart.h"
#include "world_v7.h"

rxf_u32 rxf_graph_run(rxf_runtime_context *, rxf_object_handle, rxf_u64, rxf_u64, rxf_u64 *);

/* The copy of the handle the host passes by address (the entry convention's
 * HANDLE_COPY, outside the image exactly as the harness keeps it).
 * .bss: the wasm module's memory starts zeroed by construction, so this seat's
 * "BSS zeroing" is the format's, not code's. */
static rxf_u64 handle_copy[4] __attribute__((section(".bss.copy")));

/* The map the host reads to set the layout up: seven 32-bit words of layout,
 * in linear memory. The world rows are the map's rows at the base (world_v7.h
 * is the one home); the copy slot is this module's own. */
struct rxf_boot_map {
    rxf_u32 base, context, handle, handle_copy, args, argc, out;
};

#define WIMG ((rxf_u32)RXF_BOOT_WASM_IMAGE_BASE)

static struct rxf_boot_map map __attribute__((section(".data.map"))) = {
    WIMG,
    WIMG + RXF_BOOT_CONTEXT,
    WIMG + RXF_BOOT_HANDLE_COPY,
    (rxf_u32)(unsigned long)handle_copy,
    WIMG + RXF_BOOT_ARGS,
    RXF_BOOT_ARGC,
    WIMG + RXF_BOOT_OUT,
};

__attribute__((export_name("map_at"))) rxf_u32 rxf_boot_map_at(void) {
    return (rxf_u32)(unsigned long)&map;
}

/* The Uart device block the host serves (docs/32 3.1): the one the linked
 * image's capability names (link.LiveSpec's "uart" region), not a row of the
 * carrier. The host calls this after it wrote the image -- the capability
 * table is the image's own -- and the walk is the entry's
 * (image_stub/entry.c): one capability model, two callers. */
__attribute__((export_name("fifo_at"))) rxf_u32 rxf_boot_fifo_at(void) {
    const rxf_native_slot *cap =
        rxf_boot_uart_capability((rxf_runtime_context *)(unsigned long)map.context);
    return cap ? (rxf_u32)cap->address : 0;
}

/* The seat's growth fill (docs/32 3.1): wasm grows with memory.grow, with an
 * initial and a maximum. The shape is Memory.commit's (core/arena/arena.hpp):
 * the end of the bytes the arena wants materialized, answered true when linear
 * memory covers them now. The host's harness calls it as its prove. */
__attribute__((export_name("grow"))) bool rxf_boot_wasm_grow(void *ctx, rxf_u64 committed) {
    (void)ctx;
    const rxf_u64 have = (rxf_u64)__builtin_wasm_memory_size(0) * 65536u;
    if (committed <= have)
        return true;
    const rxf_u64 pages = (committed - have + 65535u) / 65536u;
    return __builtin_wasm_memory_grow(0, (unsigned long)pages) != (unsigned long)-1;
}

/* The image's entry under the name the host calls. The export's wasm type
 * (i32, i32, i64, i64, i32) -> i32 is spelled with scalars so it cannot move
 * with a compiler's struct-lowering rules; the 24-byte handle is rebuilt from
 * the host's copy on the way in. */
__attribute__((export_name("run"))) rxf_u32 run(rxf_u32 context, rxf_u32 handle_copy_at,
                                                rxf_u64 args, rxf_u64 argc, rxf_u32 out) {
    const rxf_u64 *words = (const rxf_u64 *)(unsigned long)handle_copy_at;
    rxf_object_handle program = {words[0], words[1], words[2]};
    return rxf_graph_run((rxf_runtime_context *)(unsigned long)context, program, args, argc,
                         (rxf_u64 *)(unsigned long)out);
}
