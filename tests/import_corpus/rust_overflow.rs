/* Rust overflowing arithmetic: `overflowing_add` / `overflowing_sub` lower at opt-level 0
   to the add/sub + compare shape (the llvm.*.with.overflow contract), with the { i64, i1 }
   pair built by insertvalue and read back by extractvalue. The overflow flag is folded
   into the answer branchlessly (a mask), so no branch join reaches memory: the subset
   keeps such a join in one block (a next step promotes the joins to SSA). */

#[no_mangle]
pub extern "C" fn r_uadd_ovf(a: u64, b: u64) -> u64 {
    let (s, o) = a.overflowing_add(b);
    s ^ (0u64.wrapping_sub(o as u64) & 0x8000_0000_0000_0000u64)
}

#[no_mangle]
pub extern "C" fn r_usub_ovf(a: u64, b: u64) -> u64 {
    let (s, o) = a.overflowing_sub(b);
    s ^ (0u64.wrapping_sub(o as u64) & 0x8000_0000_0000_0000u64)
}

#[no_mangle]
pub extern "C" fn r_sadd_ovf(a: i64, b: i64) -> u64 {
    let (s, o) = a.overflowing_add(b);
    (s as u64) ^ (0u64.wrapping_sub(o as u64) & 0x8000_0000_0000_0000u64)
}
