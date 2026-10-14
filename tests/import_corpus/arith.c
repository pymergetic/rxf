/* The C -> RXF import corpus: i64 arithmetic and bitwise, one LLVM opcode each.
 *
 * Every function is deterministic C without undefined behavior on the inputs of
 * tests/test_import.py (the shift counts it feeds stay below 64). The contract of
 * tools/native/rximport.py says the imported graph answers the same (status, value)
 * as this same C compiled natively; the tests compare exactly that.
 */

#include <stdint.h>

uint64_t wrapping_add(uint64_t a, uint64_t b) { return a + b; }
uint64_t wrapping_sub(uint64_t a, uint64_t b) { return a - b; }
uint64_t bit_and(uint64_t a, uint64_t b) { return a & b; }
uint64_t bit_or(uint64_t a, uint64_t b) { return a | b; }
uint64_t bit_not(uint64_t a) { return ~a; }
uint64_t xor_mix(uint64_t a, uint64_t b) { return a ^ b; }
uint64_t shift_left(uint64_t a, uint64_t n) { return a << n; }
uint64_t shift_right(uint64_t a, uint64_t n) { return a >> n; }
