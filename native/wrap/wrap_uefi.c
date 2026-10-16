/*
 * The UEFI seat of the wrapper: a PE/COFF application (BOOTX64.EFI) whose entry is the
 * one the firmware calls with the image handle and the system table. It reads the
 * package from its .rxfpkg section (native/wrap/wrap_package.S), takes the block at the
 * base the package names from the boot services, runs the entry like every other seat
 * (native/wrap/wrap.h), and answers EFI status -- after the same report line on the
 * serial port and on the console of the firmware.
 */
#include "wrap.h"

#define EFI_SUCCESS 0
#define EFI_DEVICE_ERROR 0x8000000000000007ULL
#define EFI_ALLOCATE_ADDRESS 2
#define EFI_LOADER_CODE 1

typedef rxf_u64 efi_status;
typedef void *efi_handle;

typedef struct {
    rxf_u64 signature;
    rxf_u32 revision, header_size, crc32, reserved;
} efi_table_header;

/* The output protocol of the console: the seat writes its line through OutputString,
 * the second method (UEFI spec, 12.4). */
typedef struct efi_text_output efi_text_output;
struct efi_text_output {
    void *reset;
    efi_status (*output_string)(efi_text_output *, const rxf_u16 *);
    void *test_string, *query_mode, *set_mode, *set_attribute, *clear_screen;
    void *set_cursor_position, *enable_cursor;
};

/* The system table, with the fields the seat reads (UEFI spec, 4.3). */
typedef struct {
    efi_table_header header;
    rxf_u16 *firmware_vendor;
    rxf_u32 firmware_revision, padding;
    void *console_in_handle, *con_in;
    void *console_out_handle;
    efi_text_output *con_out;
    void *standard_error_handle, *std_err;
    void *runtime_services, *boot_services;
    rxf_u64 number_of_table_entries;
    void *configuration_table;
} efi_system_table;

/* The boot services, as far as the seat goes into them (UEFI spec, 7.3). */
typedef struct {
    efi_table_header header;
    void *raise_tpl, *restore_tpl;
    efi_status (*allocate_pages)(rxf_u32 type, rxf_u32 memory_type, rxf_u64 pages, rxf_u64 *memory);
} efi_boot_services;

_Static_assert(__builtin_offsetof(efi_system_table, con_out) == 64, "system table con_out");
_Static_assert(__builtin_offsetof(efi_boot_services, allocate_pages) == 40, "boot services pages");

extern char rxf_wrap_package_start[], rxf_wrap_package_end[];

static void console_write(efi_text_output *console, const char *text, rxf_u64 bytes) {
    rxf_u16 line[RXF_WRAP_REPORT_BYTES];

    for (rxf_u64 i = 0; i < bytes; i++)
        line[i] = (rxf_u8)text[i];
    line[bytes] = 0;
    console->output_string(console, line);
}

efi_status efi_main(efi_handle image, efi_system_table *system_table) {
    char report[RXF_WRAP_REPORT_BYTES];
    rxf_wrap_result result;
    efi_boot_services *services;
    const rxf_wrap_package *p;
    rxf_u64 block, package_bytes;
    rxf_u32 answer;

    (void)image;
    package_bytes = (rxf_u64)(rxf_wrap_package_end - rxf_wrap_package_start);
    if (package_bytes < RXF_WRAP_PACKAGE_BYTES)
        return EFI_DEVICE_ERROR;
    p = (const rxf_wrap_package *)(void *)rxf_wrap_package_start;
    /* The block is the one of the package, at its base; the pages come from the
     * firmware. */
    block = p->base;
    services = (efi_boot_services *)system_table->boot_services;
    if (services->allocate_pages(EFI_ALLOCATE_ADDRESS, EFI_LOADER_CODE,
                                 (p->image_bytes + 0xFFF) / 0x1000, &block))
        return EFI_DEVICE_ERROR;
    rxf_wrap_serial_init();
    answer = rxf_wrap_enter(rxf_wrap_package_start, package_bytes,
                            (void *)(unsigned long long)block, p->image_bytes, &result);
    if (answer == RXF_WRAP_ANSWER_OK) {
        rxf_wrap_report_line(report, &result);
        rxf_wrap_serial_write(report, RXF_WRAP_REPORT_BYTES - 1);
        console_write(system_table->con_out, report, RXF_WRAP_REPORT_BYTES - 1);
        answer = (rxf_u8)result.status;
    }
    rxf_wrap_outb(RXF_WRAP_MAGIC_PORT,
                  answer == RXF_WRAP_ANSWER_OK ? RXF_WRAP_MAGIC_OK : RXF_WRAP_MAGIC_FAIL);
    return answer == RXF_WRAP_ANSWER_OK ? EFI_SUCCESS : EFI_DEVICE_ERROR;
}
