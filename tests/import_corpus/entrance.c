#include <stdint.h>

/* The compiler door of the `.ll` entrance (docs/22): this body and entrance.ll are the
   same program -- entrance.ll is the raw textual LLVM IR of it, written out as the
   importer reads IR. The importer must lower both to the byte-identical plan
   (test_the_ll_entrance_is_read_as_is): the language suffix is convenience, the IR text
   is the entrance format, and the IR itself stays ephemeral -- never in the image. */
uint64_t entrance_mix(uint64_t a, uint64_t b) {
    uint64_t mixed = (a ^ b) + (a * 3);
    return mixed < b ? mixed : b;
}
