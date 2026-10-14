/* A module global as an arena cell: the C static is one named data object of the import
   unit (the cell), and a graph's loads and stores of it are flat loads and stores at the
   cell's payload address. The differential's native side keeps the same static storage:
   one cell that survives between calls, so the sequence of answers compares the state
   evolution of both sides, not one call alone. */
#include <stdint.h>

static uint64_t counter = 7; /* the cell: its initial payload is the 7 the runs start with */

uint64_t counter_add(uint64_t x) {
    counter += x;
    return counter;
}

uint64_t counter_get(void) {
    return counter;
}

uint64_t counter_set(uint64_t x) {
    counter = x;
    return counter;
}

uint64_t counter_at(void) {
    /* the cell's address as a word: what the run resolves the Object operand to */
    return (uint64_t)(uintptr_t)&counter;
}
