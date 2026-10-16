#ifndef PYMERGETIC_RXF_CORE_MOUNT_MOUNT_HPP
#define PYMERGETIC_RXF_CORE_MOUNT_MOUNT_HPP

/*
 * The mount: the object world of one image as a Python face, synthesized from the
 * self-description and nothing else. Every Type object becomes a Python class whose
 * properties read the payload at the field's offset; every Module cell becomes a module
 * object with its exported names; every other object becomes an instance of its class,
 * carrying `id` (its handle) and, through the class, the image it reads.
 *
 * The synthesizer is read-only (the editing verbs are a delta, docs/27) and belongs to one
 * image, which every face it makes keeps alive. The host import hook (rxftk/mount)
 * and the guest's module walk read the same faces: one object world, one mapping, and no
 * second copy. What the text form writes and what the properties read is one layout
 * (meta/types.cpp), so a mount cannot drift from the image.
 *
 * Two shared states, deliberately: `State` is what every face reads (the image, its
 * self-description, the ground) and is held by the property closures; `Faces` is the
 * register of faces made so far (one class per Type, one object per handle) and is owned by
 * the synthesizer alone — the closures that consult it hold a weak reference. That way the
 * faces and their closures form no ownership cycle: dropping the synthesizer drops the
 * register, and whatever the caller kept (a class, an instance) stays readable.
 */

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include <nanobind/nanobind.h>

#include <pymergetic/rxftk/core/meta/types.hpp>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::mount {

namespace nb = nanobind;

using meta::Types;
using object::Image;
using object::Status;
using Handle = pymergetic_rxf_handle;

/* The module names of the image: the path of every Module cell (NodeKind.Module), which is
 * exactly the set the guest's import walk answers "package" or "module" for (docs/23 §4.6),
 * in import spelling, sorted. */
std::vector<std::string> module_names(const Image &image);

namespace detail {

/* What every face reads; held by the property closures for as long as any face lives. */
struct State {
    nb::object image_py;
    Image *image = nullptr;
    Types types;
    std::vector<Handle> ground; // the root's non-module children: Type, Field, ... (docs/01)
    explicit State(nb::object py);
};

/* The register of faces made so far: one class per Type, one object per handle, one module
 * per Module cell. Owned by the synthesizer; consulted through a weak reference. */
struct Faces {
    std::unordered_map<Handle, nb::object> classes;
    std::unordered_map<Handle, nb::object> modules;
    std::unordered_map<Handle, nb::object> objects;
};

/* The mounted face of one object: a module for a Module cell, an instance of the class of
 * its Type otherwise. */
nb::object wrap(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                Handle handle);
/* The class synthesized for a Type object (one per Type). */
nb::object make_class(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                      Handle type);
/* The module object of a Module cell: its exported names (its children) and the ground. */
nb::object make_module(const std::shared_ptr<State> &state, const std::shared_ptr<Faces> &faces,
                       Handle cell);

} // namespace detail

class Synthesizer {
  public:
    /* `image` is the Python Image object the synthesized face keeps alive. */
    explicit Synthesizer(nb::object image);

    /* Every module name of the image (module_names), the truth an import hook walks. */
    std::vector<std::string> names() {
        return module_names(*state_->image);
    }
    /* The module object of a module name; TreeError where the name is no module. */
    nb::object module(const std::string &name);
    /* The mounted face of one object. */
    nb::object object(Handle handle) {
        return detail::wrap(state_, faces_, handle);
    }
    /* The class synthesized for a Type object. */
    nb::object class_of(Handle type) {
        return detail::make_class(state_, faces_, type);
    }
    /* The image every face reads. */
    nb::object image() const {
        return state_->image_py;
    }

  private:
    std::shared_ptr<detail::State> state_;
    std::shared_ptr<detail::Faces> faces_;
};

} // namespace pymergetic::rxf::core::mount

#endif
