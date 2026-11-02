#!/bin/sh
# boot/check_map.sh -- the map's carriers must agree, or the smoke proves a map
# nothing boots with.  The one home is image_stub/world_v7.h (every seat boots
# the packed v7 image tools/native/boot_image.py makes); the asm files speak it
# in .equ rows and the entry links place the entry code at its row
# (image_stub/entry_v7.ld).  This script compares each carrier against the map,
# per row.
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

# The row of the map header, as written (0x8800u -> 0x8800; trailing comments ignored).
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

# The row as the linked entry places it: the symbol's address minus the base.
nmsym() {
    addr=$(nm -n "$1" | awk -v s="$2" '$3 == s { print $1; exit }')
    [ -n "$addr" ] || { echo ""; return; }
    printf '0x%x' $((0x$addr - $3))
}

# One map (world_v7.h), spoken by both glue files in .equ rows.
MAP=image_stub/world_v7.h
for asm in x86_64.S aarch64.S; do
    same "$(hdr $MAP RXF_BOOT_ENTRY)" "$(equ $asm ENTRY)" "$asm ENTRY"
    same "$(hdr $MAP RXF_BOOT_CONTEXT)" "$(equ $asm CONTEXT)" "$asm CONTEXT"
    same "$(hdr $MAP RXF_BOOT_ARGS)" "$(equ $asm ARGS)" "$asm ARGS"
    same "$(hdr $MAP RXF_BOOT_OUT)" "$(equ $asm OUT)" "$asm OUT"
    same "$(hdr $MAP RXF_BOOT_HANDLE_COPY)" "$(equ $asm HANDLE)" "$asm HANDLE"
    same "$(hdr $MAP RXF_BOOT_FRAME)" "$(equ $asm BSS_START)" "$asm BSS_START"
    same "$(hdr $MAP RXF_BOOT_BSS_END)" "$(equ $asm BSS_END)" "$asm BSS_END"
    same "$(hdr $MAP RXF_BOOT_ARGC)" "$(equ0 $asm ARGC)" "$asm ARGC"
done

# The loader's chunk is the map's too: the sector reads CHUNK_SECTORS sectors
# per EDD request, and one chunk is one read (world_v7.h's CHUNK).
chunk=$(equ0 x86_64.S CHUNK_SECTORS)
if [ -n "$chunk" ]; then
    same "$(hdr $MAP RXF_BOOT_CHUNK)" "$((chunk * 512))" "x86_64.S CHUNK_SECTORS * 512"
fi

# The entry links place the entry code at the map's ENTRY row (entry_v7.ld):
# one entry.c per seat, at the base that seat runs the image at.
for seat in x86_64 aarch64; do
    case $seat in
    x86_64) base=$(hdr $MAP RXF_BOOT_X86_IMAGE_BASE) ;;
    aarch64) base=$(hdr $MAP RXF_BOOT_A64_IMAGE_BASE) ;;
    esac
    elf=build/entry_v7_$seat.elf
    if [ -f "$elf" ]; then
        same "$(hdr $MAP RXF_BOOT_ENTRY)" "$(nmsym $elf rxf_graph_run "$base")" \
            "entry_v7_$seat rxf_graph_run"
    else
        echo "check-map: FAIL $elf missing"
        fail=1
    fi
done

if [ "$fail" -ne 0 ]; then
    echo "check-map: the map's carriers disagree"
    exit 1
fi
echo "check-map: all carriers agree with their map"
