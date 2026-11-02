/*
 * rxfb: the slim reader of an RXFB v7 image. See rxfb.h.
 *
 * Freestanding: nothing from libc, no allocation, no global state, no writes. Every function here
 * is the engine's own read path (core/arena/object_arena.cpp) with the checks an untrusted image
 * needs added - the walks, the order and the rules are the same:
 *
 *   rxfb_attach      ObjectArena::attach        + model_ready, the symbol table, sys/specs
 *   rxfb_record      ObjectArena::load
 *   rxfb_payload     ObjectArena::payload
 *   rxfb_symbol      ObjectArena::symbol
 *   rxfb_find        ObjectArena::find          (symbol_id, then the chain)
 *   rxfb_children    ObjectArena::children
 *   rxfb_path        ObjectArena::path_of
 *   rxfb_spec        ObjectArena::spec
 *   rxfb_refs        ObjectArena::refs_of       (relation_rows, path_matches: a view, not a copy)
 *   rxfb_table       ObjectArena::relation_rows (the rows of one table, found by its handle)
 *   rxfb_slot_of     tools/native/link.py       (_leaf, _code: the seats' own resolution)
 *
 * Every chain walk is bounded by the number of handles the image holds, so a corrupt link cycle
 * ends in RXFB_BAD_IMAGE instead of a hang.
 */

#include "rxfb.h"

/* Words of the image, read in place. */
typedef uint64_t __attribute__((may_alias)) word64;
typedef uint32_t __attribute__((may_alias)) word32;

/* The end marker: a FREE object at `committed` with the two ring links. */
#define RXFB_END_MARKER (sizeof(rxfb_object) + 16U)
/* A path longer than this many segments is a cycle, not a tree (the engine's bound). */
#define RXFB_PATH_DEPTH 1024U

/* ---- bytes ---------------------------------------------------------------------------------- */

static uint32_t text_length(const char *text) {
    uint32_t n = 0;
    while (text[n] != 0)
        n++;
    return n;
}

static int same_bytes(const char *a, const char *b, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) {
        if (a[i] != b[i])
            return 0;
    }
    return 1;
}

/* ---- the index ------------------------------------------------------------------------------ */

static const rxfb_index_entry *index_entry(const rxfb *rx, uint32_t index) {
    return (const rxfb_index_entry *)(rx->index + 1) + index;
}

static const rxfb_module *module_of(const rxfb_object *object) {
    return (const rxfb_module *)(object + 1);
}

static const uint8_t *payload_of(const rxfb_object *object) {
    return (const uint8_t *)(object + 1);
}

/* The index is the first object of the image and entry 0 is its own identity: a region without
 * that shape has no object model in it. The table has to lie inside the committed bytes. */
static int model_ready(uint64_t committed, const rxfb_index_head *head) {
    if (head->capacity == 0 || head->capacity > (1U << RXFB_HANDLE_INDEX_BITS) ||
        head->count == 0 || head->count > head->capacity)
        return 0;
    const uint64_t table_end = (uint64_t)RXFB_INDEX_OFFSET + sizeof(rxfb_object) +
                               sizeof(rxfb_index_head) +
                               (uint64_t)head->capacity * sizeof(rxfb_index_entry);
    if (table_end > committed)
        return 0;
    const rxfb_index_entry *self = (const rxfb_index_entry *)(head + 1);
    return self->slot == RXFB_INDEX_OFFSET && self->kind == RXFB_OBJ_MODULE;
}

rxfb_status rxfb_record(const rxfb *rx, rxfb_handle handle, const rxfb_object **record) {
    if (handle == RXFB_HANDLE_NONE)
        return RXFB_BAD_HANDLE;
    const uint32_t index = rxfb_handle_index(handle);
    const uint32_t gen = rxfb_handle_gen(handle);
    if (index >= rx->index->count)
        return RXFB_BAD_HANDLE;
    const rxfb_index_entry *entry = index_entry(rx, index);
    const uint64_t committed = rx->super->committed;
    if (entry->slot == 0 || entry->gen != gen || entry->slot >= committed)
        return RXFB_BAD_HANDLE;
    /* The record and its payload have to lie inside the objects: a slot the engine trusts is a
     * claim here. */
    if ((entry->slot & (RXFB_OBJECT_ALIGN - 1U)) != 0 || entry->slot < RXFB_INDEX_OFFSET ||
        committed - entry->slot < sizeof(rxfb_object))
        return RXFB_BAD_IMAGE;
    const rxfb_object *found = (const rxfb_object *)(rx->base + entry->slot);
    if (found->gen != gen || found->kind != entry->kind)
        return RXFB_BAD_HANDLE;
    if (found->payload > committed - entry->slot - sizeof(rxfb_object))
        return RXFB_BAD_IMAGE;
    *record = found;
    return RXFB_OK;
}

rxfb_status rxfb_payload(const rxfb *rx, rxfb_handle handle, const uint8_t **bytes,
                         uint32_t *size) {
    const rxfb_object *record = 0;
    const rxfb_status status = rxfb_record(rx, handle, &record);
    if (status != RXFB_OK)
        return status;
    *bytes = payload_of(record);
    *size = record->payload;
    return RXFB_OK;
}

/* ---- the symbols ---------------------------------------------------------------------------- */

/* The symbol table as entry 1 names it (the identity, not a cached pointer). */
static rxfb_status syms_view(const rxfb *rx, const rxfb_syms **head, uint32_t *room) {
    const rxfb_index_entry *entry = index_entry(rx, RXFB_INDEX_SYMS);
    const rxfb_object *record = 0;
    const rxfb_status status =
        rxfb_record(rx, rxfb_handle_pack(RXFB_INDEX_SYMS, entry->gen), &record);
    if (status != RXFB_OK)
        return RXFB_BAD_IMAGE;
    if (record->kind != RXFB_OBJ_SYM || record->payload < sizeof(rxfb_syms))
        return RXFB_BAD_IMAGE;
    const rxfb_syms *syms = (const rxfb_syms *)payload_of(record);
    if (syms->count == 0 || syms->count > syms->capacity ||
        (uint64_t)syms->capacity * sizeof(uint32_t) + sizeof(rxfb_syms) > record->payload)
        return RXFB_BAD_IMAGE;
    *head = syms;
    *room = record->payload;
    return RXFB_OK;
}

/* The string of symbol `id` inside the table's payload, NUL-terminated within it. */
static rxfb_status syms_text(const rxfb_syms *head, uint32_t room, uint32_t id, const char **text,
                             uint32_t *length) {
    if (id == RXFB_SYMS_ANONYMOUS || id >= head->count)
        return RXFB_BAD_NAME;
    const word32 *offsets = (const word32 *)(head + 1);
    const uint32_t at = offsets[id];
    if (at >= room)
        return RXFB_BAD_IMAGE;
    const char *held = (const char *)head + at;
    uint32_t n = 0;
    while (at + n < room && held[n] != 0)
        n++;
    if (at + n >= room)
        return RXFB_BAD_IMAGE; /* no terminator inside the table */
    *text = held;
    *length = n;
    return RXFB_OK;
}

rxfb_status rxfb_symbol(const rxfb *rx, uint32_t id, const char **text, uint32_t *length) {
    const rxfb_syms *head = 0;
    uint32_t room = 0;
    rxfb_status status = syms_view(rx, &head, &room);
    if (status != RXFB_OK)
        return status;
    return syms_text(head, room, id, text, length);
}

/* The id a text already has; 0 when nothing interned it. */
static rxfb_status symbol_id(const rxfb *rx, const char *text, uint32_t length, uint32_t *id) {
    const rxfb_syms *head = 0;
    uint32_t room = 0;
    rxfb_status status = syms_view(rx, &head, &room);
    if (status != RXFB_OK)
        return status;
    const word32 *offsets = (const word32 *)(head + 1);
    *id = RXFB_SYMS_ANONYMOUS;
    for (uint32_t i = 1; i < head->count; i++) {
        const uint32_t at = offsets[i];
        /* The string and its terminator have to be inside the table. */
        if (at >= room || length >= room - at)
            continue;
        const char *held = (const char *)head + at;
        if (held[length] == 0 && same_bytes(held, text, length)) {
            *id = i;
            return RXFB_OK;
        }
    }
    return RXFB_OK;
}

rxfb_status rxfb_name(const rxfb *rx, rxfb_handle handle, const char **text, uint32_t *length) {
    const rxfb_object *record = 0;
    const rxfb_status status = rxfb_record(rx, handle, &record);
    if (status != RXFB_OK)
        return status;
    *text = 0;
    *length = 0;
    if (record->name == RXFB_SYMS_ANONYMOUS)
        return RXFB_OK;
    return rxfb_symbol(rx, record->name, text, length);
}

/* ---- the tree ------------------------------------------------------------------------------- */

/* The head of a module's chain, checked. A leaf has no chain. */
static rxfb_status chain_head(const rxfb_object *module, rxfb_handle *head) {
    if (module->payload < sizeof(rxfb_module))
        return RXFB_BAD_IMAGE;
    *head = module_of(module)->children;
    return RXFB_OK;
}

rxfb_status rxfb_find(const rxfb *rx, rxfb_handle parent, const char *name, uint32_t length,
                      rxfb_handle *handle) {
    *handle = RXFB_HANDLE_NONE;
    if (name == 0 || length == 0)
        return RXFB_BAD_NAME;
    if (rxfb_handle_index(parent) == RXFB_INDEX_SELF)
        return RXFB_PROTECTED; /* its payload is the index table, not a chain */
    const rxfb_object *module = 0;
    rxfb_status status = rxfb_record(rx, parent, &module);
    if (status != RXFB_OK)
        return status;
    if (module->kind != RXFB_OBJ_MODULE)
        return RXFB_NOT_FOUND; /* a leaf has no children */
    uint32_t id = RXFB_SYMS_ANONYMOUS;
    status = symbol_id(rx, name, length, &id);
    if (status != RXFB_OK)
        return status;
    if (id == RXFB_SYMS_ANONYMOUS)
        return RXFB_NOT_FOUND; /* nothing interned it, so no child carries it */
    rxfb_handle walk = RXFB_HANDLE_NONE;
    status = chain_head(module, &walk);
    if (status != RXFB_OK)
        return status;
    for (uint32_t steps = 0; walk != RXFB_HANDLE_NONE; steps++) {
        if (steps > rx->index->count)
            return RXFB_BAD_IMAGE; /* a chain longer than the image has objects */
        const rxfb_object *child = 0;
        status = rxfb_record(rx, walk, &child);
        if (status != RXFB_OK)
            return status;
        if (child->name == id) {
            *handle = walk;
            return RXFB_OK;
        }
        walk = child->link;
    }
    return RXFB_NOT_FOUND;
}

rxfb_status rxfb_lookup(const rxfb *rx, const char *path, uint32_t length, rxfb_handle *handle) {
    *handle = RXFB_HANDLE_NONE;
    rxfb_handle at = rxfb_root(rx);
    uint32_t cut = 0;
    while (cut < length) {
        uint32_t end = cut;
        while (end < length && path[end] != '.')
            end++;
        if (end == cut)
            return RXFB_BAD_NAME; /* an empty segment */
        const rxfb_status status = rxfb_find(rx, at, path + cut, end - cut, &at);
        if (status != RXFB_OK)
            return status;
        cut = end + 1;
        if (end < length && cut == length)
            return RXFB_BAD_NAME; /* a path that ends in a dot */
    }
    *handle = at;
    return RXFB_OK;
}

rxfb_status rxfb_first_child(const rxfb *rx, rxfb_handle parent, rxfb_handle *child) {
    *child = RXFB_HANDLE_NONE;
    if (rxfb_handle_index(parent) == RXFB_INDEX_SELF)
        return RXFB_PROTECTED;
    const rxfb_object *module = 0;
    const rxfb_status status = rxfb_record(rx, parent, &module);
    if (status != RXFB_OK)
        return status;
    if (module->kind != RXFB_OBJ_MODULE)
        return RXFB_OK; /* a leaf cannot have children */
    return chain_head(module, child);
}

rxfb_status rxfb_next_sibling(const rxfb *rx, rxfb_handle child, rxfb_handle *next) {
    *next = RXFB_HANDLE_NONE;
    const rxfb_object *record = 0;
    const rxfb_status status = rxfb_record(rx, child, &record);
    if (status != RXFB_OK)
        return status;
    *next = record->link;
    return RXFB_OK;
}

rxfb_status rxfb_children(const rxfb *rx, rxfb_handle handle, rxfb_handle *out, uint32_t capacity,
                          uint32_t *written) {
    *written = 0;
    rxfb_handle walk = RXFB_HANDLE_NONE;
    rxfb_status status = rxfb_first_child(rx, handle, &walk);
    if (status != RXFB_OK)
        return status;
    uint32_t count = 0;
    while (walk != RXFB_HANDLE_NONE) {
        if (count > rx->index->count)
            return RXFB_BAD_IMAGE;
        if (out != 0 && count < capacity)
            out[count] = walk;
        count++;
        status = rxfb_next_sibling(rx, walk, &walk);
        if (status != RXFB_OK)
            return status;
    }
    *written = count;
    if (count == 0)
        return RXFB_OK;
    if (out == 0 || count > capacity)
        return RXFB_NO_ROOM; /* *written is what the buffer has to offer */
    return RXFB_OK;
}

rxfb_status rxfb_path(const rxfb *rx, rxfb_handle handle, char *out, uint32_t capacity,
                      uint32_t *written) {
    *written = 0;
    const rxfb_object *record = 0;
    rxfb_status status = rxfb_record(rx, handle, &record); /* the handle is checked first */
    if (status != RXFB_OK)
        return status;
    const rxfb_syms *head = 0;
    uint32_t room = 0;
    status = syms_view(rx, &head, &room);
    if (status != RXFB_OK)
        return status;

    /* The root is the image, not a path segment: its path is empty. */
    uint64_t total = 0;
    rxfb_handle walk = handle;
    uint32_t depth = 0;
    while (walk != rxfb_root(rx)) {
        const rxfb_object *part = 0;
        status = rxfb_record(rx, walk, &part);
        if (status != RXFB_OK)
            return status;
        if (part->name != RXFB_SYMS_ANONYMOUS) {
            const char *text = 0;
            uint32_t size = 0;
            status = syms_text(head, room, part->name, &text, &size);
            if (status != RXFB_OK)
                return status;
            total += (uint64_t)size + (total == 0 ? 0U : 1U);
        }
        if (part->parent == RXFB_HANDLE_NONE)
            return RXFB_BAD_IMAGE; /* a tree that does not reach the root */
        walk = part->parent;
        if (++depth > RXFB_PATH_DEPTH)
            return RXFB_BAD_IMAGE;
    }
    if (total > 0xFFFFFFFFULL)
        return RXFB_BAD_IMAGE;
    *written = (uint32_t)total;
    if (total > capacity)
        return RXFB_NO_ROOM; /* *written is what the buffer has to offer */

    /* Written backwards from the end, so the segments land in tree order. */
    uint64_t at = total;
    walk = handle;
    while (walk != rxfb_root(rx)) {
        const rxfb_object *part = 0;
        if (rxfb_record(rx, walk, &part) != RXFB_OK)
            return RXFB_BAD_IMAGE;
        if (part->name != RXFB_SYMS_ANONYMOUS) {
            const char *text = 0;
            uint32_t size = 0;
            if (syms_text(head, room, part->name, &text, &size) != RXFB_OK)
                return RXFB_BAD_IMAGE;
            at -= size;
            for (uint32_t i = 0; i < size; i++)
                out[at + i] = text[i];
            if (at != 0)
                out[--at] = '.';
        }
        walk = part->parent;
    }
    return RXFB_OK;
}

/* ---- the semantic rows and the references --------------------------------------------------- */

rxfb_status rxfb_spec(const rxfb *rx, rxfb_handle handle, const rxfb_spec_row **row) {
    const rxfb_object *record = 0;
    rxfb_status status = rxfb_record(rx, handle, &record);
    if (status != RXFB_OK)
        return status;
    if (rx->specs == RXFB_HANDLE_NONE)
        return RXFB_BAD_IMAGE; /* an image without the table */
    const rxfb_object *table = 0;
    status = rxfb_record(rx, rx->specs, &table);
    if (status != RXFB_OK)
        return status;
    const uint32_t index = rxfb_handle_index(handle);
    if (index >= table->payload / sizeof(rxfb_spec_row))
        return RXFB_BAD_IMAGE;
    *row = (const rxfb_spec_row *)payload_of(table) + index;
    return RXFB_OK;
}

/* Does `name` spell the path of `handle`? Compares from the object up, so the path never has to be
 * materialized. */
static int path_matches(const rxfb *rx, const rxfb_syms *head, uint32_t room, rxfb_handle handle,
                        const char *name, uint32_t length) {
    if (name == 0 || length == 0)
        return 0;
    const char *const start = name;
    const char *end = name + length;
    rxfb_handle walk = handle;
    uint32_t depth = 0;
    while (end != start) {
        /* More segments than the tree has: the root is the image, not a segment. */
        if (walk == RXFB_HANDLE_NONE || walk == rxfb_root(rx) || ++depth > RXFB_PATH_DEPTH)
            return 0;
        const rxfb_object *part = 0;
        if (rxfb_record(rx, walk, &part) != RXFB_OK)
            return 0;
        if (part->name == RXFB_SYMS_ANONYMOUS)
            return 0; /* an unnamed object has no path segment */
        const char *segment = end;
        while (segment != start && segment[-1] != '.')
            --segment;
        const uint32_t size = (uint32_t)(end - segment);
        const char *text = 0;
        uint32_t held = 0;
        if (syms_text(head, room, part->name, &text, &held) != RXFB_OK)
            return 0;
        if (held != size || !same_bytes(text, segment, size))
            return 0;
        walk = part->parent;
        end = (segment == start) ? segment : segment - 1; /* the dot before the segment */
    }
    return walk == rxfb_root(rx);
}

/* The rows of a relation table: the room of a table is a whole number of rows and may carry the
 * zero padding of an 8-byte slot; a real row always names a target, and a live handle is never 0,
 * so an all-zero row is padding. */
static void table_rows(const rxfb_object *table, const rxfb_ref **rows, uint32_t *count) {
    const rxfb_ref *found = (const rxfb_ref *)payload_of(table);
    uint32_t held = table->payload / sizeof(rxfb_ref);
    while (held > 0 && found[held - 1].target == 0 && found[held - 1].role == 0 &&
           found[held - 1].kind == 0 && found[held - 1].binding == 0 &&
           found[held - 1].index == 0)
        --held;
    *rows = found;
    *count = held;
}

rxfb_status rxfb_table(const rxfb *rx, rxfb_handle table, const rxfb_ref **rows, uint32_t *count) {
    *rows = 0;
    *count = 0;
    const rxfb_object *record = 0;
    rxfb_status status = rxfb_record(rx, table, &record);
    if (status != RXFB_OK)
        return status;
    if (rx->relations == RXFB_HANDLE_NONE || record->parent != rx->relations)
        return RXFB_BAD_HANDLE; /* not a table of sys/relations */
    table_rows(record, rows, count);
    return RXFB_OK;
}

rxfb_status rxfb_refs(const rxfb *rx, rxfb_handle handle, const rxfb_ref **rows, uint32_t *count) {
    *rows = 0;
    *count = 0;
    const rxfb_object *record = 0;
    rxfb_status status = rxfb_record(rx, handle, &record); /* the handle is checked first */
    if (status != RXFB_OK)
        return status;
    if (rx->relations == RXFB_HANDLE_NONE)
        return RXFB_OK; /* no tables at all, so nothing has refs */
    const rxfb_object *module = 0;
    status = rxfb_record(rx, rx->relations, &module);
    if (status != RXFB_OK)
        return status;
    const rxfb_syms *head = 0;
    uint32_t room = 0;
    status = syms_view(rx, &head, &room);
    if (status != RXFB_OK)
        return status;
    rxfb_handle walk = RXFB_HANDLE_NONE;
    status = chain_head(module, &walk);
    if (status != RXFB_OK)
        return status;
    for (uint32_t steps = 0; walk != RXFB_HANDLE_NONE; steps++) {
        if (steps > rx->index->count)
            return RXFB_BAD_IMAGE;
        const rxfb_object *table = 0;
        status = rxfb_record(rx, walk, &table);
        if (status != RXFB_OK)
            return status;
        if (table->name != RXFB_SYMS_ANONYMOUS) {
            const char *name = 0;
            uint32_t size = 0;
            status = syms_text(head, room, table->name, &name, &size);
            if (status != RXFB_OK)
                return status;
            if (path_matches(rx, head, room, handle, name, size)) {
                table_rows(table, rows, count);
                return RXFB_OK;
            }
        }
        walk = table->link;
    }
    return RXFB_OK; /* the object has no table: no refs */
}

/* ---- the code story ------------------------------------------------------------------------- */

/* A reference slot of a payload holds a handle zero-extended to 64 bits. */
static rxfb_status handle_of(uint64_t word, rxfb_handle *handle) {
    if (word >> 32 != 0)
        return RXFB_BAD_IMAGE;
    *handle = (rxfb_handle)word;
    return RXFB_OK;
}

rxfb_status rxfb_code_of(const rxfb *rx, rxfb_handle function, const char *machine,
                         rxfb_code *code) {
    code->entry = 0;
    code->length = 0;
    code->code = RXFB_HANDLE_NONE;
    code->blob = RXFB_HANDLE_NONE;
    rxfb_handle found = RXFB_HANDLE_NONE;
    rxfb_status status = rxfb_find(rx, function, machine, text_length(machine), &found);
    if (status != RXFB_OK)
        return status;
    const rxfb_object *record = 0;
    status = rxfb_record(rx, found, &record);
    if (status != RXFB_OK)
        return status;
    if (record->kind != RXFB_OBJ_FUNC || record->payload < RXFB_CODE_SIZE)
        return RXFB_BAD_IMAGE;
    const uint8_t *fixed = payload_of(record);
    const uint64_t entry_offset = *(const word64 *)(fixed + RXFB_CODE_ENTRY_OFFSET);
    const uint64_t body = *(const word64 *)(fixed + RXFB_CODE_BODY);
    if (body == RXFB_REF_NONE)
        return RXFB_NOT_FOUND; /* a Code without bytes */
    rxfb_handle blob_handle = RXFB_HANDLE_NONE;
    status = handle_of(body, &blob_handle);
    if (status != RXFB_OK)
        return status;
    const rxfb_object *blob = 0;
    status = rxfb_record(rx, blob_handle, &blob);
    if (status != RXFB_OK)
        return RXFB_BAD_IMAGE;
    if (blob->kind != RXFB_OBJ_BLOB || blob->payload < RXFB_BLOB_DATA)
        return RXFB_BAD_IMAGE;
    const uint64_t byte_count = *(const word64 *)(payload_of(blob) + RXFB_BLOB_BYTE_COUNT);
    if (byte_count > blob->payload - RXFB_BLOB_DATA || entry_offset > byte_count)
        return RXFB_BAD_IMAGE; /* the bytes are not inside the blob's room */
    code->entry = payload_of(blob) + RXFB_BLOB_DATA + entry_offset;
    code->length = byte_count - entry_offset;
    code->code = found;
    code->blob = blob_handle;
    return RXFB_OK;
}

rxfb_status rxfb_slot_of(const rxfb *rx, rxfb_handle run, const char *machine, uint32_t slot,
                         rxfb_code *code) {
    const rxfb_ref *rows = 0;
    uint32_t count = 0;
    rxfb_status status = rxfb_refs(rx, run, &rows, &count);
    if (status != RXFB_OK)
        return status;
    for (uint32_t i = 0; i < count; i++) {
        if (rows[i].index != slot || rows[i].role == RXFB_REF_NONE)
            continue;
        rxfb_handle role = RXFB_HANDLE_NONE;
        if (handle_of(rows[i].role, &role) != RXFB_OK)
            continue;
        const char *word = 0;
        uint32_t size = 0;
        if (rxfb_name(rx, role, &word, &size) != RXFB_OK || size != 4 ||
            !same_bytes(word, "Leaf", 4))
            continue;
        rxfb_handle function = RXFB_HANDLE_NONE;
        status = handle_of(rows[i].target, &function);
        if (status != RXFB_OK)
            return status;
        return rxfb_code_of(rx, function, machine, code);
    }
    code->entry = 0;
    code->length = 0;
    code->code = RXFB_HANDLE_NONE;
    code->blob = RXFB_HANDLE_NONE;
    return RXFB_NOT_FOUND; /* the interpreter has no such leaf */
}

rxfb_status rxfb_slot(const rxfb *rx, const char *machine, uint32_t slot, rxfb_code *code) {
    rxfb_handle run = RXFB_HANDLE_NONE;
    const rxfb_status status = rxfb_lookup(rx, RXFB_RUN_PATH, sizeof(RXFB_RUN_PATH) - 1U, &run);
    if (status != RXFB_OK)
        return status;
    return rxfb_slot_of(rx, run, machine, slot, code);
}

/* ---- the image ------------------------------------------------------------------------------ */

rxfb_handle rxfb_root(const rxfb *rx) {
    return rx->super->root;
}

rxfb_handle rxfb_entry(const rxfb *rx) {
    return rx->super->entry;
}

uint32_t rxfb_count(const rxfb *rx) {
    return rx->index->count;
}

/* A table of sys/: its handle, or NONE when the image does not have it. */
static rxfb_status sys_table(const rxfb *rx, rxfb_handle sys, const char *name,
                             rxfb_handle *handle) {
    *handle = RXFB_HANDLE_NONE;
    if (sys == RXFB_HANDLE_NONE)
        return RXFB_OK;
    const rxfb_status status = rxfb_find(rx, sys, name, text_length(name), handle);
    if (status == RXFB_NOT_FOUND)
        return RXFB_OK;
    return status;
}

rxfb_status rxfb_attach(rxfb *rx, const void *bytes, uint64_t length) {
    if (bytes == 0 || ((uintptr_t)bytes & (RXFB_OBJECT_ALIGN - 1U)) != 0)
        return RXFB_BAD_IMAGE;
    if (length < RXFB_SUPER_SIZE + sizeof(rxfb_object) || length > (uint64_t)UINTPTR_MAX)
        return RXFB_BAD_IMAGE;
    const rxfb_superblock *super = (const rxfb_superblock *)bytes;
    if (super->magic[0] != 'R' || super->magic[1] != 'X' || super->magic[2] != 'F' ||
        super->magic[3] != 'A' || super->version != RXFB_SUPER_VERSION)
        return RXFB_BAD_IMAGE;
    /* A truncated image (committed or its end marker beyond what is there) is never trusted. */
    if (super->committed < RXFB_SUPER_SIZE || super->committed > super->limit ||
        super->committed > length || length - super->committed < RXFB_END_MARKER ||
        super->limit - super->committed < RXFB_END_MARKER)
        return RXFB_BAD_IMAGE;
    const uint64_t index_head = (uint64_t)RXFB_INDEX_OFFSET + sizeof(rxfb_object);
    if (super->committed < index_head + sizeof(rxfb_index_head))
        return RXFB_BAD_IMAGE;
    const rxfb_index_head *head = (const rxfb_index_head *)((const uint8_t *)bytes + index_head);
    if (!model_ready(super->committed, head))
        return RXFB_BAD_IMAGE;

    /* The walks below need the anchors; they run on a local view and the caller's struct is only
     * written when the image has passed every check. */
    rxfb view;
    view.base = (const uint8_t *)bytes;
    view.size = length;
    view.super = super;
    view.index = head;
    view.specs = RXFB_HANDLE_NONE;
    view.relations = RXFB_HANDLE_NONE;

    /* The anchors: the symbol table, the root, then sys/specs and sys/relations. */
    const rxfb_syms *syms = 0;
    uint32_t room = 0;
    rxfb_status status = syms_view(&view, &syms, &room);
    if (status != RXFB_OK)
        return status;
    const rxfb_object *root = 0;
    if (rxfb_record(&view, super->root, &root) != RXFB_OK || root->kind != RXFB_OBJ_MODULE)
        return RXFB_BAD_IMAGE;
    rxfb_handle sys = RXFB_HANDLE_NONE;
    if (sys_table(&view, super->root, "sys", &sys) != RXFB_OK ||
        sys_table(&view, sys, "specs", &view.specs) != RXFB_OK ||
        sys_table(&view, sys, "relations", &view.relations) != RXFB_OK)
        return RXFB_BAD_IMAGE;

    rx->base = view.base;
    rx->size = view.size;
    rx->super = view.super;
    rx->index = view.index;
    rx->specs = view.specs;
    rx->relations = view.relations;
    return RXFB_OK;
}
