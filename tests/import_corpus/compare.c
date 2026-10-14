/* The C -> RXF import corpus: every icmp predicate of the subset (the signed ones
 * through the a + 2^63 of the importer), returned as a 64-bit 0/1.
 */

#include <stdint.h>

uint64_t cmp_eq(uint64_t a, uint64_t b) { return a == b; }
uint64_t cmp_ne(uint64_t a, uint64_t b) { return a != b; }
uint64_t cmp_ult(uint64_t a, uint64_t b) { return a < b; }
uint64_t cmp_uge(uint64_t a, uint64_t b) { return a >= b; }
uint64_t cmp_slt(int64_t a, int64_t b) { return a < b; }
uint64_t cmp_sle(int64_t a, int64_t b) { return a <= b; }
