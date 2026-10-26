#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/arena/object_arena.hpp>
#include <pymergetic/rxftk/core/object/image.hpp>

#include <memory>

#include <nanobind/stl/unique_ptr.h>

namespace pymergetic::rxf::core::bindings {

namespace {

/* The v7 one-arena allocator (docs/32 3) over host memory. `create` reserves the
 * region and commits it on demand through the growth hook; `fixed` commits it whole
 * and has no hook, the seat case PYMERGETIC_RXF_FIXED_REGION refuses in. */
struct HostObjectArena {
    std::unique_ptr<object::HostMemory> memory;
    arena::ObjectArena arena;
};

} // namespace

/* The ground layout as the compiled core sees it. ground.h is the only place that defines the
 * constants. */
void bind_arena(nb::module_ &m) {
    m.attr("ARENA_VERSION") = PYMERGETIC_RXF_ARENA_VERSION;
    m.attr("CELL_ALIGN") = PYMERGETIC_RXF_CELL_ALIGN;
    m.attr("INDEX_CHUNK_ENTRIES") = PYMERGETIC_RXF_INDEX_CHUNK_ENTRIES;
    m.attr("HANDLE_NONE") = PYMERGETIC_RXF_HANDLE_NONE;
    m.attr("HANDLE_ROOT") = PYMERGETIC_RXF_HANDLE_ROOT;
    m.attr("HANDLE_TYPE") = PYMERGETIC_RXF_HANDLE_TYPE;
    m.attr("HANDLE_FIELD") = PYMERGETIC_RXF_HANDLE_FIELD;
    m.attr("ARENA_HEADER_SIZE") = sizeof(pymergetic_rxf_arena_header);
    m.attr("CELL_SIZE") = sizeof(pymergetic_rxf_cell);
    m.attr("REF_SIZE") = sizeof(pymergetic_rxf_ref);
    m.attr("TYPE_SIZE") = sizeof(pymergetic_rxf_type);
    m.attr("FIELD_SIZE") = sizeof(pymergetic_rxf_field);
    m.attr("KIND_BLOCK") = PYMERGETIC_RXF_KIND_BLOCK;
    m.attr("KIND_DEAD") = PYMERGETIC_RXF_KIND_DEAD;
    m.attr("BLOCK_INDEX") = PYMERGETIC_RXF_BLOCK_INDEX;
    m.attr("BLOCK_NAMES") = PYMERGETIC_RXF_BLOCK_NAMES;
    m.attr("NAME_BLOCK_PAYLOAD") = PYMERGETIC_RXF_NAME_BLOCK_PAYLOAD;
    /* The one-arena object model (docs/32). The OBJ_* values are members of an unnamed
     * enumeration in ground.h; nanobind reports them as plain integers. */
    m.attr("SUPERBLOCK_SIZE") = sizeof(pymergetic_rxf_superblock);
    m.attr("SUPERBLOCK_VERSION") = PYMERGETIC_RXF_SUPER_VERSION;
    m.attr("OBJECT_HEADER_SIZE") = sizeof(pymergetic_rxf_object);
    m.attr("OBJECT_ALIGN") = PYMERGETIC_RXF_OBJECT_ALIGN;
    m.attr("HANDLE32_NONE") = PYMERGETIC_RXF_HANDLE32_NONE;
    m.attr("HANDLE32_INDEX_BITS") = PYMERGETIC_RXF_HANDLE32_INDEX_BITS;
    m.attr("HANDLE32_GEN_BITS") = PYMERGETIC_RXF_HANDLE32_GEN_BITS;
    m.attr("OBJ_MODULE") = static_cast<int>(PYMERGETIC_RXF_OBJ_MODULE);
    m.attr("OBJ_FUNC") = static_cast<int>(PYMERGETIC_RXF_OBJ_FUNC);
    m.attr("OBJ_HASH") = static_cast<int>(PYMERGETIC_RXF_OBJ_HASH);
    m.attr("OBJ_SPEC") = static_cast<int>(PYMERGETIC_RXF_OBJ_SPEC);
    m.attr("OBJ_SYM") = static_cast<int>(PYMERGETIC_RXF_OBJ_SYM);
    m.attr("OBJ_BLOB") = static_cast<int>(PYMERGETIC_RXF_OBJ_BLOB);
    m.attr("OBJ_FRAME") = static_cast<int>(PYMERGETIC_RXF_OBJ_FRAME);
    m.attr("OBJ_FREE") = PYMERGETIC_RXF_OBJ_FREE;
    m.attr("OFLAG_RO") = PYMERGETIC_RXF_OFLAG_RO;
    m.attr("OFLAG_ROOT") = PYMERGETIC_RXF_OFLAG_ROOT;
    m.attr("OFLAG_DIRTY") = PYMERGETIC_RXF_OFLAG_DIRTY;
    m.attr("OFLAG_JOURNALED") = PYMERGETIC_RXF_OFLAG_JOURNALED;
    m.attr("FREE_PREV") = PYMERGETIC_RXF_FREE_PREV;
    m.attr("FREE_NEXT") = PYMERGETIC_RXF_FREE_NEXT;
    m.attr("FREE_LINKS") = PYMERGETIC_RXF_FREE_LINKS;
    m.attr("FREE_MIN_SPAN") = (uint64_t)PYMERGETIC_RXF_FREE_MIN_SPAN;

    nb::class_<HostObjectArena> object_arena(m, "ObjectArena");
    object_arena
        .def_static(
            "create",
            [](uint64_t limit, bool fixed) {
                auto host = object::HostMemory::reserve(limit);
                if (host == nullptr)
                    throw ArenaError("OutOfMemory");
                arena::Memory memory = host->memory();
                if (fixed) {
                    if (!host->commit(limit))
                        throw ArenaError("OutOfMemory");
                    memory = host->memory();
                    memory.commit = nullptr;
                }
                auto held = std::make_unique<HostObjectArena>();
                held->memory = std::move(host);
                check(held->arena.init(memory, 0, 0));
                return held;
            },
            nb::arg("limit"), nb::arg("fixed") = false)
        .def("s_allocate",
             [](HostObjectArena &self, uint64_t room) {
                 uint64_t offset = 0;
                 check(self.arena.s_allocate(room, &offset));
                 return offset;
             },
             nb::arg("room"))
        .def("s_release",
             [](HostObjectArena &self, uint64_t offset) {
                 check(self.arena.s_release(offset));
             },
             nb::arg("offset"))
        .def("s_ensure",
             [](HostObjectArena &self, uint64_t at, uint64_t length) {
                 check(self.arena.s_ensure(at, length));
             },
             nb::arg("at"), nb::arg("length"))
        .def("verify", [](HostObjectArena &self) { check(self.arena.verify()); })
        .def("record",
             [](const HostObjectArena &self, uint64_t offset) {
                 const pymergetic_rxf_object *record =
                     reinterpret_cast<const pymergetic_rxf_object *>(
                         self.arena.memory().base + offset);
                 return nb::make_tuple(record->kind, record->gen, record->flags, record->name,
                                       record->parent, record->link, record->refs,
                                       record->payload);
             },
             nb::arg("offset"))
        .def("links",
             [](const HostObjectArena &self, uint64_t offset) {
                 const uint64_t *links = reinterpret_cast<const uint64_t *>(
                     self.arena.memory().base + offset + sizeof(pymergetic_rxf_object));
                 return nb::make_tuple(links[PYMERGETIC_RXF_FREE_PREV / 8],
                                       links[PYMERGETIC_RXF_FREE_NEXT / 8]);
             },
             nb::arg("offset"))
        .def_prop_ro("committed",
                     [](const HostObjectArena &self) { return self.arena.committed(); })
        .def_prop_ro("limit", [](const HostObjectArena &self) { return self.arena.limit(); })
        .def_prop_ro("need", [](const HostObjectArena &self) { return self.arena.need(); });
    // The constructor registers the exception type with nanobind and the module.
    [[maybe_unused]] nb::exception<ArenaError> error(m, "ArenaError", PyExc_RuntimeError);
}

} // namespace pymergetic::rxf::core::bindings
