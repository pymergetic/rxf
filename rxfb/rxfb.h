#ifndef RXFB_H
#define RXFB_H

/*
 * rxfb: the slim reader of an RXFB v7 image (docs/32), as a library.
 *
 * One freestanding C translation unit (rxfb.c) and this header. No libc, no allocation, no global
 * state: `rxfb_attach` validates the image and fills a small struct the caller owns (it lives on
 * the caller's stack), and every other call answers a VIEW onto the mapped bytes - a pointer into
 * the image, never a copy. There is nothing to release, so there is no deinit: dropping the struct
 * is the teardown.
 *
 * The image is read-only here and must stay mapped, unchanged, for as long as a view is in use. It
 * must be 8-byte aligned (an mmap'ed file, a malloc'ed buffer, a Python `bytes`) and it is
 * treated as untrusted: every offset, length and chain is bounds-checked, a corrupt image answers
 * RXFB_BAD_IMAGE or RXFB_BAD_HANDLE and never reads outside [bytes, bytes + len).
 *
 * The read path is the engine's own (src/pymergetic/rxftk/core/arena/object_arena.cpp: attach,
 * load, find, children, payload, symbol, path_of, spec, refs_of): the same walks over the same
 * pinned layout (rxfb_layout.h), the engine's logic with the writes taken out.
 *
 * A path is the dotted names below the root ("pymergetic.rxf.foundation.graph.run"); the root's
 * own name is the image, not a path segment, so its path is "". Names and paths are passed as a
 * pointer and a length (they need no NUL) and are compared byte for byte.
 */

#include "rxfb_layout.h"

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum rxfb_status {
    RXFB_OK = 0,
    RXFB_BAD_IMAGE = 1,  /* the bytes are not a usable v7 image (or a chain/offset is corrupt) */
    RXFB_BAD_HANDLE = 2, /* stale, out of range, or RXFB_HANDLE_NONE */
    RXFB_BAD_NAME = 3,   /* an empty name or path segment */
    RXFB_PROTECTED = 4,  /* the object index itself: its payload is a table, not a chain */
    RXFB_NO_ROOM = 5,    /* the caller's buffer is too small; *written is what it must offer */
    RXFB_NOT_FOUND = 6   /* a lookup that names nothing (the handle out-parameter is NONE) */
} rxfb_status;

/* An attached image. Plain data; copy it, keep it on the stack, share it between threads: a reader
 * never writes. The fields are the library's cache of where the anchors are, not an interface. */
typedef struct rxfb {
    const uint8_t *base;
    uint64_t size;
    const rxfb_superblock *super;
    const rxfb_index_head *index;
    rxfb_handle specs;     /* sys/specs, or RXFB_HANDLE_NONE (an image without semantic rows) */
    rxfb_handle relations; /* sys/relations, or RXFB_HANDLE_NONE (an image without refs) */
} rxfb;

/* A function's bytes for one machine: where it starts and how long it is. `entry` points into the
 * image (read-only, for a host to interpret, JIT or link); `code` and `blob` are the Code and
 * CodeBlob objects it came from. */
typedef struct rxfb_code {
    const uint8_t *entry;
    uint64_t length;
    rxfb_handle code;
    rxfb_handle blob;
} rxfb_code;

/* ---- the image ------------------------------------------------------------------------------ */

/* Validates the superblock (magic RXFA, version 1, committed/limit/size in order, the end marker
 * inside the bytes) and the object index (its anchor entry, the symbol table), and finds
 * sys/specs and sys/relations. Reads nothing else. */
rxfb_status rxfb_attach(rxfb *rx, const void *bytes, uint64_t length);

rxfb_handle rxfb_root(const rxfb *rx);
rxfb_handle rxfb_entry(const rxfb *rx); /* RXFB_HANDLE_NONE when the image has no entry */
uint32_t rxfb_count(const rxfb *rx);    /* the handles (index slots) the image holds */

/* ---- objects -------------------------------------------------------------------------------- */

/* The record a handle names, checked against its index entry and the record itself. */
rxfb_status rxfb_record(const rxfb *rx, rxfb_handle handle, const rxfb_object **record);

/* The payload of an object: `*bytes` points into the image, `*size` is the room (a multiple of 8;
 * a module's payload opens with its 8-byte chain head). */
rxfb_status rxfb_payload(const rxfb *rx, rxfb_handle handle, const uint8_t **bytes, uint32_t *size);

/* The text of an interned name. `text` is NUL-terminated inside the image. */
rxfb_status rxfb_symbol(const rxfb *rx, uint32_t id, const char **text, uint32_t *length);

/* The name of an object; an anonymous object answers *length == 0 and a NULL text. */
rxfb_status rxfb_name(const rxfb *rx, rxfb_handle handle, const char **text, uint32_t *length);

/* The semantic row of an object (sys/specs): its Type, flags word, order index and generation. */
rxfb_status rxfb_spec(const rxfb *rx, rxfb_handle handle, const rxfb_spec_row **row);

/* ---- the tree ------------------------------------------------------------------------------- */

/* The child of `parent` with this name; *handle is RXFB_HANDLE_NONE and the answer RXFB_NOT_FOUND
 * when the chain has none. */
rxfb_status rxfb_find(const rxfb *rx, rxfb_handle parent, const char *name, uint32_t length,
                      rxfb_handle *handle);

/* A dotted path from the root. The empty path is the root. */
rxfb_status rxfb_lookup(const rxfb *rx, const char *path, uint32_t length, rxfb_handle *handle);

/* The path of an object, written into `out` (no NUL). *written is the length; when it is more
 * than `capacity` nothing is written and the answer is RXFB_NO_ROOM. */
rxfb_status rxfb_path(const rxfb *rx, rxfb_handle handle, char *out, uint32_t capacity,
                      uint32_t *written);

/* The children of an object in chain order, as an iterator that needs no memory: the first child
 * (RXFB_HANDLE_NONE for a leaf or an empty module) and the sibling after a child. */
rxfb_status rxfb_first_child(const rxfb *rx, rxfb_handle parent, rxfb_handle *child);
rxfb_status rxfb_next_sibling(const rxfb *rx, rxfb_handle child, rxfb_handle *next);

/* The same walk into a caller's array. *written is the number of children; when it is more than
 * `capacity` (or `out` is NULL) the answer is RXFB_NO_ROOM and the count is what it takes. */
rxfb_status rxfb_children(const rxfb *rx, rxfb_handle handle, rxfb_handle *out, uint32_t capacity,
                          uint32_t *written);

/* ---- references ----------------------------------------------------------------------------- */

/* The reference rows of an object (its table in sys/relations): `*rows` points at them inside the
 * image (8-byte aligned, `*count` rows of 24 bytes). An object with no table has none. */
rxfb_status rxfb_refs(const rxfb *rx, rxfb_handle handle, const rxfb_ref **rows, uint32_t *count);

/* The same rows, read straight off a table object of sys/relations (its payload, padding trimmed):
 * the way to walk every table of an image once, where `rxfb_refs` finds one object's table by
 * walking the chain. Any other object is RXFB_BAD_HANDLE. */
rxfb_status rxfb_table(const rxfb *rx, rxfb_handle table, const rxfb_ref **rows, uint32_t *count);

/* ---- the code story ------------------------------------------------------------------------- */

/* The interpreter whose Leaf references are the function slots of a linked image. */
#define RXFB_RUN_PATH "pymergetic.rxf.foundation.graph.run"

/* The bytes of a Function for a machine: its child Code named `machine` (NUL-terminated, e.g.
 * "x86_64_linux", "aarch64_linux", "wasm32"), resolved through the Code's `body` into the
 * CodeBlob's bytes. RXFB_NOT_FOUND when the function has no code for that machine. */
rxfb_status rxfb_code_of(const rxfb *rx, rxfb_handle function, const char *machine,
                         rxfb_code *code);

/* Function slot `slot` of the interpreter `run`: the Leaf reference with that number, resolved to
 * its Function and from there to the bytes for `machine` - the resolution the seats link by. */
rxfb_status rxfb_slot_of(const rxfb *rx, rxfb_handle run, const char *machine, uint32_t slot,
                         rxfb_code *code);

/* The same for the image's own interpreter (RXFB_RUN_PATH). */
rxfb_status rxfb_slot(const rxfb *rx, const char *machine, uint32_t slot, rxfb_code *code);

#ifdef __cplusplus
}
#endif

#endif
