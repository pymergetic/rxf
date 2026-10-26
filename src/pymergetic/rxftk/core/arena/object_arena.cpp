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

} // namespace pymergetic::rxf::core::arena
