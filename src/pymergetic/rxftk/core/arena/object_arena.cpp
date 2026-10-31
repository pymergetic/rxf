#include <pymergetic/rxftk/core/arena/mem.hpp>
#include <pymergetic/rxftk/core/arena/object_arena.hpp>
#include <pymergetic/rxftk/core/ground.h>

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

/* Worst-case undo granules of one write sequence that caches ring state between
 * its reads and its writes (docs/32 3, step 4). Each mutation reserves its budget
 * before the first read, so the log cannot relocate mid-sequence and leave the
 * mutation pointing at spans the ring has moved on from. */
constexpr uint32_t kReserveAllocate = 32;
constexpr uint32_t kReserveRelease = 32;
constexpr uint32_t kReserveRing = 8;

/* docs/32 5.4 step 2: the first index the bootstrap does not make (the index,
 * sys/syms, the root image, sys and its five leaves fill 0..8). */
constexpr uint32_t kWalkFirst = 9;
/* The capacity of a fresh arena is the world's survey plus the writer's
 * headroom (tools/native/arena_export.py: objects + tables + INDEX_HEADROOM).
 * The survey is the live slots minus the arena's own scaffolding - the
 * bootstrap's ten and the founding core of docs/36 (its tree and its tables)
 * - so the headroom a rebuild needs is the writer's 256 minus that 36. */
constexpr uint32_t kIndexHeadroom = 220;
/* The tree walk is iterative (the engine has no recursion): the stack holds
 * one entry per open module - its last placed child name. */
constexpr uint32_t kWalkDepth = 32;

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
    specs_handle_ = 0;
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
    specs_handle_ = 0;
    const pymergetic_rxf_superblock *super = this->super();
    if (super->magic[0] != 'R' || super->magic[1] != 'X' || super->magic[2] != 'F' ||
        super->magic[3] != 'A' || super->version != PYMERGETIC_RXF_SUPER_VERSION)
        return Status::BadImage;
    /* A truncated image (committed or its tail marker beyond what the seat
     * materialized) is never trusted. */
    const uint64_t foot = sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS;
    if (super->limit > memory.limit || super->committed < PYMERGETIC_RXF_SUPER_SIZE ||
        super->committed > super->limit || super->committed + foot > memory.committed ||
        super->committed + foot > super->limit)
        return Status::BadImage;
    /* The journal leaf is re-derived from the module tree (sys/journal): from here on
     * every write is recorded again and an attached image can roll back to its last
     * commit (docs/32 3). An image without that leaf simply has no undo ring. */
    uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
    if (find(super->root, "sys", &sys) == Status::Ok) {
        uint32_t journal = PYMERGETIC_RXF_HANDLE32_NONE;
        if (find(sys, "journal", &journal) == Status::Ok)
            return journal_attach(journal);
    }
    return Status::Ok;
}

Status ObjectArena::image(uint8_t *out, uint64_t capacity, uint64_t *written) const {
    if (written == nullptr)
        return Status::BadImage;
    *written = 0;
    if (memory_.base == nullptr)
        return Status::BadImage;
    const pymergetic_rxf_superblock *super = this->super();
    const uint64_t committed = super->committed;
    /* The tail marker sits at the committed boundary and its ring links are live
     * allocator state, so the image carries those bytes too: a loader's verify()
     * finds them where it expects them. */
    const uint64_t foot = sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS;
    if (committed < PYMERGETIC_RXF_SUPER_SIZE || committed + foot > memory_.limit)
        return Status::BadImage;
    const uint64_t end = committed + foot;
    if (out == nullptr || capacity < end) {
        *written = end;
        return Status::OutOfMemory;
    }
    mem::copy(out, memory_.base, end);
    *written = end;
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

Status ObjectArena::ring_unlink(uint64_t offset) {
    Status st = journal_reserve(kReserveRing);
    if (st != Status::Ok)
        return st;
    uint64_t *links = links_at(offset);
    const uint64_t prev = link_prev(links);
    const uint64_t next = link_next(links);
    st = j_set(&links_at(prev)[PYMERGETIC_RXF_FREE_NEXT / 8], next);
    if (st != Status::Ok)
        return st;
    return j_set(&links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8], prev);
}

Status ObjectArena::ring_insert_after(uint64_t anchor, uint64_t offset) {
    Status st = journal_reserve(kReserveRing);
    if (st != Status::Ok)
        return st;
    uint64_t *links = links_at(offset);
    uint64_t *anchor_links = links_at(anchor);
    const uint64_t next = link_next(anchor_links);
    st = j_set(&links[PYMERGETIC_RXF_FREE_PREV / 8], anchor);
    if (st != Status::Ok)
        return st;
    st = j_set(&links[PYMERGETIC_RXF_FREE_NEXT / 8], next);
    if (st != Status::Ok)
        return st;
    st = j_set(&links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8], offset);
    if (st != Status::Ok)
        return st;
    return j_set(&anchor_links[PYMERGETIC_RXF_FREE_NEXT / 8], offset);
}

Status ObjectArena::s_allocate(uint64_t room, uint64_t *offset, bool absorb) {
    if (offset == nullptr || room > kMaxPayload)
        return Status::TooLarge;
    Status reserve = journal_reserve(kReserveAllocate);
    if (reserve != Status::Ok)
        return reserve;
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
        /* A raw grant takes a span whole when its rest cannot carry ring links and
         * absorbs the slack (docs/32 1.2). An object's room is its request - the
         * canonical form reads the room back as the object's size (docs/32 5.4) -
         * so there such a span is no candidate at all: the absorbed slack would
         * make the grant, and the dump that reads it, depend on the hole pattern
         * of the moment. The rest that is left free stays free; it still serves
         * the exact fit it was shaped for. */
        const bool clean = span == total || span - total >= PYMERGETIC_RXF_FREE_MIN_SPAN;
        if (span >= total && (!is_marker || span >= total + PYMERGETIC_RXF_FREE_MIN_SPAN) &&
            (absorb || clean) && (!found || span < best_span)) {
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
    const uint64_t write_span =
        split ? total + sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS : best_span;
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
        Status st = j_fill(record_at(rest), 0,
                           (uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS);
        if (st != Status::Ok)
            return st;
        pymergetic_rxf_object *rest_record = record_at(rest);
        st = j_set(&rest_record->kind, (uint16_t)PYMERGETIC_RXF_OBJ_FREE);
        if (st != Status::Ok)
            return st;
        st = j_set(&rest_record->payload,
                   (uint32_t)(best_span - total - sizeof(pymergetic_rxf_object)));
        if (st != Status::Ok)
            return st;
        st = j_set(&links_at(rest)[PYMERGETIC_RXF_FREE_PREV / 8], prev_home);
        if (st != Status::Ok)
            return st;
        st = j_set(&links_at(rest)[PYMERGETIC_RXF_FREE_NEXT / 8], next_home);
        if (st != Status::Ok)
            return st;
        if (prev_home != rest) {
            st = j_set(&links_at(prev_home)[PYMERGETIC_RXF_FREE_NEXT / 8], rest);
            if (st != Status::Ok)
                return st;
        }
        if (next_home != rest) {
            st = j_set(&links_at(next_home)[PYMERGETIC_RXF_FREE_PREV / 8], rest);
            if (st != Status::Ok)
                return st;
        }
        if (best_is_marker) {
            st = j_set(&super()->committed, rest);
            if (st != Status::Ok)
                return st;
        }
    } else {
        /* Taken whole: the granted room is the span minus the header. */
        if (best_is_marker)
            return Status::BadImage; /* refused by the search above */
        Status st = ring_unlink(best);
        if (st != Status::Ok)
            return st;
        room = best_span - sizeof(pymergetic_rxf_object);
    }

    /* The record header and the first payload bytes (the span's bookkeeping:
     * header, links) are undone like every write; the payload interior below
     * is the fresh grant. */
    const uint64_t bookkeeping =
        sizeof(pymergetic_rxf_object) +
        (room < PYMERGETIC_RXF_FREE_LINKS ? room : PYMERGETIC_RXF_FREE_LINKS);
    status = j_fill(record, 0, bookkeeping);
    if (status != Status::Ok)
        return status;
    /* The fresh payload interior (the grant past the bookkeeping - the same
     * coverage the plain fill always gave, FREE_LINKS bytes in and room minus
     * FREE_LINKS wide) is part of the transaction when the span came from the
     * ring: a refused apply would otherwise leave the previous occupant's
     * bytes zeroed behind and the rollback could not put them back (the
     * refusal the tests hold to: a refused delta leaves no trace). Its
     * granules are reserved here, after the ring mutations and before the
     * first interior byte, so the log cannot relocate mid-fill. A span taken
     * from the end marker keeps the plain fill: its interior lies beyond the
     * committed boundary of the last commit, so no snapshot ever saw those
     * bytes - and the plain fill keeps the undo ring's growth, and with it the
     * ring's own relocation moment, exactly where an allocation history the
     * canonical form pins had it. While the log itself relocates
     * (journal_growing_) the fill stays plain too: its bytes are the new
     * span's own content, parked-exempt like the rest of the move. */
    if (room > PYMERGETIC_RXF_FREE_LINKS) {
        const uint64_t interior = room - PYMERGETIC_RXF_FREE_LINKS;
        uint8_t *at = reinterpret_cast<uint8_t *>(record) + sizeof(pymergetic_rxf_object) +
                      PYMERGETIC_RXF_FREE_LINKS;
        if (!best_is_marker && !journal_growing_) {
            status = journal_reserve(uint32_t((interior + PYMERGETIC_RXF_JOURNAL_GRANULE - 1) /
                                              PYMERGETIC_RXF_JOURNAL_GRANULE));
            if (status != Status::Ok)
                return status;
            status = j_fill(at, 0, interior);
            if (status != Status::Ok)
                return status;
        } else {
            mem::fill(at, 0, interior);
        }
    }
    status = j_set(&record->gen, gen);
    if (status != Status::Ok)
        return status;
    status = j_set(&record->payload, (uint32_t)room);
    if (status != Status::Ok)
        return status;
    *offset = best;
    sync_free_head();
    return Status::Ok;
}

Status ObjectArena::s_release(uint64_t offset) {
    pymergetic_rxf_superblock *super = this->super();
    if (offset < PYMERGETIC_RXF_SUPER_SIZE || offset >= super->committed ||
        (offset & (PYMERGETIC_RXF_OBJECT_ALIGN - 1U)) != 0)
        return Status::BadImage;
    Status reserve = journal_reserve(kReserveRelease);
    if (reserve != Status::Ok)
        return reserve;
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
        Status st = j_set(&super->committed, offset);
        if (st != Status::Ok)
            return st;
        st = j_fill(record, 0, (uint64_t)sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS);
        if (st != Status::Ok)
            return st;
        st = j_set(&record->kind, (uint16_t)PYMERGETIC_RXF_OBJ_FREE);
        if (st != Status::Ok)
            return st;
        st = j_set(&record->gen, gen);
        if (st != Status::Ok)
            return st;
        st =
            j_set(&record->payload, (uint32_t)(span + marker_span - sizeof(pymergetic_rxf_object)));
        if (st != Status::Ok)
            return st;
        uint64_t *links = links_at(offset);
        if (prev == old_marker) {
            st = j_set(&links[PYMERGETIC_RXF_FREE_PREV / 8], offset);
            if (st != Status::Ok)
                return st;
            st = j_set(&links[PYMERGETIC_RXF_FREE_NEXT / 8], offset);
            if (st != Status::Ok)
                return st;
        } else {
            st = j_set(&links[PYMERGETIC_RXF_FREE_PREV / 8], prev);
            if (st != Status::Ok)
                return st;
            st = j_set(&links[PYMERGETIC_RXF_FREE_NEXT / 8], next);
            if (st != Status::Ok)
                return st;
            st = j_set(&links_at(prev)[PYMERGETIC_RXF_FREE_NEXT / 8], offset);
            if (st != Status::Ok)
                return st;
            st = j_set(&links_at(next)[PYMERGETIC_RXF_FREE_PREV / 8], offset);
            if (st != Status::Ok)
                return st;
        }
        sync_free_head();
        return Status::Ok;
    }

    /* A ring span carries links (40 bytes or more); a smaller one stays linkless. */
    const uint64_t room = span - sizeof(pymergetic_rxf_object);
    Status st = j_fill(memory_.base + offset + sizeof(pymergetic_rxf_object), 0,
                       room < PYMERGETIC_RXF_FREE_LINKS ? room : PYMERGETIC_RXF_FREE_LINKS);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->kind, (uint16_t)PYMERGETIC_RXF_OBJ_FREE);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->gen, gen);
    if (st != Status::Ok)
        return st;
    if (span >= PYMERGETIC_RXF_FREE_MIN_SPAN) {
        st = ring_insert_after(marker_offset(), offset);
        if (st != Status::Ok)
            return st;
    }
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
    return reinterpret_cast<pymergetic_rxf_slots *>(static_cast<uint8_t *>(memory_.base) +
                                                    PYMERGETIC_RXF_INDEX_OFFSET +
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
    const uint32_t handle = pymergetic_rxf_handle32_pack(PYMERGETIC_RXF_INDEX_SYMS, entry->gen);
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
        st = j_set(&head->children, child);
        if (st != Status::Ok)
            return st;
    } else {
        uint32_t walk = head->children;
        for (;;) {
            pymergetic_rxf_object *sibling = nullptr;
            st = load(walk, &sibling);
            if (st != Status::Ok)
                return st;
            if (sibling->link == PYMERGETIC_RXF_HANDLE32_NONE) {
                st = j_set(&sibling->link, child);
                if (st != Status::Ok)
                    return st;
                break;
            }
            walk = sibling->link;
        }
    }
    return j_set(&head->count, head->count + 1);
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
    st = j_set(&head->count, old->count);
    if (st != Status::Ok)
        return st;
    st = j_set(&head->capacity, capacity);
    if (st != Status::Ok)
        return st;
    auto *offsets = reinterpret_cast<uint32_t *>(head + 1);
    uint8_t *payload = reinterpret_cast<uint8_t *>(head);
    uint64_t used = sizeof(pymergetic_rxf_syms) + (uint64_t)capacity * sizeof(uint32_t);
    for (uint32_t i = 1; i < old->count; i++) {
        const char *text = reinterpret_cast<const char *>(old_payload + old_offsets[i]);
        const uint64_t length = mem::length(text) + 1;
        st = j_copy(payload + used, text, length);
        if (st != Status::Ok)
            return st;
        st = j_set(&offsets[i], static_cast<uint32_t>(used));
        if (st != Status::Ok)
            return st;
        used += length;
    }

    /* The identity moves to the new span: the handle stands (docs/32: callers
     * hold handles, nothing outside the arena knows addresses). */
    st = j_set(&record->kind, (uint16_t)PYMERGETIC_RXF_OBJ_SYM);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->gen, old_record->gen);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->flags, old_record->flags);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->name, old_record->name);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->parent, old_record->parent);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->link, old_record->link);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->refs, old_record->refs);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_slot *entry = index_entry(PYMERGETIC_RXF_INDEX_SYMS);
    st = j_set(&entry->kind, record->kind);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->gen, record->gen);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->slot, at);
    if (st != Status::Ok)
        return st;

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
    /* A derived mirror: the store goes through the journal like every write. */
    (void)j_set(reinterpret_cast<uint64_t *>(reinterpret_cast<uint8_t *>(record) +
                                             sizeof(pymergetic_rxf_object)),
                marker_offset());
}

/* The rows of sys/specs (docs/32 5). The handle is looked up by name once - a cold
 * attach has nothing cached - and kept; the object itself is loaded every time, so
 * a stale or moved table is never trusted. */
void ObjectArena::set_entry(uint32_t handle) {
    super()->entry = handle;
}

uint32_t ObjectArena::entry() const {
    return super()->entry;
}

Status ObjectArena::spec_rows(pymergetic_rxf_spec **rows, uint32_t *count) {
    if (specs_handle_ == 0) {
        uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
        uint32_t found = PYMERGETIC_RXF_HANDLE32_NONE;
        const uint32_t root = super()->root;
        if (find(root, "sys", &sys) != Status::Ok || sys == PYMERGETIC_RXF_HANDLE32_NONE)
            return Status::BadImage;
        if (find(sys, "specs", &found) != Status::Ok || found == PYMERGETIC_RXF_HANDLE32_NONE)
            return Status::BadImage;
        specs_handle_ = found;
    }
    pymergetic_rxf_object *table = nullptr;
    Status st = load(specs_handle_, &table);
    if (st != Status::Ok)
        return st;
    const uint8_t *base = nullptr;
    uint32_t size = 0;
    st = payload(specs_handle_, &base, &size);
    if (st != Status::Ok)
        return st;
    *rows = reinterpret_cast<pymergetic_rxf_spec *>(const_cast<uint8_t *>(base));
    *count = table->payload / sizeof(pymergetic_rxf_spec);
    return Status::Ok;
}

/* One object placed into the output: the writer's create (the name, the
 * storage role, the payload room) with the module head left to the appends
 * (tools/native/arena_export.py's EMPTY_MODULE_HEAD), and the source's handle
 * mapped to the output's in `remap`. */
bool ObjectArena::place_one(ObjectArena &out, uint32_t source, uint32_t *remap,
                            uint32_t remap_count) {
    const uint32_t index = pymergetic_rxf_handle32_index(source);
    if (index >= remap_count || remap[index] != PYMERGETIC_RXF_HANDLE32_NONE)
        return false;
    pymergetic_rxf_object *record = nullptr;
    if (load(source, &record) != Status::Ok)
        return false;
    const char *name = nullptr;
    uint32_t length = 0;
    if (record->name != PYMERGETIC_RXF_SYMS_ANONYMOUS &&
        symbol(record->name, &name, &length) != Status::Ok)
        return false;
    const uint32_t parent = record->parent == PYMERGETIC_RXF_HANDLE32_NONE
                                ? PYMERGETIC_RXF_HANDLE32_NONE
                                : remap[pymergetic_rxf_handle32_index(record->parent)];
    /* The writer's placement (tools/native/arena_export.py's _place): a node
     * with children is a Module, a childless one a leaf in the storage role of
     * its world kind (the LEAF_KIND map of the semantic row's flags). A module
     * that lost its last child still carries its chain head - the head is
     * arena bookkeeping, not the object's own - so the walk reads the children
     * the source has, and a chain that emptied demotes here (docs/32 1.2, the
     * mirror of into_module). A scaffold module (row kind 0) keeps its head. */
    bool module = record->kind == PYMERGETIC_RXF_OBJ_MODULE;
    uint16_t kind = record->kind;
    if (module) {
        const bool empty_chain = module_of(record)->children == PYMERGETIC_RXF_HANDLE32_NONE;
        pymergetic_rxf_spec row{};
        if (spec(source, &row) != Status::Ok)
            return false;
        const uint32_t node = row.flags & PYMERGETIC_RXF_FLAG_KIND_MASK;
        if (empty_chain && node != 0) {
            module = false;
            kind = PYMERGETIC_RXF_OBJ_BLOB;
            switch (node) {
            case PYMERGETIC_RXF_NODE_FN:
            case PYMERGETIC_RXF_NODE_CODE:
                kind = PYMERGETIC_RXF_OBJ_FUNC;
                break;
            case PYMERGETIC_RXF_NODE_TYPE:
            case PYMERGETIC_RXF_NODE_FIELD:
            case PYMERGETIC_RXF_NODE_ENUMERATOR:
                kind = PYMERGETIC_RXF_OBJ_SPEC;
                break;
            case PYMERGETIC_RXF_NODE_IMPORT:
            case PYMERGETIC_RXF_NODE_EXPORT:
            case PYMERGETIC_RXF_NODE_ALIAS:
                kind = PYMERGETIC_RXF_OBJ_SYM;
                break;
            default:
                break;
            }
        }
    }
    uint32_t handle = PYMERGETIC_RXF_HANDLE32_NONE;
    /* A module's room carries its chain head; a demoted leaf's is the value
     * alone (the head stays in the source); a leaf's room is its own. */
    const bool demoted = !module && record->kind == PYMERGETIC_RXF_OBJ_MODULE;
    const uint64_t room = module ? record->payload
                                 : (demoted ? (record->payload > sizeof(pymergetic_rxf_module)
                                                   ? record->payload - sizeof(pymergetic_rxf_module)
                                                   : 0)
                                            : record->payload);
    const Status placed = out.create(parent, name, kind, room, &handle);
    if (placed != Status::Ok)
        return false;
    if (record->payload != 0) {
        const uint8_t *bytes = nullptr;
        uint32_t size = 0;
        if (payload(source, &bytes, &size) != Status::Ok)
            return false;
        /* A module's first bytes are its chain head (children, count): the
         * appends own it, and the writer writes it empty. The object's own
         * data is what follows the head - whether the output takes it as the
         * module it is or the leaf a demoted empty chain becomes (the leaf has
         * no head, so its value lands at 0). */
        const uint64_t skip =
            record->kind == PYMERGETIC_RXF_OBJ_MODULE && size >= sizeof(pymergetic_rxf_module)
                ? sizeof(pymergetic_rxf_module)
                : 0;
        const uint64_t dest =
            module && size >= sizeof(pymergetic_rxf_module) ? sizeof(pymergetic_rxf_module) : 0;
        if (skip > size ||
            out.set_payload_at(handle, dest, bytes + skip, size - skip) != Status::Ok)
            return false;
    }
    if (out.set_flags(handle, record->flags) != Status::Ok)
        return false;
    remap[index] = handle;
    return true;
}

/* The children of a module in name order (docs/32 5.4: "children by name").
 * The chain is the creation order - the order an apply appends at its end -
 * so the walk picks the smallest name the walk has not placed yet. */
bool ObjectArena::next_child(uint32_t parent, uint16_t after, uint32_t *next) {
    pymergetic_rxf_object *module = nullptr;
    if (load(parent, &module) != Status::Ok || module->kind != PYMERGETIC_RXF_OBJ_MODULE)
        return false;
    const char *after_text = nullptr;
    uint32_t after_length = 0;
    if (after != 0xFFFF && symbol(after, &after_text, &after_length) != Status::Ok)
        return false;
    uint32_t best = PYMERGETIC_RXF_HANDLE32_NONE;
    const char *best_text = nullptr;
    uint32_t best_length = 0;
    uint32_t walk = module_of(module)->children;
    while (walk != PYMERGETIC_RXF_HANDLE32_NONE) {
        const uint32_t candidate = walk;
        pymergetic_rxf_object *kid = nullptr;
        if (load(walk, &kid) != Status::Ok)
            return false;
        walk = kid->link;
        const char *text = nullptr;
        uint32_t length = 0;
        if (kid->name == PYMERGETIC_RXF_SYMS_ANONYMOUS)
            continue;
        if (symbol(kid->name, &text, &length) != Status::Ok)
            return false;
        if (after != 0xFFFF) {
            const uint32_t common = length < after_length ? length : after_length;
            const int diff = mem::compare(text, after_text, common);
            const bool beyond = diff > 0 || (diff == 0 && length > after_length);
            if (!beyond)
                continue;
        }
        if (best == PYMERGETIC_RXF_HANDLE32_NONE ||
            name_less(text, length, best_text, best_length)) {
            best = candidate;
            best_text = text;
            best_length = length;
        }
    }
    *next = best;
    return true;
}

/* The name order (the writer's children: docs/32 5.4 "children by name"). */
bool ObjectArena::name_less(const char *a, uint32_t an, const char *b, uint32_t bn) {
    const uint32_t common = an < bn ? an : bn;
    const int diff = mem::compare(a, b, common);
    return diff < 0 || (diff == 0 && an < bn);
}

/* The name id of a placed object (the sorted walk's cursor). */
bool ObjectArena::name_of(uint32_t handle, uint16_t *name) {
    pymergetic_rxf_object *record = nullptr;
    if (load(handle, &record) != Status::Ok)
        return false;
    *name = record->name;
    return true;
}

/* Whether a relation table belongs to the genesis (its name is the object's
 * path: the genesis's tables open with "honors."). The writer's _genesis
 * appends them after the genesis tree, so the walk keeps them there. */
bool ObjectArena::genesis_table(uint32_t source) {
    pymergetic_rxf_object *record = nullptr;
    if (load(source, &record) != Status::Ok || record->name == PYMERGETIC_RXF_SYMS_ANONYMOUS)
        return false;
    const char *text = nullptr;
    uint32_t length = 0;
    if (symbol(record->name, &text, &length) != Status::Ok)
        return false;
    static const char kGenesis[] = {'h', 'o', 'n', 'o', 'r', 's', '.'};
    return length >= sizeof(kGenesis) && mem::compare(text, kGenesis, sizeof(kGenesis)) == 0;
}

/* Whether `source` sits in the subtree of `root` (the genesis's membership). */
bool ObjectArena::under(uint32_t source, uint32_t root) {
    if (source == root)
        return true;
    uint32_t at = source;
    for (uint32_t depth = 0; depth < kWalkDepth; depth++) {
        pymergetic_rxf_object *record = nullptr;
        if (load(at, &record) != Status::Ok)
            return false;
        if (record->parent == PYMERGETIC_RXF_HANDLE32_NONE)
            return at == root;
        if (record->parent == root)
            return true;
        at = record->parent;
    }
    return false;
}

/* A relation table is a child of sys/relations (docs/32 2): its payload is the
 * pinned reference rows, and their two handles are paths until the remap. */
bool ObjectArena::is_ref_table(uint32_t source, uint32_t relations,
                               const pymergetic_rxf_object *record) {
    return record->parent == relations && source != relations;
}

/* The subtree of `first` in the writer's order (the name order). */
bool ObjectArena::walk_place(ObjectArena &out, uint32_t first, uint32_t *remap,
                             uint32_t remap_count) {
    struct Open {
        uint32_t parent;
        uint16_t last;
    };
    Open resume[kWalkDepth];
    uint32_t depth = 0;
    uint32_t at = first;
    for (;;) {
        if (!place_one(out, at, remap, remap_count))
            return false;
        pymergetic_rxf_object *record = nullptr;
        if (load(at, &record) != Status::Ok)
            return false;
        uint32_t child = PYMERGETIC_RXF_HANDLE32_NONE;
        if (record->kind == PYMERGETIC_RXF_OBJ_MODULE && !next_child(at, 0xFFFF, &child))
            return false;
        if (child != PYMERGETIC_RXF_HANDLE32_NONE) {
            if (depth >= kWalkDepth)
                return false;
            pymergetic_rxf_object *kid = nullptr;
            if (load(child, &kid) != Status::Ok)
                return false;
            resume[depth].parent = at;
            resume[depth].last = kid->name;
            depth++;
            at = child;
            continue;
        }
        for (;;) {
            if (depth == 0)
                return true;
            const Open open = resume[depth - 1];
            uint32_t up = PYMERGETIC_RXF_HANDLE32_NONE;
            if (!next_child(open.parent, open.last, &up))
                return false;
            if (up != PYMERGETIC_RXF_HANDLE32_NONE) {
                pymergetic_rxf_object *nxt = nullptr;
                if (load(up, &nxt) != Status::Ok)
                    return false;
                resume[depth - 1].last = nxt->name;
                at = up;
                break;
            }
            depth--;
        }
    }
}

bool ObjectArena::fixup_refs(ObjectArena &out, uint32_t where, uint32_t source, uint32_t type,
                             uint64_t head, uint64_t shift, const uint32_t *remap,
                             uint32_t remap_count) {
    if (type == PYMERGETIC_RXF_HANDLE32_NONE)
        return true;
    uint32_t fields[64];
    uint32_t held = 0;
    if (children(type, fields, 64, &held) != Status::Ok || held > 64)
        return false;
    for (uint32_t k = 0; k < held; k++) {
        pymergetic_rxf_object *record = nullptr;
        if (load(fields[k], &record) != Status::Ok)
            return false;
        if (record->payload < sizeof(pymergetic_rxf_field))
            continue;
        const uint8_t *bytes = nullptr;
        uint32_t size = 0;
        if (payload(fields[k], &bytes, &size) != Status::Ok)
            continue;
        pymergetic_rxf_field field{};
        mem::copy(&field, bytes, sizeof(field));
        if ((field.flags & PYMERGETIC_RXF_FIELD_FLAG_REF) == 0)
            continue;
        for (uint64_t i = 0; i < field.count; i++) {
            const uint64_t at = head + field.offset + 8 * i;
            if (!fixup_slot(out, where, source, at, shift, remap, remap_count))
                return false;
        }
    }
    return true;
}

/* One u64 of a payload: a handle that points at an object that is gone is
 * unset in the canonical image (compaction does the same: tools/native/
 * physical.py's records), and the rest follows the remap. The source slot
 * sits at `at` (its module's head included); the output's slot sits at
 * `at - shift` - `shift` is the head a demoted leaf dropped (docs/32 1.2),
 * 0 for everything that keeps its shape. */
bool ObjectArena::fixup_slot(ObjectArena &out, uint32_t where, uint32_t source, uint64_t at,
                             uint64_t shift, const uint32_t *remap, uint32_t remap_count) {
    const uint8_t *bytes = nullptr;
    uint32_t size = 0;
    if (payload(source, &bytes, &size) != Status::Ok || at + 8 > size)
        return false;
    uint64_t value = 0;
    mem::copy(&value, bytes + at, 8);
    if (value == PYMERGETIC_RXF_HANDLE_NONE)
        return true;
    if (value > 0xFFFFFFFFULL || pymergetic_rxf_handle32_index((uint32_t)value) >= remap_count)
        return false;
    const uint32_t target = remap[pymergetic_rxf_handle32_index((uint32_t)value)];
    const uint64_t mapped =
        target == PYMERGETIC_RXF_HANDLE32_NONE ? PYMERGETIC_RXF_HANDLE_NONE : (uint64_t)target;
    return out.set_payload_at(where, at - shift, reinterpret_cast<const uint8_t *>(&mapped), 8) ==
           Status::Ok;
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
    Status st = s_allocate(
        sizeof(pymergetic_rxf_slots) + (uint64_t)capacity * sizeof(pymergetic_rxf_slot), &at);
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

    static const char *const leaves[] = {"functions"};
    for (const char *leaf : leaves) {
        st = create(sys, leaf, PYMERGETIC_RXF_OBJ_MODULE, sizeof(pymergetic_rxf_module), &made);
        if (st != Status::Ok)
            return st;
    }

    /* sys/specs (docs/32 5): one row per index of the object index. It is made
     * before the journal attaches, like the index and syms: the table is boot
     * ground, and a journaled fill of its rows would be the biggest thing the
     * undo ring ever records. Every row starts untyped. */
    st = create(sys, "specs", PYMERGETIC_RXF_OBJ_SPEC,
                (uint64_t)capacity * sizeof(pymergetic_rxf_spec), &made);
    if (st != Status::Ok)
        return st;
    specs_handle_ = made;
    pymergetic_rxf_spec *rows = nullptr;
    uint32_t row_count = 0;
    st = spec_rows(&rows, &row_count);
    if (st != Status::Ok)
        return st;
    for (uint32_t i = 0; i < row_count; i++)
        rows[i].type = PYMERGETIC_RXF_HANDLE32_NONE;

    /* The journal leaf carries the undo ring (docs/32 3): from its attach on,
     * every write is recorded before the store. The ring is sized for the boot
     * sequence itself, so boot never relocates it: the allocation layout - and
     * with it every handle number - stays independent of how many undo records
     * the boot writes. */
    st = create(sys, "journal", PYMERGETIC_RXF_OBJ_JOURNAL,
                sizeof(pymergetic_rxf_journal) + 256 * sizeof(pymergetic_rxf_undo), &made);
    if (st != Status::Ok)
        return st;
    if (load(made, &record) != Status::Ok)
        return Status::BadImage;
    pymergetic_rxf_journal *journal = journal_of(record);
    journal->count = 0;
    journal->capacity = 256;
    journal->granularity = PYMERGETIC_RXF_JOURNAL_GRANULE;
    journal->reserved = 0;
    journal->base = 0;
    st = journal_attach(made);
    if (st != Status::Ok)
        return st;

    st = create(sys, "caps", PYMERGETIC_RXF_OBJ_MODULE, sizeof(pymergetic_rxf_module), &made);
    if (st != Status::Ok)
        return st;
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
    return journal_commit(); /* a fresh tree is one committed state */
}

Status ObjectArena::child_named(uint32_t parent, const char *text, uint32_t length,
                                uint32_t *handle) {
    *handle = PYMERGETIC_RXF_HANDLE32_NONE;
    if (text == nullptr || length == 0)
        return Status::BadName;
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
        if (child->name != PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            const char *held = nullptr;
            uint32_t size = 0;
            st = symbol(child->name, &held, &size);
            if (st != Status::Ok)
                return st;
            if (size == length && mem::compare(held, text, length) == 0) {
                *handle = walk;
                return Status::Ok;
            }
        }
        walk = child->link;
    }
    return Status::Ok;
}

/* A leaf that gains a child becomes a module (docs/32 1.2: "a module owns its
 * chain head first"): its value moves behind a fresh chain head. The record
 * that outgrows its room is freed and re-grown (docs/32 1.2) - the same
 * pattern a value that grows follows. */
Status ObjectArena::into_module(uint32_t handle, uint32_t *fresh) {
    *fresh = handle;
    pymergetic_rxf_object *old = nullptr;
    Status st = load(handle, &old);
    if (st != Status::Ok)
        return st;
    if (old->kind == PYMERGETIC_RXF_OBJ_MODULE)
        return Status::Ok;
    if (pymergetic_rxf_handle32_index(handle) == PYMERGETIC_RXF_INDEX_SELF)
        return Status::Protected;
    const uint32_t parent = old->parent;
    const uint16_t name_id = old->name;
    const uint16_t flags = old->flags;
    const uint64_t value = old->payload;
    const uint64_t at_old = index_entry(pymergetic_rxf_handle32_index(handle))->slot;
    uint64_t at_new = 0;
    /* The object's room is its request (docs/32 5.4), never an absorbed slack. */
    st = s_allocate(sizeof(pymergetic_rxf_module) + value, &at_new, false);
    if (st != Status::Ok)
        return st;
    /* the chain head first (the writer's EMPTY_MODULE_HEAD), then the value */
    for (uint64_t k = 0; k < value; k += 8) {
        uint64_t word = 0;
        mem::copy(&word,
                  reinterpret_cast<const uint8_t *>(record_at(at_old)) +
                      sizeof(pymergetic_rxf_object) + k,
                  8);
        st = j_set(reinterpret_cast<uint64_t *>(reinterpret_cast<uint8_t *>(record_at(at_new)) +
                                                sizeof(pymergetic_rxf_object) +
                                                sizeof(pymergetic_rxf_module) + k),
                   word);
        if (st != Status::Ok)
            return st;
    }
    pymergetic_rxf_object *made = record_at(at_new);
    st = j_set(&made->kind, (uint16_t)PYMERGETIC_RXF_OBJ_MODULE);
    if (st != Status::Ok)
        return st;
    st = j_set(&made->name, name_id);
    if (st != Status::Ok)
        return st;
    st = j_set(&made->parent, parent);
    if (st != Status::Ok)
        return st;
    st = j_set(&made->link, old->link);
    if (st != Status::Ok)
        return st;
    st = j_set(&made->refs, old->refs);
    if (st != Status::Ok)
        return st;
    st = j_set(&made->flags, flags);
    if (st != Status::Ok)
        return st;
    /* The empty chain spells HANDLE32_NONE (docs/32 1.2): a zeroed head would
     * read children = 0, the index object's handle - the same hazard the
     * bootstrap and create spell out for a fresh module. */
    st = j_set(&module_of(made)->children, PYMERGETIC_RXF_HANDLE32_NONE);
    if (st != Status::Ok)
        return st;
    st = j_set(&module_of(made)->count, (uint32_t)0);
    if (st != Status::Ok)
        return st;
    st = s_release(at_old);
    if (st != Status::Ok)
        return st;
    /* the index hands the entry out again (docs/32 1.2): the generation the
     * span was handed is the one the entry and the record share (load() checks
     * both). */
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    pymergetic_rxf_slot *entry = index_entry(index);
    const uint16_t gen = record_at(at_new)->gen;
    st = j_set(&entry->slot, at_new);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->kind, (uint16_t)PYMERGETIC_RXF_OBJ_MODULE);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->gen, gen);
    if (st != Status::Ok)
        return st;
    *fresh = pymergetic_rxf_handle32_pack(index, gen);
    return Status::Ok;
}

/* A module that loses its last child becomes a leaf again (docs/32 1.2, the
 * mirror of into_module): the value moves out from behind the chain head and
 * the record takes the storage role of its world kind - the writer's map
 * (tools/native/arena_export.py's LEAF_KIND) reads the semantic row's flags.
 * A scaffold module (a row whose kind word is 0) keeps its head: it is
 * bookkeeping, not a world node. */

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
    /* An object's room is the room its world asks for (docs/32 5.4: the canonical
     * form reads it back as the object's size) - a span whose slack would be
     * absorbed is no candidate here. */
    Status st = s_allocate(room, &at, false);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_object *record = record_at(at);
    st = j_set(&record->kind, kind);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->name, name_id);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->parent, parent);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->link, PYMERGETIC_RXF_HANDLE32_NONE);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->refs, (uint32_t)1);
    if (st != Status::Ok)
        return st;
    if (kind == PYMERGETIC_RXF_OBJ_MODULE) {
        /* A zeroed payload would read children = 0, which is the index object's
         * handle: the empty chain spells HANDLE32_NONE (docs/32 1.2). */
        pymergetic_rxf_module *module = module_of(record);
        st = j_set(&module->children, PYMERGETIC_RXF_HANDLE32_NONE);
        if (st != Status::Ok)
            return st;
        st = j_set(&module->count, (uint32_t)0);
        if (st != Status::Ok)
            return st;
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
    st = j_set(&entry->kind, kind);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->gen, record->gen);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->reserved, (uint32_t)0);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->slot, at);
    if (st != Status::Ok)
        return st;
    if (index == slots->count) {
        st = j_set(&slots->count, index + 1);
        if (st != Status::Ok)
            return st;
    }
    *handle = pymergetic_rxf_handle32_pack(index, record->gen);

    if (parent != PYMERGETIC_RXF_HANDLE32_NONE) {
        st = append_child(parent, *handle);
        if (st != Status::Ok)
            return st;
    }
    return Status::Ok;
}

uint32_t ObjectArena::handle_count() const {
    const pymergetic_rxf_slots *slots = index_table();
    return model_ready(slots) ? slots->count : 0;
}

bool ObjectArena::path_matches(uint32_t handle, const char *name, uint32_t length) {
    if (name == nullptr || length == 0)
        return false;
    const char *const start = name;
    const char *end = name + length;
    uint32_t walk = handle;
    while (end != start) {
        /* More segments than the tree has: the root is the image, not a segment. */
        if (walk == PYMERGETIC_RXF_HANDLE32_NONE || walk == super()->root)
            return false;
        pymergetic_rxf_object *part = nullptr;
        if (load(walk, &part) != Status::Ok)
            return false;
        if (part->name == PYMERGETIC_RXF_SYMS_ANONYMOUS)
            return false; /* an unnamed object has no path segment */
        const char *segment = end;
        while (segment != start && segment[-1] != '.')
            --segment;
        const auto size = static_cast<uint32_t>(end - segment);
        const char *text = nullptr;
        uint32_t held = 0;
        if (symbol(part->name, &text, &held) != Status::Ok)
            return false;
        if (held != size || mem::compare(text, segment, size) != 0)
            return false;
        walk = part->parent;
        end = (segment == start) ? segment : segment - 1; /* the dot before the segment */
    }
    return walk == super()->root;
}

Status ObjectArena::path_of(uint32_t handle, char *out, uint32_t capacity, uint32_t *written) {
    *written = 0;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record); /* the handle is checked first */
    if (st != Status::Ok)
        return st;

    /* The root is the image, not a path segment: its path is empty. */
    uint64_t total = 0;
    uint32_t walk = handle;
    uint32_t depth = 0;
    while (walk != super()->root) {
        pymergetic_rxf_object *part = nullptr;
        st = load(walk, &part);
        if (st != Status::Ok)
            return st;
        if (part->name != PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            const char *text = nullptr;
            uint32_t size = 0;
            st = symbol(part->name, &text, &size);
            if (st != Status::Ok)
                return st;
            total += size + (total == 0 ? 0 : 1);
        }
        if (part->parent == PYMERGETIC_RXF_HANDLE32_NONE)
            return Status::BadImage; /* a tree that does not reach the root */
        walk = part->parent;
        if (++depth > 1024)
            return Status::BadImage;
    }
    *written = static_cast<uint32_t>(total);
    if (total > capacity)
        return Status::OutOfMemory; /* *written is what the buffer has to offer */

    /* Written backwards from the end, so the segments land in tree order. */
    uint64_t at = total;
    walk = handle;
    while (walk != super()->root) {
        pymergetic_rxf_object *part = nullptr;
        if (load(walk, &part) != Status::Ok)
            return Status::BadImage;
        if (part->name != PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            const char *text = nullptr;
            uint32_t size = 0;
            if (symbol(part->name, &text, &size) != Status::Ok)
                return Status::BadImage;
            at -= size;
            mem::copy(out + at, text, size);
            if (at != 0)
                out[--at] = '.';
        }
        walk = part->parent;
    }
    return Status::Ok;
}

Status ObjectArena::refs_of(uint32_t handle, pymergetic_rxf_ref *out, uint32_t capacity,
                            uint32_t *count) {
    *count = 0;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record); /* the handle is checked first */
    if (st != Status::Ok)
        return st;
    const pymergetic_rxf_ref *rows = nullptr;
    uint32_t held = 0;
    st = relation_rows(handle, &rows, &held);
    if (st != Status::Ok)
        return st;
    *count = held;
    if (out == nullptr || held > capacity)
        return held == 0 ? Status::Ok : Status::OutOfMemory;
    for (uint32_t i = 0; i < held; i++)
        out[i] = rows[i];
    return Status::Ok;
}

Status ObjectArena::relation_rows(uint32_t handle, const pymergetic_rxf_ref **rows_out,
                                  uint32_t *count) {
    *rows_out = nullptr;
    *count = 0;
    uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
    uint32_t relations = PYMERGETIC_RXF_HANDLE32_NONE;
    Status st = child_named(super()->root, "sys", 3, &sys);
    if (st != Status::Ok)
        return st;
    if (sys == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok; /* no sys/, so nothing has refs */
    st = child_named(sys, "relations", 9, &relations);
    if (st != Status::Ok)
        return st;
    if (relations == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok; /* no tables at all */
    pymergetic_rxf_object *module = nullptr;
    st = load(relations, &module);
    if (st != Status::Ok)
        return st;
    uint32_t walk = module_of(module)->children;
    while (walk != PYMERGETIC_RXF_HANDLE32_NONE) {
        pymergetic_rxf_object *table = nullptr;
        st = load(walk, &table);
        if (st != Status::Ok)
            return st;
        if (table->name != PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            const char *name = nullptr;
            uint32_t size = 0;
            st = symbol(table->name, &name, &size);
            if (st != Status::Ok)
                return st;
            if (path_matches(handle, name, size)) {
                const auto *rows = reinterpret_cast<const pymergetic_rxf_ref *>(
                    reinterpret_cast<const uint8_t *>(table) + sizeof(pymergetic_rxf_object));
                /* The room of a table is a whole number of rows and may carry the
                 * zero padding of an 8-byte slot; a real row always names a target,
                 * and a live handle32 is never 0, so an all-zero row is padding. */
                uint32_t held = table->payload / sizeof(pymergetic_rxf_ref);
                while (held > 0 && rows[held - 1].target == 0 && rows[held - 1].role == 0 &&
                       rows[held - 1].kind == 0 && rows[held - 1].binding == 0 &&
                       rows[held - 1].index == 0)
                    --held;
                *rows_out = rows;
                *count = held;
                return Status::Ok;
            }
        }
        walk = table->link;
    }
    return Status::Ok; /* the object has no table: no refs */
}

Status ObjectArena::set_spec(uint32_t handle, const pymergetic_rxf_spec &row) {
    if (row.reserved != 0)
        return Status::BadImage; /* the reserved word is zero in every row */
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_spec *rows = nullptr;
    uint32_t count = 0;
    st = spec_rows(&rows, &count);
    if (st != Status::Ok)
        return st;
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    if (index >= count)
        return Status::BadHandle;
    return j_copy(&rows[index], &row, sizeof(row));
}

/* The semantic row of one object (docs/32 5): sys/specs is keyed by the
 * index of the handle (a reused span keeps its rows: `gen` only certifies
 * which span an old handle spoke of). */
Status ObjectArena::spec(uint32_t handle, pymergetic_rxf_spec *row) {
    pymergetic_rxf_spec *rows = nullptr;
    uint32_t count = 0;
    Status st = spec_rows(&rows, &count);
    if (st != Status::Ok)
        return st;
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    if (index >= count)
        return Status::BadImage;
    const pymergetic_rxf_slot *entry = index_entry(index);
    if (entry->slot == 0)
        return Status::BadHandle;
    mem::copy(row, rows + index, sizeof(pymergetic_rxf_spec));
    return Status::Ok;
}

Status ObjectArena::rebuild(ObjectArena &out, uint32_t *remap, uint32_t remap_count) {
    const pymergetic_rxf_slots *slots = index_table();
    if (memory_.base == nullptr || out.memory_.base == nullptr || &out == this ||
        !model_ready(slots) || model_ready(out.index_table()) || remap == nullptr ||
        remap_count < slots->count)
        return Status::BadImage;
    for (uint32_t i = 0; i < slots->count; i++)
        remap[i] = PYMERGETIC_RXF_HANDLE32_NONE;
    for (uint32_t i = 0; i < kWalkFirst && i < slots->count; i++) {
        const pymergetic_rxf_slot *entry = index_entry(i);
        remap[i] = pymergetic_rxf_handle32_pack(i, entry->gen);
    }
    /* The superblock is replayed whole (docs/32 5.4 step 1 names it first): the
     * claim - the limit - is part of the bytes, and the tail marker's span is
     * the claim's tail. The rebuild's arena may sit in memory of another size;
     * the claim it answers is the source's, and the marker takes the claim's
     * tail formula over again (init wrote it for the memory's own claim). */
    out.memory_.limit = super()->limit;
    out.super()->limit = super()->limit;
    out.super()->entry = super()->entry; /* the entry is a handle (docs/32 2) */
    {
        pymergetic_rxf_object *marker = out.record_at(out.marker_offset());
        marker->payload =
            (uint32_t)(super()->limit - out.marker_offset() - sizeof(pymergetic_rxf_object));
    }
    /* Step 1: the bootstrap is replayed whole - the same call the writer made
     * (tools/native/arena_export.py's place_world), so the interning order of
     * the skeleton and with it every symbol id is the same. The capacity is
     * the live slots plus the headroom: `slots->count` still names the dead
     * slots a release leaves behind, and the writer's survey counts the world
     * as it stands - one dead slot in the source would size the output one
     * slot above the writer's and the canonical bytes would drift. */
    uint32_t live = 0;
    for (uint32_t i = 0; i < slots->count; i++) {
        if (index_entry(i)->slot != 0)
            live++;
    }
    Status st = out.bootstrap("boot", live + kIndexHeadroom);
    if (st != Status::Ok)
        return st;
    st = out.journal_detach(); /* the walk is a build: nothing to unwind */
    if (st != Status::Ok)
        return st;
    /* Step 2: the walk in the writer's order - the world's tree in name order,
     * then sys/relations and its tables in the chain's order, then the genesis
     * tree in the index order (docs/32 5.4: place_world's own). An object an
     * apply created sits at the index end; the walk places it where the writer
     * would have, and the handles its payload carries follow (step 3). */
    const uint32_t root = super()->root;
    uint16_t name_sys = PYMERGETIC_RXF_SYMS_ANONYMOUS;
    uint16_t name_honors = PYMERGETIC_RXF_SYMS_ANONYMOUS;
    if (!symbol_id("sys", &name_sys) || !symbol_id("honors", &name_honors))
        return Status::BadImage;
    uint16_t cursor = 0xFFFF;
    for (;;) {
        uint32_t child = PYMERGETIC_RXF_HANDLE32_NONE;
        if (!next_child(root, cursor, &child))
            return Status::BadImage;
        if (child == PYMERGETIC_RXF_HANDLE32_NONE)
            break;
        pymergetic_rxf_object *kid = nullptr;
        if (load(child, &kid) != Status::Ok)
            return Status::BadImage;
        cursor = kid->name;
        if (kid->name == name_sys || kid->name == name_honors)
            continue;
        if (!walk_place(out, child, remap, remap_count))
            return Status::BadImage;
    }
    uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
    if (find(root, "sys", &sys) != Status::Ok || sys == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::BadImage;
    uint32_t relations = PYMERGETIC_RXF_HANDLE32_NONE;
    if (find(sys, "relations", &relations) != Status::Ok ||
        relations == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::BadImage;
    {
        pymergetic_rxf_object *record = nullptr;
        if (load(relations, &record) != Status::Ok)
            return Status::BadImage;
        if (!place_one(out, relations, remap, remap_count))
            return Status::BadImage;
        /* The tables in the writer's order (docs/32 5.4): `_relations` walks
         * the world depth-first, and a depth-first walk of name-sorted
         * children is the lexicographic order of the dotted paths - the
         * tables' names. The chain is the creation order (an apply appends at
         * its end), so the walk sorts by name instead of following links. */
        uint16_t cursor = 0xFFFF;
        for (;;) {
            uint32_t at = PYMERGETIC_RXF_HANDLE32_NONE;
            if (!next_child(relations, cursor, &at))
                return Status::BadImage;
            if (at == PYMERGETIC_RXF_HANDLE32_NONE)
                break;
            pymergetic_rxf_object *kid = nullptr;
            if (load(at, &kid) != Status::Ok)
                return Status::BadImage;
            cursor = kid->name;
            if (!genesis_table(at) && !place_one(out, at, remap, remap_count))
                return Status::BadImage;
        }
    }
    /* Segment 3: the genesis tree (docs/36's founding core) and then its own
     * relation tables - the writer's _genesis appends the tables last. */
    uint32_t genesis = PYMERGETIC_RXF_HANDLE32_NONE;
    if (find(root, "honors", &genesis) == Status::Ok && genesis != PYMERGETIC_RXF_HANDLE32_NONE) {
        for (int pass = 0; pass < 2; pass++) {
            for (uint32_t index = kWalkFirst; index < slots->count; ++index) {
                const pymergetic_rxf_slot *entry = index_entry(index);
                if (entry->slot == 0)
                    continue;
                const uint32_t source = pymergetic_rxf_handle32_pack(index, entry->gen);
                const bool table = genesis_table(source);
                if ((pass == 0) == table)
                    continue;
                if (!table && !under(source, genesis))
                    continue;
                if (!place_one(out, source, remap, remap_count))
                    return Status::BadImage;
            }
        }
    }

    /* Step 3: the handles the payloads and the rows carry, rewritten to the
     * output's (the type's declared reference slots are the walk tools'
     * world/export.py does). */
    for (uint32_t index = kWalkFirst; index < slots->count; ++index) {
        const pymergetic_rxf_slot *entry = index_entry(index);
        if (entry->slot == 0)
            continue;
        const uint32_t source = pymergetic_rxf_handle32_pack(index, entry->gen);
        const uint32_t where = remap[index];
        if (where == PYMERGETIC_RXF_HANDLE32_NONE)
            continue;
        /* The semantic row: its type is a handle (docs/32 5). */
        pymergetic_rxf_spec row{};
        st = spec(source, &row);
        if (st != Status::Ok)
            return st;
        const uint32_t type_source = row.type;
        row.type = row.type == PYMERGETIC_RXF_HANDLE32_NONE
                       ? PYMERGETIC_RXF_HANDLE32_NONE
                       : remap[pymergetic_rxf_handle32_index(row.type)];
        st = out.set_spec(where, row);
        if (st != Status::Ok)
            return st;
        /* The payload's declared slots and the relation rows' two handles. */
        pymergetic_rxf_object *record = nullptr;
        st = load(source, &record);
        if (st != Status::Ok)
            return st;
        const bool table = is_ref_table(source, relations, record);
        /* The type's offsets are inside the world's value (tools/native/
         * arena_export.py's _relink_payloads): a module's arena payload opens
         * with the chain head, and the value follows it. A module the walk
         * demoted to a leaf (place_one: its chain emptied and its row carries a
         * world kind) dropped that head in the output - its value's slots sit
         * `head` lower there. */
        const uint64_t head =
            record->kind == PYMERGETIC_RXF_OBJ_MODULE ? sizeof(pymergetic_rxf_module) : 0;
        uint64_t shift = 0;
        if (head != 0) {
            pymergetic_rxf_object *placed = nullptr;
            if (out.load(where, &placed) != Status::Ok)
                return Status::BadImage;
            if (placed->kind != PYMERGETIC_RXF_OBJ_MODULE)
                shift = head;
        }
        if (table) {
            for (uint64_t k = 0; k + sizeof(pymergetic_rxf_ref) <= record->payload;
                 k += sizeof(pymergetic_rxf_ref)) {
                if (!fixup_slot(out, where, source, k, 0, remap, remap_count) ||
                    !fixup_slot(out, where, source, k + 8, 0, remap, remap_count))
                    return Status::BadImage;
            }
        } else if (!fixup_refs(out, where, source, type_source, head, shift, remap, remap_count))
            return Status::BadImage;
    }

    /* Step 4: the semantic rows of the skeleton (the writer's pass writes the
     * row of every index and the root's row carries the world's flags). */
    for (uint32_t index = 0; index < kWalkFirst; ++index) {
        const pymergetic_rxf_slot *entry = index_entry(index);
        const uint32_t source = pymergetic_rxf_handle32_pack(index, entry->gen);
        const uint32_t where = remap[index];
        if (where == PYMERGETIC_RXF_HANDLE32_NONE)
            continue;
        pymergetic_rxf_spec row{};
        st = spec(source, &row);
        if (st != Status::Ok)
            return st;
        row.type = row.type == PYMERGETIC_RXF_HANDLE32_NONE
                       ? PYMERGETIC_RXF_HANDLE32_NONE
                       : remap[pymergetic_rxf_handle32_index(row.type)];
        st = out.set_spec(where, row);
        if (st != Status::Ok)
            return st;
    }

    /* The entry is a handle too (docs/32 2): when it names a live object it
     * follows the remap; an entry that names nothing - the writer leaves the
     * field zero - stands as it is. */
    if (out.super()->entry != PYMERGETIC_RXF_HANDLE32_NONE) {
        pymergetic_rxf_object *named = nullptr;
        if (load(out.super()->entry, &named) == Status::Ok) {
            const uint32_t mapped = remap[pymergetic_rxf_handle32_index(out.super()->entry)];
            if (mapped == PYMERGETIC_RXF_HANDLE32_NONE)
                return Status::BadImage;
            out.super()->entry = mapped;
        }
    }
    st = out.journal_commit();
    if (st != Status::Ok)
        return st;
    return out.verify();
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

Status ObjectArena::children(uint32_t handle, uint32_t *out, uint32_t capacity, uint32_t *written) {
    *written = 0;
    if (pymergetic_rxf_handle32_index(handle) == PYMERGETIC_RXF_INDEX_SELF)
        return Status::Protected; /* its payload is the slot table, not a chain */
    pymergetic_rxf_object *module = nullptr;
    Status st = load(handle, &module);
    if (st != Status::Ok)
        return st;
    if (module->kind != PYMERGETIC_RXF_OBJ_MODULE)
        return Status::Ok; /* a leaf cannot have children */
    uint32_t count = 0;
    uint32_t walk = module_of(module)->children;
    while (walk != PYMERGETIC_RXF_HANDLE32_NONE) {
        if (out != nullptr && count < capacity)
            out[count] = walk;
        ++count;
        pymergetic_rxf_object *child = nullptr;
        st = load(walk, &child);
        if (st != Status::Ok)
            return st;
        walk = child->link;
    }
    *written = count;
    if (count == 0)
        return Status::Ok;
    if (out == nullptr || count > capacity)
        return Status::OutOfMemory; /* *written is what the buffer has to offer */
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

Status ObjectArena::locate(uint32_t handle, uint64_t *record_offset, uint64_t *payload_offset) {
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    const pymergetic_rxf_slot *entry = index_entry(pymergetic_rxf_handle32_index(handle));
    *record_offset = entry->slot;
    *payload_offset = entry->slot + sizeof(pymergetic_rxf_object);
    return Status::Ok;
}

Status ObjectArena::set_payload(uint32_t handle, const uint8_t *bytes, uint32_t size) {
    return set_payload_at(handle, 0, bytes, size);
}

Status ObjectArena::set_payload_at(uint32_t handle, uint64_t offset, const uint8_t *bytes,
                                   uint32_t size) {
    if (size > 0 && bytes == nullptr)
        return Status::BadImage;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    if (offset > record->payload || size > record->payload - offset)
        return Status::TooLarge; /* a write past the granted room refuses */
    if (size == 0)
        return Status::Ok;
    return j_copy(reinterpret_cast<uint8_t *>(record) + sizeof(pymergetic_rxf_object) + offset,
                  bytes, size);
}

Status ObjectArena::payload(uint32_t handle, const uint8_t **bytes, uint32_t *size) {
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    *bytes = reinterpret_cast<const uint8_t *>(record) + sizeof(pymergetic_rxf_object);
    *size = record->payload;
    return Status::Ok;
}

Status ObjectArena::set_flags(uint32_t handle, uint16_t flags) {
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    return j_set(&record->flags, flags);
}

Status ObjectArena::release_handle(uint32_t handle) {
    if (handle == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok; /* releasing nothing is a no-op */
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    const uint32_t index = pymergetic_rxf_handle32_index(handle);
    /* The rows of sys/specs, when the image has them: the table is protected like
     * the index, and the row of the released object goes back to the default. */
    pymergetic_rxf_spec *rows = nullptr;
    uint32_t row_count = 0;
    const bool has_specs = spec_rows(&rows, &row_count) == Status::Ok;
    if (handle == super()->root || handle == journal_handle_ ||
        (has_specs && handle == specs_handle_) || index == PYMERGETIC_RXF_INDEX_SELF ||
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
                    if (prev == PYMERGETIC_RXF_HANDLE32_NONE) {
                        st = j_set(&module->children, next);
                        if (st != Status::Ok)
                            return st;
                    } else {
                        pymergetic_rxf_object *before = nullptr;
                        if (load(prev, &before) == Status::Ok) {
                            st = j_set(&before->link, next);
                            if (st != Status::Ok)
                                return st;
                        }
                    }
                    if (module->count > 0) {
                        st = j_set(&module->count, module->count - 1);
                        if (st != Status::Ok)
                            return st;
                    }
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
    st = j_set(&entry->kind, (uint16_t)0);
    if (st != Status::Ok)
        return st;
    /* The entry is free again; a new object reuses it under a new gen. */
    st = j_set(&entry->slot, (uint64_t)0);
    if (st != Status::Ok)
        return st;
    if (has_specs && index < row_count) {
        pymergetic_rxf_spec fresh{};
        fresh.type = PYMERGETIC_RXF_HANDLE32_NONE;
        st = j_copy(&rows[index], &fresh, sizeof(fresh));
        if (st != Status::Ok)
            return st;
    }
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

    st = j_copy(payload + used, text, need);
    if (st != Status::Ok)
        return st;
    st = j_set(&offsets[head->count], static_cast<uint32_t>(used));
    if (st != Status::Ok)
        return st;
    *id = static_cast<uint16_t>(head->count);
    return j_set(&head->count, head->count + 1);
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

/* ---- sys/journal: the undo ring (docs/32 3, steps 4-5) ---- */

pymergetic_rxf_journal *ObjectArena::journal_of(pymergetic_rxf_object *object) const {
    return reinterpret_cast<pymergetic_rxf_journal *>(reinterpret_cast<uint8_t *>(object) +
                                                      sizeof(pymergetic_rxf_object));
}

Status ObjectArena::j_fill(void *at, uint8_t byte, uint64_t len) {
    Status st = journal_before_write(static_cast<uint64_t>(reinterpret_cast<uint8_t *>(at) -
                                                           static_cast<uint8_t *>(memory_.base)),
                                     len);
    if (st != Status::Ok)
        return st;
    mem::fill(at, byte, len);
    return Status::Ok;
}

Status ObjectArena::j_copy(void *at, const void *src, uint64_t len) {
    Status st = journal_before_write(static_cast<uint64_t>(reinterpret_cast<const uint8_t *>(at) -
                                                           static_cast<uint8_t *>(memory_.base)),
                                     len);
    if (st != Status::Ok)
        return st;
    mem::copy(at, src, len);
    return Status::Ok;
}

Status ObjectArena::journal_before_write(uint64_t at, uint64_t len) {
    if (len == 0 || journal_handle_ == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok; /* an arena without the model writes through */
    if (at + len < at || at + len > super()->limit)
        return Status::BadImage;
    /* The head is log state, never its subject: only the journal internals store
     * into it (and they are plain stores). A write barrier aimed at the head is a
     * caller holding a stale pointer into the log's own span - refuse it. */
    if (!journal_growing_ && journal_lo_ != 0 && at + len > journal_lo_ &&
        at < journal_lo_ + sizeof(pymergetic_rxf_journal))
        return Status::BadImage;

    /* The log's own bytes are exempt: the ring content is the undo log, not its
     * subject (record appends, the head, and a relocation's new span). */
    auto exempt = [this](uint64_t granule) {
        if (granule >= journal_lo_ && granule < journal_hi_)
            return true;
        return journal_growing_ && granule >= grow_lo_ && granule < grow_hi_;
    };

    const uint64_t first = at & ~(uint64_t)(PYMERGETIC_RXF_JOURNAL_GRANULE - 1u);
    const uint64_t last = (at + len - 1u) & ~(uint64_t)(PYMERGETIC_RXF_JOURNAL_GRANULE - 1u);
    for (uint64_t granule = first; granule <= last; granule += PYMERGETIC_RXF_JOURNAL_GRANULE) {
        if (exempt(granule))
            continue;
        if (journal_growing_) {
            /* The ring is mid-relocation: park the record, flush it into the new
             * ring when the move is done. */
            if (pending_count_ >= kJournalPending)
                return Status::OutOfMemory;
            pending_[pending_count_].at = granule;
            mem::copy(&pending_[pending_count_].before,
                      static_cast<uint8_t *>(memory_.base) + granule,
                      PYMERGETIC_RXF_JOURNAL_GRANULE);
            pending_count_ += 1;
            continue;
        }
        pymergetic_rxf_object *record = nullptr;
        Status st = load(journal_handle_, &record);
        if (st != Status::Ok)
            return st;
        pymergetic_rxf_journal *head = journal_of(record);
        if (head->count >= head->capacity) {
            st = journal_grow(head->capacity < 32 ? 32 : head->capacity * 2);
            if (st != Status::Ok)
                return st;
            record = nullptr;
            st = load(journal_handle_, &record);
            if (st != Status::Ok)
                return st;
            head = journal_of(record);
        }
        auto *records = reinterpret_cast<pymergetic_rxf_undo *>(head + 1);
        records[head->count].at = granule;
        mem::copy(&records[head->count].before, static_cast<uint8_t *>(memory_.base) + granule,
                  PYMERGETIC_RXF_JOURNAL_GRANULE);
        head->count += 1;
    }
    return Status::Ok;
}

Status ObjectArena::journal_reserve(uint32_t granules) {
    if (journal_handle_ == PYMERGETIC_RXF_HANDLE32_NONE || journal_growing_)
        return Status::Ok;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(journal_handle_, &record);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_journal *head = journal_of(record);
    const uint64_t needed = (uint64_t)head->count + granules;
    if (needed <= head->capacity)
        return Status::Ok;
    const uint64_t doubled = (uint64_t)head->capacity * 2;
    return journal_grow((uint32_t)(doubled < needed ? needed : doubled));
}

Status ObjectArena::journal_grow(uint32_t capacity) {
    /* The ring relocates to a larger object (the sys/syms shape). Its own writes
     * are parked in pending_ and land in the new ring afterwards, so a rollback
     * still unwinds the move itself. */
    pymergetic_rxf_object *old_record = nullptr;
    Status st = load(journal_handle_, &old_record);
    if (st != Status::Ok)
        return st;
    const pymergetic_rxf_journal *old = journal_of(old_record);
    const auto *old_records = reinterpret_cast<const pymergetic_rxf_undo *>(old + 1);
    const uint32_t old_count = old->count;
    const uint64_t old_at = index_entry(pymergetic_rxf_handle32_index(journal_handle_))->slot;

    const uint64_t room =
        sizeof(pymergetic_rxf_journal) + (uint64_t)capacity * sizeof(pymergetic_rxf_undo);
    journal_growing_ = true;
    uint64_t at = 0;
    st = s_allocate(room, &at);
    if (st != Status::Ok) {
        journal_growing_ = false;
        return st;
    }
    grow_lo_ = at + sizeof(pymergetic_rxf_object);
    grow_hi_ = at + sizeof(pymergetic_rxf_object) + room;

    pymergetic_rxf_object *record = record_at(at);
    pymergetic_rxf_journal *head = journal_of(record);
    head->count = old_count;
    head->capacity = capacity;
    head->granularity = PYMERGETIC_RXF_JOURNAL_GRANULE;
    head->reserved = 0;
    head->base = old->base;
    auto *records = reinterpret_cast<pymergetic_rxf_undo *>(head + 1);
    for (uint32_t i = 0; i < old_count; i++)
        mem::copy(&records[i], &old_records[i], sizeof(pymergetic_rxf_undo));

    /* The move is part of the transaction (docs/32 3: the journal "can itself be
     * journaled"): every store of it goes through the barrier, so a rollback puts
     * the identity and its index entry back on the span they left. */
    st = j_set(&record->kind, (uint16_t)PYMERGETIC_RXF_OBJ_JOURNAL);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->gen, old_record->gen);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->flags, old_record->flags);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->name, old_record->name);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->parent, old_record->parent);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->link, old_record->link);
    if (st != Status::Ok)
        return st;
    st = j_set(&record->refs, old_record->refs);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_slot *entry = index_entry(pymergetic_rxf_handle32_index(journal_handle_));
    st = j_set(&entry->kind, record->kind);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->gen, record->gen);
    if (st != Status::Ok)
        return st;
    st = j_set(&entry->slot, at);

    st = s_release(old_at);
    journal_growing_ = false;
    grow_lo_ = 0;
    grow_hi_ = 0;
    journal_lo_ = at + sizeof(pymergetic_rxf_object);
    journal_hi_ = journal_lo_ + room;
    if (st != Status::Ok)
        return st;

    /* The parked records of the move itself land in the new ring: a rollback
     * restores the entry, both spans and the superblock granules. */
    for (uint32_t i = 0; i < pending_count_; i++) {
        if (head->count >= head->capacity)
            return Status::OutOfMemory;
        records[head->count] = pending_[i];
        head->count += 1;
    }
    pending_count_ = 0;
    return Status::Ok;
}

Status ObjectArena::journal_attach(uint32_t handle) {
    pymergetic_rxf_object *record = nullptr;
    Status st = load(handle, &record);
    if (st != Status::Ok)
        return st;
    if (record->kind != PYMERGETIC_RXF_OBJ_JOURNAL)
        return Status::BadImage;
    const uint64_t at = index_entry(pymergetic_rxf_handle32_index(handle))->slot;
    journal_handle_ = handle;
    journal_lo_ = at + sizeof(pymergetic_rxf_object);
    journal_hi_ = journal_lo_ + record->payload;
    return Status::Ok;
}

Status ObjectArena::journal_commit() {
    if (journal_handle_ == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(journal_handle_, &record);
    if (st != Status::Ok)
        return st;
    /* Dropping the ring is log state: the head is exempt from its own undo. The dead
     * log of the flight is cleared with it, so the committed image is deterministic
     * - it reads exactly as a state that never held that flight (docs/32 3). */
    journal_of(record)->count = 0;
    if (journal_hi_ > journal_lo_ + sizeof(pymergetic_rxf_journal))
        mem::fill(static_cast<uint8_t *>(memory_.base) + journal_lo_ +
                      sizeof(pymergetic_rxf_journal),
                  0, journal_hi_ - journal_lo_ - sizeof(pymergetic_rxf_journal));
    return Status::Ok;
}

Status ObjectArena::journal_rollback() {
    if (journal_handle_ == PYMERGETIC_RXF_HANDLE32_NONE)
        return Status::Ok;
    pymergetic_rxf_object *record = nullptr;
    Status st = load(journal_handle_, &record);
    if (st != Status::Ok)
        return st;
    pymergetic_rxf_journal *head = journal_of(record);
    const auto *records = reinterpret_cast<const pymergetic_rxf_undo *>(head + 1);
    /* The walk is in reverse (docs/32 3, step 5): the last write is undone first,
     * so a granule written twice lands on its earliest held bytes. The restores
     * are plain stores - they are the undo, not a new subject for it. */
    for (uint32_t i = head->count; i > 0; i--) {
        const pymergetic_rxf_undo &undo = records[i - 1];
        mem::copy(static_cast<uint8_t *>(memory_.base) + undo.at, &undo.before,
                  PYMERGETIC_RXF_JOURNAL_GRANULE);
    }
    head->count = 0;
    /* If the log relocated during the transaction, the walk has put the entry
     * back on the span the move left. The head is log state - its bytes are
     * exempt from the undo - so the reopened one is rebuilt as an empty ring,
     * not restored: capacity follows the span the entry names again. */
    pymergetic_rxf_slot *entry = index_entry(pymergetic_rxf_handle32_index(journal_handle_));
    if (entry->slot + sizeof(pymergetic_rxf_object) != journal_lo_) {
        Status reopen = journal_attach(journal_handle_);
        if (reopen != Status::Ok)
            return reopen;
        pymergetic_rxf_object *reopened = nullptr;
        reopen = load(journal_handle_, &reopened);
        if (reopen != Status::Ok)
            return reopen;
        pymergetic_rxf_journal *fresh = journal_of(reopened);
        fresh->count = 0;
        fresh->capacity = (uint32_t)((reopened->payload - sizeof(pymergetic_rxf_journal)) /
                                     sizeof(pymergetic_rxf_undo));
        fresh->granularity = PYMERGETIC_RXF_JOURNAL_GRANULE;
        fresh->reserved = 0;
        fresh->base = 0;
    }
    /* The ring payload is the dead log of the flight. Cleared, the image reads
     * exactly as it did at the last commit (docs/32 3): the flight left no byte. */
    if (journal_hi_ > journal_lo_ + sizeof(pymergetic_rxf_journal))
        mem::fill(static_cast<uint8_t *>(memory_.base) + journal_lo_ +
                      sizeof(pymergetic_rxf_journal),
                  0, journal_hi_ - journal_lo_ - sizeof(pymergetic_rxf_journal));
    return Status::Ok;
}

uint32_t ObjectArena::journal_records() {
    if (journal_handle_ == PYMERGETIC_RXF_HANDLE32_NONE)
        return 0;
    pymergetic_rxf_object *record = nullptr;
    if (load(journal_handle_, &record) != Status::Ok)
        return 0;
    return journal_of(record)->count;
}

Status ObjectArena::journal_detach() {
    Status st = journal_commit(); /* the drop a detach is: what is written is kept */
    if (st != Status::Ok)
        return st;
    journal_handle_ = PYMERGETIC_RXF_HANDLE32_NONE;
    journal_lo_ = 0;
    journal_hi_ = 0;
    pending_count_ = 0;
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::arena
