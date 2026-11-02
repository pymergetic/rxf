/*
 * rxfb_layout.h is the library's copy of the v7 part of the engine's ground.h. This file compiles
 * both and fails when they differ in a size, an offset, a constant or an enumerator, so the copy
 * can never drift from the layout the engine writes.
 *
 * The assertions are checked by the compiler (-fsyntax-only); there is nothing to run. Both
 * headers compile as C11 and as C++20, on every target the engine pins.
 */

#include <pymergetic/rxftk/core/ground.h>

#include "../rxfb_layout.h"

#define SAME_SIZE(ours, theirs)                                                                    \
    RXFB_ASSERT(sizeof(ours) == sizeof(theirs), #ours " is not " #theirs " in size");              \
    RXFB_ASSERT(RXFB_ALIGNOF(ours) == RXFB_ALIGNOF(theirs), #ours " is not " #theirs " aligned")

#define SAME_FIELD(ours, theirs, field)                                                            \
    RXFB_ASSERT(offsetof(ours, field) == offsetof(theirs, field),                                  \
                #ours "." #field " is not " #theirs "." #field " in offset");                      \
    RXFB_ASSERT(sizeof(((ours *)0)->field) == sizeof(((theirs *)0)->field),                        \
                #ours "." #field " is not " #theirs "." #field " in size")

/* Through a 64-bit value: the enumerators of two headers are different types in C++. */
#define SAME_VALUE(ours, theirs)                                                                   \
    RXFB_ASSERT((unsigned long long)(ours) == (unsigned long long)(theirs),                        \
                #ours " is not " #theirs)

/* the superblock */
SAME_SIZE(rxfb_superblock, pymergetic_rxf_superblock);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, magic);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, version);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, committed);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, limit);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, root);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, entry);
SAME_FIELD(rxfb_superblock, pymergetic_rxf_superblock, reserved);
SAME_VALUE(RXFB_SUPER_SIZE, PYMERGETIC_RXF_SUPER_SIZE);
SAME_VALUE(RXFB_SUPER_VERSION, PYMERGETIC_RXF_SUPER_VERSION);

/* handles */
SAME_SIZE(rxfb_handle, pymergetic_rxf_handle32);
SAME_VALUE(RXFB_HANDLE_NONE, PYMERGETIC_RXF_HANDLE32_NONE);
SAME_VALUE(RXFB_HANDLE_INDEX_BITS, PYMERGETIC_RXF_HANDLE32_INDEX_BITS);
SAME_VALUE(RXFB_HANDLE_GEN_BITS, PYMERGETIC_RXF_HANDLE32_GEN_BITS);
SAME_VALUE(RXFB_HANDLE_INDEX_MASK, PYMERGETIC_RXF_HANDLE32_INDEX_MASK);
SAME_VALUE(RXFB_HANDLE_GEN_MASK, PYMERGETIC_RXF_HANDLE32_GEN_MASK);

/* the object record */
SAME_SIZE(rxfb_object, pymergetic_rxf_object);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, kind);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, gen);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, flags);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, name);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, parent);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, link);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, refs);
SAME_FIELD(rxfb_object, pymergetic_rxf_object, payload);
SAME_VALUE(RXFB_OBJECT_ALIGN, PYMERGETIC_RXF_OBJECT_ALIGN);
SAME_VALUE(RXFB_OBJ_MODULE, PYMERGETIC_RXF_OBJ_MODULE);
SAME_VALUE(RXFB_OBJ_FUNC, PYMERGETIC_RXF_OBJ_FUNC);
SAME_VALUE(RXFB_OBJ_HASH, PYMERGETIC_RXF_OBJ_HASH);
SAME_VALUE(RXFB_OBJ_SPEC, PYMERGETIC_RXF_OBJ_SPEC);
SAME_VALUE(RXFB_OBJ_SYM, PYMERGETIC_RXF_OBJ_SYM);
SAME_VALUE(RXFB_OBJ_BLOB, PYMERGETIC_RXF_OBJ_BLOB);
SAME_VALUE(RXFB_OBJ_FRAME, PYMERGETIC_RXF_OBJ_FRAME);
SAME_VALUE(RXFB_OBJ_JOURNAL, PYMERGETIC_RXF_OBJ_JOURNAL);
SAME_VALUE(RXFB_OBJ_FREE, PYMERGETIC_RXF_OBJ_FREE);
SAME_VALUE(RXFB_OFLAG_RO, PYMERGETIC_RXF_OFLAG_RO);
SAME_VALUE(RXFB_OFLAG_ROOT, PYMERGETIC_RXF_OFLAG_ROOT);
SAME_VALUE(RXFB_OFLAG_DIRTY, PYMERGETIC_RXF_OFLAG_DIRTY);
SAME_VALUE(RXFB_OFLAG_JOURNALED, PYMERGETIC_RXF_OFLAG_JOURNALED);

/* the object index */
SAME_SIZE(rxfb_index_entry, pymergetic_rxf_slot);
SAME_FIELD(rxfb_index_entry, pymergetic_rxf_slot, kind);
SAME_FIELD(rxfb_index_entry, pymergetic_rxf_slot, gen);
SAME_FIELD(rxfb_index_entry, pymergetic_rxf_slot, reserved);
SAME_FIELD(rxfb_index_entry, pymergetic_rxf_slot, slot);
SAME_SIZE(rxfb_index_head, pymergetic_rxf_slots);
SAME_FIELD(rxfb_index_head, pymergetic_rxf_slots, count);
SAME_FIELD(rxfb_index_head, pymergetic_rxf_slots, capacity);
SAME_VALUE(RXFB_INDEX_OFFSET, PYMERGETIC_RXF_INDEX_OFFSET);
SAME_VALUE(RXFB_INDEX_SELF, PYMERGETIC_RXF_INDEX_SELF);
SAME_VALUE(RXFB_INDEX_SYMS, PYMERGETIC_RXF_INDEX_SYMS);

/* modules, symbols */
SAME_SIZE(rxfb_module, pymergetic_rxf_module);
SAME_FIELD(rxfb_module, pymergetic_rxf_module, children);
SAME_FIELD(rxfb_module, pymergetic_rxf_module, count);
SAME_SIZE(rxfb_syms, pymergetic_rxf_syms);
SAME_FIELD(rxfb_syms, pymergetic_rxf_syms, count);
SAME_FIELD(rxfb_syms, pymergetic_rxf_syms, capacity);
SAME_VALUE(RXFB_SYMS_ANONYMOUS, PYMERGETIC_RXF_SYMS_ANONYMOUS);

/* the semantic rows */
SAME_SIZE(rxfb_spec_row, pymergetic_rxf_spec);
SAME_FIELD(rxfb_spec_row, pymergetic_rxf_spec, type);
SAME_FIELD(rxfb_spec_row, pymergetic_rxf_spec, flags);
SAME_FIELD(rxfb_spec_row, pymergetic_rxf_spec, index);
SAME_FIELD(rxfb_spec_row, pymergetic_rxf_spec, reserved);
SAME_FIELD(rxfb_spec_row, pymergetic_rxf_spec, generation);
SAME_VALUE(RXFB_FLAG_KIND_SHIFT, PYMERGETIC_RXF_FLAG_KIND_SHIFT);
SAME_VALUE(RXFB_FLAG_KIND_MASK, PYMERGETIC_RXF_FLAG_KIND_MASK);
SAME_VALUE(RXFB_FLAG_STATE_SHIFT, PYMERGETIC_RXF_FLAG_STATE_SHIFT);
SAME_VALUE(RXFB_FLAG_STATE_MASK, PYMERGETIC_RXF_FLAG_STATE_MASK);
SAME_VALUE(RXFB_FLAG_OWNER_SHIFT, PYMERGETIC_RXF_FLAG_OWNER_SHIFT);
SAME_VALUE(RXFB_FLAG_OWNER_MASK, PYMERGETIC_RXF_FLAG_OWNER_MASK);

/* the references */
SAME_SIZE(rxfb_ref, pymergetic_rxf_ref);
SAME_FIELD(rxfb_ref, pymergetic_rxf_ref, target);
SAME_FIELD(rxfb_ref, pymergetic_rxf_ref, role);
SAME_FIELD(rxfb_ref, pymergetic_rxf_ref, kind);
SAME_FIELD(rxfb_ref, pymergetic_rxf_ref, binding);
SAME_FIELD(rxfb_ref, pymergetic_rxf_ref, index);
SAME_VALUE(RXFB_REF_NONE, PYMERGETIC_RXF_HANDLE_NONE);
SAME_VALUE(RXFB_REF_CALL, PYMERGETIC_RXF_REF_CALL);
SAME_VALUE(RXFB_REF_DATA, PYMERGETIC_RXF_REF_DATA);
SAME_VALUE(RXFB_REF_ENTRY, PYMERGETIC_RXF_REF_ENTRY);
SAME_VALUE(RXFB_REF_TYPE, PYMERGETIC_RXF_REF_TYPE);
SAME_VALUE(RXFB_REF_IMPORT, PYMERGETIC_RXF_REF_IMPORT);
SAME_VALUE(RXFB_REF_IMPLEMENTATION, PYMERGETIC_RXF_REF_IMPLEMENTATION);
SAME_VALUE(RXFB_REF_OWNER, PYMERGETIC_RXF_REF_OWNER);
SAME_VALUE(RXFB_REF_DOC, PYMERGETIC_RXF_REF_DOC);
SAME_VALUE(RXFB_BINDING_MANDATORY, PYMERGETIC_RXF_BINDING_MANDATORY);
SAME_VALUE(RXFB_BINDING_OPTIONAL, PYMERGETIC_RXF_BINDING_OPTIONAL);

/* The test is the compiler; this keeps a C translation unit from being empty. */
int rxfb_layout_pins(void) {
    return 0;
}
