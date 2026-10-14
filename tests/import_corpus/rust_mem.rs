/* Rust through flat memory: raw pointers of the run's space. Each dereference at
   opt-level 0 carries the null (and alignment) guard of rustc -- the panic path lowers
   to the run's Refuse (see the docstring of tools/native/rximport.py) and the inputs of
   the corpus never reach it (the addresses the differential passes are aligned and
   non-null). */

#[no_mangle]
pub extern "C" fn r_store8(p: *mut u8, v: u64) -> u64 {
    unsafe {
        *p = v as u8;
        (*p) as u64
    }
}

#[no_mangle]
pub extern "C" fn r_load32(p: *const u32) -> u64 {
    unsafe { (*p) as u64 }
}

#[no_mangle]
pub extern "C" fn r_idx8(p: *mut u8, i: u64, v: u64) -> u64 {
    unsafe {
        *p.add((i & 7) as usize) = v as u8;
        (*p.add((i & 7) as usize)) as u64
    }
}
