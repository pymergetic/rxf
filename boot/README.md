# boot/ — the minimal boot glue, per architecture

The ~200–500 lines per architecture that are architecturally unavoidable to
hand control to the RXF object world, plus the QEMU smoke that proves each one.
Everything here is standalone; `docs/115-selfhosting.md` is the plan this
satisfies (its four glue jobs: reset vector + stack/BSS, exception vectors,
machine mode setup, entry trampoline) and `tests/graph_world.py` is the map and
entry convention this reproduces row for row.

## What boots, and what the smoke proves

`sh boot/run_smoke.sh` builds every seat and boots it. Each seat's serial line
must say exactly what the image's program answered -- the argument word the
glue passed in, `"RXFOK"` -- and the result line of `native/wrap/wrap.h`:

```
RXFOK
rxf result 00000000 0000004b4f465852
```

All four seats pass on this machine (QEMU 8.2, clang 18, gas, wasm-ld, node):

| seat | what runs the glue | coarse exit |
|---|---|---|
| `x86_64-bios` | sector → stage2 (real → protected → long mode) → image | magic port 0xF4, QEMU exit 33 |
| `aarch64-virt` | EL2→EL1 drop, VBAR_EL1, MMU off → image | PSCI SYSTEM_OFF (QEMU exit 0) |
| `wasm32-node` | JS host: map → handle copy → `run` → FIFO drain | node exit 0 |
| `x86_64-uefi` | OVMF → PE stub → EFI file protocol → image | magic port 0xF4, QEMU exit 33 |

The run is the whole convention on the path of the magic bytes: the glue
resolves the handle (3 words) to the program object, passes `args`/`argc`/`out`
at their rows, the image's mini entry executes the object's program (MOVE +
RETURN), writes the answer to `out`, and reports through the Uart capability of
the seat (NS16550 port on x86_64, PL011 MMIO on aarch64, FIFO block + JS drain
on wasm32).

## The files

| file | what it is |
|---|---|
| `x86_64.S` | 16-bit BIOS sector + 32/64-bit trampoline (gas) |
| `x86_64.ld` | sector at 0x7C00, stage2 at 0x8000 |
| `aarch64.S` | QEMU virt entry: vectors, EL drop, MMU off, entry call |
| `aarch64.ld` | boot ELF: glue code + the image's bytes at the map base |
| `wasm_shim.c` | wasm32 trampoline: `run`/`map_at` exports |
| `wasm_shim.js` | the wasm32 host: lays the world out, calls `run`, drains the FIFO |
| `uefi_stub.c` | minimal UEFI PE entry (the firmware contract, in C) |
| `image_stub/world.h` | the map: every row offset, per-seat bases, magic port |
| `image_stub/world.c` | the smoke's world: context, object, program, handle, capabilities |
| `image_stub/entry.c` | the image's `rxf_graph_run`: the subset the program needs |
| `image_stub/uart_*.c` | the Uart capability's fill, one per machine |
| `image_stub/image.ld` | the flat image: the rows at their map offsets |
| `Makefile` | builds every seat; `check-map` proves the carriers agree |
| `check_map.sh` | the map's carriers compared: world.h vs asm vs linked images |
| `run_smoke.sh` | boots every seat, asserts the serial bytes and the exit |

## Build and run

```
make -C boot              # every seat
make -C boot check-map    # the map's carriers, compared row for row
make -C boot smoke        # build + boot every seat, assert the magic
```

Tools: `as` + `ld` (the x86_64 asm, gas as preferred), `clang` (C; its
integrated assembler is the aarch64 gas since no cross GNU binutils is here),
`wasm-ld` + `node`, `lld-link` (UEFI PE), `qemu-system-x86_64` +
`qemu-system-aarch64`, OVMF (`/usr/share/OVMF/OVMF_CODE_4M.fd`). A seat whose
tool is missing is skipped loudly, never silently.

## The entry convention (tests/graph_world.py's `run`, per ABI)

`run(context, handle, args, argc, out)` — the handle is `rxf_object_handle`
(24 bytes: object id, generation, offset), the smoke's `argc` is 1.

| seat | ABI | how the call looks |
|---|---|---|
| x86_64 | System V | `rdi` context, the 24-byte handle **by value on the stack** (MEMORY class), `rsi` args, `rdx` argc, `rcx` out |
| aarch64 | AAPCS64 | `x0` context, `x1` **address of a copy** of the handle (a >16-byte struct is passed by reference), `x2` args, `x3` argc, `x4` out |
| wasm32 | wasm | exported `run(i32 context, i32 handle_copy, i64 args, i64 argc, i32 out) -> i32`, the handle rebuilt from the host's copy |

`tests/emulator.py`'s `call_sysv` pins the x86_64 stack shape (return address,
then the handle's three words at `[rsp+8..32)`); `tests/graph_world.py`'s
`Blob(struct.pack('<3Q', *handle))` is that stack argument and its wasm
`HANDLE_COPY` step is the by-reference copy.

---

## Appendix A — the memory map the glue establishes

Every row is `tests/graph_world.py`'s row, offset for offset. The base moves
per machine (a flat image runs where its machine has room); the offsets never
do. `make check-map` proves the three carriers (world.h, the asm `.equ` rows,
the linked images' symbols) agree on every one.

| offset (from base) | row | content in the smoke |
|---|---|---|
| `0x0000` | CONTEXT | `rxf_runtime_context`, 144 bytes, version 2 |
| `0x0100` | JOURNAL | the journal row (unused: no transactions in the smoke) |
| `0x0400` | OBJECTS | the object table, 80-byte entries, sorted by id |
| `0x3100` | PAYLOADS | the payload bump row (the smoke adds none) |
| `0x4000` | PROGRAM | the program words (MOVE + RETURN), the object's payload |
| `0x5800` | ARGS | one argument word: `"RXFOK"` little-endian |
| `0x5900` | OUT | 256 bytes: where the answer lands |
| `0x5A00` | TEXT | 64 bytes of identity |
| `0x5B00`–`0x5E00` | SET/MAP rows | collection rows the smoke's run never opens (zero) |
| `0x5E00` | AREA | 272 bytes, the idle live area |
| `0x5F40` | CAPABILITIES | the Uart capability slot (id, generation, base) |
| `0x6000` | HEAP | 8 KiB heap row — **here the compared world ends (`0x8000`)** |
| `0x8000` | FUNCTIONS | the function table row (the smoke calls none) |
| `0x8600` | HANDLE_COPY | the 3-word handle the glue passes |
| `0x8800` | FRAME | the 8 KiB run cell (`RXF_GRAPH_CELL_BYTES`) |
| `0xA800` | FIFO | 576 bytes: the wasm seat's Uart device block |
| `0xB000` | ENTRY | the image's entry code (`rxf_graph_run`) |
| `0x8800`–`0xAB00` | BSS rows | FRAME + FIFO: zeroed by the glue before the entry call |

Per seat:

| seat | base | who establishes it |
|---|---|---|
| x86_64 BIOS | `0x10000` | the sector's INT 13h read of LBA 5 (disk: sector, stage2 LBA 1–4, image LBA 5+) |
| x86_64 UEFI | `0x10000` | the stub's `AllocatePages(EFI_ALLOCATE_ADDRESS)` + `\RXF.IMG` read |
| aarch64 virt | `0x40100000` | the boot ELF carries the image's bytes (`aarch64.ld`); QEMU `-kernel` loads it |
| wasm32 | `0x10000` (linear memory) | the host writes the image's bytes at the base (like the harness's `emulator.write`) |

Other glue-owned places: x86_64 stack at `0x7C00` growing down (page tables at
`0x2000`–`0x5000`, GDT in the sector at `0x7C00`, DAP buffer at `0x7000`);
aarch64 stack in the boot ELF's `.boot.bss` (16 KiB); wasm32 C stack 8 KiB
below the module's data (`--stack-first`), the world written at `0x10000`.

## Appendix B — Source-object fields (for docs/115 §3, lift mechanically)

Each row is one file of this directory as a Source object: `lang`, `arch`,
`text` (the file's bytes), `semantic_digest` (computed by the integration pass
from `text` at lift time — not fixed here), and what it `builds` into (Code).

| file | lang | arch | face (entry/export) | builds → |
|---|---|---|---|---|
| `boot/x86_64.S` | asm | x86_64 | `_start16` (reset), `_start32` | Code: boot sector + stage2 (`build/boot_x86_64.bin`) |
| `boot/x86_64.ld` | asm (link script) | x86_64 | — | carried with the boot blob above |
| `boot/aarch64.S` | asm | aarch64 | `_start` (reset) | Code: boot ELF (`build/boot_aarch64.elf`) |
| `boot/aarch64.ld` | asm (link script) | aarch64 | — | carried with the boot ELF above |
| `boot/wasm_shim.c` | c | wasm32 | `run`, `map_at` | Code: the wasm module (`build/rxf_boot.wasm`) |
| `boot/wasm_shim.js` | js | wasm32 (host) | `main` | host tool (not guest code) |
| `boot/uefi_stub.c` | c | x86_64 (PE) | `efi_main` | Code: `build/BOOTX64.EFI` |
| `boot/image_stub/world.h` | c (header) | all | the map's one home | carried by every image |
| `boot/image_stub/world.c` | c | all (seat via `RXF_BOOT_SEAT_*`) | `rxf_boot_context` | Code: the world rows of every image |
| `boot/image_stub/entry.c` | c | all | `rxf_graph_run` | Code: the image's entry |
| `boot/image_stub/uart_x86_64.c` | c | x86_64 | `rxf_boot_uart_putc` | Code: the Uart fill |
| `boot/image_stub/uart_aarch64.c` | c | aarch64 | `rxf_boot_uart_putc` | Code: the Uart fill |
| `boot/image_stub/uart_wasm32.c` | c | wasm32 | `rxf_boot_uart_putc` | Code: the Uart fill |
| `boot/image_stub/uart.h` | c (header) | all | the fill's face | carried by every image |
| `boot/image_stub/image.ld` | asm (link script) | all | — | the flat image layout (rows) |
| `boot/Makefile`, `boot/check_map.sh`, `boot/run_smoke.sh` | make/sh | — | — | build + proof tooling (not guest code) |

The image binaries (`build/image_*.bin`) are **Code** built from the rows above;
the disk/ELF/module packagings are their containers, not separate sources.

## Appendix C — what each arch still needs for a REAL bootloader

The smoke fixes the paths its QEMU offers; a real bootloader must discover
them. Per seat, what is deliberately missing:

**x86_64 BIOS**
- The image comes from a fixed LBA (5) of a raw disk. Real: a partition-table
  or filesystem story (a FAT/ext reader in the sector's size budget is its own
  project), retries and CHS fallback for old BIOSes, an EDD (AH=42h) presence
  check, and a disk identity instead of the BIOS's DL passed through.
- A20 is assumed on (QEMU is). Real: enable A20 before the load matters.
- No IDT is installed: a fault triple-faults the machine (visible and honest
  in QEMU's log). Real: exception vectors before the entry, per docs/115's job 2.
- No E820 memory map, no allocator: the map is fixed. Real: hand the image a
  memory map and make the map relocatable.

**aarch64 virt**
- The image rides inside the kernel ELF (qemu `-kernel` loads one file). Real:
  a block driver (or firmware) to load the image, and a device tree to find
  memory and the UART.
- The PSCI conduit is fixed to SMC (this QEMU's virt wires PSCI on SMC and
  exposes no `psci-conduit` knob). Real: read `/psci`'s `method` from the DT.
- The MMU is left off: correct but slow, and no cache policy is set. Real: a
  translation table for the world before enabling the MMU.
- The vector table spins; no GIC, no interrupts. Real: handlers and (if wanted)
  interrupt setup before the entry.

**wasm32**
- The host does everything the firmware does elsewhere: establishes the memory
  layout (the map), makes the handle copy, drains the Uart FIFO. Real: a host
  contract that owns the line (this is the same seam `native/uart.c`'s wasm32
  fill has), and whatever sandbox/pagination story the deployment wants.

**x86_64 UEFI**
- Fixed allocation at `0x10000` and a file named `RXF.IMG` at the volume root
  (QEMU's virtual FAT). Real: ExitBootServices and the boot-services memory
  map handover (the entry must not call boot services), a config-driven path,
  Secure Boot signing of the PE stub, and relocatable images if the fixed map
  is abandoned.

**Everywhere**
- The map is fixed. A real loader that wants another base must move the map
  with it (`image.ld`'s base, `world.h`'s rows, the asm's `.equ` rows --
  `check-map` is the guard against moving only one of them).
- The image's entry is the smoke's mini interpreter (`image_stub/entry.c`, the
  MOVE/RETURN/REFUSE subset with the reference interpreter's checks). When
  docs/115's ladder replaces it with the compiled interpreter blob, the call
  convention above must not move.
