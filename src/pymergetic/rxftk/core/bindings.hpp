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

/* The value of a field as the text form spells it (docs/27): one spelling, written down as
 * the delta and read back by the codec of the text form. A bool is "true"/"false", a number
 * its decimal, a float its repr, text as it stands (an enumerator path), bytes as hex. */
inline std::string value_text(const nb::object &value) {
    if (nb::isinstance<nb::bool_>(value)) return nb::cast<bool>(value) ? "true" : "false";
    if (nb::isinstance<nb::str>(value)) return nb::cast<std::string>(value);
    if (nb::isinstance<nb::bytes>(value)) {
        const nb::bytes bytes = nb::cast<nb::bytes>(value);
        static const char digits[] = "0123456789abcdef";
        std::string out;
        out.reserve(bytes.size() * 2);
        for (size_t i = 0; i < bytes.size(); i++) {
            const uint8_t byte = static_cast<uint8_t>(bytes.c_str()[i]);
            out.push_back(digits[byte >> 4]);
            out.push_back(digits[byte & 15]);
        }
        return out;
    }
    if (nb::isinstance<nb::int_>(value) || nb::isinstance<nb::float_>(value))
        return nb::cast<std::string>(nb::str(value));
    throw DeltaError("a value is a number, a bool, a float, text or bytes");
}

/* A target of the edit set as the Python face names it: paths name things, handles identify
 * them (docs/27) — the verbs take either, and a mounted object is its `id`. */
inline uint64_t target_handle(object::Image &image, const nb::object &target) {
    if (nb::isinstance<nb::bool_>(target)) throw DeltaError("a target is a path or a handle");
    if (nb::isinstance<nb::int_>(target)) return nb::cast<uint64_t>(target);
    if (nb::isinstance<nb::str>(target)) {
        const std::string path = nb::cast<std::string>(target);
        const uint64_t handle = image.resolve(path);
        if (handle == PYMERGETIC_RXF_HANDLE_NONE) throw DeltaError("no such object: " + path);
        return handle;
    }
    if (nb::hasattr(target, "id")) return nb::cast<uint64_t>(nb::getattr(target, "id"));
    throw DeltaError("a target is a path or a handle");
}

void bind_kinds(nb::module_ &m);
void bind_arena(nb::module_ &m);
void bind_hash(nb::module_ &m);
nb::class_<object::Image> bind_object(nb::module_ &m);
void bind_meta(nb::module_ &m, nb::class_<object::Image> &image);
void bind_delta(nb::module_ &m, nb::class_<object::Image> &image);
void bind_tree(nb::module_ &m);
void bind_live(nb::module_ &m);
void bind_mount(nb::module_ &m, nb::class_<object::Image> &image);

} // namespace pymergetic::rxf::core::bindings

#endif
