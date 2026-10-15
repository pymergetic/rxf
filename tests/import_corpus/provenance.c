#include <stdint.h>

/* tests/import_corpus/provenance.c -- the comment-lifting fixture (docs/22).
 *
 * This file comment is free-floating: rximport lifts it onto the module the file
 * imports to. The comments at the definitions ride onto the functions they
 * precede, and a task marker is its own managed kind (DocKind.Todo). The bodies
 * are plain wrapping arithmetic: the subset imports them and nothing here runs.
 */

// provenance_add adds its two words (a comment at a definition).
uint64_t provenance_add(uint64_t a, uint64_t b) { return a + b; }

// FIXME: provenance_xor is all this file proves so far; the checked form waits.
uint64_t provenance_xor(uint64_t a, uint64_t b) { return a ^ b; }
