/* Rust of the flat memory profile: integers, pointers and casts, as
   `#[no_mangle] pub extern "C"` functions (their names survive the mangling).

   rustc emits LLVM IR at opt-level 0 where arithmetic is plain SSA (add/icmp/casts of the
   arguments); the wrapping_* forms keep the debug-assertion panic paths out of the
   arithmetic itself. The differential runner compiles this same file to a shared library
   and calls it with ctypes, so the values of uncanonical widths are exercised on both
   sides (a u8 parameter is what the low byte of the register holds). */

#[no_mangle]
pub extern "C" fn r_add8(a: u8, b: u8) -> u64 {
    (a.wrapping_add(b)) as u64
}

#[no_mangle]
pub extern "C" fn r_sub16(a: u16, b: u16) -> u64 {
    (a.wrapping_sub(b)) as u64
}

#[no_mangle]
pub extern "C" fn r_add32(a: u32, b: u32) -> u64 {
    (a.wrapping_add(b)) as u64
}

#[no_mangle]
pub extern "C" fn r_xor64(a: u64, b: u64) -> u64 {
    a ^ b
}

/* the shift counts are masked in the source (the count at or past the width is outside
   the contract of the subset, as in the C corpus); the shift operators carry a panic
   guard for the count that lowers to the run's Refuse and the inputs never take */
#[no_mangle]
pub extern "C" fn r_shl8(a: u8, n: u32) -> u64 {
    (a << (n & 7)) as u64
}

#[no_mangle]
pub extern "C" fn r_ashr32(a: i32, n: u32) -> u64 {
    (a >> (n & 31)) as u64
}

#[no_mangle]
pub extern "C" fn r_ult8(a: u8, b: u8) -> u64 {
    (a < b) as u64
}

#[no_mangle]
pub extern "C" fn r_slt32(a: i32, b: i32) -> u64 {
    (a < b) as u64
}

#[no_mangle]
pub extern "C" fn r_zext_mix(a: u8, b: u16) -> u64 {
    ((a as u64) << 16) | (b as u64)
}
