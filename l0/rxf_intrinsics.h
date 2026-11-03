/* L0 intrinsics: the minimal instruction set every platform must provide (docs/37, docs/38).
 *
 * Nothing above these is code; everything else is composed. The list is derived from what the
 * three big programs measure (live 7.1k lines: 679 loads, 469 stores, 119 while; compile 3.5k:
 * 227/208; graph 1.4k: 150/36) plus what the object model needs (docs/32) and the belt model
 * (docs/38 10). One opcode, one meaning; a platform implements it once, a backend lowers it,
 * an FPGA stage is one of these.
 *
 * Layout only, freestanding C11. No behaviour: the interpreter of the platform gives each opcode
 * its C case, and that case is the definition of its semantics.
 */
#ifndef PYMERGETIC_RXF_L0_INTRINSICS_H
#define PYMERGETIC_RXF_L0_INTRINSICS_H

#ifdef __cplusplus
extern "C" {
#endif

/* The width of a memory intrinsic: bits, so load/store/fill name one opcode per width. */
#define PYMERGETIC_RXF_W8 0u
#define PYMERGETIC_RXF_W16 1u
#define PYMERGETIC_RXF_W32 2u
#define PYMERGETIC_RXF_W64 3u

/* The shift a platform must guard: a count >= width yields 0 (sar: the sign), never the raw
 * hardware behaviour (x86 masks to 63). This is the whole shift contract (docs/29 table). */
#define PYMERGETIC_RXF_SHIFT_GUARD(width_bits) ((width_bits) >= 64 ? 64 : (width_bits))

enum {
    /* ---- arithmetic (wrapping; the refusing forms are composed, not intrinsic) ---- */
    PYMERGETIC_RXF_I_ADD = 1, /* a + b, wrapping */
    PYMERGETIC_RXF_I_SUB = 2,
    PYMERGETIC_RXF_I_NEG = 3,
    PYMERGETIC_RXF_I_MUL = 4,
    PYMERGETIC_RXF_I_DIVU = 5, /* refusal on zero: 15, division by zero */
    PYMERGETIC_RXF_I_REMU = 6,
    PYMERGETIC_RXF_I_DIVS = 7,
    PYMERGETIC_RXF_I_REMS = 8,

    /* ---- bits ---- */
    PYMERGETIC_RXF_I_AND = 9,
    PYMERGETIC_RXF_I_OR = 10,
    PYMERGETIC_RXF_I_XOR = 11,
    PYMERGETIC_RXF_I_NOT = 12,
    PYMERGETIC_RXF_I_SHL = 13, /* count >= width: 0 */
    PYMERGETIC_RXF_I_SHR = 14, /* logical, count >= width: 0 */
    PYMERGETIC_RXF_I_SAR = 15, /* arithmetic, count >= width: the sign */

    /* ---- compare (unsigned; the signed forms are composed) ---- */
    PYMERGETIC_RXF_I_EQ = 16,
    PYMERGETIC_RXF_I_NE = 17,
    PYMERGETIC_RXF_I_LTU = 18,
    PYMERGETIC_RXF_I_LEU = 19,
    PYMERGETIC_RXF_I_GTU = 20,
    PYMERGETIC_RXF_I_GEU = 21,
    PYMERGETIC_RXF_I_LTS = 22,
    PYMERGETIC_RXF_I_LES = 23,
    PYMERGETIC_RXF_I_GTS = 24,
    PYMERGETIC_RXF_I_GES = 25,

    /* ---- width (conversion) ---- */
    PYMERGETIC_RXF_I_ZEXT = 26,  /* zero extend */
    PYMERGETIC_RXF_I_SEXT = 27,  /* sign extend */
    PYMERGETIC_RXF_I_TRUNC = 28, /* take the low bits */

    /* ---- memory (unsafe; raw addresses live only here) ---- */
    PYMERGETIC_RXF_I_LOAD = 29,  /* width: zero extending */
    PYMERGETIC_RXF_I_STORE = 30, /* width: the low bytes */
    PYMERGETIC_RXF_I_COPY = 31,  /* n bytes, overlap defined as memmove */
    PYMERGETIC_RXF_I_FILL = 32,  /* n bytes, one value */

    /* ---- objects (the arena is reached only through these) ---- */
    PYMERGETIC_RXF_I_ALLOCATE = 33, /* kind, name, parent, payload -> handle */
    PYMERGETIC_RXF_I_RELEASE = 34,  /* handle -> the count drops */
    PYMERGETIC_RXF_I_RESOLVE = 35,  /* path -> handle */
    PYMERGETIC_RXF_I_GET = 36,      /* handle, field -> value */
    PYMERGETIC_RXF_I_SET = 37,      /* handle, field, value */
    PYMERGETIC_RXF_I_PIN = 38,      /* keep this object where it is (docs/23 4) */
    PYMERGETIC_RXF_I_UNPIN = 39,

    /* ---- calls ---- */
    PYMERGETIC_RXF_I_CALL = 40,     /* function handle, arguments */
    PYMERGETIC_RXF_I_TAILCALL = 41, /* reuse the caller's instance (docs/38 7) */

    /* ---- belts (docs/38 10) ---- */
    PYMERGETIC_RXF_I_PUT = 42,  /* belt, item */
    PYMERGETIC_RXF_I_TAKE = 43, /* belt -> item (parks when empty) */
    PYMERGETIC_RXF_I_WAKE = 44, /* something a parked call waits for arrived */

    /* ---- platform ---- */
    PYMERGETIC_RXF_I_OUT = 45,   /* one byte to the character output */
    PYMERGETIC_RXF_I_FENCE = 46, /* order what other carriers see */
    PYMERGETIC_RXF_I_CLOCK = 47, /* the tick of the seat */

    /* ---- control (not an opcode a backend lowers alone: the graph holds it) ---- */
    PYMERGETIC_RXF_I_REFUSE = 48 /* the status of a refusal; the graph's edges do the rest */
};

#ifdef __cplusplus
}
#endif
#endif /* PYMERGETIC_RXF_L0_INTRINSICS_H */
