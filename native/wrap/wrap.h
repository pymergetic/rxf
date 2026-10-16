/*
 * The shell of an RXF program: the one wrapper every seat runs, on BIOS, on UEFI and
 * as a process of an OS. The program is the linked image of tools/native/link.py; the
 * wrapper is a shell around it and nothing more (docs/17): it does three things and
 * reports one result.
 *
 * The entry convention (the same three lines on every seat):
 *
 *   1. A wrapper finds the RXFW package -- a header of RXF_WRAP_PACKAGE_BYTES and the
 *      linked image behind it -- beside its code, and one memory block at the base the
 *      header names.
 *   2. It loads the image into the block, calls the activation seam
 *      (rxf_activate_check, the one the certification lane fills), and runs the entry
 *      the header names through the run cell of the image itself, with no arguments:
 *      the `application.main` shape (rxf_graph_run of native/graph.h, the entry handle
 *      passed by value).
 *   3. It reports `rxf result <status> <out>` -- one ASCII line of
 *      RXF_WRAP_REPORT_BYTES with the status of the run and the first word of its
 *      output, both in lowercase hex -- on the channel of its seat (the serial port
 *      COM1 for firmware, standard output for a process), and answers: the magic port
 *      RXF_WRAP_MAGIC_PORT on x86, the exit of the process, the status of the EFI call.
 *
 * The answer of a seat is one byte, the same byte everywhere (RXF_WRAP_ANSWER_*): 0
 * when the run answered 0, the status of the run when it refused, and 0xF1 and up when
 * the wrapper itself could not run it. The magic port takes the coarse version of it
 * (RXF_WRAP_MAGIC_OK / RXF_WRAP_MAGIC_FAIL): QEMU's isa-debug-exit keeps 8 bits of what
 * a port is written, so the exact result travels on the serial line, never the port.
 */
#ifndef RXF_WRAP_H
#define RXF_WRAP_H

#include "../stdlib_runtime.h"

#define RXF_WRAP_MAGIC "RXFW"
#define RXF_WRAP_VERSION 1
#define RXF_WRAP_PACKAGE_BYTES 192
/* "rxf result " + 8 hex + " " + 16 hex + "\n" and its terminator. */
#define RXF_WRAP_REPORT_BYTES 38
/* The serial port of a firmware seat (COM1) and the magic port a boot writes its answer
 * to (QEMU's isa-debug-exit at 0xf4). */
#define RXF_WRAP_COM1 0x3F8
#define RXF_WRAP_MAGIC_PORT 0xF4
#define RXF_WRAP_MAGIC_OK 0x10
#define RXF_WRAP_MAGIC_FAIL 0x11
/* The answers: the wrapper could not use the package, could not place its block, or the
 * activation seam refused the image. */
#define RXF_WRAP_ANSWER_OK 0
#define RXF_WRAP_ANSWER_BAD_PACKAGE 0xF1
#define RXF_WRAP_ANSWER_NO_BLOCK 0xF2
#define RXF_WRAP_ANSWER_ACTIVATION 0xF3

/* The run cell of the image is the code of its machine, called with the C ABI of the
 * machine (System V on x86-64). The UEFI seat compiles for the Microsoft ABI, so its
 * call of the run is named here; see the typedef of rxf_wrap_run_fn below. */
#if defined(__x86_64__) && defined(_WIN32)
#define RXF_WRAP_SYSV __attribute__((sysv_abi))
#else
#define RXF_WRAP_SYSV
#endif

/*
 * The header of a package: what the wrapper needs and nothing more. Every address is
 * the one the image was linked at (tools/native/link.py), so the block sits at `base`
 * and nothing in it is relocated. The reserved words are zero now and zero-checked by
 * every reader.
 */
typedef struct {
    char magic[4];           /* "RXFW" */
    rxf_u32 version;         /* RXF_WRAP_VERSION */
    rxf_u64 total;           /* the bytes of the package */
    rxf_u64 base;            /* where the block must be */
    rxf_u64 image;           /* the offset of the linked image in the package */
    rxf_u64 image_bytes;     /* its bytes: the whole block */
    rxf_u64 run;             /* the address of the run cell of the image */
    rxf_u64 args;            /* the address of the argument words */
    rxf_u64 out;             /* the address of the run's output */
    rxf_u64 frame;           /* the address of the interpreter's frame block */
    rxf_object_handle entry; /* the handle the run is called with */
    rxf_u64 reserved[12];
} rxf_wrap_package;

_Static_assert(sizeof(rxf_wrap_package) == RXF_WRAP_PACKAGE_BYTES, "package ABI");
_Static_assert(__builtin_offsetof(rxf_wrap_package, entry) == 72, "package entry offset");
_Static_assert(__builtin_offsetof(rxf_wrap_package, reserved) == 96, "package reserved");

/* What a run answered: its status and the first word of its output. */
typedef struct {
    rxf_u64 out;
    rxf_u32 status;
} rxf_wrap_result;

/* The signature of rxf_graph_run (native/graph.h) with the ABI its code is written
 * with. The prototype of the header cannot carry the ABI of a seat, so the wrapper
 * names it here; native/wrap/wrap.c checks the two types are one on every seat that
 * compiles for the ABI of the machine. */
typedef rxf_u32(RXF_WRAP_SYSV *rxf_wrap_run_fn)(rxf_runtime_context *, rxf_object_handle, rxf_u64,
                                                rxf_u64, rxf_u64 *);

/*
 * Loads the image of a package into a block and runs its entry. `block` must sit at the
 * base the header names and hold `image_bytes` of it. Answers RXF_WRAP_ANSWER_OK when
 * the run happened (read the result), else why it did not.
 */
rxf_u32 rxf_wrap_enter(const void *package, rxf_u64 package_bytes, void *block, rxf_u64 block_bytes,
                       rxf_wrap_result *result);

/*
 * The one report line of a seat: `rxf result <status> <out>` in lowercase hex. `report`
 * takes RXF_WRAP_REPORT_BYTES; the answer is the length of the line, without its
 * terminator.
 */
rxf_u32 rxf_wrap_report_line(char *report, const rxf_wrap_result *result);

/*
 * The serial channel a firmware seat reports on (native/wrap/wrap_serial.c): COM1 set
 * up once, then written a line at a time.
 */
void rxf_wrap_serial_init(void);
void rxf_wrap_serial_write(const char *text, rxf_u64 bytes);

/* The port I/O of x86, where the seats that have it write their answer. */
#if defined(__x86_64__) || defined(__i386__)
static inline void rxf_wrap_outb(rxf_u16 port, rxf_u8 value) {
    __asm__ volatile("outb %0, %1" : : "a"(value), "Nd"(port));
}

static inline rxf_u8 rxf_wrap_inb(rxf_u16 port) {
    rxf_u8 value;
    __asm__ volatile("inb %1, %0" : "=a"(value) : "Nd"(port));
    return value;
}
#endif

/*
 * The activation seam: the certification lane fills this in (src/pymergetic/rxf/certify
 * owns the checks that live behind it). It answers 0 to pass the image, anything else
 * to refuse it; the wrapper reports an activation refusal and runs nothing.
 */
rxf_u32 rxf_activate_check(const void *block, rxf_u64 block_bytes);

#endif
