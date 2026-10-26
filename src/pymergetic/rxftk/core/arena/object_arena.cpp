#include <pymergetic/rxftk/core/arena/mem.hpp>
#include <pymergetic/rxftk/core/arena/object_arena.hpp>

namespace pymergetic::rxf::core::arena {

namespace {

inline uint64_t room_up(uint64_t requested) {
    return (requested + 7ULL) & ~7ULL;
}

inline uint64_t span_of(const pymergetic_rxf_object *record) {
    return pymergetic_rxf_object_size(record);
}

/* The 12-bit generation of a reused span, never 0: 0 is the fresh-tail generation. */
inline uint16_t next_gen(uint16_t was) {
    uint32_t gen = ((uint32_t)was + 1U) & PYMERGETIC_RXF_HANDLE32_GEN_MASK;
    return (uint16_t)(gen == 0U ? 1U : gen);
}

inline uint64_t link_prev(const uint64_t *links) {
    return links[PYMERGETIC_RXF_FREE_PREV / 8];
}

inline uint64_t link_next(const uint64_t *links) {
    return links[PYMERGETIC_RXF_FREE_NEXT / 8];
}

} // namespace

pymergetic_rxf_object *ObjectArena::record_at(uint64_t offset) const {
    return reinterpret_cast<pymergetic_rxf_object *>(memory_.base + offset);
}

uint64_t *ObjectArena::links_at(uint64_t offset) const {
    return reinterpret_cast<uint64_t *>(memory_.base + offset + sizeof(pymergetic_rxf_object));
}

uint64_t ObjectArena::marker_offset() const {
    return super()->committed;
}

Status ObjectArena::init(const Memory &memory, uint32_t root, uint32_t entry) {
    if (memory.base == nullptr || memory.limit == PYMERGETIC_RXF_LIMIT_UNKNOWN ||
        memory.limit < PYMERGETIC_RXF_SUPER_SIZE + PYMERGETIC_RXF_FREE_MIN_SPAN)
        return Status::OutOfMemory;
    memory_ = memory;
    need_ = 0;
    /* The superblock and the end marker have to be materialized before they are written. */
    Status status = s_ensure(0, PYMERGETIC_RXF_SUPER_SIZE + PYMERGETIC_RXF_FREE_MIN_SPAN);
    if (status != Status::Ok)
        return status;

    pymergetic_rxf_superblock *super = this->super();
    mem::fill(super, 0, sizeof(pymergetic_rxf_superblock));
    super->magic[0] = 'R';
    super->magic[1] = 'X';
    super->magic[2] = 'F';
    super->magic[3] = 'A';
    super->version = PYMERGETIC_RXF_SUPER_VERSION;
    super->committed = PYMERGETIC_RXF_SUPER_SIZE;
    super->limit = memory.limit;
    super->root = root;
    super->entry = entry;

    /* The end marker: the FREE ring anchor that covers the whole tail. */
    const uint64_t tail = memory.limit - PYMERGETIC_RXF_SUPER_SIZE;
    pymergetic_rxf_object *marker = record_at(PYMERGETIC_RXF_SUPER_SIZE);
    mem::fill(marker, 0, (uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS);
    marker->kind = PYMERGETIC_RXF_OBJ_FREE;
    marker->payload = (uint32_t)(tail - sizeof(pymergetic_rxf_object));
    uint64_t *links = links_at(PYMERGETIC_RXF_SUPER_SIZE);
    links[PYMERGETIC_RXF_FREE_PREV / 8] = PYMERGETIC_RXF_SUPER_SIZE;
    links[PYMERGETIC_RXF_FREE_NEXT / 8] = PYMERGETIC_RXF_SUPER_SIZE;
    return verify();
}

Status ObjectArena::attach(const Memory &memory) {
    if (memory.base == nullptr)
        return Status::BadImage;
    memory_ = memory;
    need_ = 0;
    const pymergetic_rxf_superblock *super = this->super();
    if (super->magic[0] != 'R' || super->magic[1] != 'X' || super->magic[2] != 'F' ||
        super->magic[3] != 'A' || super->version != PYMERGETIC_RXF_SUPER_VERSION)
        return Status::BadImage;
    if (super->limit > memory.limit || super->committed < PYMERGETIC_RXF_SUPER_SIZE ||
        super->committed > super->limit)
        return Status::BadImage;
    return Status::Ok;
}

Status ObjectArena::s_ensure(uint64_t at, uint64_t length) {
    if (length > ~0ULL - at)
        return Status::TooLarge;
    const uint64_t end = at + length;
    if (end <= memory_.committed)
        return Status::Ok;
    need_ = end;
    if (memory_.limit != PYMERGETIC_RXF_LIMIT_UNKNOWN && end > memory_.limit)
        return Status::OutOfMemory;
#ifdef PYMERGETIC_RXF_FIXED_REGION
    // A region that is all committed from the start: nothing to call, and a native body has no
    // function pointer it could call through.
    return Status::OutOfMemory;
#else
    if (memory_.commit == nullptr)
        return Status::OutOfMemory;
    uint64_t want = align_up(end, kPageSize);
    if (memory_.limit != PYMERGETIC_RXF_LIMIT_UNKNOWN && want > memory_.limit)
        want = memory_.limit;
    if (!memory_.commit(memory_.ctx, want))
        return Status::OutOfMemory;
    memory_.committed = want;
    return Status::Ok;
#endif
}

void ObjectArena::ring_unlink(uint64_t offset) {
    uint64_t *links = links_at(offset);
    const uint64_t prev = link_prev(links);
    const uint64_t next = link_next(links);
    links_at(prev)[PYMERGETIC_RXF_FREE_NEXT / 8] = next;
    links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8] = prev;
}

void ObjectArena::ring_insert_after(uint64_t anchor, uint64_t offset) {
    uint64_t *links = links_at(offset);
    uint64_t *anchor_links = links_at(anchor);
    const uint64_t next = link_next(anchor_links);
    links[PYMERGETIC_RXF_FREE_PREV / 8] = anchor;
    links[PYMERGETIC_RXF_FREE_NEXT / 8] = next;
    links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8] = offset;
    anchor_links[PYMERGETIC_RXF_FREE_NEXT / 8] = offset;
}

Status ObjectArena::s_allocate(uint64_t room, uint64_t *offset) {
    if (offset == nullptr || room > kMaxPayload)
        return Status::TooLarge;
    room = room_up(room);
    const uint64_t total = sizeof(pymergetic_rxf_object) + room;

    /* Best fit over the FREE ring, the end marker included. The marker is never taken
     * whole: it is the ring anchor and has to keep a ring span (40 bytes) behind. */
    const uint64_t anchor = marker_offset();
    uint64_t best = 0;
    uint64_t best_span = 0;
    bool best_is_marker = false;
    bool found = false;
    uint64_t walk = anchor;
    uint64_t guard = (super()->limit / PYMERGETIC_RXF_FREE_MIN_SPAN) + 2;
    while (guard-- > 0) {
        const pymergetic_rxf_object *record = record_at(walk);
        const uint64_t span = span_of(record);
        const bool is_marker = walk == anchor;
        if (span >= total && (!is_marker || span >= total + PYMERGETIC_RXF_FREE_MIN_SPAN) &&
            (!found || span < best_span)) {
            best = walk;
            best_span = span;
            best_is_marker = is_marker;
            found = true;
        }
        const uint64_t next = link_next(links_at(walk));
        if (next == anchor)
            break;
        walk = next;
    }
    if (!found) {
        need_ = anchor + total;
        return Status::OutOfMemory;
    }

    /* The writes of this allocation - the record, and the remainder's header and links -
     * have to be inside committed bytes (docs/32 3, step 3). */
    const bool split = best_span - total >= PYMERGETIC_RXF_FREE_MIN_SPAN;
    const uint64_t write_span = split ? total + sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS
                                      : best_span;
    Status status = s_ensure(best, write_span);
    if (status != Status::Ok)
        return status;

    pymergetic_rxf_object *record = record_at(best);
    const uint16_t gen = next_gen(record->gen);
    if (split) {
        /* Split: the remainder keeps the ring position of the taken span. */
        const uint64_t rest = best + total;
        const uint64_t prev = link_prev(links_at(best));
        const uint64_t next = link_next(links_at(best));
        const uint64_t prev_home = (prev == best) ? rest : prev;
        const uint64_t next_home = (next == best) ? rest : next;
        mem::fill(record_at(rest), 0,
                  (uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS);
        pymergetic_rxf_object *rest_record = record_at(rest);
        rest_record->kind = PYMERGETIC_RXF_OBJ_FREE;
        rest_record->payload = (uint32_t)(best_span - total - sizeof(pymergetic_rxf_object));
        links_at(rest)[PYMERGETIC_RXF_FREE_PREV / 8] = prev_home;
        links_at(rest)[PYMERGETIC_RXF_FREE_NEXT / 8] = next_home;
        if (prev_home != rest)
            links_at(prev_home)[PYMERGETIC_RXF_FREE_NEXT / 8] = rest;
        if (next_home != rest)
            links_at(next_home)[PYMERGETIC_RXF_FREE_PREV / 8] = rest;
        if (best_is_marker)
            super()->committed = rest;
    } else {
        /* Taken whole: the granted room is the span minus the header. */
        if (best_is_marker)
            return Status::BadImage; /* refused by the search above */
        ring_unlink(best);
        room = best_span - sizeof(pymergetic_rxf_object);
    }

    mem::fill(record, 0, sizeof(pymergetic_rxf_object) + room);
    record->gen = gen;
    record->payload = (uint32_t)room;
    *offset = best;
    sync_free_head();
    return Status::Ok;
}

Status ObjectArena::s_release(uint64_t offset) {
    pymergetic_rxf_superblock *super = this->super();
    if (offset < PYMERGETIC_RXF_SUPER_SIZE || offset >= super->committed ||
        (offset & (PYMERGETIC_RXF_OBJECT_ALIGN - 1U)) != 0)
        return Status::BadImage;
    pymergetic_rxf_object *record = record_at(offset);
    if (record->kind == PYMERGETIC_RXF_OBJ_FREE)
        return Status::BadImage;
    const uint64_t span = span_of(record);
    if (span > super->committed - offset)
        return Status::BadImage;
    const uint16_t gen = record->gen;

    if (offset + span == super->committed) {
        /* Adjacent to the tail: the end marker grows downward over both spans. */
        const uint64_t old_marker = super->committed;
        const uint64_t marker_span = span_of(record_at(old_marker));
        const uint64_t prev = link_prev(links_at(old_marker));
        const uint64_t next = link_next(links_at(old_marker));
        super->committed = offset;
        mem::fill(record, 0,
                  (uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS);
        record->kind = PYMERGETIC_RXF_OBJ_FREE;
        record->gen = gen;
        record->payload = (uint32_t)(span + marker_span - sizeof(pymergetic_rxf_object));
        uint64_t *links = links_at(offset);
        if (prev == old_marker) {
            links[PYMERGETIC_RXF_FREE_PREV / 8] = offset;
            links[PYMERGETIC_RXF_FREE_NEXT / 8] = offset;
        } else {
            links[PYMERGETIC_RXF_FREE_PREV / 8] = prev;
            links[PYMERGETIC_RXF_FREE_NEXT / 8] = next;
            links_at(prev)[PYMERGETIC_RXF_FREE_NEXT / 8] = offset;
            links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8] = offset;
        }
        sync_free_head();
        return Status::Ok;
    }

    /* A ring span carries links (40 bytes or more); a smaller one stays linkless. */
    const uint64_t room = span - sizeof(pymergetic_rxf_object);
    mem::fill(memory_.base + offset + sizeof(pymergetic_rxf_object), 0,
              room < PYMERGETIC_RXF_FREE_LINKS ? room : PYMERGETIC_RXF_FREE_LINKS);
    record->kind = PYMERGETIC_RXF_OBJ_FREE;
    record->gen = gen;
    if (span >= PYMERGETIC_RXF_FREE_MIN_SPAN)
        ring_insert_after(marker_offset(), offset);
    sync_free_head();
    return Status::Ok;
}

Status ObjectArena::verify() const {
    const pymergetic_rxf_superblock *super = this->super();
    if (super->magic[0] != 'R' || super->magic[1] != 'X' || super->magic[2] != 'F' ||
        super->magic[3] != 'A' || super->version != PYMERGETIC_RXF_SUPER_VERSION)
        return Status::BadImage;
    if (super->committed < PYMERGETIC_RXF_SUPER_SIZE || super->limit < super->committed ||
        super->committed > memory_.limit ||
        ((super->committed - PYMERGETIC_RXF_SUPER_SIZE) & (PYMERGETIC_RXF_OBJECT_ALIGN - 1U)) != 0)
        return Status::BadImage;
    for (uint64_t i = 0; i < sizeof(super->reserved); i++) {
        if (super->reserved[i] != 0)
            return Status::BadImage;
    }

    /* The object stream packs back to back and ends exactly at the marker. */
    uint64_t offset = PYMERGETIC_RXF_SUPER_SIZE;
    uint64_t free_with_links = 0;
    while (offset < super->committed) {
        const pymergetic_rxf_object *record = record_at(offset);
        if ((record->payload & 7U) != 0)
            return Status::BadImage;
        const uint64_t span = span_of(record);
        if (span < sizeof(pymergetic_rxf_object) || span > super->committed - offset)
            return Status::BadImage;
        if (record->kind == PYMERGETIC_RXF_OBJ_FREE && span >= PYMERGETIC_RXF_FREE_MIN_SPAN)
            free_with_links++;
        offset += span;
    }
    if (offset != super->committed)
        return Status::BadImage;

    /* The end marker: the ring anchor that covers the tail. */
    const pymergetic_rxf_object *marker = record_at(super->committed);
    if (marker->kind != PYMERGETIC_RXF_OBJ_FREE ||
        span_of(marker) != super->limit - super->committed ||
        span_of(marker) < PYMERGETIC_RXF_FREE_MIN_SPAN)
        return Status::BadImage;

    /* The ring is circular, consistent, and holds every linkable free span. */
    uint64_t members = 0;
    uint64_t walk = super->committed;
    uint64_t guard = free_with_links + 2;
    while (guard-- > 0) {
        const pymergetic_rxf_object *record = record_at(walk);
        if (record->kind != PYMERGETIC_RXF_OBJ_FREE ||
            span_of(record) < PYMERGETIC_RXF_FREE_MIN_SPAN || walk < PYMERGETIC_RXF_SUPER_SIZE ||
            walk > super->committed || (walk & (PYMERGETIC_RXF_OBJECT_ALIGN - 1U)) != 0)
            return Status::BadImage;
        const uint64_t *links = links_at(walk);
        const uint64_t prev = link_prev(links);
        const uint64_t next = link_next(links);
        if (prev < PYMERGETIC_RXF_SUPER_SIZE || prev >= super->limit ||
            next < PYMERGETIC_RXF_SUPER_SIZE || next >= super->limit ||
            links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8] != walk ||
            links_at(prev)[PYMERGETIC_RXF_FREE_NEXT / 8] != walk)
            return Status::BadImage;
        members++;
        walk = next;
        if (walk == super->committed)
            return members == free_with_links + 1 ? Status::Ok : Status::BadImage;
    }
    return Status::BadImage;
}

/* ---- the object model (docs/32 2): the index, the tree, the names ---- */

/* The table is the index object's payload: the object header at
 * PYMERGETIC_RXF_INDEX_OFFSET carries the identity, the slots sit behind it. */
pymergetic_rxf_slots *ObjectArena::index_table() const {
    return reinterpret_cast<pymergetic_rxf_slots *>(
        static_cast<uint8_t *>(memory_.base) + PYMERGETIC_RXF_INDEX_OFFSET +
        sizeof(pymergetic_rxf_object));
}

pymergetic_rxf_slot *ObjectArena::index_entry(uint32_t index) const {
    auto *behind = reinterpret_cast<uint8_t *>(index_table()) + sizeof(pymergetic_rxf_slots);
    return reinterpret_cast<pymergetic_rxf_slot *>(behind) + index;
}

pymergetic_rxf_module *ObjectArena::module_of(pymergetic_rxf_object *object) const {
    return reinterpret_cast<pymergetic_rxf_module *>(reinterpret_cast<uint8_t *>(object) +
                                                     sizeof(pymergetic_rxf_object));
}

pymergetic_rxf_syms *ObjectArena::syms_of(pymergetic_rxf_object *object) const {
    return reinterpret_cast<pymergetic_rxf_syms *>(reinterpret_cast<uint8_t *>(object) +
                                                   sizeof(pymergetic_rxf_object));
}

/* The one anchor: the index is the first object of the arena and entry 0 is its
 * own identity. A region without that shape has no object model in it. */
static bool model_ready(const pymergetic_rxf_slots *slots) {
    if (slots->capacity == 0 || slots->capacity > (1u << PYMERGETIC_RXF_HANDLE32_INDEX_BITS) ||
        slots->count == 0 || slots->count > slots->capacity)
        return false;
    const auto *self = reinterpret_cast<const pymergetic_rxf_slot *>(slots + 1);
    return self->slot == PYMERGETIC_RXF_INDEX_OFFSET && self->kind == PYMERGETIC_RXF_OBJ_MODULE;
}

Status ObjectArena::syms_record(pymergetic_rxf_object **out) {
    const pymergetic_rxf_slot *entry = index_entry(PYMERGETIC_RXF_INDEX_SYMS);
    const uint32_t handle =
        pymergetic_rxf_handle32_pack(PYMERGETIC_RXF_INDEX_SYMS, entry->gen);
    return load(handle, out);
}

bool ObjectArena::symbol_id(const char *text, uint16_t *id) {
    pymergetic_rxf_object *record = nullptr;
    if (syms_record(&record) != Status::Ok)
        return false;
    const pymergetic_rxf_syms *head = syms_of(record);
    const uint8_t *payload = reinterpret_cast<const uint8_t *>(head);
    const auto *offsets = reinterpret_cast<const uint32_t *>(head + 1);
    const uint64_t want = mem::length(text);
    for (uint32_t i = 1; i < head->count; i++) {
        const char *held = reinterpret_cast<const char *>(payload + offsets[i]);
        if (mem::length(held) == want && mem::compare(held, text, want) == 0) {
            *id = static_cast<uint16_t>(i);
            return true;
        }
    }
    return false;
}

Status ObjectArena::append_child(uint32_t parent, uint32_t child) {
    if (pymergetic_rxf_handle32_index(parent) == PYMERGETIC_RXF_INDEX_SELF)
        return Status::Protected; /* its payload is the slot table, not a chain */
    pymergetic_rxf_object *module = nullptr;
    Status st = load(parent, &module);
    if (st != Status::Ok)
        return st;
    if (module->kind != PYMERGETIC_RXF_OBJ_MODULE)
        return Status::BadImage;
    pymergetic_rxf_module *head = module_of(module);
    if (head->children == PYMERGETIC_RXF_HANDLE32_NONE) {
        head->children = child;
    } else {
        uint32_t walk = head->children;
        for (;;) {
            pymergetic_rxf_object *sibling = nullptr;
            st = load(walk, &sibling);
            if (st != Status::Ok)
                return st;
            if (sibling->link == PYMERGETIC_RXF_HANDLE32_NONE) {
                sibling->link = child;
                break;
            }
            walk = sibling->link;
        }
    }
    head->count += 1;
    return Status::Ok;
}

Status ObjectArena::syms_relocate(uint32_t capacity, uint32_t room) {
    pymergetic_rxf_object *old_record = nullptr;
    Status st = syms_record(&old_record);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_syms *old = syms_of(old_record);
    const auto *old_payload = reinterpret_cast<const uint8_t *>(old);
    const auto *old_offsets = reinterpret_cast<const uint32_t *>(old + 1);
    const uint64_t old_at = index_entry(PYMERGETIC_RXF_INDEX_SYMS)->slot;

    uint64_t at = 0;
    st = s_allocate(room, &at);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_object *record = record_at(at);
    pymergetic_rxf_syms *head = syms_of(record);
    head->count = old->count;
    head->capacity = capacity;
    auto *offsets = reinterpret_cast<uint32_t *>(head + 1);
    uint8_t *payload = reinterpret_cast<uint8_t *>(head);
    uint64_t used = sizeof(pymergetic_rxf_syms) + (uint64_t)capacity * sizeof(uint32_t);
    for (uint32_t i = 1; i < old->count; i++) {
        const char *text = reinterpret_cast<const char *>(old_payload + old_offsets[i]);
        const uint64_t length = mem::length(text) + 1;
        mem::copy(payload + used, text, length);
        offsets[i] = static_cast<uint32_t>(used);
        used += length;
    }

    /* The identity moves to the new span: the handle stands (docs/32: callers
     * hold handles, nothing outside the arena knows addresses). */
    record->kind = PYMERGETIC_RXF_OBJ_SYM;
    record->gen = old_record->gen;
    record->flags = old_record->flags;
    record->name = old_record->name;
    record->parent = old_record->parent;
    record->link = old_record->link;
    record->refs = old_record->refs;
    pymergetic_rxf_slot *entry = index_entry(PYMERGETIC_RXF_INDEX_SYMS);
    entry->kind = record->kind;
    entry->gen = record->gen;
    entry->slot = at;

    st = s_release(old_at);
    if (st != Status::Ok)
        return st;
    sync_free_head();
    return Status::Ok;
}

void ObjectArena::sync_free_head() {
    if (free_handle_ == PYMERGETIC_RXF_HANDLE32_NONE)
        return;
    if (free_handle_ == 0) {
        uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
        uint32_t free_object = PYMERGETIC_RXF_HANDLE32_NONE;
        if (find(super()->root, "sys", &sys) != Status::Ok ||
            find(sys, "free", &free_object) != Status::Ok ||
            free_object == PYMERGETIC_RXF_HANDLE32_NONE)
            return;
        free_handle_ = free_object;
    }
    pymergetic_rxf_object *record = nullptr;
    if (load(free_handle_, &record) != Status::Ok)
        return;
    *reinterpret_cast<uint64_t *>(reinterpret_cast<uint8_t *>(record) +
                                  sizeof(pymergetic_rxf_object)) = marker_offset();
}

Status ObjectArena::bootstrap(const char *image_name, uint32_t capacity) {
    if (image_name == nullptr || image_name[0] == 0)
        return Status::BadName;
    if (capacity < 4 || capacity > (1u << PYMERGETIC_RXF_HANDLE32_INDEX_BITS))
        return Status::TooLarge;
    if (model_ready(index_table()))
        return Status::BadImage;

    /* The index: the first object of the arena, published at its own entry. */
    uint64_t at = 0;
    Status st =
        s_allocate(sizeof(pymergetic_rxf_slots) + (uint64_t)capacity * sizeof(pymergetic_rxf_slot),
                   &at);
    if (st != Status::Ok)
        return st;
    if (at != PYMERGETIC_RXF_INDEX_OFFSET)
        return Status::BadImage;
    pymergetic_rxf_object *record = record_at(at);
    record->kind = PYMERGETIC_RXF_OBJ_MODULE;
    record->parent = PYMERGETIC_RXF_HANDLE32_NONE;
    record->link = PYMERGETIC_RXF_HANDLE32_NONE;
    record->refs = 1;
    pymergetic_rxf_slots *slots = index_table();
    slots->count = 1;
    slots->capacity = capacity;
    pymergetic_rxf_slot *self = index_entry(PYMERGETIC_RXF_INDEX_SELF);
    self->kind = record->kind;
    self->gen = record->gen;
    self->reserved = 0;
    self->slot = at;

    /* sys/syms: the names the tree is walked by. */
    st = s_allocate(sizeof(pymergetic_rxf_syms) + 32 * sizeof(uint32_t) + 256, &at);
    if (st != Status::Ok)
        return st;
    record = record_at(at);
    record->kind = PYMERGETIC_RXF_OBJ_SYM;
    record->parent = PYMERGETIC_RXF_HANDLE32_NONE;
    record->link = PYMERGETIC_RXF_HANDLE32_NONE;
    record->refs = 1;
    pymergetic_rxf_syms *syms = syms_of(record);
    syms->count = 1; /* id 0 is anonymous and stored nowhere */
    syms->capacity = 32;
    pymergetic_rxf_slot *entry = index_entry(PYMERGETIC_RXF_INDEX_SYMS);
    entry->kind = record->kind;
    entry->gen = record->gen;
    entry->reserved = 0;
    entry->slot = at;
    slots->count = 2;
    const uint32_t syms_handle =
        pymergetic_rxf_handle32_pack(PYMERGETIC_RXF_INDEX_SYMS, record->gen);

    /* The tree: the root image, sys, and the system leaves. */
    uint32_t root = PYMERGETIC_RXF_HANDLE32_NONE;
    uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
    uint32_t made = PYMERGETIC_RXF_HANDLE32_NONE;
    st = create(PYMERGETIC_RXF_HANDLE32_NONE, image_name, PYMERGETIC_RXF_OBJ_MODULE,
                sizeof(pymergetic_rxf_module), &root);
    if (st != Status::Ok)
        return st;
    super()->root = root;
    if (load(root, &record) != Status::Ok)
        return Status::BadImage;
    record->flags |= PYMERGETIC_RXF_OFLAG_ROOT;
    st = create(root, "sys", PYMERGETIC_RXF_OBJ_MODULE, sizeof(pymergetic_rxf_module), &sys);
    if (st != Status::Ok)
        return st;

    /* objects/ first: the index object itself moves into the tree. */
    const uint32_t index_handle =
        pymergetic_rxf_handle32_pack(PYMERGETIC_RXF_INDEX_SELF, self->gen);
    if (load(index_handle, &record) != Status::Ok)
        return Status::BadImage;
    record->parent = sys;
    st = append_child(sys, index_handle);
    if (st != Status::Ok)
        return st;
    st = intern("objects", &record->name);
    if (st != Status::Ok)
        return st;

    static const char *const leaves[] = {"functions", "journal", "caps"};
    for (const char *leaf : leaves) {
        st = create(sys, leaf, PYMERGETIC_RXF_OBJ_MODULE, sizeof(pymergetic_rxf_module), &made);
        if (st != Status::Ok)
            return st;
    }
    st = create(sys, "free", PYMERGETIC_RXF_OBJ_BLOB, sizeof(uint64_t), &free_handle_);
    if (st != Status::Ok)
        return st;

    /* syms last: the tree order of docs/32 2 is objects, functions, journal,
     * caps, free, syms. */
    if (load(syms_handle, &record) != Status::Ok)
        return Status::BadImage;
    record->parent = sys;
    st = append_child(sys, syms_handle);
    if (st != Status::Ok)
        return st;
    st = intern("syms", &record->name);
    if (st != Status::Ok)
        return st;

    sync_free_head();
    return Status::Ok;
}

Status ObjectArena::create(uint32_t parent, const char *name, uint16_t kind, uint64_t room,
                           uint32_t *handle) {
    if (room > 0xFFFFFFFFu - sizeof(pymergetic_rxf_object))
        return Status::TooLarge;
    if (parent != PYMERGETIC_RXF_HANDLE32_NONE &&
        pymergetic_rxf_handle32_index(parent) == PYMERGETIC_RXF_INDEX_SELF)
        return Status::Protected; /* its payload is the slot table, not a chain */
    pymergetic_rxf_slots *slots = index_table();
    if (!model_ready(slots))
        return Status::BadImage;
    if (slots->count >= slots->capacity)
        return Status::IndexFull;
    uint16_t name_id = PYMERGETIC_RXF_SYMS_ANONYMOUS;
    if (name != nullptr) {
        Status st = intern(name, &name_id);
        if (st != Status::Ok)
            return st;
    }

    uint64_t at = 0;
    Status st = s_allocate(room, &at);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_object *record = record_at(at);
    record->kind = kind;
    record->name = name_id;
    record->parent = parent;
    record->link = PYMERGETIC_RXF_HANDLE32_NONE;
    record->refs = 1;
    if (kind == PYMERGETIC_RXF_OBJ_MODULE) {
        /* A zeroed payload would read children = 0, which is the index object's
         * handle: the empty chain spells HANDLE32_NONE (docs/32 1.2). */
        pymergetic_rxf_module *module = module_of(record);
        module->children = PYMERGETIC_RXF_HANDLE32_NONE;
        module->count = 0;
    }

    /* Index selection: a freed entry is handed out again, and its generation is
     * taken from the fresh record, so stale handles of its previous life keep
     * failing (docs/32 1.2: every write bumps gen). */
    uint32_t index = slots->count;
    for (uint32_t i = 0; i < slots->count; ++i) {
        if (index_entry(i)->slot == 0 && index_entry(i)->kind == 0) {
            index = i;
            break;
        }
    }
    pymergetic_rxf_slot *entry = index_entry(index);
    entry->kind = kind;
    entry->gen = record->gen;
    entry->reserved = 0;
    entry->slot = at;
    if (index == slots->count)
        slots->count = index + 1;
    *handle = pymergetic_rxf_handle32_pack(index, record->gen);

    if (parent != PYMERGETIC_RXF_HANDLE32_NONE) {
        st = append_child(parent, *handle);
        if (st != Status::Ok)
            return st;
    }
    return Status::Ok;
}

Status ObjectArena::find(uint32_t parent, const char *name, uint32_t *handle) {
    *handle = PYMERGETIC_RXF_HANDLE32_NONE;
    if (name == nullptr || name[0] == 0)
        return Status::BadName;
    if (pymergetic_rxf_handle32_index(parent) == PYMERGETIC_RXF_INDEX_SELF)
        return Status::Protected; /* its payload is the slot table, not a chain */
    uint16_t id = PYMERGETIC_RXF_SYMS_ANONYMOUS;
    if (!symbol_id(name, &id))
        return Status::Ok; /* nothing interned it, so no child carries it */
    pymergetic_rxf_object *module = nullptr;
    Status st = load(parent, &module);
    if (st != Status::Ok)
        return st;
    if (module->kind != PYMERGETIC_RXF_OBJ_MODULE)
        return Status::BadImage;
    uint32_t walk = module_of(module)->children;
    while (walk != PYMERGETIC_RXF_HANDLE32_NONE) {
        pymergetic_rxf_object *child = nullptr;
        st = load(walk, &child);
        if (st != Status::Ok)
            return st;
        if (child->name == id) {
            *handle = walk;
            return Status::Ok;
        }
        walk = child->link;
    }
    return Status::Ok;
}

Status ObjectArena::load(uint32_t handle, pymergetic_rxf_object **out) {
    if (handle == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::BadHandle;
    const pymergetic_rxf_slots *slots = index_table();
    if (!model_ready(slots))
        return Status::BadImage;
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    const uint32_t gen = pymergetic_rxf_handle32_gen(handle);
    if (index >= slots->count)
        return Status::BadHandle;
    const pymergetic_rxf_slot *entry = index_entry(index);
    if (entry->slot == 0 || entry->gen != gen || entry->slot >= super()->committed)
        return Status::BadHandle;
    pymergetic_rxf_object *record = record_at(entry->slot);
    if (record->gen != gen || record->kind != entry->kind)
        return Status::BadHandle;
    *out = record;
    return Status::Ok;
}

Status ObjectArena::release_handle(uint32_t handle) {
    if (handle == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok; /* releasing nothing is a no-op */
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    if (handle == super()->root || index == PYMERGETIC_RXF_INDEX_SELF ||
        index == PYMERGETIC_RXF_INDEX_SYMS)
        return Status::Protected;
    if (record->kind == PYMERGETIC_RXF_OBJ_MODULE &&
        module_of(record)->children != PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::HasChildren;
    /* Unlink from the parent's children chain first: a walk must never meet a
     * dead link (docs/32 1.2 release semantics). */
    if (record->parent != PYMERGETIC_RXF_HANDLE32_NONE) {
        pymergetic_rxf_object *parent_record = nullptr;
        if (load(record->parent, &parent_record) == Status::Ok &&
            parent_record->kind == PYMERGETIC_RXF_OBJ_MODULE) {
            pymergetic_rxf_module *module = module_of(parent_record);
            uint32_t prev = PYMERGETIC_RXF_HANDLE32_NONE;
            uint32_t cur = module->children;
            while (cur != PYMERGETIC_RXF_HANDLE32_NONE) {
                pymergetic_rxf_object *child = nullptr;
                if (load(cur, &child) != Status::Ok)
                    break;
                const uint32_t next = child->link;
                if (cur == handle) {
                    if (prev == PYMERGETIC_RXF_HANDLE32_NONE)
                        module->children = next;
                    else {
                        pymergetic_rxf_object *before = nullptr;
                        if (load(prev, &before) == Status::Ok)
                            before->link = next;
                    }
                    if (module->count > 0)
                        module->count--;
                    break;
                }
                prev = cur;
                cur = next;
            }
        }
    }
    const uint64_t at = index_entry(index)->slot;
    st = s_release(at);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_slot *entry = index_entry(index);
    entry->kind = 0;
    entry->slot = 0; /* the entry is free again; a new object reuses it under a new gen */
    if (free_handle_ == handle)
        free_handle_ = 0;
    sync_free_head();
    return Status::Ok;
}

Status ObjectArena::intern(const char *text, uint16_t *id) {
    if (text == nullptr || text[0] == 0)
        return Status::BadName;
    if (symbol_id(text, id))
        return Status::Ok;

    pymergetic_rxf_object *record = nullptr;
    Status st = syms_record(&record);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_syms *head = syms_of(record);
    if (head->count >= 0xFFFFu)
        return Status::TooLarge;
    uint8_t *payload = reinterpret_cast<uint8_t *>(head);
    auto *offsets = reinterpret_cast<uint32_t *>(head + 1);
    const uint64_t need = mem::length(text) + 1;
    uint64_t used = sizeof(pymergetic_rxf_syms) + (uint64_t)head->capacity * sizeof(uint32_t);
    if (head->count > 1) {
        const uint32_t last = offsets[head->count - 1];
        used = last + mem::length(reinterpret_cast<const char *>(payload + last)) + 1;
    }

    if (head->count >= head->capacity || used + need > record->payload) {
        uint32_t capacity = head->capacity * 2;
        if (head->count + 1u > capacity)
            capacity = head->count + 1u; /* ids 1..count-1 plus the reserved 0 */
        uint64_t room = used + need;
        if (room < (uint64_t)record->payload * 2)
            room = (uint64_t)record->payload * 2;
        st = syms_relocate(capacity, static_cast<uint32_t>(room));
        if (st != Status::Ok)
            return st;
        st = syms_record(&record);
        if (st != Status::Ok)
            return st;
        head = syms_of(record);
        payload = reinterpret_cast<uint8_t *>(head);
        offsets = reinterpret_cast<uint32_t *>(head + 1);
        used = sizeof(pymergetic_rxf_syms) + (uint64_t)head->capacity * sizeof(uint32_t);
        if (head->count > 1) {
            const uint32_t last = offsets[head->count - 1];
            used = last + mem::length(reinterpret_cast<const char *>(payload + last)) + 1;
        }
    }

    mem::copy(payload + used, text, need);
    offsets[head->count] = static_cast<uint32_t>(used);
    *id = static_cast<uint16_t>(head->count);
    head->count += 1;
    return Status::Ok;
}

Status ObjectArena::symbol(uint16_t id, const char **text, uint32_t *length) {
    if (id == PYMERGETIC_RXF_SYMS_ANONYMOUS)
        return Status::BadName;
    pymergetic_rxf_object *record = nullptr;
    Status st = syms_record(&record);
    if (st != Status::Ok)
        return st;
    const pymergetic_rxf_syms *head = syms_of(record);
    if (id == 0 || id >= head->count)
        return Status::BadName;
    const uint8_t *payload = reinterpret_cast<const uint8_t *>(head);
    const auto *offsets = reinterpret_cast<const uint32_t *>(head + 1);
    const char *held = reinterpret_cast<const char *>(payload + offsets[id]);
    *text = held;
    *length = static_cast<uint32_t>(mem::length(held));
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::arena
