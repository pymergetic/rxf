/*
 * pymergetic.rxftk.core: the compiled core. The extension is the __init__ of the
 * package, and its submodules mirror the C++ modules next to it:
 *
 *   core.kinds   the words of the format as enums (NodeKind, RefKind, Form, ...)
 *   core.arena   ground layout and its constants
 *   core.hash    SHA-256
 *   core.object  Image
 *   core.meta    self-description: check, fields, layouts, compaction, digest (methods of Image)
 *   core.delta   deltas: replay and canonical export (methods of Image)
 *   core.tree    an image as one delta per module
 *   core.live    the live layer of a running image (host build of the native leaf, for tests)
 */

#include <string>

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <pymergetic/rxftk/core/bindings.hpp>

namespace bindings = pymergetic::rxf::core::bindings;
namespace nb = nanobind;

namespace {

/* A submodule that `import pymergetic.rxftk.core.<name>` finds again: without the entry in
 * sys.modules the import system would look for it on disk, where only its sources and
 * stub are, and replace the attribute with an empty namespace package. */
nb::module_ submodule(nb::module_ &parent, const char *name, const char *doc) {
    nb::module_ sub = parent.def_submodule(name, doc);
    const std::string full = nb::cast<std::string>(parent.attr("__name__")) + "." + name;
    nb::module_::import_("sys").attr("modules")[full.c_str()] = sub;
    return sub;
}

} // namespace

NB_MODULE(core, m) {
    nb::module_ kinds = submodule(m, "kinds", "The words of the format: kinds, forms, flags.");
    nb::module_ arena = submodule(m, "arena", "The ground layout.");
    nb::module_ hash = submodule(m, "hash", "SHA-256.");
    nb::module_ object = submodule(m, "object", "The host image: paths, names, compaction.");
    nb::module_ meta = submodule(m, "meta", "Self-description: Type and Field objects, link check, digest.");
    nb::module_ delta = submodule(m, "delta", "Deltas: replay by path and canonical export.");
    nb::module_ tree = submodule(m, "tree", "An image as one delta per module.");
    nb::module_ live = submodule(m, "live", "The live layer: frames, reads, staged deltas, dumps.");

    bindings::bind_kinds(kinds);
    bindings::bind_arena(arena);
    bindings::bind_hash(hash);
    nb::class_<pymergetic::rxf::core::object::Image> image = bindings::bind_object(object);
    bindings::bind_meta(meta, image);
    bindings::bind_delta(delta, image);
    bindings::bind_tree(tree);
    bindings::bind_live(live);
}
