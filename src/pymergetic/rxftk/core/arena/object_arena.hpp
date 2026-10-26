#ifndef PYMERGETIC_RXF_ARENA_OBJECT_ARENA_HPP
#define PYMERGETIC_RXF_ARENA_OBJECT_ARENA_HPP

/*
 * The v7 one-arena allocator (docs/32 3): the FREE ring over the object records of
 * the superblock model. s_allocate takes a span from the ring (best fit, split when
 * the remainder can carry links), s_ensure grows the region through the seat hook,
 * s_release returns a span to the ring. Freestanding like the rest of the arena:
 * no exceptions, no RTTI, no heap, no library call.
 */

#include <pymergetic/rxftk/core/arena/arena.hpp>

namespace pymergetic::rxf::core::arena {

class ObjectArena {
  public:
    /* A fresh arena: the superblock at byte 0 and the end marker (the FREE span that
     * covers the tail) at PYMERGETIC_RXF_SUPER_SIZE. The region has to offer at least
     * one ring span behind the superblock. */
    Status init(const Memory &memory, uint32_t root, uint32_t entry);
    /* An existing image in `memory`; checks the superblock only. */
    Status attach(const Memory &memory);
    /* Every invariant of the layout and of the FREE ring. Walks the whole arena. */
    Status verify() const;

    /* A zeroed object record (header + `room` payload bytes, room rounded up to 8).
     * The record keeps its payload field as the granted room; kind, name, parent,
     * link and refs are zero and the caller writes them. The generation is bumped
     * when the span is reused, so a stale handle fails its check. */
    Status s_allocate(uint64_t room, uint64_t *offset);
    /* A span frees: the end marker grows downward when the span is adjacent to it,
     * a ring span (40 bytes or more) carries links, and a smaller one stays a
     * linkless fragment until the tail merge or compaction takes it. */
    Status s_release(uint64_t offset);
    /* Writes at [at, at + length) need to be inside committed bytes. The growth hook
     * (Memory.commit, the seat fill) raises committed; a region without a hook - or
     * compiled PYMERGETIC_RXF_FIXED_REGION - refuses and records `need`. */
    Status s_ensure(uint64_t at, uint64_t length);

    pymergetic_rxf_superblock *super() const {
        return reinterpret_cast<pymergetic_rxf_superblock *>(memory_.base);
    }
    const Memory &memory() const {
        return memory_;
    }
    uint64_t committed() const {
        return super()->committed;
    }
    uint64_t limit() const {
        return super()->limit;
    }
    /* The end offset of the last ask that needed growth (0: none yet). */
    uint64_t need() const {
        return need_;
    }

  private:
    pymergetic_rxf_object *record_at(uint64_t offset) const;
    uint64_t *links_at(uint64_t offset) const;
    /* The end marker: the FREE ring anchor at superblock.committed. */
    uint64_t marker_offset() const;
    void ring_unlink(uint64_t offset);
    void ring_insert_after(uint64_t anchor, uint64_t offset);

    Memory memory_{};
    uint64_t need_ = 0;
};

} // namespace pymergetic::rxf::core::arena

#endif
