#include <stdint.h>

/* Several cases of one switch reach one block: LLVM lists one phi entry per edge, so the
   phi of the destination names the same predecessor once per case, and every repeat
   carries the same value. The importer reads one value per predecessor block (as the phi
   does at run time) instead of one per edge. */

uint64_t dupedge(uint64_t x, uint64_t v) {
    uint64_t y;
    if (x == 100) {
        y = v + 1;
        goto out;
    }
    y = v + 5;
    switch (x) {
        case 1:
        case 2:
        case 3:
            goto out;
        default:
            y = v + 7;
            goto out;
    }
out:
    return y + 1;
}
