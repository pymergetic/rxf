/* The C -> RXF import corpus: control flow and select. clang folds if/else chains
 * to select and llvm.umin/umax/smin here; the importer lowers all of them to the
 * Branch + join-parameter pattern (a select has no opcode of its own).
 */

#include <stdint.h>

uint64_t abs_diff(uint64_t a, uint64_t b) {
    if (a < b) return b - a;
    return a - b;
}

uint64_t clamp64(uint64_t x, uint64_t lo, uint64_t hi) {
    if (x < lo) return lo;
    if (x > hi) return hi;
    return x;
}

uint64_t min64(uint64_t a, uint64_t b) { return a < b ? a : b; }
uint64_t max64(uint64_t a, uint64_t b) { return a < b ? b : a; }
uint64_t signed_min(int64_t a, int64_t b) { return a < b ? a : b; }
uint64_t signed_max(int64_t a, int64_t b) { return a < b ? b : a; } /* llvm.smax */
