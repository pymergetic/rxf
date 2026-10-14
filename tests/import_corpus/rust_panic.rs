/* Rust panic paths: the panic call of core/std::panicking diverges. The importer lowers
   the call to nothing and the unreachable after it to the run's Refuse
   (RXF_REFUSE_UNSUPPORTED): the run refuses on the path that would panic instead of
   unwinding (the status-branch idiom). The corpus asserts both faces: `r_panic_if_zero`
   answers the same as the native code for its non-panicking inputs and refuses the run at
   zero, and `r_always_panics` refuses every run. */

#[no_mangle]
pub extern "C" fn r_panic_if_zero(x: u64) -> u64 {
    if x == 0 {
        panic!("zero");
    }
    x.wrapping_add(3)
}

#[no_mangle]
pub extern "C" fn r_always_panics(x: u64) -> u64 {
    let _ = x;
    panic!("no")
}
