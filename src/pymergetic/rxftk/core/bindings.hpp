#ifndef PYMERGETIC_RXF_CORE_BINDINGS_HPP
#define PYMERGETIC_RXF_CORE_BINDINGS_HPP

/*
 * The Python face of the core. Every C++ module that has one keeps it next to
 * its sources (`<module>/bindings.cpp`) and fills one submodule of
 * `pymergetic.rxftk.core` with the same name; module.cpp creates the submodules
 * and calls these in dependency order. `Image` is one class: object makes it,
 * meta and delta add the methods that belong to their modules.
 */

#include <stdexcept>

#include <nanobind/nanobind.h>

#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::bindings {

namespace nb = nanobind;

/* The exceptions of the core; each is registered in the submodule of its module. */
struct ArenaError : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct DeltaError : std::runtime_error {
    using std::runtime_error::runtime_error;
};
struct TreeError : std::runtime_error {
    using std::runtime_error::runtime_error;
};

inline void check(object::Status status) {
    if (status != object::Status::Ok) throw ArenaError(arena::status_name(status));
}

void bind_kinds(nb::module_ &m);
void bind_arena(nb::module_ &m);
void bind_hash(nb::module_ &m);
nb::class_<object::Image> bind_object(nb::module_ &m);
void bind_meta(nb::module_ &m, nb::class_<object::Image> &image);
void bind_delta(nb::module_ &m, nb::class_<object::Image> &image);
void bind_tree(nb::module_ &m);
void bind_live(nb::module_ &m);

} // namespace pymergetic::rxf::core::bindings

#endif
