#!/bin/sh
# boot/check_map.sh -- the maps' carriers must agree, or the smoke proves a map
# nothing boots with.  The homes are image_stub/world.h (the stub world the
# aarch64, wasm32 and UEFI seats still carry) and image_stub/world_v7.h (the
# v7 map the x86_64-bios seat boots the linked image with); the asm files speak
# them in .equ rows and the linked images in symbols (the ld scripts place the
# rows).  This script compares each carrier against its map, per row.
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

# The row of a map header, as written (0x8800u -> 0x8800; trailing comments ignored).
hdr() {
    awk -v n="$2" '$1 == "#define" && $2 == n { v = $3; sub(/[uUlL]+$/, "", v); print v }' \
        "$1"
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

# The stub map: the aarch64 glue and the three stub worlds (world.h).
WORLD=image_stub/world.h
for asm in aarch64.S; do
    same "$(hdr $WORLD RXF_BOOT_ENTRY)" "$(equ $asm ENTRY)" "$asm ENTRY"
    same "$(hdr $WORLD RXF_BOOT_CONTEXT)" "$(equ $asm CONTEXT)" "$asm CONTEXT"
    same "$(hdr $WORLD RXF_BOOT_ARGS)" "$(equ $asm ARGS)" "$asm ARGS"
    same "$(hdr $WORLD RXF_BOOT_OUT)" "$(equ $asm OUT)" "$asm OUT"
    same "$(hdr $WORLD RXF_BOOT_HANDLE_COPY)" "$(equ $asm HANDLE)" "$asm HANDLE"
    same "$(hdr $WORLD RXF_BOOT_FRAME)" "$(equ $asm BSS_START)" "$asm BSS_START"
    same "$(hdr $WORLD RXF_BOOT_BSS_END)" "$(equ $asm BSS_END)" "$asm BSS_END"
    same "$(hdr $WORLD RXF_BOOT_ARGC)" "$(equ0 $asm ARGC)" "$asm ARGC"
done

for seat in x86_64 aarch64 wasm32; do
    case $seat in
    x86_64 | wasm32) base=0x10000 ;;
    aarch64) base=0x40100000 ;;
    esac
    elf=build/image_$seat.elf
    [ -f "$elf" ] || { echo "check-map: FAIL $elf missing"; fail=1; continue; }
    same "$(hdr $WORLD RXF_BOOT_CONTEXT)" "$(nmsym $elf rxf_boot_context $base)" "$seat rxf_boot_context"
    same "$(hdr $WORLD RXF_BOOT_PROGRAM)" "$(nmsym $elf rxf_boot_program $base)" "$seat rxf_boot_program"
    same "$(hdr $WORLD RXF_BOOT_ARGS)" "$(nmsym $elf rxf_boot_args $base)" "$seat rxf_boot_args"
    same "$(hdr $WORLD RXF_BOOT_OUT)" "$(nmsym $elf rxf_boot_out $base)" "$seat rxf_boot_out"
    same "$(hdr $WORLD RXF_BOOT_TEXT)" "$(nmsym $elf rxf_boot_text $base)" "$seat rxf_boot_text"
    same "$(hdr $WORLD RXF_BOOT_AREA)" "$(nmsym $elf rxf_boot_area $base)" "$seat rxf_boot_area"
    same "$(hdr $WORLD RXF_BOOT_CAPABILITIES)" "$(nmsym $elf rxf_boot_capabilities $base)" "$seat rxf_boot_capabilities"
    same "$(hdr $WORLD RXF_BOOT_HEAP)" "$(nmsym $elf rxf_boot_heap $base)" "$seat rxf_boot_heap"
    same "$(hdr $WORLD RXF_BOOT_HANDLE_COPY)" "$(nmsym $elf rxf_boot_handle $base)" "$seat rxf_boot_handle"
    same "$(hdr $WORLD RXF_BOOT_FIFO)" "$(nmsym $elf rxf_boot_fifo $base)" "$seat rxf_boot_fifo"
    if [ "$seat" != wasm32 ]; then
        # the wasm32 world has no entry code row: its entry is the module's
        same "$(hdr $WORLD RXF_BOOT_ENTRY)" "$(nmsym $elf rxf_graph_run $base)" "$seat rxf_graph_run"
    fi
done

# The v7 map: the x86_64 glue (x86_64.S) and the entry code of the linked
# image (entry_v7.ld places it at the map's ENTRY row).  The loader's chunk is
# the map's too: the sector reads CHUNK_SECTORS sectors per EDD request, and
# one chunk is one read (world_v7.h's CHUNK).
V7=image_stub/world_v7.h
same "$(hdr $V7 RXF_BOOT_ENTRY)" "$(equ x86_64.S ENTRY)" "x86_64.S ENTRY"
same "$(hdr $V7 RXF_BOOT_CONTEXT)" "$(equ x86_64.S CONTEXT)" "x86_64.S CONTEXT"
same "$(hdr $V7 RXF_BOOT_ARGS)" "$(equ x86_64.S ARGS)" "x86_64.S ARGS"
same "$(hdr $V7 RXF_BOOT_OUT)" "$(equ x86_64.S OUT)" "x86_64.S OUT"
same "$(hdr $V7 RXF_BOOT_HANDLE_COPY)" "$(equ x86_64.S HANDLE)" "x86_64.S HANDLE"
same "$(hdr $V7 RXF_BOOT_FRAME)" "$(equ x86_64.S BSS_START)" "x86_64.S BSS_START"
same "$(hdr $V7 RXF_BOOT_BSS_END)" "$(equ x86_64.S BSS_END)" "x86_64.S BSS_END"
same "$(hdr $V7 RXF_BOOT_ARGC)" "$(equ0 x86_64.S ARGC)" "x86_64.S ARGC"
chunk=$(equ0 x86_64.S CHUNK_SECTORS)
if [ -n "$chunk" ]; then
    same "$(hdr $V7 RXF_BOOT_CHUNK)" "$((chunk * 512))" "x86_64.S CHUNK_SECTORS * 512"
fi
elf=build/entry_v7_x86_64.elf
if [ -f "$elf" ]; then
    same "$(hdr $V7 RXF_BOOT_ENTRY)" \
        "$(nmsym $elf rxf_graph_run "$(hdr $V7 RXF_BOOT_X86_IMAGE_BASE)")" \
        "v7 entry rxf_graph_run"
else
    echo "check-map: FAIL $elf missing"
    fail=1
fi

if [ "$fail" -ne 0 ]; then
    echo "check-map: the map's carriers disagree"
    exit 1
fi
echo "check-map: all carriers agree with their map"
