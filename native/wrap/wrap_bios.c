/*
 * The BIOS seat of the wrapper: what the loader of native/wrap/wrap_stage2.S calls once
 * the machine is in long mode and the package is staged in extended memory.
 *
 * The block sits at RXF_WRAP_BIOS_BLOCK (the base the packages of this seat are linked
 * at), the report line goes to the serial port COM1 word by word (a serial word for a
 * firmware), and the answer goes to the magic port RXF_WRAP_MAGIC_PORT where QEMU's
 * isa-debug-exit turns it into the exit of the machine. There is no return: the seat
 * answers and halts.
 */
#include "wrap.h"

#define RXF_WRAP_BIOS_BLOCK 0x04000000ULL
#define RXF_WRAP_BIOS_BLOCK_BYTES 0x02000000ULL

void rxf_wrap_bios_main(void *package, rxf_u64 package_bytes) {
    char report[RXF_WRAP_REPORT_BYTES];
    rxf_wrap_result result;
    rxf_u32 answer;

    rxf_wrap_serial_init();
    answer = rxf_wrap_enter(package, package_bytes, (void *)(unsigned long long)RXF_WRAP_BIOS_BLOCK,
                            RXF_WRAP_BIOS_BLOCK_BYTES, &result);
    if (answer == RXF_WRAP_ANSWER_OK) {
        rxf_wrap_report_line(report, &result);
        rxf_wrap_serial_write(report, RXF_WRAP_REPORT_BYTES - 1);
        answer = (rxf_u8)result.status;
    }
    rxf_wrap_outb(RXF_WRAP_MAGIC_PORT,
                  answer == RXF_WRAP_ANSWER_OK ? RXF_WRAP_MAGIC_OK : RXF_WRAP_MAGIC_FAIL);
    for (;;) {
        __asm__ volatile("cli; hlt");
    }
}
