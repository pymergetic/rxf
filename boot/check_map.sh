#!/bin/sh
# boot/check_map.sh -- the map's carriers must agree, or the smoke proves a map
# nothing boots with.  The home is image_stub/world.h; x86_64.S and aarch64.S
# speak it in .equ rows, and the linked images in symbols (image.ld places the
# rows).  This script compares all three, per row.
set -u
cd "$(dirname "$0")"

fail=0

same() {
    want=$1 got=$2 what=$3
    if [ -n "$want" ] && [ -n "$got" ]; then
        # numeric form: 0x0000 and 0x0 are the same row
        want=$(printf '0x%x' $((want)))
        got=$(printf '0x%x' $((got)))
    fi
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
        echo "check-map: ok   $what = $want"
    else
        echo "check-map: FAIL $what: got '$got', want '$want'"
        fail=1
    fi
}

# The row of world.h, as written (0x8800u -> 0x8800; trailing comments ignored).
hdr() {
    awk -v n="$1" '$1 == "#define" && $2 == n { v = $3; sub(/[uU]$/, "", v); print v }' \
        image_stub/world.h
}

# The row of an asm file, relative to its base (.equ NAME, IMAGE_BASE + 0xNNN).
equ() {
    sed -n "s/^[[:space:]]*\\.equ $2, IMAGE_BASE + \\([0-9A-Fa-fx]*\\).*/\\1/p" "$1"
}

# A scalar of an asm file (.equ NAME, 0xNNN).
equ0() {
    sed -n "s/^[[:space:]]*\\.equ $2, \\([0-9A-Fa-fx]*\\).*/\\1/p" "$1"
}

# The row as the linked image places it: the symbol's address minus the base.
nmsym() {
    addr=$(nm -n "$1" | awk -v s="$2" '$3 == s { print $1; exit }')
    [ -n "$addr" ] || { echo ""; return; }
    printf '0x%x' $((0x$addr - $3))
}

for asm in x86_64.S aarch64.S; do
    same "$(hdr RXF_BOOT_ENTRY)" "$(equ $asm ENTRY)" "$asm ENTRY"
    same "$(hdr RXF_BOOT_CONTEXT)" "$(equ $asm CONTEXT)" "$asm CONTEXT"
    same "$(hdr RXF_BOOT_ARGS)" "$(equ $asm ARGS)" "$asm ARGS"
    same "$(hdr RXF_BOOT_OUT)" "$(equ $asm OUT)" "$asm OUT"
    same "$(hdr RXF_BOOT_HANDLE_COPY)" "$(equ $asm HANDLE)" "$asm HANDLE"
    same "$(hdr RXF_BOOT_FRAME)" "$(equ $asm BSS_START)" "$asm BSS_START"
    same "$(hdr RXF_BOOT_BSS_END)" "$(equ $asm BSS_END)" "$asm BSS_END"
    same "$(hdr RXF_BOOT_ARGC)" "$(equ0 $asm ARGC)" "$asm ARGC"
done

for seat in x86_64 aarch64 wasm32; do
    case $seat in
    x86_64 | wasm32) base=0x10000 ;;
    aarch64) base=0x40100000 ;;
    esac
    elf=build/image_$seat.elf
    [ -f "$elf" ] || { echo "check-map: FAIL $elf missing"; fail=1; continue; }
    same "$(hdr RXF_BOOT_CONTEXT)" "$(nmsym $elf rxf_boot_context $base)" "$seat rxf_boot_context"
    same "$(hdr RXF_BOOT_PROGRAM)" "$(nmsym $elf rxf_boot_program $base)" "$seat rxf_boot_program"
    same "$(hdr RXF_BOOT_ARGS)" "$(nmsym $elf rxf_boot_args $base)" "$seat rxf_boot_args"
    same "$(hdr RXF_BOOT_OUT)" "$(nmsym $elf rxf_boot_out $base)" "$seat rxf_boot_out"
    same "$(hdr RXF_BOOT_TEXT)" "$(nmsym $elf rxf_boot_text $base)" "$seat rxf_boot_text"
    same "$(hdr RXF_BOOT_AREA)" "$(nmsym $elf rxf_boot_area $base)" "$seat rxf_boot_area"
    same "$(hdr RXF_BOOT_CAPABILITIES)" "$(nmsym $elf rxf_boot_capabilities $base)" "$seat rxf_boot_capabilities"
    same "$(hdr RXF_BOOT_HEAP)" "$(nmsym $elf rxf_boot_heap $base)" "$seat rxf_boot_heap"
    same "$(hdr RXF_BOOT_HANDLE_COPY)" "$(nmsym $elf rxf_boot_handle $base)" "$seat rxf_boot_handle"
    same "$(hdr RXF_BOOT_FIFO)" "$(nmsym $elf rxf_boot_fifo $base)" "$seat rxf_boot_fifo"
    if [ "$seat" != wasm32 ]; then
        # the wasm32 world has no entry code row: its entry is the module's
        same "$(hdr RXF_BOOT_ENTRY)" "$(nmsym $elf rxf_graph_run $base)" "$seat rxf_graph_run"
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "check-map: the map's carriers disagree"
    exit 1
fi
echo "check-map: all carriers agree with image_stub/world.h"
