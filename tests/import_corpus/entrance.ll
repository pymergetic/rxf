; The import entrance format is textual LLVM IR (docs/22): this file is read as it
; stands and no compiler runs on it. It is the IR of tests/import_corpus/entrance.c
; written out by hand -- the same five instructions clang -O1 emits for that body (the
; min of the select idiom is the umin intrinsic of that output). test_the_ll_entrance_
; is_read_as_is pins that this door and the .c door lower to the byte-identical plan,
; and that no line of this text rides into the plan (the IR is ephemeral).
define i64 @entrance_mix(i64 %a, i64 %b) {
  %mixed = xor i64 %b, %a
  %scaled = mul i64 %a, 3
  %sum = add i64 %mixed, %scaled
  %answer = tail call i64 @llvm.umin.i64(i64 %sum, i64 %b)
  ret i64 %answer
}

declare i64 @llvm.umin.i64(i64, i64)
