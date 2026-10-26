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

    /* The sys/ skeleton (docs/32 2): the object index as the first object of the
     * arena (the one anchor a cold attach needs), the root image, sys, and the
     * system leaves - objects, functions, journal, caps, free, syms. Names are
     * interned into sys/syms; the root handle is published in the superblock and
     * sys/free mirrors the FREE ring anchor (allocator state). */
    Status bootstrap(const char *image_name, uint32_t capacity = 1024);

    /* An object under `parent`, published into the index: name (null = anonymous),
     * kind, and zeroed payload room (granted, like s_allocate). The handle it
     * answers is (index, generation): the identity every later use is checked
     * against. A full index refuses (IndexFull), the shape the v6 index has. */
    Status create(uint32_t parent, const char *name, uint16_t kind, uint64_t room,
                  uint32_t *handle);

    /* The tree walk (docs/32 2): the child of `parent` named `name`, or
     * PYMERGETIC_RXF_HANDLE32_NONE when the chain has none. */
    Status find(uint32_t parent, const char *name, uint32_t *handle);

    /* The record a handle names, checked against the index entry and the record
     * itself; a stale handle answers BadHandle before it touches payload bytes. */
    Status load(uint32_t handle, pymergetic_rxf_object **out);

    /* The object's span frees, its index entry dies, and the handle is stale from
     * here on (the reuse of the span bumps the generation). */
    Status release_handle(uint32_t handle);

    /* Symbol interning (sys/syms): id 0 is anonymous, the first string takes id 1.
     * The table grows by moving to a larger object - the entry is repointed and
     * the handle stands. */
    Status intern(const char *text, uint16_t *id);
    Status symbol(uint16_t id, const char **text, uint32_t *length);

    uint32_t root_handle() const {
        return super()->root;
    }

  private:
    pymergetic_rxf_object *record_at(uint64_t offset) const;
    uint64_t *links_at(uint64_t offset) const;
    /* The end marker: the FREE ring anchor at superblock.committed. */
    uint64_t marker_offset() const;
    void ring_unlink(uint64_t offset);
    void ring_insert_after(uint64_t anchor, uint64_t offset);

    /* The object index at PYMERGETIC_RXF_INDEX_OFFSET and one of its entries. */
    pymergetic_rxf_slots *index_table() const;
    pymergetic_rxf_slot *index_entry(uint32_t index) const;
    /* The payload of a Module (children chain head) and of the sys/syms table. */
    pymergetic_rxf_module *module_of(pymergetic_rxf_object *object) const;
    pymergetic_rxf_syms *syms_of(pymergetic_rxf_object *object) const;
    /* The sys/syms table as entry 1 names it (the identity, not a cached pointer). */
    Status syms_record(pymergetic_rxf_object **out);
    /* The id a text already has; false when nothing interned it yet. */
    bool symbol_id(const char *text, uint16_t *id);
    /* The chain: the last child of `parent`, and a new head or link into it. */
    Status append_child(uint32_t parent, uint32_t child);
    /* The sys/syms table in `capacity` ids and `room` bytes of payload: moved to a
     * larger object and its index entry repointed, so the handle stands. */
    Status syms_relocate(uint32_t capacity, uint32_t room);
    /* sys/free mirrors the FREE ring anchor; no-op on an arena without the model. */
    void sync_free_head();

    Memory memory_{};
    uint64_t need_ = 0;
    uint32_t free_handle_ = 0;
};

} // namespace pymergetic::rxf::core::arena

#endif
