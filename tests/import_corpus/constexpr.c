#include <stdint.h>

/* Integer arithmetic over the address of a static, folded by the compiler into a constant
   expression operand: LWIP_MEM_ALIGN(ram_heap) is ((addr + 7) & ~7), and clang writes the
   inner sums as ``sub (i64 add (i64 ptrtoint (ptr @heap to i64), i64 8), i64 1)`` in the
   operand of the final ``and``. The address of the cell is known only where the run places it,
   so the importer makes one wrapping leaf call per operator; the two sides keep the heap at
   addresses of their own, so the answers are the properties that hold for any address (the
   result is aligned, is at most one alignment unit past its base, lies inside the array),
   never the address itself. */

static uint8_t heap[256];

#define ALIGN(address, unit) (((address) + ((unit) - 1)) & ~(uintptr_t)((unit) - 1))

uint64_t align8_flags(uint64_t k) {
    uintptr_t base = (uintptr_t)&heap[3];
    uintptr_t aligned = ALIGN(base, 8);
    return (uint64_t)(aligned - base < 8) | ((uint64_t)((aligned & 7) == 0) << 1) |
           ((uint64_t)(k & 1) << 2);
}

uint64_t align16_flags(uint64_t k) {
    uintptr_t aligned = ALIGN((uintptr_t)heap + 40, 16);
    return (uint64_t)((aligned & 15) == 0) | ((uint64_t)(aligned - (uintptr_t)heap >= 40) << 1) |
           ((uint64_t)(aligned - (uintptr_t)heap < 56) << 2) | ((uint64_t)(k & 1) << 3);
}

/* the shape of lwIP's mem_init: the aligned start, then the span it leaves from the end */
uint64_t span_flags(uint64_t k) {
    uintptr_t start = ALIGN((uintptr_t)heap + 8, 8);
    uintptr_t end = (uintptr_t)heap + sizeof(heap);
    return (uint64_t)(end - start <= sizeof(heap) - 8) |
           ((uint64_t)(end - start >= sizeof(heap) - 16) << 1) | ((uint64_t)(k & 1) << 2);
}
