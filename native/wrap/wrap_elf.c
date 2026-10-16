/*
 * The Linux seat of the wrapper: a static executable an OS loader can start (a static
 * ELF, no libc and no runtime). The package rides in its .rxfpkg section
 * (native/wrap/wrap_package.S); the seat maps the block at the base the package names,
 * runs the entry like every other seat (native/wrap/wrap.h), reports the line on
 * standard output, and answers with the exit of the process: 0 when the run answered 0,
 * the status of the run when it refused (the byte of an answer), and RXF_WRAP_ANSWER_*
 * when the wrapper itself could not run it.
 */
#include "wrap.h"

#define SYS_WRITE 1
#define SYS_MMAP 9
#define SYS_EXIT 60
#define STDOUT 1
/* The block is what the run reads, writes and executes (the code of the leaves and the
 * interpreter lives in the arena with everything else). */
#define PROT_READ_WRITE_EXEC 7
#define MAP_PRIVATE 0x02
#define MAP_ANONYMOUS 0x20
#define MAP_FIXED 0x10

extern char rxf_wrap_package_start[], rxf_wrap_package_end[];

static rxf_i64 syscall3(rxf_i64 number, rxf_i64 a, rxf_i64 b, rxf_i64 c) {
    rxf_i64 answer;
    __asm__ volatile("syscall"
                     : "=a"(answer)
                     : "a"(number), "D"(a), "S"(b), "d"(c)
                     : "rcx", "r11", "memory");
    return answer;
}

static rxf_i64 syscall6(rxf_i64 number, rxf_i64 a, rxf_i64 b, rxf_i64 c, rxf_i64 d, rxf_i64 e,
                        rxf_i64 f) {
    rxf_i64 answer;
    register rxf_i64 r10 __asm__("r10") = d;
    register rxf_i64 r8 __asm__("r8") = e;
    register rxf_i64 r9 __asm__("r9") = f;
    __asm__ volatile("syscall"
                     : "=a"(answer)
                     : "a"(number), "D"(a), "S"(b), "d"(c), "r"(r10), "r"(r8), "r"(r9)
                     : "rcx", "r11", "memory");
    return answer;
}

/* The kernel enters a process with the stack aligned one way and the C ABI another
 * (8 bytes apart); the entry realigns before it frames anything. */
__attribute__((noreturn, force_align_arg_pointer)) void _start(void) {
    char report[RXF_WRAP_REPORT_BYTES];
    rxf_wrap_result result;
    const rxf_wrap_package *p;
    rxf_u64 package_bytes;
    rxf_i64 block;
    rxf_u32 answer;

    package_bytes = (rxf_u64)(rxf_wrap_package_end - rxf_wrap_package_start);
    if (package_bytes < RXF_WRAP_PACKAGE_BYTES) {
        syscall3(SYS_EXIT, RXF_WRAP_ANSWER_BAD_PACKAGE, 0, 0);
        __builtin_unreachable();
    }
    p = (const rxf_wrap_package *)(void *)rxf_wrap_package_start;
    block = syscall6(SYS_MMAP, (rxf_i64)p->base, (rxf_i64)p->image_bytes, PROT_READ_WRITE_EXEC,
                     MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED, -1, 0);
    if (block != (rxf_i64)p->base) {
        syscall3(SYS_EXIT, RXF_WRAP_ANSWER_NO_BLOCK, 0, 0);
        __builtin_unreachable();
    }
    answer = rxf_wrap_enter(rxf_wrap_package_start, package_bytes,
                            (void *)(unsigned long long)block, p->image_bytes, &result);
    if (answer == RXF_WRAP_ANSWER_OK) {
        rxf_wrap_report_line(report, &result);
        syscall3(SYS_WRITE, STDOUT, (rxf_i64)(unsigned long long)report, RXF_WRAP_REPORT_BYTES - 1);
        answer = (rxf_u8)result.status;
    }
    syscall3(SYS_EXIT, answer, 0, 0);
    __builtin_unreachable();
}
