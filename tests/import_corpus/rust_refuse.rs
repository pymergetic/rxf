/* Rust outside the subset, on purpose: each function names one refusal of the importer
   (asserted in test_outside_the_subset_is_refused). Nothing here is imported. */

/* the join of the two arms is a local kept in memory at opt-level 0 (rustc spills the
   result of a branch to the stack): the subset refuses the read across blocks rather
   than read the wrong store (promoting such joins to SSA is a next step) */
#[no_mangle]
pub extern "C" fn r_join(a: u64, b: u64) -> u64 {
    let t = if a < b { a.wrapping_add(1) } else { b.wrapping_add(1) };
    t.wrapping_add(1)
}

/* an array local is addressed by getelementptr of its alloca: the pass does no pointer
   arithmetic on a local's own bytes (a flat address is the only base it walks) */
#[no_mangle]
pub extern "C" fn r_array(i: u64) -> u64 {
    let a = [1u64, 2, 3];
    a[(i & 2) as usize]
}
