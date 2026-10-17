/*
 * The trust anchor of a wrapper, as bytes beside its code. The build writes the anchor
 * to rxf_wrap_trust.bin in its work directory (the trust store of the deployment as
 * bytes) and assembles this file there; the assembler reads the anchor relative to its
 * own working directory. Every seat that activates an image (UEFI, ELF, and the BIOS
 * image the boot sector streams from disk) reads the same two symbols. The anchor is
 * wrapper content, never image content (docs/11).
 */
#ifdef _WIN32
    .section .rxftrst, "dr"
#else
    .section .rxftrst, "a"
#endif
    .global rxf_wrap_trust_start
    .global rxf_wrap_trust_end
rxf_wrap_trust_start:
    .incbin "rxf_wrap_trust.bin"
rxf_wrap_trust_end:
