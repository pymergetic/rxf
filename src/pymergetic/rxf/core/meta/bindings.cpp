#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/doc/provenance.hpp>
#include <pymergetic/rxf/core/ground.h>
#include <pymergetic/rxf/core/hash/sha256.hpp>
#include <pymergetic/rxf/core/meta/types.hpp>

namespace pymergetic::rxf::core::bindings {

using object::Image;

namespace {

/* The per-import provenance level as the run declares it; below the floor is refused. */
doc::Tier tier_or_throw(const std::string &word) {
    doc::Tier tier;
    std::string error;
    if (!doc::tier_of(word, &tier, &error)) throw DeltaError(error);
    return tier;
}

/* A locator line as the format stores it: 32 bits. A Python int is unbounded;
 * one beyond the locator's width is refused here, at the boundary, never
 * narrowed into a different line. */
uint32_t line_or_throw(uint64_t line) {
    if (line > UINT32_MAX) throw DeltaError("a locator's line numbers fit in 32 bits");
    return static_cast<uint32_t>(line);
}

void thrown(object::Status status, const std::string &error) {
    if (status != object::Status::Ok) throw DeltaError(error.empty() ? arena::status_name(status) : error);
}

} // namespace

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

        // The hybrid module tree (docs/23 §4.6): the image IS the module tree. The trichotomy is
        // the one mp_import_stat returns, so the upy port's fill is one call here.
        .def(
            "resolve_module",
            [](const Image &self, const std::string &name) {
                std::string error;
                const meta::ModuleStat stat = meta::module_stat(self, name, &error);
                if (!error.empty()) throw TreeError(error);
                return std::string(meta::module_stat_word(stat));
            },
            nb::arg("name"),
            "The module tree's trichotomy for one name: \"package\", \"module\" or \"none\" — the "
            "answer mp_import_stat gives. A name that spells no one clear module is refused (TreeError).")
        .def(
            "module_children",
            [](const Image &self, const std::string &name) {
                std::string error;
                std::vector<std::string> out = meta::module_children(self, name, &error);
                if (!error.empty()) throw TreeError(error);
                return out;
            },
            nb::arg("name"),
            "The sub-module names of a package (what makes it one), in name order; empty for a leaf "
            "module and for none.")
        .def(
            "module_roots",
            [](const Image &) { return meta::module_roots(); },
            "The Quad: the four roots of the module tree (px, pm, pt, py) and nothing else.")

        .def(
            "doc_text",
            [](const Image &self, uint64_t handle) {
                meta::Types types(&self);
                std::string out, error;
                if (self.arena().cell(handle) == nullptr) throw ArenaError("BadHandle");
                if (meta::doc_text(types, handle, &out, &error) != object::Status::Ok) throw DeltaError(error);
                return out;
            },
            nb::arg("handle"), "The text of a Doc object, decompressed on demand (docs/22).")

        // core/doc/provenance.hpp (docs/22): provenance facts and the locator seam. A locator is the
        // citing Doc's text tail, key=value lines — RefSpec carries none (see docs/22 / docs/15).
        .def(
            "doc_add",
            [](Image &self, uint64_t parent, const std::string &name, const std::string &text, uint32_t kind,
               uint32_t status, uint32_t codec) {
                uint64_t made = 0;
                std::string error;
                thrown(doc::add(self, parent, name, kind, status, codec, text, &made, &error), error);
                return made;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("text"), nb::arg("kind") = 0, nb::arg("status") = 0,
            nb::arg("codec") = 0,
            "A Doc object below `parent` carrying `text` (DocKind/DocStatus/DocCodec words), made by the rules "
            "of the ground. Refused below the ground's floor: an image cannot hold a kind it does not know.")
        .def(
            "doc_place",
            [](Image &self, uint64_t source, uint64_t doc, uint32_t role) {
                std::string error;
                thrown(doc::place(self, source, doc, role, &error), error);
            },
            nb::arg("source"), nb::arg("doc"), nb::arg("role") = 0,
            "A RefKind.Doc placement of `doc` on `source` (DocRole word). Stored docs make zero runtime refs "
            "(the image check), so the placement is editorial.")
        .def(
            "lift_comment",
            [](Image &self, uint64_t target, const std::string &name, const std::string &text, uint32_t kind,
               uint32_t status, uint32_t role) {
                uint64_t made = 0;
                std::string error;
                thrown(doc::lift_comment(self, target, name, text, kind, status, role, &made, &error), error);
                return made;
            },
            nb::arg("target"), nb::arg("name"), nb::arg("text"), nb::arg("kind") = 0, nb::arg("status") = 0,
            nb::arg("role") = 0,
            "A comment/docstring lifted to a Narrates Doc object (Lz4 by default) and placed on `target` — the "
            "import lane's single call. Refused below the floor as any other doc creation.")
        .def(
            "doc_origin",
            [](Image &self, uint64_t parent, const std::string &name, const std::string &tier,
               const std::string &upstream, const std::string &repository, const std::string &commit,
               const std::string &license, const std::string &importer) {
                uint64_t made = 0;
                std::string error;
                doc::Origin origin{upstream, repository, commit, license, importer};
                thrown(doc::origin(self, parent, name, tier_or_throw(tier), origin, &made, &error), error);
                return made;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("tier"), nb::arg("upstream"), nb::arg("repository"),
            nb::arg("commit"), nb::arg("license") = "", nb::arg("importer") = "",
            "The origin record of an import (a Topic Doc): upstream, repository, commit, license, importer. "
            "Made at every provenance tier (docs/22); `tier` is the run's per-import tier.")
        .def(
            "doc_source",
            [](Image &self, uint64_t parent, const std::string &name, const std::string &tier,
               const std::string &source, std::optional<uint64_t> origin_handle) {
                uint64_t made = 0;
                std::string error;
                thrown(doc::source(self, parent, name, tier_or_throw(tier), source, origin_handle.value_or(0), &made,
                                   &error),
                       error);
                return made;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("tier"), nb::arg("source"), nb::arg("origin") = nb::none(),
            "The imported source itself as one stored payload (a Narrative Doc, Lz4), its origin in the tail "
            "first. The 'full' tier's payload — place() it on every object it explains: one payload, N "
            "placements.")
        .def(
            "doc_hash",
            [](Image &self, uint64_t parent, const std::string &name, const std::string &tier,
               const std::string &sha256) {
                uint64_t made = 0;
                std::string error;
                thrown(doc::hash(self, parent, name, tier_or_throw(tier), sha256, &made, &error), error);
                return made;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("tier"), nb::arg("sha256"),
            "The file's content SHA as one hash Document (a Topic Doc, Raw): exactly 64 lowercase hex digits "
            "or refused. The 'hash' tier's payload.")
        .def(
            "doc_cite",
            [](Image &self, uint64_t parent, const std::string &name, const std::string &tier,
               const std::vector<uint64_t> &placed_on, const std::vector<uint64_t> &provenance,
               const std::string &path, uint64_t first_line, uint64_t last_line, const std::string &sha256) {
                uint64_t made = 0;
                std::string error;
                doc::Locator locator{path, line_or_throw(first_line), line_or_throw(last_line), sha256};
                thrown(doc::cite(self, parent, name, tier_or_throw(tier), locator, placed_on, provenance, &made,
                                 &error),
                       error);
                return made;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("tier"), nb::arg("placed_on"), nb::arg("provenance"),
            nb::arg("path"), nb::arg("first_line"), nb::arg("last_line"), nb::arg("sha256") = "",
            "A citation (a Narrative Doc with a locator in its tail) placed on each object of `placed_on`, "
            "citing each fact of `provenance` (the import's origin record, the file's source Doc at full). "
            "First and last line are 1-based, or refused. Made at the 'cite' and 'full' tiers.")
        .def(
            "doc_locator",
            [](Image &self, uint64_t doc) {
                doc::Locator locator;
                std::string error;
                thrown(doc::locator_of(self, doc, &locator, &error), error);
                nb::typed<nb::dict, nb::str, nb::any> out;
                out["path"] = locator.path;
                out["first_line"] = locator.first_line;
                out["last_line"] = locator.last_line;
                out["sha256"] = locator.sha256;
                return out;
            },
            nb::arg("doc"),
            "The locator of a citation: {path, first_line, last_line, sha256} parsed out of the Doc's text tail "
            "(the seam, docs/22). Refused on a Doc without one.")
        .def(
            "doc_permalink",
            [](Image &self, uint64_t doc) {
                std::string error, link;
                thrown(doc::citation_permalink(self, doc, &link, &error), error);
                return link;
            },
            nb::arg("doc"),
            "A citation's permalink built from its locator: repo at commit, path, the line range.")
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
