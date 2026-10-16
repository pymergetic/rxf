#ifndef PYMERGETIC_RXF_ARENA_ARENA_HPP
#define PYMERGETIC_RXF_ARENA_ARENA_HPP

/*
 * The arena: allocator, index, and cell access over one contiguous memory
 * region. This layer is freestanding: no exceptions, no RTTI, no heap, no
 * standard library, no library call at all: bytes are moved by the inline loops of mem.hpp.
 *
 * Layout: [header][index directory][cell stream]. The cell stream holds object
 * cells and block cells (index chunks, name blocks), so one sequential walk
 * covers all of it. See docs/03-ALLOCATOR-STATE.md.
 */

#include <pymergetic/rxftk/core/ground.h>

namespace pymergetic::rxf::core::arena {

enum class Status : int32_t {
    Ok = 0,
    OutOfMemory,    // the memory region cannot commit more bytes
    BadHandle,      // not a live handle
    BadImage,       // a layout invariant does not hold
    IndexFull,      // the index directory has no room for another chunk
    TooLarge,       // a size is outside what a cell can describe
    Protected,      // the root, Type, and Field cannot be destroyed
    // The host layer (core/object) adds these.
    NameTaken,      // a sibling already has this name
    BadName,        // empty, or contains '.' or NUL
    HasChildren,    // an object with children cannot be removed
    Dangling,       // a mandatory reference or a parent points at an omitted object
    BadPath,        // a path does not resolve
    JournalFull,    // the undo log has no room; the transaction has to be rolled back
    NoJournal,      // begin() twice, or commit() and rollback() without begin()
};

inline constexpr const char *status_name(Status status) {
    switch (status) {
    case Status::Ok: return "Ok";
    case Status::OutOfMemory: return "OutOfMemory";
    case Status::BadHandle: return "BadHandle";
    case Status::BadImage: return "BadImage";
    case Status::IndexFull: return "IndexFull";
    case Status::TooLarge: return "TooLarge";
    case Status::Protected: return "Protected";
    case Status::NameTaken: return "NameTaken";
    case Status::BadName: return "BadName";
    case Status::HasChildren: return "HasChildren";
    case Status::Dangling: return "Dangling";
    case Status::BadPath: return "BadPath";
    case Status::JournalFull: return "JournalFull";
    case Status::NoJournal: return "NoJournal";
    }
    return "Unknown";
}

/* The region an arena lives in. `base` never moves. Bytes below `committed`
 * are usable. `commit`, when set, raises `committed` (up to `limit`). */
struct Memory {
    uint8_t *base = nullptr;
    uint64_t committed = 0;
    uint64_t limit = PYMERGETIC_RXF_LIMIT_UNKNOWN;
    bool (*commit)(void *ctx, uint64_t bytes) = nullptr;
    void *ctx = nullptr;
};

/* What to create. A reference slot is zeroed; the caller fills refs() after. */
struct CellSpec {
    pymergetic_rxf_handle type = PYMERGETIC_RXF_HANDLE_NONE;
    pymergetic_rxf_handle parent = PYMERGETIC_RXF_HANDLE_NONE;
    const char *name = nullptr; /* NUL-free bytes; nullptr or length 0 = no name */
    uint64_t name_length = 0;
    uint32_t flags = 0;         /* kind, state, owner kind */
    uint64_t generation = 0;
    uint32_t index = 0;
    uint64_t payload_size = 0;
    uint32_t ref_count = 0;
};

inline constexpr uint64_t kPageSize = 4096;
inline constexpr uint64_t kMaxPayload = 1ULL << 48;
inline constexpr uint32_t kMaxRefs = 1U << 24;

inline constexpr uint64_t align_up(uint64_t value, uint64_t alignment) {
    return (value + alignment - 1) & ~(alignment - 1);
}

class Arena {
public:
    /* A fresh, empty arena. `index_capacity` is the number of handles the index
     * can hold; it is rounded up to whole chunks and the directory to a
     * multiple of 8 chunks. */
    Status init(const Memory &memory, uint64_t index_capacity);
    /* An existing image in `memory`; checks the header only. */
    Status attach(const Memory &memory);
    /* Every invariant of the layout. Walks the whole arena. */
    Status verify() const;

    Status create(const CellSpec &spec, pymergetic_rxf_handle *out);
    /* Marks the cell TOMBSTONE and frees its slot. The bytes stay until compaction. */
    Status destroy(pymergetic_rxf_handle handle);
    /* The name of a cell is repointed at freshly interned text (rename). The old bytes stay
     * in the NAME block until compaction, as any other abandoned name. */
    Status set_name(pymergetic_rxf_handle handle, const char *text, uint64_t length);
    /* A cell of another size in the same slot. The old cell is left as a
     * TOMBSTONE whose `forward` names the new one. */
    Status resize(pymergetic_rxf_handle handle, uint64_t payload_size, uint32_t ref_count);

    /* The undo log (docs/19). begin() records the header and the end of the cell stream;
     * from then on every write below that end has to be announced, before it is made, by
     * before_write() or by the writable accessors; the first write to a 64-byte granule keeps
     * its old bytes in `log`. rollback() puts back the granules and the header, so the arena
     * is as it was at begin(), byte for byte up to that end; commit() drops the log. Whatever
     * is written at or above the end of begin() is new and needs nothing. A write that answers
     * JournalFull (or a null pointer from an accessor of a live object) was not made, and the
     * only way on is rollback(). `log` is memory the arena does not own; at least one bit per
     * granule below the end and 72 bytes for each granule a transaction touches. */
    Status begin(const Memory &log);
    Status commit();
    Status rollback();
    bool journaling() const { return journaling_; }
    Status before_write(uint64_t offset, uint64_t length);
    /* Pointers for writing: the cell header (64 bytes), the payload, the references. nullptr
     * when the handle is not live or the log is full. */
    pymergetic_rxf_cell *cell_w(pymergetic_rxf_handle handle);
    uint8_t *payload_w(pymergetic_rxf_handle handle);
    pymergetic_rxf_ref *refs_w(pymergetic_rxf_handle handle);

    bool live(pymergetic_rxf_handle handle) const;
    pymergetic_rxf_cell *cell(pymergetic_rxf_handle handle) const;       /* nullptr when not live */
    uint8_t *payload(pymergetic_rxf_handle handle) const;     /* nullptr when not live */
    pymergetic_rxf_ref *refs(pymergetic_rxf_handle handle) const;        /* nullptr when not live */
    const char *name(pymergetic_rxf_handle handle) const;     /* "" when unnamed */

    /* Sequential walk over the cell stream; offsets are arena-relative. */
    uint64_t first_offset() const { return header_->cells_off; }
    uint64_t end_offset() const { return header_->cells_off + header_->frontier; }
    pymergetic_rxf_cell *cell_at(uint64_t offset) const {
        return reinterpret_cast<pymergetic_rxf_cell *>(memory_.base + offset);
    }

    const pymergetic_rxf_arena_header *header() const { return header_; }
    pymergetic_rxf_arena_header *mutable_header() { return header_; }
    const Memory &memory() const { return memory_; }
    uint64_t handle_count() const { return header_->handle_count; }
    /* The end offset the last allocation asked for and the region did not have (0: none yet). A
     * host that commits pages on demand reads it to know how far to commit. */
    uint64_t need() const { return need_; }

private:
    Status ensure(uint64_t end);
    Status ensure_log(uint64_t end);
    Status write_slot(pymergetic_rxf_handle handle, uint64_t value);
    Status allocate(uint64_t bytes, uint64_t *offset);
    Status make_block(uint32_t block_kind, uint64_t payload_size, uint64_t *offset);
    Status intern(const char *text, uint64_t length, uint64_t *offset);
    Status slot_for_new(pymergetic_rxf_handle *handle);
    uint64_t *slot(pymergetic_rxf_handle handle) const;
    uint64_t *directory() const;
    uint64_t directory_capacity() const;

    Memory memory_{};
    pymergetic_rxf_arena_header *header_ = nullptr;

    Memory log_{};
    bool journaling_ = false;
    pymergetic_rxf_arena_header saved_; /* the header at begin(); not zeroed (a block clear is a library call) */
    uint64_t mark_ = 0;                   /* end of the cell stream at begin() */
    uint64_t used_ = 0;                   /* bytes of the log in use: bitmap, then records */
    uint64_t bitmap_ = 0;                 /* bytes of the bitmap at the start of the log */
    uint64_t need_ = 0;                   /* see need() */
};

} // namespace pymergetic::rxf::core::arena

#endif
