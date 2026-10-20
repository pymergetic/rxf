/*
 * The compiler of a program of the interpreter into machine code (docs/19, WP12).
 *
 * rxf_compile_program(context, request) turns the program of a function (the words that
 * rxf_graph_run reads, native/graph.h) into the code of the machine it runs on: x86_64, aarch64
 * or the body of a wasm32 function. The compiled function does what the interpreter does with
 * the same program:
 *
 *     status f(context, args, argc, out)
 *
 * `args`, `argc` and `out` are those of rxf_graph_run; the program itself is gone, the context is
 * still needed, because the compiled code calls the leaves of the interpreter with the same
 * shapes. The code needs no relocation and can run where it is put.
 *
 * Every instruction of the interpreter is compiled: MOVE, MAP, FIELD, CONSTRUCT, LESS, INCREMENT,
 * CALL of every leaf (the ones that take a handle too), GOTO, BRANCH, SWITCH, RETURN and REFUSE,
 * with loops and with the copies of an edge. What stays with the interpreter is what only it can
 * do: a program that is not whole (a word out of the program, a jump into the middle of an
 * instruction), and a slot that a machine could not read or write in one piece (an odd width, an
 * unaligned offset): those are refused with RXF_REFUSE_UNSUPPORTED and `at`, the word of the
 * instruction. The addresses of the objects and of the leaves are those of this moment: code
 * compiled before a change of the arena, or a relink, is compiled again. The compiled code has
 * no safe points; the interpreter keeps them.
 *
 * Two passes over the same writer. The first only counts, and records where each instruction
 * starts; the second writes, with every jump to an instruction already placed. A jump has one
 * size whatever its distance, so the passes agree. The tables of the passes (an instruction number
 * for each word of the program, a code offset for each instruction) are in the `work` memory of
 * the request. On wasm32, which has no jump, the code is a loop over a table jump
 * (`br_table`) to the block of the next instruction.
 */
/* A machine does not use every helper of the others. */
#define RXF_INLINE static __attribute__((always_inline, unused)) inline
#include "graph.h"

#define RXF_FUNCTION __attribute__((noinline))
#define RXF_SLOT_WORDS 5U
#define RXF_EDGE_HEADER 2U
#define RXF_EDGE_COPY_WORDS 5U
#define RXF_OPERAND_WORDS 3U
#define RXF_CALL_HEADER 6U
#define RXF_CALL_OPERANDS 6U
#define RXF_MAX_ARGUMENTS 8U

typedef unsigned long rxf_uptr;
typedef long long rxf_i64;

/* In: the program and the room for the code. Out: how many bytes the code takes (also when the
 * room is too small), and the word of the program that was refused. The struct is the one of
 * compile.h, the header of this compiler. */
#include "compile.h"

#define RXF_NO_INSTRUCTION 0xFFFFFFFFU
/* Beyond the frame of the program the code keeps what an edge stages, and the copies of the
 * handles that a call passes by address. */
#define RXF_STAGE_AT RXF_GRAPH_FRAME
#define RXF_HANDLES_AT (RXF_GRAPH_FRAME + RXF_GRAPH_COPY)
#define RXF_HANDLE_BYTES 24U
#define RXF_FRAME_ALLOC (RXF_HANDLES_AT + RXF_CALL_OPERANDS * RXF_HANDLE_BYTES)

typedef struct {
    rxf_runtime_context *c;
    const rxf_u64 *code;
    rxf_u64 count;
    rxf_u8 *buf;
    rxf_u64 cap, size;
    rxf_u64 frame; /* bytes of the frame of the program */
    rxf_u64 alloc; /* bytes taken on the stack: the frame of the interpreter, whatever the program
                      uses */
    rxf_u32 failed;
    rxf_u32 write;            /* 0: the first pass, only counting */
    volatile rxf_u32 *index;  /* the instruction that starts at a word, or RXF_NO_INSTRUCTION */
    volatile rxf_u32 *offset; /* the code offset of an instruction */
    rxf_u64 instructions;     /* how many */
    rxf_u64 current;          /* the instruction being written */
    rxf_u32 nest;             /* wasm32: `if` blocks open */
    rxf_u64 at;               /* the word that was refused */
    rxf_u64 area;             /* the area of the Channel of the context: 0 when it has none */
    rxf_u64 safe;             /* the address of the safe point leaf: 0 when the context has none */
} rxf_cc;

/* ---- output -----------------------------------------------------------------------------------
 */

RXF_INLINE void put(rxf_cc *e, rxf_u32 byte) {
    if (e->write) {
        if (e->size < e->cap)
            ((volatile rxf_u8 *)e->buf)[e->size] =
                (rxf_u8)byte; /* not merged into a vector store */
        else
            e->failed = 1;
    }
    e->size++;
}

RXF_INLINE void put32(rxf_cc *e, rxf_u32 value) {
    put(e, value);
    put(e, value >> 8);
    put(e, value >> 16);
    put(e, value >> 24);
}

RXF_INLINE void put64(rxf_cc *e, rxf_u64 value) {
    put32(e, (rxf_u32)value);
    put32(e, (rxf_u32)(value >> 32));
}

/* Writes a word into what was written before (a branch distance). */
RXF_INLINE void patch32(rxf_cc *e, rxf_u64 at, rxf_u32 value) {
    if (e->write && at + 4 <= e->cap)
        for (rxf_u32 i = 0; i < 4; i++)
            ((volatile rxf_u8 *)e->buf)[at + i] = (rxf_u8)(value >> (8 * i));
}

/* The size of the next piece of a copy: whole words where both ends are aligned. */
RXF_INLINE rxf_u32 pick(rxf_u64 left, rxf_u64 a, rxf_u64 b) {
    rxf_u64 low = a | b;
    if (left >= 8 && !(low & 7))
        return 8;
    if (left >= 4 && !(low & 3))
        return 4;
    if (left >= 2 && !(low & 1))
        return 2;
    return 1;
}

/* The arguments of a call, in the order of the C function of the leaf: an operand of the call,
 * the context, the output, or the staged arguments of a program call (the words its callee is
 * called with, staged at RXF_STAGE_AT). The letters are those of the shapes of graph.c. */
#define ARG_CONTEXT 6U
#define ARG_OUT 7U
#define ARG_STAGE 8U

/* A status a be_call is given where no slot catches the answer: a refusal the callee answers
 * then ends the run with it (the uncaught shape of answer() in graph.c). */
#define RXF_STATUS_UNCAUGHT (RXF_GRAPH_NONE - 1)

RXF_INLINE rxf_u32 plan(rxf_u32 shape, rxf_u32 *arg, rxf_u32 *type) {
/* volatile: the stores are not merged into a vector constant, which would be a relocation */
#define ARG(i, a, t) (((volatile rxf_u32 *)arg)[i] = (a), ((volatile rxf_u32 *)type)[i] = (t))
    switch (shape) {
    case RXF_SHAPE_QO:
        ARG(0, 0, 'q');
        ARG(1, ARG_OUT, 'o');
        return 2;
    case RXF_SHAPE_QQO:
        ARG(0, 0, 'q');
        ARG(1, 1, 'q');
        ARG(2, ARG_OUT, 'o');
        return 3;
    case RXF_SHAPE_QQQQQPO:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'q');
        ARG(2, 1, 'q');
        ARG(3, 2, 'q');
        ARG(4, 3, 'q');
        ARG(5, 4, 'q');
        ARG(6, 5, 'p');
        ARG(7, ARG_OUT, 'o');
        return 8;
    case RXF_SHAPE_P:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'p');
        return 2;
    case RXF_SHAPE_WWO:
        ARG(0, 0, 'w');
        ARG(1, 1, 'w');
        ARG(2, ARG_OUT, 'o');
        return 3;
    case RXF_SHAPE_QWO:
        ARG(0, 0, 'q');
        ARG(1, 1, 'w');
        ARG(2, ARG_OUT, 'o');
        return 3;
    case RXF_SHAPE_PQPQQ:
        ARG(0, 0, 'p');
        ARG(1, 1, 'q');
        ARG(2, 2, 'p');
        ARG(3, 3, 'q');
        ARG(4, 4, 'q');
        return 5;
    case RXF_SHAPE_PQPQQO:
        ARG(0, 0, 'p');
        ARG(1, 1, 'q');
        ARG(2, 2, 'p');
        ARG(3, 3, 'q');
        ARG(4, 4, 'q');
        ARG(5, ARG_OUT, 'o');
        return 6;
    case RXF_SHAPE_PQBQ:
        ARG(0, 0, 'p');
        ARG(1, 1, 'q');
        ARG(2, 2, 'b');
        ARG(3, 3, 'q');
        return 4;
    case RXF_SHAPE_HPQQO:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'p');
        ARG(3, 2, 'q');
        ARG(4, 3, 'q');
        ARG(5, ARG_OUT, 'o');
        return 6;
    case RXF_SHAPE_HQO:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'q');
        ARG(3, ARG_OUT, 'o');
        return 4;
    case RXF_SHAPE_HQ:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'q');
        return 3;
    case RXF_SHAPE_H:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        return 2;
    case RXF_SHAPE_HPQQ:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'p');
        ARG(3, 2, 'q');
        ARG(4, 3, 'q');
        return 5;
    case RXF_SHAPE_HPQQQ:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'p');
        ARG(3, 2, 'q');
        ARG(4, 3, 'q');
        ARG(5, 4, 'q');
        return 6;
    case RXF_SHAPE_HPW:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'p');
        ARG(3, 2, 'w');
        return 4;
    case RXF_SHAPE_HQHQPO:
        ARG(0, ARG_CONTEXT, 'c');
        ARG(1, 0, 'h');
        ARG(2, 1, 'q');
        ARG(3, 2, 'h');
        ARG(4, 3, 'q');
        ARG(5, 4, 'p');
        ARG(6, ARG_OUT, 'o');
        return 7;
    default:
        return 0;
    }
#undef ARG
}

/* ---- the machines -------------------------------------------------------------------------------
 * Each one writes: begin (the frame, the arguments), param, zero, move, call, return, refuse,
 * and the skip of a branch. `base` is a register (or, on wasm32, a local) and `disp` a byte
 * offset; an object is an address that has been put in a register first. */

#if defined(__x86_64__)

#define RAX 0U
#define RCX 1U
#define RDX 2U
#define RBX 3U
#define RBP 5U
#define RSI 6U
#define RDI 7U
#define R8 8U
#define R9 9U
#define R15 15U

RXF_INLINE void x_rex(rxf_cc *e, rxf_u32 w, rxf_u32 reg, rxf_u32 rm) {
    rxf_u32 rex = 0x40U | (w << 3) | ((reg >> 3) << 2) | (rm >> 3);
    if (rex != 0x40U)
        put(e, rex);
}

/* [base + disp32]: the bases used are never rsp or r12, which need a SIB byte. */
RXF_INLINE void x_mem(rxf_cc *e, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    put(e, 0x80U | ((reg & 7U) << 3) | (base & 7U));
    put32(e, disp);
}

RXF_INLINE void x_load(rxf_cc *e, rxf_u32 size, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    if (size == 8) {
        x_rex(e, 1, reg, base);
        put(e, 0x8B);
    } else if (size == 4) {
        x_rex(e, 0, reg, base);
        put(e, 0x8B);
    } else {
        x_rex(e, 1, reg, base);
        put(e, 0x0F);
        put(e, size == 2 ? 0xB7U : 0xB6U);
    }
    x_mem(e, reg, base, disp);
}

RXF_INLINE void x_store(rxf_cc *e, rxf_u32 size, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    if (size == 2)
        put(e, 0x66);
    x_rex(e, size == 8, reg, base);
    put(e, size == 1 ? 0x88U : 0x89U);
    x_mem(e, reg, base, disp);
}

RXF_INLINE void x_imm(rxf_cc *e, rxf_u32 reg, rxf_u64 value) {
    if (value >> 32) {
        x_rex(e, 1, 0, reg);
        put(e, 0xB8U + (reg & 7U));
        put64(e, value);
    } else {
        x_rex(e, 0, 0, reg);
        put(e, 0xB8U + (reg & 7U));
        put32(e, (rxf_u32)value);
    }
}

RXF_INLINE void x_lea(rxf_cc *e, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    x_rex(e, 1, reg, base);
    put(e, 0x8D);
    x_mem(e, reg, base, disp);
}

RXF_INLINE void x_mov(rxf_cc *e, rxf_u32 dst, rxf_u32 src) {
    x_rex(e, 1, src, dst);
    put(e, 0x89);
    put(e, 0xC0U | ((src & 7U) << 3) | (dst & 7U));
}

RXF_INLINE void x_copy(rxf_cc *e, rxf_u32 dst_base, rxf_u64 dst, rxf_u32 src_base, rxf_u64 src,
                       rxf_u64 count) {
    for (rxf_u64 done = 0; done < count;) {
        rxf_u32 size = pick(count - done, dst + done, src + done);
        x_load(e, size, RAX, src_base, (rxf_u32)(src + done));
        x_store(e, size, RAX, dst_base, (rxf_u32)(dst + done));
        done += size;
    }
}

RXF_INLINE void x_zero(rxf_cc *e, rxf_u32 base, rxf_u64 disp, rxf_u64 count) {
    if (!count)
        return;
    put(e, 0x31); /* xor eax, eax */
    put(e, 0xC0);
    for (rxf_u64 done = 0; done < count;) {
        rxf_u64 left = count - done;
        rxf_u32 size = left >= 8 ? 8U : left >= 4 ? 4U : left >= 2 ? 2U : 1U;
        x_store(e, size, RAX, base, (rxf_u32)(disp + done));
        done += size;
    }
}

RXF_INLINE void x_epilogue(rxf_cc *e) {
    put(e, 0x48); /* add rsp, alloc */
    put(e, 0x81);
    put(e, 0xC4);
    put32(e, (rxf_u32)e->alloc);
    put(e, 0x41); /* pop r15 */
    put(e, 0x5F);
    put(e, 0x5B); /* pop rbx */
    put(e, 0x5D); /* pop rbp */
    put(e, 0xC3);
}

/* The value of an operand in a register (see load() and pointer() of graph.c). */
RXF_INLINE void x_value(rxf_cc *e, rxf_u32 reg, const rxf_u64 *op, rxf_u64 object, rxf_u32 wide) {
    if (op[0] == RXF_OPERAND_LITERAL) {
        x_imm(e, reg, op[1]);
    } else if (op[0] == RXF_OPERAND_OBJECT) {
        x_imm(e, reg, object);
    } else if (wide && op[2] > 8) {
        x_lea(e, reg, RBP, (rxf_u32)op[1]);
    } else {
        x_load(e, op[2] >= 8 ? 8U : (rxf_u32)op[2], reg, RBP, (rxf_u32)op[1]);
    }
}

#define R10 10U
#define R11 11U

RXF_INLINE void be_begin(rxf_cc *e, rxf_u64 words) {
    e->alloc = RXF_FRAME_ALLOC; /* the interpreter's frame and more: a pointer may reach past it */
    put(e, 0x55);               /* push rbp */
    put(e, 0x53);               /* push rbx */
    put(e, 0x41);               /* push r15 */
    put(e, 0x57);
    put(e, 0x48); /* sub rsp, alloc */
    put(e, 0x81);
    put(e, 0xEC);
    put32(e, (rxf_u32)e->alloc);
    put(e, 0x48); /* mov rbp, rsp */
    put(e, 0x89);
    put(e, 0xE5);
    x_mov(e, RBX, RDI); /* the context */
    x_mov(e, R15, RCX); /* the output */
    put(e, 0x48);       /* cmp rdx, words */
    put(e, 0x81);
    put(e, 0xFA);
    put32(e, (rxf_u32)words);
    put(e, 0x0F); /* je past the refusal */
    put(e, 0x84);
    rxf_u64 at = e->size;
    put32(e, 0);
    x_imm(e, RAX, RXF_REFUSE_OUT_OF_RANGE);
    x_epilogue(e);
    patch32(e, at, (rxf_u32)(e->size - (at + 4)));
    x_zero(e, RBP, 0, e->frame);
}

RXF_INLINE void be_param(rxf_cc *e, rxf_u64 slot, rxf_u64 width, rxf_u64 used) {
    x_copy(e, RBP, slot, RSI, used, width);
}

RXF_INLINE void be_move(rxf_cc *e, rxf_u64 slot, rxf_u64 width, const rxf_u64 *src, rxf_u64 object,
                        rxf_u64 have) {
    x_zero(e, RBP, slot, width);
    if (src[0] == RXF_OPERAND_SLOT) {
        x_copy(e, RBP, slot, RBP, src[1], have);
    } else if (src[0] == RXF_OPERAND_OBJECT) {
        x_imm(e, RCX, object);
        x_copy(e, RBP, slot, RCX, 0, have);
    } else {
        for (rxf_u64 done = 0; done < have;) {
            rxf_u32 size = pick(have - done, slot + done, 0);
            rxf_u64 piece = src[1] >> (8 * done);
            if (size == 8) {
                x_imm(e, RAX, piece);
            } else {
                x_imm(e, RAX, piece & (((rxf_u64)1 << (8 * size)) - 1));
            }
            x_store(e, size, RAX, RBP, (rxf_u32)(slot + done));
            done += size;
        }
    }
}

RXF_INLINE rxf_u32 x_argument_register(rxf_u32 i) {
    return i == 0 ? RDI : i == 1 ? RSI : i == 2 ? RDX : i == 3 ? RCX : i == 4 ? R8 : R9;
}

/* One argument of a call in a register: the context, the output, the staged arguments of a
 * program call, or an operand. */
RXF_INLINE void x_argument(rxf_cc *e, rxf_u32 reg, rxf_u32 which, rxf_u32 type,
                           const rxf_u64 *operands, const rxf_u64 *objects, rxf_u64 out_slot) {
    if (which == ARG_CONTEXT) {
        x_mov(e, reg, RBX);
    } else if (which == ARG_OUT) {
        x_lea(e, reg, RBP, (rxf_u32)out_slot);
    } else if (which == ARG_STAGE) {
        x_lea(e, reg, RBP, RXF_STAGE_AT);
    } else {
        const rxf_u64 *op = operands + which * RXF_OPERAND_WORDS;
        if (type == 'b' && op[0] == RXF_OPERAND_LITERAL)
            x_imm(e, reg, op[1] & 0xFF);
        else
            x_value(e, reg, op, objects[which], 1);
    }
}

/* push qword [rbp + disp] */
RXF_INLINE void x_push_slot(rxf_cc *e, rxf_u64 disp) {
    put(e, 0xFF);
    put(e, 0xB5);
    put32(e, (rxf_u32)disp);
}

/* push qword [rax + disp] (disp < 128) */
RXF_INLINE void x_push_object(rxf_cc *e, rxf_u32 disp) {
    put(e, 0xFF);
    put(e, disp ? 0x70U : 0x30U);
    if (disp)
        put(e, disp);
}

/* A handle goes by value on the stack (a structure of 24 bytes is passed in memory): the three
 * words, the last first, so that the first is the lowest. */
RXF_INLINE void x_push_handle(rxf_cc *e, const rxf_u64 *op, rxf_u64 object) {
    if (op[0] == RXF_OPERAND_OBJECT)
        x_imm(e, RAX, object);
    for (rxf_u32 word = 3; word > 0; word--) {
        if (op[0] == RXF_OPERAND_OBJECT)
            x_push_object(e, 8U * (word - 1));
        else
            x_push_slot(e, op[1] + 8U * (word - 1));
    }
}

RXF_INLINE void be_call(rxf_cc *e, const rxf_u32 *arg, const rxf_u32 *type, rxf_u32 n,
                        rxf_u64 function, const rxf_u64 *operands, const rxf_u64 *objects,
                        rxf_u32 has_out, rxf_u64 out_slot, rxf_u64 out_width, rxf_u64 status) {
    rxf_u32 handles = 0;
    for (rxf_u32 i = 0; i < n; i++)
        handles += type[i] == 'h';
    if (handles & 1) {
        put(e, 0x48); /* sub rsp, 8: the stack is 16-byte aligned at the call */
        put(e, 0x83);
        put(e, 0xEC);
        put(e, 8);
    }
    for (rxf_u32 i = n; i > 0; i--)
        if (type[i - 1] == 'h')
            x_push_handle(e, operands + arg[i - 1] * RXF_OPERAND_WORDS, objects[arg[i - 1]]);
    rxf_u32 registers = 0;
    for (rxf_u32 i = 0; i < n; i++)
        if (type[i] != 'h' && registers < 6)
            x_argument(e, x_argument_register(registers++), arg[i], type[i], operands, objects,
                       out_slot);
    for (rxf_u32 i = n; i > 6 + handles; i--) {
        x_argument(e, RAX, arg[i - 1], type[i - 1], operands, objects, out_slot);
        put(e, 0x50); /* push rax */
    }
    if (has_out)
        x_zero(e, RBP, out_slot, out_width);
    x_imm(e, RAX, function);
    put(e, 0xFF); /* call rax */
    put(e, 0xD0);
    rxf_u32 left = (n > 6 + handles ? n - 6 - handles : 0) + 3 * handles + (handles & 1);
    if (left) {
        put(e, 0x48); /* add rsp, 8 * pushed */
        put(e, 0x83);
        put(e, 0xC4);
        put(e, 8U * left);
    }
    if (status == RXF_STATUS_UNCAUGHT) {
        put(e, 0x48); /* test rax, rax */
        put(e, 0x85);
        put(e, 0xC0);
        put(e, 0x0F); /* jz past the answer */
        put(e, 0x84);
        rxf_u64 at = e->size;
        put32(e, 0);
        x_epilogue(e);
        patch32(e, at, (rxf_u32)(e->size - (at + 4)));
    } else if (status != RXF_GRAPH_NONE) {
        x_store(e, 4, RAX, RBP, (rxf_u32)status);
    }
}

RXF_INLINE void be_return(rxf_cc *e, const rxf_u64 *src, rxf_u64 object, rxf_u64 width) {
    if (width > 8) {
        if (src[0] == RXF_OPERAND_OBJECT) {
            x_imm(e, RCX, object);
            x_copy(e, R15, 0, RCX, 0, width);
        } else {
            x_copy(e, R15, 0, RBP, src[1], width);
        }
    } else {
        x_value(e, RAX, src, object, 0);
        x_store(e, 8, RAX, R15, 0);
    }
    put(e, 0x31); /* xor eax, eax */
    put(e, 0xC0);
    x_epilogue(e);
}

RXF_INLINE void be_refuse(rxf_cc *e, const rxf_u64 *src, rxf_u64 object) {
    x_value(e, RAX, src, object, 0);
    x_epilogue(e);
}

/* The safe point of an edge that goes back (native/safe.c): the leaf is called on the context and
 * the area, and what it answers other than OK is the status of the program. */
RXF_INLINE void be_safe_point(rxf_cc *e) {
    x_mov(e, RDI, RBX);
    x_imm(e, RSI, e->area);
    x_imm(e, RAX, e->safe);
    put(e, 0xFF); /* call rax */
    put(e, 0xD0);
    put(e, 0x85); /* test eax, eax */
    put(e, 0xC0);
    put(e, 0x0F); /* je past the return */
    put(e, 0x84);
    rxf_u64 at = e->size;
    put32(e, 0);
    x_epilogue(e);
    patch32(e, at, (rxf_u32)(e->size - (at + 4)));
}

RXF_INLINE void be_zero(rxf_cc *e, rxf_u64 slot, rxf_u64 width) {
    x_zero(e, RBP, slot, width);
}

RXF_INLINE void be_slot_copy(rxf_cc *e, rxf_u64 dst, rxf_u64 src, rxf_u64 width) {
    x_copy(e, RBP, dst, RBP, src, width);
}

/* dst (one byte) = a < b, unsigned */
RXF_INLINE void be_less(rxf_cc *e, rxf_u64 dst, const rxf_u64 *a, rxf_u64 a_object,
                        const rxf_u64 *b, rxf_u64 b_object) {
    x_value(e, RAX, a, a_object, 0);
    x_value(e, RCX, b, b_object, 0);
    put(e, 0x48); /* cmp rax, rcx */
    put(e, 0x39);
    put(e, 0xC8);
    put(e, 0x0F); /* setb al */
    put(e, 0x92);
    put(e, 0xC0);
    x_store(e, 1, RAX, RBP, (rxf_u32)dst);
}

/* dst (width 1, 2, 4 or 8 bytes) = src + 1 */
RXF_INLINE void be_increment(rxf_cc *e, rxf_u64 dst, rxf_u64 width, const rxf_u64 *src,
                             rxf_u64 object) {
    x_value(e, RAX, src, object, 0);
    put(e, 0x48); /* add rax, 1 */
    put(e, 0x83);
    put(e, 0xC0);
    put(e, 1);
    x_store(e, width >= 8 ? 8U : (rxf_u32)width, RAX, RBP, (rxf_u32)dst);
}

/* The value that the next `if` looks at. */
RXF_INLINE void be_value(rxf_cc *e, const rxf_u64 *op, rxf_u64 object) {
    x_value(e, R10, op, object, 0);
}

/* The code up to the matching end runs only when the value is zero (`on_zero`) or not. The place
 * to patch is the distance of the skip. */
RXF_INLINE rxf_u64 be_if_zero(rxf_cc *e, rxf_u32 on_zero) {
    put(e, 0x4D); /* test r10, r10 */
    put(e, 0x85);
    put(e, 0xD2);
    put(e, 0x0F);
    put(e, on_zero ? 0x85U : 0x84U); /* jne / je past the code */
    rxf_u64 at = e->size;
    put32(e, 0);
    return at;
}

/* ... only when the value is the tag */
RXF_INLINE rxf_u64 be_if_tag(rxf_cc *e, rxf_u64 tag) {
    x_imm(e, R11, tag);
    put(e, 0x4D); /* cmp r10, r11 */
    put(e, 0x39);
    put(e, 0xDA);
    put(e, 0x0F); /* jne past the code */
    put(e, 0x85);
    rxf_u64 at = e->size;
    put32(e, 0);
    return at;
}

RXF_INLINE void be_if_else(rxf_cc *e, rxf_u64 at) {
    patch32(e, at, (rxf_u32)(e->size - (at + 4)));
}

RXF_INLINE void be_if_end(rxf_cc *e, rxf_u64 at) {
    patch32(e, at, (rxf_u32)(e->size - (at + 4)));
}

RXF_INLINE void be_if_else_end(rxf_cc *e) {
    (void)e;
}

RXF_INLINE void be_dispatch(rxf_cc *e) {
    (void)e;
}

RXF_INLINE void be_enter(rxf_cc *e) {
    (void)e;
}

/* jmp to the start of instruction `target` */
RXF_INLINE void be_jump(rxf_cc *e, rxf_u64 target) {
    put(e, 0xE9);
    put32(e, (rxf_u32)((rxf_i64)e->offset[target] - (rxf_i64)(e->size + 4)));
}

/* what is after the last instruction: the program ran off its end */
RXF_INLINE void be_tail(rxf_cc *e) {
    x_imm(e, RAX, RXF_REFUSE_OUT_OF_RANGE);
    x_epilogue(e);
}

#elif defined(__aarch64__)

/* x0..x7 carry the arguments; x19 is the context, x20 the frame, x21 the output; x9, x10 and
 * x16 are scratch. Every instruction is one little-endian word. */
#define X9 9U
#define X10 10U
#define X11 11U
#define X16 16U
#define X19 19U
#define X20 20U
#define X21 21U
#define XZR 31U

RXF_INLINE void a_imm(rxf_cc *e, rxf_u32 reg, rxf_u64 value) {
    put32(e, 0xD2800000U | (((rxf_u32)value & 0xFFFFU) << 5) | reg);
    for (rxf_u32 shift = 1; shift < 4; shift++) {
        rxf_u32 piece = (rxf_u32)(value >> (16 * shift)) & 0xFFFFU;
        if (piece)
            put32(e, 0xF2800000U | (shift << 21) | (piece << 5) | reg);
    }
}

RXF_INLINE void a_load(rxf_cc *e, rxf_u32 size, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    rxf_u32 opcode = size == 8   ? 0xF9400000U
                     : size == 4 ? 0xB9400000U
                     : size == 2 ? 0x79400000U
                                 : 0x39400000U;
    put32(e, opcode | ((disp / size) << 10) | (base << 5) | reg);
}

RXF_INLINE void a_store(rxf_cc *e, rxf_u32 size, rxf_u32 reg, rxf_u32 base, rxf_u32 disp) {
    rxf_u32 opcode = size == 8   ? 0xF9000000U
                     : size == 4 ? 0xB9000000U
                     : size == 2 ? 0x79000000U
                                 : 0x39000000U;
    put32(e, opcode | ((disp / size) << 10) | (base << 5) | reg);
}

RXF_INLINE void a_mov(rxf_cc *e, rxf_u32 dst, rxf_u32 src) {
    put32(e, 0xAA0003E0U | (src << 16) | dst);
}

RXF_INLINE void a_add(rxf_cc *e, rxf_u32 dst, rxf_u32 base, rxf_u32 imm) {
    put32(e, 0x91000000U | (imm << 10) | (base << 5) | dst);
}

RXF_INLINE void a_copy(rxf_cc *e, rxf_u32 dst_base, rxf_u64 dst, rxf_u32 src_base, rxf_u64 src,
                       rxf_u64 count) {
    for (rxf_u64 done = 0; done < count;) {
        rxf_u32 size = pick(count - done, dst + done, src + done);
        a_load(e, size, X9, src_base, (rxf_u32)(src + done));
        a_store(e, size, X9, dst_base, (rxf_u32)(dst + done));
        done += size;
    }
}

RXF_INLINE void a_zero(rxf_cc *e, rxf_u32 base, rxf_u64 disp, rxf_u64 count) {
    for (rxf_u64 done = 0; done < count;) {
        rxf_u32 size = pick(count - done, disp + done, 0);
        a_store(e, size, XZR, base, (rxf_u32)(disp + done));
        done += size;
    }
}

RXF_INLINE void a_epilogue(rxf_cc *e) {
    put32(e, 0x910003FFU | ((rxf_u32)e->alloc << 10)); /* add sp, sp, alloc */
    put32(e, 0xF94013F5U);                             /* ldr x21, [sp, 32] */
    put32(e, 0xA94153F3U);                             /* ldp x19, x20, [sp, 16] */
    put32(e, 0xA8C37BFDU);                             /* ldp x29, x30, [sp], 48 */
    put32(e, 0xD65F03C0U);                             /* ret */
}

RXF_INLINE void a_value(rxf_cc *e, rxf_u32 reg, const rxf_u64 *op, rxf_u64 object, rxf_u32 wide) {
    if (op[0] == RXF_OPERAND_LITERAL) {
        a_imm(e, reg, op[1]);
    } else if (op[0] == RXF_OPERAND_OBJECT) {
        a_imm(e, reg, object);
    } else if (wide && op[2] > 8) {
        a_add(e, reg, X20, (rxf_u32)op[1]);
    } else {
        a_load(e, op[2] >= 8 ? 8U : (rxf_u32)op[2], reg, X20, (rxf_u32)op[1]);
    }
}

RXF_INLINE void be_begin(rxf_cc *e, rxf_u64 words) {
    e->alloc = RXF_FRAME_ALLOC; /* the interpreter's frame and more: a pointer may reach past it */
    put32(e, 0xA9BD7BFDU);      /* stp x29, x30, [sp, -48]! */
    put32(e, 0xA90153F3U);      /* stp x19, x20, [sp, 16] */
    put32(e, 0xF90013F5U);      /* str x21, [sp, 32] */
    put32(e, 0xD10003FFU | ((rxf_u32)e->alloc << 10)); /* sub sp, sp, alloc */
    a_add(e, X20, 31, 0);                              /* x20 = sp */
    a_mov(e, X19, 0);
    a_mov(e, X21, 3);
    put32(e, 0xF100001FU | ((rxf_u32)words << 10) | (2U << 5)); /* cmp x2, words */
    rxf_u64 at = e->size;
    put32(e, 0x54000000U); /* b.eq past the refusal */
    put32(e, 0x528000C0U); /* mov w0, RXF_REFUSE_OUT_OF_RANGE (6) */
    a_epilogue(e);
    if (at + 4 <= e->cap) {
        rxf_u32 skip = (rxf_u32)((e->size - at) / 4);
        patch32(e, at, 0x54000000U | (skip << 5));
    }
    a_zero(e, X20, 0, e->frame);
}

RXF_INLINE void be_param(rxf_cc *e, rxf_u64 slot, rxf_u64 width, rxf_u64 used) {
    a_copy(e, X20, slot, 1, used, width);
}

RXF_INLINE void be_move(rxf_cc *e, rxf_u64 slot, rxf_u64 width, const rxf_u64 *src, rxf_u64 object,
                        rxf_u64 have) {
    a_zero(e, X20, slot, width);
    if (src[0] == RXF_OPERAND_SLOT) {
        a_copy(e, X20, slot, X20, src[1], have);
    } else if (src[0] == RXF_OPERAND_OBJECT) {
        a_imm(e, X10, object);
        a_copy(e, X20, slot, X10, 0, have);
    } else {
        for (rxf_u64 done = 0; done < have;) {
            rxf_u32 size = pick(have - done, slot + done, 0);
            rxf_u64 piece = src[1] >> (8 * done);
            a_imm(e, X9, size == 8 ? piece : piece & (((rxf_u64)1 << (8 * size)) - 1));
            a_store(e, size, X9, X20, (rxf_u32)(slot + done));
            done += size;
        }
    }
}

/* A handle goes by address of a copy that the callee may change: the copy is in the frame. */
RXF_INLINE void a_handle(rxf_cc *e, rxf_u32 reg, rxf_u32 which, const rxf_u64 *operands,
                         const rxf_u64 *objects, rxf_u32 ordinal) {
    const rxf_u64 *op = operands + which * RXF_OPERAND_WORDS;
    rxf_u64 copy = RXF_HANDLES_AT + (rxf_u64)ordinal * RXF_HANDLE_BYTES;
    if (op[0] == RXF_OPERAND_OBJECT) {
        a_imm(e, X10, objects[which]);
        a_copy(e, X20, copy, X10, 0, RXF_HANDLE_BYTES);
    } else {
        a_copy(e, X20, copy, X20, op[1], RXF_HANDLE_BYTES);
    }
    a_add(e, reg, X20, (rxf_u32)copy);
}

RXF_INLINE void a_argument(rxf_cc *e, rxf_u32 reg, rxf_u32 which, rxf_u32 type,
                           const rxf_u64 *operands, const rxf_u64 *objects, rxf_u64 out_slot) {
    if (which == ARG_CONTEXT) {
        a_mov(e, reg, X19);
    } else if (which == ARG_OUT) {
        a_add(e, reg, X20, (rxf_u32)out_slot);
    } else if (which == ARG_STAGE) {
        a_add(e, reg, X20, RXF_STAGE_AT);
    } else {
        const rxf_u64 *op = operands + which * RXF_OPERAND_WORDS;
        if (type == 'b' && op[0] == RXF_OPERAND_LITERAL)
            a_imm(e, reg, op[1] & 0xFF);
        else
            a_value(e, reg, op, objects[which], 1);
    }
}

RXF_INLINE void be_call(rxf_cc *e, const rxf_u32 *arg, const rxf_u32 *type, rxf_u32 n,
                        rxf_u64 function, const rxf_u64 *operands, const rxf_u64 *objects,
                        rxf_u32 has_out, rxf_u64 out_slot, rxf_u64 out_width, rxf_u64 status) {
    rxf_u32 handles = 0;
    for (rxf_u32 i = 0; i < n; i++) {
        if (type[i] == 'h')
            a_handle(e, i, arg[i], operands, objects, handles++);
        else
            a_argument(e, i, arg[i], type[i], operands, objects, out_slot);
    }
    if (has_out)
        a_zero(e, X20, out_slot, out_width);
    a_imm(e, X16, function);
    put32(e, 0xD63F0000U | (X16 << 5)); /* blr x16 */
    if (status == RXF_STATUS_UNCAUGHT) {
        rxf_u64 at = e->size;
        put32(e, 0xB4000000U); /* cbz x0, past the answer */
        a_epilogue(e);
        if (at + 4 <= e->cap) {
            rxf_u32 skip = (rxf_u32)((e->size - at) / 4);
            patch32(e, at, 0xB4000000U | (skip << 5));
        }
    } else if (status != RXF_GRAPH_NONE) {
        a_store(e, 4, 0, X20, (rxf_u32)status);
    }
}

RXF_INLINE void be_return(rxf_cc *e, const rxf_u64 *src, rxf_u64 object, rxf_u64 width) {
    if (width > 8) {
        if (src[0] == RXF_OPERAND_OBJECT) {
            a_imm(e, X10, object);
            a_copy(e, X21, 0, X10, 0, width);
        } else {
            a_copy(e, X21, 0, X20, src[1], width);
        }
    } else {
        a_value(e, X9, src, object, 0);
        a_store(e, 8, X9, X21, 0);
    }
    put32(e, 0x2A1F03E0U); /* mov w0, wzr */
    a_epilogue(e);
}

RXF_INLINE void be_refuse(rxf_cc *e, const rxf_u64 *src, rxf_u64 object) {
    a_value(e, 0, src, object, 0);
    a_epilogue(e);
}

/* The safe point of an edge that goes back (native/safe.c): the leaf is called on the context and
 * the area, and what it answers other than OK is the status of the program. */
RXF_INLINE void be_safe_point(rxf_cc *e) {
    a_mov(e, 0, X19);
    a_imm(e, 1, e->area);
    a_imm(e, X16, e->safe);
    put32(e, 0xD63F0000U | (X16 << 5)); /* blr x16 */
    rxf_u64 at = e->size;
    put32(e, 0x34000000U); /* cbz w0, past the return */
    a_epilogue(e);
    if (e->write && at + 4 <= e->cap)
        patch32(e, at, 0x34000000U | ((rxf_u32)((e->size - at) / 4) << 5));
}

RXF_INLINE void be_zero(rxf_cc *e, rxf_u64 slot, rxf_u64 width) {
    a_zero(e, X20, slot, width);
}

RXF_INLINE void be_slot_copy(rxf_cc *e, rxf_u64 dst, rxf_u64 src, rxf_u64 width) {
    a_copy(e, X20, dst, X20, src, width);
}

/* dst (one byte) = a < b, unsigned */
RXF_INLINE void be_less(rxf_cc *e, rxf_u64 dst, const rxf_u64 *a, rxf_u64 a_object,
                        const rxf_u64 *b, rxf_u64 b_object) {
    a_value(e, X9, a, a_object, 0);
    a_value(e, X10, b, b_object, 0);
    put32(e, 0xEB0A013FU); /* cmp x9, x10 */
    put32(e, 0x1A9F27E9U); /* cset w9, lo */
    a_store(e, 1, X9, X20, (rxf_u32)dst);
}

/* dst (width 1, 2, 4 or 8 bytes) = src + 1 */
RXF_INLINE void be_increment(rxf_cc *e, rxf_u64 dst, rxf_u64 width, const rxf_u64 *src,
                             rxf_u64 object) {
    a_value(e, X9, src, object, 0);
    a_add(e, X9, X9, 1);
    a_store(e, width >= 8 ? 8U : (rxf_u32)width, X9, X20, (rxf_u32)dst);
}

/* The value that the next `if` looks at. */
RXF_INLINE void be_value(rxf_cc *e, const rxf_u64 *op, rxf_u64 object) {
    a_value(e, X11, op, object, 0);
}

/* The code up to the matching end runs only when the value is zero (`on_zero`) or not. The place
 * to patch is the instruction that skips it. */
RXF_INLINE rxf_u64 be_if_zero(rxf_cc *e, rxf_u32 on_zero) {
    rxf_u64 at = e->size;
    /* cbnz skips the code when the value is not zero, cbz when it is */
    put32(e, (on_zero ? 0xB5000000U : 0xB4000000U) | X11);
    return at;
}

/* ... only when the value is the tag */
RXF_INLINE rxf_u64 be_if_tag(rxf_cc *e, rxf_u64 tag) {
    a_imm(e, X10, tag);
    put32(e, 0xEB0A017FU); /* cmp x11, x10 */
    rxf_u64 at = e->size;
    put32(e, 0x54000001U); /* b.ne past the code */
    return at;
}

RXF_INLINE void be_if_else(rxf_cc *e, rxf_u64 at) {
    if (e->write && at + 4 <= e->cap) {
        rxf_u32 skip = (rxf_u32)((e->size - at) / 4);
        rxf_u32 word = 0;
        for (rxf_u32 i = 0; i < 4; i++)
            word |= (rxf_u32)((volatile rxf_u8 *)e->buf)[at + i] << (8 * i);
        patch32(e, at, word | (skip << 5));
    }
}

RXF_INLINE void be_if_end(rxf_cc *e, rxf_u64 at) {
    be_if_else(e, at);
}

RXF_INLINE void be_if_else_end(rxf_cc *e) {
    (void)e;
}

RXF_INLINE void be_dispatch(rxf_cc *e) {
    (void)e;
}

RXF_INLINE void be_enter(rxf_cc *e) {
    (void)e;
}

/* b to the start of instruction `target` */
RXF_INLINE void be_jump(rxf_cc *e, rxf_u64 target) {
    rxf_i64 words = ((rxf_i64)e->offset[target] - (rxf_i64)e->size) / 4;
    put32(e, 0x14000000U | ((rxf_u32)words & 0x03FFFFFFU));
}

/* what is after the last instruction: the program ran off its end */
RXF_INLINE void be_tail(rxf_cc *e) {
    put32(e, 0x528000C0U); /* mov w0, RXF_REFUSE_OUT_OF_RANGE (6) */
    a_epilogue(e);
}

#elif defined(__wasm32__)

/* The body of a function (i32 context, i32 args, i64 argc, i32 out) -> i32 with locals: 4 the
 * frame, 5 a temporary, 6 the next instruction (i32), 7 the value an `if` looks at (i64). The
 * frame is taken from global 0, the stack pointer of the module convention. The type of a
 * call_indirect is the one of the leaf: its id less one (the module lists the types of the leaves
 * first, in the order of their ids). */
#define W_FRAME 4U
#define W_TEMP 5U
#define W_NEXT 6U
#define W_VALUE 7U

RXF_INLINE void w_uleb(rxf_cc *e, rxf_u64 value) {
    do {
        rxf_u32 byte = (rxf_u32)(value & 0x7F);
        value >>= 7;
        put(e, value ? byte | 0x80U : byte);
    } while (value);
}

RXF_INLINE void w_sleb(rxf_cc *e, rxf_i64 value) {
    for (;;) {
        rxf_u32 byte = (rxf_u32)(value & 0x7F);
        value >>= 7;
        if ((value == 0 && !(byte & 0x40U)) || (value == -1 && (byte & 0x40U))) {
            put(e, byte);
            return;
        }
        put(e, byte | 0x80U);
    }
}

RXF_INLINE void w_get(rxf_cc *e, rxf_u32 local) {
    put(e, 0x20);
    w_uleb(e, local);
}

RXF_INLINE void w_i32(rxf_cc *e, rxf_u64 value) {
    put(e, 0x41);
    w_sleb(e, (rxf_i64)(int)(rxf_u32)value);
}

RXF_INLINE void w_i64(rxf_cc *e, rxf_u64 value) {
    put(e, 0x42);
    w_sleb(e, (rxf_i64)value);
}

RXF_INLINE void w_mem(rxf_cc *e, rxf_u32 opcode, rxf_u64 offset) {
    put(e, opcode);
    put(e, 0); /* alignment 1: the hint of a copy that is not aligned */
    w_uleb(e, offset);
}

/* Leaves the address `base + disp` of a piece of memory on the stack as an offset: the base is
 * a local, or 0xFF for an absolute address (the instruction then carries it as its offset). */
#define W_ABSOLUTE 0xFFU

RXF_INLINE void w_base(rxf_cc *e, rxf_u32 base) {
    if (base == W_ABSOLUTE)
        w_i32(e, 0);
    else
        w_get(e, base);
}

/* load of `size` bytes, zero-extended to i32 (to i64 when `wide`) */
RXF_INLINE void w_load(rxf_cc *e, rxf_u32 size, rxf_u32 wide, rxf_u32 base, rxf_u64 disp) {
    w_base(e, base);
    if (wide)
        w_mem(e, size == 8 ? 0x29U : size == 4 ? 0x35U : size == 2 ? 0x33U : 0x31U, disp);
    else
        w_mem(e, size == 4 ? 0x28U : size == 2 ? 0x2FU : 0x2DU, disp);
}

RXF_INLINE void w_copy(rxf_cc *e, rxf_u32 dst_base, rxf_u64 dst, rxf_u32 src_base, rxf_u64 src,
                       rxf_u64 count) {
    for (rxf_u64 done = 0; done < count;) {
        rxf_u64 left = count - done;
        rxf_u32 size = left >= 8 ? 8U : left >= 4 ? 4U : left >= 2 ? 2U : 1U;
        w_base(e, dst_base);
        w_load(e, size, size == 8, src_base, src + done);
        w_mem(e, size == 8 ? 0x37U : size == 4 ? 0x36U : size == 2 ? 0x3BU : 0x3AU, dst + done);
        done += size;
    }
}

RXF_INLINE void w_zero(rxf_cc *e, rxf_u32 base, rxf_u64 disp, rxf_u64 count) {
    for (rxf_u64 done = 0; done < count;) {
        rxf_u64 left = count - done;
        rxf_u32 size = left >= 8 ? 8U : left >= 4 ? 4U : left >= 2 ? 2U : 1U;
        w_base(e, base);
        if (size == 8)
            w_i64(e, 0);
        else
            w_i32(e, 0);
        w_mem(e, size == 8 ? 0x37U : size == 4 ? 0x36U : size == 2 ? 0x3BU : 0x3AU, disp + done);
        done += size;
    }
}

/* restores the stack pointer; the stack of values is not touched */
RXF_INLINE void w_leave(rxf_cc *e) {
    w_get(e, W_FRAME);
    w_i32(e, e->alloc);
    put(e, 0x6A); /* i32.add */
    put(e, 0x24); /* global.set 0 */
    put(e, 0x00);
}

/* The value of an operand as an i64 on the stack. */
RXF_INLINE void w_value(rxf_cc *e, const rxf_u64 *op, rxf_u64 object, rxf_u32 wide) {
    if (op[0] == RXF_OPERAND_LITERAL) {
        w_i64(e, op[1]);
    } else if (op[0] == RXF_OPERAND_OBJECT) {
        w_i64(e, object);
    } else if (wide && op[2] > 8) {
        w_get(e, W_FRAME);
        w_i32(e, op[1]);
        put(e, 0x6A);
        put(e, 0xAD); /* i64.extend_i32_u */
    } else {
        w_load(e, op[2] >= 8 ? 8U : (rxf_u32)op[2], 1, W_FRAME, op[1]);
    }
}

RXF_INLINE void be_begin(rxf_cc *e, rxf_u64 words) {
    e->alloc = RXF_FRAME_ALLOC; /* the interpreter's frame and more: a pointer may reach past it */
    put(e, 2);                  /* two groups of locals: three i32, one i64 */
    put(e, 3);
    put(e, 0x7F);
    put(e, 1);
    put(e, 0x7E);
    put(e, 0x23); /* global.get 0 */
    put(e, 0x00);
    w_i32(e, e->alloc);
    put(e, 0x6B); /* i32.sub */
    put(e, 0x22); /* local.tee frame */
    w_uleb(e, W_FRAME);
    put(e, 0x24); /* global.set 0 */
    put(e, 0x00);
    w_get(e, 2); /* if argc != words: refuse */
    w_i64(e, words);
    put(e, 0x52); /* i64.ne */
    put(e, 0x04); /* if */
    put(e, 0x40);
    w_i32(e, RXF_REFUSE_OUT_OF_RANGE);
    w_leave(e);
    put(e, 0x0F); /* return */
    put(e, 0x0B); /* end */
    w_zero(e, W_FRAME, 0, e->frame);
}

RXF_INLINE void be_param(rxf_cc *e, rxf_u64 slot, rxf_u64 width, rxf_u64 used) {
    w_copy(e, W_FRAME, slot, 1, used, width);
}

RXF_INLINE void be_move(rxf_cc *e, rxf_u64 slot, rxf_u64 width, const rxf_u64 *src, rxf_u64 object,
                        rxf_u64 have) {
    w_zero(e, W_FRAME, slot, width);
    if (src[0] == RXF_OPERAND_SLOT) {
        w_copy(e, W_FRAME, slot, W_FRAME, src[1], have);
    } else if (src[0] == RXF_OPERAND_OBJECT) {
        w_copy(e, W_FRAME, slot, W_ABSOLUTE, object, have);
    } else {
        for (rxf_u64 done = 0; done < have;) {
            rxf_u64 left = have - done;
            rxf_u32 size = left >= 8 ? 8U : left >= 4 ? 4U : left >= 2 ? 2U : 1U;
            rxf_u64 piece = src[1] >> (8 * done);
            w_get(e, W_FRAME);
            if (size == 8)
                w_i64(e, piece);
            else
                w_i32(e, piece & (((rxf_u64)1 << (8 * size)) - 1));
            w_mem(e,
                  size == 8   ? 0x37U
                  : size == 4 ? 0x36U
                  : size == 2 ? 0x3BU
                              : 0x3AU,
                  slot + done);
            done += size;
        }
    }
}

RXF_INLINE void be_call(rxf_cc *e, const rxf_u32 *arg, const rxf_u32 *type, rxf_u32 n,
                        rxf_u64 function, rxf_u64 leaf, const rxf_u64 *operands,
                        const rxf_u64 *objects, rxf_u32 has_out, rxf_u64 out_slot,
                        rxf_u64 out_width, rxf_u64 status) {
    /* a handle goes by address of a copy: the copies come first, in the frame */
    rxf_u32 ordinal = 0;
    for (rxf_u32 i = 0; i < n; i++) {
        if (type[i] != 'h')
            continue;
        const rxf_u64 *op = operands + arg[i] * RXF_OPERAND_WORDS;
        rxf_u64 copy = RXF_HANDLES_AT + (rxf_u64)ordinal++ * RXF_HANDLE_BYTES;
        if (op[0] == RXF_OPERAND_OBJECT)
            w_copy(e, W_FRAME, copy, W_ABSOLUTE, objects[arg[i]], RXF_HANDLE_BYTES);
        else
            w_copy(e, W_FRAME, copy, W_FRAME, op[1], RXF_HANDLE_BYTES);
    }
    ordinal = 0;
    for (rxf_u32 i = 0; i < n; i++) {
        rxf_u32 which = arg[i], kind = type[i];
        if (kind == 'h') {
            w_get(e, W_FRAME);
            w_i32(e, RXF_HANDLES_AT + (rxf_u64)ordinal++ * RXF_HANDLE_BYTES);
            put(e, 0x6A);
        } else if (which == ARG_CONTEXT) {
            w_get(e, 0);
        } else if (which == ARG_OUT) {
            w_get(e, W_FRAME);
            w_i32(e, out_slot);
            put(e, 0x6A);
        } else if (which == ARG_STAGE) {
            w_get(e, W_FRAME);
            w_i32(e, RXF_STAGE_AT);
            put(e, 0x6A); /* i32.add */
            put(e, 0xAD); /* i64.extend_i32_u: the staged words are a u64 address */
        } else {
            const rxf_u64 *op = operands + which * RXF_OPERAND_WORDS;
            if (kind == 'b' && op[0] == RXF_OPERAND_LITERAL) {
                w_i32(e, op[1] & 0xFF);
            } else {
                w_value(e, op, objects[which], 1);
                if (kind != 'q')
                    put(e, 0xA7); /* i32.wrap_i64 */
            }
        }
    }
    if (has_out)
        w_zero(e, W_FRAME, out_slot, out_width);
    w_i32(e, function);
    put(e, 0x11); /* call_indirect */
    w_uleb(e, leaf - 1);
    put(e, 0x00);
    if (status == RXF_STATUS_UNCAUGHT) {
        put(e, 0x21); /* local.set temp */
        w_uleb(e, W_TEMP);
        w_get(e, W_TEMP);
        put(e, 0x04); /* if */
        put(e, 0x40);
        w_get(e, W_TEMP);
        w_leave(e);
        put(e, 0x0F); /* return */
        put(e, 0x0B);
    } else if (status != RXF_GRAPH_NONE) {
        put(e, 0x21); /* local.set temp */
        w_uleb(e, W_TEMP);
        w_get(e, W_FRAME);
        w_get(e, W_TEMP);
        w_mem(e, 0x36, status);
    } else {
        put(e, 0x1A); /* drop */
    }
}

RXF_INLINE void be_return(rxf_cc *e, const rxf_u64 *src, rxf_u64 object, rxf_u64 width) {
    if (width > 8) {
        if (src[0] == RXF_OPERAND_OBJECT)
            w_copy(e, 3, 0, W_ABSOLUTE, object, width);
        else
            w_copy(e, 3, 0, W_FRAME, src[1], width);
    } else {
        w_get(e, 3);
        w_value(e, src, object, 0);
        w_mem(e, 0x37, 0);
    }
    w_i32(e, 0);
    w_leave(e);
    put(e, 0x0F);
}

RXF_INLINE void be_refuse(rxf_cc *e, const rxf_u64 *src, rxf_u64 object) {
    w_value(e, src, object, 0);
    put(e, 0xA7);
    w_leave(e);
    put(e, 0x0F);
}

/* The safe point of an edge that goes back (native/safe.c): the leaf is called on the context and
 * the area, and what it answers other than OK is the status of the program. */
RXF_INLINE void be_safe_point(rxf_cc *e) {
    w_get(e, 0);
    w_i32(e, e->area);
    w_i32(e, e->safe);
    put(e, 0x11); /* call_indirect */
    w_uleb(e, RXF_LEAF_SAFE_POINT - 1);
    put(e, 0x00);
    put(e, 0x21); /* local.set temp */
    w_uleb(e, W_TEMP);
    w_get(e, W_TEMP);
    put(e, 0x04); /* if */
    put(e, 0x40);
    w_get(e, W_TEMP);
    w_leave(e);
    put(e, 0x0F); /* return */
    put(e, 0x0B);
}

RXF_INLINE void be_zero(rxf_cc *e, rxf_u64 slot, rxf_u64 width) {
    w_zero(e, W_FRAME, slot, width);
}

RXF_INLINE void be_slot_copy(rxf_cc *e, rxf_u64 dst, rxf_u64 src, rxf_u64 width) {
    w_copy(e, W_FRAME, dst, W_FRAME, src, width);
}

/* dst (one byte) = a < b, unsigned */
RXF_INLINE void be_less(rxf_cc *e, rxf_u64 dst, const rxf_u64 *a, rxf_u64 a_object,
                        const rxf_u64 *b, rxf_u64 b_object) {
    w_get(e, W_FRAME);
    w_value(e, a, a_object, 0);
    w_value(e, b, b_object, 0);
    put(e, 0x54); /* i64.lt_u */
    w_mem(e, 0x3A, dst);
}

/* dst (width 1, 2, 4 or 8 bytes) = src + 1 */
RXF_INLINE void be_increment(rxf_cc *e, rxf_u64 dst, rxf_u64 width, const rxf_u64 *src,
                             rxf_u64 object) {
    w_get(e, W_FRAME);
    w_value(e, src, object, 0);
    w_i64(e, 1);
    put(e, 0x7C); /* i64.add */
    if (width >= 8) {
        w_mem(e, 0x37, dst);
    } else {
        put(e, 0xA7); /* i32.wrap_i64 */
        w_mem(e, width == 4 ? 0x36U : width == 2 ? 0x3BU : 0x3AU, dst);
    }
}

/* The value that the next `if` looks at. */
RXF_INLINE void be_value(rxf_cc *e, const rxf_u64 *op, rxf_u64 object) {
    w_value(e, op, object, 0);
    put(e, 0x21); /* local.set value */
    w_uleb(e, W_VALUE);
}

/* The code up to the matching end runs only when the value is zero (`on_zero`) or not. */
RXF_INLINE rxf_u64 be_if_zero(rxf_cc *e, rxf_u32 on_zero) {
    w_get(e, W_VALUE);
    put(e, 0x50); /* i64.eqz */
    if (!on_zero)
        put(e, 0x45); /* i32.eqz */
    put(e, 0x04);
    put(e, 0x40);
    e->nest++;
    return 0;
}

/* ... only when the value is the tag */
RXF_INLINE rxf_u64 be_if_tag(rxf_cc *e, rxf_u64 tag) {
    w_get(e, W_VALUE);
    w_i64(e, tag);
    put(e, 0x51); /* i64.eq */
    put(e, 0x04);
    put(e, 0x40);
    e->nest++;
    return 0;
}

RXF_INLINE void be_if_else(rxf_cc *e, rxf_u64 at) {
    (void)at;
    put(e, 0x05);
}

RXF_INLINE void be_if_end(rxf_cc *e, rxf_u64 at) {
    (void)at;
    put(e, 0x0B);
    e->nest--;
}

RXF_INLINE void be_if_else_end(rxf_cc *e) {
    put(e, 0x0B);
    e->nest--;
}

/* A loop around one block for each instruction; `br_table` on the next instruction goes to the end
 * of its block, which is where its code is. */
RXF_INLINE void be_dispatch(rxf_cc *e) {
    put(e, 0x03); /* loop */
    put(e, 0x40);
    for (rxf_u64 i = 0; i < e->instructions; i++) {
        put(e, 0x02); /* block */
        put(e, 0x40);
    }
    w_get(e, W_NEXT);
    put(e, 0x0E); /* br_table */
    w_uleb(e, e->instructions);
    for (rxf_u64 i = 0; i < e->instructions; i++)
        w_uleb(e, i);
    w_uleb(e, e->instructions - 1);
}

/* the end of the block of an instruction: its code follows */
RXF_INLINE void be_enter(rxf_cc *e) {
    put(e, 0x0B);
}

/* the next instruction is `target`: back to the loop */
RXF_INLINE void be_jump(rxf_cc *e, rxf_u64 target) {
    w_i32(e, target);
    put(e, 0x21); /* local.set next */
    w_uleb(e, W_NEXT);
    put(e, 0x0C); /* br loop */
    w_uleb(e, e->instructions - 1 - e->current + e->nest);
}

/* what is after the last instruction: the program ran off its end */
RXF_INLINE void be_tail(rxf_cc *e) {
    put(e, 0x0B); /* end of the loop */
    w_i32(e, RXF_REFUSE_OUT_OF_RANGE);
    w_leave(e);
    put(e, 0x0F);
    put(e, 0x0B); /* end of the function */
}

#endif

/* ---- the front end: what the interpreter checks, once, here ------------------------------------
 */

RXF_INLINE rxf_u32 have(const rxf_cc *e, rxf_u64 at, rxf_u64 words) {
    return at <= e->count && words <= e->count - at;
}

RXF_INLINE rxf_u32 in_frame(const rxf_cc *e, rxf_u64 offset, rxf_u64 width) {
    return width <= e->frame && offset <= e->frame - width;
}

RXF_INLINE rxf_u64 find_leaf(const rxf_cc *e, rxf_u64 leaf) {
    const rxf_native_slot *slots = (const rxf_native_slot *)(rxf_uptr)e->c->functions;
    for (rxf_u64 i = 0; i < e->c->function_count; i++)
        if (slots[i].id == leaf)
            return slots[i].address;
    return 0;
}

/* The area of the first Channel in the capability table of the context, or 0. */
RXF_INLINE rxf_u64 channel_area(const rxf_runtime_context *c) {
    const rxf_native_slot *table = (const rxf_native_slot *)(rxf_uptr)c->capabilities;
    for (rxf_u64 i = 0; i < c->capability_count; i++)
        if (table[i].id == RXF_CAPABILITY_CHANNEL)
            return table[i].address;
    return 0;
}

/* locate() of graph.c: the operand has `need` bytes; the address of an object. */
RXF_INLINE rxf_u32 check(const rxf_cc *e, const rxf_u64 *op, rxf_u64 need, rxf_u64 *object) {
    switch (op[0]) {
    case RXF_OPERAND_SLOT:
        return in_frame(e, op[1], need) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    case RXF_OPERAND_LITERAL:
        return need > 8 ? RXF_REFUSE_OUT_OF_RANGE : RXF_OK;
    case RXF_OPERAND_OBJECT: {
        rxf_object_entry *found = entry(e->c, op[1]);
        if (!found)
            return RXF_REFUSE_UNBOUND;
        if (need > found->size)
            return RXF_REFUSE_OUT_OF_RANGE;
        *object = found->payload;
        return RXF_OK;
    }
    default:
        return RXF_REFUSE_OUT_OF_RANGE;
    }
}

/* load() and pointer() of graph.c: an operand that becomes a value. A slot is read whole when its
 * width is one a machine reads (1, 2, 4, 8), by address when it is wider (`wide`). */
RXF_INLINE rxf_u32 value_check(const rxf_cc *e, const rxf_u64 *op, rxf_u32 wide, rxf_u64 *object) {
    if (op[0] == RXF_OPERAND_LITERAL)
        return op[2] > 8 ? RXF_REFUSE_OUT_OF_RANGE : RXF_OK;
    if (op[0] == RXF_OPERAND_OBJECT)
        return check(e, op, 0, object);
    if (op[0] != RXF_OPERAND_SLOT)
        return RXF_REFUSE_OUT_OF_RANGE;
    if (wide && op[2] > 8)
        return check(e, op, op[2], object);
    rxf_u32 status = check(e, op, op[2] < 8 ? op[2] : 8, object);
    if (status)
        return status;
    rxf_u64 size = op[2] >= 8 ? 8 : op[2];
    if (size != 1 && size != 2 && size != 4 && size != 8)
        return RXF_REFUSE_UNSUPPORTED;
    return op[1] & (size - 1) ? RXF_REFUSE_UNSUPPORTED : RXF_OK; /* a machine reads it aligned */
}

/* RETURN and REFUSE at `at`, written where the code is now. */
RXF_INLINE rxf_u32 terminal(rxf_cc *e, rxf_u64 at) {
    const rxf_u64 *w = e->code + at;
    rxf_u64 object = 0;
    if (w[0] == RXF_OP_RETURN) {
        rxf_u32 status =
            w[4] > 8 ? check(e, w + 1, w[4], &object) : value_check(e, w + 1, 0, &object);
        if (status)
            return status;
        be_return(e, w + 1, object, w[4]);
        return RXF_OK;
    }
    rxf_u32 status = value_check(e, w + 1, 0, &object);
    if (status)
        return status;
    be_refuse(e, w + 1, object);
    return RXF_OK;
}

/* A call of a program (word 1 is RXF_GRAPH_CALL): the callee runs in a run of its own -- the
 * interpreter entry is leaf RXF_LEAF_GRAPH_RUN -- with its handle and its arguments staged at
 * RXF_STAGE_AT the way a run binds them (a parameter's words, zeroed, the operand's bytes first).
 * The answer comes back to the call's out word and the call goes on where answer() of graph.c
 * leaves it: the status to its slot, or a refusal nothing catches ends the run. The compile
 * resolves the callee where it stands: one published anew between this and the run refuses
 * STALE_GENERATION -- the seam of a call that names its callee by the generation it has here. */
RXF_INLINE rxf_u32 compile_program_call(rxf_cc *e, rxf_u64 at) {
    const rxf_u64 *w = e->code + at;
    rxf_u64 count = w[2], out_slot = w[3], out_width = w[4], status = w[5];
    const rxf_u64 *name = w + RXF_CALL_HEADER;
    const rxf_u64 *operands = name + RXF_OPERAND_WORDS;
    if (name[0] != RXF_OPERAND_OBJECT || name[2] != 0)
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_object_entry *found = entry(e->c, name[1]);
    if (!found || found->state != RXF_OBJECT_LIVE)
        return RXF_REFUSE_UNBOUND;
    if (found->payload & 7)
        return RXF_REFUSE_OUT_OF_RANGE;
    const rxf_u64 *callee = (const rxf_u64 *)(rxf_uptr)found->payload;
    rxf_u64 callee_words = found->size / 8;
    if (callee_words < RXF_SLOT_WORDS || callee[0] != RXF_GRAPH_MAGIC)
        return RXF_REFUSE_TYPE_MISMATCH;
    rxf_u64 parameters = callee[2], words = callee[3];
    if (parameters != count || parameters > callee_words ||
        RXF_SLOT_WORDS + parameters * 2 > callee_words)
        return RXF_REFUSE_OUT_OF_RANGE;
    if (out_slot != RXF_GRAPH_NONE && (out_width != 8 || !in_frame(e, out_slot, out_width)))
        return RXF_REFUSE_UNSUPPORTED; /* the out of a call is one word where the compile goes */
    if (status != RXF_GRAPH_NONE && (!in_frame(e, status, 4) || (status & 3)))
        return (status & 3) ? RXF_REFUSE_UNSUPPORTED : RXF_REFUSE_OUT_OF_RANGE;
    for (rxf_u64 i = 0; i + RXF_OPERAND_WORDS + 2 <= callee_words; i++)
        if (callee[i] == RXF_OP_RETURN && callee[i + 4] > 8)
            return RXF_REFUSE_UNSUPPORTED; /* an answer wider than the one word it comes to */
    rxf_u64 function = find_leaf(e, RXF_LEAF_GRAPH_RUN);
    if (!function)
        return RXF_REFUSE_UNBOUND;
    rxf_u64 used = 0;
    for (rxf_u64 i = 0; i < parameters; i++) {
        rxf_u64 width = callee[RXF_SLOT_WORDS + 2 * i + 1];
        const rxf_u64 *op = operands + i * RXF_OPERAND_WORDS;
        rxf_u64 object = 0;
        rxf_u64 present = op[2] < width ? op[2] : width;
        rxf_u32 refused = check(e, op, present, &object);
        if (refused)
            return refused;
        if (width > RXF_GRAPH_COPY - RXF_HANDLE_BYTES - used)
            return RXF_REFUSE_UNSUPPORTED;
        be_move(e, RXF_STAGE_AT + used, width, op, object, present);
        used += (width + 7) & ~(rxf_u64)7;
    }
    /* the handle the call names: the callee and the generation it has at this compile */
    rxf_u64 handle[RXF_OPERAND_WORDS];
    for (rxf_u32 word = 0; word < 3; word++) {
        ((volatile rxf_u64 *)handle)[0] = RXF_OPERAND_LITERAL;
        ((volatile rxf_u64 *)handle)[1] = word == 0 ? name[1] : word == 1 ? found->generation : 0;
        ((volatile rxf_u64 *)handle)[2] = 8;
        be_move(e, RXF_STAGE_AT + used + 8 * word, 8, handle, 0, 8);
    }
    rxf_u64 call[3 * RXF_OPERAND_WORDS] = {0};
    rxf_u64 objects[3] = {0};
    ((volatile rxf_u64 *)call)[0] = RXF_OPERAND_SLOT;
    ((volatile rxf_u64 *)call)[1] = RXF_STAGE_AT + used;
    ((volatile rxf_u64 *)call)[2] = RXF_HANDLE_BYTES;
    ((volatile rxf_u64 *)call)[6] = RXF_OPERAND_LITERAL;
    ((volatile rxf_u64 *)call)[7] = words;
    ((volatile rxf_u64 *)call)[8] = 8;
    rxf_u32 arg[5], type[5];
#define PROGRAM_ARG(i, a, t)                                                                       \
    (((volatile rxf_u32 *)arg)[i] = (a), ((volatile rxf_u32 *)type)[i] = (t))
    PROGRAM_ARG(0, ARG_CONTEXT, 'c');
    PROGRAM_ARG(1, 0, 'h');         /* the handle, the three words by address */
    PROGRAM_ARG(2, ARG_STAGE, 'q'); /* the staged arguments */
    PROGRAM_ARG(3, 2, 'q');         /* the words of them */
    PROGRAM_ARG(4, ARG_OUT, 'o');   /* the answer: the call's out word */
#undef PROGRAM_ARG
    rxf_u64 answer = out_slot;
    rxf_u32 zeroes = 1;
    if (out_slot == RXF_GRAPH_NONE) {
        answer = RXF_HANDLES_AT + RXF_HANDLE_BYTES; /* the run needs a word to answer to */
        zeroes = 0;
    }
#if defined(__wasm32__)
    be_call(e, arg, type, 5, function, RXF_LEAF_GRAPH_RUN, call, objects, zeroes, answer, 8,
            status == RXF_GRAPH_NONE ? RXF_STATUS_UNCAUGHT : status);
#else
    be_call(e, arg, type, 5, function, call, objects, zeroes, answer, 8,
            status == RXF_GRAPH_NONE ? RXF_STATUS_UNCAUGHT : status);
#endif
    return RXF_OK;
}

RXF_INLINE rxf_u32 compile_call(rxf_cc *e, rxf_u64 at) {
    const rxf_u64 *word = e->code + at;
    if (word[1] == RXF_GRAPH_CALL)
        return compile_program_call(e, at);
    rxf_u64 leaf = word[1], count = word[2], out_slot = word[3], out_width = word[4];
    rxf_u32 arg[RXF_MAX_ARGUMENTS], type[RXF_MAX_ARGUMENTS];
    rxf_u32 shape = shape_of(leaf), has_out = 0;
    rxf_u32 n = plan(shape, arg, type);
    if (!n)
        return RXF_REFUSE_UNSUPPORTED; /* not a leaf with a shape */
    for (rxf_u32 i = 0; i < n; i++)
        has_out |= arg[i] == ARG_OUT;
    rxf_u32 handles;
    rxf_u32 out_of_shape;
    if (shape_layout(shape, &handles, &out_of_shape) != count)
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 function = find_leaf(e, leaf);
    if (!function)
        return RXF_REFUSE_UNBOUND;
    rxf_u64 objects[RXF_CALL_OPERANDS];
    const rxf_u64 *operands = word + RXF_CALL_HEADER;
    for (rxf_u32 i = 0; i < n; i++) {
        if (arg[i] >= ARG_CONTEXT)
            continue;
        const rxf_u64 *op = operands + arg[i] * RXF_OPERAND_WORDS;
        objects[arg[i]] = 0;
        rxf_u32 status;
        if (type[i] == 'h')
            status = check(e, op, RXF_HANDLE_BYTES, &objects[arg[i]]);
        else
            status = value_check(e, op, 1, &objects[arg[i]]);
        if (!status && type[i] == 'b' && op[0] != RXF_OPERAND_LITERAL &&
            !(op[0] == RXF_OPERAND_SLOT && op[2] == 1))
            status = RXF_REFUSE_UNSUPPORTED;
        if (status)
            return status;
    }
    if (has_out) {
        if (out_slot == RXF_GRAPH_NONE || !in_frame(e, out_slot, out_width))
            return RXF_REFUSE_OUT_OF_RANGE;
    } else if (out_slot != RXF_GRAPH_NONE) {
        return RXF_REFUSE_OUT_OF_RANGE;
    }
    if (word[5] != RXF_GRAPH_NONE && (!in_frame(e, word[5], 4) || (word[5] & 3)))
        return word[5] & 3 ? RXF_REFUSE_UNSUPPORTED : RXF_REFUSE_OUT_OF_RANGE;
#if defined(__wasm32__)
    be_call(e, arg, type, n, function, leaf, operands, objects, has_out, out_slot, out_width,
            word[5]);
#else
    be_call(e, arg, type, n, function, operands, objects, has_out, out_slot, out_width, word[5]);
#endif
    return RXF_OK;
}

/* The words of an edge: its header, and five for each copy. */
RXF_INLINE rxf_u32 edge_size(const rxf_cc *e, rxf_u64 at, rxf_u64 *words) {
    if (!have(e, at, RXF_EDGE_HEADER))
        return RXF_REFUSE_OUT_OF_RANGE;
    rxf_u64 count = e->code[at + 1];
    if (count > RXF_GRAPH_COPY || !have(e, at + RXF_EDGE_HEADER, count * RXF_EDGE_COPY_WORDS))
        return RXF_REFUSE_OUT_OF_RANGE;
    *words = RXF_EDGE_HEADER + count * RXF_EDGE_COPY_WORDS;
    return RXF_OK;
}

/* The words of the instruction at `pc`, and that all of it is in the program. */
RXF_INLINE rxf_u32 instruction_size(const rxf_cc *e, rxf_u64 pc, rxf_u64 *words) {
    const rxf_u64 *w = e->code + pc;
    rxf_u64 first, second, at;
    rxf_u32 status;
    switch (w[0]) {
    case RXF_OP_CALL: {
        /* a program call carries the callee's operand before its arguments (graph.c) */
        rxf_u64 named = w[1] == RXF_GRAPH_CALL ? RXF_OPERAND_WORDS : 0;
        if (!have(e, pc, RXF_CALL_HEADER) || w[2] > RXF_CALL_OPERANDS ||
            !have(e, pc, RXF_CALL_HEADER + named + w[2] * RXF_OPERAND_WORDS))
            return RXF_REFUSE_OUT_OF_RANGE;
        *words = RXF_CALL_HEADER + named + w[2] * RXF_OPERAND_WORDS;
        return RXF_OK;
    }
    case RXF_OP_MOVE:
    case RXF_OP_INCREMENT:
        *words = 6;
        return have(e, pc, 6) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    case RXF_OP_MAP:
        if (!have(e, pc, 8) || w[7] > e->count || !have(e, pc, 8 + w[7] * 2))
            return RXF_REFUSE_OUT_OF_RANGE;
        *words = 8 + w[7] * 2;
        return RXF_OK;
    case RXF_OP_FIELD:
    case RXF_OP_LESS:
        *words = 8;
        return have(e, pc, 8) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    case RXF_OP_CONSTRUCT:
        *words = 10;
        return have(e, pc, 10) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    case RXF_OP_GOTO:
        status = edge_size(e, pc + 1, &first);
        if (status)
            return status;
        *words = 1 + first;
        return RXF_OK;
    case RXF_OP_BRANCH:
        if (!have(e, pc, 5))
            return RXF_REFUSE_OUT_OF_RANGE;
        status = edge_size(e, pc + 5, &first);
        if (status)
            return status;
        status = edge_size(e, pc + 5 + first, &second);
        if (status)
            return status;
        *words = 5 + first + second;
        return RXF_OK;
    case RXF_OP_SWITCH:
        if (!have(e, pc, 5) || w[4] > e->count || !have(e, pc, 5 + w[4]))
            return RXF_REFUSE_OUT_OF_RANGE;
        at = pc + 5 + w[4];
        for (rxf_u64 i = 0; i <= w[4]; i++) {
            status = edge_size(e, at, &first);
            if (status)
                return status;
            at += first;
        }
        *words = at - pc;
        return RXF_OK;
    case RXF_OP_RETURN:
        *words = 5;
        return have(e, pc, 5) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    case RXF_OP_REFUSE:
        *words = 4;
        return have(e, pc, 4) ? RXF_OK : RXF_REFUSE_OUT_OF_RANGE;
    default:
        return RXF_REFUSE_OUT_OF_RANGE;
    }
}

/* take(): the copies of an edge staged and then written, and the jump to its target. `last` is
 * the edge after which the next instruction follows: no jump to it is needed. */
RXF_INLINE rxf_u32 compile_edge(rxf_cc *e, rxf_u64 at, rxf_u32 last) {
    rxf_u64 words;
    rxf_u32 status = edge_size(e, at, &words);
    if (status)
        return status;
    rxf_u64 count = e->code[at + 1], used = 0;
    const rxf_u64 *copy = e->code + at + RXF_EDGE_HEADER;
    for (rxf_u64 i = 0; i < count; i++, copy += RXF_EDGE_COPY_WORDS) {
        rxf_u64 width = copy[4], present = copy[2] < width ? copy[2] : width, object = 0;
        status = check(e, copy, present, &object);
        if (status)
            return status;
        if (width > RXF_GRAPH_COPY - used)
            return RXF_REFUSE_OUT_OF_RANGE;
        be_move(e, RXF_STAGE_AT + used, width, copy, object, present);
        used += width;
    }
    used = 0;
    copy = e->code + at + RXF_EDGE_HEADER;
    for (rxf_u64 i = 0; i < count; i++, copy += RXF_EDGE_COPY_WORDS) {
        if (!in_frame(e, copy[3], copy[4]))
            return RXF_REFUSE_OUT_OF_RANGE;
        be_slot_copy(e, copy[3], RXF_STAGE_AT + used, copy[4]);
        used += copy[4];
    }
    rxf_u64 target = e->code[at];
    rxf_u32 next = target < e->count ? e->index[target] : RXF_NO_INSTRUCTION;
    if (next == RXF_NO_INSTRUCTION)
        return RXF_REFUSE_UNSUPPORTED; /* a jump to where no instruction starts */
    if (!(last && next == e->current + 1)) {
        /* an edge that goes back is where a loop can be stopped: the state is whole here */
        if (next <= e->current && e->area && e->safe)
            be_safe_point(e);
        be_jump(e, next);
    }
    return RXF_OK;
}

/* The instruction at `pc`, written where the code is now. */
RXF_INLINE rxf_u32 compile_instruction(rxf_cc *e, rxf_u64 pc, rxf_u64 size) {
    const rxf_u64 *w = e->code + pc;
    rxf_u64 object = 0, other = 0;
    rxf_u32 status = RXF_OK;
    switch (w[0]) {
    case RXF_OP_CALL:
        return compile_call(e, pc);
    case RXF_OP_MOVE: {
        rxf_u64 have_bytes = w[5] < w[2] ? w[5] : w[2];
        if (!in_frame(e, w[1], w[2]))
            return RXF_REFUSE_OUT_OF_RANGE;
        status = check(e, w + 3, have_bytes, &object);
        if (!status)
            be_move(e, w[1], w[2], w + 3, object, have_bytes);
        return status;
    }
    case RXF_OP_MAP: {
        rxf_u64 have_bytes = w[5] < w[2] ? w[5] : w[2];
        if (!in_frame(e, w[1], w[2]) || !in_frame(e, w[1] + w[6], 4))
            return RXF_REFUSE_OUT_OF_RANGE;
        status = check(e, w + 3, have_bytes, &object);
        if (status)
            return status;
        be_move(e, w[1], w[2], w + 3, object, have_bytes);
        if (w[7]) {
            /* the first pair that has the status wins: the pairs from the last, each looking at
             * the status as it was */
            rxf_u64 field[RXF_OPERAND_WORDS];
            ((volatile rxf_u64 *)field)[0] = RXF_OPERAND_SLOT;
            ((volatile rxf_u64 *)field)[1] = w[1] + w[6];
            ((volatile rxf_u64 *)field)[2] = 4;
            status = value_check(e, field, 0, &other);
            if (status)
                return status;
            be_value(e, field, 0);
            for (rxf_u64 i = w[7]; i > 0; i--) {
                rxf_u64 from = w[8 + 2 * (i - 1)], to = w[9 + 2 * (i - 1)];
                if (from >> 32)
                    continue; /* a status is 32 bits: no match */
                rxf_u64 at = be_if_tag(e, from);
                rxf_u64 literal[RXF_OPERAND_WORDS];
                ((volatile rxf_u64 *)literal)[0] = RXF_OPERAND_LITERAL;
                ((volatile rxf_u64 *)literal)[1] = to & 0xFFFFFFFFU;
                ((volatile rxf_u64 *)literal)[2] = 4;
                be_move(e, w[1] + w[6], 4, literal, 0, 4);
                be_if_end(e, at);
            }
        }
        return RXF_OK;
    }
    case RXF_OP_FIELD: {
        if (w[7] > w[2] || !in_frame(e, w[1], w[2]))
            return RXF_REFUSE_OUT_OF_RANGE;
        if (w[6] >> 32) /* the sum below does not overflow */
            return RXF_REFUSE_OUT_OF_RANGE;
        status = check(e, w + 3, w[6] + w[7], &object);
        if (status)
            return status;
        /* the bytes from `offset`, as an operand of their own */
        rxf_u64 part[RXF_OPERAND_WORDS];
        ((volatile rxf_u64 *)part)[0] = w[3];
        ((volatile rxf_u64 *)part)[2] = w[7];
        if (w[3] == RXF_OPERAND_SLOT) {
            ((volatile rxf_u64 *)part)[1] = w[4] + w[6];
        } else if (w[3] == RXF_OPERAND_OBJECT) {
            ((volatile rxf_u64 *)part)[1] = w[4];
            object += w[6];
        } else {
            ((volatile rxf_u64 *)part)[1] = w[6] < 8 ? w[4] >> (8 * w[6]) : 0;
        }
        be_move(e, w[1], w[2], part, object, w[7]);
        return RXF_OK;
    }
    case RXF_OP_CONSTRUCT: {
        if (!in_frame(e, w[1], w[2]) || !in_frame(e, w[1] + w[3], 4) || w[9] > w[2] ||
            w[8] > w[2] - w[9])
            return RXF_REFUSE_OUT_OF_RANGE;
        rxf_u64 bytes = w[9] < w[7] ? w[9] : w[7];
        status = check(e, w + 5, bytes, &object);
        if (status)
            return status;
        be_zero(e, w[1], w[2]);
        rxf_u64 tag[RXF_OPERAND_WORDS];
        ((volatile rxf_u64 *)tag)[0] = RXF_OPERAND_LITERAL;
        ((volatile rxf_u64 *)tag)[1] = w[4] & 0xFFFFFFFFU;
        ((volatile rxf_u64 *)tag)[2] = 4;
        be_move(e, w[1] + w[3], 4, tag, 0, 4);
        be_move(e, w[1] + w[8], bytes, w + 5, object, bytes);
        return RXF_OK;
    }
    case RXF_OP_LESS:
        if (!in_frame(e, w[1], 1))
            return RXF_REFUSE_OUT_OF_RANGE;
        status = value_check(e, w + 2, 0, &object);
        if (!status)
            status = value_check(e, w + 5, 0, &other);
        if (!status)
            be_less(e, w[1], w + 2, object, w + 5, other);
        return status;
    case RXF_OP_INCREMENT:
        if (!in_frame(e, w[1], w[2]))
            return RXF_REFUSE_OUT_OF_RANGE;
        status = value_check(e, w + 3, 0, &object);
        if (status)
            return status;
        if (w[2] == 0)
            return RXF_OK;
        if ((w[2] != 1 && w[2] != 2 && w[2] != 4 && w[2] < 8) ||
            (w[1] & ((w[2] >= 8 ? 8 : w[2]) - 1)))
            return RXF_REFUSE_UNSUPPORTED;
        be_increment(e, w[1], w[2], w + 3, object);
        return RXF_OK;
    case RXF_OP_GOTO:
        return compile_edge(e, pc + 1, 1);
    case RXF_OP_BRANCH: {
        rxf_u64 first = pc + 5, words;
        status = value_check(e, w + 1, 0, &object);
        if (status)
            return status;
        status = edge_size(e, first, &words);
        if (status)
            return status;
        be_value(e, w + 1, object);
        /* the first edge is taken when (value == 0) == (mode == ZERO) */
        rxf_u64 at = be_if_zero(e, w[4] == RXF_MODE_ZERO);
        status = compile_edge(e, first, 0);
        if (status)
            return status;
        be_if_else(e, at);
        status = compile_edge(e, first + words, 1);
        be_if_else_end(e);
        return status;
    }
    case RXF_OP_SWITCH: {
        status = value_check(e, w + 1, 0, &object);
        if (status)
            return status;
        be_value(e, w + 1, object);
        rxf_u64 at = pc + 5 + w[4], words;
        for (rxf_u64 i = 0; i < w[4]; i++) {
            rxf_u64 patch = be_if_tag(e, w[5 + i]);
            status = compile_edge(e, at, 0);
            if (status)
                return status;
            be_if_end(e, patch);
            status = edge_size(e, at, &words);
            if (status)
                return status;
            at += words;
        }
        return compile_edge(e, at, 1); /* the default */
    }
    case RXF_OP_RETURN:
    case RXF_OP_REFUSE:
        return terminal(e, pc);
    default:
        (void)size;
        return RXF_REFUSE_UNSUPPORTED;
    }
}

/* One pass over the whole program: the prologue, the parameters, every instruction. */
RXF_INLINE rxf_u32 compile_pass(rxf_cc *e, rxf_u64 words, rxf_u64 parameters) {
    e->size = 0;
    e->nest = 0;
    e->failed = 0;
    be_begin(e, words);
    rxf_u64 used = 0;
    for (rxf_u64 i = 0; i < parameters; i++) {
        rxf_u64 slot = e->code[RXF_SLOT_WORDS + 2 * i], width = e->code[RXF_SLOT_WORDS + 2 * i + 1];
        if (!in_frame(e, slot, width) || width > words * 8 - used) {
            e->at = 0;
            return RXF_REFUSE_OUT_OF_RANGE;
        }
        be_param(e, slot, width, used);
        used += (width + 7) & ~(rxf_u64)7;
    }
    be_dispatch(e);
    rxf_u64 pc = e->code[4];
    for (rxf_u64 i = 0; i < e->instructions; i++) {
        rxf_u64 size = 0;
        e->current = i;
        e->offset[i] = (rxf_u32)e->size;
        instruction_size(e, pc, &size);
        be_enter(e);
        rxf_u32 status = compile_instruction(e, pc, size);
        if (status) {
            e->at = pc;
            return status;
        }
        pc += size;
    }
    be_tail(e);
    return RXF_OK;
}

RXF_FUNCTION rxf_u32 rxf_compile_program(rxf_runtime_context *c, void *request) {
    rxf_compile_request *r = (rxf_compile_request *)request;
    if (!valid(c) || !r || !r->program || !r->out)
        return RXF_REFUSE_UNBOUND;
    rxf_cc cc;
    rxf_cc *e = &cc;
    e->c = c;
    e->code = (const rxf_u64 *)(rxf_uptr)r->program;
    e->count = r->program_size / 8;
    e->buf = (rxf_u8 *)(rxf_uptr)r->out;
    e->cap = r->out_capacity;
    e->size = 0;
    e->failed = 0;
    e->write = 0;
    e->at = 0;
    e->instructions = 0;
    e->current = 0;
    e->nest = 0;
    e->area = channel_area(c);
    e->safe = e->area ? find_leaf(e, RXF_LEAF_SAFE_POINT) : 0;
    r->out_size = 0;
    r->at = 0;
    if (!have(e, 0, RXF_SLOT_WORDS) || e->code[0] != RXF_GRAPH_MAGIC)
        return RXF_REFUSE_TYPE_MISMATCH;
    e->frame = e->code[1];
    rxf_u64 parameters = e->code[2], words = e->code[3], entry_word = e->code[4];
    if (e->frame > RXF_GRAPH_FRAME || (e->frame & 7) || parameters > e->count ||
        !have(e, RXF_SLOT_WORDS, parameters * 2) || words > RXF_GRAPH_FRAME)
        return RXF_REFUSE_OUT_OF_RANGE;
    /* the tables of the passes: where each word starts an instruction, where each instruction is */
    if (!r->work || (r->work & 3) || r->work_capacity / 8 < e->count) {
        r->at = e->count * 8; /* the room that it needs */
        return RXF_REFUSE_OUT_OF_MEMORY;
    }
    e->index = (volatile rxf_u32 *)(rxf_uptr)r->work;
    e->offset = e->index + e->count;
    for (rxf_u64 i = 0; i < e->count; i++) {
        e->index[i] = RXF_NO_INSTRUCTION;
        e->offset[i] = 0;
    }
    rxf_u64 pc = entry_word;
    rxf_u32 status = RXF_OK;
    while (pc < e->count) {
        rxf_u64 size = 0;
        status = instruction_size(e, pc, &size);
        if (status)
            break;
        e->index[pc] = (rxf_u32)e->instructions++;
        pc += size;
    }
    if (!status && !e->instructions)
        status = RXF_REFUSE_OUT_OF_RANGE;
    if (status) {
        r->at = pc;
        return status;
    }
    status = compile_pass(e, words, parameters); /* counts, and places every instruction */
    if (!status) {
        r->out_size = e->size;
        if (e->size > e->cap)
            return RXF_REFUSE_OUT_OF_MEMORY;
        e->write = 1;
        status = compile_pass(e, words, parameters);
        if (!status && e->failed)
            return RXF_REFUSE_OUT_OF_MEMORY;
    }
    r->out_size = e->size;
    r->at = e->at;
    return status;
}
