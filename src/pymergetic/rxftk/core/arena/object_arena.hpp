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
#include <pymergetic/rxftk/core/ground.h>

namespace pymergetic::rxf::core::arena {

class ObjectArena {
  public:
    /* A fresh arena: the superblock at byte 0 and the end marker (the FREE span that
     * covers the tail) at PYMERGETIC_RXF_SUPER_SIZE. The region has to offer at least
     * one ring span behind the superblock. */
    Status init(const Memory &memory, uint32_t root, uint32_t entry);
    /* A leaf that gains a child becomes a module (docs/32 1.2): its value
     * moves behind a fresh chain head and the handle follows the regrow. */
    Status into_module(uint32_t handle, uint32_t *fresh);
    /* The entry of the image (docs/32 2: the superblock's entry): the handle
     * of what runs. */
    void set_entry(uint32_t handle);
    uint32_t entry() const;
    /* An existing image in `memory`; checks the superblock only. */
    Status attach(const Memory &memory);

    /* The committed image of the arena: the superblock and every byte up to
     * superblock.committed (docs/32 1 and 3). A loader reopens it with attach().
     * Two arenas that saw the same work give the same bytes. When the buffer is too
     * small (or out == nullptr) the required size is reported in *written. */
    Status image(uint8_t *out, uint64_t capacity, uint64_t *written) const;
    /* The canonical form of this arena (docs/32 5.4): a rebuild into `out`, an
     * arena the caller has made empty over its own memory (init, not yet
     * bootstrapped). The walk replays the bootstrap whole and then fills the
     * output in the writer's order - the world's tree, then the relation
     * tables, then the genesis tree - and remaps the handles the payloads and
     * the rows carry to the output's (`remap`, one entry per source index,
     * room the caller provides: the engine's work region, the host's heap).
     * A rebuild of a canonical arena is the arena itself, byte for byte. */
    Status rebuild(ObjectArena &out, uint32_t *remap, uint32_t remap_count);
    /* The declared reference slots of a type's payload (docs/32 5): its Field
     * objects that carry FIELD_FLAG_REF tell which u64 words hold a handle.
     * The fixups walk them and the relation rows, one slot at a time; `shift`
     * is the chain head a demoted leaf dropped in the output. */
    bool fixup_refs(ObjectArena &out, uint32_t where, uint32_t source, uint32_t type, uint64_t head,
                    uint64_t shift, const uint32_t *remap, uint32_t remap_count);
    bool fixup_slot(ObjectArena &out, uint32_t where, uint32_t source, uint64_t at, uint64_t shift,
                    const uint32_t *remap, uint32_t remap_count);
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
    /* The children of `handle` in chain order (docs/32 2): the whole live chain of a
     * module, empty for anything that is not one (a leaf cannot have children). The
     * contract of image(): when the buffer is too small (or out == nullptr) the count
     * it needs is reported in *written and the call answers OutOfMemory. */
    Status children(uint32_t handle, uint32_t *out, uint32_t capacity, uint32_t *written);

    /* The record a handle names, checked against the index entry and the record
     * itself; a stale handle answers BadHandle before it touches payload bytes. */
    Status load(uint32_t handle, pymergetic_rxf_object **out);
    /* Where that record and its payload sit in the committed image, in bytes from
     * the arena start (the slot table carries the record offset): what a loader
     * needs to point at an object's value. */
    Status locate(uint32_t handle, uint64_t *record_offset, uint64_t *payload_offset);

    /* The payload behind the record header (docs/32 2): the room the object was
     * created with, zeroed on grant (the record's payload length is that room,
     * and the span depends on it). `set_payload` copies the bytes through the
     * journal and refuses past the room (TooLarge); `payload` answers the room
     * as bytes. */
    Status set_payload(uint32_t handle, const uint8_t *bytes, uint32_t size);
    /* The payload bytes at `offset` behind the record header, the same room
     * check as set_payload. A rebuild writes an object's data and leaves the
     * module head alone: the head is the chain bookkeeping (children, count)
     * and the appends own it, exactly what the writer does with its
     * EMPTY_MODULE_HEAD (tools/native/arena_export.py). */
    Status set_payload_at(uint32_t handle, uint64_t offset, const uint8_t *bytes, uint32_t size);
    Status payload(uint32_t handle, const uint8_t **bytes, uint32_t *size);

    /* The state flags (ground.h PYMERGETIC_RXF_OFLAG_*). The Genesis of the
     * honors (docs/36 3) is marked OFLAG_RO at image build: the flag states the
     * intent as data. */
    Status set_flags(uint32_t handle, uint16_t flags);

    /* The semantic face of an object (docs/32 5): its row of sys/specs, keyed by
     * the index of the handle (the handle is checked like every other use). A
     * freshly created object has the default row - untyped, all zero - until the
     * creator writes it; `set_spec` goes through the journal. An image without the
     * table (not bootstrapped) answers BadImage. */
    Status spec(uint32_t handle, pymergetic_rxf_spec *out);
    Status set_spec(uint32_t handle, const pymergetic_rxf_spec &row);

    /* The tree of an object as the world spells it: the names below the root,
     * joined with '.' (the root's own name is the image, not a path segment). An
     * anonymous object contributes no segment. A handle that is not live answers
     * BadHandle, and a name that does not fit is OutOfMemory with `*written` the
     * length it would take. */
    Status path_of(uint32_t handle, char *out, uint32_t capacity, uint32_t *written);

    /* The refs of an object: the rows of its relation table `sys/relations/<path>`
     * (docs/32 2), remapped to arena handles by the exporter. An object with no
     * table has no refs and answers a count of zero. The rows are the pinned
     * pymergetic_rxf_ref of ground.h. */
    Status refs_of(uint32_t handle, pymergetic_rxf_ref *out, uint32_t capacity, uint32_t *count);

    /* How many indices the object index hands out: the live layer's handle count. */
    uint32_t handle_count() const;

    /* The object's span frees, its index entry dies, and the handle is stale from
     * here on (the reuse of the span bumps the generation). Its spec row returns
     * to the default. */
    Status release_handle(uint32_t handle);

    /* Symbol interning (sys/syms): id 0 is anonymous, the first string takes id 1.
     * The table grows by moving to a larger object - the entry is repointed and
     * the handle stands. */
    Status intern(const char *text, uint16_t *id);
    Status symbol(uint16_t id, const char **text, uint32_t *length);

    /* sys/journal: the undo ring (docs/32 3, steps 4-5). Every write path records
     * the granules it is about to touch before the store; `journal_commit` drops
     * the ring and `journal_rollback` walks it in reverse, restoring every granule
     * to the bytes of the last commit. The log's own state (record appends, the
     * head's count, the ring content while it relocates) is exempt: the ring is
     * the undo log, not its own subject. */
    Status journal_commit();
    Status journal_rollback();
    uint32_t journal_records();
    /* The journal stops recording and its ring is dropped, as at a commit: a
     * detach is not a rollback - what is written is kept. A build (the world->
     * arena writer) detaches: it has nothing to unwind, and the undo ring would
     * otherwise be the biggest thing in the image it makes. The image itself is
     * unaffected - a cold attach re-derives the journal from the tree. */
    Status journal_detach();
    uint32_t journal_handle() const {
        return journal_handle_;
    }

    uint32_t root_handle() const {
        return super()->root;
    }

  private:
    /* Every store goes through the journal: the granules it touches are recorded
     * before the write (docs/32 3, step 4). `j_set` is the field store, `j_fill`
     * and `j_copy` the region writes; on an arena without a journal they just
     * store. */
    template <typename T> Status j_set(T *dst, T value) {
        Status st =
            journal_before_write(static_cast<uint64_t>(reinterpret_cast<uint8_t *>(dst) -
                                                       static_cast<uint8_t *>(memory_.base)),
                                 sizeof(T));
        if (st != Status::Ok)
            return st;
        *dst = value;
        return Status::Ok;
    }
    Status j_fill(void *at, uint8_t byte, uint64_t len);
    Status j_copy(void *at, const void *src, uint64_t len);
    Status journal_before_write(uint64_t at, uint64_t len);
    /* Undo room for one write sequence that caches ring state between reads and
     * writes (docs/32 3, step 4). A ring mutation reserves its worst-case granule
     * count up front, so the log cannot relocate - and with it the tail marker or
     * a free span the mutation still points at - in the middle of the sequence. */
    Status journal_reserve(uint32_t granules);
    Status journal_attach(uint32_t handle);
    Status journal_grow(uint32_t capacity);

    pymergetic_rxf_object *record_at(uint64_t offset) const;
    uint64_t *links_at(uint64_t offset) const;
    /* The end marker: the FREE ring anchor at superblock.committed. */
    uint64_t marker_offset() const;
    Status ring_unlink(uint64_t offset);
    Status ring_insert_after(uint64_t anchor, uint64_t offset);

    /* The object index at PYMERGETIC_RXF_INDEX_OFFSET and one of its entries. */
    pymergetic_rxf_slots *index_table() const;
    pymergetic_rxf_slot *index_entry(uint32_t index) const;
    /* The payload of a Module (children chain head) and of the sys/syms table. */
    pymergetic_rxf_module *module_of(pymergetic_rxf_object *object) const;
    pymergetic_rxf_syms *syms_of(pymergetic_rxf_object *object) const;
    pymergetic_rxf_journal *journal_of(pymergetic_rxf_object *object) const;
    /* The sys/syms table as entry 1 names it (the identity, not a cached pointer). */
    Status syms_record(pymergetic_rxf_object **out);
    /* The id a text already has; false when nothing interned it yet. */
    bool symbol_id(const char *text, uint16_t *id);
    /* The chain: the last child of `parent`, and a new head or link into it. */
    Status append_child(uint32_t parent, uint32_t child);
    /* The rebuild's walk (docs/32 5.4): one object placed into the output and
     * mapped, and the subtree of a node in the writer's order. */
    bool place_one(ObjectArena &out, uint32_t source, uint32_t *remap, uint32_t remap_count);
    bool walk_place(ObjectArena &out, uint32_t first, uint32_t *remap, uint32_t remap_count);
    bool is_ref_table(uint32_t source, uint32_t relations, const pymergetic_rxf_object *record);
    bool next_child(uint32_t parent, uint16_t after, uint32_t *next);
    static bool name_less(const char *a, uint32_t an, const char *b, uint32_t bn);
    bool name_of(uint32_t handle, uint16_t *name);
    bool under(uint32_t source, uint32_t root);
    bool genesis_table(uint32_t source);
    /* The sys/syms table in `capacity` ids and `room` bytes of payload: moved to a
     * larger object and its index entry repointed, so the handle stands. */
    Status syms_relocate(uint32_t capacity, uint32_t room);
    /* sys/free mirrors the FREE ring anchor; no-op on an arena without the model. */
    void sync_free_head();
    /* The rows of sys/specs and how many the table holds. The handle is looked up
     * once (a cold attach has none cached) and kept; an image without the table
     * answers BadImage. */
    Status spec_rows(pymergetic_rxf_spec **rows, uint32_t *count);
    /* The relation table of an object, or the refusal a path that has none: the
     * rows are read out of `sys/relations/<path>`. */
    Status relation_rows(uint32_t handle, const pymergetic_rxf_ref **rows, uint32_t *count);
    /* One child of a module by the exact bytes of its name, and a dotted path
     * walked from the root (the lookup a relation table is found by). */
    Status child_named(uint32_t parent, const char *text, uint32_t length, uint32_t *handle);
    /* Does `name` spell the path of `handle`? Compares from the object up, so the
     * path never has to be materialized. */
    bool path_matches(uint32_t handle, const char *name, uint32_t length);

    Memory memory_{};
    uint64_t need_ = 0;
    uint32_t free_handle_ = 0;
    uint32_t specs_handle_ = 0; /* 0: not looked up yet; HANDLE32_NONE: the image has none */
    uint32_t journal_handle_ = PYMERGETIC_RXF_HANDLE32_NONE;
    bool journal_growing_ = false;
    /* The exempt log regions: the ring payload (its bytes are the undo log) and,
     * mid-relocation, the new span's content. */
    uint64_t journal_lo_ = 0;
    uint64_t journal_hi_ = 0;
    uint64_t grow_lo_ = 0;
    uint64_t grow_hi_ = 0;
    /* The relocation of the ring redirects its own writes here: the appends land
     * in the new ring once the move is done (64 records cover the move's
     * bookkeeping; the ring content copy is exempt, like the appends). */
    static constexpr uint32_t kJournalPending = 64;
    pymergetic_rxf_undo pending_[kJournalPending] = {};
    uint32_t pending_count_ = 0;
};

} // namespace pymergetic::rxf::core::arena

#endif
