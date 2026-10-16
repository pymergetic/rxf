#ifndef PYMERGETIC_RXF_OBJECT_IMAGE_HPP
#define PYMERGETIC_RXF_OBJECT_IMAGE_HPP

/*
 * The host layer over the arena: an Image owns its memory and keeps the
 * derived lookups (parent and name to child) that the format does not store.
 * It enforces what the arena leaves open: names are unique under a parent, a
 * name is one path component, and an object with children cannot be removed.
 * It also builds successors by copying compaction in path order.
 */

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include <pymergetic/rxftk/core/arena/arena.hpp>

namespace pymergetic::rxf::core::object {

using arena::Arena;
using arena::CellSpec;
using arena::Memory;
using arena::Status;
using arena::align_up;
using arena::kMaxRefs;
using arena::kPageSize;

/* Memory reserved up front and committed lazily; the base never moves. */
class HostMemory {
public:
    static std::unique_ptr<HostMemory> reserve(uint64_t limit);
    ~HostMemory();
    HostMemory(const HostMemory &) = delete;
    HostMemory &operator=(const HostMemory &) = delete;
    Memory memory();
    bool commit(uint64_t bytes);
    uint8_t *base() const { return base_; }
    uint64_t limit() const { return limit_; }
    uint64_t committed() const { return committed_; }

private:
    HostMemory() = default;
    uint8_t *base_ = nullptr;
    uint64_t limit_ = 0;
    uint64_t committed_ = 0;
};

struct ObjectSpec {
    pymergetic_rxf_handle type = PYMERGETIC_RXF_HANDLE_NONE;
    pymergetic_rxf_handle parent = PYMERGETIC_RXF_HANDLE_NONE;
    std::string name;               // empty only for the root
    uint32_t kind = 0;              // NodeKind
    uint32_t state = 0;             // packed object state, see PYMERGETIC_RXF_STATE_*
    uint32_t owner_kind = PYMERGETIC_RXF_OWNER_SHARED;
    uint64_t generation = 0;
    uint32_t index = 0;             // position among the ordered siblings
    std::vector<uint8_t> payload;
    std::vector<pymergetic_rxf_ref> refs;
};

inline constexpr uint64_t kDefaultLimit = 1ULL << 30;

/* The state of an object that comes from an image: IMAGE, RETAIN, CLEAN, and the given mobility. */
constexpr uint32_t image_state(uint32_t mobility = PYMERGETIC_RXF_MOBILITY_MOVABLE) {
    return (PYMERGETIC_RXF_PROVENANCE_IMAGE << PYMERGETIC_RXF_STATE_PROVENANCE_SHIFT) |
           (PYMERGETIC_RXF_DISPOSITION_RETAIN << PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT) |
           (PYMERGETIC_RXF_CLEANLINESS_CLEAN << PYMERGETIC_RXF_STATE_CLEANLINESS_SHIFT) |
           (mobility << PYMERGETIC_RXF_STATE_MOBILITY_SHIFT);
}

class Image;

/* A payload may hold handles (fields flagged REFERENCE). The object layer does
 * not know the types, so compaction asks this hook to rewrite them. */
class PayloadRemap {
public:
    virtual ~PayloadRemap() = default;
    /* `payload` is a copy of the object's payload in the source image. `map`
     * gives the new handle of a source handle, or NONE when it is omitted.
     * Return Dangling when a mandatory handle maps to NONE. */
    virtual Status remap(const Image &source, pymergetic_rxf_handle object, uint8_t *payload, uint64_t size,
                         const std::function<pymergetic_rxf_handle(pymergetic_rxf_handle)> &map) const = 0;
};

class Image {
public:
    static Status create(uint64_t limit, uint64_t index_capacity, std::unique_ptr<Image> *out);
    /* Canonical bytes of an image written by to_bytes(). */
    static Status load(const uint8_t *bytes, uint64_t size, uint64_t limit, std::unique_ptr<Image> *out);
    std::vector<uint8_t> to_bytes() const;

    Status add(const ObjectSpec &spec, pymergetic_rxf_handle *out);
    /* The ground of every image: the root, Type, and Field at the fixed handles
     * 0, 1, and 2, then uint32_t and uint64_t, Enumerator, the enumerations of the
     * format (NodeKind, Form, TypeFlags, FieldFlags) with their Enumerator objects,
     * and the Field objects that describe Type, Field and Enumerator. */
    Status bootstrap();
    Status remove(pymergetic_rxf_handle handle);
    Status set_payload(pymergetic_rxf_handle handle, const uint8_t *bytes, uint64_t size);
    Status set_refs(pymergetic_rxf_handle handle, const pymergetic_rxf_ref *refs, uint32_t count);
    Status set_parent(pymergetic_rxf_handle handle, pymergetic_rxf_handle parent);
    Status set_type(pymergetic_rxf_handle handle, pymergetic_rxf_handle type);
    Status set_state(pymergetic_rxf_handle handle, uint32_t state);

    pymergetic_rxf_handle resolve(const std::string &path) const; // PYMERGETIC_RXF_HANDLE_NONE when unknown
    std::string path(pymergetic_rxf_handle handle) const;
    std::vector<pymergetic_rxf_handle> children(pymergetic_rxf_handle handle) const; // ordered by name
    /* The child of `handle` with this name, PYMERGETIC_RXF_HANDLE_NONE when there is none. */
    pymergetic_rxf_handle child(pymergetic_rxf_handle handle, const std::string &name) const;

    /* A successor: retained objects only, in path order, handles 0 to n-1. */
    Status compact(uint64_t index_capacity, std::unique_ptr<Image> *out, const PayloadRemap *remap = nullptr) const;
    /* An independent copy with the same handles (bytes in, bytes out). */
    Status clone(std::unique_ptr<Image> *out) const;
    /* Exchanges everything with `other`; the arena and its memory move together. */
    void swap(Image &other) noexcept;
    Status verify() const;

    /* A transaction on this image: the changes that follow are logged (arena.hpp), and
     * rollback() puts the arena back byte for byte and rebuilds the lookups. */
    /* `log_limit` bounds the undo log in bytes (0: the limit of the image). */
    Status begin(uint64_t log_limit = 0);
    Status commit();
    Status rollback();

    const Arena &arena() const { return arena_; }
    Arena &arena() { return arena_; }
    uint64_t limit() const { return memory_->limit(); }

private:
    Image() = default;
    Status rebuild_index();

    std::unique_ptr<HostMemory> memory_;
    std::unique_ptr<HostMemory> log_; // the undo log of a transaction, reserved on first use
    uint64_t log_limit_ = 0;
    Arena arena_;
    std::unordered_map<pymergetic_rxf_handle, std::map<std::string, pymergetic_rxf_handle>> children_;
};

} // namespace pymergetic::rxf::core::object

#endif
