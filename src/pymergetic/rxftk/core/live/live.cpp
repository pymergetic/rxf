#include <pymergetic/rxftk/core/live/live.hpp>

#include <pymergetic/rxftk/core/arena/mem.hpp>
#include <pymergetic/rxftk/core/arena/object_arena.hpp>

/*
 * See live.hpp. Every function here has to be inlinable into one body: no recursion, no function
 * pointer, no virtual call, nothing from a library. Loops that would be recursions keep their own
 * small stack.
 *
 * The arena is the v7 one-arena object model (docs/32): one superblock, everything after it an
 * object, a handle the (index, gen) of the object index's slot entry. The read ops answer from it
 * exactly what the native twin (native/live.rxfs) answers - the world cell of docs/32 5.3, the
 * payload behind the record, the rows of sys/relations/<path>, the paths and names of the tree.
 *
 * The write path (the delta apply) and the canonical dump, relink and compaction are the next
 * slice (docs/32 5.3: the semantic row is what the apply writes). Until they land they refuse
 * plainly - a well-formed request that is not done - and never answer wrong bytes. The refusal is
 * the same stub the twin answers with; the debt is named where it lives (serve()).
 */

namespace pymergetic::rxf::core::live {

namespace {

using arena::Memory;
using arena::ObjectArena;
using arena::Status;
using Area = pymergetic_rxf_live_area;

constexpr uint64_t kNone = PYMERGETIC_RXF_HANDLE_NONE;
constexpr uint32_t kNone32 = PYMERGETIC_RXF_HANDLE32_NONE;
/* The empty name of an anonymous object is the superblock's zero tail: the world cell
 * answers the offset of that string as its name (docs/32 5.3, native s_name). */
constexpr uint64_t kAnonymousName = 32;
constexpr uint32_t kChildPage = 120; /* children in one CHILDREN frame */
constexpr uint32_t kRefPage = 40;    /* reference rows in one REFS frame */
constexpr uint32_t kMaxName = 900;   /* name bytes in one GET frame */

template <typename T> PYMERGETIC_RXF_INLINE T load(const void *p) {
    T v;
    mem::copy(&v, p, sizeof(T));
    return v;
}
template <typename T> PYMERGETIC_RXF_INLINE void store(void *p, T v) {
    mem::copy(p, &v, sizeof(T));
}

PYMERGETIC_RXF_INLINE uint8_t *ptr(uint64_t address) {
    return reinterpret_cast<uint8_t *>(static_cast<uintptr_t>(address));
}

PYMERGETIC_RXF_INLINE uint32_t crc_update(uint32_t crc, const uint8_t *p, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) {
        crc ^= p[i];
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
    }
    return crc;
}

/* The world cell of the live engine (docs/32 5.3): a 64-byte view of the record and its
 * spec row together, whose field offsets the protocol keeps. A cell is not stored, it is
 * derived; the view is good until the next call of the accessor that filled it. The value
 * bytes and the reference rows are not in the view - they stay real pointers into the
 * arena, so a read of a payload or a ref row never copies. */
struct WorldCell {
    uint64_t type;         /* spec.type: a handle32 zero-extended, all ones = untyped */
    uint64_t parent;       /* record.parent: zero-extended, all ones = no parent */
    uint64_t payload_size; /* record.payload: the granted room in bytes */
    uint64_t generation;   /* spec.generation: the world's 64-bit generation */
    uint64_t name;         /* the offset of the name string from the arena start */
    uint32_t flags;        /* spec.flags: the world's packed word (kind | state | owner) */
    uint32_t ref_count;    /* the rows of sys/relations/<path> */
    uint64_t forward;      /* the compaction home (docs/20 S7: the record's link word) */
    uint32_t index;        /* spec.index: the position among the ordered siblings */
    uint32_t reserved;
};

PYMERGETIC_RXF_GROUND_ASSERT(sizeof(WorldCell) == 64, "the world cell is 64 bytes");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, type) == 0, "world cell type");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, parent) == 8, "world cell parent");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, payload_size) == 16, "world cell payload_size");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, generation) == 24, "world cell generation");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, name) == 32, "world cell name");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, flags) == 40, "world cell flags");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, ref_count) == 44, "world cell ref_count");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, forward) == 48, "world cell forward");
PYMERGETIC_RXF_GROUND_ASSERT(offsetof(WorldCell, index) == 56, "world cell index");

class Session {
  public:
    bool open(Area *area, void *context) {
        a_ = area;
        context_ = context;
        if (area->magic != PYMERGETIC_RXF_LIVE_MAGIC ||
            area->version != PYMERGETIC_RXF_LIVE_VERSION)
            return false;
        Memory m;
        m.base = ptr(area->arena_base);
        m.committed = area->arena_committed == 0 ? area->arena_capacity : area->arena_committed;
        if (m.committed > area->arena_capacity)
            return false;
        /* The superblock's limit is the claim the region grows into (docs/32 3.1); the area
         * offers `committed` and every store is fenced there (this seat has no growth hook:
         * PYMERGETIC_RXF_FIXED_REGION refuses). The claim is what the attach gate wants -
         * the same arena the twin attaches over the same bytes. */
        const auto *super = reinterpret_cast<const pymergetic_rxf_superblock *>(m.base);
        m.limit = super->limit;
        if (arena_.attach(m) != Status::Ok)
            return false;
        slots_ = area->table_slots;
        if (slots_ < 8 || (slots_ & (slots_ - 1)) != 0 ||
            uint64_t(slots_) * 4 > area->work_capacity)
            return false;
        table_ = reinterpret_cast<uint32_t *>(ptr(area->work_base));
        scratch_base_ = ptr(area->work_base) + arena::align_up(uint64_t(slots_) * 4, 16);
        scratch_end_ = ptr(area->work_base) + area->work_capacity;
        scratch_at_ = scratch_base_;
        if (area->index_state != 1) {
            if (!build_table())
                return false;
            area->index_state = 1;
        }
        return true;
    }

    // ---- requests -------------------------------------------------------------------------

    uint16_t serve(uint8_t op, uint32_t argument, const uint8_t *rq, uint32_t rl, uint8_t *rs,
                   uint32_t *rsl, uint32_t *rarg) {
        *rsl = 0;
        *rarg = argument;
        scratch_reset();
        /* The read side answers from the v7 model (docs/32 5.3). The write side does not:
         * a delta apply, a dump, a compaction and a relink are not moved yet, and an apply
         * would write offsets into an arena that does not have them. REFUSED is what a
         * well-formed request that is not done gets (live.h) - the same stub the twin
         * (native/live.rxfs) answers, byte for byte. Debt: the v7 write path here; the
         * walkers move to the object index and the apply writes records, spec rows and
         * relation tables. */
        if (op == PYMERGETIC_RXF_OP_STAGE_COMMIT || op == PYMERGETIC_RXF_OP_DIGEST ||
            op == PYMERGETIC_RXF_OP_EMIT || op == PYMERGETIC_RXF_OP_COMPACT ||
            op == PYMERGETIC_RXF_OP_RELINK) {
            store<uint64_t>(rs, 0);
            store<uint64_t>(rs + 8, 0);
            *rsl = 16;
            return PYMERGETIC_RXF_LIVE_REFUSED;
        }
        switch (op) {
        case PYMERGETIC_RXF_OP_PING:
            mem::copy(rs, rq, rl);
            *rsl = rl;
            return PYMERGETIC_RXF_LIVE_OK;
        case PYMERGETIC_RXF_OP_HELLO: {
            store<uint32_t>(rs + 0, PYMERGETIC_RXF_LIVE_VERSION);
            store<uint32_t>(rs + 4, 0);
            store<uint64_t>(rs + 8, arena_.handle_count()); /* the slot head's count */
            store<uint64_t>(rs + 16, arena_.committed());   /* the superblock's committed */
            store<uint64_t>(rs + 24, a_->arena_capacity);
            store<uint64_t>(rs + 32, a_->stage_capacity);
            store<uint64_t>(rs + 40, a_->emit_capacity);
            store<uint64_t>(rs + 48, a_->work_capacity);
            store<uint64_t>(rs + 56, a_->commits);
            store<uint64_t>(rs + 64, a_->rejects);
            store<uint64_t>(rs + 72, arena_.memory().committed);
            *rsl = 80;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_HEADER:
            /* the superblock (docs/32 1), the fixed structure of the image */
            mem::copy(rs, ptr(a_->arena_base), 192);
            *rsl = 192;
            return PYMERGETIC_RXF_LIVE_OK;
        case PYMERGETIC_RXF_OP_RESOLVE: {
            const uint64_t h = resolve(rq, rl);
            if (h == kNone)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            store<uint64_t>(rs, h);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_PATH: {
            if (rl < 8)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t h = load<uint64_t>(rq);
            if (h > 0xFFFFFFFFULL)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            uint32_t n = 0;
            if (arena_.path_of(static_cast<uint32_t>(h), reinterpret_cast<char *>(rs),
                               PYMERGETIC_RXF_FRAME_MAX, &n) != Status::Ok)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            *rsl = n;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_GET: {
            if (rl < 8)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t h = load<uint64_t>(rq);
            WorldCell c;
            if (!cell(h, &c))
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            const char *name = nullptr;
            uint32_t n = 0;
            if (!name_of(h, &name, &n))
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            if (n > kMaxName)
                n = kMaxName;
            store<uint64_t>(rs + 0, c.type);
            store<uint64_t>(rs + 8, c.parent);
            store<uint64_t>(rs + 16, c.payload_size);
            store<uint64_t>(rs + 24, c.generation);
            store<uint32_t>(rs + 32, c.flags);
            store<uint32_t>(rs + 36, c.ref_count);
            store<uint32_t>(rs + 40, c.index);
            store<uint32_t>(rs + 44, n);
            mem::copy(rs + 48, name, n);
            *rsl = 48 + n;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_READ: {
            if (rl < 16)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t h = load<uint64_t>(rq);
            const uint32_t offset = load<uint32_t>(rq + 8);
            uint32_t count = load<uint32_t>(rq + 12);
            WorldCell c;
            if (!cell(h, &c))
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            if (offset > c.payload_size)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            if (count > PYMERGETIC_RXF_FRAME_MAX)
                count = PYMERGETIC_RXF_FRAME_MAX;
            if (count > c.payload_size - offset)
                count = static_cast<uint32_t>(c.payload_size - offset);
            const uint8_t *payload = nullptr;
            if (!payload_of(h, &payload))
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            mem::copy(rs, payload + offset, count);
            *rsl = count;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_CHILDREN: {
            if (rl < 12)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t parent = load<uint64_t>(rq);
            uint64_t at = load<uint32_t>(rq + 8);
            const uint64_t n = arena_.handle_count();
            uint32_t count = 0;
            /* The walk over all objects (docs/32 1.2): each entry packed (index, gen) -
             * a bare index is not a handle. The twin walks its index the same way; the
             * module chain is navigation, the parent word is what a child answers by. */
            while (at < n && count < kChildPage) {
                const uint64_t h = packed(static_cast<uint32_t>(at));
                if (h != 0) {
                    const pymergetic_rxf_object *record = nullptr;
                    if (record_of(h, &record) &&
                        (record->parent == kNone32 ? kNone : uint64_t(record->parent)) == parent) {
                        store<uint64_t>(rs + 8 + 8 * count, h);
                        count++;
                    }
                }
                at++;
            }
            store<uint32_t>(rs + 0, count);
            store<uint32_t>(rs + 4, at >= n ? kNone32 : static_cast<uint32_t>(at));
            *rsl = 8 + 8 * count;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_REFS: {
            if (rl < 12)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t h = load<uint64_t>(rq);
            const uint32_t skip = load<uint32_t>(rq + 8);
            if (h > 0xFFFFFFFFULL)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            const uint32_t handle = static_cast<uint32_t>(h);
            uint32_t held = 0;
            Status st = arena_.refs_of(handle, nullptr, 0, &held);
            if (st == Status::BadHandle)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            if (st != Status::Ok && st != Status::OutOfMemory)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            /* The rows of sys/relations/<path> (docs/32 5.2) come out of the arena once
             * and are paged from scratch; the twin pages them in place. */
            auto *rows = static_cast<pymergetic_rxf_ref *>(
                take(sizeof(pymergetic_rxf_ref) * uint64_t(held)));
            if (rows == nullptr && held != 0)
                return PYMERGETIC_RXF_LIVE_NO_ROOM;
            if (held != 0 && arena_.refs_of(handle, rows, held, &held) != Status::Ok)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            uint32_t count = 0;
            for (uint32_t k = skip; k < held && count < kRefPage; k++, count++)
                mem::copy(rs + 8 + 24 * count, &rows[k], 24);
            store<uint32_t>(rs + 0, count);
            store<uint32_t>(rs + 4, held);
            *rsl = 8 + 24 * count;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_STAGE_RESET:
            a_->stage_length = 0;
            return PYMERGETIC_RXF_LIVE_OK;
        case PYMERGETIC_RXF_OP_STAGE_WRITE: {
            if (uint64_t(argument) + rl > a_->stage_capacity)
                return PYMERGETIC_RXF_LIVE_NO_ROOM;
            mem::copy(ptr(a_->stage_base) + argument, rq, rl);
            if (argument + rl > a_->stage_length)
                a_->stage_length = argument + rl;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_EMIT_READ: {
            if (rl < 4)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            uint32_t count = load<uint32_t>(rq);
            if (argument > a_->emit_length)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            if (count > PYMERGETIC_RXF_FRAME_MAX)
                count = PYMERGETIC_RXF_FRAME_MAX;
            if (count > a_->emit_length - argument)
                count = static_cast<uint32_t>(a_->emit_length - argument);
            mem::copy(rs, ptr(a_->emit_base) + argument, count);
            *rsl = count;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_GROW: {
            if (rl < 8)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint64_t bytes = load<uint64_t>(rq);
            if (bytes < arena_.memory().committed || bytes > a_->arena_capacity) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, PYMERGETIC_RXF_REASON_RANGE);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_REFUSED;
            }
            a_->arena_committed = bytes;
            a_->grow_request = 0;
            store<uint64_t>(rs, bytes);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_BREAK:
            a_->break_request = argument;
            store<uint32_t>(rs, a_->break_ack);
            *rsl = 4;
            return PYMERGETIC_RXF_LIVE_OK;
        default:
            return PYMERGETIC_RXF_LIVE_UNKNOWN_OP;
        }
    }

  private:
    // ---- scratch --------------------------------------------------------------------------

    void scratch_reset() {
        scratch_at_ = scratch_base_;
    }
    void *take(uint64_t bytes) {
        bytes = (bytes + 15) & ~uint64_t(15);
        if (static_cast<uint64_t>(scratch_end_ - scratch_at_) < bytes)
            return nullptr;
        void *p = scratch_at_;
        scratch_at_ += bytes;
        return p;
    }

    // ---- the object index and its handles (docs/32 1.2) -----------------------------------

    /* One entry of the object index: where its record sits and the identity its handle is
     * checked against. The index is the first object of the arena, at its fixed place. */
    const pymergetic_rxf_slot *slot_entry(uint32_t index) const {
        return reinterpret_cast<const pymergetic_rxf_slot *>(
            ptr(a_->arena_base) + PYMERGETIC_RXF_INDEX_OFFSET + sizeof(pymergetic_rxf_object) +
            sizeof(pymergetic_rxf_slots) + uint64_t(index) * sizeof(pymergetic_rxf_slot));
    }

    /* The handle of this index as its entry packs it: (gen << 20) | index, or 0 when the
     * slot is dead. A walk over all objects packs handles this way - a bare index is not a
     * handle. */
    uint64_t packed(uint32_t index) const {
        const pymergetic_rxf_slot *entry = slot_entry(index);
        if (entry->slot == 0)
            return 0;
        return pymergetic_rxf_handle32_pack(index, entry->gen);
    }

    /* The record a handle names, checked against the index entry and the record itself;
     * a stale handle answers false before it touches payload bytes. */
    bool record_of(uint64_t handle, const pymergetic_rxf_object **out) {
        if (handle > 0xFFFFFFFFULL)
            return false;
        pymergetic_rxf_object *record = nullptr;
        if (arena_.load(static_cast<uint32_t>(handle), &record) != Status::Ok)
            return false;
        *out = record;
        return true;
    }

    /* cell(handle) -> the world cell in view A (docs/32 5.3), or false. The view is good
     * until the next call of this accessor. */
    bool cell(uint64_t handle, WorldCell *out) {
        const pymergetic_rxf_object *record = nullptr;
        if (!record_of(handle, &record))
            return false;
        pymergetic_rxf_spec row{};
        if (arena_.spec(static_cast<uint32_t>(handle), &row) != Status::Ok)
            return false;
        uint32_t rows = 0;
        const Status st = arena_.refs_of(static_cast<uint32_t>(handle), nullptr, 0, &rows);
        if (st != Status::Ok && st != Status::OutOfMemory)
            return false;
        const char *name = nullptr;
        uint32_t length = 0;
        if (!name_of(handle, &name, &length))
            return false;
        out->type = row.type == kNone32 ? kNone : uint64_t(row.type);
        out->parent = record->parent == kNone32 ? kNone : uint64_t(record->parent);
        out->payload_size = record->payload;
        out->generation = row.generation;
        out->name = static_cast<uint64_t>(reinterpret_cast<uintptr_t>(name) -
                                          reinterpret_cast<uintptr_t>(ptr(a_->arena_base)));
        out->flags = row.flags;
        out->ref_count = rows;
        out->forward = 0; /* no compaction is in flight (docs/20 S7) */
        out->index = row.index;
        out->reserved = 0;
        return true;
    }

    /* name(handle): the symbol string of the record, or the empty string of an anonymous
     * object - the superblock's zero tail, the same bytes the twin answers. */
    bool name_of(uint64_t handle, const char **text, uint32_t *length) {
        const pymergetic_rxf_object *record = nullptr;
        if (!record_of(handle, &record))
            return false;
        if (record->name == PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            *text = reinterpret_cast<const char *>(ptr(a_->arena_base) + kAnonymousName);
            *length = 0;
            return true;
        }
        return arena_.symbol(record->name, text, length) == Status::Ok;
    }

    /* payload(handle): the value bytes of an object, inline behind the record header
     * (docs/32 1.2: record + 24). The room is what the record's payload word grants. */
    bool payload_of(uint64_t handle, const uint8_t **bytes) {
        if (handle > 0xFFFFFFFFULL)
            return false;
        uint32_t size = 0;
        return arena_.payload(static_cast<uint32_t>(handle), bytes, &size) == Status::Ok;
    }

    // ---- lookup table: (parent, name) -> handle, open addressing -----------------------------

    static uint32_t name_hash(uint64_t parent, const void *p, uint64_t n) {
        uint64_t h = 1469598103934665603ULL ^ (parent * 0x9E3779B97F4A7C15ULL);
        const uint8_t *text = static_cast<const uint8_t *>(p);
        for (uint64_t i = 0; i < n; i++) {
            h ^= text[i];
            h *= 1099511628211ULL;
        }
        return static_cast<uint32_t>(h ^ (h >> 32));
    }

    bool name_equals(uint64_t h, const uint8_t *p, uint64_t n) {
        const char *text = nullptr;
        uint32_t length = 0;
        if (!name_of(h, &text, &length))
            return false;
        return uint64_t(length) == n && mem::compare(text, p, n) == 0;
    }

    uint64_t parent_of(uint64_t h) {
        const pymergetic_rxf_object *record = nullptr;
        if (!record_of(h, &record))
            return kNone;
        return record->parent == kNone32 ? kNone : uint64_t(record->parent);
    }

    uint32_t home_of(uint64_t h) {
        const char *text = nullptr;
        uint32_t length = 0;
        if (!name_of(h, &text, &length))
            return 0;
        return name_hash(parent_of(h), text, length);
    }

    uint64_t find_child(uint64_t parent, const uint8_t *p, uint64_t n) {
        const uint32_t mask = slots_ - 1;
        uint32_t i = name_hash(parent, p, n) & mask;
        for (uint32_t e = table_[i]; e != 0; e = table_[i]) {
            const uint64_t h = e - 1;
            if (parent_of(h) == parent && name_equals(h, p, n))
                return h;
            i = (i + 1) & mask;
        }
        return kNone;
    }

    /* false: the name is taken or the table is full. */
    bool table_insert(uint64_t h) {
        const pymergetic_rxf_object *record = nullptr;
        if (!record_of(h, &record))
            return false;
        const char *text = nullptr;
        uint32_t length = 0;
        if (!name_of(h, &text, &length))
            return false;
        const uint64_t parent = record->parent == kNone32 ? kNone : uint64_t(record->parent);
        if (h >= 0xFFFFFFF0ULL || uint64_t(arena_.handle_count()) * 2 > slots_)
            return false;
        if (find_child(parent, reinterpret_cast<const uint8_t *>(text), length) != kNone)
            return false;
        const uint32_t mask = slots_ - 1;
        uint32_t i = name_hash(parent, text, length) & mask;
        while (table_[i] != 0)
            i = (i + 1) & mask;
        table_[i] = static_cast<uint32_t>(h + 1);
        return true;
    }

    bool build_table() {
        mem::fill(table_, 0, uint64_t(slots_) * 4);
        const uint32_t n = arena_.handle_count();
        for (uint32_t index = 0; index < n; index++) {
            const uint64_t h = packed(index);
            if (h == 0)
                continue; /* a dead slot is no child of anything */
            const pymergetic_rxf_object *record = nullptr;
            if (!record_of(h, &record) || record->parent == kNone32)
                continue;
            if (!table_insert(h))
                return false;
        }
        return true;
    }

    /* path -> handle: the dotted path from the root, one (parent, name) hop per segment. */
    uint64_t resolve(const uint8_t *p, uint64_t n) {
        uint64_t at = arena_.root_handle();
        if (n == 0)
            return at;
        uint64_t start = 0;
        for (;;) {
            uint64_t end = start;
            while (end < n && p[end] != '.')
                end++;
            at = find_child(at, p + start, end - start);
            if (at == kNone || end >= n)
                return at;
            start = end + 1;
        }
    }

    Area *a_ = nullptr;
    void *context_ = nullptr;
    ObjectArena arena_;
    uint32_t *table_ = nullptr;
    uint32_t slots_ = 0;
    uint8_t *scratch_base_ = nullptr, *scratch_at_ = nullptr, *scratch_end_ = nullptr;
};

} // namespace

uint32_t crc32(const void *data, uint64_t size) {
    return ~crc_update(0xFFFFFFFFU, static_cast<const uint8_t *>(data), size);
}

uint32_t pump(pymergetic_rxf_live_area *a, uint32_t limit, void *context, bool relocatable) {
    Session session;
    if (!session.open(a, context))
        return 0;
    uint8_t request[PYMERGETIC_RXF_FRAME_HEADER + PYMERGETIC_RXF_FRAME_MAX +
                    PYMERGETIC_RXF_FRAME_TRAILER];
    uint8_t answer[PYMERGETIC_RXF_FRAME_HEADER + PYMERGETIC_RXF_FRAME_MAX +
                   PYMERGETIC_RXF_FRAME_TRAILER];
    uint8_t *rx = ptr(a->rx_base);
    uint8_t *tx = ptr(a->tx_base);
    const uint32_t rx_mask = static_cast<uint32_t>(a->rx_capacity) - 1;
    const uint32_t tx_mask = static_cast<uint32_t>(a->tx_capacity) - 1;
    uint32_t served = 0;
    while (served < limit) {
        const uint32_t head = __atomic_load_n(&a->rx_head, __ATOMIC_ACQUIRE);
        const uint32_t tail = a->rx_tail;
        const uint32_t available = head - tail;
        if (available < PYMERGETIC_RXF_FRAME_HEADER + PYMERGETIC_RXF_FRAME_TRAILER)
            break;
        for (uint32_t i = 0; i < PYMERGETIC_RXF_FRAME_HEADER; i++)
            request[i] = rx[(tail + i) & rx_mask];
        const pymergetic_rxf_frame_header h = load<pymergetic_rxf_frame_header>(request);
        if (h.magic != PYMERGETIC_RXF_FRAME_MAGIC || h.length > PYMERGETIC_RXF_FRAME_MAX) {
            __atomic_store_n(&a->rx_tail, tail + 1, __ATOMIC_RELEASE);
            continue;
        }
        const uint32_t total =
            PYMERGETIC_RXF_FRAME_HEADER + h.length + PYMERGETIC_RXF_FRAME_TRAILER;
        if (available < total)
            break;
        for (uint32_t i = PYMERGETIC_RXF_FRAME_HEADER; i < total; i++)
            request[i] = rx[(tail + i) & rx_mask];
        if (load<uint32_t>(request + total - 4) != crc32(request, total - 4)) {
            __atomic_store_n(&a->rx_tail, tail + 1, __ATOMIC_RELEASE);
            continue;
        }
        const uint32_t tx_used = a->tx_head - __atomic_load_n(&a->tx_tail, __ATOMIC_ACQUIRE);
        if (a->tx_capacity - tx_used <
            PYMERGETIC_RXF_FRAME_HEADER + PYMERGETIC_RXF_FRAME_MAX + PYMERGETIC_RXF_FRAME_TRAILER)
            break;

        // A compaction can move the code that runs it: the leaf that called this asks for a copy of
        // itself outside the arena first, and serves the frame from there (native/live.rxfs).
        if (relocatable && h.op == PYMERGETIC_RXF_OP_COMPACT && a->relocate == 0 &&
            a->relocated == 0 && a->in_run == 0 && a->machine != PYMERGETIC_RXF_MACHINE_WASM32) {
            a->relocate = 1;
            break;
        }

        uint32_t answer_length = 0, answer_argument = 0;
        const uint16_t status =
            session.serve(h.op, h.argument, request + PYMERGETIC_RXF_FRAME_HEADER, h.length,
                          answer + PYMERGETIC_RXF_FRAME_HEADER, &answer_length, &answer_argument);
        pymergetic_rxf_frame_header r;
        r.magic = PYMERGETIC_RXF_FRAME_MAGIC;
        r.op = static_cast<uint8_t>(h.op | PYMERGETIC_RXF_OP_ANSWER);
        r.flags = 0;
        r.sequence = h.sequence;
        r.argument = answer_argument;
        r.length = static_cast<uint16_t>(answer_length);
        r.status = status;
        store(answer, r);
        const uint32_t out_total =
            PYMERGETIC_RXF_FRAME_HEADER + answer_length + PYMERGETIC_RXF_FRAME_TRAILER;
        store<uint32_t>(answer + out_total - 4, crc32(answer, out_total - 4));
        const uint32_t out_head = a->tx_head;
        for (uint32_t i = 0; i < out_total; i++)
            tx[(out_head + i) & tx_mask] = answer[i];
        __atomic_store_n(&a->tx_head, out_head + out_total, __ATOMIC_RELEASE);
        __atomic_store_n(&a->rx_tail, tail + total, __ATOMIC_RELEASE);
        a->last_status = status;
        a->frames++;
        served++;
    }
    return served;
}

} // namespace pymergetic::rxf::core::live
