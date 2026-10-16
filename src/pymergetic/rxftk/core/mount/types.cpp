#include <cstring>

#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/mount/mount.hpp>

namespace pymergetic::rxf::core::mount {

using bindings::ArenaError;
using bindings::DeltaError;
using meta::FieldInfo;
using meta::Leaf;
using meta::LeafKind;
using meta::TypeInfo;

namespace detail {

State::State(nb::object py)
    : image_py(std::move(py)), image(nb::cast<Image *>(image_py)), types(image) {
    /* The ground every module carries: the root's children that are not modules. */
    for (Handle h : image->children(0)) {
        const pymergetic_rxf_cell *c = image->arena().cell(h);
        const uint32_t kind =
            (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
        if (kind != PYMERGETIC_RXF_NODE_MODULE)
            ground.push_back(h);
    }
}

} // namespace detail

namespace {

using detail::Faces;
using detail::State;

uint64_t load_uint(const uint8_t *p, uint64_t size) {
    uint64_t v = 0;
    std::memcpy(&v, p, size); // little-endian host, the read of the codec (meta/types.cpp)
    return v;
}

/* One leaf as its Python value: a scalar as a number, an enumeration as the path of its
 * Enumerator, a reference as the handle it holds (None where it is unset), raw bytes as
 * bytes. The generic reader of meta/types.cpp without the text round trip: the same
 * layout, the same offsets, straight out of the cell. */
nb::object read_leaf(const std::shared_ptr<State> &state, const Leaf &leaf, const uint8_t *payload,
                     uint64_t payload_size) {
    if (leaf.offset + leaf.size > payload_size)
        throw ArenaError("payload does not fit its type");
    const uint8_t *p = payload + leaf.offset;
    switch (leaf.kind) {
    case LeafKind::Bool:
        return nb::bool_(load_uint(p, leaf.size) != 0);
    case LeafKind::Uint:
        return nb::int_(load_uint(p, leaf.size));
    case LeafKind::Sint: {
        const uint64_t v = load_uint(p, leaf.size);
        const uint64_t sign = 1ULL << (leaf.size * 8 - 1);
        const int64_t s =
            leaf.size == 8 ? static_cast<int64_t>(v) : static_cast<int64_t>((v ^ sign) - sign);
        return nb::int_(s);
    }
    case LeafKind::Float:
        if (leaf.size == 4) {
            float f;
            std::memcpy(&f, p, 4);
            return nb::float_(f);
        } else {
            double d;
            std::memcpy(&d, p, 8);
            return nb::float_(d);
        }
    case LeafKind::Hex:
        return nb::bytes(reinterpret_cast<const char *>(p), leaf.size);
    case LeafKind::Enum: {
        const uint64_t v = load_uint(p, leaf.size);
        if (v == 0)
            return nb::none();
        const meta::EnumInfo *e = state->types.enumeration(leaf.enumeration);
        if (e == nullptr)
            throw DeltaError("field " + leaf.name + " is not of an enumeration type");
        const std::string head = state->image->path(leaf.enumeration) + ".";
        if (!e->bits) {
            for (const auto &item : e->items)
                if (item.second == v)
                    return nb::str((head + item.first).c_str());
        } else {
            std::string text;
            uint64_t rest = v;
            for (const auto &item : e->items) {
                if (item.second == 0 || (item.second & rest) != item.second)
                    continue;
                text += text.empty() ? head : "|";
                text += item.first;
                rest &= ~item.second;
            }
            if (rest == 0)
                return nb::str(text.c_str());
        }
        throw DeltaError("field " + leaf.name + " holds " + std::to_string(v) + ", which " +
                         state->image->path(leaf.enumeration) + " does not name");
    }
    case LeafKind::Ref: {
        const uint64_t h = load_uint(p, 8);
        if (h == PYMERGETIC_RXF_HANDLE_NONE)
            return nb::none();
        if (state->image->arena().cell(h) == nullptr)
            throw ArenaError("reference " + leaf.name + " holds a dead handle");
        return nb::int_(h);
    }
    }
    throw ArenaError("unknown leaf kind");
}

/* The instance slot every synthesized type carries (the pinned name: `id`). */
Handle handle_of(nb::handle self) {
    return nb::cast<Handle>(self.attr("id"));
}

/* The value of one property: the leaf read from the object's payload at the field's
 * offset, straight out of the cell (no codec, no dict). */
nb::object leaf_getter(const std::shared_ptr<State> &state, Leaf leaf) {
    return nb::cpp_function(
        [state, leaf](nb::handle self) {
            const Handle handle = handle_of(self);
            const pymergetic_rxf_cell *c = state->image->arena().cell(handle);
            if (c == nullptr)
                throw ArenaError("BadHandle");
            return read_leaf(state, leaf, state->image->arena().payload(handle), c->payload_size);
        },
        nb::arg("self"));
}

/* The value of one declared relation (Children, Inverse): its objects, in order, mounted.
 * The register is consulted weakly: a face list must not own the register that owns it. */
nb::object member_getter(const std::shared_ptr<State> &state, std::weak_ptr<Faces> faces,
                         Handle field) {
    return nb::cpp_function(
        [state, faces, field](nb::handle self) -> nb::object {
            std::shared_ptr<Faces> held = faces.lock();
            if (!held)
                held = std::make_shared<Faces>();
            const Handle handle = handle_of(self);
            for (const meta::Member &m : meta::members(state->types, handle)) {
                if (m.field != field)
                    continue;
                nb::list out;
                for (Handle item : m.items)
                    out.append(detail::wrap(state, held, item));
                return nb::object(out);
            }
            return nb::object(nb::list());
        },
        nb::arg("self"));
}

} // namespace

namespace detail {

nb::object make_class(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                      Handle type) {
    auto it = faces->classes.find(type);
    if (it != faces->classes.end())
        return it->second;
    const TypeInfo *t = state->types.get(type);
    if (t == nullptr)
        throw ArenaError("not a Type object");
    const meta::Layout &layout = state->types.layout(type);

    nb::object builtins = nb::module_::import_("builtins");
    nb::dict d;
    d["__doc__"] = nb::str(
        ("mounted from the Type object " + state->image->path(type) + " (docs/16)").c_str());
    d["__image__"] = state->image_py;
    d["__type_handle__"] = nb::int_(type);
    d["__init__"] =
        nb::cpp_function([](nb::object self, Handle id) { self.attr("id") = id; }, nb::is_method(),
                         nb::arg("id"), "The instance slot: the handle of the node it reads.");
    d["__repr__"] = nb::cpp_function(
        [state, name = t->name](nb::handle self) {
            return nb::str(("<" + name + " " + state->image->path(handle_of(self)) + ">").c_str());
        },
        nb::is_method());

    /* The payload values: one property per leaf, reading the cell at the field's offset.
     * Derived leaves take no value (docs/27) and no property. Where the leaves end and the
     * type is not fully covered, the tail bytes ride as `data` (a leaf of that name wins). */
    bool has_data = false;
    if (layout.error.empty()) {
        for (const Leaf &leaf : layout.leaves) {
            if (leaf.derived || leaf.name == "id")
                continue;
            if (leaf.name == "data")
                has_data = true;
            d[leaf.name.c_str()] = builtins.attr("property")(leaf_getter(state, leaf));
        }
        for (const FieldInfo &m : t->members) {
            if (m.name == "id")
                continue;
            if (m.name == "data")
                has_data = true;
            d[m.name.c_str()] = builtins.attr("property")(member_getter(state, faces, m.id));
        }
    }
    if (!has_data) {
        d["data"] = builtins.attr("property")(nb::cpp_function(
            [state, prefix = layout.prefix](nb::handle self) {
                const Handle handle = handle_of(self);
                const pymergetic_rxf_cell *c = state->image->arena().cell(handle);
                if (c == nullptr)
                    throw ArenaError("BadHandle");
                const uint64_t size = c->payload_size > prefix ? c->payload_size - prefix : 0;
                return nb::bytes(
                    reinterpret_cast<const char *>(state->image->arena().payload(handle) + prefix),
                    size);
            },
            nb::arg("self")));
    }

    nb::object cls = builtins.attr("type")(nb::str(t->name.c_str()), nb::make_tuple(), d);
    faces->classes.emplace(type, cls);
    return cls;
}

nb::object wrap(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                Handle handle) {
    const pymergetic_rxf_cell *c = state->image->arena().cell(handle);
    if (c == nullptr)
        throw ArenaError("BadHandle");
    const uint32_t kind =
        (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
    if (kind == PYMERGETIC_RXF_NODE_MODULE)
        return make_module(state, faces, handle);
    auto it = faces->objects.find(handle);
    if (it != faces->objects.end())
        return it->second;
    nb::object out = make_class(state, faces, c->type)(handle);
    faces->objects.emplace(handle, out);
    return out;
}

} // namespace detail

Synthesizer::Synthesizer(nb::object image)
    : state_(std::make_shared<detail::State>(std::move(image))),
      faces_(std::make_shared<detail::Faces>()) {
}

} // namespace pymergetic::rxf::core::mount
