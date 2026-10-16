#include <cstring>

#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/delta/edit.hpp>
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

/* The edit set (docs/27) on a mounted face: one mutation is one delta, refused exactly as the
 * delta refuses it (the verbs of core/delta/edit.cpp are the one implementation). */
void put_leaf(const std::shared_ptr<State> &state, Handle handle, const std::string &field,
              const nb::object &value) {
    std::string error;
    if (delta::edit_put(*state->image, handle, field, bindings::value_text(value), &error) !=
        object::Status::Ok)
        throw DeltaError(error);
}

/* The names the face itself holds: a field of one of these names takes no property (the edit
 * verbs and the instance slot win). */
bool reserved(const std::string &name) {
    return name == "id" || name == "put" || name == "ref" || name == "unset" || name == "rename" ||
           name == "move" || name == "delete";
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

/* The write side of one property: `alloc.size = 4096` is put (docs/27). A reference is
 * written by ref() only — the one place a handle may be written. */
nb::object leaf_setter(const std::shared_ptr<State> &state, Leaf leaf) {
    return nb::cpp_function(
        [state, leaf](nb::handle self, nb::object value) {
            if (leaf.kind == LeafKind::Ref)
                throw DeltaError("a reference is written by ref(): " + leaf.name);
            put_leaf(state, handle_of(self), leaf.name, value);
        },
        nb::arg("self"), nb::arg("value"));
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

    /* The edit set (docs/27) as methods on every mounted class: one mutation is one delta,
     * a property assignment is put, and a reference is written by ref() — the one place a
     * handle may be written. */
    d["put"] = nb::cpp_function(
        [state](nb::handle self, const std::string &field, nb::object value) {
            put_leaf(state, handle_of(self), field, value);
        },
        nb::is_method(), nb::arg("field"), nb::arg("value"), "One leaf is written (docs/27).");
    d["ref"] = nb::cpp_function(
        [state](nb::handle self, const std::string &field, nb::object to) {
            std::string error;
            if (delta::edit_ref(*state->image, handle_of(self), field,
                                bindings::target_handle(*state->image, to),
                                &error) != object::Status::Ok)
                throw DeltaError(error);
        },
        nb::is_method(), nb::arg("field"), nb::arg("to"),
        "The reference field of this object points at another (docs/27).");
    d["unset"] = nb::cpp_function(
        [state](nb::handle self, const std::string &field) {
            std::string error;
            if (delta::edit_unset(*state->image, handle_of(self), field, &error) != object::Status::Ok)
                throw DeltaError(error);
        },
        nb::is_method(), nb::arg("field"), "An OPTIONAL field goes back to unset (docs/27).");
    d["rename"] = nb::cpp_function(
        [state](nb::handle self, const std::string &name) {
            std::string error;
            if (delta::edit_rename(*state->image, handle_of(self), name, &error) != object::Status::Ok)
                throw DeltaError(error);
        },
        nb::is_method(), nb::arg("name"), "The name of this object changes (docs/27).");
    d["move"] = nb::cpp_function(
        [state](nb::handle self, nb::object parent) {
            std::string error;
            if (delta::edit_move(*state->image, handle_of(self),
                                 bindings::target_handle(*state->image, parent),
                                 &error) != object::Status::Ok)
                throw DeltaError(error);
        },
        nb::is_method(), nb::arg("parent"), "This object sits under another parent (docs/27).");
    d["delete"] = nb::cpp_function(
        [state](nb::handle self) {
            std::string error;
            if (delta::edit_delete(*state->image, handle_of(self), &error) != object::Status::Ok)
                throw DeltaError(error);
        },
        nb::is_method(), "This object and everything below it are removed (docs/27).");

    /* The payload values: one property per leaf, reading the cell at the field's offset and
     * writing it as put (docs/27). Derived leaves take no value (docs/27) and no property.
     * Where the leaves end and the type is not fully covered, the tail bytes ride as `data`
     * (a leaf of that name wins). */
    bool has_data = false;
    if (layout.error.empty()) {
        for (const Leaf &leaf : layout.leaves) {
            if (leaf.derived || reserved(leaf.name))
                continue;
            if (leaf.name == "data")
                has_data = true;
            d[leaf.name.c_str()] = builtins.attr("property")(leaf_getter(state, leaf),
                                                            leaf_setter(state, leaf));
        }
        for (const FieldInfo &m : t->members) {
            if (reserved(m.name))
                continue;
            if (m.name == "data")
                has_data = true;
            d[m.name.c_str()] = builtins.attr("property")(
                member_getter(state, faces, m.id), nb::cpp_function(
                    [](nb::handle, nb::object) {
                        throw DeltaError("a declared list takes no value");
                    },
                    nb::arg("self"), nb::arg("value")));
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
