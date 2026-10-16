/*
 * The activation seam (native/wrap/wrap.h). The certification lane
 * (src/pymergetic/rxftk/certify) fills this in with the checks an image passes before it
 * is entered; here it is the pass-through, and it stays one named function so the two
 * lanes never meet anywhere else.
 */
#include "wrap.h"

rxf_u32 rxf_activate_check(const void *block, rxf_u64 block_bytes) {
    (void)block;
    (void)block_bytes;
    return 0;
}
