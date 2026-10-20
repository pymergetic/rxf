#include <pymergetic/rxftk/core/live/live.hpp>

#include <pymergetic/rxftk/core/arena/arena.hpp>
#include <pymergetic/rxftk/core/arena/mem.hpp>
#include <pymergetic/rxftk/core/hash/sha256_core.hpp>

/*
 * See live.hpp. Every function here has to be inlinable into one body: no recursion, no function
 * pointer, no virtual call, nothing from a library. Loops that would be recursions keep their
 * own small stack.
 */

namespace pymergetic::rxf::core::live {

namespace {

using arena::Arena;
using arena::Memory;
using arena::Status;
using Handle = pymergetic_rxf_handle;
using Area = pymergetic_rxf_live_area;

constexpr Handle kNone = PYMERGETIC_RXF_HANDLE_NONE;
constexpr uint32_t kNone32 = 0xFFFFFFFFU;
constexpr uint32_t kTypeTag = 0x54595045U;  // "TYPE"
constexpr uint32_t kFieldTag = 0x464C4420U; // "FLD "
constexpr uint32_t kMaxDepth = 6;
constexpr uint64_t kMaxExtent = 1ULL << 30;
constexpr uint32_t kMaxLeaves = 256;
constexpr uint32_t kMaxRefLeaves = 128;
constexpr uint32_t kPoolWords = 16384;

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

PYMERGETIC_RXF_INLINE uint32_t kind_of(const pymergetic_rxf_cell *c) {
    return (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
}
PYMERGETIC_RXF_INLINE uint32_t state_of(const pymergetic_rxf_cell *c) {
    return (c->flags >> PYMERGETIC_RXF_FLAG_STATE_SHIFT) & PYMERGETIC_RXF_FLAG_STATE_MASK;
}
PYMERGETIC_RXF_INLINE uint32_t disposition_of(const pymergetic_rxf_cell *c) {
    return (state_of(c) >> PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT) & 3U;
}

PYMERGETIC_RXF_INLINE uint32_t crc_update(uint32_t crc, const uint8_t *p, uint64_t n) {
    for (uint64_t i = 0; i < n; i++) {
        crc ^= p[i];
        for (int k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320U & (0U - (crc & 1U)));
    }
    return crc;
}

/* A literal as bytes in a local array, stored from immediates: a native body has no data section.
 */
#ifdef __clang__
#define PYMERGETIC_RXF_UNROLL _Pragma("clang loop unroll(full)")
#else
#define PYMERGETIC_RXF_UNROLL
#endif

template <uint64_t N> PYMERGETIC_RXF_INLINE void literal(uint8_t *out, const char (&text)[N]) {
    PYMERGETIC_RXF_UNROLL
    for (uint64_t i = 0; i < N; i++)
        out[i] = static_cast<uint8_t>(text[i]);
}

struct Row {
    uint64_t id, type_id, address, size;
};

/* A name is text without control characters, without '.', not starting with '@', valid UTF-8. */
PYMERGETIC_RXF_INLINE bool good_name(const uint8_t *p, uint64_t n) {
    if (n == 0 || p[0] == '@')
        return false;
    for (uint64_t i = 0; i < n; i++)
        if (p[i] == '.' || p[i] < 0x20 || p[i] == 0x7F)
            return false;
    uint64_t i = 0;
    while (i < n) {
        const uint8_t b = p[i];
        uint32_t extra = 0;
        uint32_t low = 0x80, code = 0;
        if (b < 0x80) {
            i++;
            continue;
        } else if (b >= 0xC2 && b <= 0xDF) {
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

struct Pending {
    uint32_t handle;
    uint32_t kind;
    uint32_t a;
    uint32_t path;
    uint32_t op;
};
enum { kPendType = 0, kPendPatch = 1, kPendTarget = 2, kPendRole = 3 };

struct Frame {
    Handle type;
    uint32_t pos, end;
    uint64_t base;
    Handle vtype;
    uint64_t voff, vsize, vcount, i;
    bool active;
};

class Session {
  public:
    Session() = default;

    bool open(Area *area, void *context) {
        a_ = area;
        context_ = context;
        if (area->magic != PYMERGETIC_RXF_LIVE_MAGIC ||
            area->version != PYMERGETIC_RXF_LIVE_VERSION)
            return false;
        Memory m;
        m.base = ptr(area->arena_base);
        m.committed = area->arena_committed == 0 ? area->arena_capacity : area->arena_committed;
        m.limit = area->arena_capacity;
        if (m.committed > m.limit)
            return false;
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
        uint8_t job = kJobNone;
        switch (op) {
        case PYMERGETIC_RXF_OP_PING:
            mem::copy(rs, rq, rl);
            *rsl = rl;
            return PYMERGETIC_RXF_LIVE_OK;
        case PYMERGETIC_RXF_OP_HELLO: {
            const pymergetic_rxf_arena_header *h = arena_.header();
            store<uint32_t>(rs + 0, PYMERGETIC_RXF_LIVE_VERSION);
            store<uint32_t>(rs + 4, 0);
            store<uint64_t>(rs + 8, h->handle_count);
            store<uint64_t>(rs + 16, h->body_size);
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
            mem::copy(rs, arena_.header(), sizeof(pymergetic_rxf_arena_header));
            *rsl = sizeof(pymergetic_rxf_arena_header);
            return PYMERGETIC_RXF_LIVE_OK;
        case PYMERGETIC_RXF_OP_RESOLVE: {
            const Handle h = resolve(rq, rl);
            if (h == kNone)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            store<uint64_t>(rs, h);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_PATH: {
            if (rl < 8)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            uint32_t n = 0;
            if (!path_of(load<uint64_t>(rq), rs, PYMERGETIC_RXF_FRAME_MAX, &n))
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            *rsl = n;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_GET: {
            if (rl < 8)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const Handle h = load<uint64_t>(rq);
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            const char *name = arena_.name(h);
            uint64_t n = mem::length(name);
            if (n > 900)
                n = 900;
            store<uint64_t>(rs + 0, c->type);
            store<uint64_t>(rs + 8, c->parent);
            store<uint64_t>(rs + 16, c->payload_size);
            store<uint64_t>(rs + 24, c->generation);
            store<uint32_t>(rs + 32, c->flags);
            store<uint32_t>(rs + 36, c->ref_count);
            store<uint32_t>(rs + 40, c->index);
            store<uint32_t>(rs + 44, static_cast<uint32_t>(n));
            mem::copy(rs + 48, name, n);
            *rsl = static_cast<uint32_t>(48 + n);
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_READ: {
            if (rl < 16)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const Handle h = load<uint64_t>(rq);
            const uint32_t offset = load<uint32_t>(rq + 8);
            uint32_t count = load<uint32_t>(rq + 12);
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            if (offset > c->payload_size)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            if (count > PYMERGETIC_RXF_FRAME_MAX)
                count = PYMERGETIC_RXF_FRAME_MAX;
            if (count > c->payload_size - offset)
                count = static_cast<uint32_t>(c->payload_size - offset);
            mem::copy(rs, arena_.payload(h) + offset, count);
            *rsl = count;
            return PYMERGETIC_RXF_LIVE_OK;
        }
        case PYMERGETIC_RXF_OP_CHILDREN: {
            if (rl < 12)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            const Handle parent = load<uint64_t>(rq);
            uint64_t at = load<uint32_t>(rq + 8);
            const uint64_t n = arena_.handle_count();
            uint32_t count = 0;
            while (at < n && count < 120) {
                const pymergetic_rxf_cell *c = arena_.cell(at);
                if (c != nullptr && c->parent == parent) {
                    store<uint64_t>(rs + 8 + 8 * count, at);
                    count++;
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
            const Handle h = load<uint64_t>(rq);
            const uint32_t skip = load<uint32_t>(rq + 8);
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr)
                return PYMERGETIC_RXF_LIVE_NOT_FOUND;
            const pymergetic_rxf_ref *refs = arena_.refs(h);
            uint32_t count = 0;
            for (uint32_t k = skip; k < c->ref_count && count < 40; k++, count++)
                mem::copy(rs + 8 + 24 * count, &refs[k], 24);
            store<uint32_t>(rs + 0, count);
            store<uint32_t>(rs + 4, c->ref_count);
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
        case PYMERGETIC_RXF_OP_STAGE_COMMIT: {
            if (rl < 36)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            length_ = load<uint32_t>(rq);
            if (length_ > a_->stage_length)
                return PYMERGETIC_RXF_LIVE_BAD_REQUEST;
            hash::Sha256Core sha;
            sha.init();
            sha.update(ptr(a_->stage_base), length_);
            uint8_t digest[32];
            sha.finish(digest);
            if (mem::compare(digest, rq + 4, 32) != 0)
                return PYMERGETIC_RXF_LIVE_DIGEST;
            job = kJobApply;
            break;
        }
        case PYMERGETIC_RXF_OP_DIGEST:
        case PYMERGETIC_RXF_OP_EMIT:
            job = kJobEmit;
            break;
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
        case PYMERGETIC_RXF_OP_COMPACT:
            job = kJobCompact;
            break;
        case PYMERGETIC_RXF_OP_RELINK:
            job = kJobRelink;
            break;
        case PYMERGETIC_RXF_OP_BREAK:
            a_->break_request = argument;
            store<uint32_t>(rs, a_->break_ack);
            *rsl = 4;
            return PYMERGETIC_RXF_LIVE_OK;
        default:
            return PYMERGETIC_RXF_LIVE_UNKNOWN_OP;
        }
        // The jobs that need the big routines (emit, relink, replay) run in one place, so that a
        // native body holds each of them once.
        run_job(job);
        switch (job) {
        case kJobEmit:
            if (!ok_) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, reason_);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_BAD_IMAGE;
            }
            if (op == PYMERGETIC_RXF_OP_DIGEST) {
                mem::copy(rs, a_->emit_digest, 32);
                *rsl = 32;
            } else {
                store<uint64_t>(rs, emitted_);
                mem::copy(rs + 8, a_->emit_digest, 32);
                *rsl = 40;
            }
            return PYMERGETIC_RXF_LIVE_OK;
        case kJobRelink:
            if (!ok_) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, reason_);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_REFUSED;
            }
            store<uint32_t>(rs, linked_entries_);
            store<uint32_t>(rs + 4, linked_slots_);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_OK;
        case kJobCompact:
            if (!ok_) {
                store<uint32_t>(rs, 0);
                store<uint32_t>(rs + 4, reason_);
                *rsl = 8;
                return PYMERGETIC_RXF_LIVE_REFUSED;
            }
            store<uint64_t>(rs, arena_.handle_count());
            store<uint64_t>(rs + 8, arena_.header()->body_size);
            mem::copy(rs + 16, a_->emit_digest, 32);
            store<uint64_t>(rs + 48, reclaimed_);
            *rsl = 56;
            return PYMERGETIC_RXF_LIVE_OK;
        default: // kJobApply
            if (ok_) {
                a_->commits++;
                mem::copy(rs, a_->emit_digest, 32);
                *rsl = 32;
                return PYMERGETIC_RXF_LIVE_OK;
            }
            a_->rejects++;
            if (reason_ == PYMERGETIC_RXF_REASON_ARENA &&
                arena_.need() > arena_.memory().committed) {
                // The first allocation that failed, and room for what the delta still brings: its
                // bytes at most come back as cells (the cell header and the padding are the rest).
                uint64_t want = arena_.need() + 3 * uint64_t(length_) + 4096;
                if (want > a_->arena_capacity)
                    want = a_->arena_capacity;
                a_->grow_request = want;
            }
            store<uint32_t>(rs, at_op_);
            store<uint32_t>(rs + 4, reason_);
            *rsl = 8;
            return PYMERGETIC_RXF_LIVE_APPLY;
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

    // ---- lookup table: (parent, name) -> handle, open addressing -----------------------------

    static uint32_t name_hash(uint64_t parent, const uint8_t *p, uint64_t n) {
        uint64_t h = 1469598103934665603ULL ^ (parent * 0x9E3779B97F4A7C15ULL);
        for (uint64_t i = 0; i < n; i++) {
            h ^= p[i];
            h *= 1099511628211ULL;
        }
        return static_cast<uint32_t>(h ^ (h >> 32));
    }

    bool name_equals(Handle h, const uint8_t *p, uint64_t n) const {
        const uint8_t *name = reinterpret_cast<const uint8_t *>(arena_.name(h));
        for (uint64_t i = 0; i < n; i++)
            if (name[i] != p[i])
                return false;
        return name[n] == 0;
    }

    uint32_t home_of(Handle h) const {
        const pymergetic_rxf_cell *c = arena_.cell(h);
        const uint8_t *name = reinterpret_cast<const uint8_t *>(arena_.name(h));
        return name_hash(c->parent, name, mem::length(reinterpret_cast<const char *>(name)));
    }

    Handle find_child(Handle parent, const uint8_t *p, uint64_t n) const {
        const uint32_t mask = slots_ - 1;
        uint32_t i = name_hash(parent, p, n) & mask;
        for (uint32_t e = table_[i]; e != 0; e = table_[i]) {
            const Handle h = e - 1;
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c != nullptr && c->parent == parent && name_equals(h, p, n))
                return h;
            i = (i + 1) & mask;
        }
        return kNone;
    }

    /* false: the name is taken or the table is full. */
    bool table_insert(Handle h) {
        const pymergetic_rxf_cell *c = arena_.cell(h);
        const uint8_t *name = reinterpret_cast<const uint8_t *>(arena_.name(h));
        const uint64_t n = mem::length(reinterpret_cast<const char *>(name));
        if (h >= 0xFFFFFFF0ULL || arena_.handle_count() * 2 > slots_)
            return false;
        if (find_child(c->parent, name, n) != kNone)
            return false;
        const uint32_t mask = slots_ - 1;
        uint32_t i = name_hash(c->parent, name, n) & mask;
        while (table_[i] != 0)
            i = (i + 1) & mask;
        table_[i] = static_cast<uint32_t>(h + 1);
        return true;
    }

    void table_remove(Handle h) {
        const uint32_t mask = slots_ - 1;
        uint32_t i = home_of(h) & mask;
        while (table_[i] != 0 && table_[i] != h + 1)
            i = (i + 1) & mask;
        if (table_[i] == 0)
            return;
        for (;;) {
            uint32_t j = i;
            for (;;) {
                j = (j + 1) & mask;
                if (table_[j] == 0) {
                    table_[i] = 0;
                    return;
                }
                const uint32_t k = home_of(table_[j] - 1) & mask;
                const bool between = i <= j ? (i < k && k <= j) : (i < k || k <= j);
                if (!between)
                    break;
            }
            table_[i] = table_[j];
            i = j;
        }
    }

    bool build_table() {
        mem::fill(table_, 0, uint64_t(slots_) * 4);
        const uint64_t n = arena_.handle_count();
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr || c->parent == kNone)
                continue;
            if (!table_insert(h))
                return false;
        }
        return true;
    }

    Handle resolve(const uint8_t *p, uint64_t n) const {
        Handle at = arena_.header()->root;
        if (at == kNone || n == 0)
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

    bool path_of(Handle h, uint8_t *out, uint32_t capacity, uint32_t *length) const {
        uint32_t total = 0, depth = 0;
        for (Handle at = h; at != kNone;) {
            const pymergetic_rxf_cell *c = arena_.cell(at);
            if (c == nullptr)
                return false;
            if (c->parent == kNone)
                break;
            total += static_cast<uint32_t>(mem::length(arena_.name(at))) + (depth ? 1 : 0);
            if (++depth > 512 || total > capacity)
                return false;
            at = c->parent;
        }
        uint32_t pos = total;
        bool leaf = true;
        for (Handle at = h; at != kNone;) {
            const pymergetic_rxf_cell *c = arena_.cell(at);
            if (c->parent == kNone)
                break;
            const uint32_t n = static_cast<uint32_t>(mem::length(arena_.name(at)));
            if (leaf) {
                pos -= n;
                mem::copy(out + pos, arena_.name(at), n);
            } else {
                pos -= n + 1;
                mem::copy(out + pos, arena_.name(at), n);
                out[pos + n] = '.';
            }
            leaf = false;
            at = c->parent;
        }
        *length = total;
        return true;
    }

    // ---- structure over the arena ----------------------------------------------------------

    bool less(Handle a, Handle b) const {
        const uint8_t *x = reinterpret_cast<const uint8_t *>(arena_.name(a));
        const uint8_t *y = reinterpret_cast<const uint8_t *>(arena_.name(b));
        for (uint64_t i = 0;; i++) {
            if (x[i] != y[i])
                return x[i] < y[i];
            if (x[i] == 0)
                return false;
        }
    }

    /* The children of every object, as lists in `list` delimited by `start`, each sorted by name.
     */
    bool build_kids(bool sorted) {
        const uint64_t n = arena_.handle_count();
        n_ = static_cast<uint32_t>(n);
        start_ = static_cast<uint32_t *>(take(4 * (n + 1)));
        uint32_t *cursor = static_cast<uint32_t *>(take(4 * (n + 1)));
        if (start_ == nullptr || cursor == nullptr)
            return false;
        mem::fill(start_, 0, 4 * (n + 1));
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c != nullptr && c->parent != kNone && c->parent < n)
                start_[c->parent + 1]++;
        }
        for (uint64_t i = 0; i < n; i++)
            start_[i + 1] += start_[i];
        list_ = static_cast<uint32_t *>(take(4 * (uint64_t(start_[n]) + 1)));
        if (list_ == nullptr)
            return false;
        mem::copy(cursor, start_, 4 * (n + 1));
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c != nullptr && c->parent != kNone && c->parent < n)
                list_[cursor[c->parent]++] = static_cast<uint32_t>(h);
        }
        if (!sorted)
            return true;
        for (uint64_t p = 0; p < n; p++) {
            uint32_t *a = list_ + start_[p];
            const uint32_t count = start_[p + 1] - start_[p];
            if (count < 2)
                continue;
            // heap sort: ascending by name
            for (uint32_t s = count / 2; s-- > 0;)
                sift(a, s, count);
            for (uint32_t e = count - 1; e > 0; e--) {
                const uint32_t t = a[0];
                a[0] = a[e];
                a[e] = t;
                sift(a, 0, e);
            }
        }
        return true;
    }

    void sift(uint32_t *a, uint32_t root, uint32_t end) {
        for (;;) {
            uint32_t child = 2 * root + 1;
            if (child >= end)
                return;
            if (child + 1 < end && less(a[child], a[child + 1]))
                child++;
            if (!less(a[root], a[child]))
                return;
            const uint32_t t = a[root];
            a[root] = a[child];
            a[child] = t;
            root = child;
        }
    }

    // ---- payload references, from the Type and Field objects -------------------------------

    bool type_info(Handle t, pymergetic_rxf_type *out) const {
        const pymergetic_rxf_cell *c = arena_.cell(t);
        if (c == nullptr || c->type != PYMERGETIC_RXF_HANDLE_TYPE ||
            c->payload_size != sizeof(pymergetic_rxf_type))
            return false;
        mem::copy(out, arena_.payload(t), sizeof(*out));
        return out->descriptor_tag == kTypeTag;
    }

    bool field_info(Handle f, pymergetic_rxf_field *out) const {
        const pymergetic_rxf_cell *c = arena_.cell(f);
        if (c == nullptr || c->type != PYMERGETIC_RXF_HANDLE_FIELD ||
            c->payload_size != sizeof(pymergetic_rxf_field))
            return false;
        mem::copy(out, arena_.payload(f), sizeof(*out));
        return out->descriptor_tag == kFieldTag &&
               (out->flags &
                (PYMERGETIC_RXF_FIELD_FLAG_CHILDREN | PYMERGETIC_RXF_FIELD_FLAG_INVERSE)) == 0;
    }

    bool has_fields(Handle t) const {
        pymergetic_rxf_field f;
        if (t >= n_)
            return false;
        for (uint32_t k = start_[t]; k < start_[t + 1]; k++)
            if (field_info(list_[k], &f))
                return true;
        return false;
    }

    /* The payload offsets of the references of a Type: `*words` points at the count, then the
     * offsets (bit 31: the reference may be unset). false: the live layer cannot read this type. */
    bool refs_of(Handle type, const uint32_t **words) {
        if (type == kNone || type >= n_) {
            *words = empty_;
            return true;
        }
        if (memo_[type] != 0) {
            *words = memo_[type] == 1 ? empty_ : pool_ + (memo_[type] - 2);
            return true;
        }
        uint32_t offsets[kMaxRefLeaves];
        uint32_t nrefs = 0;
        uint64_t ext_off[kMaxLeaves], ext_end[kMaxLeaves];
        uint32_t leaves = 0;
        bool none = false; // an error or a union: the type has no references to follow
        pymergetic_rxf_type t;
        if (!type_info(type, &t) || t.form == PYMERGETIC_RXF_FORM_UNION || !has_fields(type)) {
            none = true;
        } else {
            Frame frames[kMaxDepth + 1];
            uint32_t sp = 0;
            frames[0].type = type;
            frames[0].pos = start_[type];
            frames[0].end = start_[type + 1];
            frames[0].base = 0;
            frames[0].active = false;
            sp = 1;
            while (sp > 0 && !none) {
                Frame &f = frames[sp - 1];
                if (f.active) {
                    if (f.i < f.vcount) {
                        const uint64_t at = f.base + f.voff + f.i * f.vsize;
                        f.i++;
                        Frame &g = frames[sp];
                        g.type = f.vtype;
                        g.pos = start_[f.vtype];
                        g.end = start_[f.vtype + 1];
                        g.base = at;
                        g.active = false;
                        sp++;
                        continue;
                    }
                    f.active = false;
                }
                if (f.pos == f.end) {
                    sp--;
                    continue;
                }
                const Handle fh = list_[f.pos++];
                pymergetic_rxf_field fld;
                if (!field_info(fh, &fld))
                    continue;
                pymergetic_rxf_type v;
                if (!type_info(fld.value_type, &v) || fld.count == 0 || fld.offset > (1ULL << 31) ||
                    fld.count > (1ULL << 31) || v.size > (1ULL << 31)) {
                    none = true;
                    break;
                }
                if (fld.flags & PYMERGETIC_RXF_FIELD_FLAG_REF) {
                    if (nrefs >= kMaxRefLeaves || leaves >= kMaxLeaves)
                        return false; // more than the live layer reads
                    if (v.form != PYMERGETIC_RXF_FORM_UINT || v.size != 8 || fld.count != 1 ||
                        f.base + fld.offset >= kMaxExtent) {
                        none = true;
                        break;
                    }
                    offsets[nrefs++] =
                        static_cast<uint32_t>(f.base + fld.offset) |
                        ((fld.flags & PYMERGETIC_RXF_FIELD_FLAG_OPTIONAL) ? 0x80000000U : 0U);
                    ext_off[leaves] = f.base + fld.offset;
                    ext_end[leaves] = f.base + fld.offset + 8;
                    leaves++;
                    continue;
                }
                if (v.form == PYMERGETIC_RXF_FORM_STRUCT && has_fields(fld.value_type)) {
                    if (sp - 1 >= kMaxDepth) {
                        none = true;
                        break;
                    }
                    f.vtype = fld.value_type;
                    f.voff = fld.offset;
                    f.vsize = v.size;
                    f.vcount = fld.count;
                    f.i = 0;
                    f.active = true;
                    continue;
                }
                if (leaves >= kMaxLeaves)
                    return false;
                if (v.size == 0) {
                    none = true;
                    break;
                }
                ext_off[leaves] = f.base + fld.offset;
                ext_end[leaves] = f.base + fld.offset + v.size * fld.count;
                leaves++;
            }
            // Fields that overlap are a union in disguise: raw bytes, no references. Both indices
            // run from 0 and the pair (y <= x) is skipped: the flattened wasm32 body of the pump
            // turns the `y = x + 1` induction form into code whose y-indexed words read the wrong
            // slot of the frame (every optimization level), and this shape compiles right on every
            // machine. docs/31, "the overlap check".
            for (uint32_t x = 0; x < leaves && !none; x++)
                for (uint32_t y = 0; y < leaves; y++) {
                    if (y <= x)
                        continue;
                    if (ext_off[x] < ext_end[y] && ext_off[y] < ext_end[x]) {
                        none = true;
                        break;
                    }
                }
            if (!none && t.size != 0 && !(t.flags & PYMERGETIC_RXF_TYPE_FLAG_VARIABLE)) {
                uint64_t end = 0;
                for (uint32_t x = 0; x < leaves; x++)
                    if (ext_end[x] > end)
                        end = ext_end[x];
                if (end > t.size)
                    none = true;
            }
        }
        if (!none && (t.flags & PYMERGETIC_RXF_TYPE_FLAG_VARIABLE)) {
            // The fixed part of a variable type ends where its last field ends; the rest is its
            // value.
            uint64_t end = 0;
            for (uint32_t x = 0; x < leaves; x++)
                if (ext_end[x] > end)
                    end = ext_end[x];
            pfx_[type] = end > 0xFFFFFFFFULL ? 0xFFFFFFFFU : static_cast<uint32_t>(end);
        }
        if (none || nrefs == 0) {
            memo_[type] = 1;
            *words = empty_;
            return true;
        }
        if (pool_len_ + 1 + nrefs > kPoolWords)
            return false;
        memo_[type] = pool_len_ + 2;
        pool_[pool_len_] = nrefs;
        for (uint32_t k = 0; k < nrefs; k++)
            pool_[pool_len_ + 1 + k] = offsets[k];
        *words = pool_ + pool_len_;
        pool_len_ += 1 + nrefs;
        return true;
    }

    bool prepare_types() {
        memo_ = static_cast<uint32_t *>(take(4 * (uint64_t(n_) + 1)));
        pool_ = static_cast<uint32_t *>(take(4 * uint64_t(kPoolWords)));
        if (memo_ == nullptr || pool_ == nullptr)
            return false;
        pfx_ = static_cast<uint32_t *>(take(4 * (uint64_t(n_) + 1)));
        if (pfx_ == nullptr)
            return false;
        mem::fill(memo_, 0, 4 * (uint64_t(n_) + 1));
        mem::fill(pfx_, 0, 4 * (uint64_t(n_) + 1));
        pool_len_ = 0;
        empty_[0] = 0;
        return true;
    }

    // ---- the canonical form ---------------------------------------------------------------------

    /* Writes the compacted image to the emit region and its digest to the area. */
    bool emit(uint64_t *length, uint32_t *reason) {
        scratch_reset();
        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
        const uint64_t n = arena_.handle_count();
        if (n >= 0xFFFFFFF0ULL)
            return false;
        uint8_t *keep = static_cast<uint8_t *>(take(n + 1));
        if (keep == nullptr || !build_kids(true) || !prepare_types())
            return false;
        uint32_t kept = 0;
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            keep[h] =
                (c != nullptr && disposition_of(c) == PYMERGETIC_RXF_DISPOSITION_RETAIN) ? 1 : 0;
            kept += keep[h];
        }
        *reason = PYMERGETIC_RXF_REASON_DANGLING;
        for (Handle fixed = 0; fixed <= PYMERGETIC_RXF_HANDLE_FIELD; fixed++)
            if (fixed >= n || !keep[fixed])
                return false;
        for (Handle h = 0; h < n; h++) {
            if (!keep[h])
                continue;
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c->parent != kNone && (c->parent >= n || !keep[c->parent]))
                return false;
            if (c->type != kNone && (c->type >= n || !keep[c->type]))
                return false;
            const pymergetic_rxf_ref *refs = arena_.refs(h);
            for (uint32_t k = 0; k < c->ref_count; k++) {
                const Handle t = refs[k].target;
                const bool gone = t != kNone && (t >= n || !keep[t]);
                if (gone && refs[k].binding == PYMERGETIC_RXF_BINDING_MANDATORY)
                    return false;
                if (refs[k].role != kNone && (refs[k].role >= n || !keep[refs[k].role]))
                    return false;
            }
        }
        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
        uint32_t *order = static_cast<uint32_t *>(take(4 * (uint64_t(kept) + 4)));
        uint32_t *remap = static_cast<uint32_t *>(take(4 * (n + 1)));
        uint32_t *stack = static_cast<uint32_t *>(take(4 * (n + 4)));
        if (order == nullptr || remap == nullptr || stack == nullptr)
            return false;
        uint32_t count = 0;
        order[count++] = 0;
        order[count++] = 1;
        order[count++] = 2;
        uint32_t sp = 0;
        stack[sp++] = 0;
        while (sp > 0) {
            const uint32_t at = stack[--sp];
            if (at > 2) {
                if (count >= kept) {
                    *reason = PYMERGETIC_RXF_REASON_LINK;
                    return false;
                }
                order[count++] = at;
            }
            const uint32_t b = start_[at], e = start_[at + 1];
            for (uint32_t k = e; k > b; k--) {
                const uint32_t child = list_[k - 1];
                if (!keep[child])
                    continue;
                if (sp >= n)
                    return false;
                stack[sp++] = child;
            }
        }
        if (count != kept) {
            *reason = PYMERGETIC_RXF_REASON_LINK; // a retained object that the root does not reach
            return false;
        }
        for (uint64_t i = 0; i < n; i++)
            remap[i] = kNone32;
        for (uint32_t i = 0; i < count; i++)
            remap[order[i]] = i;

        Arena out;
        Memory m;
        m.base = ptr(a_->emit_base);
        m.committed = a_->emit_capacity;
        m.limit = a_->emit_capacity;
        *reason = PYMERGETIC_RXF_REASON_ARENA;
        if (out.init(m, kept) != Status::Ok)
            return false;
        for (uint32_t i = 0; i < count; i++) {
            const Handle old = order[i];
            const pymergetic_rxf_cell *c = arena_.cell(old);
            const pymergetic_rxf_ref *refs = arena_.refs(old);
            uint32_t nref = 0;
            for (uint32_t k = 0; k < c->ref_count; k++)
                if (refs[k].target == kNone || (refs[k].target < n && keep[refs[k].target]))
                    nref++;
            const uint32_t mobility = (state_of(c) >> PYMERGETIC_RXF_STATE_MOBILITY_SHIFT) & 3U;
            const uint32_t state =
                (PYMERGETIC_RXF_PROVENANCE_IMAGE << PYMERGETIC_RXF_STATE_PROVENANCE_SHIFT) |
                (PYMERGETIC_RXF_DISPOSITION_RETAIN << PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT) |
                (PYMERGETIC_RXF_CLEANLINESS_CLEAN << PYMERGETIC_RXF_STATE_CLEANLINESS_SHIFT) |
                (mobility << PYMERGETIC_RXF_STATE_MOBILITY_SHIFT);
            arena::CellSpec spec;
            spec.type = c->type == kNone || c->type >= n ? kNone : remap[c->type];
            spec.type = spec.type == kNone32 ? kNone : spec.type;
            spec.parent = c->parent == kNone || c->parent >= n ? kNone : remap[c->parent];
            spec.parent = spec.parent == kNone32 ? kNone : spec.parent;
            const char *name = arena_.name(old);
            spec.name = name;
            spec.name_length = mem::length(name);
            spec.flags =
                (c->flags & ~(PYMERGETIC_RXF_FLAG_STATE_MASK << PYMERGETIC_RXF_FLAG_STATE_SHIFT)) |
                (state << PYMERGETIC_RXF_FLAG_STATE_SHIFT);
            spec.generation = c->generation;
            spec.index = c->index;
            spec.payload_size = c->payload_size;
            spec.ref_count = nref;
            Handle made = 0;
            if (out.create(spec, &made) != Status::Ok)
                return false;
            if (c->payload_size) {
                uint8_t *payload = out.payload(made);
                mem::copy(payload, arena_.payload(old), c->payload_size);
                const uint32_t *words = nullptr;
                *reason = PYMERGETIC_RXF_REASON_LAYOUT;
                if (!refs_of(c->type, &words))
                    return false;
                for (uint32_t k = 0; k < words[0]; k++) {
                    const uint32_t offset = words[1 + k] & 0x7FFFFFFFU;
                    if (uint64_t(offset) + 8 > c->payload_size)
                        continue;
                    const uint64_t before = load<uint64_t>(payload + offset);
                    if (before == kNone)
                        continue;
                    const uint64_t now =
                        before < n && remap[before] != kNone32 ? remap[before] : kNone;
                    if (now == kNone && !(words[1 + k] & 0x80000000U)) {
                        *reason = PYMERGETIC_RXF_REASON_DANGLING;
                        return false;
                    }
                    store<uint64_t>(payload + offset, now);
                }
                *reason = PYMERGETIC_RXF_REASON_ARENA;
            }
            if (nref) {
                pymergetic_rxf_ref *to = out.refs(made);
                uint32_t w = 0;
                for (uint32_t k = 0; k < c->ref_count; k++) {
                    if (refs[k].target != kNone && !(refs[k].target < n && keep[refs[k].target]))
                        continue;
                    pymergetic_rxf_ref r = refs[k];
                    r.target = r.target == kNone ? kNone : remap[r.target];
                    r.role = r.role == kNone || r.role >= n || remap[r.role] == kNone32
                                 ? kNone
                                 : remap[r.role];
                    to[w++] = r;
                }
            }
        }
        pymergetic_rxf_arena_header *head = out.mutable_header();
        head->flags = arena_.header()->flags;
        head->root = 0;
        const Handle entry = arena_.header()->entry;
        if (entry != kNone && (entry >= n || !keep[entry])) {
            *reason = PYMERGETIC_RXF_REASON_DANGLING;
            return false;
        }
        head->entry = entry == kNone ? kNone : remap[entry];
        head->committed_size = head->body_size;
        head->limit = PYMERGETIC_RXF_LIMIT_UNKNOWN;
        hash::Sha256Core sha;
        sha.init();
        sha.update(ptr(a_->emit_base), head->body_size);
        sha.finish(a_->emit_digest);
        a_->emit_length = head->body_size;
        *length = head->body_size;
        *reason = 0;
        return true;
    }

    // ---- jobs
    // --------------------------------------------------------------------------------------
    //
    // A request that needs the big routines (emit, relink, replay) is a short sequence of phases
    // that run_job drives. Every routine is called from one place, so a native body, which cannot
    // call anything, holds one copy of each.

    enum : uint8_t { kJobNone, kJobEmit, kJobRelink, kJobCompact, kJobApply };
    enum : uint8_t {
        kPhEmit,
        kPhRelink,
        kPhApplyPrepare,
        kPhApplyBase,
        kPhApplyReplay,
        kPhApplyTail,
        kPhApplyCommit,
        kPhCompactScan,
        kPhCompactEmit,
        kPhCompactGuard,
        kPhCompactCopy,
        kPhDone,
        kPhFail
    };

    bool linkable() const {
        return context_ != nullptr && a_->entries_capacity != 0;
    }

    void run_job(uint8_t job) {
        at_op_ = 0;
        reason_ = 0;
        ok_ = false;
        reclaimed_ = emitted_ = 0;
        linked_entries_ = linked_slots_ = 0;
        override_ = 0;
        guard_ = false;
        uint8_t phase = kPhApplyPrepare;
        if (job == kJobEmit) {
            emit_next_ = kPhDone;
            phase = kPhEmit;
        } else if (job == kJobRelink) {
            relink_next_ = kPhDone;
            dry_ = false;
            phase = kPhRelink;
            if (!linkable()) {
                reason_ = PYMERGETIC_RXF_REASON_UNBOUND;
                phase = kPhFail;
            }
        } else if (job == kJobCompact) {
            phase = kPhCompactScan;
        }
        for (;;) {
            switch (phase) {
            case kPhEmit:
                if (emit(&emitted_, &reason_)) {
                    phase = emit_next_;
                } else {
                    if (override_ != 0)
                        reason_ = override_;
                    phase = kPhFail;
                }
                override_ = 0;
                break;
            case kPhRelink:
                phase = relink(&reason_, &linked_entries_, &linked_slots_, dry_)
                            ? uint8_t(relink_next_)
                            : uint8_t(kPhFail);
                break;
            case kPhApplyPrepare:
                if (!apply_prepare()) {
                    phase = kPhFail;
                } else if (head_.flags & PYMERGETIC_RXF_DELTA_CHECK_BASE) {
                    override_ = PYMERGETIC_RXF_REASON_BASE;
                    emit_next_ = kPhApplyBase;
                    phase = kPhEmit;
                } else {
                    phase = kPhApplyReplay;
                }
                break;
            case kPhApplyBase:
                if (mem::compare(a_->emit_digest, head_.base_digest, 32) != 0) {
                    reason_ = PYMERGETIC_RXF_REASON_BASE;
                    phase = kPhFail;
                } else {
                    phase = kPhApplyReplay;
                }
                break;
            case kPhApplyReplay:
                if (apply_replay()) {
                    emit_next_ = kPhApplyTail;
                    phase = kPhEmit;
                } else {
                    phase = kPhFail;
                }
                break;
            case kPhApplyTail:
                phase = kPhApplyCommit;
                if (linkable()) {
                    dry_ = false;
                    relink_next_ = kPhApplyCommit;
                    phase = kPhRelink;
                }
                break;
            case kPhApplyCommit:
                arena_.commit();
                phase = kPhDone;
                break;
            case kPhCompactScan:
                if (!compact_scan()) {
                    phase = kPhFail;
                } else {
                    phase = kPhCompactEmit;
                    if (linkable()) { // the dry run: if it links now, it links under the new
                                      // handles
                        dry_ = true;
                        relink_next_ = kPhCompactEmit;
                        phase = kPhRelink;
                    }
                }
                break;
            case kPhCompactEmit:
                dry_ = false;
                emit_next_ = kPhCompactGuard;
                phase = kPhEmit;
                break;
            case kPhCompactGuard:
                phase = kPhCompactCopy;
                if (linkable()) { // the code that is running must stay where it is
                    dry_ = guard_ = true;
                    relink_next_ = kPhCompactCopy;
                    phase = kPhRelink;
                }
                break;
            case kPhCompactCopy:
                dry_ = guard_ = false;
                phase = kPhDone;
                if (!compact_copy()) {
                    phase = kPhFail;
                } else if (linkable()) {
                    relink_next_ = kPhDone;
                    phase = kPhRelink;
                }
                break;
            case kPhFail:
                if (arena_.journaling()) {
                    arena_.rollback();
                    a_->index_state = build_table() ? 1 : 0;
                }
                ok_ = false;
                return;
            default:
                if (job != kJobEmit)
                    a_->epoch++; // what runs above this pump holds addresses of before
                ok_ = true;
                return;
            }
        }
    }

    /* Compaction moves handles: an object that is pinned (it must stay where it is) forbids it. */
    bool compact_scan() {
        const uint64_t n = arena_.handle_count();
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c != nullptr && ((state_of(c) >> PYMERGETIC_RXF_STATE_MOBILITY_SHIFT) & 3U) ==
                                    PYMERGETIC_RXF_MOBILITY_PINNED) {
                reason_ = PYMERGETIC_RXF_REASON_PINNED;
                return false;
            }
        }
        return true;
    }

    /* The arena becomes its own canonical successor, where it is: the emitted bytes replace the
     * arena's, so dead cells and the old handle order are gone. No undo log is involved. */
    bool compact_copy() {
        const uint64_t length = emitted_;
        const uint64_t before = arena_.header()->body_size;
        const uint64_t committed = arena_.memory().committed;
        uint8_t *base = ptr(a_->arena_base);
        mem::copy(base, ptr(a_->emit_base), length);
        if (before > length)
            mem::fill(base + length, 0, before - length);
        pymergetic_rxf_arena_header *head = reinterpret_cast<pymergetic_rxf_arena_header *>(base);
        head->committed_size = committed;
        head->limit = PYMERGETIC_RXF_LIMIT_UNKNOWN;
        Memory m;
        m.base = base;
        m.committed = committed;
        m.limit = a_->arena_capacity;
        if (arena_.attach(m) != Status::Ok) {
            reason_ = PYMERGETIC_RXF_REASON_ARENA;
            return false;
        }
        a_->index_state = 0;
        a_->compactions++;
        reclaimed_ = before - length;
        // The same objects under other handles: the table is rebuilt for the relink that follows.
        if (linkable() && build_table())
            a_->index_state = 1;
        return true;
    }

    // ---- linking: the entries and function slots of the context, from the arena
    // -------------------

    template <uint64_t N> Handle child_named(Handle parent, const char (&text)[N]) const {
        if (parent == kNone)
            return kNone;
        uint8_t buf[N];
        literal(buf, text);
        return find_child(parent, buf, N - 1);
    }

    template <uint64_t N> bool name_is(Handle h, const char (&text)[N]) const {
        uint8_t buf[N];
        literal(buf, text);
        const char *name = arena_.name(h);
        return mem::length(name) == N - 1 && mem::compare(name, buf, N - 1) == 0;
    }

    /* The first 8 bytes (little-endian) of the SHA-256 of the path of an object. */
    bool path_id(Handle h, uint8_t *buffer, uint64_t *id) const {
        uint32_t n = 0;
        if (!path_of(h, buffer, 1024, &n))
            return false;
        hash::Sha256Core sha;
        sha.init();
        sha.update(buffer, n);
        uint8_t digest[32];
        sha.finish(digest);
        *id = load<uint64_t>(digest);
        return true;
    }

    void sift_rows(Row *a, uint64_t root, uint64_t end) const {
        for (;;) {
            uint64_t child = 2 * root + 1;
            if (child >= end)
                return;
            if (child + 1 < end && a[child].id < a[child + 1].id)
                child++;
            if (a[root].id >= a[child].id)
                return;
            const Row t = a[root];
            a[root] = a[child];
            a[child] = t;
            root = child;
        }
    }

    /* The Code of a native Function for this machine, as an address. */
    bool code_address(Handle function, uint64_t *address, uint64_t *size) const {
        Handle code = kNone;
        if (a_->machine == PYMERGETIC_RXF_MACHINE_X86_64)
            code = child_named(function, "x86_64_linux");
        else if (a_->machine == PYMERGETIC_RXF_MACHINE_AARCH64)
            code = child_named(function, "aarch64_linux");
        const pymergetic_rxf_cell *c = code == kNone ? nullptr : arena_.cell(code);
        if (c == nullptr || c->payload_size < 56)
            return false;
        const uint8_t *payload = arena_.payload(code);
        const uint64_t entry = load<uint64_t>(payload + 40);
        const uint64_t bytes = load<uint64_t>(payload + 48);
        if (bytes > c->payload_size - 56 || entry >= bytes + 1)
            return false;
        const uint64_t start =
            static_cast<uint64_t>(reinterpret_cast<uintptr_t>(payload)) + c->payload_size - bytes;
        *address = start + entry;
        size[0] = start;
        size[1] = bytes;
        return true;
    }

    /* Writes what the linker makes of an arena (tools/native/link.py) into the context: one entry
     * for every live typed object, sorted by id, behind the spare ones, and the address of the Code
     * of every leaf the interpreter names. Nothing is written unless all of it can be. `dry`: check
     * only. */
    bool relink(uint32_t *reason, uint32_t *entries, uint32_t *slots, bool dry = false) {
        uint64_t *words = static_cast<uint64_t *>(context_);
        scratch_reset();
        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
        if (!build_kids(false) || !prepare_types())
            return false;
        const uint64_t n = n_;
        Row *rows = static_cast<Row *>(take(sizeof(Row) * (n + 1)));
        uint64_t *type_ids = static_cast<uint64_t *>(take(8 * (n + 1)));
        uint8_t *known = static_cast<uint8_t *>(take(n + 1));
        uint8_t *path = static_cast<uint8_t *>(take(1024));
        if (rows == nullptr || type_ids == nullptr || known == nullptr || path == nullptr)
            return false;
        mem::fill(known, 0, n + 1);
        uint64_t count = 0;
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr)
                continue;
            const uint32_t kind = kind_of(c);
            if (kind == PYMERGETIC_RXF_KIND_BLOCK || kind == PYMERGETIC_RXF_KIND_DEAD ||
                c->type == kNone)
                continue;
            *reason = PYMERGETIC_RXF_REASON_DANGLING;
            if (c->type >= n || arena_.cell(c->type) == nullptr)
                return false;
            *reason = PYMERGETIC_RXF_REASON_LAYOUT;
            const uint32_t *ignored = nullptr;
            if (!refs_of(c->type, &ignored))
                return false;
            const uint64_t prefix = pfx_[c->type];
            if (prefix > c->payload_size)
                return false;
            *reason = PYMERGETIC_RXF_REASON_LINK;
            if (!known[c->type]) {
                if (!path_id(c->type, path, &type_ids[c->type]))
                    return false;
                known[c->type] = 1;
            }
            uint64_t id = 0;
            bool named = false;
            if (name_is(c->type, "SemanticObject")) {
                const Handle f = child_named(c->type, "object_id");
                pymergetic_rxf_field fld;
                if (f != kNone && field_info(f, &fld) && fld.offset + 8 <= c->payload_size) {
                    id = load<uint64_t>(arena_.payload(h) + fld.offset);
                    named = true;
                }
            }
            if (!named && !path_id(h, path, &id))
                return false;
            Row &r = rows[count++];
            r.id = id;
            r.type_id = type_ids[c->type];
            r.address =
                static_cast<uint64_t>(reinterpret_cast<uintptr_t>(arena_.payload(h))) + prefix;
            r.size = c->payload_size - prefix;
        }
        if (count > 1) {
            for (uint64_t s = count / 2; s-- > 0;)
                sift_rows(rows, s, count);
            for (uint64_t e = count - 1; e > 0; e--) {
                const Row t = rows[0];
                rows[0] = rows[e];
                rows[e] = t;
                sift_rows(rows, 0, e);
            }
        }
        *reason = PYMERGETIC_RXF_REASON_LINK;
        for (uint64_t i = 0; i < count; i++)
            if (rows[i].id <= PYMERGETIC_RXF_LINK_SPARE || (i > 0 && rows[i].id == rows[i - 1].id))
                return false;
        if (PYMERGETIC_RXF_LINK_SPARE + count > a_->entries_capacity)
            return false;

        const uint64_t function_count = words[2];
        uint8_t *function_table = ptr(words[3]);
        uint64_t *addresses = static_cast<uint64_t *>(take(8 * (function_count + 1)));
        if (addresses == nullptr) {
            *reason = PYMERGETIC_RXF_REASON_SCRATCH;
            return false;
        }
        mem::fill(addresses, 0, 8 * (function_count + 1));
        const bool addressed = a_->machine != PYMERGETIC_RXF_MACHINE_WASM32;
        if (addressed) {
            Handle run = arena_.header()->root;
            run = child_named(run, "pymergetic");
            run = child_named(run, "rxf");
            run = child_named(run, "foundation");
            run = child_named(run, "graph");
            run = child_named(run, "run");
            const pymergetic_rxf_cell *rc = run == kNone ? nullptr : arena_.cell(run);
            if (rc == nullptr)
                return false;
            const pymergetic_rxf_ref *refs = arena_.refs(run);
            for (uint32_t k = 0; k < rc->ref_count; k++) {
                const Handle role = refs[k].role;
                if (role == kNone || arena_.cell(role) == nullptr || !name_is(role, "Leaf"))
                    continue;
                const uint32_t id = refs[k].index;
                if (id == 0 || id > function_count || refs[k].target == kNone)
                    return false;
                uint64_t range[2];
                if (!code_address(refs[k].target, &addresses[id - 1], range))
                    return false;
                // What runs right now must stay: the interpreter, when it is below the pump, holds
                // addresses of every leaf. Otherwise only the pump runs, and it has left the arena
                // when it was able to (`relocated`); the rest of the leaves move with their Code.
                if (guard_ && (a_->in_run != 0 || (id == kLeafPump && a_->relocated == 0))) {
                    const uint64_t at =
                        range[0] -
                        static_cast<uint64_t>(reinterpret_cast<uintptr_t>(ptr(a_->arena_base)));
                    *reason = PYMERGETIC_RXF_REASON_PINNED;
                    if (at + range[1] > emitted_ ||
                        mem::compare(ptr(range[0]), ptr(a_->emit_base) + at, range[1]) != 0)
                        return false;
                }
            }
            for (uint64_t k = 0; k < function_count; k++)
                if (addresses[k] == 0 ||
                    load<uint64_t>(function_table + k * PYMERGETIC_RXF_LINK_SLOT_BYTES) != k + 1)
                    return false;
        }
        *entries = static_cast<uint32_t>(PYMERGETIC_RXF_LINK_SPARE + count);
        *slots = addressed ? static_cast<uint32_t>(function_count) : 0;
        if (dry)
            return true;

        uint8_t *at =
            ptr(words[5]) + uint64_t(PYMERGETIC_RXF_LINK_SPARE) * PYMERGETIC_RXF_LINK_ENTRY_BYTES;
        for (uint64_t i = 0; i < count; i++, at += PYMERGETIC_RXF_LINK_ENTRY_BYTES) {
            uint64_t entry[10];
            entry[0] = rows[i].id;
            entry[1] = 1;
            entry[2] = rows[i].type_id;
            entry[3] = rows[i].address;
            entry[4] = rows[i].size;
            entry[5] = rows[i].size;
            entry[6] = 1; // live
            entry[7] = 0;
            entry[8] = 0;
            entry[9] = 0;
            mem::copy(at, entry, sizeof(entry));
        }
        words[4] = PYMERGETIC_RXF_LINK_SPARE + count;
        if (addressed)
            for (uint64_t k = 0; k < function_count; k++)
                store<uint64_t>(function_table + k * PYMERGETIC_RXF_LINK_SLOT_BYTES + 16,
                                addresses[k]);
        *reason = 0;
        return true;
    }

    // ---- applying a delta ---------------------------------------------------------------------

    bool string_at(uint32_t offset, const uint8_t *strings, uint32_t size, const uint8_t **p,
                   uint32_t *n) const {
        if (offset >= size)
            return false;
        uint32_t e = offset;
        while (e < size && strings[e] != 0)
            e++;
        if (e >= size)
            return false;
        *p = strings + offset;
        *n = e - offset;
        return true;
    }

    static uint32_t reason_of(Status s) {
        switch (s) {
        case Status::JournalFull:
            return PYMERGETIC_RXF_REASON_JOURNAL;
        case Status::NameTaken:
            return PYMERGETIC_RXF_REASON_EXISTS;
        case Status::BadName:
            return PYMERGETIC_RXF_REASON_NAME;
        case Status::Protected:
            return PYMERGETIC_RXF_REASON_PROTECTED;
        case Status::Dangling:
            return PYMERGETIC_RXF_REASON_DANGLING;
        case Status::BadPath:
        case Status::BadHandle:
            return PYMERGETIC_RXF_REASON_PATH;
        default:
            return PYMERGETIC_RXF_REASON_ARENA;
        }
    }

    /* The format of the staged delta: every operation, and how many fixups they need. */
    bool apply_prepare() {
        uint32_t *at_op = &at_op_;
        uint32_t *reason = &reason_;
        const uint32_t length = length_;
        *at_op = 0;
        *reason = PYMERGETIC_RXF_REASON_FORMAT;
        const uint8_t *d = ptr(a_->stage_base);
        if (length < sizeof(pymergetic_rxf_delta_header))
            return false;
        mem::copy(&head_, d, sizeof(head_));
        const pymergetic_rxf_delta_header &head = head_;
        if (head.magic != PYMERGETIC_RXF_DELTA_MAGIC ||
            head.version != PYMERGETIC_RXF_DELTA_VERSION)
            return false;
        const uint64_t strings_end = 64ULL + head.string_bytes;
        const uint64_t ops_start = (strings_end + 7) & ~uint64_t(7);
        ops_start_ = ops_start;
        if (strings_end > length || ops_start > length)
            return false;
        const uint8_t *strings = d + 64;
        const uint32_t ssize = head.string_bytes;
        ssize_ = ssize;
        if (ssize != 0 && strings[ssize - 1] != 0)
            return false;

        // Pass 1: the format of every operation, and how many fixups they will need.
        uint64_t fixups = 0;
        uint64_t pos = ops_start;
        for (uint32_t i = 0; i < head.op_count; i++) {
            *at_op = i;
            if (pos + 8 > length)
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
                                      8ULL * o.patch_count + 16ULL * o.ref_count;
                if (pos + size > length || !string_at(o.path, strings, ssize, &p, &n))
                    return false;
                if (o.type != PYMERGETIC_RXF_STRING_NONE) {
                    if (!string_at(o.type, strings, ssize, &p, &n))
                        return false;
                    fixups++;
                }
                const uint8_t *tail =
                    d + pos + sizeof(o) + ((uint64_t(o.payload_length) + 7) & ~uint64_t(7));
                for (uint32_t k = 0; k < o.patch_count; k++) {
                    const pymergetic_rxf_delta_patch pt =
                        load<pymergetic_rxf_delta_patch>(tail + 8ULL * k);
                    if (uint64_t(pt.offset) + 8 > o.payload_length)
                        return false;
                    if (pt.path != PYMERGETIC_RXF_STRING_NONE) {
                        if (!string_at(pt.path, strings, ssize, &p, &n))
                            return false;
                        fixups++;
                    }
                }
                tail += 8ULL * o.patch_count;
                for (uint32_t k = 0; k < o.ref_count; k++) {
                    const pymergetic_rxf_delta_ref r =
                        load<pymergetic_rxf_delta_ref>(tail + 16ULL * k);
                    if (r.target != PYMERGETIC_RXF_STRING_NONE) {
                        if (!string_at(r.target, strings, ssize, &p, &n))
                            return false;
                        fixups++;
                    }
                    if (r.role != PYMERGETIC_RXF_STRING_NONE) {
                        if (!string_at(r.role, strings, ssize, &p, &n))
                            return false;
                        fixups++;
                    }
                }
                pos += size;
            } else if (op == PYMERGETIC_RXF_DELTA_DELETE || op == PYMERGETIC_RXF_DELTA_ENTRY) {
                const uint32_t s = load<uint32_t>(d + pos + 4);
                if (!(op == PYMERGETIC_RXF_DELTA_ENTRY && s == PYMERGETIC_RXF_STRING_NONE) &&
                    !string_at(s, strings, ssize, &p, &n))
                    return false;
                pos += 8;
            } else {
                return false;
            }
        }
        if (pos != length)
            return false;
        fixups_ = fixups;
        *at_op = 0;
        return true;
    }

    /* The operations on the journal; the journal stays open, and whatever went wrong is the
     * caller's to roll back. */
    bool apply_replay() {
        uint32_t *at_op = &at_op_;
        uint32_t *reason = &reason_;
        const uint8_t *d = ptr(a_->stage_base);
        const pymergetic_rxf_delta_header &head = head_;
        const uint8_t *strings = d + 64;
        const uint32_t ssize = ssize_;
        const uint64_t ops_start = ops_start_;
        const uint64_t fixups = fixups_;
        uint64_t pos = 0;
        scratch_reset();
        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
        Pending *pend = static_cast<Pending *>(take(sizeof(Pending) * (fixups + 1)));
        if (pend == nullptr)
            return false;
        uint32_t npend = 0;

        Memory log;
        log.base = ptr(a_->log_base);
        log.committed = a_->log_capacity;
        log.limit = a_->log_capacity;
        *reason = PYMERGETIC_RXF_REASON_JOURNAL;
        if (arena_.begin(log) != Status::Ok)
            return false;

        bool ok = true;
        pos = ops_start;
        for (uint32_t i = 0; i < head.op_count && ok; i++) {
            *at_op = i;
            const uint32_t op = load<uint32_t>(d + pos);
            const uint8_t *p = nullptr;
            uint32_t n = 0;
            if (op == PYMERGETIC_RXF_DELTA_CREATE || op == PYMERGETIC_RXF_DELTA_SET) {
                const pymergetic_rxf_delta_object o = load<pymergetic_rxf_delta_object>(d + pos);
                const uint64_t padded = (uint64_t(o.payload_length) + 7) & ~uint64_t(7);
                const uint8_t *body = d + pos + sizeof(o);
                const uint8_t *patches = body + padded;
                const uint8_t *refs = patches + 8ULL * o.patch_count;
                pos += sizeof(o) + padded + 8ULL * o.patch_count + 16ULL * o.ref_count;
                string_at(o.path, strings, ssize, &p, &n);
                Handle h = kNone;
                if (op == PYMERGETIC_RXF_DELTA_CREATE) {
                    uint32_t cut = n;
                    while (cut > 0 && p[cut - 1] != '.')
                        cut--;
                    const uint8_t *name = p + cut;
                    const uint32_t name_length = n - cut;
                    Handle parent = arena_.header()->root;
                    if (cut > 0)
                        parent = resolve(p, cut - 1);
                    if (n == 0 || parent == kNone) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        ok = false;
                        break;
                    }
                    if (!good_name(name, name_length)) {
                        *reason = PYMERGETIC_RXF_REASON_NAME;
                        ok = false;
                        break;
                    }
                    if (find_child(parent, name, name_length) != kNone) {
                        *reason = PYMERGETIC_RXF_REASON_EXISTS;
                        ok = false;
                        break;
                    }
                    arena::CellSpec spec;
                    spec.type = kNone;
                    spec.parent = parent;
                    spec.name = reinterpret_cast<const char *>(name);
                    spec.name_length = name_length;
                    spec.flags = o.flags;
                    spec.generation = o.generation;
                    spec.index = o.index;
                    spec.payload_size = o.payload_length;
                    spec.ref_count = o.ref_count;
                    const Status s = arena_.create(spec, &h);
                    if (s != Status::Ok) {
                        *reason = reason_of(s);
                        ok = false;
                        break;
                    }
                    if (!table_insert(h)) {
                        *reason = PYMERGETIC_RXF_REASON_EXISTS;
                        ok = false;
                        break;
                    }
                } else {
                    h = resolve(p, n);
                    if (h == kNone) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        ok = false;
                        break;
                    }
                    const pymergetic_rxf_cell *old = arena_.cell(h);
                    if (old->payload_size != o.payload_length || old->ref_count != o.ref_count) {
                        const Status s = arena_.resize(h, o.payload_length, o.ref_count);
                        if (s != Status::Ok) {
                            *reason = reason_of(s);
                            ok = false;
                            break;
                        }
                    }
                    pymergetic_rxf_cell *c = arena_.cell_w(h);
                    if (c == nullptr) {
                        *reason = PYMERGETIC_RXF_REASON_JOURNAL;
                        ok = false;
                        break;
                    }
                    c->flags = o.flags;
                    c->generation = o.generation;
                    c->index = o.index;
                }
                if (o.payload_length) {
                    uint8_t *payload = arena_.payload_w(h);
                    if (payload == nullptr) {
                        *reason = PYMERGETIC_RXF_REASON_JOURNAL;
                        ok = false;
                        break;
                    }
                    mem::copy(payload, body, o.payload_length);
                }
                if (o.type != PYMERGETIC_RXF_STRING_NONE)
                    pend[npend++] = Pending{static_cast<uint32_t>(h), kPendType, 0, o.type, i};
                else {
                    pymergetic_rxf_cell *c = arena_.cell_w(h);
                    if (c == nullptr) {
                        *reason = PYMERGETIC_RXF_REASON_JOURNAL;
                        ok = false;
                        break;
                    }
                    c->type = kNone;
                }
                for (uint32_t k = 0; k < o.patch_count; k++) {
                    const pymergetic_rxf_delta_patch pt =
                        load<pymergetic_rxf_delta_patch>(patches + 8ULL * k);
                    if (pt.path == PYMERGETIC_RXF_STRING_NONE)
                        store<uint64_t>(arena_.payload_w(h) + pt.offset, kNone);
                    else
                        pend[npend++] =
                            Pending{static_cast<uint32_t>(h), kPendPatch, pt.offset, pt.path, i};
                }
                if (o.ref_count) {
                    pymergetic_rxf_ref *to = arena_.refs_w(h);
                    if (to == nullptr) {
                        *reason = PYMERGETIC_RXF_REASON_JOURNAL;
                        ok = false;
                        break;
                    }
                    for (uint32_t k = 0; k < o.ref_count; k++) {
                        const pymergetic_rxf_delta_ref r =
                            load<pymergetic_rxf_delta_ref>(refs + 16ULL * k);
                        to[k].target = kNone;
                        to[k].role = kNone;
                        to[k].kind = r.kind;
                        to[k].binding = r.binding;
                        to[k].index = r.index;
                        if (r.target != PYMERGETIC_RXF_STRING_NONE)
                            pend[npend++] =
                                Pending{static_cast<uint32_t>(h), kPendTarget, k, r.target, i};
                        if (r.role != PYMERGETIC_RXF_STRING_NONE)
                            pend[npend++] =
                                Pending{static_cast<uint32_t>(h), kPendRole, k, r.role, i};
                    }
                }
            } else if (op == PYMERGETIC_RXF_DELTA_DELETE) {
                string_at(load<uint32_t>(d + pos + 4), strings, ssize, &p, &n);
                pos += 8;
                const Handle h = resolve(p, n);
                if (h == kNone) {
                    *reason = PYMERGETIC_RXF_REASON_PATH;
                    ok = false;
                    break;
                }
                if (h <= PYMERGETIC_RXF_HANDLE_FIELD) {
                    *reason = PYMERGETIC_RXF_REASON_PROTECTED;
                    ok = false;
                    break;
                }
                table_remove(h);
                const Status s = arena_.destroy(h);
                if (s != Status::Ok) {
                    *reason = reason_of(s);
                    ok = false;
                    break;
                }
            } else {
                const uint32_t s = load<uint32_t>(d + pos + 4);
                pos += 8;
                Handle h = kNone;
                if (s != PYMERGETIC_RXF_STRING_NONE) {
                    string_at(s, strings, ssize, &p, &n);
                    h = resolve(p, n);
                    if (h == kNone) {
                        *reason = PYMERGETIC_RXF_REASON_PATH;
                        ok = false;
                        break;
                    }
                }
                arena_.mutable_header()->entry = h;
            }
        }

        // Pass 2: the paths that name other objects, now that all of them are there.
        for (uint32_t k = 0; k < npend && ok; k++) {
            const Pending &e = pend[k];
            *at_op = e.op;
            const uint8_t *p = nullptr;
            uint32_t n = 0;
            string_at(e.path, strings, ssize, &p, &n);
            const Handle t = resolve(p, n);
            if (t == kNone) {
                *reason = PYMERGETIC_RXF_REASON_PATH;
                ok = false;
                break;
            }
            if (e.kind == kPendType) {
                pymergetic_rxf_cell *c = arena_.cell_w(e.handle);
                if (c == nullptr)
                    ok = false;
                else
                    c->type = t;
            } else if (e.kind == kPendPatch) {
                uint8_t *payload = arena_.payload_w(e.handle);
                if (payload == nullptr)
                    ok = false;
                else
                    store<uint64_t>(payload + e.a, t);
            } else {
                pymergetic_rxf_ref *refs = arena_.refs_w(e.handle);
                if (refs == nullptr)
                    ok = false;
                else if (e.kind == kPendTarget)
                    refs[e.a].target = t;
                else
                    refs[e.a].role = t;
            }
            if (!ok)
                *reason = PYMERGETIC_RXF_REASON_JOURNAL;
        }

        if (ok) {
            *reason = PYMERGETIC_RXF_REASON_LINK;
            ok = check_all(reason) && arena_.verify() == Status::Ok;
        }
        return ok;
    }

    /* Every handle that something holds points at an object that is there. */
    bool check_all(uint32_t *reason) {
        scratch_reset();
        const uint64_t n = arena_.handle_count();
        *reason = PYMERGETIC_RXF_REASON_SCRATCH;
        if (!build_kids(false) || !prepare_types())
            return false;
        *reason = PYMERGETIC_RXF_REASON_DANGLING;
        const Handle entry = arena_.header()->entry;
        if (entry != kNone && arena_.cell(entry) == nullptr)
            return false;
        for (Handle h = 0; h < n; h++) {
            const pymergetic_rxf_cell *c = arena_.cell(h);
            if (c == nullptr)
                continue;
            if (c->parent != kNone && arena_.cell(c->parent) == nullptr)
                return false;
            if (c->type != kNone && arena_.cell(c->type) == nullptr)
                return false;
            const pymergetic_rxf_ref *refs = arena_.refs(h);
            for (uint32_t k = 0; k < c->ref_count; k++) {
                if (refs[k].target != kNone && arena_.cell(refs[k].target) == nullptr)
                    return false;
                if (refs[k].role != kNone && arena_.cell(refs[k].role) == nullptr)
                    return false;
            }
            if (c->payload_size == 0 || c->type == kNone)
                continue;
            const uint32_t *words = nullptr;
            if (!refs_of(c->type, &words)) {
                *reason = PYMERGETIC_RXF_REASON_LAYOUT;
                return false;
            }
            for (uint32_t k = 0; k < words[0]; k++) {
                const uint32_t offset = words[1 + k] & 0x7FFFFFFFU;
                if (uint64_t(offset) + 8 > c->payload_size)
                    continue;
                const uint64_t value = load<uint64_t>(arena_.payload(h) + offset);
                if (value != kNone && arena_.cell(value) == nullptr)
                    return false;
            }
        }
        return true;
    }

    Area *a_ = nullptr;
    Arena arena_;
    uint32_t *table_ = nullptr;
    uint32_t slots_ = 0;
    uint8_t *scratch_base_ = nullptr, *scratch_at_ = nullptr, *scratch_end_ = nullptr;
    // structure built per request
    uint32_t n_ = 0;
    uint32_t *start_ = nullptr, *list_ = nullptr;
    uint32_t *memo_ = nullptr, *pool_ = nullptr, *pfx_ = nullptr;
    void *context_ = nullptr;
    // the state of the job that is running
    bool ok_ = false, dry_ = false, guard_ = false;
    uint8_t emit_next_ = 0, relink_next_ = 0;
    static constexpr uint32_t kLeafPump = 29; // RXF_LEAF_LIVE_PUMP (native/graph.h)
    uint32_t length_ = 0, at_op_ = 0, reason_ = 0, override_ = 0, linked_entries_ = 0,
             linked_slots_ = 0, ssize_ = 0;
    uint64_t emitted_ = 0, reclaimed_ = 0, ops_start_ = 0, fixups_ = 0;
    pymergetic_rxf_delta_header head_;
    uint32_t pool_len_ = 0;
    uint32_t empty_[1] = {0};
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
        // itself outside the arena first, and serves the frame from there (native/live.cpp).
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
