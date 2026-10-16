#ifndef PYMERGETIC_RXF_DELTA_DELTA_HPP
#define PYMERGETIC_RXF_DELTA_DELTA_HPP

/*
 * Deltas: the storage and exchange form of an image. A delta is an ordered
 * list of operations addressed by path, never by handle. Fields are written
 * by name from the Type and Field objects of the image, as text; a field that
 * holds a reference is written as the path of its target and may point
 * forward. Replay applies a delta atomically: the image changes only when
 * every reference resolves and the result links. The text form (XML) lives in
 * pymergetic/rxftk/core/xml.
 */

#include <cstdint>
#include <memory>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <pymergetic/rxftk/core/meta/types.hpp>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::delta {

using meta::Attr;
using object::Image;
using object::Status;

/* The kind of an object is the node kind of its Type (DATA when the Type names none) unless it is given. */
constexpr uint32_t kKindDerived = 0xFFFFFFFFU;

struct RefSpec {
    std::string role;   // path of the object that names the relationship (empty: none)
    uint32_t index = 0; // position when the role repeats
    uint32_t kind = PYMERGETIC_RXF_REF_DATA;
    uint32_t binding = PYMERGETIC_RXF_BINDING_MANDATORY;
    std::string target; // path
};

struct Create {
    std::string parent; // path; empty is the root
    std::string name;
    std::string type;   // the name of a Type object
    uint32_t kind = kKindDerived;
    uint32_t mobility = PYMERGETIC_RXF_MOBILITY_MOVABLE;
    uint32_t owner_kind = PYMERGETIC_RXF_OWNER_SHARED;
    uint64_t generation = 0;
    uint32_t index = 0; // position among the ordered siblings
    std::vector<Attr> attrs;
    std::vector<RefSpec> refs;
};

/* Changes what is listed; the rest of the object stays. `name` renames the object and
 * `parent` moves it under another one (docs/27: the text form is the same ops written down). */
struct Set {
    std::string path;
    std::optional<std::string> name;   // rename
    std::optional<std::string> parent; // move
    std::vector<Attr> attrs;
    bool has_refs = false; // replaces all references when true
    std::vector<RefSpec> refs;
    std::optional<uint32_t> mobility;
    std::optional<uint32_t> owner_kind;
    std::optional<uint64_t> generation;
    std::optional<uint32_t> index;
};

/* Removes an object and everything below it; refused while anything else points at it. */
struct Delete {
    std::string path;
};

/* Names the entry Function of the image (empty path: none). */
struct Entry {
    std::string path;
};

using Op = std::variant<Create, Set, Delete, Entry>;

struct Delta {
    std::vector<Op> ops;
};

struct Failure {
    size_t op = static_cast<size_t>(-1); // index of the failing operation, -1 for the commit
    Status status = Status::Ok;
    std::string message;
    std::vector<meta::Issue> issues;
};

/* Applies `delta` to `image`. On failure `image` is unchanged. */
Status apply(Image &image, const Delta &delta, Failure *failure, bool links = true);

/* A failure as text: the operation, the reason, and the link problems, one per line. */
std::string describe(const Failure &failure);

/* The canonical delta of an image: everything but the ground, in path order.
 * With `module`, only that Module object (or "" for what sits at the root) and what
 * it holds, without its sub-modules; the entry belongs to the root. */
Status export_image(const Image &image, Delta *out, std::string *error, const std::string *module = nullptr);

/* A new image with the ground and nothing else. */
Status make_image(uint64_t limit, uint64_t index_capacity, std::unique_ptr<Image> *out);

} // namespace pymergetic::rxf::core::delta

#endif
