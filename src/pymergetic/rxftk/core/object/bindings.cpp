#include <nanobind/stl/optional.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/tuple.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <unordered_map>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::bindings {

namespace {

using object::Image;

// target, role (none: no role), index, kind, binding
using RefTuple = std::tuple<uint64_t, std::optional<uint64_t>, uint32_t, uint16_t, uint16_t>;

std::vector<pymergetic_rxf_ref> to_refs(const std::vector<RefTuple> &items) {
    std::vector<pymergetic_rxf_ref> refs;
    refs.reserve(items.size());
    for (const auto &item : items) {
        pymergetic_rxf_ref r{};
        r.target = std::get<0>(item);
        const std::optional<uint64_t> &role = std::get<1>(item);
        r.role = role ? *role : PYMERGETIC_RXF_HANDLE_NONE;
        r.index = std::get<2>(item);
        r.kind = std::get<3>(item);
        r.binding = std::get<4>(item);
        refs.push_back(r);
    }
    return refs;
}

using Described = nb::typed<nb::dict, nb::str, nb::any>;

Described describe(const Image &image, uint64_t handle) {
    const pymergetic_rxf_cell *c = image.arena().cell(handle);
    if (c == nullptr)
        throw ArenaError("BadHandle");
    nb::dict d;
    d["handle"] = handle;
    d["type"] = c->type;
    d["parent"] = c->parent;
    d["name"] = std::string(image.arena().name(handle));
    d["path"] = image.path(handle);
    d["kind"] = (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
    d["state"] = (c->flags >> PYMERGETIC_RXF_FLAG_STATE_SHIFT) & PYMERGETIC_RXF_FLAG_STATE_MASK;
    d["owner_kind"] =
        (c->flags >> PYMERGETIC_RXF_FLAG_OWNER_SHIFT) & PYMERGETIC_RXF_FLAG_OWNER_MASK;
    d["generation"] = c->generation;
    d["index"] = c->index;
    d["payload"] =
        nb::bytes(reinterpret_cast<const char *>(image.arena().payload(handle)), c->payload_size);
    std::vector<RefTuple> refs;
    const pymergetic_rxf_ref *r = image.arena().refs(handle);
    for (uint32_t i = 0; i < c->ref_count; i++) {
        std::optional<uint64_t> role;
        if (r[i].role != PYMERGETIC_RXF_HANDLE_NONE)
            role = r[i].role;
        refs.emplace_back(r[i].target, role, r[i].index, r[i].kind, r[i].binding);
    }
    d["refs"] = refs;
    return Described(d);
}

/* The arena as the inspector draws it: the header decoded, and every cell of the stream with its
 * arena offset and decoded header. The offsets and sizes this decoding reads are the ones
 * ground.h pins; the assertions below restate what is relied on, so a layout change stops the
 * build here too (the walk advances by the pinned cell size formula, nothing else). */
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, body_size, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, index_off, 48, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, cells_off, 56, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, names_off, 64, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_arena_header, handle_count, 88, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, payload_size, 16, 8);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, flags, 40, 4);
PYMERGETIC_RXF_PIN(pymergetic_rxf_cell, ref_count, 44, 4);

nb::dict describe_header(const pymergetic_rxf_arena_header *h) {
    nb::dict d;
    d["magic"] = std::string(reinterpret_cast<const char *>(h->magic), sizeof(h->magic));
    d["version"] = h->version;
    d["header_size"] = h->header_size;
    d["flags"] = h->flags;
    d["body_size"] = h->body_size;
    d["committed_size"] = h->committed_size;
    d["limit"] = h->limit;
    d["frontier"] = h->frontier;
    d["index_off"] = h->index_off;
    d["cells_off"] = h->cells_off;
    d["names_off"] = h->names_off;
    d["names_size"] = h->names_size;
    d["index_chunks"] = h->index_chunks;
    d["handle_count"] = h->handle_count;
    d["free_head"] = h->free_head;
    d["entry"] = h->entry;
    d["root"] = h->root;
    d["cell_align"] = h->cell_align;
    d["page_size"] = h->page_size;
    return d;
}

std::vector<nb::dict> describe_cells(const Image &image) {
    const arena::Arena &a = image.arena();
    const pymergetic_rxf_arena_header *h = a.header();
    /* The live handle of each cell: the walk sees bytes, the index says whose they are. */
    std::unordered_map<uint64_t, uint64_t> owners;
    for (uint64_t handle = 0; handle < h->handle_count; handle++) {
        const pymergetic_rxf_cell *c = a.cell(handle);
        if (c != nullptr)
            owners.emplace(
                static_cast<uint64_t>(reinterpret_cast<const uint8_t *>(c) - a.memory().base),
                handle);
    }
    std::vector<nb::dict> out;
    uint64_t at = a.first_offset();
    const uint64_t end = a.end_offset();
    while (at < end) {
        if (end - at < sizeof(pymergetic_rxf_cell))
            throw ArenaError("BadImage");
        const pymergetic_rxf_cell *c = a.cell_at(at);
        if (c->reserved != 0 || c->payload_size > arena::kMaxPayload ||
            c->ref_count > arena::kMaxRefs)
            throw ArenaError("BadImage");
        const uint64_t size = pymergetic_rxf_cell_size(c);
        if (size < sizeof(pymergetic_rxf_cell) || size > end - at)
            throw ArenaError("BadImage");
        const uint32_t kind =
            (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
        std::optional<uint64_t> handle;
        const auto found = owners.find(at);
        if (found != owners.end())
            handle = found->second;
        nb::dict d;
        d["offset"] = at;
        d["size"] = size;
        d["kind"] = kind;
        d["block_kind"] = kind == PYMERGETIC_RXF_KIND_BLOCK ? static_cast<uint32_t>(c->type) : 0U;
        d["handle"] = handle;
        d["type"] = c->type;
        d["parent"] = c->parent;
        d["payload_size"] = c->payload_size;
        d["ref_count"] = c->ref_count;
        d["flags"] = c->flags;
        d["generation"] = c->generation;
        d["name_off"] = c->name;
        d["forward"] = c->forward;
        d["index"] = c->index;
        out.push_back(std::move(d));
        at += size;
    }
    return out;
}

} // namespace

nb::class_<object::Image> bind_object(nb::module_ &m) {
    m.attr("DEFAULT_LIMIT") = object::kDefaultLimit;
    m.def(
        "image_state", [](uint32_t mobility) { return object::image_state(mobility); },
        nb::arg("mobility") = 0,
        "The state word of an object that comes from an image (IMAGE, RETAIN, CLEAN, the given "
        "mobility).");

    nb::class_<Image> image(m, "Image");
    image
        .def_static(
            "arena",
            [](uint64_t limit, uint64_t index_capacity) {
                std::unique_ptr<Image> created;
                check(Image::create(limit, index_capacity, &created));
                return created;
            },
            nb::arg("limit") = object::kDefaultLimit, nb::arg("index_capacity") = 0,
            "A fresh, empty arena: an image without the ground (the constructor of the arena "
            "layer, Image::create in C++; `create` on an image is the edit verb of docs/27).")
        .def_static(
            "from_bytes",
            [](nb::bytes data, uint64_t limit) {
                std::unique_ptr<Image> loaded;
                check(Image::load(reinterpret_cast<const uint8_t *>(data.c_str()), data.size(),
                                  limit, &loaded));
                return loaded;
            },
            nb::arg("data"), nb::arg("limit") = object::kDefaultLimit)
        .def("bootstrap", [](Image &self) { check(self.bootstrap()); })
        .def(
            "add",
            [](Image &self, std::optional<uint64_t> parent, const std::string &name, uint32_t kind,
               uint64_t type, nb::bytes payload, const std::vector<RefTuple> &refs, uint32_t state,
               uint32_t owner_kind, uint64_t generation, uint32_t index) {
                object::ObjectSpec spec;
                spec.parent = parent ? *parent : PYMERGETIC_RXF_HANDLE_NONE;
                spec.name = name;
                spec.kind = kind;
                spec.type = type;
                spec.payload.assign(reinterpret_cast<const uint8_t *>(payload.c_str()),
                                    reinterpret_cast<const uint8_t *>(payload.c_str()) +
                                        payload.size());
                spec.refs = to_refs(refs);
                spec.state = state;
                spec.owner_kind = owner_kind;
                spec.generation = generation;
                spec.index = index;
                pymergetic_rxf_handle handle = 0;
                check(self.add(spec, &handle));
                return handle;
            },
            nb::arg("parent"), nb::arg("name"), nb::arg("kind") = 0,
            nb::arg("type") = PYMERGETIC_RXF_HANDLE_NONE, nb::arg("payload") = nb::bytes("", 0),
            nb::arg("refs") = std::vector<RefTuple>{}, nb::arg("state") = 0,
            nb::arg("owner_kind") = static_cast<uint32_t>(PYMERGETIC_RXF_OWNER_SHARED),
            nb::arg("generation") = 0, nb::arg("index") = 0)
        .def("remove", [](Image &self, uint64_t handle) { check(self.remove(handle)); })
        .def("set_payload",
             [](Image &self, uint64_t handle, nb::bytes data) {
                 check(self.set_payload(handle, reinterpret_cast<const uint8_t *>(data.c_str()),
                                        data.size()));
             })
        .def("set_refs",
             [](Image &self, uint64_t handle, const std::vector<RefTuple> &refs) {
                 const std::vector<pymergetic_rxf_ref> list = to_refs(refs);
                 check(self.set_refs(handle, list.data(), static_cast<uint32_t>(list.size())));
             })
        .def("set_parent", [](Image &self, uint64_t handle,
                              uint64_t parent) { check(self.set_parent(handle, parent)); })
        .def("set_type", [](Image &self, uint64_t handle,
                            uint64_t type) { check(self.set_type(handle, type)); })
        .def("set_state", [](Image &self, uint64_t handle,
                             uint32_t state) { check(self.set_state(handle, state)); })
        .def("get", [](const Image &self, uint64_t handle) { return describe(self, handle); })
        .def("live", [](const Image &self, uint64_t handle) { return self.arena().live(handle); })
        .def(
            "arena_header",
            [](const Image &self) { return describe_header(self.arena().header()); },
            "The arena header decoded: sizes, region offsets, and counts.")
        .def(
            "arena_cells", [](const Image &self) { return describe_cells(self); },
            "Every cell of the stream in order: arena offset, size, decoded header, and the live "
            "handle it holds (none for a block or dead cell).")
        .def("resolve",
             [](const Image &self, const std::string &path) -> std::optional<uint64_t> {
                 const pymergetic_rxf_handle h = self.resolve(path);
                 if (h == PYMERGETIC_RXF_HANDLE_NONE)
                     return std::nullopt;
                 return h;
             })
        .def(
            "handle",
            [](const Image &self, const std::string &path) -> uint64_t {
                const pymergetic_rxf_handle h = self.resolve(path);
                if (h == PYMERGETIC_RXF_HANDLE_NONE)
                    throw nb::key_error(("no object at path: " + path).c_str());
                return h;
            },
            "The handle at a path; KeyError when there is none (resolve returns None).")
        .def("path", [](const Image &self, uint64_t handle) { return self.path(handle); })
        .def("children", [](const Image &self, uint64_t handle) { return self.children(handle); })
        .def("verify", [](const Image &self) { check(self.verify()); })
        .def(
            "begin", [](Image &self, uint64_t log_limit) { check(self.begin(log_limit)); },
            nb::arg("log_limit") = 0,
            "Starts a transaction: what follows is logged until commit() or rollback(). "
            "`log_limit` bounds the undo log (0: the limit of the image).")
        .def(
            "commit", [](Image &self) { check(self.commit()); },
            "Ends the transaction and keeps the changes.")
        .def(
            "rollback", [](Image &self) { check(self.rollback()); },
            "Ends the transaction; the image is as it was at begin(), byte for byte.")
        .def("to_bytes",
             [](const Image &self) {
                 const std::vector<uint8_t> bytes = self.to_bytes();
                 return nb::bytes(reinterpret_cast<const char *>(bytes.data()), bytes.size());
             })
        .def_prop_ro("handle_count",
                     [](const Image &self) { return self.arena().header()->handle_count; })
        .def_prop_ro("body_size",
                     [](const Image &self) { return self.arena().header()->body_size; })
        .def_prop_ro("frontier", [](const Image &self) { return self.arena().header()->frontier; })
        .def_prop_ro("index_chunks",
                     [](const Image &self) { return self.arena().header()->index_chunks; })
        .def_prop_ro("free_head",
                     [](const Image &self) { return self.arena().header()->free_head; })
        .def_prop_ro("limit", [](const Image &self) { return self.limit(); })
        .def_prop_ro(
            "entry_path",
            [](const Image &self) {
                const uint64_t h = self.arena().header()->entry;
                return h == PYMERGETIC_RXF_HANDLE_NONE ? std::string() : self.path(h);
            },
            "The path of the entry Function, empty when there is none.")
        .def_prop_rw(
            "entry",
            [](const Image &self) -> std::optional<uint64_t> {
                const uint64_t h = self.arena().header()->entry;
                if (h == PYMERGETIC_RXF_HANDLE_NONE)
                    return std::nullopt;
                return h;
            },
            [](Image &self, std::optional<uint64_t> handle) {
                if (handle && self.arena().cell(*handle) == nullptr)
                    throw ArenaError("BadHandle");
                self.arena().mutable_header()->entry =
                    handle ? *handle : PYMERGETIC_RXF_HANDLE_NONE;
            },
            "The entry Function of the image.");
    return image;
}

} // namespace pymergetic::rxf::core::bindings
