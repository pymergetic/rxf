/* tests/import_corpus/dispatch.c -- the pin of the function-word rule.

   A call through a function word the run carries: the body dispatches on a word its
   caller passes, and no store of this import wires a word (no boot fills a slot here).
   The card model names its callee when the words are made (the store that wires the
   slot is what names the body), so a dispatch whose words no store of this import
   carries cannot name one: the import refuses this body with that precise rule --
   tests/test_import.py's REFUSED records it (the same entry the vtable of
   cpp_refuse.cpp and the unwired slot of obj.c's mp_obj_is_true get). */

typedef int (*word_fn_t)(int);

int call_a_word(word_fn_t fn, int x) {
    return fn(x);
}
