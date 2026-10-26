/* boot/uefi_stub.c -- the minimal UEFI PE entry: locate the image, call its entry.
 *
 * This one file is C on purpose (docs/115: UEFI demands a PE stub, and that
 * stub is the firmware contract, not image logic). Everything a firmware
 * loader makes unavoidable lives here: the PE entry point in the Microsoft x64
 * calling convention, the protocols that find the file the image lives in, the
 * fixed-address allocation the memory map (boot/image_stub/world.h) assumes,
 * the seat's growth fill that claims it against the firmware's memory map
 * (docs/32 3.1), and the coarse answer on the magic port. The image's entry is
 * called per System V exactly as the BIOS trampoline calls it -- the convention
 * of tests/graph_world.py, unchanged between firmware and firmware.
 *
 * Build: clang --target=x86_64-windows-msvc -fuse-ld=lld-link -nostdlib
 *              -Wl,/subsystem:efi_application -Wl,/entry:efi_main
 * Run:   qemu with OVMF and the stub as \EFI\BOOT\BOOTX64.EFI next to
 *        \RXF.IMG (boot/run_smoke.sh builds that volume from a directory).
 *
 * The types below are the UEFI ones, spelled the minimum the stub needs; the
 * firmware ABI is the Microsoft x64 one, so every service pointer is ms_abi
 * and the one call into the image is sysv_abi.
 */
#include <stdbool.h>

#include "image_stub/uart.h"
#include "image_stub/world.h"

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef unsigned long long u64;
typedef u64 efi_status;
typedef void *efi_handle;

#define MS __attribute__((ms_abi))
#define SYSV __attribute__((sysv_abi))
#define EFI_SUCCESS 0ULL
#define EFI_FILE_MODE_READ 1ULL
#define EFI_ALLOCATE_ADDRESS 2U
#define EFI_LOADER_DATA 2U

typedef struct {
    u32 d1;
    u16 d2, d3;
    u8 d4[8];
} efi_guid;

/* The two protocols the stub walks: the loaded image names the volume it came
 * from, the simple file system opens the file on it. */
static const efi_guid loaded_image_guid = {0x5b1b31a1, 0x9562, 0x11d2,
                                           {0x8e, 0x3f, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b}};
static const efi_guid simple_fs_guid = {0x964e5b22, 0x6459, 0x11d2,
                                        {0x8e, 0x39, 0x00, 0xa0, 0xc9, 0x69, 0x72, 0x3b}};

typedef struct {
    u32 revision;
    efi_handle parent;
    void *system_table;
    efi_handle device; /* the volume the stub itself was loaded from */
} loaded_image;

typedef struct efi_file efi_file;
struct efi_file {
    u64 revision;
    efi_status(MS *open)(efi_file *, efi_file **, u16 *, u64, u64);
    efi_status(MS *close)(efi_file *);
    void *delete;
    efi_status(MS *read)(efi_file *, u64 *, void *);
};

typedef struct {
    u64 revision;
    efi_status(MS *open)(void *, efi_file **);
} simple_fs;

/* The boot services the stub uses, in their ABI order: the fields before the
 * used ones are named only to keep the offsets honest. */
typedef struct {
    u8 header[24];
    void *raise_tpl, *restore_tpl;
    efi_status(MS *allocate_pages)(u32, u32, u64, u64 *);
    void *free_pages;
    efi_status(MS *get_memory_map)(u64 *, void *, u64 *, u64 *, u32 *);
    void *allocate_pool, *free_pool;
    void *create_event, *set_timer, *wait_for_event, *signal_event, *close_event, *check_event;
    void *install_protocol, *reinstall_protocol, *uninstall_protocol;
    efi_status(MS *handle_protocol)(efi_handle, const efi_guid *, void **);
    void *reserved;
    void *register_protocol_notify, *locate_handle, *locate_device_path;
    void *install_configuration, *load_image, *start_image, *exit, *unload_image;
    void *exit_boot_services, *get_next_monotonic, *stall, *set_watchdog;
    void *connect_controller, *disconnect_controller, *open_protocol, *close_protocol;
    void *open_protocol_information, *protocols_per_handle, *locate_handle_buffer;
    efi_status(MS *locate_protocol)(const efi_guid *, void *, void **);
} boot_services;

typedef struct {
    u8 header[24];
    u16 *firmware_vendor;
    u32 firmware_revision;
    efi_handle console_in_handle;
    void *con_in;
    efi_handle console_out_handle;
    void *con_out;
    efi_handle standard_error_handle;
    void *std_err;
    void *runtime_services;
    boot_services *boot;
} system_table;

/* The image's entry, per System V: the 24-byte handle by value -- a MEMORY
 * argument, so the call must carry the struct, not its words (three scalars
 * would land in registers and lie about the ABI; tests/emulator.py's call_sysv
 * pins the stack shape). */
typedef u32(SYSV * graph_run_fn)(void *context, rxf_object_handle program, u64 args, u64 argc,
                                 void *out);

/* The one report this file makes itself: the fill of the Uart capability
 * (boot/image_stub/uart_x86_64.c -- the same one the image uses) with the
 * slot a COM1 16550 names. The success line belongs to the image's entry. */
static void report(boot_services *boot, const u16 *why) {
    (void)boot;
    const rxf_native_slot com1 = {2, 4, 0x3f8}; /* RXF_CAPABILITY_UART, NS16550_PORT, COM1 */
    static const char head[] = "rxf result ffffffff 0000000000000000: ";
    for (const char *p = head; *p; p++)
        rxf_boot_uart_putc(&com1, (rxf_u8)*p);
    for (; *why; why++)
        rxf_boot_uart_putc(&com1, (u8)*why);
    rxf_boot_uart_putc(&com1, '\n');
}

static void magic_port(u8 byte) {
    __asm__ volatile("outb %0, %1" : : "a"(byte), "Nd"((u16)RXF_BOOT_MAGIC_PORT));
}

/* The memory descriptor GetMemoryMap hands out (the stride the walk uses is
 * descriptor_size, so the fields below are only the prefix every version shares). */
typedef struct {
    u32 type;
    u32 pad;
    u64 physical_start;
    u64 virtual_start;
    u64 pages;
    u64 attribute;
} efi_memory_descriptor;

#define EFI_CONVENTIONAL_MEMORY 7u

/* The seat's growth fill (docs/32 3.1: UEFI answers with E820's question through
 * GetMemoryMap and then AllocatePages). The shape is Memory.commit's
 * (core/arena/arena.hpp): the end of the bytes the arena wants materialized,
 * counted from the region's start, answered true when they are material now. */
typedef struct {
    boot_services *boot;
    u64 base;    /* where the region starts: the map's base */
    u64 have;    /* bytes materialized from the base */
    u64 ceiling; /* the end the region may grow to */
} efi_growth;

static bool efi_commit(void *ctx, u64 committed) {
    efi_growth *growth = (efi_growth *)ctx;
    if (committed <= growth->have)
        return true;
    if (committed > growth->ceiling)
        return false;
    u64 at = growth->base + growth->have;
    u64 pages = (committed - growth->have + 0xfff) / 0x1000;
    if (growth->boot->allocate_pages(EFI_ALLOCATE_ADDRESS, EFI_LOADER_DATA, pages, &at) !=
        EFI_SUCCESS)
        return false;
    growth->have += pages * 0x1000;
    return true;
}

/* The room above the promised one the firmware's map offers at this base: how
 * far the conventional memory run behind the map's base reaches. Zero when the
 * map cannot be read; the promised room is claimed either way. */
static u64 growth_headroom(boot_services *boot, u64 base, u64 room) {
    static u8 map[32768];
    u64 size = sizeof(map), key = 0, stride = 0;
    u32 version = 0;
    if (boot->get_memory_map(&size, map, &key, &stride, &version) != EFI_SUCCESS || stride == 0)
        return 0;
    u64 ceiling = base + room;
    for (u64 at = 0; at + sizeof(efi_memory_descriptor) <= size; at += stride) {
        const efi_memory_descriptor *d = (const efi_memory_descriptor *)(map + at);
        if (d->type != EFI_CONVENTIONAL_MEMORY)
            continue;
        u64 stop = d->physical_start + d->pages * 0x1000;
        if (d->physical_start <= base && base < stop && stop > ceiling)
            ceiling = stop;
    }
    return ceiling - (base + room);
}

efi_status MS efi_main(efi_handle image, system_table *st) {
    void *loaded = 0, *fs = 0;
    efi_file *root = 0, *file = 0;
    if (st->boot->handle_protocol(image, &loaded_image_guid, &loaded) != EFI_SUCCESS ||
        st->boot->handle_protocol(((loaded_image *)loaded)->device, &simple_fs_guid, &fs) !=
            EFI_SUCCESS) {
        report(st->boot, (const u16 *)L"no volume");
        magic_port(RXF_BOOT_MAGIC_FAIL);
        return EFI_SUCCESS;
    }

    /* The fixed-address room the map promises: world, entry code and the zero
     * rows the glue zeroes. The seat's growth fill claims it, with the ceiling
     * the firmware's memory map offers above the room for the arena to grow
     * into. A real boot that wants to live anywhere else must move the map with
     * it (boot/README.md). */
    efi_growth growth = {st->boot, RXF_BOOT_X86_IMAGE_BASE, 0, 0};
    growth.ceiling = growth.base + RXF_BOOT_X86_ROOM +
                     growth_headroom(st->boot, RXF_BOOT_X86_IMAGE_BASE, RXF_BOOT_X86_ROOM);
    if (!efi_commit(&growth, RXF_BOOT_X86_ROOM)) {
        report(st->boot, (const u16 *)L"no room at the map");
        magic_port(RXF_BOOT_MAGIC_FAIL);
        return EFI_SUCCESS;
    }

    if (((simple_fs *)fs)->open(fs, &root) != EFI_SUCCESS) {
        report(st->boot, (const u16 *)L"no root");
        magic_port(RXF_BOOT_MAGIC_FAIL);
        return EFI_SUCCESS;
    }
    if (root->open(root, &file, (u16 *)L"RXF.IMG", EFI_FILE_MODE_READ, 0) != EFI_SUCCESS) {
        report(st->boot, (const u16 *)L"no RXF.IMG");
        magic_port(RXF_BOOT_MAGIC_FAIL);
        return EFI_SUCCESS;
    }
    u64 size = RXF_BOOT_X86_ROOM; /* the buffer is the whole room; the file is smaller */
    if (file->read(file, &size, (void *)RXF_BOOT_X86_IMAGE_BASE) != EFI_SUCCESS ||
        size < RXF_BOOT_ENTRY + 64) {
        report(st->boot, (const u16 *)L"short RXF.IMG");
        magic_port(RXF_BOOT_MAGIC_FAIL);
        return EFI_SUCCESS;
    }

    /* BSS zeroing, the firmware edition: the image file has no .bss in it. */
    for (u64 *p = (u64 *)RXF_BOOT_X86_BSS; p < (u64 *)RXF_BOOT_X86_BSS_END; p++)
        *p = 0;

    /* The entry call, per System V -- the same picture the BIOS trampoline and
     * the aarch64 one build, from the same world words. */
    u64 *handle = (u64 *)RXF_BOOT_X86_HANDLE; /* the image's HANDLE_COPY row */
    graph_run_fn entry = (graph_run_fn)RXF_BOOT_X86_ENTRY;
    u32 status = entry((void *)RXF_BOOT_X86_IMAGE_BASE, *(rxf_object_handle *)handle,
                       RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_ARGS, RXF_BOOT_ARGC,
                       (void *)(RXF_BOOT_X86_IMAGE_BASE + RXF_BOOT_OUT));

    magic_port(status ? RXF_BOOT_MAGIC_FAIL : RXF_BOOT_MAGIC_OK);
    return EFI_SUCCESS;
}
