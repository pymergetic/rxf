/* The worker of the native x86_64 seat (tests/emulator.py, _NativeX86).
 *
 * A fresh, position independent process: its own image sits high, so the fixed addresses of a
 * guest (the code at 0x800000 and up) are free in it - the Python of the suite is an executable
 * linked at 0x400000 whose image reaches past them. It maps the guest memory it is sent (a memfd
 * over the socket) at the guest's own addresses, calls a guest function with the System V
 * argument layout when it is told to, and answers every message once. It dies with its parent.
 *
 *   native_x86_worker FD        FD is the SOCK_SEQPACKET socket the parent made for it.
 *
 * Request, all fields little endian, one message each:
 *   header   u32 op (1 map, 2 call), u32 count, u64 a, u64 b
 *   map      a = guest base, b = size; the memfd travels as SCM_RIGHTS
 *   call     a = the address, count = arguments; per argument {u32 kind, u32 width, u64 bits}
 *            (kind 0 an integer, 1 a real of `width` bytes, 2 a structure of `width` bytes whose
 *            data follows the table), the structures in their order
 * Reply     i32 status (0, or an errno for a map that failed), u32 value (the 32-bit result)
 */

#define _GNU_SOURCE
#include <errno.h>
#include <signal.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

#define OP_MAP 1u
#define OP_CALL 2u
#define KIND_INTEGER 0u
#define KIND_REAL 1u
#define KIND_STRUCTURE 2u
#define INTEGER_REGISTERS 6u
#define REAL_REGISTERS 8u
#define MESSAGE_MAX (1u << 16)
#define STACK_WORDS_MAX (MESSAGE_MAX / 8u)

#ifndef MAP_FIXED_NOREPLACE
#define MAP_FIXED_NOREPLACE 0x100000
#endif

struct header {
    uint32_t op;
    uint32_t count;
    uint64_t a;
    uint64_t b;
};

struct argument {
    uint32_t kind;
    uint32_t width;
    uint64_t bits;
};

struct reply {
    int32_t status;
    uint32_t value;
};

/* uint32_t rxf_sysv_call(fn, ints[6], reals[8], stack, count): the registers of a System V call
 * from the tables, `count` words copied to the stack, the result in eax. */
extern uint32_t rxf_sysv_call(void *fn, const uint64_t *ints, const uint64_t *reals,
                              const uint64_t *stack, size_t count);
__asm__(".text\n"
        ".globl rxf_sysv_call\n"
        ".type rxf_sysv_call,@function\n"
        "rxf_sysv_call:\n"
        "  pushq %rbp\n"
        "  movq %rsp, %rbp\n"
        "  pushq %rbx\n"
        "  pushq %r12\n"
        "  movq %rdi, %r11\n"
        "  movq %rsi, %r10\n"
        "  movq %rdx, %rbx\n"
        "  movq %rcx, %rsi\n"
        "  movq %r8, %rcx\n"
        "  leaq 1(%rcx), %rax\n"
        "  andq $-2, %rax\n"
        "  shlq $3, %rax\n"
        "  subq %rax, %rsp\n"
        "  andq $-16, %rsp\n"
        "  xorq %rax, %rax\n"
        "1:\n"
        "  cmpq %rcx, %rax\n"
        "  jge 2f\n"
        "  movq (%rsi,%rax,8), %r12\n"
        "  movq %r12, (%rsp,%rax,8)\n"
        "  incq %rax\n"
        "  jmp 1b\n"
        "2:\n"
        "  movq 0(%rbx), %xmm0\n"
        "  movq 8(%rbx), %xmm1\n"
        "  movq 16(%rbx), %xmm2\n"
        "  movq 24(%rbx), %xmm3\n"
        "  movq 32(%rbx), %xmm4\n"
        "  movq 40(%rbx), %xmm5\n"
        "  movq 48(%rbx), %xmm6\n"
        "  movq 56(%rbx), %xmm7\n"
        "  movq 0(%r10), %rdi\n"
        "  movq 8(%r10), %rsi\n"
        "  movq 16(%r10), %rdx\n"
        "  movq 24(%r10), %rcx\n"
        "  movq 32(%r10), %r8\n"
        "  movq 40(%r10), %r9\n"
        "  movl $8, %eax\n"
        "  callq *%r11\n"
        "  leaq -16(%rbp), %rsp\n"
        "  popq %r12\n"
        "  popq %rbx\n"
        "  popq %rbp\n"
        "  retq\n"
        ".size rxf_sysv_call, .-rxf_sysv_call\n");

static void answer(int sock, int32_t status, uint32_t value) {
    struct reply reply = {status, value};
    if (send(sock, &reply, sizeof reply, 0) != (ssize_t)sizeof reply)
        _exit(1);
}

static void map_guest(int sock, const struct header *header, int fd) {
    void *at = mmap((void *)(uintptr_t)header->a, (size_t)header->b,
                    PROT_READ | PROT_WRITE | PROT_EXEC, MAP_SHARED | MAP_FIXED_NOREPLACE, fd, 0);
    int problem = errno;
    if (at == MAP_FAILED) {
        answer(sock, problem != 0 ? problem : EINVAL, 0);
    } else if (at != (void *)(uintptr_t)header->a) { /* an old kernel ignores the flag */
        munmap(at, (size_t)header->b);
        answer(sock, EEXIST, 0);
    } else {
        answer(sock, 0, 0);
    }
}

static void call_guest(int sock, const struct header *header, const uint8_t *table) {
    uint64_t ints[INTEGER_REGISTERS] = {0};
    uint64_t reals[REAL_REGISTERS] = {0};
    static uint64_t stack[STACK_WORDS_MAX];
    size_t integers = 0, real = 0, words = 0;
    const struct argument *arguments = (const struct argument *)table;
    const uint8_t *data = table + (size_t)header->count * sizeof(struct argument);

    for (uint32_t k = 0; k < header->count; k++) {
        const struct argument *argument = &arguments[k];
        if (argument->kind == KIND_REAL) {
            if (real < REAL_REGISTERS)
                reals[real++] = argument->bits;
        } else if (argument->kind == KIND_INTEGER) {
            if (integers < INTEGER_REGISTERS)
                ints[integers++] = argument->bits;
            else if (words < STACK_WORDS_MAX)
                stack[words++] = argument->bits;
        } else if (argument->kind == KIND_STRUCTURE) {
            size_t padded = ((size_t)argument->width + 7u) / 8u;
            if (words + padded > STACK_WORDS_MAX)
                _exit(2);
            memset(&stack[words], 0, padded * 8u);
            memcpy(&stack[words], data, argument->width);
            data += argument->width;
            words += padded;
        }
    }
    answer(sock, 0, rxf_sysv_call((void *)(uintptr_t)header->a, ints, reals, stack, words));
}

int main(int argc, char **argv) {
    static uint8_t message[MESSAGE_MAX];
    if (argc != 2)
        return 2;
    int sock = (int)strtol(argv[1], NULL, 10);
    signal(SIGINT, SIG_IGN);
    prctl(PR_SET_PDEATHSIG, SIGKILL);
    for (;;) {
        char control[CMSG_SPACE(sizeof(int))];
        struct iovec vector = {message, sizeof message};
        struct msghdr incoming = {0};
        incoming.msg_iov = &vector;
        incoming.msg_iovlen = 1;
        incoming.msg_control = control;
        incoming.msg_controllen = sizeof control;
        ssize_t got = recvmsg(sock, &incoming, 0);
        if (got < (ssize_t)sizeof(struct header))
            return 0; /* the parent closed its end, or sent nothing we know */
        struct header header;
        memcpy(&header, message, sizeof header);
        if (header.op == OP_MAP) {
            struct cmsghdr *cmsg = CMSG_FIRSTHDR(&incoming);
            if (cmsg == NULL || cmsg->cmsg_type != SCM_RIGHTS) {
                answer(sock, EBADF, 0);
                continue;
            }
            int fd;
            memcpy(&fd, CMSG_DATA(cmsg), sizeof fd);
            map_guest(sock, &header, fd);
            close(fd);
        } else if (header.op == OP_CALL) {
            call_guest(sock, &header, message + sizeof header);
        } else {
            answer(sock, EINVAL, 0);
        }
    }
}
