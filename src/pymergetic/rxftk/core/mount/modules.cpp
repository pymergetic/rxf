#include <algorithm>

#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/ground.h>
#include <pymergetic/rxftk/core/mount/mount.hpp>

namespace pymergetic::rxf::core::mount {

using bindings::TreeError;

namespace {

bool is_module_cell(const Image &image, Handle h) {
    const pymergetic_rxf_cell *c = image.arena().cell(h);
    return c != nullptr && ((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) &
                            PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_MODULE;
}

} // namespace

std::vector<std::string> module_names(const Image &image) {
    std::vector<std::string> out;
    const uint64_t count = image.arena().header()->handle_count;
    for (Handle h = 0; h < count; h++)
        if (is_module_cell(image, h))
            out.push_back(image.path(h));
    std::sort(out.begin(), out.end());
    return out;
}

namespace detail {

/* The module object of one Module cell. Its exported names are its children (what the text
 * form writes under it), and every module also carries the ground (the root's non-module
 * children: Type, Field, ...) as its shared context — the ground is what a module needs to
 * exist (docs/01). Sub-modules and unwrapped names arrive lazily (PEP 562): the cell is
 * read when the attribute is asked for, not when the module is made. */
nb::object make_module(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                       Handle cell) {
    auto it = faces->modules.find(cell);
    if (it != faces->modules.end())
        return it->second;

    const std::string path = state->image->path(cell);
    nb::object mod =
        nb::steal<nb::object>(PyModule_New(path.empty() ? "<rxf root>" : path.c_str()));
    faces->modules.emplace(cell, mod); // before the callbacks: a module child can point back

    mod.attr("__doc__") =
        nb::str(("mounted from the module " + (path.empty() ? std::string("<root>") : path) +
                 " of one image (docs/16)")
                    .c_str());
    mod.attr("__rxf_mount__") = true;
    mod.attr("__image__") = state->image_py;
    /* A package: it has sub-modules (the same trichotomy the guest's import walk reads). */
    for (Handle child : state->image->children(cell)) {
        if (is_module_cell(*state->image, child)) {
            mod.attr("__path__") = nb::list();
            break;
        }
    }

    nb::dict ns = nb::cast<nb::dict>(mod.attr("__dict__"));
    /* No caching into the module's own dict here: the dict would hold this closure and the
     * closure the dict — a cycle invisible to Python's collector (nanobind closures are not
     * traversed). The register (weak below) already gives one object per handle. */
    nb::object missing = nb::cpp_function(
        [state, weak = std::weak_ptr<Faces>(faces), cell,
         path](const std::string &name) -> nb::object {
            std::shared_ptr<Faces> held = weak.lock();
            if (!held)
                held = std::make_shared<Faces>();
            const Handle child = state->image->child(cell, name);
            if (child != PYMERGETIC_RXF_HANDLE_NONE && state->image->arena().cell(child) != nullptr)
                return wrap(state, held, child);
            for (Handle g : state->ground) // the ground is what a module needs to exist
                if (state->image->arena().name(g) == name)
                    return wrap(state, held, g);
            PyErr_Format(PyExc_AttributeError, "module '%s' has no attribute '%s'", path.c_str(),
                         name.c_str());
            throw nb::python_error();
        },
        nb::arg("name"));
    ns["__getattr__"] = missing;
    ns["__dir__"] = nb::cpp_function([state, cell]() {
        std::vector<std::string> out;
        for (Handle child : state->image->children(cell))
            out.emplace_back(state->image->arena().name(child));
        for (Handle g : state->ground)
            out.emplace_back(state->image->arena().name(g));
        std::sort(out.begin(), out.end());
        return out;
    });
    return mod;
}

} // namespace detail

nb::object Synthesizer::module(const std::string &name) {
    std::string error;
    const meta::ModuleStat stat = meta::module_stat(*state_->image, name, &error);
    if (!error.empty())
        throw TreeError(error);
    if (stat == meta::ModuleStat::None)
        throw TreeError("image has no module '" + name + "'");
    return detail::wrap(state_, faces_, state_->image->resolve(name));
}

} // namespace pymergetic::rxf::core::mount
