/*
 * The package of a wrapper, as bytes beside its code. The build writes the package to
 * rxf_wrap_package.bin in its work directory and assembles this file there; the
 * assembler reads the package relative to its own working directory. Every seat that
 * embeds a package (UEFI, ELF, and the BIOS image the boot sector streams from disk)
 * reads the same two symbols.
 */
#ifdef _WIN32
    .section .rxfpkg, "dr"
#else
    .section .rxfpkg, "a"
#endif
    .global rxf_wrap_package_start
    .global rxf_wrap_package_end
rxf_wrap_package_start:
    .incbin "rxf_wrap_package.bin"
rxf_wrap_package_end:
