#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/mount/mount.hpp>

namespace pymergetic::rxf::core::bindings {

using object::Image;

/* The mount face (docs/16): the object world of one image as Python. `Image.mount_names`
 * answers the truth an import hook walks; `mount.Synthesizer` makes the faces themselves
 * (classes from Type objects, modules from Module cells, instances for the rest). */
void bind_mount(nb::module_ &m, nb::class_<Image> &image) {
    nb::class_<mount::Synthesizer>(m, "Synthesizer", nb::is_weak_referenceable())
        .def(nb::init<nb::object>(), nb::arg("image"),
             "The mounted Python face of one image, synthesized from its self-description.")
        .def("names", &mount::Synthesizer::names,
             "Every module name of the image, in import spelling.")
        .def("module", &mount::Synthesizer::module, nb::arg("name"),
             "The module object of a module name (its exported names, lazily mounted).")
        .def("object", &mount::Synthesizer::object, nb::arg("handle"),
             "The mounted face of one object: a module for a Module cell, an instance otherwise.")
        .def("class_of", &mount::Synthesizer::class_of, nb::arg("type"),
             "The class synthesized for a Type object (one per Type, cached).")
        .def("image", &mount::Synthesizer::image, "The image every face reads.");

    image.def(
        "mount_names", [](const Image &self) { return mount::module_names(self); },
        "Every module name of the image (the path of every Module cell), sorted: the truth "
        "the guest's import walk and the host's mount both answer from.");
}

} // namespace pymergetic::rxf::core::bindings
