/* The external ground-truth link of global_reloc.c, the way nlr_stubs.c and obj_stubs.c
   carry theirs: global_reloc.c only *declares* elsewhere (a declaration without bytes,
   which the importer refuses at the use -- tests/test_import.py's REFUSED), and the
   native differential still has to load and run, so the definition the real link of
   this translation unit would carry lives here. The importer never reads this file: the
   refusal of use_decl stays honest on the graph side, while the native side answers the
   number this definition carries. */
#include <stdint.h>

uint64_t elsewhere = 41;
