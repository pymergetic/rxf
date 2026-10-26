# boot/Makefile -- the boot glue per seat, one map (image_stub/world.h).
#
#   make              build every seat (x86_64 BIOS, aarch64 virt, wasm32, UEFI)
#   make check-map    prove the map's carriers agree (world.h vs asm vs images)
#   make smoke        build + boot every seat and assert the magic (run_smoke.sh)
#
# Tools and why: gas `as` + `ld` assemble and link the x86_64 asm (the
# preference); clang compiles C and assembles aarch64 (no cross GNU aarch64
# binutils is installed, its integrated assembler is the gas there); wasm-ld +
# node are the wasm32 seat; clang + lld-link make the UEFI PE stub. The three
# image links all run boot/image_stub/image.ld, one row map for every seat.

CLANG   := clang
AS      := as
LD      := ld
OBJCOPY := objcopy
NODE    := node
OVMF    := /usr/share/OVMF/OVMF_CODE_4M.fd

BUILD   := build
WORLD   := image_stub/world.c
ENTRY   := image_stub/entry.c
MAP     := image_stub/world.h image_stub/image.ld
INC     := -I. -Iimage_stub -I../native
CFLAGS  := -Os -ffreestanding -fno-builtin -fno-stack-protector -fno-pic \
           -fno-asynchronous-unwind-tables -Wall -Wextra $(INC)

X86_CC  := $(CLANG) --target=x86_64-unknown-none-elf
A64_CC  := $(CLANG) --target=aarch64-none-elf
WASM_CC := $(CLANG) --target=wasm32

# The bases: where each machine runs the world (world.h's per-seat notes).
X86_BASE  := 0x10000
A64_BASE  := 0x40100000
WASM_BASE := 0x10000

.PHONY: all x86 aarch64 wasm uefi check-map smoke clean
all: x86 aarch64 wasm uefi

$(BUILD):
	mkdir -p $(BUILD)

# ---- x86_64: a BIOS disk (sector + stage2 + the flat image) -----------------

x86: $(BUILD)/disk.img $(BUILD)/image_x86_64.bin

$(BUILD)/world_x86.o: $(WORLD) $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -DRXF_BOOT_SEAT_X86 -c $< -o $@

$(BUILD)/entry_x86.o: $(ENTRY) $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -DRXF_BOOT_SEAT_X86 -c $< -o $@

$(BUILD)/uart_x86.o: image_stub/uart_x86_64.c image_stub/uart.h $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -DRXF_BOOT_SEAT_X86 -c $< -o $@

$(BUILD)/image_x86_64.elf: $(BUILD)/world_x86.o $(BUILD)/entry_x86.o $(BUILD)/uart_x86.o image_stub/image.ld
	$(LD) -T image_stub/image.ld --defsym=IMAGE_BASE=$(X86_BASE) -o $@ \
	    $(BUILD)/world_x86.o $(BUILD)/entry_x86.o $(BUILD)/uart_x86.o

$(BUILD)/image_x86_64.bin: $(BUILD)/image_x86_64.elf
	$(OBJCOPY) -O binary $< $@
	@test $$(stat -c%s $@) -le $$((104 * 512)) || \
	    { echo "image does not fit the sector count x86_64.S reads"; exit 1; }

# The boot sector and stage2: pure gas (x86_64.S has no includes).
$(BUILD)/boot_x86_64.o: x86_64.S | $(BUILD)
	$(AS) --64 -o $@ $<

$(BUILD)/boot_x86_64.elf: $(BUILD)/boot_x86_64.o x86_64.ld
	$(LD) -T x86_64.ld -o $@ $(BUILD)/boot_x86_64.o

$(BUILD)/boot_x86_64.bin: $(BUILD)/boot_x86_64.elf
	$(OBJCOPY) -O binary $< $@

# The disk the BIOS sees (x86_64.S's DAPs): sector at LBA 0, stage2 at
# LBA 1..4 (the boot blob has a zero gap the DAPs do not read), image at LBA 5.
$(BUILD)/disk.img: $(BUILD)/boot_x86_64.bin $(BUILD)/image_x86_64.bin
	dd if=/dev/zero of=$@ bs=512 count=112 status=none
	dd if=$(BUILD)/boot_x86_64.bin of=$@ bs=512 count=1 conv=notrunc status=none
	dd if=$(BUILD)/boot_x86_64.bin of=$@ bs=1 skip=1024 seek=512 conv=notrunc status=none
	dd if=$(BUILD)/image_x86_64.bin of=$@ bs=512 seek=5 conv=notrunc status=none

# ---- aarch64: one ELF for qemu -kernel (glue + the flat image's bytes) ------

aarch64: $(BUILD)/boot_aarch64.elf $(BUILD)/image_aarch64.bin

$(BUILD)/world_a64.o: $(WORLD) $(MAP) | $(BUILD)
	$(A64_CC) $(CFLAGS) -DRXF_BOOT_SEAT_A64 -c $< -o $@

$(BUILD)/entry_a64.o: $(ENTRY) $(MAP) | $(BUILD)
	$(A64_CC) $(CFLAGS) -DRXF_BOOT_SEAT_A64 -c $< -o $@

$(BUILD)/uart_a64.o: image_stub/uart_aarch64.c image_stub/uart.h $(MAP) | $(BUILD)
	$(A64_CC) $(CFLAGS) -DRXF_BOOT_SEAT_A64 -c $< -o $@

$(BUILD)/image_aarch64.elf: $(BUILD)/world_a64.o $(BUILD)/entry_a64.o $(BUILD)/uart_a64.o image_stub/image.ld
	$(CLANG) --target=aarch64-none-elf -nostdlib -fuse-ld=lld \
	    -Wl,-T,image_stub/image.ld -Wl,--defsym=IMAGE_BASE=$(A64_BASE) -o $@ \
	    $(BUILD)/world_a64.o $(BUILD)/entry_a64.o $(BUILD)/uart_a64.o

$(BUILD)/image_aarch64.bin: $(BUILD)/image_aarch64.elf
	$(OBJCOPY) -O binary $< $@ || llvm-objcopy -O binary $< $@

$(BUILD)/boot_aarch64.o: aarch64.S $(BUILD)/image_aarch64.bin $(MAP)
	$(A64_CC) -c -o $@ aarch64.S

$(BUILD)/boot_aarch64.elf: $(BUILD)/boot_aarch64.o aarch64.ld
	$(CLANG) --target=aarch64-none-elf -nostdlib -fuse-ld=lld \
	    -Wl,-T,aarch64.ld -Wl,-e,_start -o $@ $<

# ---- wasm32: the module (entry + shim) and the world's bytes ----------------

wasm: $(BUILD)/rxf_boot.wasm $(BUILD)/image_wasm32.bin

$(BUILD)/world_wasm.o: $(WORLD) $(MAP) | $(BUILD)
	$(X86_CC) $(CFLAGS) -DRXF_BOOT_SEAT_WASM -c $< -o $@

# The wasm32 world is data: the host toolchain links the same rows at the same
# base (0x10000, graph_world.py's BASE) and the host writes them into memory.
$(BUILD)/image_wasm32.elf: $(BUILD)/world_wasm.o image_stub/image.ld
	$(LD) -T image_stub/image.ld --defsym=IMAGE_BASE=$(WASM_BASE) -o $@ $(BUILD)/world_wasm.o

$(BUILD)/image_wasm32.bin: $(BUILD)/image_wasm32.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD)/entry_wasm.o: $(ENTRY) $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -c $< -o $@

$(BUILD)/uart_wasm.o: image_stub/uart_wasm32.c image_stub/uart.h $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -c $< -o $@

$(BUILD)/shim_wasm.o: wasm_shim.c $(MAP) | $(BUILD)
	$(WASM_CC) $(CFLAGS) -c $< -o $@

# --stack-first: the 8 KiB C stack sits below the module's data, clear of the
# world rows the host writes at 0x10000. run is the face; map_at arrives via
# the shim's export_name attribute (the linker renames it there). The initial
# memory is the room the map promises; the maximum is the seat's growth headroom
# (docs/32 3.1: wasm grows with memory.grow, with an initial and a maximum).
$(BUILD)/rxf_boot.wasm: $(BUILD)/entry_wasm.o $(BUILD)/uart_wasm.o $(BUILD)/shim_wasm.o
	wasm-ld --no-entry --export=run --export-memory \
	    --stack-first -z stack-size=8192 --initial-memory=131072 --max-memory=1048576 \
	    -o $@ $^

# ---- x86_64 UEFI: a PE stub and the volume it reads the image from ----------

uefi: $(BUILD)/uefi_root/EFI/BOOT/BOOTX64.EFI

$(BUILD)/BOOTX64.EFI: uefi_stub.c image_stub/uart_x86_64.c image_stub/uart.h $(MAP)
	$(CLANG) --target=x86_64-windows-msvc -fuse-ld=lld-link -nostdlib $(CFLAGS) \
	    -Wl,/subsystem:efi_application -Wl,/entry:efi_main -Wl,/timestamp:0 \
	    uefi_stub.c image_stub/uart_x86_64.c -o $@

# QEMU's virtual FAT (fat:rw:) serves this directory: the stub is the removable
# media boot file and the image is the file its protocol opens.
$(BUILD)/uefi_root/EFI/BOOT/BOOTX64.EFI: $(BUILD)/BOOTX64.EFI $(BUILD)/image_x86_64.bin
	mkdir -p $(BUILD)/uefi_root/EFI/BOOT
	cp $(BUILD)/BOOTX64.EFI $@
	cp $(BUILD)/image_x86_64.bin $(BUILD)/uefi_root/RXF.IMG

# ---- the map's carriers, compared ------------------------------------------

check-map: $(BUILD)/image_x86_64.elf $(BUILD)/image_aarch64.elf $(BUILD)/image_wasm32.elf
	sh check_map.sh

# ---- the smoke: boot every seat, assert the magic ---------------------------

smoke: all
	sh run_smoke.sh

clean:
	rm -rf $(BUILD)
