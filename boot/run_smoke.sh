#!/bin/sh
# boot/run_smoke.sh -- build the glue and boot every seat, asserting the magic.
#
# The success criterion of the whole exercise: the guest hands control to the
# image's entry (boot/README.md's four jobs), the image's program answers the
# constant word the glue passed, and the seat's Uart capability emits:
#
#     RXFOK
#     rxf result 00000000 0000004b4f584652
#
# which the host asserts byte for byte.  The coarse exit agrees: the x86_64
# seats answer on the magic port (QEMU isa-debug-exit: exit code 33), the
# aarch64 seat answers with PSCI SYSTEM_OFF, the wasm seat with a status.
#
# Usage: sh boot/run_smoke.sh        (builds first; every seat is proved)
set -u
cd "$(dirname "$0")"

MAGIC="RXFOK"
RESULT="rxf result 00000000 0000004b4f465852"
LOG=build/smoke.log

make --no-print-directory all check-map || exit 1

fail=0

# seat_name log want_code command...
seat() {
    name=$1 log=$2 want=$3
    shift 3
    timeout 120 "$@" >"$log" 2>&1
    code=$?
    ok=1
    if [ -n "$want" ] && [ "$code" != "$want" ]; then
        echo "$name: FAIL exit code $code, wanted $want"
        ok=0
    fi
    if ! grep -q "$MAGIC" "$log"; then
        echo "$name: FAIL serial never said $MAGIC"
        ok=0
    fi
    if ! grep -q "$RESULT" "$log"; then
        echo "$name: FAIL serial never said '$RESULT'"
        ok=0
    fi
    if [ "$ok" = 1 ]; then
        echo "$name: PASS -- $(grep -m1 "$MAGIC" "$log") / $(grep -m1 "$RESULT" "$log")"
    else
        echo "$name: ---- serial follows ----"
        cat "$log"
        echo "$name: ---- end serial ----"
        fail=1
    fi
}

echo "== x86_64 BIOS (disk boot: sector -> stage2 -> long mode -> image) =="
if command -v qemu-system-x86_64 >/dev/null 2>&1; then
    # 33 = (MAGIC_OK 0x10 << 1) | 1: the guest's magic-port answer.
    # kvm first, tcg second (the guest is our own kernel either way).
    seat "x86_64-bios" "$LOG.x86" 33 \
        qemu-system-x86_64 -machine pc \
        -accel kvm -accel tcg \
        -drive format=raw,file=build/disk.img \
        -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
        -serial stdio -display none
else
    echo "x86_64-bios: SKIP (qemu-system-x86_64 not installed)"
fi

echo "== aarch64 QEMU virt (EL2 -> EL1 drop, VBAR_EL1, PSCI off) =="
if command -v qemu-system-aarch64 >/dev/null 2>&1; then
    # virtualization=on so the guest starts in EL2 and the drop is on the path;
    # the exit code is PSCI SYSTEM_OFF's (0): this seat has no magic port.
    # kvm first, tcg second: on an x86 host the aarch64 KVM init fails and QEMU
    # takes the next -accel itself.
    seat "aarch64-virt" "$LOG.a64" 0 \
        qemu-system-aarch64 -machine virt,virtualization=on \
        -accel kvm -accel tcg \
        -cpu cortex-a72 -m 64M -kernel build/boot_aarch64.elf \
        -serial stdio -display none
else
    echo "aarch64-virt: SKIP (qemu-system-aarch64 not installed)"
fi

echo "== wasm32 (node host: map -> handle copy -> run -> FIFO drain) =="
if command -v node >/dev/null 2>&1; then
    seat "wasm32-node" "$LOG.wasm" 0 \
        node wasm_shim.js build/rxf_boot.wasm build/image_wasm32.bin
else
    echo "wasm32-node: SKIP (node not installed)"
fi

echo "== x86_64 UEFI (OVMF, PE stub, EFI file protocol) =="
if command -v qemu-system-x86_64 >/dev/null 2>&1 && [ -f /usr/share/OVMF/OVMF_CODE_4M.fd ]; then
    # kvm first, tcg second (the guest is our own kernel either way).
    seat "x86_64-uefi" "$LOG.uefi" 33 \
        qemu-system-x86_64 -machine q35 \
        -accel kvm -accel tcg \
        -drive if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd \
        -drive format=raw,file=fat:rw:build/uefi_root \
        -device isa-debug-exit,iobase=0xf4,iosize=0x04 \
        -serial stdio -display none
else
    echo "x86_64-uefi: SKIP (qemu-system-x86_64 or OVMF not installed)"
fi

if [ "$fail" -ne 0 ]; then
    echo "boot smoke: FAIL"
    exit 1
fi
echo "boot smoke: PASS"
