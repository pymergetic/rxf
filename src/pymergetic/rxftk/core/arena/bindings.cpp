#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::bindings {

/* The ground layout as the compiled core sees it. ground.h is the only place that defines the constants. */
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
    // The constructor registers the exception type with nanobind and the module.
    [[maybe_unused]] nb::exception<ArenaError> error(m, "ArenaError", PyExc_RuntimeError);
}

} // namespace pymergetic::rxf::core::bindings
