//! Compiles the C core (`rxfb/rxfb.c`) into the crate. The core is the one implementation of the
//! reader: this crate only declares it, and the safe crate wraps it. Nothing of the reader is
//! written again in Rust.

use std::path::PathBuf;

fn main() {
    let manifest = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("cargo sets it"));
    // rxfb/rust/rxfb-sys -> rxfb
    let core = manifest.join("..").join("..");
    let source = core.join("rxfb.c");

    cc::Build::new()
        .file(&source)
        .include(&core)
        .std("c11")
        // The core is freestanding: no libc symbol, no startup, no allocation.
        .flag("-ffreestanding")
        .flag("-fno-builtin")
        .flag("-Wall")
        .flag("-Wextra")
        .flag("-Werror")
        .opt_level(2)
        .pic(true)
        .compile("rxfb");

    for name in ["rxfb.c", "rxfb.h", "rxfb_layout.h"] {
        println!("cargo:rerun-if-changed={}", core.join(name).display());
    }
    println!("cargo:rerun-if-changed=build.rs");
}
