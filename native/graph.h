#ifndef RXF_GRAPH_H
#define RXF_GRAPH_H

#include "stdlib_runtime.h"

/*
 * A composed function: the semantic graph of a Function as a program of 64-bit
 * words, run by rxf_graph_run. The program is the same for every target; the
 * interpreter is native code of each target, and the leaves it calls are the
 * native Functions of the starter, found by their id in the function table of
 * the runtime context. tools/native/compose.py writes programs and reads the
 * numbers of this header; it is the one place that defines them.
 *
 * Program (words):
 *   0 magic, 1 frame bytes, 2 parameter count, 3 argument words, 4 entry word
 *   5.. one (slot, width) per parameter
 *   then the instructions; every jump is a word index.
 *
 * Operand (3 words): kind, a, b
 *   SLOT    a = byte offset in the frame, b = width
 *   LITERAL a = the value (at most 8 bytes), b = width
 *   OBJECT  a = durable object id, b = width; the value is the payload address
 *
 * Edge: target, n, then n copies of (operand, destination slot, width). All
 * sources are read before any destination is written.
 *
 *   CALL      callee, n, out slot, out width, status slot, [program operand,] n operands
 *   MOVE      dst, width, src
 *   MAP       dst, width, src, offset, n, n pairs (from, to) of status codes
 *   FIELD     dst, width, src, offset, size
 *   CONSTRUCT dst, width, tag offset, tag, src, payload offset, payload size
 *   LESS      dst, a, b
 *   INCREMENT dst, width, src
 *   GOTO      edge
 *   BRANCH    condition, mode, edge when taken, edge when not
 *             mode ZERO takes the first edge on 0 (a status), NONZERO on not 0
 *   SWITCH    condition, n, n tags, n + 1 edges (the last is the default)
 *   RETURN    src, width: written to the output (at least 8 bytes)
 *   REFUSE    src: the status the run returns
 *
 * A CALL names its callee one of two ways, and both are the same call step:
 *
 *   - a leaf id (1..48, the function table of the context), then n operands; or
 *   - RXF_GRAPH_CALL (word 1 is 0: there is no leaf 0), then one program operand
 *     (OBJECT: a = the object id the callee's program words are bound at, b = 0)
 *     and n argument operands.  The callee is a program of its own: its parameters
 *     are the call's arguments as words of the caller's frame (a scalar one word, a
 *     handle its three words -- the shapes run-entry ARGS takes), bound into the
 *     callee's parameter slots when the call is made, and its locals live in its
 *     own frame words of the run cell.  Nothing but words and handles crosses the
 *     boundary: no raw pointer outlives a step (S7, docs/20).
 *
 * The run cell carries the frames: one RXF_GRAPH_FRAME region per level of depth,
 * at most RXF_GRAPH_CALL_DEPTH of them (the depth budget -- a call past it is
 * refused RXF_REFUSE_CALL_DEPTH, the Refuse-on-budget shape).  The run's
 * interpreter iterates over a bounded stack of frame links -- (return program,
 * return word offset, the caller's out and status slots) -- so depth costs words
 * of the cell, never native stack.  A call boundary is between two instructions,
 * so it is a safe point like any other (S7, S11: mp_handle_pending lowers here).
 *
 * The callee's ending answers the call the way a leaf answers one: RETURN copies
 * its out word to the caller-named destination word (a handle returns as its three
 * words in a wide out slot, or the caller passes an Object and the callee writes
 * through it) and REFUSE gives the call its status; both write the status to the
 * call's status slot and the caller goes on after the call step.  A refusal with
 * no status slot to catch it ends the run with it.  At depth 0 (no call above)
 * RETURN writes the run's output and ends the run, as it always did.
 */

#define RXF_GRAPH_MAGIC 0x31475852ULL
#define RXF_GRAPH_FRAME 1024U
#define RXF_GRAPH_COPY 256U
#define RXF_GRAPH_NONE (~0ULL)
/* Word 1 of a CALL that names a program rather than a leaf: 0, the id no leaf has. */
#define RXF_GRAPH_CALL 0ULL
/* The depth budget of a run: the run cell carries one RXF_GRAPH_FRAME region per level
 * of nesting, at most this many, and a call past the last level is refused
 * RXF_REFUSE_CALL_DEPTH. The budget bounds the frame words a run may spend and the
 * frame links the interpreter iterates over; depth never costs native stack. */
#define RXF_GRAPH_CALL_DEPTH 8U

/* The ids of the capabilities of the runtime context (words 6 and 7 of it: how many, and the table
 * of id, generation, address). */
#define RXF_CAPABILITY_CHANNEL 1ULL
/* The Uart capability: its address is the base of the device, and its generation word says what
 * device it is: the kind in the low byte (0 or RXF_UART_DEVICE_MACHINE: the one of the machine, the
 * 16550 behind I/O ports on x86_64 and the PL011 in memory on aarch64), the distance of its
 * registers as a shift (bits 8 to 11: register n is at base + (n << shift)), and RXF_UART_WIDE when
 * a register is read and written as a 32-bit word (the byte is the low one). The 16550 in memory
 * and the PL011 in memory are the same on both machines; the I/O ports exist on x86_64 only (the
 * capability is then unbound on aarch64). */
#define RXF_UART_DEVICE_MACHINE 1ULL
#define RXF_UART_DEVICE_NS16550_MMIO 2ULL
#define RXF_UART_DEVICE_PL011_MMIO 3ULL
#define RXF_UART_DEVICE_NS16550_PORT 4ULL
#define RXF_UART_SHIFT_AT 8U
#define RXF_UART_WIDE 4096ULL
/* On wasm32 there is no port and no device to trap a load: the address is that of a block of linear
 * memory with two FIFOs, and the host is the line (the generation is not looked at). The block is
 * 32-bit words `rx_head` (the host writes), `rx_tail` (the image), `tx_head` (the image), `tx_tail`
 * (the host), then at RXF_UART_FIFO_RX and RXF_UART_FIFO_TX the bytes of the two FIFOs of
 * RXF_UART_FIFO_BYTES each. */
#define RXF_CAPABILITY_UART 2ULL
#define RXF_UART_FIFO_BYTES 256U
#define RXF_UART_FIFO_RX 64U
#define RXF_UART_FIFO_TX 320U
#define RXF_UART_FIFO_BLOCK 576U

enum rxf_graph_operand { RXF_OPERAND_SLOT = 0, RXF_OPERAND_LITERAL = 1, RXF_OPERAND_OBJECT = 2 };

enum rxf_graph_mode { RXF_MODE_ZERO = 0, RXF_MODE_NONZERO = 1 };

enum rxf_graph_opcode {
    RXF_OP_CALL = 1,
    RXF_OP_MOVE = 2,
    RXF_OP_MAP = 3,
    RXF_OP_FIELD = 4,
    RXF_OP_CONSTRUCT = 5,
    RXF_OP_LESS = 6,
    RXF_OP_INCREMENT = 7,
    RXF_OP_GOTO = 8,
    RXF_OP_BRANCH = 9,
    RXF_OP_SWITCH = 10,
    RXF_OP_RETURN = 11,
    RXF_OP_REFUSE = 12
};

/* There is no select opcode, and none should be added: a select is the two edges of one BRANCH
 * that both take the same join, each edge copying its own value into the same slot. The copies of
 * an edge are already the phi of a block parameter, so the format grows nothing for it and none of
 * the readers of the format (this interpreter, the compiler of native/compile.c, the compiler that
 * is a program of native/tiny) grows a dispatch for it. The branch shape takes 19 words where an
 * opcode of its own would take 12; words of a program are object bytes, dispatches are forever.
 * tools/native/rximport.py lowers a select to those two edges, and its subset contract says so. */

/* The ids of the leaves in the function table: one per native Function a graph calls. */
enum rxf_graph_leaf {
    RXF_LEAF_WRAPPING_ADD_UINT64_T = 1,
    RXF_LEAF_LESS_UINT64_T = 2,
    RXF_LEAF_BIT_NOT_UINT64_T = 3,
    RXF_LEAF_HASH_U64 = 4,
    RXF_LEAF_RUNTIME_HASH_MAP_LOOKUP = 5,
    RXF_LEAF_RUNTIME_HASH_SET_LOOKUP = 6,
    RXF_LEAF_RUNTIME_BEGIN_PRIVATE = 7,
    RXF_LEAF_RUNTIME_RESOLVE = 8,
    RXF_LEAF_RUNTIME_ALLOCATE_PREPARE = 9,
    RXF_LEAF_RUNTIME_PUBLISH = 10,
    RXF_LEAF_RUNTIME_ROLLBACK = 11,
    RXF_LEAF_RUNTIME_BORROW = 12,
    RXF_LEAF_RUNTIME_RELEASE_BORROW = 13,
    RXF_LEAF_RUNTIME_PIN = 14,
    RXF_LEAF_RUNTIME_UNPIN = 15,
    RXF_LEAF_RUNTIME_READ = 16,
    RXF_LEAF_RUNTIME_WRITE = 17,
    RXF_LEAF_RUNTIME_APPEND = 18,
    RXF_LEAF_RUNTIME_FORMAT_U32 = 19,
    RXF_LEAF_RUNTIME_SEARCH = 20,
    RXF_LEAF_RUNTIME_CLEANUP = 21,
    RXF_LEAF_CHECKED_ADD_UINT32_T = 22,
    RXF_LEAF_CHECKED_SUBTRACT_UINT32_T = 23,
    RXF_LEAF_CHECKED_MULTIPLY_UINT32_T = 24,
    RXF_LEAF_DIVIDE_UINT32_T = 25,
    RXF_LEAF_MEMORY_COPY = 26,
    RXF_LEAF_MEMORY_COMPARE = 27,
    RXF_LEAF_MEMORY_FILL = 28,
    RXF_LEAF_LIVE_PUMP = 29,
    RXF_LEAF_UART_PUMP = 30,
    RXF_LEAF_COMPILE_PROGRAM = 31,
    /* what a program needs to read and build words and code: the numeric Functions of 64 bits */
    RXF_LEAF_WRAPPING_SUBTRACT_UINT64_T = 32,
    RXF_LEAF_BIT_AND_UINT64_T = 33,
    RXF_LEAF_BIT_OR_UINT64_T = 34,
    RXF_LEAF_SHIFT_LEFT_UINT64_T = 35,
    RXF_LEAF_SHIFT_RIGHT_UINT64_T = 36,
    RXF_LEAF_EQUAL_UINT64_T = 37,
    /* the safe point of compiled code at an edge that goes back (native/safe.c) */
    RXF_LEAF_SAFE_POINT = 38,

    /* The integer leaves of the flat profile: multiply, divide and remainder, both kinds of
     * integer. Their refusals are the ones of native/basics.c: a division by zero refuses
     * DIVISION_BY_ZERO; the signed forms refuse OVERFLOW where the C they are written with would
     * not be defined (INT_MIN / -1, and INT_MIN % -1 which is written with the same division);
     * the checked multiplies refuse OVERFLOW where the product leaves the type. The with.overflow
     * shapes of llvm.umul/smul are one checked_multiply whose status is the overflow bit; the
     * uadd/sadd/usub/ssub shapes are a wrapping add or subtract and the carry or sign compare,
     * which is how tools/native/rximport.py lowers them. */
    RXF_LEAF_WRAPPING_MULTIPLY_UINT64_T = 39,
    RXF_LEAF_DIVIDE_UINT64_T = 40,
    RXF_LEAF_REMAINDER_UINT64_T = 41,
    RXF_LEAF_DIVIDE_INT64_T = 42,
    RXF_LEAF_REMAINDER_INT64_T = 43,
    RXF_LEAF_CHECKED_MULTIPLY_UINT64_T = 44,
    RXF_LEAF_CHECKED_MULTIPLY_INT64_T = 45,
    RXF_LEAF_REMAINDER_UINT32_T = 46,
    RXF_LEAF_DIVIDE_INT32_T = 47,
    RXF_LEAF_REMAINDER_INT32_T = 48
};

#ifndef RXF_INLINE
#define RXF_INLINE static __attribute__((always_inline)) inline
#endif

/* The refusal of the compiler (native/compile.c) for what it leaves to the interpreter. */
#define RXF_REFUSE_UNSUPPORTED 16U
/* The refusal of a run whose call depth is spent (the depth budget of RXF_GRAPH_CALL_DEPTH). */
#define RXF_REFUSE_CALL_DEPTH 17U

/* How a leaf is called: its parameters after the context, as letters. */
enum rxf_shape {
    RXF_SHAPE_NONE = 0,
    RXF_SHAPE_QO,      /* u64, out */
    RXF_SHAPE_QQO,     /* u64, u64, out */
    RXF_SHAPE_HPQQO,   /* handle, pointer, u64, u64, out */
    RXF_SHAPE_HQO,     /* handle, u64, out */
    RXF_SHAPE_QQQQQPO, /* five u64, pointer, out */
    RXF_SHAPE_P,       /* pointer */
    RXF_SHAPE_HQ,      /* handle, u64 */
    RXF_SHAPE_H,       /* handle */
    RXF_SHAPE_HPQQ,    /* handle, pointer, u64, u64 */
    RXF_SHAPE_HPQQQ,   /* handle, pointer, u64, u64, u64 */
    RXF_SHAPE_HPW,     /* handle, pointer, u32 */
    RXF_SHAPE_HQHQPO,  /* handle, u64, handle, u64, pointer, out */
    RXF_SHAPE_WWO,     /* u32, u32, out */
    RXF_SHAPE_PQPQQ,   /* pointer, u64, pointer, u64, u64 */
    RXF_SHAPE_PQPQQO,  /* pointer, u64, pointer, u64, u64, out */
    RXF_SHAPE_PQBQ,    /* pointer, u64, u8, u64 */
    RXF_SHAPE_QWO      /* u64, u32, out */
};

RXF_INLINE rxf_u32 shape_of(rxf_u64 leaf) {
    switch (leaf) {
    case RXF_LEAF_BIT_NOT_UINT64_T:
        return RXF_SHAPE_QO;
    case RXF_LEAF_WRAPPING_ADD_UINT64_T:
    case RXF_LEAF_LESS_UINT64_T:
    case RXF_LEAF_HASH_U64:
    case RXF_LEAF_WRAPPING_SUBTRACT_UINT64_T:
    case RXF_LEAF_BIT_AND_UINT64_T:
    case RXF_LEAF_BIT_OR_UINT64_T:
    case RXF_LEAF_EQUAL_UINT64_T:
    case RXF_LEAF_WRAPPING_MULTIPLY_UINT64_T:
    case RXF_LEAF_DIVIDE_UINT64_T:
    case RXF_LEAF_REMAINDER_UINT64_T:
    case RXF_LEAF_DIVIDE_INT64_T:
    case RXF_LEAF_REMAINDER_INT64_T:
    case RXF_LEAF_CHECKED_MULTIPLY_UINT64_T:
    case RXF_LEAF_CHECKED_MULTIPLY_INT64_T:
        return RXF_SHAPE_QQO;
    case RXF_LEAF_SHIFT_LEFT_UINT64_T:
    case RXF_LEAF_SHIFT_RIGHT_UINT64_T:
        return RXF_SHAPE_QWO;
    case RXF_LEAF_RUNTIME_HASH_MAP_LOOKUP:
    case RXF_LEAF_RUNTIME_HASH_SET_LOOKUP:
    case RXF_LEAF_RUNTIME_READ:
        return RXF_SHAPE_HPQQO;
    case RXF_LEAF_RUNTIME_BEGIN_PRIVATE:
    case RXF_LEAF_RUNTIME_RESOLVE:
        return RXF_SHAPE_HQO;
    case RXF_LEAF_RUNTIME_ALLOCATE_PREPARE:
        return RXF_SHAPE_QQQQQPO;
    case RXF_LEAF_RUNTIME_PUBLISH:
    case RXF_LEAF_RUNTIME_ROLLBACK:
    case RXF_LEAF_RUNTIME_CLEANUP:
    case RXF_LEAF_LIVE_PUMP:
    case RXF_LEAF_UART_PUMP:
    case RXF_LEAF_SAFE_POINT:
        return RXF_SHAPE_P;
    case RXF_LEAF_RUNTIME_BORROW:
    case RXF_LEAF_RUNTIME_RELEASE_BORROW:
        return RXF_SHAPE_HQ;
    case RXF_LEAF_RUNTIME_PIN:
    case RXF_LEAF_RUNTIME_UNPIN:
        return RXF_SHAPE_H;
    case RXF_LEAF_RUNTIME_APPEND:
        return RXF_SHAPE_HPQQ;
    case RXF_LEAF_RUNTIME_WRITE:
        return RXF_SHAPE_HPQQQ;
    case RXF_LEAF_RUNTIME_FORMAT_U32:
        return RXF_SHAPE_HPW;
    case RXF_LEAF_RUNTIME_SEARCH:
        return RXF_SHAPE_HQHQPO;
    case RXF_LEAF_CHECKED_ADD_UINT32_T:
    case RXF_LEAF_CHECKED_SUBTRACT_UINT32_T:
    case RXF_LEAF_CHECKED_MULTIPLY_UINT32_T:
    case RXF_LEAF_DIVIDE_UINT32_T:
    case RXF_LEAF_REMAINDER_UINT32_T:
    case RXF_LEAF_DIVIDE_INT32_T:
    case RXF_LEAF_REMAINDER_INT32_T:
        return RXF_SHAPE_WWO;
    case RXF_LEAF_MEMORY_COPY:
        return RXF_SHAPE_PQPQQ;
    case RXF_LEAF_MEMORY_COMPARE:
        return RXF_SHAPE_PQPQQO;
    case RXF_LEAF_MEMORY_FILL:
        return RXF_SHAPE_PQBQ;
    default:
        return RXF_SHAPE_NONE;
    }
}

/* The operands of a shape, which of them are handles (bit n), and whether it has an out. */
RXF_INLINE rxf_u32 shape_layout(rxf_u32 shape, rxf_u32 *handles, rxf_u32 *has_out) {
    *handles = 0;
    *has_out = 1;
    switch (shape) {
    case RXF_SHAPE_QO:
        return 1;
    case RXF_SHAPE_QQO:
        return 2;
    case RXF_SHAPE_HPQQO:
        *handles = 1;
        return 4;
    case RXF_SHAPE_HQO:
        *handles = 1;
        return 2;
    case RXF_SHAPE_QQQQQPO:
        return 6;
    case RXF_SHAPE_P:
        *has_out = 0;
        return 1;
    case RXF_SHAPE_HQ:
        *handles = 1;
        *has_out = 0;
        return 2;
    case RXF_SHAPE_H:
        *handles = 1;
        *has_out = 0;
        return 1;
    case RXF_SHAPE_HPQQ:
        *handles = 1;
        *has_out = 0;
        return 4;
    case RXF_SHAPE_HPQQQ:
        *handles = 1;
        *has_out = 0;
        return 5;
    case RXF_SHAPE_HPW:
        *handles = 1;
        *has_out = 0;
        return 3;
    case RXF_SHAPE_HQHQPO:
        *handles = 5;
        return 5;
    case RXF_SHAPE_WWO:
    case RXF_SHAPE_QWO:
        return 2;
    case RXF_SHAPE_PQPQQ:
        *has_out = 0;
        return 5;
    case RXF_SHAPE_PQPQQO:
        return 5;
    case RXF_SHAPE_PQBQ:
        *has_out = 0;
        return 4;
    default:
        return 0;
    }
}

/*
 * Runs `program` (the handle of an object that holds it) on the arguments at
 * `args`: `argc` words, each parameter padded to whole words. The value of the
 * function is written to `out`; the status is 0, a refusal of a leaf, or of the
 * run itself (the program or a binding is not usable).
 */
rxf_u32 rxf_graph_run(rxf_runtime_context *, rxf_object_handle program, rxf_u64 args, rxf_u64 argc,
                      rxf_u64 *out);

#endif
