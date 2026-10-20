/*
 * The seed compiler (docs/29): `.rxfs` source to x86_64 machine code, intrinsic lowering.
 * A `.rxfs` operator becomes a native instruction, not a call into a table of native leaves --
 * the crux that lets the stdlib be rewritten in `.rxfs` (because `a + b` is a real ADD,
 * `wrapping_add` itself can be `return a + b;`).
 *
 * The compiled function is the status-out ABI of docs/18 and docs/29:
 *
 *     status f(context, args, argc, out)
 *
 * x86_64 SysV: rdi = context (saved to rbx), rsi = args, rdx = argc, rcx = out (saved to r15).
 * The parameters are the argument words at [rsi]; the result is written to *out; the status
 * (0) to eax. Local variables live in a frame below rbp.
 *
 * This is a single-pass recursive-descent compiler: the lexer reads `.rxfs`, and the parser
 * emits x86_64 as it parses. Expressions lower through the intrinsic set of docs/29 (wrapping
 * add/sub/mul, bitwise, shifts with the count >= 64 -> 0 guard, unsigned compares); `if` and
 * `while` lower to branches. The aarch64 and wasm32 backends share this front end and intrinsic
 * contract; this file is the x86_64 one.
 *
 * The `.rxfs` subset compiled here: `program NAME(params) { ... }` with `var`, assignment,
 * `if`/`else`, `while`, `return`, and expressions over integer literals, variables, the binary
 * operators `* + - << >> < <= > >= == != & |`, and the unary `- ~ !`.
 *
 * Build and prove:   cc -O2 native/seed/seed.c -o /tmp/seed && /tmp/seed
 */

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

/* ---- the code buffer -------------------------------------------------------------------------- */

#define CAP 8388608
static uint8_t code[CAP];
static size_t at;

static void e8(uint8_t b) {
    if (at >= CAP) { fprintf(stderr, "code too large\n"); exit(2); }
    code[at++] = b;
}
static void e32(uint32_t w) {
    if (at + 4 > CAP) { fprintf(stderr, "code too large\n"); exit(2); }
    memcpy(code + at, &w, 4);
    at += 4;
}
static void patch32(size_t pos, uint32_t v) { memcpy(code + pos, &v, 4); }

static int obj_signed; /* 1 when the operands/compare are signed (drives setcc + extension) */

/* ---- x86_64, intrinsic lowering --------------------------------------------------------------- */

enum {
    OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR, OP_XOR,
    OP_DIV, OP_MOD, OP_SHL, OP_SHR, OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE
};

static void x_mov_rax_slot(int slot) { e8(0x48); e8(0x8B); e8(0x85); e32((uint32_t)slot); }
static void x_store_slot_rax(int slot) { e8(0x48); e8(0x89); e8(0x85); e32((uint32_t)slot); }
static void x_rax_imm(uint64_t v) {
    e8(0x48); e8(0xB8);
    if (at + 8 > CAP) { fprintf(stderr, "code too large\n"); exit(2); }
    memcpy(code + at, &v, 8);
    at += 8;
}
static void x_push_rax(void) { e8(0x50); }
static void x_pop_rax(void) { e8(0x58); }
static void x_mov_rcx_rax(void) { e8(0x48); e8(0x89); e8(0xC1); }

/* lhs in rax, rhs in rcx -> result in rax. */
static void x_binop(int op) {
    switch (op) {
    case OP_ADD: e8(0x48); e8(0x01); e8(0xC8); break; /* add rax, rcx */
    case OP_SUB: e8(0x48); e8(0x29); e8(0xC8); break; /* sub rax, rcx */
    case OP_AND: e8(0x48); e8(0x21); e8(0xC8); break;
    case OP_OR:  e8(0x48); e8(0x09); e8(0xC8); break;
    case OP_XOR: e8(0x48); e8(0x31); e8(0xC8); break;
    case OP_MUL: e8(0x48); e8(0xF7); e8(0xE1); break; /* mul rcx (wrapping) */
    case OP_DIV:
    case OP_MOD:
        /* idiv/div use rdx:rax / rcx; rdx is dead after the prologue (args live in the frame).
         * Quotient lands in rax, remainder in rdx. */
        if (obj_signed) {
            e8(0x48); e8(0x99);              /* cqo (sign-extend rax -> rdx:rax) */
            e8(0x48); e8(0xF7); e8(0xF9);    /* idiv rcx */
        } else {
            e8(0x48); e8(0x31); e8(0xD2);    /* xor rdx, rdx (zero-extend rax -> rdx:rax) */
            e8(0x48); e8(0xF7); e8(0xF1);    /* div rcx */
        }
        if (op == OP_MOD) { e8(0x48); e8(0x89); e8(0xD0); } /* mov rax, rdx (remainder) */
        break;
    case OP_SHL:
    case OP_SHR:
        e8(0x48); e8(0xD3); e8(op == OP_SHL ? 0xE0 : (obj_signed ? 0xF8 : 0xE8)); /* shl/sar/shr rax, cl */
        e8(0x31); e8(0xD2);                                 /* xor edx, edx */
        e8(0x48); e8(0x83); e8(0xF9); e8(0x40);             /* cmp rcx, 64 */
        e8(0x48); e8(0x0F); e8(0x43); e8(0xC2);             /* cmovae rax, rdx (0 if count >= 64) */
        break;
    default: { /* the compares: result 0 or 1 */
        static const uint8_t cc_u[] = {
            /* EQ  NE  LT  LE  GT  GE */ 0x94, 0x95, 0x92, 0x96, 0x97, 0x93  /* sete setne setb setbe seta setae */
        };
        static const uint8_t cc_s[] = {
            /* EQ  NE  LT  LE  GT  GE */ 0x94, 0x95, 0x9C, 0x9E, 0x9F, 0x9D  /* sete setne setl setle setg setge */
        };
        const uint8_t *cc = obj_signed ? cc_s : cc_u;
        e8(0x48); e8(0x39); e8(0xC8);             /* cmp rax, rcx */
        e8(0x0F); e8(cc[op - OP_EQ]); e8(0xC0);   /* setcc al */
        e8(0x48); e8(0x0F); e8(0xB6); e8(0xC0);   /* movzx rax, al */
        break;
    }
    }
}

static void x_unop(int op) { /* 0 = negate, 1 = not, 2 = logical not */
    if (op == 0) { e8(0x48); e8(0xF7); e8(0xD8); }           /* neg rax */
    else if (op == 1) { e8(0x48); e8(0xF7); e8(0xD0); }      /* not rax */
    else {
        e8(0x48); e8(0x85); e8(0xC0);                        /* test rax, rax */
        e8(0x0F); e8(0x94); e8(0xC0);                        /* setz al */
        e8(0x48); e8(0x0F); e8(0xB6); e8(0xC0);              /* movzx rax, al */
    }
}

static void x_test_rax(void) { e8(0x48); e8(0x85); e8(0xC0); }
static size_t x_jz(void) { e8(0x0F); e8(0x84); size_t p = at; e32(0); return p; }
static size_t x_jmp(void) { e8(0xE9); size_t p = at; e32(0); return p; }
static void x_patch(size_t p, size_t target) {
    patch32(p, (uint32_t)(int32_t)((ptrdiff_t)target - (ptrdiff_t)(p + 4)));
}
static void x_jmp_to(size_t target) { e8(0xE9); e32((uint32_t)(int32_t)((ptrdiff_t)target - (ptrdiff_t)(at + 4))); }

static size_t alloc; /* the frame bytes, set before the prologue is emitted */

static void x_prologue(void) {
    e8(0x55);                        /* push rbp */
    e8(0x53);                        /* push rbx */
    e8(0x41); e8(0x57);              /* push r15 */
    e8(0x48); e8(0x81); e8(0xEC); e32((uint32_t)alloc); /* sub rsp, alloc */
    e8(0x48); e8(0x89); e8(0xE5);    /* mov rbp, rsp */
    e8(0x48); e8(0x89); e8(0xFB);    /* mov rbx, rdi (context) */
    e8(0x49); e8(0x89); e8(0xCF);    /* mov r15, rcx (out) */
}

/* copy the parameters from the argument words at [rsi] into their frame slots. */
static void x_params(int n) {
    for (int i = 0; i < n; i++) {
        e8(0x48); e8(0x8B); e8(0x46); e8((uint8_t)(8 * i)); /* mov rax, [rsi + 8*i] */
        x_store_slot_rax(8 * i);
    }
}

static int store_width = 8; /* bytes written to *out: 1/2/4/8 (the type's width) */
/* the object ABI (docs/29 sec. 4.3): the operands are the leading parameters and `out` (a
 * pointer) follows them. A parameter narrower than 8 bytes arrives as i32 (wasm) or in a 64-bit
 * register (x86_64/aarch64) and is widened from its own type's width to the i64 the expression
 * runs in; obj_argw[i]/obj_argsx[i] drive that per parameter, and the first operand's sign
 * (obj_signed) drives the signedness of a comparison or an arithmetic shift. */
static int obj_argw[64];   /* per-word operand width in bytes (1/2/4/8) */
static int obj_argsx[64];  /* per-word: 1 = sign-extend from the width */
static int obj_argf[64];   /* per-word: 1 = a real (float/double), arrives in the float bank */
static int obj_argp[64];   /* per-word: 1 = an address word (an i32 local on wasm32) */
/* The C arguments can be shaped (!pHqqp): `H` is an object handle -- three words by value
 * on the stack for x86_64 (MEMORY class), by indirection on aarch64 and wasm32 -- every
 * other letter one word. The body names the words flat; the prologue places them. */
static char obj_shape[64];       /* the argument letters ('H' = handle); empty = flat words */
static int obj_nargs;            /* the number of C arguments (letters) */
static int obj_arg_first[64];    /* word slot of each argument's first word */
static int obj_nhandle;          /* the number of handle arguments */
static int wasm_slot_local[64];  /* word slot -> wasm local */
static int wasm_var_base;        /* the first wasm local of the function's variables */
static int wasm_out_local;       /* the wasm local of the out pointer */
static int obj_hasf;      /* set once a real parameter fixed fmath_width */
static int obj_nargw = 0;
static int obj_signed = 0;
static int obj_nparams = 2;

/* ---- the symbol table --------------------------------------------------------------------------
 * The symbols of one function: scalars are 8-byte words of the frame; a `var name[N];` is a
 * region -- N frame bytes whose name evaluates to its base address (the run cell of an
 * interpreter, the staging of a copy -- anything whose bytes are addressed, not loaded).
 * On wasm32 the scalars are the function's locals and the regions live in the module's
 * stack area under global 0, at `roff` from its base. */
static struct { char name[40]; int slot; int bytes; int roff; } syms[1024];
static int nsyms, frame, region_bytes;

static int find_sym_index(const char *name) {
    for (int i = nsyms - 1; i >= 0; i--) if (strcmp(syms[i].name, name) == 0) return i;
    return -1;
}
static int add_sym_sized(const char *name, int bytes, int region) {
    int existing = find_sym_index(name);
    if (existing >= 0) return syms[existing].slot;   /* a re-declaration reuses the slot */
    int slot, roff;
    if (region) {
        /* a region sits after the scalars of the frame (the caller allocates scalars first):
           its wasm32 address is the region base plus its own offset, its native address the
           frame slot. `var x[N]` is a region whatever N is: a region's name is its address,
           a scalar's name is its value. */
        roff = region_bytes;
        region_bytes += (bytes + 7) & ~7;
        slot = frame + roff;
    } else {
        slot = frame;
        frame += 8;
        bytes = 8;
        roff = -1;
    }
    if (nsyms >= (int)(sizeof syms / sizeof syms[0])) {
        fprintf(stderr, "too many variables\n");
        exit(2);
    }
    snprintf(syms[nsyms].name, sizeof syms[nsyms].name, "%s", name);
    syms[nsyms].slot = slot;
    syms[nsyms].bytes = bytes;
    syms[nsyms].roff = roff;
    nsyms++;
    return slot;
}
static int add_sym(const char *name) { return add_sym_sized(name, 8, 0); }

static void x_epilogue(void) {
    e8(0x48); e8(0x81); e8(0xC4); e32((uint32_t)alloc); /* add rsp, alloc */
    e8(0x41); e8(0x5F);              /* pop r15 */
    e8(0x5B);                        /* pop rbx */
    e8(0x5D);                        /* pop rbp */
    e8(0xC3);                        /* ret */
}
static void x_return(void) { /* rax = the result */
    /* *out = result at the type's width (the N-bit wrap is the low N bits of rax);
     * width 0 is a status-only function: nothing to write. */
    if (store_width == 0)      { }
    else if (store_width == 1) { e8(0x41); e8(0x88); e8(0x07); }              /* mov [r15], al */
    else if (store_width == 2) { e8(0x66); e8(0x41); e8(0x89); e8(0x07); }    /* mov [r15], ax */
    else if (store_width == 4) { e8(0x41); e8(0x89); e8(0x07); }              /* mov [r15], eax */
    else                       { e8(0x49); e8(0x89); e8(0x07); }              /* mov [r15], rax */
    e8(0x31); e8(0xC0);              /* xor eax, eax (status 0) */
    x_epilogue();
}
static void x_return_status(uint64_t status) { /* return `status` without writing *out */
    e8(0xB8); e32((uint32_t)status); /* mov eax, status */
    x_epilogue();
}
static void x_return_acc(void) { /* the accumulator is the status, no store through *out */
    x_epilogue();
}

/* ---- the wasm32 backend (the browser seat) ----------------------------------------------------
 * wasm is a stack machine: the accumulator is the stack top, so be_save is a no-op (the operands
 * live on the stack) and be_binop is the i64 op. The status-out ABI holds: the exported function
 * is (context, args, argc, out) -> status with the value words in linear memory. The aarch64
 * backend shares this front end and intrinsic contract; this file pairs x86_64 (executed) with
 * wasm32 (executed under node) so the .rxfs stdlib is proven on both the host and browser seats.
 */

enum { T_X64, T_ARM64, T_WASM };
static int target = T_X64;
/* 0 = the seed's generic status-out ABI (context, args*, argc, out);
 * 1 = the object world's direct-args status-out ABI (arg0..argN-1, out) -> status. */
static int abi_object = 0;
/* `#<fn>` of the codegen argv: compile only this fn of the module (a family file holds
 * many `fn` definitions and the object ABI carries one function's bytes per snippet;
 * the other fns' heads need not match this run's operand shapes). */
static const char *fn_select = 0;

/* ---- aarch64 (the firmware seat) instruction encoders -----------------------------------------
 * AAPCS64 status-out ABI: x0=context, x1=args, x2=argc, x3=out; x19 holds out, x20 the local base;
 * x0 is the accumulator. Encodings validated against llvm-mc and llvm-objdump (no qemu-user here). */
#define XZR 31u
static void a_movz(uint32_t rd, uint32_t imm16, uint32_t hw) { e32(0xD2800000u | (hw << 21) | (imm16 << 5) | rd); }
static void a_movk(uint32_t rd, uint32_t imm16, uint32_t hw) { e32(0xF2800000u | (hw << 21) | (imm16 << 5) | rd); }
static void a_add_imm(uint32_t rd, uint32_t rn, uint32_t imm) { e32(0x91000000u | (imm << 10) | (rn << 5) | rd); }
static void a_sub_imm(uint32_t rd, uint32_t rn, uint32_t imm) { e32(0xD1000000u | (imm << 10) | (rn << 5) | rd); }
static void a_ldr(uint32_t rt, uint32_t rn, uint32_t off) { e32(0xF9400000u | ((off / 8) << 10) | (rn << 5) | rt); }
static void a_str(uint32_t rt, uint32_t rn, uint32_t off) { e32(0xF9000000u | ((off / 8) << 10) | (rn << 5) | rt); }
static void a_str_pre(uint32_t rt, uint32_t rn, int imm9) { e32(0xF8000C00u | (((uint32_t)imm9 & 0x1FF) << 12) | (rn << 5) | rt); }
static void a_ldr_post(uint32_t rt, uint32_t rn, int imm9) { e32(0xF8400400u | (((uint32_t)imm9 & 0x1FF) << 12) | (rn << 5) | rt); }
static void a_stp_pre(uint32_t rt, uint32_t rt2, uint32_t rn, int imm7) { e32(0xA9800000u | (((uint32_t)imm7 & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt); }
static void a_ldp_post(uint32_t rt, uint32_t rt2, uint32_t rn, int imm7) { e32(0xA8C00000u | (((uint32_t)imm7 & 0x7F) << 15) | (rt2 << 10) | (rn << 5) | rt); }
static void a_add_reg(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x8B000000u | (rm << 16) | (rn << 5) | rd); }
static void a_sub_reg(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0xCB000000u | (rm << 16) | (rn << 5) | rd); }
static void a_mul(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9B000000u | (rm << 16) | (XZR << 10) | (rn << 5) | rd); }
static void a_msub(uint32_t rd, uint32_t rn, uint32_t rm, uint32_t ra) { e32(0x9B008000u | (rm << 16) | (ra << 10) | (rn << 5) | rd); } /* rd = ra - rn*rm */
static void a_udiv(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9AC00800u | (rm << 16) | (rn << 5) | rd); }
static void a_sdiv(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9AC00C00u | (rm << 16) | (rn << 5) | rd); }
static void a_and(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x8A000000u | (rm << 16) | (rn << 5) | rd); }
static void a_orr(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0xAA000000u | (rm << 16) | (rn << 5) | rd); }
static void a_eor(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0xCA000000u | (rm << 16) | (rn << 5) | rd); }
static void a_orn(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0xAA200000u | (rm << 16) | (rn << 5) | rd); }
static void a_lslv(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9AC02000u | (rm << 16) | (rn << 5) | rd); }
static void a_lsrv(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9AC02400u | (rm << 16) | (rn << 5) | rd); }
static void a_asrv(uint32_t rd, uint32_t rn, uint32_t rm) { e32(0x9AC02800u | (rm << 16) | (rn << 5) | rd); }
static void a_cmp_imm(uint32_t rn, uint32_t imm) { e32(0xF1000000u | (imm << 10) | (rn << 5) | XZR); }
static void a_cmp_reg(uint32_t rn, uint32_t rm) { e32(0xEB000000u | (rm << 16) | (rn << 5) | XZR); }
static void a_csel(uint32_t rd, uint32_t rn, uint32_t rm, uint32_t cond) { e32(0x9A800000u | (rm << 16) | (cond << 12) | (rn << 5) | rd); }
static void a_cset(uint32_t rd, uint32_t cond) { e32(0x9A9F07E0u | ((cond ^ 1u) << 12) | rd); }
static void a_mov_reg(uint32_t rd, uint32_t rm) { e32(0xAA0003E0u | (rm << 16) | rd); }
static void a_sxtb(uint32_t rd, uint32_t rn) { e32(0x93401C00u | (rn << 5) | rd); } /* sxtb xd, wn */
static void a_sxth(uint32_t rd, uint32_t rn) { e32(0x93403C00u | (rn << 5) | rd); } /* sxth xd, wn */
static void a_sxtw(uint32_t rd, uint32_t rn) { e32(0x93407C00u | (rn << 5) | rd); } /* sxtw xd, wn */
static void a_uxtb(uint32_t rd, uint32_t rn) { e32(0xD3401C00u | (rn << 5) | rd); } /* uxtb xd, wn */
static void a_uxth(uint32_t rd, uint32_t rn) { e32(0xD3403C00u | (rn << 5) | rd); } /* uxth xd, wn */
static void a_uxtw(uint32_t rd, uint32_t rn) { e32(0xD3407C00u | (rn << 5) | rd); } /* uxtw xd, wn */
static size_t a_b(void) { size_t p = at; e32(0x14000000u); return p; }               /* b . */
static void a_b_to(size_t target) { e32(0x14000000u | (((uint32_t)((int32_t)((target - at) >> 2))) & 0x03FFFFFFu)); }
static void a_patch(size_t p, size_t target) { /* fill in the branch at p to land on target */
    uint32_t ins; memcpy(&ins, code + p, 4);
    int32_t delta = (int32_t)((target - p) >> 2);
    if ((ins & 0x7C000000u) == 0x14000000u) {   /* b / bl: imm26 */
        if (delta > 0x1FFFFFF || delta < -0x2000000) {
            fprintf(stderr, "a_patch: b at %zu out of range (%d insns)\n", p, delta);
            exit(2);
        }
        ins = (ins & 0xFC000000u) | ((uint32_t)delta & 0x03FFFFFFu);
    } else {                                     /* cbz: imm19 at bits 5..23 */
        if (delta > 0x3FFFF || delta < -0x40000) {
            fprintf(stderr, "a_patch: cbz at %zu out of range (%d insns)\n", p, delta);
            exit(2);
        }
        ins = (ins & 0xFF00001Fu) | (((uint32_t)delta & 0x7FFFFu) << 5);
    }
    memcpy(code + p, &ins, 4);
}
static size_t a_bl(void) { size_t p = at; e32(0x94000000u); return p; }             /* bl . */
static void a_ret(void) { e32(0xD65F03C0u); }
/* sp +/- a count: imm12 when it fits, through x16 when it does not (graph_run's 8 KB cell
 * regions grow the frame past the 12-bit form). The register form must be the extended one:
 * in the shifted form register 31 is xzr, and sp only moves through ADD/SUB (extended).
 * x16 (IP0) is the scratch, never x9: be_tailcall_pp parks its jump target in x9 across
 * this very teardown, and a x9 scratch clobbers it (`br x9` into the constant). */
static void a_sp_off(uint32_t sub, uint32_t amount) {
    if (amount < 4096) {
        if (sub) a_sub_imm(31, 31, amount); else a_add_imm(31, 31, amount);
        return;
    }
    a_movz(16, (uint16_t)(amount & 0xFFFF), 0);
    a_movk(16, (uint16_t)((amount >> 16) & 0xFFFF), 1);
    uint32_t base = sub ? 0xCB200000u : 0x8B200000u;   /* SUB/ADD (extended register), LSL */
    e32(base | (16u << 16) | (3u << 13) | (31u << 5) | 31u);
}
static void a_epilogue(void) {
    a_sp_off(0, (uint32_t)alloc);         /* add sp,sp,#alloc */
    a_ldp_post(19, 20, 31, 2);               /* ldp x19,x20,[sp],#16 */
    a_ldp_post(29, 30, 31, 2);               /* ldp x29,x30,[sp],#16 */
    a_ret();
}
enum { C_EQ = 0, C_NE = 1, C_HS = 2, C_LO = 3, C_HI = 8, C_LS = 9,
       C_GE = 10, C_LT = 11, C_GT = 12, C_LE = 13 }; /* the signed forms: GE LT GT LE */


static uint8_t wbuf[CAP]; /* the wasm function bodies, assembled into a module below */
static size_t wat;
static int wasm_tmp; /* a scratch local index (the shift guard + the return store) */
static int wasm_region_local = -1; /* the local holding the base of the frame's regions */

static void w8(uint8_t b) {
    if (wat >= CAP) { fprintf(stderr, "wasm code too large\n"); exit(2); }
    wbuf[wat++] = b;
}
static void wuleb(uint64_t v) { do { uint8_t b = v & 0x7f; v >>= 7; if (v) b |= 0x80; w8(b); } while (v); }
static void wsleb(int64_t v) {
    int more = 1;
    while (more) {
        uint8_t b = (uint8_t)(v & 0x7f);
        v >>= 7;
        if ((v == 0 && !(b & 0x40)) || (v == -1 && (b & 0x40))) more = 0; else b |= 0x80;
        w8(b);
    }
}

/* A region's name is its base address: `lea`/`add` on the native seats, the captured
 * global-0 base plus the region's own offset on wasm32 (the base is captured at the
 * prologue, so a shaped call's temporary use of the global-0 stack never moves it). */
static void be_load_region(int idx) {
    int slot = syms[idx].slot, roff = syms[idx].roff;
    if (target == T_X64) {
        e8(0x48); e8(0x8D); e8(0x85); e32((uint32_t)slot);   /* lea rax, [rbp+slot] */
        return;
    }
    if (target == T_ARM64) {
        if (slot < 4096) { a_add_imm(0, 20, (uint32_t)slot); return; }  /* add x0, x20, #slot */
        a_movz(0, (uint32_t)(slot & 0xFFFF), 0);
        a_movk(0, (uint32_t)((slot >> 16) & 0xFFFF), 1);
        a_add_reg(0, 20, 0);                                          /* add x0, x20, x0 */
        return;
    }
    w8(0x20); wuleb((uint64_t)wasm_region_local);          /* local.get the base (i32) */
    w8(0xAC);                                              /* i64.extend_i32_u (addresses are i64 words) */
    if (roff) { w8(0x42); wsleb(roff); w8(0x7C); }         /* + the region's offset (i64.add) */
}

/* The regions of the frame live under the module's global-0 stack pointer on wasm32: the
 * prologue makes room once and captures their base in a local (the shaped calls borrow the
 * global-0 stack below it and give it back); every path that returns gives the room back. */
static void wasm_regions_open(int base_local) {
    if (target != T_WASM || region_bytes <= 0) return;
    wasm_region_local = base_local;
    w8(0x23); wuleb(0); w8(0x41); wsleb(region_bytes); w8(0x6B); w8(0x24); wuleb(0); /* global0 -= */
    w8(0x23); wuleb(0); w8(0x21); wuleb((uint64_t)base_local);  /* base = global0 */
}
static void wasm_regions_close(void) {
    if (target != T_WASM || region_bytes <= 0) return;
    w8(0x23); wuleb(0); w8(0x41); wsleb(region_bytes); w8(0x6A); w8(0x24); wuleb(0); /* global0 += */
}

static void be_const(uint64_t v) {
    if (target == T_X64) { x_rax_imm(v); return; }
    if (target == T_ARM64) {
        a_movz(0, (uint32_t)(v & 0xFFFF), 0);
        if (v >> 16) a_movk(0, (uint32_t)((v >> 16) & 0xFFFF), 1);
        if (v >> 32) a_movk(0, (uint32_t)((v >> 32) & 0xFFFF), 2);
        if (v >> 48) a_movk(0, (uint32_t)((v >> 48) & 0xFFFF), 3);
        return;
    }
    w8(0x42); wsleb((int64_t)v);                 /* i64.const */
}
static void be_load_var(int slot) {
    int idx = slot / 8;
    int is_param = abi_object && idx < obj_nparams;
    int pw = is_param ? ((idx < obj_nargw) ? obj_argw[idx] : store_width) : 8;
    int psx = is_param && idx < obj_nargw ? obj_argsx[idx] : 0;
    int pf = is_param && idx < obj_nargw ? obj_argf[idx] : 0;
    int pp = is_param && idx < obj_nargw ? obj_argp[idx] : 0;
    if (target == T_X64) {
        x_mov_rax_slot(slot);
        /* a narrow object operand is widened from the type's width to the i64 the expression
         * runs in: signed with movsx, unsigned with movzx -- so the upper bits are never the
         * caller's leftovers. A real's bits are parked full-width and need no widening. */
        if (is_param && pw < 8 && !pf) {
            if (psx) {
                if (pw >= 4) { e8(0x48); e8(0x63); e8(0xC0); }               /* movsxd rax, eax */
                else if (pw == 2) { e8(0x48); e8(0x0F); e8(0xBF); e8(0xC0); } /* movsx rax, ax */
                else { e8(0x48); e8(0x0F); e8(0xBE); e8(0xC0); }              /* movsx rax, al */
            } else {
                if (pw == 2) { e8(0x48); e8(0x0F); e8(0xB7); e8(0xC0); }      /* movzx rax, ax */
                else if (pw == 1) { e8(0x48); e8(0x0F); e8(0xB6); e8(0xC0); } /* movzx rax, al */
                else { e8(0x89); e8(0xC0); }                                  /* mov eax, eax (zero-extends) */
            }
        }
        return;
    }
    if (target == T_ARM64) {
        a_ldr(0, 20, (uint32_t)slot);  /* ldr x0, [x20,#slot] */
        if (is_param && pw < 8 && !pf) {
            if (psx) {
                if (pw >= 4) a_sxtw(0, 0);      /* sxtw x0, w0 */
                else if (pw == 2) a_sxth(0, 0); /* sxth x0, w0 */
                else a_sxtb(0, 0);              /* sxtb x0, w0 */
            } else {
                if (pw >= 4) a_uxtw(0, 0);      /* uxtw x0, w0 */
                else if (pw == 2) a_uxth(0, 0); /* uxth x0, w0 */
                else a_uxtb(0, 0);              /* uxtb x0, w0 */
            }
        }
        return;
    }
    if (abi_object) {
        w8(0x20); wuleb((uint64_t)(idx < obj_nargw ? wasm_slot_local[idx]
                                  : wasm_var_base + (idx - obj_nargw))); /* the word's local */
        if (pf) {
            /* a real operand is an f32/f64 local: reinterpret its bits to the i64 word */
            if (pw >= 8) w8(0xBD);                    /* i64.reinterpret_f64 */
            else { w8(0xBC); w8(0xAD); }              /* i32.reinterpret_f32, i64.extend_i32_u */
        } else if (is_param && pp) {
            /* an address operand is an i32 local on wasm32 (the C ABI): zero-extend it */
            w8(0xAD);                                 /* i64.extend_i32_u */
        } else if (is_param && pw < 8) {
            if (psx && pw >= 4) w8(0xAC);            /* i64.extend_i32_s (i32 -> i64) */
            else {
                w8(0xAD);                            /* i64.extend_i32_u (i32 -> i64) */
                if (psx)
                    w8(pw == 2 ? 0xC3 : 0xC2);       /* i64.extend16_s / i64.extend8_s (to the type's width) */
                else if (pw < 4) {
                    w8(0x42); wsleb(pw == 2 ? 0xFFFF : 0xFF);  /* i64.const mask */
                    w8(0x83);                        /* i64.and (to the type's width) */
                }
            }
        }
        return;
    }
    w8(0x20); wuleb((uint64_t)(5 + slot / 8));   /* local.get (0..3 ABI, 4 scratch) */
}
static void be_store_var(int slot) {
    if (target == T_X64) { x_store_slot_rax(slot); return; }
    if (target == T_ARM64) { a_str(0, 20, (uint32_t)slot); return; }  /* str x0, [x20,#slot] */
    if (abi_object) {
        int idx = slot / 8;
        if (idx < obj_nargw) {
            /* an address (and any narrow) operand is an i32 local on wasm32 (the C ABI); the
               expression runs in i64 words and wraps back into it. A real is f32/f64 there. */
            if (!obj_argf[idx] && obj_argp[idx] != 'q') w8(0xA7);   /* i32.wrap_i64 */
            w8(0x21); wuleb((uint64_t)wasm_slot_local[idx]);        /* local.set the word */
            return;
        }
        w8(0x21); wuleb((uint64_t)(wasm_var_base + (idx - obj_nargw))); /* local.set */
        return;
    }
    w8(0x21); wuleb((uint64_t)(5 + slot / 8));   /* local.set */
}
static void be_save(void) {
    if (target == T_X64) { x_push_rax(); return; }
    if (target == T_ARM64) { a_str_pre(0, 31, -16); return; }         /* str x0,[sp,#-16]! */
    /* wasm: the stack already holds lhs */
}

static void be_binop(int op) {
    if (target == T_X64) {
        x_mov_rcx_rax();
        x_pop_rax();
        x_binop(op);
        return;
    }
    if (target == T_ARM64) {
        a_ldr_post(1, 31, 16);                   /* ldr x1,[sp],#16 (pop lhs -> x1) */
        if (op == OP_SHL || op == OP_SHR) {
            a_mov_reg(2, 0);                     /* mov x2, x0 (the count) */
            a_cmp_imm(2, 64);                    /* cmp x2, #64 */
            if (op == OP_SHL) a_lslv(0, 1, 2);
            else if (obj_signed) a_asrv(0, 1, 2);
            else a_lsrv(0, 1, 2);
            a_csel(0, 0, 31, C_LO);              /* count < 64 ? shifted : 0 */
        } else if (op >= OP_EQ) {
            static const uint32_t cond_u[] = { C_EQ, C_NE, C_LO, C_LS, C_HI, C_HS };
            static const uint32_t cond_s[] = { C_EQ, C_NE, C_LT, C_LE, C_GT, C_GE };
            const uint32_t *cond = obj_signed ? cond_s : cond_u;
            a_cmp_reg(1, 0);                     /* cmp x1, x0 (lhs vs rhs) */
            a_cset(0, cond[op - OP_EQ]);
        } else if (op == OP_ADD) a_add_reg(0, 1, 0);
        else if (op == OP_SUB) a_sub_reg(0, 1, 0);
        else if (op == OP_MUL) a_mul(0, 1, 0);
        else if (op == OP_DIV) { if (obj_signed) a_sdiv(0, 1, 0); else a_udiv(0, 1, 0); }
        else if (op == OP_MOD) {
            /* remainder = lhs - (lhs / rhs) * rhs: div first, then msub */
            if (obj_signed) a_sdiv(2, 1, 0); else a_udiv(2, 1, 0); /* x2 = lhs / rhs */
            a_msub(0, 2, 0, 1);                  /* x0 = x1 - x2*x0 (lhs - q*rhs) */
        }
        else if (op == OP_AND) a_and(0, 1, 0);
        else if (op == OP_OR) a_orr(0, 1, 0);
        else a_eor(0, 1, 0);                     /* OP_XOR */
        return;
    }
    /* wasm: lhs and rhs are on the stack (rhs on top). i64.sub computes (top-1) - top = lhs-rhs. */
    static const uint8_t o[] = {
        /* ADD SUB MUL AND OR XOR */ 0x7C, 0x7D, 0x7E, 0x83, 0x84, 0x85
    };
    if (op == OP_SHL || op == OP_SHR) {
        /* the count >= 64 -> 0 guard (wasm masks the shift count, so select the zero). */
        w8(0x21); wuleb((uint64_t)wasm_tmp);     /* local.set tmp (count) */
        w8(0x20); wuleb((uint64_t)wasm_tmp);     /* local.get tmp */
        w8(op == OP_SHL ? 0x86 : (obj_signed ? 0x87 : 0x88)); /* i64.shl / i64.shr_s / i64.shr_u */
        w8(0x42); wsleb(0);                      /* i64.const 0 */
        w8(0x20); wuleb((uint64_t)wasm_tmp);     /* local.get tmp */
        w8(0x42); wsleb(64);                     /* i64.const 64 */
        w8(0x54);                                /* i64.lt_u (count < 64) */
        w8(0x1B);                                /* select (count<64 ? shifted : 0) */
        return;
    }
    if (op >= OP_EQ) {
        static const uint8_t c_u[] = { /* EQ NE LT LE GT GE (unsigned) */ 0x51, 0x52, 0x54, 0x58, 0x56, 0x5A };
        static const uint8_t c_s[] = { /* EQ NE LT LE GT GE (signed)   */ 0x51, 0x52, 0x53, 0x57, 0x55, 0x59 };
        w8((obj_signed ? c_s : c_u)[op - OP_EQ]); /* i64.eq/ne/lt/le/gt/ge -> i32 */
        w8(0xAD);                                /* i64.extend_i32_u -> i64 (the 0/1 result) */
        return;
    }
    if (op == OP_DIV || op == OP_MOD) {
        /* i64.div_u/rem_u (0x80/0x82) or div_s/rem_s (0x7F/0x81); the guards ran before, so
         * no trap (b == 0, INT_MIN/-1). rhs is on top of the stack. */
        w8(obj_signed ? (op == OP_DIV ? 0x7F : 0x81) : (op == OP_DIV ? 0x80 : 0x82));
        return;
    }
    w8(o[op]);                                   /* i64.add/sub/mul/and/or/xor */
}

/* The float intrinsics: `fadd(a, b)` and friends compute in the operand's own IEEE-754
 * format over the raw bits the real parameters arrive as, and hand back the result bits
 * in the same i64 word the expression runs in. fmath_width is the operand width of the
 * function (4 = float, 8 = double), taken from the `4f`/`8f` operand spec. */
static int fmath_width = 8;static void be_fbinop(int op) {
    if (target == T_X64) {
        e8(0x59);                                             /* pop rcx (lhs bits) */
        if (fmath_width >= 8) {
            e8(0x66); e8(0x48); e8(0x0F); e8(0x6E); e8(0xC1); /* movq xmm0, rcx */
            e8(0x66); e8(0x48); e8(0x0F); e8(0x6E); e8(0xC8); /* movq xmm1, rax */
            e8(0xF2);
        } else {
            e8(0x66); e8(0x0F); e8(0x6E); e8(0xC1);           /* movd xmm0, ecx */
            e8(0x66); e8(0x0F); e8(0x6E); e8(0xC8);           /* movd xmm1, eax */
            e8(0xF3);
        }
        e8(0x0F); e8((uint8_t)(op == OP_ADD ? 0x58 : op == OP_SUB ? 0x5C :
                               op == OP_MUL ? 0x59 : 0x5E));  /* add/sub/mul/div s? */
        e8(0xC1);                                             /* xmm0, xmm1 */
        if (fmath_width >= 8) {
            e8(0x66); e8(0x48); e8(0x0F); e8(0x7E); e8(0xC0); /* movq rax, xmm0 */
        } else {
            e8(0x66); e8(0x0F); e8(0x7E); e8(0xC0);           /* movd eax, xmm0 */
        }
        return;
    }
    if (target == T_ARM64) {
        a_ldr_post(1, 31, 16);                     /* ldr x1,[sp],#16 (pop lhs -> x1) */
        if (fmath_width >= 8) {
            e32(0x9E670000u | (1u << 5));          /* fmov d0, x1 */
            e32(0x9E670000u | 1u);                 /* fmov d1, x0 */
            e32((op == OP_ADD ? 0x1E602800u : op == OP_SUB ? 0x1E603800u :
                 op == OP_MUL ? 0x1E600800u : 0x1E601800u) | (1u << 16));
            e32(0x9E660000u);                      /* fmov x0, d0 */
        } else {
            e32(0x1E270000u | (1u << 5));          /* fmov s0, w1 */
            e32(0x1E270000u | 1u);                 /* fmov s1, w0 */
            e32((op == OP_ADD ? 0x1E202800u : op == OP_SUB ? 0x1E203800u :
                 op == OP_MUL ? 0x1E200800u : 0x1E201800u) | (1u << 16));
            e32(0x1E260000u);                      /* fmov w0, s0 */
        }
        return;
    }
    /* wasm: the stack holds lhs then rhs; park rhs in the scratch local and reinterpret
     * both operands' i64 bits into the float type, compute, reinterpret back. */
    w8(0x21); wuleb((uint64_t)wasm_tmp);           /* local.set scratch (rhs) */
    if (fmath_width >= 8) {
        w8(0xBF);                                  /* f64.reinterpret_i64 (lhs) */
        w8(0x20); wuleb((uint64_t)wasm_tmp);       /* local.get scratch */
        w8(0xBF);                                  /* f64.reinterpret_i64 (rhs) */
        w8((uint8_t)(op == OP_ADD ? 0xA0 : op == OP_SUB ? 0xA1 :
                     op == OP_MUL ? 0xA2 : 0xA3)); /* f64.add/sub/mul/div */
        w8(0xBD);                                  /* i64.reinterpret_f64 */
    } else {
        w8(0xA7);                                  /* i32.wrap_i64 (lhs) */
        w8(0xBE);                                  /* f32.reinterpret_i32 */
        w8(0x20); wuleb((uint64_t)wasm_tmp);       /* local.get scratch */
        w8(0xA7);                                  /* i32.wrap_i64 (rhs) */
        w8(0xBE);                                  /* f32.reinterpret_i32 */
        w8((uint8_t)(op == OP_ADD ? 0x92 : op == OP_SUB ? 0x93 :
                     op == OP_MUL ? 0x94 : 0x95)); /* f32.add/sub/mul/div */
        w8(0xBC);                                  /* i32.reinterpret_f32 */
        w8(0xAD);                                  /* i64.extend_i32_u */
    }
}

/* The memory intrinsics: `load8(a)` reads one unsigned byte from the pointer in `a`,
 * `store8(a, v)` writes the low byte of `v`. The pointers are raw addresses in the same
 * i64 words; the width is the intrinsic's own name (8/16/32/64 bits). */
/* in8(port): the byte an x86_64 I/O port gives; out8(port, value): what it takes. Only
 * x86_64 has the instructions and the capability (docs/19: a port is its alone, and the
 * Uart capability is unbound elsewhere), so the other machines refuse to compile them. */
static void be_in8(void) {
    if (target == T_X64) {
        e8(0x89); e8(0xC2);                    /* mov edx, eax (the port) */
        e8(0xEC);                              /* in al, dx */
        e8(0x0F); e8(0xB6); e8(0xC0);          /* movzx eax, al */
        return;
    }
    fprintf(stderr, "in8 is an x86_64 instruction; %s has no I/O ports\n",
            target == T_ARM64 ? "aarch64" : "wasm32");
    exit(2);
}
static void be_out8(void) {
    if (target == T_X64) {
        e8(0x5A);                              /* pop rdx (the port, saved before) */
        e8(0xEE);                              /* out dx, al (the value) */
        return;
    }
    fprintf(stderr, "out8 is an x86_64 instruction; %s has no I/O ports\n",
            target == T_ARM64 ? "aarch64" : "wasm32");
    exit(2);
}

static void be_mload(int w) {
    if (target == T_X64) {
        if (w == 1)      { e8(0x0F); e8(0xB6); e8(0x00); }              /* movzx eax, byte [rax] */
        else if (w == 2) { e8(0x0F); e8(0xB7); e8(0x00); }              /* movzx eax, word [rax] */
        else if (w == 4) { e8(0x8B); e8(0x00); }                        /* mov eax, [rax] */
        else             { e8(0x48); e8(0x8B); e8(0x00); }              /* mov rax, [rax] */
        return;
    }
    if (target == T_ARM64) {
        if (w == 1)      e32(0x39400000u);   /* ldrb w0, [x0] */
        else if (w == 2) e32(0x79400000u);   /* ldrh w0, [x0] */
        else if (w == 4) e32(0xB9400000u);   /* ldr w0, [x0] */
        else             e32(0xF9400000u);   /* ldr x0, [x0] */
        return;
    }
    /* wasm: the address is an i64 word; wrap it and load zero-extended back into a word. */
    w8(0xA7);                                /* i32.wrap_i64 (address) */
    if (w == 1)      { w8(0x2D); wuleb(0); wuleb(0); }   /* i32.load8_u */
    else if (w == 2) { w8(0x2F); wuleb(1); wuleb(0); }   /* i32.load16_u */
    else if (w == 4) { w8(0x28); wuleb(2); wuleb(0); }   /* i32.load */
    else             { w8(0x29); wuleb(3); wuleb(0); }   /* i64.load */
    if (w < 8) w8(0xAD);                     /* i64.extend_i32_u */
}

static void be_mstore(int w) {
    if (target == T_X64) {
        e8(0x59);                                            /* pop rcx (the address) */
        if (w == 1)      { e8(0x88); e8(0x01); }             /* mov [rcx], al */
        else if (w == 2) { e8(0x66); e8(0x89); e8(0x01); }   /* mov [rcx], ax */
        else if (w == 4) { e8(0x89); e8(0x01); }             /* mov [rcx], eax */
        else             { e8(0x48); e8(0x89); e8(0x01); }   /* mov [rcx], rax */
        return;                                              /* accumulator still holds v */
    }
    if (target == T_ARM64) {
        a_ldr_post(1, 31, 16);          /* ldr x1,[sp],#16 (pop the address -> x1) */
        if (w == 1)      e32(0x39000020u);   /* strb w0, [x1] */
        else if (w == 2) e32(0x79000020u);   /* strh w0, [x1] */
        else if (w == 4) e32(0xB9000020u);   /* str w0, [x1] */
        else             e32(0xF9000020u);   /* str x0, [x1] */
        return;
    }
    /* wasm: the stack holds the address then v; park v, wrap the address, store, keep v. */
    w8(0x21); wuleb((uint64_t)wasm_tmp);        /* local.set scratch (v) */
    w8(0xA7);                                   /* i32.wrap_i64 (address) */
    w8(0x20); wuleb((uint64_t)wasm_tmp);        /* local.get scratch (v) */
    if (w == 8) {
        w8(0x37); wuleb(3); wuleb(0);           /* i64.store */
    } else {
        w8(0xA7);                               /* i32.wrap_i64 (v) */
        if (w == 1)      { w8(0x3A); wuleb(0); wuleb(0); }   /* i32.store8 */
        else if (w == 2) { w8(0x3B); wuleb(1); wuleb(0); }   /* i32.store16 */
        else             { w8(0x36); wuleb(2); wuleb(0); }   /* i32.store */
    }
    w8(0x20); wuleb((uint64_t)wasm_tmp);        /* local.get scratch (the expression keeps v) */
}

static void be_unop(int op) {
    if (target == T_X64) { x_unop(op); return; }
    if (target == T_ARM64) {
        if (op == 0) a_sub_reg(0, 31, 0);           /* -a = xzr - a */
        else if (op == 1) a_orn(0, 31, 0);          /* ~a = mvn a */
        else { a_cmp_imm(0, 0); a_cset(0, C_EQ); }  /* !a = (a == 0) */
        return;
    }
    if (op == 0) { w8(0x42); wsleb(-1); w8(0x7E); }        /* -a = a * -1 (wrapping) */
    else if (op == 1) { w8(0x42); wsleb(-1); w8(0x85); }   /* ~a = a ^ -1 */
    else { w8(0x50); w8(0xAC); }                           /* !a = (a == 0) */
}

/* the ABI: load the value words from [args] into the locals, and on return store the result at
 * [out] with status 0. wasm locals 0..3 are (context, args, argc, out). */
static size_t wasm_ldpos; /* the local-decl count byte, patched when the body is complete */

static void be_prologue(int nparams) {
    if (abi_object) {                                    /* operands 0..nwords-1 on every target */
        obj_nparams = nparams;
        if (obj_nargw && obj_nargw != nparams) {
            fprintf(stderr, "object ABI: %d operand words for %d parameters\n", obj_nargw, nparams);
            exit(2);
        }
        if (!obj_shape[0]) { for (int k = 0; k < obj_nargw; k++) obj_arg_first[k] = k; }
    }
    if (target == T_X64) {
        x_prologue();
        if (abi_object) {
            /* the object world's direct-args status-out ABI: f(arg0..argN-1, out) -> status.
               Integer operands arrive in the SysV integer regs (rdi,rsi,rdx,rcx,r8,r9), real
               operands in the xmm bank (xmm0..xmm7) as their raw bits; each is parked in its
               frame slot so the body reads every operand as an i64 word. An object handle is
               three words of MEMORY class: on the stack at [rbp+16..], in argument order
               among the stack operands. The out pointer is the next free integer operand,
               kept in r15 so x_return writes through it. */
            /* mov [rbp+disp32], rN: REX has R.R set for the r8/r9 rows */
            static const uint8_t st_rex[] = { 0x48, 0x48, 0x48, 0x48, 0x4C, 0x4C };
            static const uint8_t st_modrm[] = { 0xBD, 0xB5, 0x95, 0x8D, 0x85, 0x8D };
            /* mov r15, rN: REX has R.R for the r8/r9 rows, R.B for r15 */
            static const uint8_t out_rex[] = { 0x49, 0x49, 0x49, 0x49, 0x4D, 0x4D };
            static const uint8_t out_modrm[] = { 0xFF, 0xF7, 0xD7, 0xCF, 0xC7, 0xCF };
            int kind[64], reg[64];                 /* per argument: 0 int reg, 1 xmm, 2 stack */
            long at[64];
            int nint = 0, nreal = 0;
            long so = 0;                           /* the stack operands, from [rbp+16] */
            for (int k = 0; k < obj_nargs; k++) {
                char letter = obj_shape[0] ? obj_shape[k]
                             : (obj_argf[obj_arg_first[k]] ? 'F' : 'q');
                if (letter == 'H') { kind[k] = 2; at[k] = so; so += 24; }
                else if (letter == 'f' || letter == 'F') { kind[k] = 1; reg[k] = nreal++; }
                else if (nint < 6) { kind[k] = 0; reg[k] = nint++; }
                else { kind[k] = 2; at[k] = so; so += 8; }
            }
            int out_kind = -1, out_reg = 0;
            long out_at = 0;
            if (store_width > 0) {
                if (nint < 6) { out_kind = 0; out_reg = nint; }
                else { out_kind = 2; out_at = so; so += 8; }
            }
            for (int k = 0; k < obj_nargs; k++) {           /* every word to its frame slot */
                int first = obj_arg_first[k];
                int words = (obj_shape[0] && obj_shape[k] == 'H') ? 3 : 1;
                for (int j = 0; j < words; j++) {
                    uint32_t slot = (uint32_t)(8 * (first + j));
                    if (kind[k] == 2) {
                        /* mov rax, [rbp+alloc+32+at+8j]: the stack operands sit above the
                           saved r15/rbx/rbp and the return address (rax is never an operand) */
                        e8(0x48); e8(0x8B); e8(0x85);
                        e32((uint32_t)(alloc + 32 + at[k] + 8 * j));
                        x_store_slot_rax(slot);
                    } else if (kind[k] == 1) {
                        /* movq rax, xmmN (f64) / movd eax, xmmN (f32): the raw bits to a slot */
                        e8(0x66);
                        if (obj_argw[first + j] >= 8) e8(0x48);
                        e8(0x0F); e8(0x7E); e8((uint8_t)(0xC0 | (reg[k] << 3)));
                        x_store_slot_rax(slot);
                    } else {
                        e8(st_rex[reg[k]]); e8(0x89); e8(st_modrm[reg[k]]); e32(slot);
                    }
                }
            }
            /* six integer registers hold operands; a status-only function of six has no
             * out pointer to capture. */
            if (out_kind == 0) {
                e8(out_rex[out_reg]); e8(0x89); e8(out_modrm[out_reg]);   /* mov r15, <out reg> */
            } else if (out_kind == 2) {
                e8(0x48); e8(0x8B); e8(0x85); e32((uint32_t)(alloc + 32 + out_at)); /* mov rax,[rbp+..] */
                e8(0x49); e8(0x89); e8(0xC7);                               /* mov r15, rax */
            }
        } else {
            x_params(nparams);
        }
        return;
    }
    if (target == T_ARM64) {
        a_stp_pre(29, 30, 31, -2);               /* stp x29,x30,[sp,#-16]! */
        a_stp_pre(19, 20, 31, -2);               /* stp x19,x20,[sp,#-16]! */
        a_sp_off(1, (uint32_t)alloc);            /* sub sp,sp,#alloc */
        a_add_imm(20, 31, 0);                    /* mov x20,sp (the local base) */
        if (abi_object) {
            /* the object world's direct-args status-out ABI: integer operands in x0..,
               real operands in s0../d0.. as their raw bits. An object handle arrives as
               the address of the caller's copy in one register (AAPCS64 indirection); its
               three words are parked through x9. The out pointer is captured first (the
               loads and fmov clobber x0), then the integer operands, then the reals. */
            int kind[64] = {0}, reg[64] = {0};      /* per argument: 0 int reg, 1 real, 2 frame */
            int nint = 0, nreal = 0, nstack = 0;
            for (int k = 0; k < obj_nargs; k++) {
                char letter = obj_shape[0] ? obj_shape[k]
                             : (obj_argf[obj_arg_first[k]] ? 'F' : 'q');
                if (letter == 'f' || letter == 'F') { kind[k] = 1; reg[k] = nreal++; }
                else if (nint < 8) { kind[k] = 0; reg[k] = nint++; }
                else { kind[k] = 2; reg[k] = nstack++; }  /* the caller's frame words */
            }
            if (store_width > 0) {
                if (nint >= 8) { kind[63] = 2; reg[63] = nstack++; }
                else a_mov_reg(19, (uint32_t)nint);   /* mov x19,xN (the out pointer) */
            }
            for (int k = 0; k < obj_nargs; k++) {           /* integer operands -> slots */
                int first = obj_arg_first[k];
                int words = (obj_shape[0] && obj_shape[k] == 'H') ? 3 : 1;
                if (kind[k] != 0) continue;
                if (words == 1) {
                    a_str((uint32_t)reg[k], 20, (uint32_t)(8 * first)); /* str xN,[x20,#slot] */
                } else {
                    for (int j = 0; j < 3; j++) {
                        a_ldr(9, (uint32_t)reg[k], (uint32_t)(8 * j));  /* ldr x9,[xN,#8j] */
                        a_str(9, 20, (uint32_t)(8 * (first + j)));
                    }
                }
            }
            for (int k = 0; k < obj_nargs; k++) {           /* the frame words -> slots */
                if (kind[k] != 2) continue;
                a_ldr(9, 31, (uint32_t)(32 + alloc + 8 * reg[k]));   /* ldr x9,[sp,#..] */
                a_str(9, 20, (uint32_t)(8 * obj_arg_first[k]));
            }
            if (store_width > 0 && kind[63] == 2) {
                a_ldr(19, 31, (uint32_t)(32 + alloc + 8 * reg[63])); /* ldr x19,[sp,#..] */
            }
            for (int k = 0; k < obj_nargs; k++) {           /* reals via x0: the raw bits */
                if (kind[k] != 1) continue;
                int first = obj_arg_first[k];
                /* fmov w0, sN (f32) / fmov x0, dN (f64) -> x0, then its slot */
                if (obj_argw[first] >= 8) e32(0x9E660000u | ((uint32_t)reg[k] << 5));
                else e32(0x1E260000u | ((uint32_t)reg[k] << 5));
                a_str(0, 20, (uint32_t)(8 * first));
            }
        } else {
            a_mov_reg(19, 3);                    /* mov x19,x3 (out: the generic ABI) */
            for (int i = 0; i < nparams; i++) {
                a_ldr(0, 1, (uint32_t)(8 * i));  /* ldr x0,[x1,#8*i] (from args) */
                a_str(0, 20, (uint32_t)(8 * i)); /* str x0,[x20,#8*i] (to a local) */
            }
        }
        return;
    }
    if (abi_object) {
        /* the object world's wasm ABI: one local per C argument (`H` an i32 address of the
         * caller's copy, its three words parked into i64 locals), `out` the next local (a
         * pointer) when the result is stored, then the scratch and the function's
         * variables. A status-only function has no out param. */
        obj_nparams = obj_nargw;
        wasm_out_local = obj_nargs;
        wasm_tmp = obj_nargs + (store_width ? 1 : 0);
        int parked = wasm_tmp + 1;
        for (int k = 0, w = 0; k < obj_nargs; k++) {
            int words = (obj_shape[0] && obj_shape[k] == 'H') ? 3 : 1;
            for (int j = 0; j < words; j++) {
                wasm_slot_local[w] = (words == 1) ? k : parked++;
                w++;
            }
        }
        wasm_var_base = parked;
        wasm_ldpos = 0;
        {
        int i64s = frame / 8 + 1 + (store_width ? 1 : 0) + 3 * obj_nhandle - obj_nargw
                   + (region_bytes ? 1 : 0);            /* its count (i64 words) */
        w8(region_bytes ? 0x02 : 0x01);                /* local groups */
        wuleb((uint64_t)i64s);                         /* the i64 words */
        w8(0x7E);                                          /* i64 */
        if (region_bytes) {
            wuleb(1); w8(0x7F);                        /* 1 x i32: the regions' base */
        }
        for (int k = 0, w = 0; k < obj_nargs; k++) {   /* the handle words in */
            int words = (obj_shape[0] && obj_shape[k] == 'H') ? 3 : 1;
            if (words == 1) { w++; continue; }
            for (int j = 0; j < 3; j++) {
                w8(0x20); wuleb((uint64_t)k);         /* local.get the address (i32) */
                w8(0x41); wsleb((int64_t)(8 * j));    /* i32.const 8*j */
                w8(0x6A);                             /* i32.add */
                w8(0x29); wuleb(3); wuleb(0);         /* i64.load align=8 offset=0 */
                w8(0x21); wuleb((uint64_t)wasm_slot_local[w]);   /* local.set the word */
                w++;
            }
        }
        if (region_bytes) {
            wasm_regions_open(wasm_tmp + i64s);
        }
        }
        return;
    }
    wasm_tmp = 4;                                 /* local 4 = scratch (shift guard, return store) */
    wasm_ldpos = 0;
    {
    int i64s = frame / 8 + 1 + (region_bytes ? 1 : 0);  /* the i64 words */
    w8(region_bytes ? 0x02 : 0x01);               /* local groups */
    wuleb((uint64_t)i64s);
    w8(0x7E);                                     /* i64 */
    if (region_bytes) {
        wuleb(1); w8(0x7F);                       /* 1 x i32: the regions' base */
    }
    for (int i = 0; i < nparams; i++) {
        w8(0x20); wuleb(1);                       /* local.get args */
        w8(0x29); wuleb(3); wuleb((uint64_t)(8 * i)); /* i64.load align=8 offset=8*i */
        w8(0x21); wuleb((uint64_t)(5 + i));       /* local.set param_i */
    }
    if (region_bytes) {
        wasm_regions_open(4 + i64s);
    }
    }
}
static void be_return(void) {
    if (target == T_X64) { x_return(); return; }
    if (target == T_ARM64) {
        /* *out = result at the type's width (strb/strh/str32/str64); 0 = status-only */
        if (store_width == 1)      e32(0x39000000u | (19u << 5));  /* strb w0,[x19] */
        else if (store_width == 2) e32(0x79000000u | (19u << 5));  /* strh w0,[x19] */
        else if (store_width == 4) e32(0xB9000000u | (19u << 5));  /* str w0,[x19] */
        else if (store_width == 8) a_str(0, 19, 0);                /* str x0,[x19] */
        a_movz(0, 0, 0);                         /* mov x0,#0 (status 0) */
        a_epilogue();
        return;
    }
    if (abi_object) {
        if (store_width == 0) {                   /* status-only: nothing to write */
            wasm_regions_close();
            w8(0x41); wsleb(0);                   /* i32.const 0 (status) */
            w8(0x0F);                             /* return */
            return;
        }
        w8(0x21); wuleb((uint64_t)wasm_tmp);       /* local.set scratch (the result) */
        w8(0x20); wuleb((uint64_t)wasm_out_local); /* local.get out (addr) */
        w8(0x20); wuleb((uint64_t)wasm_tmp);       /* local.get scratch (value) */
        if (store_width == 1)      { w8(0x3C); wuleb(0); wuleb(0); }  /* i64.store8 */
        else if (store_width == 2) { w8(0x3D); wuleb(1); wuleb(0); }  /* i64.store16 */
        else if (store_width == 4) { w8(0x3E); wuleb(2); wuleb(0); }  /* i64.store32 */
        else                       { w8(0x37); wuleb(3); wuleb(0); }  /* i64.store */
        wasm_regions_close();
        w8(0x41); wsleb(0);                        /* i32.const 0 (status) */
        w8(0x0F);                                  /* return */
        return;
    }
    w8(0x21); wuleb((uint64_t)wasm_tmp);         /* local.set tmp (the result) */
    w8(0x20); wuleb(3);                          /* local.get out (addr) */
    w8(0x20); wuleb((uint64_t)wasm_tmp);         /* local.get tmp (value) */
    w8(0x37); wuleb(3); wuleb(0);                /* i64.store out */
    wasm_regions_close();
    w8(0x41); wsleb(0);                          /* i32.const 0 (status) */
    w8(0x0F);                                    /* return */
}

/* refuse <status>: return the status without writing *out (the RXF_REFUSE_* early returns) */
static void be_refuse(uint64_t status) {
    if (target == T_X64) { x_return_status(status); return; }
    if (target == T_ARM64) { a_movz(0, (uint32_t)status, 0); a_epilogue(); return; }
    wasm_regions_close();
    w8(0x41); wsleb((int64_t)status);            /* i32.const status */
    w8(0x0F);                                    /* return */
}
static void be_refuse_acc(void) { /* `refuse expr;`: a dynamic status in the accumulator, no store */
    if (target == T_X64) { x_return_acc(); return; }
    if (target == T_ARM64) { a_epilogue(); return; }
    wasm_regions_close();
    w8(0xA7);                                    /* i32.wrap_i64 */
    w8(0x0F);                                    /* return */
}

/* control flow (if/while) on every machine. x86_64 and aarch64 patch a branch to a byte offset;
 * wasm32 uses structured blocks (`if`/`else`/`end`, `block`/`loop`/`br`) so nothing is patched --
 * the nesting is spelled by the opcodes themselves. */
typedef struct { size_t skip_then, skip_else; int has_else; } be_if_t;
typedef struct { size_t top, done; } be_loop_t;

static be_if_t be_if_open(void) {
    be_if_t h = {0, 0, 0};
    if (target == T_X64) { x_test_rax(); h.skip_then = x_jz(); return h; }  /* jz past the then */
    if (target == T_ARM64) {
        /* cbz has but ±1 MB (imm19): a then-block of a heavily macro-spliced body is larger,
         * so the skip is a cbnz over an unconditional b (imm26, ±128 MB) that is patched. */
        e32(0xB5000040u);                  /* cbnz x0, .+8 (into the then) */
        h.skip_then = a_b();               /* the false path jumps far to the else/end */
        return h;
    }
    w8(0xA7);              /* i32.wrap_i64 (the condition is an i64; `if` wants i32) */
    w8(0x04); w8(0x40);    /* if (void) */
    return h;
}
static void be_else(be_if_t *h) {
    h->has_else = 1;
    if (target == T_X64) { h->skip_else = x_jmp(); x_patch(h->skip_then, at); return; }
    if (target == T_ARM64) { h->skip_else = a_b(); a_patch(h->skip_then, at); return; }
    w8(0x05);              /* else */
}
static void be_end(be_if_t *h) {
    if (target == T_X64) { x_patch(h->has_else ? h->skip_else : h->skip_then, at); return; }
    if (target == T_ARM64) { a_patch(h->has_else ? h->skip_else : h->skip_then, at); return; }
    w8(0x0B);              /* end */
}

static be_loop_t be_loop_open(void) {
    be_loop_t l = {at, 0};
    if (target == T_WASM) { w8(0x02); w8(0x40); w8(0x03); w8(0x40); }  /* block; loop (br 1 exits) */
    return l;
}
static void be_loop_break(be_loop_t *l) {
    if (target == T_X64) { x_test_rax(); l->done = x_jz(); return; }   /* leave when cond == 0 */
    if (target == T_ARM64) {
        e32(0xB5000040u);                  /* cbnz x0, .+8 (stay in the loop) */
        l->done = a_b();                   /* the false path jumps far past the body */
        return;
    }
    w8(0xA7);              /* i32.wrap_i64 */
    w8(0x45);              /* i32.eqz (leave when cond == 0) */
    w8(0x0D); wuleb(1);    /* br_if 1 (to the enclosing block = the exit) */
}
static void be_loop_close(be_loop_t *l) {
    if (target == T_X64) { x_jmp_to(l->top); x_patch(l->done, at); return; }
    if (target == T_ARM64) { a_b_to(l->top); a_patch(l->done, at); return; }
    w8(0x0C); wuleb(0);    /* br 0 (back to the loop start) */
    w8(0x0B);              /* end (loop) */
    w8(0x0B);              /* end (block) */
}

/* ---- the lexer -------------------------------------------------------------------------------- */

enum { TK_EOF, TK_NAME, TK_NUM, TK_OP };
typedef struct { int kind; char text[40]; uint64_t num; } token;
#define NMACROS 256
#define MACRO_BODY 16384
static token *toks;
static long toks_cap;
static int ntoks, cur;

static int is_name(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_'; }
static int is_digit(char c) { return (c >= '0' && c <= '9'); }

static void lex(const char *s) {
    ntoks = cur = 0;
    while (*s) {
        /* one slot stays for the EOF token: overflow grows the buffer, never a write past. */
        if (ntoks >= toks_cap - 1) {
            toks_cap = toks_cap ? toks_cap * 2 : 65536;
            toks = (token *)realloc(toks, sizeof(token) * (size_t)toks_cap);
            if (!toks) { fprintf(stderr, "too many tokens\n"); exit(2); }
        }
        if (*s == ' ' || *s == '\t' || *s == '\n' || *s == '\r') { s++; continue; }
        if (*s == '#') { while (*s && *s != '\n') s++; continue; }
        if (is_name(*s)) {
            const char *b = s;
            while (is_name(*s) || is_digit(*s)) s++;
            toks[ntoks].kind = TK_NAME;
            snprintf(toks[ntoks].text, sizeof toks[ntoks].text, "%.*s", (int)(s - b), b);
            toks[ntoks].num = 0;
            ntoks++;
            continue;
        }
        if (is_digit(*s)) {
            uint64_t v = 0;
            if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
                s += 2;
                while (is_digit(*s) || (*s >= 'a' && *s <= 'f') || (*s >= 'A' && *s <= 'F')) {
                    int d = is_digit(*s) ? *s - '0' : (*s | 32) - 'a' + 10;
                    v = v * 16 + (uint64_t)d;
                    s++;
                }
            } else {
                while (is_digit(*s)) { v = v * 10 + (uint64_t)(*s - '0'); s++; }
            }
            toks[ntoks].kind = TK_NUM;
            toks[ntoks].text[0] = 0;
            toks[ntoks].num = v;
            ntoks++;
            continue;
        }
        /* operators: two characters first */
        toks[ntoks].kind = TK_OP;
        toks[ntoks].num = 0;
        if ((s[0] == '<' && s[1] == '<') || (s[0] == '>' && s[1] == '>') ||
            (s[0] == '=' && s[1] == '=') || (s[0] == '!' && s[1] == '=') ||
            (s[0] == '<' && s[1] == '=') || (s[0] == '>' && s[1] == '=')) {
            toks[ntoks].text[0] = s[0]; toks[ntoks].text[1] = s[1]; toks[ntoks].text[2] = 0;
            s += 2;
        } else {
            toks[ntoks].text[0] = s[0]; toks[ntoks].text[1] = 0;
            s++;
        }
        ntoks++;
    }
    toks[ntoks].kind = TK_EOF;
    toks[ntoks].text[0] = 0;
}

/* ---- macros (docs/29): `macro name(a, b) { ... }` is a block of statements expanded where
 * `name(x, y);` stands, `macro name(a) = expr;` an expression expanded where `name(x)` stands.
 * The expansion is over the token stream before the compile: the arguments splice where a
 * parameter name stands in the body (write parentheses around a parameter in the body when an
 * argument needs them). What a helper of an interpreter shares with its callers -- the status,
 * the found pointer -- is named at the use site this way, so the helpers exist once. */
static struct {
    char name[40];
    char params[8][40];
    int nparams;
    token body[MACRO_BODY];
    int nbody;
} macros[NMACROS];
static int nmacros;

static int macro_of(const char *name) {
    for (int i = 0; i < nmacros; i++) if (!strcmp(macros[i].name, name)) return i;
    return -1;
}

/* The definitions come out of the token stream; the rest stays for the compile. */
static void macro_defs(void) {
    int out = 0;
    nmacros = 0;
    for (int i = 0; toks[i].kind != TK_EOF;) {
        if (toks[i].kind != TK_NAME || strcmp(toks[i].text, "macro")) {
            toks[out++] = toks[i++];
            continue;
        }
        if (nmacros >= NMACROS || toks[i + 1].kind != TK_NAME ||
            strcmp(toks[i + 2].text, "(") != 0) {
            fprintf(stderr, "bad macro definition\n");
            exit(2);
        }
        int m = nmacros++;
        snprintf(macros[m].name, sizeof macros[m].name, "%s", toks[i + 1].text);
        macros[m].nparams = 0;
        i += 3;
        if (strcmp(toks[i].text, ")") != 0) {
            for (;;) {
                if (toks[i].kind != TK_NAME || macros[m].nparams >= 8) {
                    fprintf(stderr, "bad macro parameter\n");
                    exit(2);
                }
                snprintf(macros[m].params[macros[m].nparams], sizeof macros[m].params[0], "%s",
                         toks[i].text);
                macros[m].nparams++;
                i++;
                if (strcmp(toks[i].text, ",") == 0) { i++; continue; }
                break;
            }
        }
        if (strcmp(toks[i].text, ")") != 0) {
            fprintf(stderr, "bad macro parameter list\n");
            exit(2);
        }
        i++;
        macros[m].nbody = 0;
        if (strcmp(toks[i].text, "=") == 0) {
            for (i++; toks[i].kind != TK_EOF && strcmp(toks[i].text, ";") != 0; i++) {
                if (macros[m].nbody >= MACRO_BODY) { fprintf(stderr, "macro body too large\n"); exit(2); }
                macros[m].body[macros[m].nbody++] = toks[i];
            }
            i++; /* ; */
        } else {
            if (strcmp(toks[i].text, "{") != 0) {
                fprintf(stderr, "bad macro body\n");
                exit(2);
            }
            int depth = 0;
            for (i++;; i++) {          /* the body is the inside of the braces */
                if (toks[i].kind == TK_EOF) {
                    fprintf(stderr, "macro body never ends\n");
                    exit(2);
                }
                if (strcmp(toks[i].text, "{") == 0) depth++;
                else if (strcmp(toks[i].text, "}") == 0) {
                    if (depth == 0) break;
                    depth--;
                }
                if (macros[m].nbody >= MACRO_BODY) { fprintf(stderr, "macro body too large\n"); exit(2); }
                macros[m].body[macros[m].nbody++] = toks[i];
            }
            i++; /* } */
        }
    }
    toks[out].kind = TK_EOF;
    toks[out].text[0] = 0;
    ntoks = out;
    cur = 0;
}

/* Every macro use expanded, deepest first: one left-to-right walk splices the body where a use
 * stands and the raw argument tokens where a parameter name stands, then walks what it spliced
 * the same way. That is exactly the old fixpoint over the stream (a use's arguments land raw;
 * nested uses inside the body expand around them), but linear in the tokens produced: the
 * fixpoint respliced and rescanned the whole stream per use and spent minutes on a program of
 * this size. */
static token *mx_out;
static long mx_cap;
static int mx_n;
static int mx_depth;

static void mx_push(token t) {
    if (mx_n >= mx_cap) {
        mx_cap = mx_cap ? mx_cap * 2 : 65536;
        mx_out = (token *)realloc(mx_out, sizeof(token) * (size_t)mx_cap);
        if (!mx_out) { fprintf(stderr, "macro expansion too large\n"); exit(2); }
    }
    mx_out[mx_n++] = t;
}

static void mx_walk(const token *in, int len) {
    for (int i = 0; i < len; i++) {
        int m = -1;
        if (in[i].kind == TK_NAME && i + 1 < len && in[i + 1].kind == TK_OP &&
            in[i + 1].text[0] == '(')
            m = macro_of(in[i].text);
        if (m < 0) {
            mx_push(in[i]);
            continue;
        }
        int astart[8], aend[8], nargs = 0;
        int j = i + 2;
        if (j >= len || !(in[j].kind == TK_OP && in[j].text[0] == ')')) {
            for (;;) {
                if (j >= len || nargs >= (int)(sizeof astart / sizeof astart[0])) {
                    fprintf(stderr, "macro use never ends\n");
                    exit(2);
                }
                int depth = 0;
                astart[nargs] = j;
                for (;; j++) {
                    if (j >= len) {
                        fprintf(stderr, "macro use never ends\n");
                        exit(2);
                    }
                    if (in[j].kind == TK_OP && in[j].text[0] == '(') depth++;
                    else if (in[j].kind == TK_OP && in[j].text[0] == ')') {
                        if (depth == 0) break;
                        depth--;
                    } else if (in[j].kind == TK_OP && in[j].text[0] == ',' && depth == 0)
                        break;
                }
                aend[nargs] = j;
                nargs++;
                if (in[j].kind == TK_OP && in[j].text[0] == ',') { j++; continue; }
                break;
            }
        }
        if (j >= len || in[j].kind != TK_OP || in[j].text[0] != ')' || nargs != macros[m].nparams) {
            fprintf(stderr, "macro %s: %d arguments, wants %d\n", macros[m].name, nargs,
                    macros[m].nparams);
            exit(2);
        }
        if (mx_depth >= 64) {
            fprintf(stderr, "macro expansion budget spent (recursion?)\n");
            exit(2);
        }
        /* the body with its parameters replaced by the raw argument tokens; a parameter name
         * may stand twice, each occurrence carrying its whole argument span */
        int need = macros[m].nbody;
        for (int k = 0; k < macros[m].nbody; k++)
            if (macros[m].body[k].kind == TK_NAME)
                for (int p = 0; p < macros[m].nparams; p++)
                    if (strcmp(macros[m].body[k].text, macros[m].params[p]) == 0) {
                        need += (aend[p] - astart[p]) - 1;
                        break;
                    }
        token *tmp = (token *)malloc(sizeof(token) * (size_t)(need + 1));
        int n = 0;
        for (int k = 0; k < macros[m].nbody; k++) {
            token t = macros[m].body[k];
            int replaced = 0;
            if (t.kind == TK_NAME)
                for (int p = 0; p < macros[m].nparams && !replaced; p++)
                    if (strcmp(t.text, macros[m].params[p]) == 0) {
                        for (int q = astart[p]; q < aend[p]; q++) tmp[n++] = in[q];
                        replaced = 1;
                    }
            if (!replaced) tmp[n++] = t;
        }
        mx_depth++;
        mx_walk(tmp, n);
        mx_depth--;
        free(tmp);
        i = j;
    }
}

static void macro_expand(void) {
    mx_n = 0;
    mx_depth = 0;
    mx_walk(toks, ntoks);
    if (mx_n + 1 > toks_cap) {
        toks_cap = mx_n + 1;
        toks = (token *)realloc(toks, sizeof(token) * (size_t)toks_cap);
        if (!toks) { fprintf(stderr, "macro expansion too large\n"); exit(2); }
    }
    for (int k = 0; k < mx_n; k++) toks[k] = mx_out[k];
    toks[mx_n].kind = TK_EOF;
    toks[mx_n].text[0] = 0;
    ntoks = mx_n;
}

static int peek_is(const char *t) { return toks[cur].kind != TK_EOF && strcmp(toks[cur].text, t) == 0; }
static int take(const char *t) { if (peek_is(t)) { cur++; return 1; } return 0; }
static void expect(const char *t) {
    if (!take(t)) {
        fprintf(stderr, "expected %s, got %s (token %d:", t, toks[cur].text, cur);
        for (int k = cur > 6 ? cur - 6 : 0; k < cur + 4 && toks[k].kind != TK_EOF; k++)
            fprintf(stderr, " %s", toks[k].text);
        fprintf(stderr, ")\n");
        exit(2);
    }
}

/* ---- the parser-codegen ------------------------------------------------------------------------ */

static int binop_code(const char *op) {
    if (!strcmp(op, "+")) return OP_ADD;
    if (!strcmp(op, "-")) return OP_SUB;
    if (!strcmp(op, "*")) return OP_MUL;
    if (!strcmp(op, "/")) return OP_DIV;
    if (!strcmp(op, "%")) return OP_MOD;
    if (!strcmp(op, "&")) return OP_AND;
    if (!strcmp(op, "|")) return OP_OR;
    if (!strcmp(op, "^")) return OP_XOR;
    if (!strcmp(op, "<<")) return OP_SHL;
    if (!strcmp(op, ">>")) return OP_SHR;
    if (!strcmp(op, "==")) return OP_EQ;
    if (!strcmp(op, "!=")) return OP_NE;
    if (!strcmp(op, "<")) return OP_LT;
    if (!strcmp(op, "<=")) return OP_LE;
    if (!strcmp(op, ">")) return OP_GT;
    if (!strcmp(op, ">=")) return OP_GE;
    return -1;
}
static int prec_of(const char *op) {
    if (!strcmp(op, "|")) return 1;
    if (!strcmp(op, "^")) return 2;
    if (!strcmp(op, "&")) return 3;
    if (!strcmp(op, "==") || !strcmp(op, "!=")) return 4;
    if (!strcmp(op, "<") || !strcmp(op, "<=") || !strcmp(op, ">") || !strcmp(op, ">=")) return 5;
    if (!strcmp(op, "<<") || !strcmp(op, ">>")) return 6;
    if (!strcmp(op, "+") || !strcmp(op, "-")) return 7;
    if (!strcmp(op, "*") || !strcmp(op, "/") || !strcmp(op, "%")) return 8;
    return -1;
}

static void expr(int min_prec); /* emits code; result in rax */

/* An intrinsic call: `fadd(a, b)`, `load8(a)`, `store8(a, v)`. The operands are
 * expressions in the same accumulator model as the operators; the callee is muscle. */
/* ---- shaped calls ------------------------------------------------------------------------------
 * `call_<shape>(f, a, b, ...)` is an expression: the callee's address first, then the arguments
 * of its object shape spelled flat (a handle takes its three expressions: id, generation, offset).
 * The result is the callee's rxf_u32, widened to the i64 the expressions run in. The words park
 * below the stack (x86_64/aarch64) or in the module's stack area under global 0 (wasm32, where
 * the value stack holds the arguments and a handle goes as the address of a copy), then the
 * shape's convention places them: SysV registers and stack, AAPCS64 registers (a handle as the
 * address of its copy) and stack, or a call_indirect of the type the codegen was given after
 * the `@<shape>` entries of its argv (the index of the shape in that list). */
static char call_shape[64][16];
static int ncall_shapes;
static long call_extra;         /* the bytes every open call reserved below the stack */
static long call_stack_out;     /* where the stack operands of the open call start */

static const uint8_t x_mov_reg_rsp_rex[] = { 0x48, 0x48, 0x48, 0x48, 0x4C, 0x4C };
static const uint8_t x_mov_reg_rsp_modrm[] = { 0xBC, 0xB4, 0x94, 0x8C, 0x84, 0x8C };
/* the SysV integer bank in order */
/* the SysV register number of the k-th integer argument, in the order rdi..r9 (kept as the
 * bank the frame-word copies name). */

static void shaped_call(const char *shape); /* defined below; the wasm tail call reuses it */
static void be_park(int k);

/* pc(): the address of the running body -- `lea rax,[rip]` / `adr x0,.` / 0 on wasm32 (where the
 * C++ has no pc either). `own_body`'s "this very instruction is inside the entry" check needs it. */
static void be_pc(void) {
    if (target == T_X64) {
        e8(0x48); e8(0x8D); e8(0x05); e32(0);    /* lea rax, [rip+0] (the next instruction) */
    } else if (target == T_ARM64) {
        e32(0x10000000u);                        /* adr x0, . (this instruction) */
    } else {
        w8(0x42); wsleb(0);                      /* i64.const 0 */
    }
}

/* tailcall_pp(target, a, b): leave this frame for good and enter the pp callee with (a, b).
 * The compaction dance of native/live.cpp jumps into a copy of its own body (the arena can move
 * the original), so the call must never come back through these bytes: the frame is released
 * first and the last instruction is a jump to the target ([[clang::musttail]] in the C++).
 * wasm32 cannot jump out of a body: it calls and returns the status (the C there never takes
 * the dance either). */
static void be_tailcall_pp(void) {
    if (target == T_WASM) {
        shaped_call("pp");
        wasm_regions_close();
        w8(0xA7);              /* i32.wrap_i64 (the callee's status) */
        w8(0x0F);              /* return */
        return;
    }
    long total = 8 * 3;
    long pad = (16 - ((alloc + call_extra + total) & 15)) & 15;
    total += pad;
    call_stack_out = pad;
    if (target == T_X64) {
        e8(0x48); e8(0x81); e8(0xEC); e32((uint32_t)total);   /* sub rsp, total */
    } else {
        a_sp_off(1, (uint32_t)total);
    }
    call_extra += total;
    expect("(");
    expr(0); be_park(0);                                    /* the target */
    expect(","); expr(0); be_park(1);                       /* the first argument */
    expect(","); expr(0); be_park(2);                       /* the second argument */
    expect(")");
    if (target == T_X64) {
        e8(0x48); e8(0x8B); e8(0xBC); e8(0x24); e32((uint32_t)(call_stack_out + 8));   /* mov rdi, [rsp+..] */
        e8(0x48); e8(0x8B); e8(0xB4); e8(0x24); e32((uint32_t)(call_stack_out + 16));  /* mov rsi, [rsp+..] */
        e8(0x48); e8(0x8B); e8(0x84); e8(0x24); e32((uint32_t)call_stack_out);         /* mov rax, [rsp+..] */
        e8(0x48); e8(0x81); e8(0xC4); e32((uint32_t)total);  /* add rsp, total */
        call_extra -= total;
        e8(0x48); e8(0x81); e8(0xC4); e32((uint32_t)alloc); /* add rsp, alloc */
        e8(0x41); e8(0x5F);                                  /* pop r15 */
        e8(0x5B);                                            /* pop rbx */
        e8(0x5D);                                            /* pop rbp */
        e8(0xFF); e8(0xE0);                                  /* jmp rax */
        return;
    }
    a_ldr(0, 31, (uint32_t)(call_stack_out + 8));            /* ldr x0, [sp, #..] */
    a_ldr(1, 31, (uint32_t)(call_stack_out + 16));           /* ldr x1, [sp, #..] */
    a_ldr(9, 31, (uint32_t)call_stack_out);                  /* ldr x9, [sp, #..] */
    a_sp_off(0, (uint32_t)total);
    call_extra -= total;
    a_sp_off(0, (uint32_t)alloc);                            /* the a_epilogue teardown */
    a_ldp_post(19, 20, 31, 2);
    a_ldp_post(29, 30, 31, 2);
    e32(0xD61F0000u | (9u << 5));                            /* br x9 */
}

static void be_park(int k) {           /* store the accumulator as call word k */
    if (target == T_X64) {
        e8(0x48); e8(0x89); e8(0x84); e8(0x24); e32((uint32_t)(call_stack_out + 8 * k)); /* mov [rsp+..], rax */
    } else if (target == T_ARM64) {
        a_str(0, 31, (uint32_t)(call_stack_out + 8 * k));    /* str x0, [sp, #..] */
    }
    /* wasm: the value stack holds it (a handle is stored as its words by shaped_call) */
}

static void shaped_call(const char *shape) {
    int cti = -1;
    for (int i = 0; i < ncall_shapes && cti < 0; i++)
        if (!strcmp(call_shape[i], shape)) cti = i;   /* the first entry of the shape */
    /* The call types name signatures: shapes with the same wasm signature share its
     * index (the composer's type section names each signature once), so a shape not
     * spelled in the list takes the entry whose letters have the same i32/i64 row. */
    for (int i = 0; i < ncall_shapes && cti < 0; i++) {
        const char *a = call_shape[i], *b = shape;
        int same = 1;
        while (*a && *b && same) {
            if ((*a == 'q') != (*b == 'q')) same = 0;
            a++;
            b++;
        }
        if (same && !*a && !*b) cti = i;
    }
    if (cti < 0 && target == T_WASM) {
        fprintf(stderr, "call_%s: no @%s in the codegen's call types\n", shape, shape);
        exit(2);
    }
    int letter[64], wbase[64], nletters = 0, nwords = 1;   /* word 0 is the target */
    for (const char *s = shape; *s; s++, nletters++) {
        if (*s != 'H' && *s != 'p' && *s != 'q' && *s != 'd' && *s != 'w' && *s != 'b') {
            fprintf(stderr, "call_%s: bad shape letter %c\n", shape, *s);
            exit(2);
        }
        wbase[nletters] = nwords;
        letter[nletters] = *s;
        nwords += (*s == 'H') ? 3 : 1;
    }
    /* the class walk: what goes in a register and what on the stack */
    int kind[64], reg[64];
    long at[64], so = 0;
    int nint = 0;
    for (int k = 0; k < nletters; k++) {
        if (letter[k] == 'H' && target == T_X64) { kind[k] = 2; at[k] = so; so += 24; }
        else if (letter[k] == 'H') { kind[k] = 0; reg[k] = nint++; }   /* arm/wasm: the copy's address */
        else if (nint < (target == T_ARM64 ? 8 : 6)) { kind[k] = 0; reg[k] = nint++; }
        else { kind[k] = 2; at[k] = so; so += 8; }
    }
    long park = 8 * nwords, out = so;
    if (getenv("SEED_DEBUG")) fprintf(stderr, "shaped_call %s: nletters=%d nwords=%d\n", shape, nletters, nwords);
    long total = park + out;
    long pad = (16 - ((alloc + call_extra + total) & 15)) & 15;
    total += pad;
    call_stack_out = out + pad;
    if (target == T_X64) {
        e8(0x48); e8(0x81); e8(0xEC); e32((uint32_t)total);   /* sub rsp, total */
        call_extra += total;
        expect("(");
        expr(0);
        be_park(0);                                            /* the target */
        for (int k = 0; k < nletters; k++) {
            expect(",");
            if (letter[k] == 'H') {
                for (int j = 0; j < 3; j++) { if (j) expect(","); expr(0); be_park(wbase[k] + j); }
            } else { expr(0); be_park(wbase[k]); }
        }
        expect(")");
        for (int k = 0; k < nletters; k++) {                   /* the stack operands first */
            if (kind[k] != 2) continue;
            for (int j = 0; j < (letter[k] == 'H' ? 3 : 1); j++) {
                e8(0x48); e8(0x8B); e8(0x84); e8(0x24); e32((uint32_t)(call_stack_out + 8 * (wbase[k] + j)));
                e8(0x48); e8(0x89); e8(0x84); e8(0x24); e32((uint32_t)(at[k] + 8 * j));
            }
        }
        for (int k = 0; k < nletters; k++) {                   /* then the registers */
            if (kind[k] != 0) continue;
            e8(x_mov_reg_rsp_rex[reg[k]]); e8(0x8B); e8(x_mov_reg_rsp_modrm[reg[k]]);
            e8(0x24); e32((uint32_t)(call_stack_out + 8 * wbase[k]));
        }
        e8(0x48); e8(0x8B); e8(0x84); e8(0x24); e32((uint32_t)call_stack_out);  /* mov rax, [rsp+..] */
        e8(0xFF); e8(0xD0);                                    /* call rax */
        e8(0x48); e8(0x81); e8(0xC4); e32((uint32_t)total);    /* add rsp, total */
        call_extra -= total;
        e8(0x89); e8(0xC0);                                    /* mov eax, eax (zero the status) */
        return;
    }
    if (target == T_ARM64) {
        a_sp_off(1, (uint32_t)total);
        call_extra += total;
        expect("(");
        expr(0);
        be_park(0);
        for (int k = 0; k < nletters; k++) {
            expect(",");
            if (letter[k] == 'H') {
                for (int j = 0; j < 3; j++) { if (j) expect(","); expr(0); be_park(wbase[k] + j); }
            } else { expr(0); be_park(wbase[k]); }
        }
        expect(")");
        for (int k = 0; k < nletters; k++) {
            if (kind[k] != 2) continue;
            for (int j = 0; j < (letter[k] == 'H' ? 3 : 1); j++) {
                a_ldr(9, 31, (uint32_t)(call_stack_out + 8 * (wbase[k] + j)));
                a_str(9, 31, (uint32_t)(at[k] + 8 * j));
            }
        }
        for (int k = 0; k < nletters; k++) {
            if (kind[k] != 0) continue;
            if (letter[k] == 'H') a_add_imm((uint32_t)reg[k], 31, (uint32_t)(call_stack_out + 8 * wbase[k]));
            else a_ldr((uint32_t)reg[k], 31, (uint32_t)(call_stack_out + 8 * wbase[k]));
        }
        a_ldr(9, 31, (uint32_t)call_stack_out);
        e32(0xD63F0000u | (9u << 5));                          /* blr x9 */
        a_sp_off(0, (uint32_t)total);
        call_extra -= total;
        a_uxtw(0, 0);                                          /* uxtw x0, w0 (zero the status) */
        return;
    }
    /* wasm32: the value stack holds the arguments; the target and every handle word go to the
     * area the global-0 stack pointer makes room for, and the handle's value is its address */
    long area = 8 * nwords;
    w8(0x23); wuleb(0); w8(0x41); wsleb(area); w8(0x6B); w8(0x24); wuleb(0);  /* global0 -= area */
    expect("(");
    w8(0x23); wuleb(0); expr(0); w8(0x37); wuleb(3); wuleb(0);  /* store the target at word 0 */
    for (int k = 0; k < nletters; k++) {
        expect(",");
        if (letter[k] == 'H') {
            w8(0x23); wuleb(0); w8(0x41); wsleb(8 * wbase[k]); w8(0x6A);   /* the copy's address */
            for (int j = 0; j < 3; j++) {
                if (j) expect(",");
                w8(0x23); wuleb(0); expr(0); w8(0x37); wuleb(3); wuleb((uint64_t)(8 * (wbase[k] + j)));
            }
        } else {
            expr(0);
            if (letter[k] != 'q') w8(0xA7);                    /* i32.wrap_i64 */
        }
    }
    expect(")");
    w8(0x23); wuleb(0); w8(0x29); wuleb(3); wuleb(0); w8(0xA7); /* the target: i64.load, wrap */
    w8(0x11); wuleb((uint64_t)cti); w8(0x00);                   /* call_indirect (table 0) */
    w8(0x23); wuleb(0); w8(0x41); wsleb(area); w8(0x6A); w8(0x24); wuleb(0);  /* global0 += area */
    w8(0xAD);                                                   /* i64.extend_i32_u (the status) */
}

static void call(void) {
    const char *name = toks[cur].text;
    if (!strncmp(name, "call_", 5)) { cur++; shaped_call(name + 5); return; }
    if (!strcmp(name, "pc")) { cur += 2; expect(")"); be_pc(); return; }
    if (!strcmp(name, "host")) {
        /* host(): the machine this codegen makes code for, folded to a constant at
         * compile time (0 x86_64, 1 aarch64, 2 wasm32 -- the T_* order). A port whose
         * C has `#if defined(__x86_64__)` style target seams (native/compile.rxfs's
         * be_* encoders) branches on it; every branch folds away per seat. */
        cur += 2;
        expect(")");
        be_const((uint64_t)target);
        return;
    }
    if (!strcmp(name, "in8") || !strcmp(name, "out8")) {
        int is_out = name[1] == 'u';
        cur += 2;
        expr(0);                     /* the port (an address is not: it is an I/O number) */
        if (is_out) { be_save(); expect(","); expr(0); be_out8(); }
        else be_in8();
        expect(")");
        return;
    }
    int fop = -1, w = 0, store = 0;
    if (!strcmp(name, "fadd")) fop = OP_ADD;
    else if (!strcmp(name, "fsub")) fop = OP_SUB;
    else if (!strcmp(name, "fmul")) fop = OP_MUL;
    else if (!strcmp(name, "fdiv")) fop = OP_DIV;
    else if (!strcmp(name, "load8")) w = 1;
    else if (!strcmp(name, "load16")) w = 2;
    else if (!strcmp(name, "load32")) w = 4;
    else if (!strcmp(name, "load64")) w = 8;
    else if (!strcmp(name, "store8")) { w = 1; store = 1; }
    else if (!strcmp(name, "store16")) { w = 2; store = 1; }
    else if (!strcmp(name, "store32")) { w = 4; store = 1; }
    else if (!strcmp(name, "store64")) { w = 8; store = 1; }
    else { fprintf(stderr, "unknown function %s\n", name); exit(2); }
    cur += 2;
    expr(0);
    if (w && !store) { be_mload(w); expect(")"); return; }   /* one operand: the address */
    expect(",");
    be_save();       /* save the address/first operand */
    expr(0);         /* the value -> accumulator */
    if (w) be_mstore(w); else be_fbinop(fop);
    expect(")");
}

static void primary(void) {
    if (toks[cur].kind == TK_NUM) {
        be_const(toks[cur].num);
        cur++;
    } else if (peek_is("(")) {
        cur++;
        expr(0);
        expect(")");
    } else if (peek_is("-")) {
        cur++;
        primary();
        be_unop(0);
    } else if (peek_is("~")) {
        cur++;
        primary();
        be_unop(1);
    } else if (peek_is("!")) {
        cur++;
        primary();
        be_unop(2);
    } else if (toks[cur].kind == TK_NAME && toks[cur + 1].kind == TK_OP &&
               toks[cur + 1].text[0] == '(') {
        call();
    } else if (toks[cur].kind == TK_NAME) {
        int idx = find_sym_index(toks[cur].text);
        if (idx < 0) { fprintf(stderr, "unknown variable %s\n", toks[cur].text); exit(2); }
        if (syms[idx].roff >= 0) be_load_region(idx);  /* a region's value is its address */
        else be_load_var(syms[idx].slot);
        cur++;
    } else {
        fprintf(stderr, "unexpected token %s\n", toks[cur].text);
        exit(2);
    }
}

static void expr(int min_prec) {
    primary();
    for (;;) {
        if (toks[cur].kind != TK_OP) break;
        int prec = prec_of(toks[cur].text);
        int code = binop_code(toks[cur].text);
        if (prec < min_prec || code < 0) break;
        cur++;
        be_save();         /* save lhs */
        expr(prec + 1);    /* rhs -> accumulator */
        be_binop(code);    /* accumulator = lhs OP rhs */
    }
}

static void block(void);

static int fn_returned;   /* set when a top-level `return`/`refuse` already emitted the exit */
static int block_depth;   /* 1 inside the function body, more inside if/while blocks */

/* subroutines (defined below the parser): a body that shares the caller's frame and variables */
typedef struct { const char *name; int bytes; int region; } seed_decl;
static struct {
    char name[40];
    size_t start, end;         /* the token range of the body's braces */
    size_t offset;             /* where it is emitted (x86_64/aarch64) */
    size_t fixups[256];        /* call sites that name it before it is emitted */
    int nfix;
    int used;
    int spliced; /* consumed by sub_expand (its body or arg form reached a fn body) */
    char params[8][40];        /* the argument names: assigned before the call, read by the body */
    int nparams;
} stab[64];
static int nsubs;
static int in_sub;
static int sub_index(const char *name);
static void sub_mark_used(size_t from, size_t to);
static void sub_scan_decls(size_t from, size_t to, seed_decl *decls, int *ndecls);
static void sub_emit_used(size_t from, size_t to);

static void stmt(void) {
    if (take(";")) {
        return; /* the empty statement a statement-macro use leaves behind */
    } else if (take("var")) {
        do {
            const char *name = toks[cur].text;
            int bytes = 8, region = 0;
            cur++;
            if (take("[")) { bytes = (int)toks[cur].num; region = 1; cur++; expect("]"); }
            add_sym_sized(name, bytes, region);
        } while (take(","));
        expect(";");
    } else if (take("if")) {
        expr(0);
        be_if_t h = be_if_open();
        block();
        if (take("else")) {
            be_else(&h);
            block();
        }
        be_end(&h);
    } else if (take("while")) {
        be_loop_t l = be_loop_open();
        expr(0);
        be_loop_break(&l);
        block();
        be_loop_close(&l);
    } else if (take("refuse")) {
        /* refuse <status>: the RXF_REFUSE_* early return (a status, no store through out).
         * `refuse expr;` takes a dynamic status -- OP_REFUSE in the graph interpreter returns the
         * status a REFUSE opcode names, which lives in a frame slot, not in a literal. */
        if (in_sub) { fprintf(stderr, "refuse in a sub\n"); exit(2); }
        if (toks[cur].kind == TK_NUM) {
            uint64_t status = toks[cur].num;
            cur++;
            be_refuse(status);
        } else {
            expr(0);
            be_refuse_acc();
        }
        if (block_depth == 1) fn_returned = 1;
        expect(";");
    } else if (take("return")) {
        if (in_sub) { fprintf(stderr, "return in a sub\n"); exit(2); }
        expr(0);
        be_return();
        if (block_depth == 1) fn_returned = 1;
        expect(";");
    } else if (toks[cur].kind == TK_NAME && !strcmp(toks[cur].text, "callsub")) {
        /* `callsub NAME;`: the sub's body runs in this frame (x86_64/aarch64 a call, wasm32
         * spliced inline before parsing). No arguments: a sub reads and writes shared vars. */
        cur++;
        int s = sub_index(toks[cur].text);
        if (s < 0) { fprintf(stderr, "no sub %s\n", toks[cur].text); exit(2); }
        cur++;
        expect(";");
        if (target == T_WASM) { fprintf(stderr, "callsub survived to wasm codegen\n"); exit(2); }
        if (stab[s].nfix >= (int)(sizeof stab[s].fixups / sizeof stab[s].fixups[0])) {
            fprintf(stderr, "too many calls of one sub\n");
            exit(2);
        }
        if (target == T_X64) { e8(0xE8); stab[s].fixups[stab[s].nfix++] = at; e32(0); }
        else { stab[s].fixups[stab[s].nfix++] = a_bl(); }
    } else if (toks[cur].kind == TK_NAME && !strcmp(toks[cur].text, "tailcall_pp")) {
        cur++;
        be_tailcall_pp();    /* leaves this frame for good (the self-copy's entry) */
        expect(";");
    } else if (toks[cur].kind == TK_NAME && toks[cur + 1].kind == TK_OP &&
               toks[cur + 1].text[0] == '(') {
        call();          /* `store8(p, v);` and friends: an expression statement */
        if (target == T_WASM) w8(0x1A);   /* drop: the statement keeps no value */
        expect(";");
    } else if (toks[cur].kind == TK_NAME && toks[cur + 1].kind == TK_OP &&
               strcmp(toks[cur + 1].text, "=") == 0) {
        int idx = find_sym_index(toks[cur].text);
        if (idx < 0) { fprintf(stderr, "unknown variable %s\n", toks[cur].text); exit(2); }
        if (syms[idx].roff >= 0) {
            fprintf(stderr, "cannot assign to a region: %s\n", toks[cur].text);
            exit(2);
        }
        cur += 2;
        expr(0);
        be_store_var(syms[idx].slot);
        expect(";");
    } else {
        fprintf(stderr, "unexpected statement %s\n", toks[cur].text);
        exit(2);
    }
}

static void block(void) {
    expect("{");
    block_depth++;
    while (!peek_is("}") && toks[cur].kind != TK_EOF) stmt();
    block_depth--;
    expect("}");
}

/* the compiled module: one entry per `fn`. offset = the x86_64 code offset; wstart/wsize = the
 * wasm32 body range. */
static struct { char name[40]; size_t offset, wstart, wsize; } ftab[64];
static int nfns;

/* parse `fn NAME(params) { ... }` and emit the whole function at the current position. */
static void fn_def(void) {
    if (nfns >= (int)(sizeof ftab / sizeof ftab[0])) {
        fprintf(stderr, "too many fns in one module\n");
        exit(2);
    }
    expect("fn");
    snprintf(ftab[nfns].name, sizeof ftab[nfns].name, "%s", toks[cur].text);
    ftab[nfns].offset = at;
    ftab[nfns].wstart = wat;
    cur++; /* the function name */
    expect("(");
    nsyms = 0;
    frame = 0;
    region_bytes = 0;
    wasm_region_local = -1;
    int nparams = 0;
    if (!peek_is(")")) {
        do { add_sym(toks[cur].text); cur++; nparams++; } while (take(","));
    }
    expect(")");
    /* the body's `var` names take their frame slots now: the prologue that follows must
     * allocate the whole frame, and statement parsing re-adds them to the same slots. The
     * scalars take the first slots and the regions the space after them (so a region never
     * pushes a scalar's slot past what the wasm32 locals cover). */
    seed_decl decls[8192];
    int ndecls = 0;
    size_t body_end = cur;
    for (int i = cur, depth = 0; toks[i].kind != TK_EOF; i++) {
        if (toks[i].kind == TK_OP && toks[i].text[0] == '{') depth++;
        else if (toks[i].kind == TK_OP && toks[i].text[0] == '}') {
            body_end = i + 1;
            if (--depth == 0) break;
        } else if (depth >= 1 && toks[i].kind == TK_NAME && strcmp(toks[i].text, "var") == 0) {
            for (i++; toks[i].kind != TK_EOF; i++) {
                if (toks[i].kind == TK_OP && toks[i].text[0] == ';') break;
                if (toks[i].kind == TK_NAME) {
                    if (ndecls >= 8192) { fprintf(stderr, "too many declarations\n"); exit(2); }
                    const char *name = toks[i].text;
                    int bytes = 8, region = 0;   /* `name[9152]` is a region of that many frame bytes */
                    if (toks[i + 1].kind == TK_OP && toks[i + 1].text[0] == '[' &&
                        toks[i + 2].kind == TK_NUM &&
                        toks[i + 3].kind == TK_OP && toks[i + 3].text[0] == ']') {
                        bytes = (int)toks[i + 2].num;
                        region = 1;
                        i += 3;
                    }
                    decls[ndecls].name = name;
                    decls[ndecls].bytes = bytes;
                    decls[ndecls].region = region;
                    ndecls++;
                }
            }
        }
    }
    /* the vars of the subs this body carries share the frame: their declarations count too.
     * A spliced (wasm32) sub's body is inside the fn already — its `var` lines came with the
     * scan above — so only its parameters still need slots here. */
    sub_mark_used(cur, body_end);
    for (int s = 0; s < nsubs; s++)
        if (stab[s].used || stab[s].spliced) {
            for (int k = 0; k < stab[s].nparams; k++) {
                if (ndecls >= 8192) { fprintf(stderr, "too many declarations\n"); exit(2); }
                decls[ndecls].name = stab[s].params[k];
                decls[ndecls].bytes = 8;
                ndecls++;
            }
            if (stab[s].used && !stab[s].spliced)
                sub_scan_decls(stab[s].start, stab[s].end, decls, &ndecls);
        }
    for (int pass = 0; pass < 2; pass++)
        for (int d = 0; d < ndecls; d++)
            if ((decls[d].region == 1) == (pass == 1))
                add_sym_sized(decls[d].name, decls[d].bytes, decls[d].region);
    frame = (frame + 15) & ~(size_t)15; /* the frame is 16-byte aligned */
    alloc = (frame + region_bytes + 15) & ~(size_t)15; /* the regions of the frame too */

    be_prologue(nparams);
    fn_returned = 0;
    block();
    /* a function that falls off the end returns 0 (skip if it already returned) */
    if (!fn_returned) {
        be_const(0);
        be_return();
    }
    if (target == T_WASM) {
        w8(0x0B);                                 /* end (the body's single close) */
        /* the locals are the scalars of the frame (slot/8 + a fixed shift); their count was
           emitted with the local group at the prologue (it is final by then). */
        ftab[nfns].wsize = wat - ftab[nfns].wstart;
    }
    nfns++;
}

/* Skip one `fn` definition without compiling it (the `#<fn>` selection). The head's
 * parameter list and the body's braces are walked over; macros stay defined. */
static void fn_skip(void) {
    expect("fn");
    cur++;                                       /* the fn name */
    int depth = 0;
    while (toks[cur].kind != TK_EOF) {
        if (toks[cur].kind == TK_OP && toks[cur].text[0] == '(') depth++;
        else if (toks[cur].kind == TK_OP && toks[cur].text[0] == ')') depth--;
        else if (toks[cur].kind == TK_OP && toks[cur].text[0] == '{' && depth == 0) break;
        cur++;
    }
    depth = 0;
    do {
        if (toks[cur].kind == TK_EOF) { fprintf(stderr, "fn body never ends\n"); exit(2); }
        if (toks[cur].kind == TK_OP && toks[cur].text[0] == '{') depth++;
        else if (toks[cur].kind == TK_OP && toks[cur].text[0] == '}') depth--;
        cur++;
    } while (depth > 0);
}

/* ---- subroutines ------------------------------------------------------------------------------
 * `sub NAME() { ... }` is a body that shares the caller's frame and its variables -- the code
 * sharing a macro cannot give (a macro's body is spliced at every use; a sub is emitted once).
 * `callsub NAME;` calls it: on x86_64 a `call rel32`, on aarch64 a `bl`, so a copy of the body
 * that carries its subs (the live pump copies itself out of a compaction) still calls them; on
 * wasm32 the use is spliced inline. A sub takes no arguments and returns nothing: it reads and
 * writes the shared `var`s of the fn. Its own `var`s are part of the same frame. No recursion. */
static int sub_index(const char *name) {
    for (int i = 0; i < nsubs; i++) if (!strcmp(stab[i].name, name)) return i;
    return -1;
}

/* `sub NAME() { ... }` at cur: record its body's token range and walk over it. */
static void sub_record(void) {
    if (nsubs >= (int)(sizeof stab / sizeof stab[0])) {
        fprintf(stderr, "too many subs in one module\n");
        exit(2);
    }
    cur++;                                        /* the `sub` keyword */
    snprintf(stab[nsubs].name, sizeof stab[nsubs].name, "%s", toks[cur].text);
    cur++;
    expect("(");
    stab[nsubs].nparams = 0;
    if (!peek_is(")")) {
        do {
            if (stab[nsubs].nparams >= 8) { fprintf(stderr, "a sub takes 8 arguments\n"); exit(2); }
            snprintf(stab[nsubs].params[stab[nsubs].nparams],
                     sizeof stab[nsubs].params[0], "%s", toks[cur].text);
            stab[nsubs].nparams++;
            cur++;
        } while (take(","));
    }
    expect(")");
    stab[nsubs].start = cur;                      /* the `{` of the body */
    stab[nsubs].nfix = 0;
    stab[nsubs].used = 0;
    stab[nsubs].spliced = 0;
    int depth = 0;
    while (toks[cur].kind != TK_EOF) {
        if (toks[cur].kind == TK_OP && toks[cur].text[0] == '{') depth++;
        else if (toks[cur].kind == TK_OP && toks[cur].text[0] == '}') {
            if (--depth == 0) { cur++; break; }
        }
        cur++;
    }
    stab[nsubs].end = cur;
    nsubs++;
}

/* Every `sub` of the module, before anything is parsed: the call sites and the frame's decls
 * need the whole table whether the sub is written before or after the fn that calls it. */
static void sub_collect(void) {
    int was = cur;
    int depth = 0;
    cur = 0;
    while (toks[cur].kind != TK_EOF) {
        if (depth == 0 && toks[cur].kind == TK_NAME && !strcmp(toks[cur].text, "sub")) {
            sub_record();
            continue;
        }
        if (toks[cur].kind == TK_OP && toks[cur].text[0] == '{') depth++;
        else if (toks[cur].kind == TK_OP && toks[cur].text[0] == '}') depth--;
        cur++;
    }
    cur = was;
}

/* The `callsub NAME` uses of one token range, transitively: which subs a body carries. */
static void sub_mark_used(size_t from, size_t to) {
    for (size_t i = from; i + 1 < to; i++) {
        if (toks[i].kind != TK_NAME || strcmp(toks[i].text, "callsub")) continue;
        if (toks[i + 1].kind != TK_NAME) continue;
        int s = sub_index(toks[i + 1].text);
        if (s < 0) { fprintf(stderr, "no sub %s\n", toks[i + 1].text); exit(2); }
        if (stab[s].used) continue;
        stab[s].used = 1;
        sub_mark_used(stab[s].start, stab[s].end);
    }
}

/* The `var` declarations of one token range (the scan fn_def does over its own body). */
static void sub_scan_decls(size_t from, size_t to, seed_decl *decls, int *ndecls) {
    for (size_t i = from; i < to; i++) {
        if (toks[i].kind != TK_NAME || strcmp(toks[i].text, "var")) continue;
        /* until the ';' of the declaration line: every name, commas and [N] aside. The
         * fn-body scan sees the same shapes; a missed name would otherwise take a fresh
         * scalar slot where the regions live and overlap a buffer frame slot. */
        for (i++; i < to; i++) {
            if (toks[i].kind == TK_OP && toks[i].text[0] == ';') break;
            if (toks[i].kind != TK_NAME) continue;
            if (*ndecls >= 8192) { fprintf(stderr, "too many declarations\n"); exit(2); }
            decls[*ndecls].name = toks[i].text;
            decls[*ndecls].bytes = 8;
            decls[*ndecls].region = 0;
            if (i + 3 < to && toks[i + 1].kind == TK_OP && toks[i + 1].text[0] == '[' &&
                toks[i + 2].kind == TK_NUM && toks[i + 3].kind == TK_OP &&
                toks[i + 3].text[0] == ']') {
                decls[*ndecls].bytes = (int)toks[i + 2].num;
                decls[*ndecls].region = 1;
                i += 3;
            }
            (*ndecls)++;
        }
    }
}

/* Emit the subs a body carries, once each, after the fn: their call sites are patched to land
 * where each is. A sub reads and writes the fn's frame -- no prologue of its own. The fixups are
 * patched after every body is out: a sub that calls a sub emitted before it records its call
 * sites only while its own body is parsed, which is after the earlier sub's patch moment. */
static void sub_emit_used(size_t from, size_t to) {
    for (int s = 0; s < nsubs; s++) stab[s].used = 0;
    sub_mark_used(from, to);
    for (int s = 0; s < nsubs; s++) {
        if (!stab[s].used) continue;
        stab[s].offset = at;
        int was_cur = cur;
        cur = (int)stab[s].start;
        in_sub = 1;
        /* A sub shares the fn's frame and needs no frame prologue -- but it still owns its
         * link register: a nested `callsub` is a `bl`, which clobbers x30 and would drop
         * the sub's own `ret` into its tail (an infinite loop). Park x30 across the body.
         * x86_64 `call` pushes its return address on the stack and needs no save; wasm32
         * splices the body inline and has no link register. */
        if (target == T_ARM64) a_str_pre(30, 31, -16);   /* str x30,[sp,#-16]! */
        block();
        in_sub = 0;
        cur = was_cur;
        if (target == T_X64) e8(0xC3);            /* ret */
        else if (target == T_ARM64) {
            a_ldr_post(30, 31, 16);               /* ldr x30,[sp],#16 */
            a_ret();
        }
    }
    for (int s = 0; s < nsubs; s++) {
        if (!stab[s].used) continue;
        for (int f = 0; f < stab[s].nfix; f++) {
            if (target == T_X64) x_patch(stab[s].fixups[f], stab[s].offset);
            else a_patch(stab[s].fixups[f], stab[s].offset);
        }
    }
}

/* A `callsub` use becomes `param = arg; ...; callsub NAME;` on every seat (the sub's parameters
 * are named variables of the caller's frame, assigned before the call). On wasm32 the use is
 * then spliced with the sub's body inline (its module knows one body per fn). Fixpoint over the
 * token stream, deepest first, as macro_expand does. */
static void sub_expand(void) {
    for (;;) {
        int use = -1, s = -1, with_args = 0;
        for (int i = 0; toks[i].kind != TK_EOF && use < 0; i++) {
            if (toks[i].kind != TK_NAME || strcmp(toks[i].text, "callsub")) continue;
            if (toks[i + 1].kind != TK_NAME) continue;
            s = sub_index(toks[i + 1].text);
            if (s < 0) { fprintf(stderr, "no sub %s\n", toks[i + 1].text); exit(2); }
            if (toks[i + 2].kind == TK_OP && toks[i + 2].text[0] == '(') with_args = 1;
            else if (toks[i + 2].kind == TK_OP && toks[i + 2].text[0] == ';' && target == T_WASM)
                with_args = 0;
            else continue;
            use = i;
            /* A wasm32 body is consumed by expansion (its `var` lines land in the fn body's
             * scan); a native call keeps the body where it is -- only the arguments are
             * rewritten -- so its declarations still need scanning as a sub's. */
            stab[s].spliced = target == T_WASM;
        }
        if (use < 0) return;
        /* the splice's size is known before it is filled: the head, the expansion, the tail */
        int after;
        int astart[8], aend[8], nargs = 0, close = 0;
        if (with_args) {
            int i = use + 3;
            if (!(toks[i].kind == TK_OP && toks[i].text[0] == ')')) {
                for (;;) {
                    int depth = 0;
                    astart[nargs] = i;
                    for (;; i++) {
                        if (toks[i].kind == TK_EOF) {
                            fprintf(stderr, "sub call never ends\n");
                            exit(2);
                        }
                        if (toks[i].kind == TK_OP && toks[i].text[0] == '(') depth++;
                        else if (toks[i].kind == TK_OP && toks[i].text[0] == ')') {
                            if (depth == 0) break;
                            depth--;
                        } else if (toks[i].kind == TK_OP && toks[i].text[0] == ',' && depth == 0)
                            break;
                    }
                    aend[nargs] = i;
                    nargs++;
                    if (toks[i].kind == TK_OP && toks[i].text[0] == ',') { i++; continue; }
                    break;
                }
            }
            if (toks[i].kind != TK_OP || toks[i].text[0] != ')' || nargs != stab[s].nparams) {
                fprintf(stderr, "sub %s: %d arguments, wants %d\n", stab[s].name, nargs,
                        stab[s].nparams);
                exit(2);
            }
            close = i;                       /* the `)` of the call */
            after = close + 2;               /* the `;` follows */
        } else {
            after = use + 3;
        }
        long tail = ntoks - after;
        long grew = with_args
                        ? (long)(nargs * 4 + 3)
                        : (long)(stab[s].end - stab[s].start - 2);
        for (int k = 0; k < nargs; k++) grew += aend[k] - astart[k];
        long need = (long)use + grew + tail + 1;
        static token *out;
        static long out_cap;
        if (need > out_cap) {
            out_cap = need + 4096;
            out = (token *)realloc(out, sizeof(token) * (size_t)out_cap);
            if (!out) { fprintf(stderr, "sub expansion too large\n"); exit(2); }
        }
        int n = 0;
        for (int k = 0; k < use; k++) out[n++] = toks[k];
        if (with_args) {
            /* `callsub name(a, b);` -> `name_pa = a; name_pb = b; callsub name;` */
            for (int k = 0; k < nargs; k++) {
                token t = {TK_NAME, "", 0};
                snprintf(t.text, sizeof t.text, "%s", stab[s].params[k]);
                out[n++] = t;
                t.kind = TK_OP;
                snprintf(t.text, sizeof t.text, "=");
                out[n++] = t;
                for (int q = astart[k]; q < aend[k]; q++) out[n++] = toks[q];
                snprintf(t.text, sizeof t.text, ";");
                out[n++] = t;
            }
            out[n++] = toks[use];            /* callsub */
            out[n++] = toks[use + 1];        /* the name */
            out[n++] = toks[close + 1];      /* `;` */
        } else {
            for (int k = (int)stab[s].start + 1; k < (int)stab[s].end - 1; k++)
                out[n++] = toks[k];
        }
        for (int k = after; toks[k].kind != TK_EOF; k++) out[n++] = toks[k];
        out[n].kind = TK_EOF;
        out[n].text[0] = 0;
        if (n + 1 > toks_cap) {
            toks_cap = n + 1;
            toks = (token *)realloc(toks, sizeof(token) * (size_t)toks_cap);
            if (!toks) { fprintf(stderr, "too many tokens\n"); exit(2); }
        }
        for (int k = 0; k <= n; k++) toks[k] = out[k];
        /* the recorded body ranges were counted in the old stream: shift the ones past the splice */
        long shift = (long)n - ntoks;
        for (int s2 = 0; s2 < nsubs; s2++) {
            if ((long)stab[s2].end > (long)use) stab[s2].end += shift;
            if ((long)stab[s2].start >= (long)use) stab[s2].start += shift;
        }
        ntoks = n;
        cur = 0;
    }
}

/* compile a module of `fn` definitions into code[], with the table of offsets. */
static void compile_module(const char *src) {
    at = 0;
    wat = 0;
    nfns = 0;
    nsubs = 0;
    in_sub = 0;
    lex(src);
    macro_defs();
    macro_expand();
    sub_collect();
    sub_expand();
    while (toks[cur].kind != TK_EOF) {
        if (toks[cur].kind == TK_NAME && !strcmp(toks[cur].text, "sub")) {
            int sdepth = 0;                       /* recorded by sub_collect: walk over it */
            while (toks[cur].kind != TK_EOF) {
                if (toks[cur].kind == TK_OP && toks[cur].text[0] == '{') sdepth++;
                else if (toks[cur].kind == TK_OP && toks[cur].text[0] == '}') {
                    cur++;
                    if (--sdepth == 0) break;
                    continue;
                }
                cur++;
            }
            continue;
        }
        if (fn_select && !(toks[cur].kind == TK_NAME && !strcmp(toks[cur].text, "fn") &&
                           toks[cur + 1].kind == TK_NAME && !strcmp(toks[cur + 1].text, fn_select)))
            fn_skip();
        else {
            int fstart = cur;
            fn_def();
            /* the subs the fn carries go after its body: one copy each, called, not spliced */
            if (target != T_WASM) sub_emit_used(fstart, cur);
        }
    }
}

static long find_fn_index(const char *name) {
    for (int i = 0; i < nfns; i++) if (strcmp(ftab[i].name, name) == 0) return i;
    return -1;
}

static long find_fn(const char *name) {
    long i = find_fn_index(name);
    return i < 0 ? -1 : (long)ftab[i].offset;
}

/* ---- assemble the wasm32 bodies into a .wasm module (the browser seat) ------------------------ */
static uint8_t sec[CAP]; static size_t sn;
static void b8(uint8_t b) { sec[sn++] = b; }
static void buleb(uint64_t v) { do { uint8_t b = (uint8_t)(v & 0x7f); v >>= 7; if (v) b |= 0x80; b8(b); } while (v); }
static void wsec(FILE *f, uint8_t id) {
    fputc(id, f);
    uint8_t tmp[10]; size_t n = sn; int k = 0;
    do { uint8_t b = (uint8_t)(n & 0x7f); n >>= 7; if (n) b |= 0x80; tmp[k++] = b; } while (n);
    fwrite(tmp, 1, (size_t)k, f);
    fwrite(sec, 1, sn, f);
}

/* compile the .rxfs stdlib to wasm32 and write a module the browser seat can instantiate. */
static void stdlib_wasm_build(const char *path) {
    FILE *fp = fopen("native/seed/stdlib.rxfs", "r");
    if (!fp) { printf("cannot open native/seed/stdlib.rxfs\n"); return; }
    static char src[1048576];
    size_t len = fread(src, 1, sizeof src - 1, fp);
    src[len] = 0;
    fclose(fp);

    target = T_WASM;
    compile_module(src);
    target = T_X64;

    FILE *f = fopen(path, "wb");
    fwrite("\0asm\x01\x00\x00\x00", 1, 8, f);                 /* magic + version */
    sn = 0; b8(1); b8(0x60); b8(4); b8(0x7F); b8(0x7F); b8(0x7F); b8(0x7F); b8(1); b8(0x7F);
    wsec(f, 1);                                                /* type: (i32,i32,i32,i32)->i32 */
    sn = 0; buleb((uint64_t)nfns); for (int i = 0; i < nfns; i++) b8(0x00);
    wsec(f, 3);                                                /* function: all type 0 */
    sn = 0; b8(1); b8(0x00); b8(0x01);
    wsec(f, 5);                                                /* memory: min 1 page */
    sn = 0; buleb((uint64_t)nfns + 1);
    buleb(6); for (const char *p = "memory"; *p; p++) b8((uint8_t)*p); b8(0x02); b8(0x00);
    for (int i = 0; i < nfns; i++) {
        size_t L = strlen(ftab[i].name);
        buleb(L);
        for (size_t p = 0; p < L; p++) b8((uint8_t)ftab[i].name[p]);
        b8(0x00); buleb((uint64_t)i);                          /* func, index i */
    }
    wsec(f, 7);                                                /* export: memory + the functions */
    sn = 0; buleb((uint64_t)nfns);
    for (int i = 0; i < nfns; i++) {
        buleb(ftab[i].wsize);
        memcpy(sec + sn, wbuf + ftab[i].wstart, ftab[i].wsize);
        sn += ftab[i].wsize;
    }
    wsec(f, 10);                                               /* code: the bodies */
    fclose(f);
    printf("wrote %s: %d wasm32 functions (status-out ABI)\n", path, nfns);
}

/* compile the .rxfs stdlib to aarch64 and write the raw code for llvm-objdump verification. */
static void stdlib_arm64_build(const char *path) {
    FILE *fp = fopen("native/seed/stdlib.rxfs", "r");
    if (!fp) { printf("cannot open native/seed/stdlib.rxfs\n"); return; }
    static char src[1048576];
    size_t len = fread(src, 1, sizeof src - 1, fp);
    src[len] = 0;
    fclose(fp);

    target = T_ARM64;
    compile_module(src);
    target = T_X64;

    FILE *f = fopen(path, "wb");
    fwrite(code, 1, at, f);
    fclose(f);
    printf("wrote %s: %zu bytes, %d aarch64 functions (status-out ABI)\n", path, at, nfns);
}

/* ---- run a compiled function and check it ------------------------------------------------------ */

static int fails = 0;

typedef uint32_t (*fn_t)(void *, uint64_t *, uint64_t, uint64_t *);

static uint32_t call_fn(long off, const uint64_t *args, int n, uint64_t *out) {
    size_t sz = (at + 4095) & ~(size_t)4095;
    void *page = mmap(NULL, sz, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) { *out = 0; return 0xFFFFFFFFu; }
    memcpy(page, code, at);
    fn_t f = (fn_t)((uint8_t *)page + off);
    uint32_t status = f(NULL, (uint64_t *)args, (uint64_t)n, out);
    munmap(page, sz);
    return status;
}

static void run_check(const char *name, const char *mod, const char *fn, const uint64_t *args, int n, uint64_t want) {
    compile_module(mod);
    long off = find_fn(fn);
    if (off < 0) { printf("FAIL %-8s no fn %s\n", name, fn); fails++; return; }
    uint64_t out = 0xDEADBEEFCAFEF00DULL;
    uint32_t status = call_fn(off, args, n, &out);

    if (status != 0 || out != want) {
        printf("FAIL %-8s got=%016llx want=%016llx status=%u\n", name,
               (unsigned long long)out, (unsigned long long)want, status);
        fails++;
    } else {
        printf("ok   %-8s -> %016llx\n", name, (unsigned long long)out);
    }
}

/* prove the object world's direct-args status-out ABI: compile a .rxfs expression to it and run it
 * as f(arg0, arg1, out) -> status, matching the shape the .rxfd Code blobs use. */
typedef uint32_t (*objfn_t)(uint64_t, uint64_t, uint64_t *);

static void objabi_check(const char *name, const char *mod, const char *fn,
                         uint64_t a, uint64_t b, uint64_t want) {
    target = T_X64;
    abi_object = 1;
    /* the direct-args shape of this check: two word operands and a stored result */
    obj_nargw = 2;
    obj_nargs = 2;
    obj_shape[0] = 0;
    obj_arg_first[0] = 0;
    obj_arg_first[1] = 1;
    for (int k = 0; k < 2; k++) {
        obj_argw[k] = 8;
        obj_argsx[k] = 0;
        obj_argf[k] = 0;
        obj_argp[k] = 0;
    }
    obj_hasf = 0;
    store_width = 8;
    compile_module(mod);
    abi_object = 0;
    long off = find_fn(fn);
    if (off < 0) { printf("FAIL %-8s no fn %s\n", name, fn); fails++; return; }
    size_t sz = (at + 4095) & ~(size_t)4095;
    void *page = mmap(NULL, sz, PROT_READ | PROT_WRITE | PROT_EXEC, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (page == MAP_FAILED) { printf("FAIL %-8s mmap\n", name); fails++; return; }
    memcpy(page, code, at);
    uint64_t out = 0xDEADBEEFCAFEF00DULL;
    objfn_t f = (objfn_t)((uint8_t *)page + off);
    uint32_t status = f(a, b, &out);
    munmap(page, sz);
    if (status != 0 || out != want) {
        printf("FAIL %-8s got=%016llx want=%016llx status=%u\n", name,
               (unsigned long long)out, (unsigned long long)want, status);
        fails++;
    } else {
        printf("ok   %-8s object-ABI -> %016llx (status %u)\n", name, (unsigned long long)out, status);
    }
}

/* the stdlib, in .rxfs (docs/29): each primitive is a native instruction or two, lowered by the
 * seed. Prove parity with the semantics the stdlib promises (wrapping, shift guard, unsigned). */
static void stdlib_parity(void) {
    FILE *fp = fopen("native/seed/stdlib.rxfs", "r");
    if (!fp) { printf("cannot open native/seed/stdlib.rxfs\n"); fails++; return; }
    static char src[1048576];
    size_t len = fread(src, 1, sizeof src - 1, fp);
    src[len] = 0;
    fclose(fp);
    compile_module(src);

    /* for inspection: the compiled stdlib, so one can see it is native instructions (intrinsic
     * lowering), not a table of leaf calls. */
    { FILE *bin = fopen("/tmp/rxf_stdlib.bin", "wb"); if (bin) { fwrite(code, 1, at, bin); fclose(bin); } }

    struct { const char *fn; uint64_t a, b, want; } t[] = {
        {"wrapping_add_uint64_t", 5, 3, 8},
        {"wrapping_add_uint64_t", UINT64_MAX, 1, 0},                   /* wrapping */
        {"wrapping_subtract_uint64_t", 0, 1, UINT64_MAX},              /* wrapping */
        {"wrapping_multiply_uint64_t", 3, 4, 12},
        {"wrapping_multiply_uint64_t", UINT64_MAX, 2, UINT64_MAX - 1}, /* wrapping */
        {"bit_and_uint64_t", 0xF0, 0x0F, 0x00},
        {"bit_or_uint64_t", 0xF0, 0x0F, 0xFF},
        {"shift_left_uint64_t", 1, 4, 16},
        {"shift_left_uint64_t", 1, 64, 0},                             /* count >= 64 -> 0 */
        {"shift_right_uint64_t", 256, 4, 16},
        {"shift_right_uint64_t", 1, 64, 0},                            /* count >= 64 -> 0 */
        {"equal_uint64_t", 7, 7, 1},
        {"equal_uint64_t", 7, 8, 0},
        {"less_uint64_t", 3, 5, 1},
        {"less_uint64_t", 1ULL << 63, 1, 0},                           /* unsigned */
    };
    for (size_t i = 0; i < sizeof t / sizeof t[0]; i++) {
        long off = find_fn(t[i].fn);
        const uint64_t args[2] = {t[i].a, t[i].b};
        uint64_t out = 0;
        uint32_t status = (off < 0) ? 0xFFFFFFFFu : call_fn(off, args, 2, &out);
        if (status != 0 || out != t[i].want) {
            printf("FAIL stdlib/%s(%016llx,%016llx) got=%016llx want=%016llx\n", t[i].fn,
                   (unsigned long long)t[i].a, (unsigned long long)t[i].b,
                   (unsigned long long)out, (unsigned long long)t[i].want);
            fails++;
        } else {
            printf("ok   stdlib/%s\n", t[i].fn);
        }
    }
}

int main(int argc, char **argv) {
    /* seed codegen <x64|arm64> <generic|object>  -- read .rxfs on stdin, emit the code hex.
       This is the regen backend: a driver asks for a primitive's body compiled to the object
       world's status-out ABI and gets the bytes to drop into its <Code rxf:data>. `#<fn>`
       answers with one fn's code range (a family file holds many; without it, the whole
       module's code). */
    if (argc >= 2 && strcmp(argv[1], "codegen") == 0) {
        int want_wasm = (argc >= 3 && strcmp(argv[2], "wasm") == 0);
        target = want_wasm ? T_WASM : (argc >= 3 && strcmp(argv[2], "arm64") == 0) ? T_ARM64 : T_X64;
        abi_object = (argc >= 4 && strcmp(argv[3], "object") == 0);
        if (argc >= 5) store_width = atoi(argv[4]);   /* the result width written to *out: 1/2/4/8 */
        /* per-operand specs: <width>[s|u|f|p] e.g. `1s 4u` (a is int8, the count is uint32);
         * `4f`/`8f` is a float/double operand (arrives in the float bank as its raw bits);
         * `8p` is an address word. A bare number is an unsigned width. The first operand's
         * sign drives compares. One `!<letters>` entry instead gives the C argument shape:
         * `H` an object handle (three words), `p` an address, `q` a u64, `d` a u32,
         * `b` a u8 -- the body names the words flat (h_id, h_gen, h_off for a handle). */
        /* `#<fn>`: a family file holds many `fn` definitions and the object ABI carries one
         * function's bytes per snippet -- this names which one the hex answers for. */
        obj_nargw = 0;
        obj_nargs = 0;
        obj_nhandle = 0;
        obj_shape[0] = 0;
        for (int i = 5; i < argc && obj_nargw < 60; i++) {
            const char *s = argv[i];
            if (*s == '@') {                     /* `@<shape>`: a call type of the body's calls */
                if (ncall_shapes >= (int)(sizeof call_shape / sizeof call_shape[0])) {
                    fprintf(stderr, "too many call types\n");
                    exit(2);
                }
                strncpy(call_shape[ncall_shapes], s + 1, sizeof call_shape[0] - 1);
                call_shape[ncall_shapes][sizeof call_shape[0] - 1] = 0;
                ncall_shapes++;
                continue;
            }
            if (*s == '!') {
                strncpy(obj_shape, s + 1, sizeof obj_shape - 1);
                obj_shape[sizeof obj_shape - 1] = 0;
                for (int k = 0; obj_shape[k]; k++) {
                    obj_arg_first[obj_nargs++] = obj_nargw;
                    int words = (obj_shape[k] == 'H') ? 3 : 1;
                    if (obj_shape[k] == 'H') obj_nhandle++;
                    for (int j = 0; j < words; j++) {
                        obj_argw[obj_nargw] = 8;
                        obj_argsx[obj_nargw] = 0;
                        obj_argf[obj_nargw] = 0;
                        obj_argp[obj_nargw] = 0;
                        if (obj_shape[k] == 'p') obj_argp[obj_nargw] = 1;
                        if (obj_shape[k] == 'd') obj_argw[obj_nargw] = 4;
                        if (obj_shape[k] == 'b') obj_argw[obj_nargw] = 1;
                        if (obj_shape[k] == 'f') obj_argw[obj_nargw] = 4, obj_argf[obj_nargw] = 1;
                        if (obj_shape[k] == 'F') obj_argf[obj_nargw] = 1;
                        if (obj_shape[k] == 'f' && !obj_hasf) fmath_width = 4, obj_hasf = 1;
                        if (obj_shape[k] == 'F' && !obj_hasf) fmath_width = 8, obj_hasf = 1;
                        obj_nargw++;
                    }
                }
                continue;
            }
            if (*s == '#') { fn_select = s + 1; continue; }
            if (*s < '0' || *s > '9') break;      /* `signed` legacy keyword or junk: stop */
            int w = (int)strtol(s, NULL, 10);
            while (*s >= '0' && *s <= '9') s++;
            obj_argw[obj_nargw] = w;
            obj_argsx[obj_nargw] = (*s == 's');
            obj_argf[obj_nargw] = (*s == 'f');
            obj_argp[obj_nargw] = (*s == 'p');
            if (*s == 'f' && !obj_hasf) fmath_width = w, obj_hasf = 1;
            obj_nargw++;
        }
        if (!obj_shape[0]) { obj_nargs = obj_nargw; for (int k = 0; k < obj_nargs; k++) obj_arg_first[k] = k; }
        obj_signed = (obj_nargw > 0 && obj_argsx[0]) ||
                     (argc > 5 + obj_nargw && strcmp(argv[5 + obj_nargw], "signed") == 0);
        static char gsrc[1048576];
        size_t glen = fread(gsrc, 1, sizeof gsrc - 1, stdin);
        gsrc[glen] = 0;
        compile_module(gsrc);
        /* the answer is the whole module, or the one fn `#<name>` names (its own code range:
         * the fns of a module are contiguous in the order they are written). */
        size_t c0 = 0, c1 = at, w0 = 0, w1 = wat;
        if (fn_select) {
            long fi = find_fn_index(fn_select);
            if (fi < 0) { fprintf(stderr, "no fn %s\n", fn_select); exit(2); }
            c0 = ftab[fi].offset;
            c1 = (fi + 1 < nfns) ? ftab[fi + 1].offset : at;
            w0 = ftab[fi].wstart;
            w1 = ftab[fi].wstart + ftab[fi].wsize;
        }
        if (want_wasm) {
            for (size_t i = w0; i < w1; i++) printf("%02x", wbuf[i]);
        } else {
            for (size_t i = c0; i < c1; i++) printf("%02x", code[i]);
        }
        printf("\n");
        return 0;
    }
    { const uint64_t a[2] = {5, 3}; run_check("add", "fn f(x, y) { return x + y; }", "f", a, 2, 8); }
    { const uint64_t a[2] = {UINT64_MAX, 1}; run_check("wrap", "fn f(x, y) { return x + y; }", "f", a, 2, 0); }
    { const uint64_t a[2] = {0, 1}; run_check("sub", "fn f(x, y) { return x - y; }", "f", a, 2, UINT64_MAX); }
    { const uint64_t a[2] = {3, 4}; run_check("mul", "fn f(x, y) { return x * y; }", "f", a, 2, 12); }
    { const uint64_t a[2] = {1, 64}; run_check("shl", "fn f(x, y) { return x << y; }", "f", a, 2, 0); }
    { const uint64_t a[2] = {1, 4}; run_check("shl", "fn f(x, y) { return x << y; }", "f", a, 2, 16); }
    { const uint64_t a[2] = {1, 2}; run_check("lt", "fn f(x, y) { return x < y; }", "f", a, 2, 1); }
    { const uint64_t a[2] = {1ULL << 63, 1}; run_check("ltu", "fn f(x, y) { return x < y; }", "f", a, 2, 0); }
    { const uint64_t a[2] = {1, 2}; run_check("prec", "fn f(x, y) { return x + y * 3; }", "f", a, 2, 7); }
    { const uint64_t a[2] = {0xFF, 0x0F}; run_check("unop", "fn f(x, y) { return ~(x & y); }", "f", a, 2, ~(uint64_t)0x0F); }
    {
        const uint64_t a[1] = {10};
        run_check("sum", "fn f(n) { var i, total; i = 0; total = 0; "
                         "while i < n { total = total + i; i = i + 1; } return total; }", "f", a, 1, 45);
    }
    {
        const uint64_t a[2] = {5, 9};
        run_check("max", "fn f(x, y) { if x < y { return y; } else { return x; } }", "f", a, 2, 9);
    }

    /* the object world's direct-args status-out ABI: .rxfs bodies compiled to the shape the .rxfd
     * Code blobs carry (f(arg0, arg1, out) -> status), so .rxfs can be their source. */
    objabi_check("obj/add", "fn add(a, b) { return a + b; }\n", "add", 3, 5, 8);
    objabi_check("obj/sub", "fn sub(a, b) { return a - b; }\n", "sub", 10, 4, 6);
    objabi_check("obj/mul", "fn mul(a, b) { return a * b; }\n", "mul", 6, 7, 42);
    objabi_check("obj/and", "fn band(a, b) { return a & b; }\n", "band", 0xF0, 0x3C, 0x30);

    stdlib_parity();

    /* the browser seat: the same .rxfs stdlib, compiled to wasm32 (node instantiates + runs it) */
    stdlib_wasm_build("/tmp/rxf_stdlib.wasm");

    /* the firmware seat: the same .rxfs stdlib, compiled to aarch64 (disassembly-verified) */
    stdlib_arm64_build("/tmp/rxf_stdlib_arm64.bin");

    if (fails) { printf("\n%d FAILURES\n", fails); return 1; }
    printf("\nthe seed compiles .rxfs to running x86_64; the stdlib is .rxfs\n");
    return 0;
}
