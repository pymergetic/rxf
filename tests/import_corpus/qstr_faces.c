/* The libc faces of the qstr core -- strlen, strncmp and memcmp -- as their own
   translation unit of the import compile (tools/native/rximport.py's compile_c defines
   RXF_IMPORT_FACES; qstr.c maps the three names onto these bodies and includes this
   file).  The subset imports no libc: a call of strlen would refuse ("only leaves and
   functions of the module") and the leaf table is another lane's tree, so each face
   gets an IR body instead of a new leaf.

   memcmp is the one face with an exact leaf already: the memory_compare of
   native/stdlib_memory.c (the memory slice's runtime service) answers the sign of the
   first difference -- which is the whole C contract of memcmp (only the sign of a
   nonzero result is specified), so the body is one leaf call and the frame the leaf
   needs, not a loop.  strlen and strncmp have no leaf of that shape (a string length
   and a NUL-stopping compare are the recorded need for the leaf lane): their bodies are
   the plain loops of the standard's definition.

   The native side of the differential does NOT use these bodies: without
   RXF_IMPORT_FACES the same calls bind to libc, so the gate compares the imported
   graphs against the real thing -- the memcmp leaf above is what the graph and the
   native binary share only through its own differential.  noinline keeps the loops as
   loops at -O1 (an inlined scan can be recognized as the very libc call it
   implements); the importer inlines the call itself, as a function of the module. */

#include <stddef.h>

#include "../../native/stdlib_memory.h"

__attribute__((noinline)) size_t qstr_face_strlen(const char *s) {
    const char *p = s;
    while (*p) {
        p++;
    }
    return (size_t)(p - s);
}

__attribute__((noinline)) int qstr_face_strncmp(const char *a, const char *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        unsigned char x = (unsigned char)a[i];
        unsigned char y = (unsigned char)b[i];
        if (x != y || x == 0) {
            return (int)x - (int)y;
        }
    }
    return 0;
}

__attribute__((noinline)) int qstr_face_memcmp(const void *a, const void *b, size_t n) {
    rxf_i32 out;
    rxf_memory_compare((const rxf_u8 *)a, n, (const rxf_u8 *)b, n, n, &out);
    return out;
}
