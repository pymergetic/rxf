#include <pymergetic/rxftk/core/arena/arena.hpp>
#include <pymergetic/rxftk/core/arena/mem.hpp>

namespace pymergetic::rxf::core::arena {

namespace {

constexpr uint64_t kNoNext = ~0ULL; /* end of the free chain, stored in a free entry */

inline uint32_t kind_of(const pymergetic_rxf_cell *c) { return (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK; }

inline uint64_t cell_bytes(uint64_t payload, uint64_t refs) {
    return align_up(sizeof(pymergetic_rxf_cell) + align_up(payload, PYMERGETIC_RXF_CELL_ALIGN) + refs * sizeof(pymergetic_rxf_ref),
                    PYMERGETIC_RXF_CELL_ALIGN);
}

inline void set_kind(pymergetic_rxf_cell *c, uint32_t kind) {
    c->flags = (c->flags & ~(PYMERGETIC_RXF_FLAG_KIND_MASK << PYMERGETIC_RXF_FLAG_KIND_SHIFT)) | (kind << PYMERGETIC_RXF_FLAG_KIND_SHIFT);
}

inline bool is_free(uint64_t entry) { return (entry & PYMERGETIC_RXF_INDEX_FREE) != 0; }

} // namespace

uint64_t Arena::directory_capacity() const { return (header_->cells_off - header_->index_off) / 8; }

uint64_t *Arena::directory() const { return reinterpret_cast<uint64_t *>(memory_.base + header_->index_off); }

uint64_t *Arena::slot(pymergetic_rxf_handle handle) const {
    const uint64_t chunk_off = directory()[handle / PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES];
    return reinterpret_cast<uint64_t *>(memory_.base + chunk_off + sizeof(pymergetic_rxf_cell)) +
           handle % PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES;
}

Status Arena::ensure(uint64_t end) {
    if (end <= memory_.committed) return Status::Ok;
    need_ = end;
    if (memory_.limit != PYMERGETIC_RXF_LIMIT_UNKNOWN && end > memory_.limit) return Status::OutOfMemory;
#ifdef PYMERGETIC_RXF_FIXED_REGION
    // A region that is all committed from the start: nothing to call, and a native body has no
    // function pointer it could call through.
    return Status::OutOfMemory;
#else
    if (memory_.commit == nullptr) return Status::OutOfMemory;
    uint64_t want = align_up(end, kPageSize);
    if (memory_.limit != PYMERGETIC_RXF_LIMIT_UNKNOWN && want > memory_.limit) want = memory_.limit;
    if (!memory_.commit(memory_.ctx, want)) return Status::OutOfMemory;
    memory_.committed = want;
    if (header_ != nullptr) header_->committed_size = want;
    return Status::Ok;
#endif
}

Status Arena::write_slot(pymergetic_rxf_handle handle, uint64_t value) {
    uint64_t *entry = slot(handle);
    Status status = before_write(static_cast<uint64_t>(reinterpret_cast<uint8_t *>(entry) - memory_.base), 8);
    if (status != Status::Ok) return status;
    *entry = value;
    return Status::Ok;
}

Status Arena::init(const Memory &memory, uint64_t index_capacity) {
    memory_ = memory;
    header_ = nullptr;
    uint64_t chunks = (index_capacity + PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES - 1) / PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES;
    if (chunks == 0) chunks = 1;
    if (chunks > (1ULL << 24)) return Status::TooLarge;
    const uint64_t directory_entries = align_up(chunks, 8);
    const uint64_t cells_off = sizeof(pymergetic_rxf_arena_header) + directory_entries * 8;
    Status status = ensure(cells_off);
    if (status != Status::Ok) return status;
    mem::fill(memory_.base, 0, cells_off);
    header_ = reinterpret_cast<pymergetic_rxf_arena_header *>(memory_.base);
    header_->magic[0] = 'R';
    header_->magic[1] = 'X';
    header_->magic[2] = 'F';
    header_->magic[3] = 'B';
    header_->version = PYMERGETIC_RXF_ARENA_VERSION;
    header_->header_size = sizeof(pymergetic_rxf_arena_header);
    header_->body_size = cells_off;
    header_->committed_size = memory_.committed;
    header_->limit = memory_.limit;
    header_->index_off = sizeof(pymergetic_rxf_arena_header);
    header_->cells_off = cells_off;
    header_->free_head = PYMERGETIC_RXF_HANDLE_NONE;
    header_->entry = PYMERGETIC_RXF_HANDLE_NONE;
    header_->root = PYMERGETIC_RXF_HANDLE_NONE;
    header_->cell_align = PYMERGETIC_RXF_CELL_ALIGN;
    header_->page_size = static_cast<uint32_t>(kPageSize);
    return Status::Ok;
}

Status Arena::attach(const Memory &memory) {
    if (memory.base == nullptr || memory.committed < sizeof(pymergetic_rxf_arena_header)) return Status::BadImage;
    const pymergetic_rxf_arena_header *h = reinterpret_cast<const pymergetic_rxf_arena_header *>(memory.base);
    if (h->magic[0] != 'R' || h->magic[1] != 'X' || h->magic[2] != 'F' || h->magic[3] != 'B') return Status::BadImage;
    if (h->version != PYMERGETIC_RXF_ARENA_VERSION || h->header_size != sizeof(pymergetic_rxf_arena_header)) return Status::BadImage;
    if (h->body_size > memory.committed || h->cells_off > h->body_size) return Status::BadImage;
    memory_ = memory;
    header_ = reinterpret_cast<pymergetic_rxf_arena_header *>(memory_.base);
    header_->committed_size = memory_.committed;
    header_->limit = memory_.limit;
    return Status::Ok;
}

Status Arena::allocate(uint64_t bytes, uint64_t *offset) {
    const uint64_t at = header_->cells_off + header_->frontier;
    if (bytes > (1ULL << 62) || at + bytes < at) return Status::TooLarge;
    Status status = ensure(at + bytes);
    if (status != Status::Ok) return status;
    mem::fill(memory_.base + at, 0, bytes);
    header_->frontier += bytes;
    header_->body_size = header_->cells_off + header_->frontier;
    *offset = at;
    return Status::Ok;
}

Status Arena::make_block(uint32_t block_kind, uint64_t payload_size, uint64_t *offset) {
    Status status = allocate(cell_bytes(payload_size, 0), offset);
    if (status != Status::Ok) return status;
    pymergetic_rxf_cell *c = cell_at(*offset);
    c->type = block_kind;
    c->parent = PYMERGETIC_RXF_HANDLE_NONE;
    c->payload_size = payload_size;
    c->flags = PYMERGETIC_RXF_KIND_BLOCK << PYMERGETIC_RXF_FLAG_KIND_SHIFT;
    return Status::Ok;
}

Status Arena::intern(const char *text, uint64_t length, uint64_t *offset) {
    if (length == 0) {
        *offset = 0;
        return Status::Ok;
    }
    const uint64_t need = length + 1;
    if (header_->names_off != 0 && header_->names_size + need <= cell_at(header_->names_off)->payload_size) {
        // fits in the open block
    } else {
        uint64_t block = 0;
        const uint64_t payload = need > PYMERGETIC_RXF_NAME_BLOCK_PAYLOAD ? align_up(need, PYMERGETIC_RXF_CELL_ALIGN) : PYMERGETIC_RXF_NAME_BLOCK_PAYLOAD;
        Status status = make_block(PYMERGETIC_RXF_BLOCK_NAMES, payload, &block);
        if (status != Status::Ok) return status;
        header_->names_off = block;
        header_->names_size = 0;
    }
    const uint64_t at = header_->names_off + sizeof(pymergetic_rxf_cell) + header_->names_size;
    Status logged = before_write(at, need);
    if (logged != Status::Ok) return logged;
    mem::copy(memory_.base + at, text, length);
    memory_.base[at + length] = 0;
    header_->names_size += need;
    *offset = at;
    return Status::Ok;
}

Status Arena::set_name(pymergetic_rxf_handle handle, const char *text, uint64_t length) {
    uint64_t at = 0;
    Status status = intern(text, length, &at);
    if (status != Status::Ok) return status;
    pymergetic_rxf_cell *c = cell_w(handle);
    if (c == nullptr) return Status::BadHandle;
    c->name = at;
    return Status::Ok;
}

Status Arena::slot_for_new(pymergetic_rxf_handle *handle) {
    if (header_->free_head != PYMERGETIC_RXF_HANDLE_NONE) {
        *handle = header_->free_head;
        return Status::Ok;
    }
    const pymergetic_rxf_handle next = header_->handle_count;
    const uint64_t chunk = next / PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES;
    if (chunk >= header_->index_chunks) {
        if (chunk >= directory_capacity()) return Status::IndexFull;
        uint64_t block = 0;
        Status status = make_block(PYMERGETIC_RXF_BLOCK_INDEX, PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES * sizeof(uint64_t), &block);
        if (status != Status::Ok) return status;
        Status logged = before_write(header_->index_off + chunk * 8, 8);
        if (logged != Status::Ok) return logged;
        directory()[chunk] = block;
        header_->index_chunks = chunk + 1;
    }
    *handle = next;
    return Status::Ok;
}

bool Arena::live(pymergetic_rxf_handle handle) const {
    if (handle >= header_->handle_count) return false;
    const uint64_t entry = *slot(handle);
    return !is_free(entry) && entry >= header_->cells_off && entry < end_offset();
}

pymergetic_rxf_cell *Arena::cell(pymergetic_rxf_handle handle) const { return live(handle) ? cell_at(*slot(handle)) : nullptr; }

uint8_t *Arena::payload(pymergetic_rxf_handle handle) const {
    pymergetic_rxf_cell *c = cell(handle);
    return c ? reinterpret_cast<uint8_t *>(c) + sizeof(pymergetic_rxf_cell) : nullptr;
}

pymergetic_rxf_ref *Arena::refs(pymergetic_rxf_handle handle) const {
    pymergetic_rxf_cell *c = cell(handle);
    return c ? reinterpret_cast<pymergetic_rxf_ref *>(reinterpret_cast<uint8_t *>(c) + sizeof(pymergetic_rxf_cell) +
                                           align_up(c->payload_size, PYMERGETIC_RXF_CELL_ALIGN))
             : nullptr;
}

const char *Arena::name(pymergetic_rxf_handle handle) const {
    pymergetic_rxf_cell *c = cell(handle);
    // No name is the empty string: the first reserved byte of the header, which has to be zero. A
    // string literal would put a relocation in every native body that reads a name.
    return (c && c->name) ? reinterpret_cast<const char *>(memory_.base + c->name) : reinterpret_cast<const char *>(header_->reserved);
}

Status Arena::create(const CellSpec &spec, pymergetic_rxf_handle *out) {
    if (spec.payload_size > kMaxPayload || spec.ref_count > kMaxRefs) return Status::TooLarge;
    pymergetic_rxf_handle handle = 0;
    Status status = slot_for_new(&handle);
    if (status != Status::Ok) return status;
    uint64_t name_off = 0;
    status = intern(spec.name, spec.name_length, &name_off);
    if (status != Status::Ok) return status;
    uint64_t at = 0;
    status = allocate(cell_bytes(spec.payload_size, spec.ref_count), &at);
    if (status != Status::Ok) return status;
    pymergetic_rxf_cell *c = cell_at(at);
    c->type = spec.type;
    c->parent = spec.parent;
    c->payload_size = spec.payload_size;
    c->generation = spec.generation;
    c->index = spec.index;
    c->name = name_off;
    c->flags = spec.flags;
    c->ref_count = spec.ref_count;
    const uint64_t before = *slot(handle);
    status = write_slot(handle, at);
    if (status != Status::Ok) return status;
    if (header_->free_head == handle) {
        header_->free_head = (before == kNoNext) ? PYMERGETIC_RXF_HANDLE_NONE : (before >> 1);
    } else {
        header_->handle_count = handle + 1;
    }
    *out = handle;
    return Status::Ok;
}

Status Arena::destroy(pymergetic_rxf_handle handle) {
    pymergetic_rxf_cell *c = cell(handle);
    if (c == nullptr) return Status::BadHandle;
    if (handle == PYMERGETIC_RXF_HANDLE_ROOT || handle == PYMERGETIC_RXF_HANDLE_TYPE || handle == PYMERGETIC_RXF_HANDLE_FIELD) return Status::Protected;
    c = cell_w(handle);
    if (c == nullptr) return Status::JournalFull;
    Status status = write_slot(handle, header_->free_head == PYMERGETIC_RXF_HANDLE_NONE ? kNoNext : ((header_->free_head << 1) | PYMERGETIC_RXF_INDEX_FREE));
    if (status != Status::Ok) return status;
    set_kind(c, PYMERGETIC_RXF_KIND_DEAD);
    header_->free_head = handle;
    return Status::Ok;
}

Status Arena::resize(pymergetic_rxf_handle handle, uint64_t payload_size, uint32_t ref_count) {
    pymergetic_rxf_cell *old = cell(handle);
    if (old == nullptr) return Status::BadHandle;
    if (payload_size > kMaxPayload || ref_count > kMaxRefs) return Status::TooLarge;
    const uint64_t old_off = *slot(handle);
    uint64_t at = 0;
    Status status = allocate(cell_bytes(payload_size, ref_count), &at);
    if (status != Status::Ok) return status;
    old = cell_at(old_off); // base is stable, kept for clarity
    pymergetic_rxf_cell *fresh = cell_at(at);
    mem::copy(fresh, old, sizeof(*fresh));
    fresh->payload_size = payload_size;
    fresh->ref_count = ref_count;
    fresh->forward = 0;
    const uint64_t keep_payload = old->payload_size < payload_size ? old->payload_size : payload_size;
    mem::copy(reinterpret_cast<uint8_t *>(fresh) + sizeof(pymergetic_rxf_cell),
                     reinterpret_cast<uint8_t *>(old) + sizeof(pymergetic_rxf_cell), keep_payload);
    const uint32_t keep_refs = old->ref_count < ref_count ? old->ref_count : ref_count;
    mem::copy(reinterpret_cast<uint8_t *>(fresh) + sizeof(pymergetic_rxf_cell) + align_up(payload_size, PYMERGETIC_RXF_CELL_ALIGN),
                     reinterpret_cast<uint8_t *>(old) + sizeof(pymergetic_rxf_cell) + align_up(old->payload_size, PYMERGETIC_RXF_CELL_ALIGN),
                     keep_refs * sizeof(pymergetic_rxf_ref));
    old = cell_w(handle);
    if (old == nullptr) return Status::JournalFull;
    status = write_slot(handle, at);
    if (status != Status::Ok) return status;
    set_kind(old, PYMERGETIC_RXF_KIND_DEAD);
    old->forward = at;
    return Status::Ok;
}

// ---- the undo log -------------------------------------------------------------------------------

namespace {
constexpr uint64_t kGranule = 64;
constexpr uint64_t kRecord = 8 + kGranule; /* the granule number, then its old bytes */
} // namespace

Status Arena::ensure_log(uint64_t end) {
    if (end <= log_.committed) return Status::Ok;
#ifdef PYMERGETIC_RXF_FIXED_REGION
    return Status::JournalFull; /* a log region that is committed whole has nothing to grow */
#else
    if (end > log_.limit || log_.commit == nullptr) return Status::JournalFull;
    const uint64_t want = align_up(end, kPageSize) > log_.limit ? log_.limit : align_up(end, kPageSize);
    if (!log_.commit(log_.ctx, want)) return Status::JournalFull;
    log_.committed = want;
    return Status::Ok;
#endif
}

Status Arena::begin(const Memory &log) {
    if (journaling_) return Status::NoJournal;
    if (header_ == nullptr || log.base == nullptr) return Status::BadImage;
    log_ = log;
    mark_ = end_offset();
    bitmap_ = align_up(mark_ / kGranule / 8 + 1, 8);
    Status status = ensure_log(bitmap_);
    if (status != Status::Ok) return status;
    mem::fill(log_.base, 0, bitmap_);
    used_ = bitmap_;
    mem::copy(&saved_, header_, sizeof(saved_));
    journaling_ = true;
    return Status::Ok;
}

Status Arena::before_write(uint64_t offset, uint64_t length) {
    if (!journaling_ || length == 0) return Status::Ok;
    uint64_t end = offset + length < mark_ ? offset + length : mark_;
    if (offset < sizeof(pymergetic_rxf_arena_header)) offset = sizeof(pymergetic_rxf_arena_header); /* the header is restored whole */
    if (end <= offset) return Status::Ok;
    for (uint64_t g = offset / kGranule; g * kGranule < end; g++) {
        uint8_t &bits = log_.base[g / 8];
        const uint8_t bit = static_cast<uint8_t>(1U << (g % 8));
        if (bits & bit) continue;
        Status status = ensure_log(used_ + kRecord);
        if (status != Status::Ok) return status;
        const uint64_t from = g * kGranule;
        const uint64_t size = from + kGranule <= mark_ ? kGranule : mark_ - from;
        mem::copy(log_.base + used_, &g, 8);
        mem::copy(log_.base + used_ + 8, memory_.base + from, size);
        used_ += kRecord;
        bits |= bit;
    }
    return Status::Ok;
}

Status Arena::commit() {
    if (!journaling_) return Status::NoJournal;
    journaling_ = false;
    return Status::Ok;
}

Status Arena::rollback() {
    if (!journaling_) return Status::NoJournal;
    for (uint64_t at = bitmap_; at < used_; at += kRecord) {
        uint64_t g = 0;
        mem::copy(&g, log_.base + at, 8);
        const uint64_t from = g * kGranule;
        const uint64_t size = from + kGranule <= mark_ ? kGranule : mark_ - from;
        mem::copy(memory_.base + from, log_.base + at + 8, size);
    }
    const uint64_t committed = header_->committed_size;
    const uint64_t limit = header_->limit;
    mem::copy(header_, &saved_, sizeof(saved_));
    header_->committed_size = committed;
    header_->limit = limit;
    journaling_ = false;
    return Status::Ok;
}

pymergetic_rxf_cell *Arena::cell_w(pymergetic_rxf_handle handle) {
    pymergetic_rxf_cell *c = cell(handle);
    if (c == nullptr) return nullptr;
    const uint64_t at = static_cast<uint64_t>(reinterpret_cast<uint8_t *>(c) - memory_.base);
    return before_write(at, sizeof(pymergetic_rxf_cell)) == Status::Ok ? c : nullptr;
}

uint8_t *Arena::payload_w(pymergetic_rxf_handle handle) {
    pymergetic_rxf_cell *c = cell(handle);
    if (c == nullptr) return nullptr;
    uint8_t *p = reinterpret_cast<uint8_t *>(c) + sizeof(pymergetic_rxf_cell);
    return before_write(static_cast<uint64_t>(p - memory_.base), c->payload_size) == Status::Ok ? p : nullptr;
}

pymergetic_rxf_ref *Arena::refs_w(pymergetic_rxf_handle handle) {
    pymergetic_rxf_cell *c = cell(handle);
    if (c == nullptr) return nullptr;
    uint8_t *p = reinterpret_cast<uint8_t *>(c) + sizeof(pymergetic_rxf_cell) + align_up(c->payload_size, PYMERGETIC_RXF_CELL_ALIGN);
    return before_write(static_cast<uint64_t>(p - memory_.base), c->ref_count * sizeof(pymergetic_rxf_ref)) == Status::Ok
               ? reinterpret_cast<pymergetic_rxf_ref *>(p)
               : nullptr;
}

Status Arena::verify() const {
    const pymergetic_rxf_arena_header *h = header_;
    if (h == nullptr) return Status::BadImage;
    if (h->magic[0] != 'R' || h->magic[1] != 'X' || h->magic[2] != 'F' || h->magic[3] != 'B') return Status::BadImage;
    if (h->version != PYMERGETIC_RXF_ARENA_VERSION || h->header_size != sizeof(pymergetic_rxf_arena_header)) return Status::BadImage;
    if (h->cell_align != PYMERGETIC_RXF_CELL_ALIGN || h->index_off != sizeof(pymergetic_rxf_arena_header)) return Status::BadImage;
    if (h->cells_off < h->index_off || h->cells_off % 64 != 0 || (h->cells_off - h->index_off) % 64 != 0)
        return Status::BadImage;
    if (h->frontier % PYMERGETIC_RXF_CELL_ALIGN != 0 || h->body_size != h->cells_off + h->frontier) return Status::BadImage;
    if (h->body_size > h->committed_size || h->committed_size > memory_.committed) return Status::BadImage;
    if (h->limit != PYMERGETIC_RXF_LIMIT_UNKNOWN && h->committed_size > h->limit) return Status::BadImage;
    for (uint8_t byte : h->reserved)
        if (byte != 0) return Status::BadImage;
    if (h->index_chunks > directory_capacity()) return Status::BadImage;
    if (h->handle_count > h->index_chunks * PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES) return Status::BadImage;

    uint64_t at = h->cells_off;
    const uint64_t end = end_offset();
    uint64_t chunks_seen = 0;
    uint64_t objects = 0;
    bool names_found = h->names_off == 0;
    while (at < end) {
        if (end - at < sizeof(pymergetic_rxf_cell)) return Status::BadImage;
        const pymergetic_rxf_cell *c = cell_at(at);
        if (c->reserved != 0 || c->payload_size > kMaxPayload || c->ref_count > kMaxRefs) return Status::BadImage;
        const uint64_t size = cell_bytes(c->payload_size, c->ref_count);
        if (size > end - at) return Status::BadImage;
        const uint32_t kind = kind_of(c);
        if (kind == PYMERGETIC_RXF_KIND_BLOCK) {
            if (c->type == PYMERGETIC_RXF_BLOCK_INDEX) {
                if (chunks_seen >= h->index_chunks || directory()[chunks_seen] != at) return Status::BadImage;
                if (c->payload_size != PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES * sizeof(uint64_t)) return Status::BadImage;
                chunks_seen++;
            } else if (c->type == PYMERGETIC_RXF_BLOCK_NAMES) {
                if (at == h->names_off) {
                    if (h->names_size > c->payload_size) return Status::BadImage;
                    names_found = true;
                }
            } else {
                return Status::BadImage;
            }
        } else if (kind != PYMERGETIC_RXF_KIND_DEAD) {
            objects++;
        }
        at += size;
    }
    if (at != end || chunks_seen != h->index_chunks || !names_found) return Status::BadImage;

    uint64_t free_entries = 0;
    uint64_t live_entries = 0;
    for (pymergetic_rxf_handle i = 0; i < h->handle_count; i++) {
        const uint64_t entry = *slot(i);
        if (is_free(entry)) {
            free_entries++;
            continue;
        }
        if (entry < h->cells_off || entry >= end || entry % PYMERGETIC_RXF_CELL_ALIGN != 0) return Status::BadImage;
        const pymergetic_rxf_cell *c = cell_at(entry);
        const uint32_t kind = kind_of(c);
        if (kind == PYMERGETIC_RXF_KIND_BLOCK || kind == PYMERGETIC_RXF_KIND_DEAD) return Status::BadImage;
        if (c->type != PYMERGETIC_RXF_HANDLE_NONE && c->type >= h->handle_count) return Status::BadImage;
        if (c->parent != PYMERGETIC_RXF_HANDLE_NONE && c->parent >= h->handle_count) return Status::BadImage;
        if (c->name != 0) {
            if (c->name < h->cells_off + sizeof(pymergetic_rxf_cell) || c->name >= end) return Status::BadImage;
            uint64_t p = c->name;
            while (p < end && memory_.base[p] != 0) p++;
            if (p >= end) return Status::BadImage;
        }
        const pymergetic_rxf_ref *r = reinterpret_cast<const pymergetic_rxf_ref *>(reinterpret_cast<const uint8_t *>(c) + sizeof(pymergetic_rxf_cell) +
                                                             align_up(c->payload_size, PYMERGETIC_RXF_CELL_ALIGN));
        for (uint32_t k = 0; k < c->ref_count; k++)
            if ((r[k].target != PYMERGETIC_RXF_HANDLE_NONE && r[k].target >= h->handle_count) ||
                (r[k].role != PYMERGETIC_RXF_HANDLE_NONE && r[k].role >= h->handle_count))
                return Status::BadImage;
        live_entries++;
    }
    if (live_entries != objects) return Status::BadImage;
    uint64_t chain = 0;
    pymergetic_rxf_handle next = h->free_head;
    while (next != PYMERGETIC_RXF_HANDLE_NONE) {
        if (next >= h->handle_count || ++chain > free_entries) return Status::BadImage;
        const uint64_t entry = *slot(next);
        if (!is_free(entry)) return Status::BadImage;
        next = entry == kNoNext ? PYMERGETIC_RXF_HANDLE_NONE : (entry >> 1);
    }
    if (chain != free_entries) return Status::BadImage;
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::arena
