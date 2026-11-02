#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/live/live.hpp>

#include <pymergetic/rxftk/core/arena/mem.hpp>
#include <pymergetic/rxftk/core/arena/object_arena.hpp>
#include <pymergetic/rxftk/core/hash/sha256_core.hpp>

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
 * The write path (the delta apply), the canonical dump and the compaction are moved: the apply
 * replays the physical delta on the journal (docs/19), the dump answers the canonical form
 * (docs/32 5.4), and COMPACT slides the arena onto that form where it sits (docs/20 S8). RELINK
 * is the one write op still not served: it writes the payload slots of a runtime context, and the
 * host engine holds none (its bindings' pump passes no context) - UNBOUND is the honest refusal,
 * and the native twin (native/live.rxfs) owns the walk.
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

    /* ---- the physical delta (live.h's form; tools's physical.py the writer) -- */

    /* One fixup: a handle that is a path until the replay is done. The list
     * lives in the work region (the scratch), never the heap. */
    enum : uint32_t { kPendType, kPendPatch, kPendTarget, kPendRole, kPendEntry };
    struct Pending {
        uint32_t handle;
        uint32_t kind;
        uint32_t at;
        uint32_t string_id;
    };
    Pending *pend_ = nullptr;
    uint32_t pend_count_ = 0;
    uint32_t pend_room_ = 0;

    bool pend_push(uint32_t handle, uint32_t kind, uint32_t at, uint32_t string_id) {
        if (pend_count_ >= pend_room_)
            return false;
        pend_[pend_count_++] = Pending{handle, kind, at, string_id};
        return true;
    }

    /* Fixups are queued against the handle they write into. A reshape (a set
     * that moves the record) hands out a new (index, generation); the fixups
     * follow it (the twin re-points them: native/live.rxfs). A delete frees the
     * index with no successor, so its fixups are dropped instead (the twin:
     * "a delete has no successor, so its fixups are dropped"). Either way a
     * fixup must never write through a stale (index, generation). */
    void pend_repoint(uint32_t from, uint32_t to) {
        for (uint32_t i = 0; i < pend_count_; i++)
            if (pend_[i].handle == from)
                pend_[i].handle = to;
    }

    void pend_drop(uint32_t handle) {
        uint32_t j = 0;
        for (uint32_t i = 0; i < pend_count_; i++) {
            if (pend_[i].handle == handle)
                continue;
            pend_[j++] = pend_[i];
        }
        pend_count_ = j;
    }

    /* The record's fields the replay writes around (docs/32 1.2). */
    bool arena_record(uint32_t handle, pymergetic_rxf_object **record) {
        return arena_.load(handle, record) == arena::Status::Ok;
    }

    bool arena_name(uint32_t handle, char *out, uint32_t capacity, uint32_t *nlen) {
        pymergetic_rxf_object *record = nullptr;
        if (!arena_record(handle, &record))
            return false;
        const char *text = nullptr;
        uint32_t length = 0;
        if (record->name == PYMERGETIC_RXF_SYMS_ANONYMOUS) {
            *nlen = 0;
            return true;
        }
        if (arena_.symbol(record->name, &text, &length) != arena::Status::Ok || length >= capacity)
            return false;
        mem::copy(out, text, length);
        out[length] = 0;
        *nlen = length;
        return true;
    }

    /* The relation table of an object (docs/32 2): sys/relations holds one
     * child per related object, named by the object's dotted path, its payload
     * the pinned reference rows (ground.h pymergetic_rxf_ref). */
    bool write_refs(const uint8_t *path, uint32_t path_len, uint32_t count, const uint8_t *rows,
                    uint32_t *reason) {
        uint32_t sys = PYMERGETIC_RXF_HANDLE32_NONE;
        uint32_t relations = PYMERGETIC_RXF_HANDLE32_NONE;
        if (arena_.find(arena_.root_handle(), "sys", &sys) != arena::Status::Ok ||
            arena_.find(sys, "relations", &relations) != arena::Status::Ok ||
            relations == PYMERGETIC_RXF_HANDLE32_NONE) {
            *reason = PYMERGETIC_RXF_REASON_PATH;
            return false;
        }
        char name[256];
        if (path_len >= sizeof(name)) {
            *reason = PYMERGETIC_RXF_REASON_NAME;
            return false;
        }
        mem::copy(name, path, path_len);
        name[path_len] = 0;
        uint32_t table = PYMERGETIC_RXF_HANDLE32_NONE;
        arena_.find(relations, name, &table);
        const uint64_t room = (uint64_t(count) * sizeof(pymergetic_rxf_ref) + 7) & ~uint64_t(7);
        if (table == PYMERGETIC_RXF_HANDLE32_NONE) {
            const arena::Status s =
                arena_.create(relations, name, PYMERGETIC_RXF_OBJ_SPEC, room, &table);
            if (s != arena::Status::Ok) {
                *reason = reason_of(s);
                return false;
            }
        } else {
            pymergetic_rxf_object *record = nullptr;
            if (!arena_record(table, &record) || room > record->payload) {
                *reason = PYMERGETIC_RXF_REASON_ARENA;
                return false;
            }
        }
        for (uint32_t k = 0; k < count; k++) {
            const pymergetic_rxf_delta_ref r = load<pymergetic_rxf_delta_ref>(rows);
            rows += sizeof(r);
            /* pymergetic_rxf_ref is (target u64, role u64, kind u16, binding u16,
             * index u32): the two handles are paths until the replay is done. */
            uint8_t bytes[sizeof(pymergetic_rxf_ref)] = {0};
            const uint64_t none = PYMERGETIC_RXF_HANDLE_NONE;
            mem::copy(bytes, &none, 8);
            mem::copy(bytes + 8, &none, 8);
            mem::copy(bytes + 16, &r.kind, 2);
            mem::copy(bytes + 18, &r.binding, 2);
            mem::copy(bytes + 20, &r.index, 4);
            const uint64_t at = uint64_t(k) * sizeof(pymergetic_rxf_ref);
            if (arena_.set_payload_at(table, at, bytes, sizeof(bytes)) != arena::Status::Ok) {
                *reason = PYMERGETIC_RXF_REASON_ARENA;
                return false;
            }
            if (r.target != PYMERGETIC_RXF_STRING_NONE &&
                !pend_push(table, kPendTarget, at, r.target)) {
                *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                return false;
            }
            if (r.role != PYMERGETIC_RXF_STRING_NONE &&
                !pend_push(table, kPendRole, at + 8, r.role)) {
                *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                return false;
            }
        }
        return true;
    }

    void set_entry(uint32_t handle) {
        arena_.super()->entry = handle;
    }

    /* Whether a handle a payload slot or a row holds names an object that is there: none is
     * fine (an unset slot), anything else must pass the index entry's generation check. */
    bool holds_live(uint64_t value) {
        if (value == PYMERGETIC_RXF_HANDLE_NONE)
            return true;
        pymergetic_rxf_object *record = nullptr;
        return value <= 0xFFFFFFFFULL &&
               arena_.load(static_cast<uint32_t>(value), &record) == arena::Status::Ok;
    }

    /* Every handle that something holds points at an object that is there (the twin's
     * check_all, native/live.rxfs): the entry, the parent and the type of each object, the
     * target and role of each row of its relation table, and each declared reference slot
     * of its value. The declared slots are ObjectArena::fixup_refs's rule: the Field
     * children of the row's type that carry FIELD_FLAG_REF, each `count` slots from its
     * `offset` (behind the module head when the object is a module). The dump unsets such
     * a handle silently (docs/32 5.4: the canonical form drops what is gone), so the
     * replay must refuse it before the commit. The reason stays DANGLING unless the type's
     * layout is more than the walk reads (LAYOUT). */
    bool check_all(uint32_t *reason) {
        *reason = PYMERGETIC_RXF_REASON_DANGLING;
        at_ = 0x40000000u; /* the image itself: the run entry */
        fail_target_ = arena_.super()->entry;
        fail_p_ = reinterpret_cast<const uint8_t *>("(entry)");
        fail_n_ = 7;
        if (arena_.super()->entry != PYMERGETIC_RXF_HANDLE32_NONE &&
            !holds_live(arena_.super()->entry))
            return false;
        const uint32_t count = arena_.handle_count();
        for (uint32_t index = 0; index < count; index++) {
            const uint64_t h = packed(index);
            if (h == 0)
                continue;
            const uint32_t handle = static_cast<uint32_t>(h);
            at_ = handle; /* the source node: whichever check below fails names it */
            const char *subject = nullptr;
            uint32_t subject_len = 0;
            if (name_of(handle, &subject, &subject_len)) {
                fail_p_ = reinterpret_cast<const uint8_t *>(subject);
                fail_n_ = subject_len;
            }
            pymergetic_rxf_object *record = nullptr;
            if (arena_.load(handle, &record) != arena::Status::Ok)
                return false;
            fail_target_ = record->parent;
            if (record->parent != PYMERGETIC_RXF_HANDLE32_NONE && !holds_live(record->parent))
                return false;
            pymergetic_rxf_spec row{};
            if (arena_.spec(handle, &row) != arena::Status::Ok)
                return false;
            fail_target_ = row.type;
            if (row.type != PYMERGETIC_RXF_HANDLE32_NONE && !holds_live(row.type))
                return false;
            uint32_t held = 0;
            Status st = arena_.refs_of(handle, nullptr, 0, &held);
            if (st != Status::Ok && st != Status::OutOfMemory)
                return false;
            if (held != 0) {
                /* The rows come out of the arena once, into the work region; the
                 * mark gives the room back before the next object. */
                uint8_t *mark = scratch_at_;
                auto *rows = static_cast<pymergetic_rxf_ref *>(
                    take(sizeof(pymergetic_rxf_ref) * uint64_t(held)));
                if (rows == nullptr) {
                    *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                    return false;
                }
                if (arena_.refs_of(handle, rows, held, &held) != Status::Ok)
                    return false;
                for (uint32_t k = 0; k < held; k++) {
                    fail_target_ = rows[k].target;
                    if (!holds_live(rows[k].target) || !holds_live(rows[k].role))
                        return false;
                }
                scratch_at_ = mark;
            }
            if (row.type == PYMERGETIC_RXF_HANDLE32_NONE || record->payload == 0)
                continue;
            uint32_t fields[64];
            uint32_t fields_held = 0;
            if (arena_.children(row.type, fields, 64, &fields_held) != arena::Status::Ok ||
                fields_held > 64) {
                *reason = PYMERGETIC_RXF_REASON_LAYOUT;
                return false;
            }
            const uint8_t *value = nullptr;
            uint32_t size = 0;
            if (arena_.payload(handle, &value, &size) != arena::Status::Ok)
                return false;
            const uint64_t head =
                record->kind == PYMERGETIC_RXF_OBJ_MODULE ? sizeof(pymergetic_rxf_module) : 0;
            for (uint32_t k = 0; k < fields_held; k++) {
                const uint8_t *bytes = nullptr;
                uint32_t field_size = 0;
                if (arena_.payload(fields[k], &bytes, &field_size) != arena::Status::Ok ||
                    field_size < sizeof(pymergetic_rxf_field))
                    continue;
                pymergetic_rxf_field field{};
                mem::copy(&field, bytes, sizeof(field));
                if ((field.flags & PYMERGETIC_RXF_FIELD_FLAG_REF) == 0)
                    continue;
                for (uint64_t i = 0; i < field.count; i++) {
                    const uint64_t at = head + field.offset + 8 * i;
                    if (at + 8 > size)
                        break;
                    uint64_t slot = 0;
                    mem::copy(&slot, value + at, 8);
                    fail_target_ = static_cast<uint32_t>(slot);
                    if (!holds_live(slot))
                        return false;
                }
            }
        }
        return true;
    }

    /* A string of the delta's block: the id is its offset, 0xFFFFFFFF = none. */
    static bool delta_string(const uint8_t *strings, uint32_t size, uint32_t id, const uint8_t **p,
                             uint32_t *n) {
        if (id == PYMERGETIC_RXF_STRING_NONE) {
            *p = nullptr;
            *n = 0;
            return true;
        }
        if (id >= size)
            return false;
        uint32_t e = id;
        while (e < size && strings[e] != 0)
            e++;
        if (e >= size)
            return false;
        *p = strings + id;
        *n = e - id;
        return true;
    }

    static uint32_t reason_of(arena::Status s) {
        switch (s) {
        case arena::Status::JournalFull:
            return PYMERGETIC_RXF_REASON_JOURNAL;
        case arena::Status::NameTaken:
            return PYMERGETIC_RXF_REASON_EXISTS;
        case arena::Status::BadName:
            return PYMERGETIC_RXF_REASON_NAME;
        case arena::Status::Protected:
            return PYMERGETIC_RXF_REASON_PROTECTED;
        case arena::Status::HasChildren:
            /* The twin's own map (native/live.rxfs: status 10 answers 9):
             * a parent with children stays until they are gone - what would
             * point at them is why it refuses. */
            return PYMERGETIC_RXF_REASON_DANGLING;
        case arena::Status::Dangling:
            return PYMERGETIC_RXF_REASON_DANGLING;
        case arena::Status::BadHandle:
        case arena::Status::BadPath:
            return PYMERGETIC_RXF_REASON_PATH;
        case arena::Status::IndexFull:
            /* The index has no slot to hand out - live.h names it under ARENA
             * ("the arena has no room, or the index is full"), the twin's map
             * answers the same (its status 4 falls to the default 6). A create
             * that names an object that is there is the pre-check's own EXISTS,
             * never this. */
            return PYMERGETIC_RXF_REASON_ARENA;
        default:
            return PYMERGETIC_RXF_REASON_ARENA;
        }
    }

    /* The object a dotted path names, under the root (docs/32 2: the root's own
     * name is the image, not a path segment). */
    bool delta_resolve(const uint8_t *p, uint32_t n, uint32_t *handle) {
        fail_p_ = p;
        fail_n_ = n;
        uint32_t at = arena_.root_handle();
        uint32_t cut = 0;
        while (cut < n) {
            uint32_t end = cut;
            while (end < n && p[end] != '.')
                end++;
            char name[256];
            if (end == cut || end - cut >= sizeof(name))
                return false;
            mem::copy(name, p + cut, end - cut);
            name[end - cut] = 0;
            if (arena_.find(at, name, &at) != arena::Status::Ok ||
                at == PYMERGETIC_RXF_HANDLE32_NONE)
                return false;
            cut = end + 1;
        }
        *handle = at;
        return true;
    }

    /* Whether a name can be given to an object (the twin's good_name,
     * native/live.rxfs): not empty, not '@', no '.' and no control bytes, and
     * the whole of it a well-formed UTF-8 sequence. The model refuses the
     * same names at add (Image::add), so the engine does too - a name a
     * create carries is refused with its own reason (live.h: NAME), never
     * taken as bytes the tree would then have to walk. */
    static bool good_name(const uint8_t *p, uint32_t n) {
        if (n == 0 || p[0] == '@')
            return false;
        uint32_t i = 0;
        while (i < n) {
            if (p[i] == '.' || p[i] < 32 || p[i] == 127)
                return false;
            i++;
        }
        i = 0;
        while (i < n) {
            const uint8_t b = p[i];
            if (b < 128) {
                i++;
                continue;
            }
            uint32_t extra = 0;
            uint32_t low = 0x80;
            uint32_t code = 0;
            if (b >= 0xC2 && b <= 0xDF) {
                extra = 1;
                code = b & 0x1F;
            } else if (b >= 0xE0 && b <= 0xEF) {
                extra = 2;
                code = b & 0x0F;
                low = 0x800;
            } else if (b >= 0xF0 && b <= 0xF4) {
                extra = 3;
                code = b & 0x07;
                low = 0x10000;
            } else {
                return false;
            }
            if (i + extra >= n)
                return false;
            for (uint32_t k = 1; k <= extra; k++) {
                if ((p[i + k] & 0xC0) != 0x80)
                    return false;
                code = (code << 6) | (p[i + k] & 0x3F);
            }
            if (code < low || code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF))
                return false;
            i += extra + 1;
        }
        return true;
    }

    /* The record's storage role (the writer's map, tools/native/arena_export.py):
     * the world's node kind in the flags' low bits, and anything with children
     * is a Module. */
    static uint16_t storage_role(uint32_t flags, bool module) {
        if (module)
            return PYMERGETIC_RXF_OBJ_MODULE;
        switch (flags & PYMERGETIC_RXF_FLAG_KIND_MASK) {
        case PYMERGETIC_RXF_NODE_FN:
        case PYMERGETIC_RXF_NODE_CODE:
            return PYMERGETIC_RXF_OBJ_FUNC;
        case PYMERGETIC_RXF_NODE_TYPE:
        case PYMERGETIC_RXF_NODE_FIELD:
        case PYMERGETIC_RXF_NODE_ENUMERATOR:
            return PYMERGETIC_RXF_OBJ_SPEC;
        case PYMERGETIC_RXF_NODE_IMPORT:
        case PYMERGETIC_RXF_NODE_EXPORT:
        case PYMERGETIC_RXF_NODE_ALIAS:
            return PYMERGETIC_RXF_OBJ_SYM;
        default:
            return PYMERGETIC_RXF_OBJ_BLOB;
        }
    }

    /* The delta applied to the image, all or nothing (docs/19): the stage is
     * parsed in two passes - the format and the fixups it will need, then the
     * writes on the journal - and whatever fails is rolled back to the bytes of
     * the last commit. The answer is the digest of the result (docs/32 5.4):
     * the apply and the dump are the same image's two faces. */
    bool apply(uint32_t length, uint32_t *reason) {
        *reason = PYMERGETIC_RXF_REASON_FORMAT;
        const uint8_t *d = ptr(a_->stage_base);
        if (length < sizeof(pymergetic_rxf_delta_header))
            return false;
        const pymergetic_rxf_delta_header head = load<pymergetic_rxf_delta_header>(d);
        if (head.magic != PYMERGETIC_RXF_DELTA_MAGIC ||
            head.version != PYMERGETIC_RXF_DELTA_VERSION)
            return false;
        const uint8_t *strings = d + sizeof(head);
        const uint32_t ssize = head.string_bytes;
        const uint64_t ops_start = (sizeof(head) + ssize + 7) & ~uint64_t(7);
        if (sizeof(head) + uint64_t(ssize) > length || ops_start > length ||
            (ssize != 0 && strings[ssize - 1] != 0))
            return false;
        if ((head.flags & PYMERGETIC_RXF_DELTA_CHECK_BASE) != 0) {
            /* The base is the digest of the image the delta was compiled
             * against (docs/32 5.4: the digest of those bytes). */
            if (!dump())
                return false;
            if (mem::compare(a_->emit_digest, head.base_digest, 32) != 0) {
                *reason = PYMERGETIC_RXF_REASON_BASE;
                return false;
            }
        }

        /* Pass 1: the format of every operation and the fixups it will need.
         * The type of a record and the targets of its patches and refs are
         * paths; they resolve after the replay, when an object the delta
         * itself creates is there to point at. */
        uint64_t fixups = 0;
        uint64_t pos = ops_start;
        for (uint32_t i = 0; i < head.op_count; i++) {
            if (pos + sizeof(uint32_t) > length)
                return false;
            const uint32_t op = load<uint32_t>(d + pos);
            const uint8_t *p = nullptr;
            uint32_t n = 0;
            if (op == PYMERGETIC_RXF_DELTA_CREATE || op == PYMERGETIC_RXF_DELTA_SET) {
                if (pos + sizeof(pymergetic_rxf_delta_object) > length)
                    return false;
                const pymergetic_rxf_delta_object o = load<pymergetic_rxf_delta_object>(d + pos);
                const uint64_t size = sizeof(o) +
                                      ((uint64_t(o.payload_length) + 7) & ~uint64_t(7)) +
                                      uint64_t(o.patch_count) * sizeof(pymergetic_rxf_delta_patch) +
                                      uint64_t(o.ref_count) * sizeof(pymergetic_rxf_delta_ref);
                if (pos + size > length)
                    return false;
                if (!delta_string(strings, ssize, o.path, &p, &n) || n == 0)
                    return false;
                if (o.type != PYMERGETIC_RXF_STRING_NONE) {
                    if (!delta_string(strings, ssize, o.type, &p, &n))
                        return false;
                    fixups++;
                }
                const uint8_t *tail =
                    d + pos + sizeof(o) + ((uint64_t(o.payload_length) + 7) & ~uint64_t(7));
                for (uint32_t k = 0; k < o.patch_count; k++) {
                    const pymergetic_rxf_delta_patch pt = load<pymergetic_rxf_delta_patch>(tail);
                    tail += sizeof(pt);
                    if (uint64_t(pt.offset) + 8 > o.payload_length)
                        return false;
                    if (!delta_string(strings, ssize, pt.path, &p, &n))
                        return false;
                    if (pt.path != PYMERGETIC_RXF_STRING_NONE)
                        fixups++;
                }
                for (uint32_t k = 0; k < o.ref_count; k++) {
                    const pymergetic_rxf_delta_ref r = load<pymergetic_rxf_delta_ref>(tail);
                    tail += sizeof(r);
                    if (!delta_string(strings, ssize, r.target, &p, &n) ||
                        !delta_string(strings, ssize, r.role, &p, &n))
                        return false;
                    if (r.target != PYMERGETIC_RXF_STRING_NONE)
                        fixups++;
                    if (r.role != PYMERGETIC_RXF_STRING_NONE)
                        fixups++;
                }
                pos += size;
            } else if (op == PYMERGETIC_RXF_DELTA_DELETE || op == PYMERGETIC_RXF_DELTA_ENTRY) {
                const uint32_t s = load<uint32_t>(d + pos + sizeof(uint32_t));
                if (!delta_string(strings, ssize, s, &p, &n))
                    return false;
                pos += 2 * sizeof(uint32_t);
            } else {
                return false;
            }
        }
        if (pos != length)
            return false;

        /* The fixups' list lives in the work region (the scratch): the replay
         * writes it and the pass after reads it. */
        pend_count_ = 0;
        pend_room_ = uint32_t(fixups + 1);
        pend_ = static_cast<Pending *>(take(sizeof(Pending) * (fixups + 1)));
        if (pend_ == nullptr) {
            *reason = PYMERGETIC_RXF_REASON_SCRATCH;
            return false;
        }

        /* Pass 2: the writes. The module rule is the writer's - anything the
         * delta itself gives children is a Module (docs/32 2: the storage role
         * is the record's, not the semantic kind's). */
        *reason = PYMERGETIC_RXF_REASON_PATH;
        pos = ops_start;
        for (uint32_t i = 0; i < head.op_count; i++) {
            at_ = i;
            const uint32_t op = load<uint32_t>(d + pos);
            const uint8_t *p = nullptr;
            uint32_t n = 0;
            if (op == PYMERGETIC_RXF_DELTA_CREATE || op == PYMERGETIC_RXF_DELTA_SET) {
                const pymergetic_rxf_delta_object o = load<pymergetic_rxf_delta_object>(d + pos);
                const uint64_t size = sizeof(o) +
                                      ((uint64_t(o.payload_length) + 7) & ~uint64_t(7)) +
                                      uint64_t(o.patch_count) * sizeof(pymergetic_rxf_delta_patch) +
                                      uint64_t(o.ref_count) * sizeof(pymergetic_rxf_delta_ref);
                const uint8_t *body = d + pos + sizeof(o);
                pos += size;
                delta_string(strings, ssize, o.path, &p, &n);
                uint32_t handle = PYMERGETIC_RXF_HANDLE32_NONE;
                if (op == PYMERGETIC_RXF_DELTA_CREATE) {
                    uint32_t cut = n;
                    while (cut > 0 && p[cut - 1] != '.')
                        cut--;
                    if (cut == 0) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        return false;
                    }
                    char name[256];
                    mem::copy(name, p + cut, n - cut);
                    name[n - cut] = 0;
                    uint32_t parent = PYMERGETIC_RXF_HANDLE32_NONE;
                    if (!delta_resolve(p, cut - 1, &parent)) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        return false;
                    }
                    /* A name the model would refuse (Image::add) is refused
                     * here with the name's own reason (live.h: NAME): the
                     * twin's good_name, the same bytes checked the same way
                     * (native/live.rxfs). */
                    if (!good_name(reinterpret_cast<const uint8_t *>(name), uint32_t(n - cut))) {
                        *reason = PYMERGETIC_RXF_REASON_NAME;
                        return false;
                    }
                    /* The model refuses a taken name (Image::add: NameTaken)
                     * and the writer's world never places two objects under
                     * one parent of one name: a create that names an object
                     * that is already there is refused with its own reason
                     * (live.h: EXISTS) - a set is a set, and a duplicate
                     * create is never one. The check walks the parent's
                     * chain (arena find), not the lookup table: the table is
                     * a mirror built at attach and after each commit, and an
                     * object this delta created earlier is in the chain
                     * already. A parent that is a leaf has no chain yet -
                     * its first child is what turns it into a module
                     * (into_module, below) - so only a module parent's chain
                     * can hold the name. */
                    {
                        pymergetic_rxf_object *parent_record = nullptr;
                        uint32_t taken = PYMERGETIC_RXF_HANDLE32_NONE;
                        if (arena_.load(parent, &parent_record) != arena::Status::Ok) {
                            *reason = PYMERGETIC_RXF_REASON_PATH;
                            return false;
                        }
                        if (parent_record->kind == PYMERGETIC_RXF_OBJ_MODULE &&
                            (arena_.find(parent, name, &taken) != arena::Status::Ok ||
                             taken != PYMERGETIC_RXF_HANDLE32_NONE)) {
                            *reason = PYMERGETIC_RXF_REASON_EXISTS;
                            return false;
                        }
                    }
                    bool module = false;
                    for (uint64_t scan = pos; scan + sizeof(uint32_t) <= length && !module;) {
                        const uint32_t scan_op = load<uint32_t>(d + scan);
                        if (scan_op == PYMERGETIC_RXF_DELTA_CREATE ||
                            scan_op == PYMERGETIC_RXF_DELTA_SET) {
                            const pymergetic_rxf_delta_object sib =
                                load<pymergetic_rxf_delta_object>(d + scan);
                            const uint8_t *sp = nullptr;
                            uint32_t sn = 0;
                            delta_string(strings, ssize, sib.path, &sp, &sn);
                            module = scan_op == PYMERGETIC_RXF_DELTA_CREATE && sn > n + 1 &&
                                     mem::compare(sp, p, n) == 0 && sp[n] == '.';
                            scan += sizeof(sib) +
                                    ((uint64_t(sib.payload_length) + 7) & ~uint64_t(7)) +
                                    uint64_t(sib.patch_count) * sizeof(pymergetic_rxf_delta_patch) +
                                    uint64_t(sib.ref_count) * sizeof(pymergetic_rxf_delta_ref);
                        } else {
                            scan += 2 * sizeof(uint32_t);
                        }
                    }
                    const uint16_t kind = storage_role(o.flags, module);
                    const uint64_t room = (module ? sizeof(pymergetic_rxf_module) : 0) +
                                          ((uint64_t(o.payload_length) + 7) & ~uint64_t(7));
                    /* A leaf that gains a child becomes a module (docs/32 1.2):
                     * the parent's value moves behind a chain head first. */
                    if (parent != PYMERGETIC_RXF_HANDLE32_NONE) {
                        const arena::Status t = arena_.into_module(parent, &parent);
                        if (t != arena::Status::Ok) {
                            *reason = reason_of(t);
                            return false;
                        }
                    }
                    const arena::Status s = arena_.create(parent, name, kind, room, &handle);
                    if (s != arena::Status::Ok) {
                        *reason = reason_of(s);
                        return false;
                    }
                } else {
                    if (!delta_resolve(p, n, &handle)) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        return false;
                    }
                    pymergetic_rxf_object *old = nullptr;
                    if (!arena_record(handle, &old)) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        return false;
                    }
                    const uint64_t want =
                        (old->kind == PYMERGETIC_RXF_OBJ_MODULE ? sizeof(pymergetic_rxf_module)
                                                                : 0) +
                        ((uint64_t(o.payload_length) + 7) & ~uint64_t(7));
                    if (want != old->payload) {
                        /* A record whose room is not its value's room is freed
                         * and re-grown (docs/32 1.2), in both directions: one
                         * that outgrows its room needs the room, and one whose
                         * value shrank must not keep the bigger one - the room
                         * is the object's size in the canonical form (docs/32
                         * 5.4), so a stale room would make the image's state
                         * not the delta's state. The index is handed out again
                         * and the generation bumps. */
                        const uint32_t parent = old->parent;
                        const uint16_t kind = old->kind;
                        char name[256];
                        uint32_t nlen = 0;
                        if (!arena_name(handle, name, sizeof(name), &nlen)) {
                            *reason = PYMERGETIC_RXF_REASON_NAME;
                            return false;
                        }
                        if (arena_.release_handle(handle) != arena::Status::Ok) {
                            *reason = PYMERGETIC_RXF_REASON_PROTECTED;
                            return false;
                        }
                        const uint32_t stale = handle;
                        const arena::Status s = arena_.create(parent, name, kind, want, &handle);
                        if (s != arena::Status::Ok) {
                            *reason = reason_of(s);
                            return false;
                        }
                        /* A fixup that was pushed for the record before its
                         * re-grow holds the handle the index carried then: the
                         * index is handed out again with a new generation, so a
                         * fixup left on the old handle would name a slot that
                         * is no longer this object (the arena answers
                         * BadHandle). The fixups follow the object. */
                        pend_repoint(stale, handle);
                    }
                }
                /* The state: the semantic row (docs/32 5) and the value. */
                pymergetic_rxf_spec row{};
                row.type = PYMERGETIC_RXF_HANDLE32_NONE;
                row.flags = o.flags;
                row.index = o.index;
                row.generation = o.generation;
                /* The delta's payload is the world's value (physical.py's
                 * records); a module's arena payload opens with the chain head
                 * (the writer's EMPTY_MODULE_HEAD), so the value follows it. */
                pymergetic_rxf_object *made = nullptr;
                if (!arena_record(handle, &made)) {
                    *reason = PYMERGETIC_RXF_REASON_ARENA;
                    return false;
                }
                const uint64_t head =
                    made->kind == PYMERGETIC_RXF_OBJ_MODULE ? sizeof(pymergetic_rxf_module) : 0;
                if (o.payload_length != 0) {
                    const arena::Status s =
                        arena_.set_payload_at(handle, head, body, o.payload_length);
                    if (s != arena::Status::Ok) {
                        *reason = reason_of(s);
                        return false;
                    }
                }
                /* The room behind the value is zeros, not the tail of the value
                 * that sat there before (docs/32 1.2: the delta's state is the
                 * image's state in both directions; the writer pads every value
                 * to its room with zeros, arena_export.py's `ljust`). A value
                 * whose room changed was re-grown above and born zeroed, so
                 * what is cleared here is the room's own tail: the padding of
                 * the value's last 8-byte slot, at most 7 bytes. */
                const uint32_t pad = uint32_t(((uint64_t(o.payload_length) + 7) & ~uint64_t(7)) -
                                              uint64_t(o.payload_length));
                if (pad != 0) {
                    static const uint8_t kZeros[8] = {0, 0, 0, 0, 0, 0, 0, 0};
                    const arena::Status s = arena_.set_payload_at(
                        handle, head + uint64_t(o.payload_length), kZeros, pad);
                    if (s != arena::Status::Ok) {
                        *reason = reason_of(s);
                        return false;
                    }
                }
                /* The patches: the payload's declared handle slots hold paths
                 * until the replay is done (live.h: every handle is a path). */
                const uint8_t *tail = body + ((uint64_t(o.payload_length) + 7) & ~uint64_t(7));
                for (uint32_t k = 0; k < o.patch_count; k++) {
                    const pymergetic_rxf_delta_patch pt = load<pymergetic_rxf_delta_patch>(tail);
                    tail += sizeof(pt);
                    if (pt.path == PYMERGETIC_RXF_STRING_NONE) {
                        const uint64_t none = PYMERGETIC_RXF_HANDLE_NONE;
                        /* An offset into the VALUE, like the path patches' (the
                         * module head stands in front of it). */
                        const arena::Status s = arena_.set_payload_at(
                            handle, pt.offset + head, reinterpret_cast<const uint8_t *>(&none), 8);
                        if (s != arena::Status::Ok) {
                            *reason = reason_of(s);
                            return false;
                        }
                    } else if (!pend_push(handle, kPendPatch, pt.offset + head, pt.path)) {
                        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                        return false;
                    }
                }
                /* tail has already walked past the patches above; the ref rows
                 * follow them in the record (docs/32 5.2). */
                if (o.ref_count != 0) {
                    if (!write_refs(p, n, o.ref_count, tail, reason))
                        return false;
                    tail += uint64_t(o.ref_count) * sizeof(pymergetic_rxf_delta_ref);
                }
                /* The type is a path like the patches (live.h): an object the
                 * delta creates without one keeps no type at all - the root is
                 * what an empty path resolves to and it is not a Type. */
                if (o.type != PYMERGETIC_RXF_STRING_NONE &&
                    !pend_push(handle, kPendType, 0, o.type)) {
                    *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                    return false;
                }
                const arena::Status s = arena_.set_spec(handle, row);
                if (s != arena::Status::Ok) {
                    *reason = reason_of(s);
                    return false;
                }
            } else if (op == PYMERGETIC_RXF_DELTA_DELETE) {
                delta_string(strings, ssize, load<uint32_t>(d + pos + 4), &p, &n);
                pos += 8;
                uint32_t handle = PYMERGETIC_RXF_HANDLE32_NONE;
                if (!delta_resolve(p, n, &handle)) {
                    *reason = PYMERGETIC_RXF_REASON_PATH;
                    return false;
                }
                /* The trio the model pins (Image::remove: root, Type and
                 * Field) is refused with its own reason (live.h: PROTECTED)
                 * before any arena word is touched - the same refusal the
                 * twin answers (native/live.rxfs: handles 1 and 2 are the
                 * ground's, however the arena numbers its own). They are
                 * found by path from the root, like the twin's own resolve. */
                if (handle == arena_.root_handle()) {
                    *reason = PYMERGETIC_RXF_REASON_PROTECTED;
                    return false;
                }
                {
                    uint32_t type_handle = PYMERGETIC_RXF_HANDLE32_NONE;
                    uint32_t field_handle = PYMERGETIC_RXF_HANDLE32_NONE;
                    if (arena_.find(arena_.root_handle(), "Type", &type_handle) ==
                            arena::Status::Ok &&
                        arena_.find(arena_.root_handle(), "Field", &field_handle) ==
                            arena::Status::Ok) {
                        if (handle == type_handle || handle == field_handle) {
                            *reason = PYMERGETIC_RXF_REASON_PROTECTED;
                            return false;
                        }
                    }
                }
                const arena::Status s = arena_.release_handle(handle);
                if (s != arena::Status::Ok) {
                    *reason = reason_of(s);
                    return false;
                }
                pend_drop(handle);
            } else { /* ENTRY: the image's entry, the path of the run */
                const uint32_t s = load<uint32_t>(d + pos + 4);
                delta_string(strings, ssize, s, &p, &n);
                pos += 8;
                if (s == PYMERGETIC_RXF_STRING_NONE)
                    set_entry(PYMERGETIC_RXF_HANDLE32_NONE);
                else if (!pend_push(0, kPendEntry, 0, s)) {
                    *reason = PYMERGETIC_RXF_REASON_SCRATCH;
                    return false;
                }
            }
        }

        /* Pass 3: the fixups - the paths that only the replay made real. */
        for (uint32_t k = 0; k < pend_count_; k++) {
            at_ = 0x80000000u | k;
            const Pending &q = pend_[k];
            const uint8_t *p = nullptr;
            uint32_t n = 0;
            delta_string(strings, ssize, q.string_id, &p, &n);
            uint32_t target = PYMERGETIC_RXF_HANDLE32_NONE;
            if (!delta_resolve(p, n, &target)) {
                /* A fixup names its subject by path (live.h: every handle is a
                 * path) and this pass runs after every create the delta makes:
                 * a path that still names nothing is the model's own refusal
                 * ("a name that names nothing", reason PATH) - an unset write
                 * would commit a world the delta did not describe. The fixups
                 * of a record this replay deleted are dropped where it is
                 * deleted (pend_drop above), never here. */
                *reason = PYMERGETIC_RXF_REASON_PATH;
                return false;
            }
            if (q.kind == kPendType) {
                pymergetic_rxf_spec row{};
                if (arena_.spec(q.handle, &row) != arena::Status::Ok) {
                    *reason = PYMERGETIC_RXF_REASON_ARENA;
                    return false;
                }
                row.type = target;
                if (arena_.set_spec(q.handle, row) != arena::Status::Ok) {
                    *reason = PYMERGETIC_RXF_REASON_ARENA;
                    return false;
                }
            } else if (q.kind == kPendPatch) {
                const uint64_t value = target;
                if (arena_.set_payload_at(q.handle, q.at, reinterpret_cast<const uint8_t *>(&value),
                                          8) != arena::Status::Ok) {
                    *reason = PYMERGETIC_RXF_REASON_ARENA;
                    return false;
                }
            } else if (q.kind == kPendEntry) {
                set_entry(target);
            } else { /* kPendTarget / kPendRole: a row of the relation table */
                const uint64_t value = target;
                if (arena_.set_payload_at(q.handle, q.at, reinterpret_cast<const uint8_t *>(&value),
                                          8) != arena::Status::Ok) {
                    *reason = PYMERGETIC_RXF_REASON_ARENA;
                    return false;
                }
            }
        }
        /* Pass 4: nothing may point at what is gone (the twin's check_all):
         * the dump would unset such a slot and the commit would answer a
         * digest of a world the delta did not describe. */
        if (!check_all(reason))
            return false;
        *reason = 0;
        return true;
    }

    /* The canonical form of the image (docs/32 5.4): a rebuild into the emit
     * region, its bytes the dump and their SHA-256 the digest. The region IS
     * the output arena's memory, so the dump is no copy: the canonical bytes
     * are the region's prefix and emit_length is where they end. Two images
     * that carry the same world answer the same bytes. */
    bool dump() {
        arena::Memory into{};
        into.base = ptr(a_->emit_base);
        into.committed = a_->emit_capacity;
        into.limit = a_->emit_capacity;
        into.commit = nullptr;
        arena::ObjectArena out;
        if (out.init(into, 0, 0) != arena::Status::Ok)
            return false;
        /* The remap: one entry per source index, in the work region (the
         * engine has no heap). */
        const uint32_t count = arena_.handle_count();
        auto *remap = static_cast<uint32_t *>(take(sizeof(uint32_t) * count));
        if (remap == nullptr)
            return false;
        if (arena_.rebuild(out, remap, count) != arena::Status::Ok)
            return false;
        uint64_t need = 0;
        if (out.image(nullptr, 0, &need) != arena::Status::OutOfMemory || need > a_->emit_capacity)
            return false;
        a_->emit_length = need;
        hash::Sha256Core sha;
        sha.init();
        sha.update(ptr(a_->emit_base), need);
        sha.finish(a_->emit_digest);
        return true;
    }

    /* The compaction (docs/20 S8, live.h COMPACT): the canonical bytes of the
     * dump replace the arena's, in place. No undo log is involved - the
     * bytes are the rebuild's, and the rebuild has verified them. The arena's
     * claim (the superblock's limit) is a record of how much room the image
     * says it needs, not the size of the region it was written to, so the
     * copied marker keeps the source's claim; the region's own committed size
     * is the area's word (arena_committed), never the bytes'. The lookup
     * table and the journal bounds are re-derived from what now sits there -
     * a cold attach would find the same (docs/32 3: the journal is not
     * stored, it is re-derived from the tree). A pinned object (its state's
     * mobility bits) forbids the move before a byte is touched. */
    bool compact(uint32_t *reason) {
        *reason = PYMERGETIC_RXF_REASON_PINNED;
        const uint32_t count = arena_.handle_count();
        for (uint32_t index = 0; index < count; index++) {
            const uint64_t h = packed(index);
            if (h == 0)
                continue;
            pymergetic_rxf_spec row{};
            if (arena_.spec(static_cast<uint32_t>(h), &row) != arena::Status::Ok)
                return false;
            const uint32_t mobility = (row.flags >> (PYMERGETIC_RXF_FLAG_STATE_SHIFT +
                                                     PYMERGETIC_RXF_STATE_MOBILITY_SHIFT)) &
                                      3U;
            if (mobility == PYMERGETIC_RXF_MOBILITY_PINNED)
                return false;
        }
        *reason = PYMERGETIC_RXF_REASON_ARENA;
        const uint64_t before =
            arena_.committed() + sizeof(pymergetic_rxf_object) + PYMERGETIC_RXF_FREE_LINKS;
        if (!dump())
            return false;
        const uint64_t length = a_->emit_length;
        uint8_t *base = ptr(a_->arena_base);
        mem::copy(base, ptr(a_->emit_base), length);
        /* The arena region is the area's own: committed whole or in part, the
         * pages the host made stay made - only the stream's tail between the
         * new committed and the old image's end is the compaction's to zero
         * (the copied marker's links are the fresh ring, the absorbed words
         * beyond them are not records any more). */
        if (before > length)
            mem::fill(base + length, 0, before - length);
        Memory m;
        m.base = base;
        m.committed = a_->arena_committed == 0 ? a_->arena_capacity : a_->arena_committed;
        m.limit = reinterpret_cast<const pymergetic_rxf_superblock *>(base)->limit;
        if (arena_.attach(m) != arena::Status::Ok)
            return false;
        if (!build_table()) {
            *reason = PYMERGETIC_RXF_REASON_ARENA;
            return false;
        }
        a_->index_state = 1;
        a_->compactions++;
        reclaimed_ = before - length;
        *reason = 0;
        return true;
    }

    uint16_t serve(uint8_t op, uint32_t argument, const uint8_t *rq, uint32_t rl, uint8_t *rs,
                   uint32_t *rsl, uint32_t *rarg) {
        *rsl = 0;
        *rarg = argument;
        scratch_reset();
        /* The read side answers from the v7 model (docs/32 5.3). RELINK is the
         * one write op not served: it writes the payload slots of a runtime
         * context (native/stdlib_runtime.h), and the host engine holds none
         * (its bindings' pump passes no context) - UNBOUND is the honest
         * refusal, the same reason the twin answers when it has no context.
         * COMPACT is served below (docs/20 S8): the canonical form, in place. */
        if (op == PYMERGETIC_RXF_OP_RELINK) {
            store<uint32_t>(rs, 0);
            store<uint32_t>(rs + 4, context_ == nullptr || a_->entries_capacity == 0
                                        ? PYMERGETIC_RXF_REASON_UNBOUND
                                        : PYMERGETIC_RXF_REASON_LINK);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_REFUSED;
        }
        switch (op) {
        case PYMERGETIC_RXF_OP_STAGE_COMMIT: {
            /* length, SHA-256 of the stage: apply it, all or nothing (live.h). */
            if (rl < 36)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const uint32_t length = load<uint32_t>(rq);
            if (length > a_->stage_length)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            hash::Sha256Core sha;
            sha.init();
            sha.update(ptr(a_->stage_base), length);
            uint8_t digest[32];
            sha.finish(digest);
            if (mem::compare(digest, rq + 4, 32) != 0)
                return PYMERGETIC_RXF_LIVE_DIGEST;
            uint32_t reason = 0;
            if (!apply(length, &reason)) {
                arena_.journal_rollback();
                a_->rejects++;
                store<uint32_t>(rs, at_);
                store<uint32_t>(rs + 4, reason);
                store<uint32_t>(rs + 8, fail_target_);
                /* Carry the subject (the path whose resolve failed, or the
                 * source node's name for a check_all dangling) after the (at,
                 * reason, target) triple so the client names it; one that would
                 * not fit a frame keeps the bare triple. */
                uint32_t carried = 12;
                if (fail_p_ != nullptr && fail_n_ != 0 &&
                    12 + fail_n_ <= PYMERGETIC_RXF_FRAME_MAX) {
                    mem::copy(rs + 12, fail_p_, fail_n_);
                    carried = 12 + fail_n_;
                }
                *rsl = carried;
                /* The delta was refused (frames.APPLY: "delta refused"): the
                 * payload is its reason, and the client answers it as Refused.
                 * "request refused" is the well-formed request not done. */
                return PYMERGETIC_RXF_LIVE_APPLY;
            }
            arena_.journal_commit();
            a_->commits++;
            /* The lookup index is a derived mirror of the committed arena (the
             * table open() builds at attach): a commit may have created,
             * renamed-in or released paths, so the table is rebuilt from what
             * the arena now holds - a resolve or a delta after the commit finds
             * the paths the commit made (docs/32 1: the index is derived,
             * never stored). */
            if (!build_table()) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, PYMERGETIC_RXF_REASON_ARENA);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_BAD_IMAGE;
            }
            if (!dump()) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, PYMERGETIC_RXF_REASON_ARENA);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_BAD_IMAGE;
            }
            mem::copy(rs, a_->emit_digest, 32);
            *rsl = 32;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_DIGEST:
        case PYMERGETIC_RXF_OP_EMIT: {
            if (!dump()) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, PYMERGETIC_RXF_REASON_ARENA);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_BAD_IMAGE;
            }
            if (op == PYMERGETIC_RXF_OP_DIGEST) {
                mem::copy(rs, a_->emit_digest, 32);
                *rsl = 32;
            } else {
                store<uint64_t>(rs, a_->emit_length);
                mem::copy(rs + 8, a_->emit_digest, 32);
                *rsl = 40;
            }
            return PYMERGETIC_RXF_LIVE_OK;
        }
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
        case PYMERGETIC_RXF_OP_COMPACT: {
            /* The arena becomes its own canonical successor, where it sits
             * (live.h): the dump (the rebuild of docs/32 5.4) already answers
             * the bytes; compact verifies them into the emit region, slides
             * them over the arena, and re-derives what attaches - the lookup
             * table and the journal bounds (docs/20 S8). An object that is
             * pinned (its state's mobility bits) is not moved: the request is
             * declined, reason 13, and the arena is byte for byte as it was. */
            uint32_t reason = 0;
            if (!compact(&reason)) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, reason);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_REFUSED;
            }
            store<uint64_t>(rs + 0, arena_.handle_count());
            store<uint64_t>(rs + 8, arena_.committed());
            mem::copy(rs + 16, a_->emit_digest, 32);
            store<uint64_t>(rs + 48, reclaimed_);
            *rsl = 56;
            a_->epoch++;
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
    uint64_t reclaimed_ = 0;
    uint8_t *scratch_base_ = nullptr, *scratch_at_ = nullptr, *scratch_end_ = nullptr;
    /* Where a refused apply stopped: the index of the operation being replayed
     * (or 0x80000000 | fixup index in the fixup pass) and the path whose resolve
     * failed. The path points into the staged delta, valid until the answer is
     * written. Carried out in the APPLY refusal so the client can name the exact
     * operation and subject instead of a bare reason. */
    uint32_t at_ = 0;
    const uint8_t *fail_p_ = nullptr;
    uint32_t fail_n_ = 0;
    /* The dangling handle a check_all refusal names (0 when the refusal is a
     * resolve that would not name one). Carried in the APPLY refusal beside the
     * source so the client can say which subject holds a ref at what is gone. */
    uint32_t fail_target_ = 0;
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
