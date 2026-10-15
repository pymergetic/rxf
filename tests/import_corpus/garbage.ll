; A `.ll` the subset cannot take (docs/22: what passes is decided by the subset of
; operations the importer lowers, never the language the front end came from). This
; body's floating-point add has no word of the graph to become: the words are integers
; and the leaves are the integer leaves. The import refuses it by name at import time
; (test_a_ll_outside_the_subset_is_refused) -- never a guess and never a wrong word.
define i64 @garbage_float(i64 %a, i64 %b) {
entry:
  %wrong = fadd double 1.0, 2.0
  ret i64 %a
}
