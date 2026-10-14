/* Outside the importer's subset on purpose: an i64 multiply has no leaf of the
 * interpreter, and tests/test_import.py asserts the importer refuses this function
 * rather than guesses. It is never imported by the differential tests.
 */

#include <stdint.h>

uint64_t squared(uint64_t x) { return x * x; }
