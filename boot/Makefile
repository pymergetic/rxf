# boot/Makefile -- the boot glue per seat, one map per image (world_v7.h).
#
#   make              build every seat (x86_64 BIOS, aarch64 virt, wasm32, UEFI)
#   make check-map    prove the map's carriers agree (world_v7.h vs asm vs links)
#   make smoke        build + boot every seat and assert the magic (run_smoke.sh)
#
# Tools and why: gas `as` + `ld` assemble and link the x86_64 asm (the
# preference); clang compiles C and assembles aarch64 (no cross GNU aarch64
# binutils is installed, its integrated assembler is the gas there); wasm-ld +
# node are the wasm32 seat; clang + lld-link make the UEFI PE stub. Every seat
# boots the packed v7 image (tools/native/boot_image.py): the image
# tools/native/link.py makes of the starter (the v7 arena of docs/32) packed
# with the carrier rows of world_v7.h and this tree's entry code -- bytes,
# never source.

CLANG   := clang
AS      := as
LD      := ld
OBJCOPY := objcopy
NODE    := node
OVMF    := /usr/share/OVMF/OVMF_CODE_4M.fd
# The packer needs the image model (pymergetic): the tree's own venv when there
# is one, else the python that has it installed (CI's).
PYTHON  ?= $(shell test -x ../.venv/bin/python && echo $(abspath ../.venv/bin/python) || echo python3)

BUILD   := build
ENTRY   := image_stub/entry.c
MAP     := image_stub/world_v7.h image_stub/entry_v7.ld
INC     := -I. -Iimage_stub -I../native -I../src
CFLAGS  := -Os -ffreestanding -fno-builtin -fno-stack-protector -fno-pic \
           -fno-asynchronous-unwind-tables -Wall -Wextra $(INC)

X86_CC  := $(CLANG) --target=x86_64-unknown-none-elf
A64_CC  := $(CLANG) --target=aarch64-none-elf
WASM_CC := $(CLANG) --target=wasm32

# The bases: where each machine runs the image (world_v7.h's per-seat bases,
# `make check-map` pins the rows against them).
X86_BASE  := 0x4000000
A64_BASE  := 0x40100000
WASM_BASE := 0x10000

.PHONY: all x86 aarch64 wasm uefi check-map smoke clean
all: x86 aarch64 wasm uefi

$(BUILD):
	mkdir -p $(BUILD)

# ---- the image's entry code, at its carrier row (world_v7.h) --------------
#
# The same entry.c on every seat (ground.h and live.h are the layouts it reads
# out of the linked image, so ../src joins the includes). The x86_64 and
# aarch64 seats link it into the carrier room with entry_v7.ld and
# boot_image.py packs the bytes at the ENTRY row; the wasm32 seat links it into
# its module, whose `run` is the entry.

$(BUILD)/entry_v7_x86_64.o: $(ENTRY) image_stub/uart.h $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -I../src -c $< -o $@

$(BUILD)/uart_x86.o: image_stub/uart_x86_64.c image_stub/uart.h $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -c $< -o $@

$(BUILD)/entry_v7_x86_64.elf: $(BUILD)/entry_v7_x86_64.o $(BUILD)/uart_x86.o image_stub/entry_v7.ld
	$(LD) -T image_stub/entry_v7.ld --defsym=IMAGE_BASE=$(X86_BASE) -o $@ \
	    $(BUILD)/entry_v7_x86_64.o $(BUILD)/uart_x86.o

$(BUILD)/entry_v7_x86_64.bin: $(BUILD)/entry_v7_x86_64.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD)/entry_v7_aarch64.o: $(ENTRY) image_stub/uart.h $(MAP) | $(BUILD)
	$(A64_CC) $(CFLAGS) -I../src -c $< -o $@

$(BUILD)/uart_a64.o: image_stub/uart_aarch64.c image_stub/uart.h $(MAP) | $(BUILD)
	$(A64_CC) $(CFLAGS) -c $< -o $@

$(BUILD)/entry_v7_aarch64.elf: $(BUILD)/entry_v7_aarch64.o $(BUILD)/uart_a64.o image_stub/entry_v7.ld
	$(CLANG) --target=aarch64-none-elf -nostdlib -fuse-ld=lld \
	    -Wl,-T,image_stub/entry_v7.ld -Wl,--defsym=IMAGE_BASE=$(A64_BASE) -o $@ \
	    $(BUILD)/entry_v7_aarch64.o $(BUILD)/uart_a64.o

$(BUILD)/entry_v7_aarch64.bin: $(BUILD)/entry_v7_aarch64.elf
	$(OBJCOPY) -O binary $< $@ || llvm-objcopy -O binary $< $@

$(BUILD)/entry_wasm.o: $(ENTRY) image_stub/uart.h $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -I../src -c $< -o $@

$(BUILD)/uart_wasm.o: image_stub/uart_wasm32.c image_stub/uart.h $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -c $< -o $@

$(BUILD)/shim_wasm.o: wasm_shim.c image_stub/uart.h $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -c $< -o $@

# --stack-first: the 8 KiB C stack sits below the module's data, clear of the
# image the host writes at 0x10000. run is the face; map_at and fifo_at arrive
# via the shim's export_name attribute (the linker renames it there). The
# initial memory is the room the map promises below the image; the maximum is
# the image's headroom (docs/32 3.1: wasm grows with memory.grow, with an
# initial and a maximum). The function table is the HOST's (--import-table:
# env.__indirect_function_table): a function slot names its leaf by table index
# (tools/native/link.py) and the host fills the table from the leaves module
# (tools/native/wasm_table.py). LLD 18 has no --table-max -- the room is the
# host table's, and --growable-table drops the link-time maximum.
$(BUILD)/rxf_boot.wasm: $(BUILD)/entry_wasm.o $(BUILD)/uart_wasm.o $(BUILD)/shim_wasm.o
	wasm-ld --no-entry --export=run --export-memory \
	    --import-table --growable-table \
	    --stack-first -z stack-size=8192 --initial-memory=131072 --max-memory=268435456 \
	    -o $@ $^

# ---- the packed images: the linked v7 image, per machine ------------------
#
# tools/native/boot_image.py packs the linked image with the carrier rows and
# the entry code. It is derived from the world and the tools that link it: a
# change under starter/ or native/ repacks it. No pin of these bytes can exist
# in boot.rxfd -- they carry the starter and hence the pins themselves -- and
# tests/test_image.py pins the arena byte for byte instead.
V7_INPUTS := $(shell find ../starter ../native -type f) \
    ../tools/native/boot_image.py ../tools/native/link.py \
    ../tools/native/arena_export.py ../tools/native/programs.py \
    ../tools/native/graph_header.py ../tools/native/wasm_table.py \
    ../tools/native/wasm_module.py ../tools/native/seed_build.py \
    ../tools/native/compose.py

$(BUILD)/image_x86_64_bios.bin: $(BUILD)/entry_v7_x86_64.bin $(MAP) $(V7_INPUTS) | $(BUILD)
	$(PYTHON) ../tools/native/boot_image.py --machine x86_64 \
	    --entry $(BUILD)/entry_v7_x86_64.bin $@

$(BUILD)/image_aarch64.bin: $(BUILD)/entry_v7_aarch64.bin $(MAP) $(V7_INPUTS) | $(BUILD)
	$(PYTHON) ../tools/native/boot_image.py --machine aarch64 \
	    --entry $(BUILD)/entry_v7_aarch64.bin $@

$(BUILD)/image_wasm32.bin: $(MAP) $(V7_INPUTS) | $(BUILD)
	$(PYTHON) ../tools/native/boot_image.py --machine wasm32 $@

# The leaves module: the image's leaf bodies as one module over the seat's
# memory and table (tools/native/wasm_table.py). The host instantiates it and
# fills table 0 by leaf id -- the function slots of the wasm32 image name
# their leaves by exactly that index.
$(BUILD)/rxf_leaves.wasm: $(V7_INPUTS) | $(BUILD)
	$(PYTHON) ../tools/native/wasm_table.py $@

# ---- x86_64 BIOS: a disk (sector + stage2) carrying the packed image -------

x86: $(BUILD)/disk.img

$(BUILD)/boot_x86_64.o: x86_64.S | $(BUILD)
	$(AS) --64 -o $@ $<

$(BUILD)/boot_x86_64.elf: $(BUILD)/boot_x86_64.o x86_64.ld
	$(LD) -T x86_64.ld -o $@ $(BUILD)/boot_x86_64.o

$(BUILD)/boot_x86_64.bin: $(BUILD)/boot_x86_64.elf
	$(OBJCOPY) -O binary $< $@

# The disk the BIOS sees (x86_64.S's DAPs): sector at LBA 0, stage2 at
# LBA 1..4 (the boot blob has a zero gap the DAPs do not read), the image at
# LBA 5 -- as many 64 KiB chunks as its head row carries, whole chunks, so the
# disk is exactly big enough (boot_image.py pads the image to them).
$(BUILD)/disk.img: $(BUILD)/boot_x86_64.bin $(BUILD)/image_x86_64_bios.bin
	dd if=/dev/zero of=$@ bs=512 \
	    count=$$((5 + $$(stat -c%s $(BUILD)/image_x86_64_bios.bin) / 512)) \
	    conv=sparse status=none
	dd if=$(BUILD)/boot_x86_64.bin of=$@ bs=512 count=1 conv=notrunc status=none
	dd if=$(BUILD)/boot_x86_64.bin of=$@ bs=1 skip=1024 seek=512 conv=notrunc status=none
	dd if=$(BUILD)/image_x86_64_bios.bin of=$@ bs=512 seek=5 conv=notrunc status=none

# ---- aarch64: one ELF for qemu -kernel (glue + the image's bytes) ----------
#
# boot_aarch64.elf is the seat's container (like x86_64's disk.img): the glue's
# code low and the packed image's bytes at the map base (aarch64.ld). What the
# image pins of this seat is the glue WITHOUT the image, build/boot_aarch64.bin
# -- the ELF carries the starter's arena, and a pin of it would name itself.
aarch64: $(BUILD)/boot_aarch64.elf $(BUILD)/boot_aarch64.bin $(BUILD)/image_aarch64.bin

$(BUILD)/boot_aarch64.o: aarch64.S $(BUILD)/image_aarch64.bin $(MAP)
	$(A64_CC) -c -o $@ aarch64.S

$(BUILD)/boot_aarch64.elf: $(BUILD)/boot_aarch64.o aarch64.ld
	$(CLANG) --target=aarch64-none-elf -nostdlib -fuse-ld=lld \
	    -Wl,-T,aarch64.ld -Wl,-e,_start -o $@ $<

$(BUILD)/boot_aarch64.bin: $(BUILD)/boot_aarch64.elf
	$(OBJCOPY) -O binary --remove-section=.image $< $@ \
	    || llvm-objcopy -O binary --remove-section=.image $< $@

# ---- wasm32: the module (entry + shim) and the image's bytes ---------------

wasm: $(BUILD)/rxf_boot.wasm $(BUILD)/image_wasm32.bin $(BUILD)/rxf_leaves.wasm

# ---- x86_64 UEFI: a PE stub and the volume it reads the image from ----------

uefi: $(BUILD)/uefi_root/EFI/BOOT/BOOTX64.EFI

$(BUILD)/BOOTX64.EFI: uefi_stub.c image_stub/uart_x86_64.c image_stub/uart.h $(MAP)
	$(CLANG) --target=x86_64-windows-msvc -fuse-ld=lld-link -nostdlib $(CFLAGS) \
	    -Wl,/subsystem:efi_application -Wl,/entry:efi_main -Wl,/timestamp:0 \
	    uefi_stub.c image_stub/uart_x86_64.c -o $@

# QEMU's virtual FAT (fat:rw:) serves this directory: the stub is the removable
# media boot file and the image is the file its protocol opens -- the same
# packed image the BIOS seat boots (one map, one image per machine).
$(BUILD)/uefi_root/EFI/BOOT/BOOTX64.EFI: $(BUILD)/BOOTX64.EFI $(BUILD)/image_x86_64_bios.bin
	mkdir -p $(BUILD)/uefi_root/EFI/BOOT
	cp $(BUILD)/BOOTX64.EFI $@
	cp $(BUILD)/image_x86_64_bios.bin $(BUILD)/uefi_root/RXF.IMG

# ---- the map's carriers, compared ------------------------------------------

check-map: $(BUILD)/entry_v7_x86_64.elf $(BUILD)/entry_v7_aarch64.elf
	sh check_map.sh

# ---- the smoke: boot every seat, assert the magic ---------------------------

smoke: all
	sh run_smoke.sh

clean:
	rm -rf $(BUILD)
