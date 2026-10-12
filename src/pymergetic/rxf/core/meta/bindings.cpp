#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/ground.h>
#include <pymergetic/rxf/core/hash/sha256.hpp>
#include <pymergetic/rxf/core/meta/types.hpp>

namespace pymergetic::rxf::core::bindings {

using object::Image;

/* Adds what the self-description gives an image: the link check, fields by name, layouts, compaction, and the digest. */
void bind_meta(nb::module_ &, nb::class_<Image> &image) {
    image
        .def(
            "members",
            [](const Image &self, uint64_t handle) {
                meta::Types types(&self);
                std::vector<std::pair<uint64_t, std::vector<uint64_t>>> out;
                for (meta::Member &m : meta::members(types, handle)) out.emplace_back(m.field, std::move(m.items));
                return out;
            },
            nb::arg("handle"),
            "The declared relations of an object (Children and Inverse fields) as (field, objects in order).")
        .def(
            "ordered",
            [](const Image &self, const std::vector<uint64_t> &handles) {
                meta::Types types(&self);
                return meta::ordered(types, handles);
            },
            nb::arg("handles"), "The objects in the order of siblings: by position, then by name with [n] read as a number.")
        .def(
            "compact",
            [](const Image &self, uint64_t index_capacity) {
                std::unique_ptr<Image> next;
                check(meta::compact(self, index_capacity, &next));
                return next;
            },
            nb::arg("index_capacity") = 0, "A successor: retained objects only, in path order, handles 0 to n-1.")
        .def(
            "check",
            [](const Image &self) {
                meta::Types types(&self);
                std::vector<std::pair<std::string, std::string>> out;
                for (const auto &i : meta::check(types)) out.emplace_back(i.path, i.message);
                return out;
            },
            "Structural problems as (path, message); empty when the image links.")
        .def(
            "alias_uses",
            [](const Image &self) {
                meta::Types types(&self);
                std::vector<std::pair<uint64_t, uint64_t>> out;
                for (const auto &use : meta::alias_uses(types)) out.emplace_back(use.alias, use.user);
                return out;
            },
            "The objects whose written paths go through an Alias, as (alias, user) handles; each pair once.")
        .def(
            "paths",
            [](const Image &self, uint64_t handle) {
                meta::Types types(&self);
                if (self.arena().cell(handle) == nullptr) throw ArenaError("BadHandle");
                return meta::written_paths(types, handle);
            },
            nb::arg("handle"), "The paths an object holds that the text form writes (and an alias may shorten), absolute.")
        .def(
            "fields",
            [](const Image &self, uint64_t handle) {
                meta::Types types(&self);
                std::vector<meta::Attr> attrs;
                std::string error;
                if (self.arena().cell(handle) == nullptr) throw ArenaError("BadHandle");
                if (meta::decode_payload(types, handle, &attrs, &error) != object::Status::Ok) throw DeltaError(error);
                std::vector<std::pair<std::string, std::string>> out;
                for (const auto &a : attrs) out.emplace_back(a.name, a.value);
                return out;
            },
            nb::arg("handle"), "The fields of an object as (name, text); zero and unset values are left out.")
        .def("type_name",
             [](const Image &self, uint64_t handle) -> std::optional<std::string> {
                 meta::Types types(&self);
                 const pymergetic_rxf_cell *c = self.arena().cell(handle);
                 if (c == nullptr) throw ArenaError("BadHandle");
                 const auto *t = types.get(c->type);
                 if (t == nullptr) return std::nullopt;
                 return t->name;
             })
        .def(
            "layout",
            [](const Image &self, uint64_t type) -> nb::typed<nb::dict, nb::str, nb::any> {
                meta::Types types(&self);
                if (types.get(type) == nullptr) throw ArenaError("not a Type object");
                const meta::Layout &layout = types.layout(type);
                nb::dict out;
                out["raw"] = layout.raw;
                out["fixed"] = layout.fixed;
                out["prefix"] = layout.prefix;
                out["error"] = layout.error;
                nb::list leaves;
                for (const auto &leaf : layout.leaves) {
                    nb::dict d;
                    d["name"] = leaf.name;
                    d["offset"] = leaf.offset;
                    d["size"] = leaf.size;
                    d["kind"] = static_cast<unsigned>(leaf.kind);
                    d["reference"] = leaf.kind == meta::LeafKind::Ref;
                    d["optional"] = leaf.optional;
                    d["derived"] = leaf.derived;
                    leaves.append(d);
                }
                out["leaves"] = leaves;
                return nb::typed<nb::dict, nb::str, nb::any>(out);
            },
            nb::arg("type"), "How the payload of a Type object is laid out: leaves with offset, size, kind, reference.")
        .def(
            "digest",
            [](const Image &self) {
                std::unique_ptr<Image> next;
                check(meta::compact(self, 0, &next));
                const std::vector<uint8_t> bytes = next->to_bytes();
                return hash::to_hex(hash::sha256(bytes.data(), bytes.size()));
            },
            "SHA-256 of the canonical bytes (the image after compaction).");
}

} // namespace pymergetic::rxf::core::bindings
