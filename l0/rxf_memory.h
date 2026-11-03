/* L0 memory: the one arena, as the smallest building block (docs/32 is the specification).
 *
 * One linear region. The only fixed structure is the superblock at byte 0; everything after it
 * is objects. This header defines the layout only: sizes, offsets, kinds - no function, no
 * behaviour. It is freestanding C11; it pulls in nothing.
 *
 * Every constant here is checked against the running system by tests/test_l0_layout.py
 * (GROUND_ASSERT below freezes what the compiler sees).
 */
#ifndef PYMERGETIC_RXF_L0_MEMORY_H
#define PYMERGETIC_RXF_L0_MEMORY_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- the region ---------------------------------------------------------- */

#define PYMERGETIC_RXF_PAGE 4096u

/* ---- the superblock (docs/32 1.1) ----------------------------------------- */

#define PYMERGETIC_RXF_MAGIC 1095129170u /* "RXFA" as a little-endian u32 */
#define PYMERGETIC_RXF_VERSION 1u
#define PYMERGETIC_RXF_SUPERBLOCK_SIZE 4096u

#define PYMERGETIC_RXF_SUPERBLOCK_OFF_MAGIC 0u     /* u32 */
#define PYMERGETIC_RXF_SUPERBLOCK_OFF_VERSION 4u   /* u32 */
#define PYMERGETIC_RXF_SUPERBLOCK_OFF_COMMITTED 8u /* u64: the first free offset */
#define PYMERGETIC_RXF_SUPERBLOCK_OFF_LIMIT 16u    /* u64: the claimed end */
#define PYMERGETIC_RXF_SUPERBLOCK_OFF_ROOT 24u     /* u32: handle32 of the root object */
#define PYMERGETIC_RXF_SUPERBLOCK_OFF_ENTRY 28u    /* u32: handle32 of the index object */

/* ---- the handle ----------------------------------------------------------- */

/* A handle32 packs the index (20 bits) and the generation (12 bits). Index 0 with generation 0
 * is no object at all. */
#define PYMERGETIC_RXF_HANDLE32_NONE 0u
#define PYMERGETIC_RXF_HANDLE_INDEX_BITS 20
#define PYMERGETIC_RXF_HANDLE_GEN_BITS 12
#define PYMERGETIC_RXF_HANDLE_INDEX_MAX 1048575u
#define PYMERGETIC_RXF_HANDLE_GEN_MAX 4095u

/* ---- the object record (docs/32 1.2) --------------------------------------- */

/* Every object in the arena is one record followed inline by its payload, 8-byte aligned. */
#define PYMERGETIC_RXF_RECORD_SIZE 24u

#define PYMERGETIC_RXF_RECORD_OFF_KIND 0u     /* u16, PYMERGETIC_RXF_KIND_* */
#define PYMERGETIC_RXF_RECORD_OFF_GEN 2u      /* u16 */
#define PYMERGETIC_RXF_RECORD_OFF_FLAGS 4u    /* u16 */
#define PYMERGETIC_RXF_RECORD_OFF_NAME 6u     /* u16: a symbol id, 0 is anonymous */
#define PYMERGETIC_RXF_RECORD_OFF_PARENT 8u   /* u32: handle32 */
#define PYMERGETIC_RXF_RECORD_OFF_LINK 12u    /* u32: handle32, the next sibling */
#define PYMERGETIC_RXF_RECORD_OFF_REFS 16u    /* u32 */
#define PYMERGETIC_RXF_RECORD_OFF_PAYLOAD 20u /* u32: payload bytes, room follows inline */

/* A FREE span is a record with kind FREE and 16 link bytes behind its header. */
#define PYMERGETIC_RXF_FREE_LINKS 16u

/* ---- the kinds (an object's own record; the type row of docs/32 5) ---------- */

enum {
    PYMERGETIC_RXF_KIND_NONE = 0,
    PYMERGETIC_RXF_KIND_MODULE = 1,
    PYMERGETIC_RXF_KIND_FUNC = 2,
    PYMERGETIC_RXF_KIND_HASH = 3,
    PYMERGETIC_RXF_KIND_SPEC = 4,
    PYMERGETIC_RXF_KIND_SYM = 5,
    PYMERGETIC_RXF_KIND_BLOB = 6,
    PYMERGETIC_RXF_KIND_FRAME = 7,
    PYMERGETIC_RXF_KIND_JOURNAL = 8,
    /* 9..253 reserved for the language's kinds (a Module, an Import, a Type, a Field,
     * a Function, a Parameter, a Signature, a Graph, a Block, an Operation, a Value,
     * a Belt, a Stage, a Doc: docs/38 names them as they are decided). */
    PYMERGETIC_RXF_KIND_DEAD = 254, /* a freed index entry keeps its generation */
    PYMERGETIC_RXF_KIND_BLOCK = 255,
    PYMERGETIC_RXF_KIND_FREE = 65535
};

/* ---- the index (the first object, at offset 4096; docs/32 1) ---------------- */

/* The index object's payload is the slot head then the entries. */
#define PYMERGETIC_RXF_INDEX_OFF_COUNT 24u    /* u32, behind the record */
#define PYMERGETIC_RXF_INDEX_OFF_CAPACITY 28u /* u32 */
#define PYMERGETIC_RXF_INDEX_ENTRIES_AT 32u   /* behind the record head */

#define PYMERGETIC_RXF_INDEX_ENTRY_SIZE 16u
#define PYMERGETIC_RXF_INDEX_ENTRY_OFF_KIND 0u   /* u16 */
#define PYMERGETIC_RXF_INDEX_ENTRY_OFF_GEN 2u    /* u16 */
#define PYMERGETIC_RXF_INDEX_ENTRY_OFF_SLOT 4u   /* u32, reserved */
#define PYMERGETIC_RXF_INDEX_ENTRY_OFF_OFFSET 8u /* u64: the record's offset, 0 = dead */

/* A module's payload opens with the arena's chain head (docs/32 1.2): the value of a module
 * starts behind it. */
#define PYMERGETIC_RXF_MODULE_HEAD 8u

#ifdef __cplusplus
}
#endif
#endif /* PYMERGETIC_RXF_L0_MEMORY_H */
