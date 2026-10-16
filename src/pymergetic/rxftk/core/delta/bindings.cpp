#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/delta/delta.hpp>
#include <pymergetic/rxftk/core/delta/edit.hpp>
#include <pymergetic/rxftk/core/hash/sha256.hpp>
#include <pymergetic/rxftk/core/xml/xml.hpp>

namespace pymergetic::rxf::core::bindings {

namespace {

using object::Image;
using object::Status;

/* Parses each text (error messages carry its name) and applies all operations as one delta. */
void apply_texts(Image &image, const std::vector<std::pair<std::string, std::string>> &files, bool links) {
    delta::Delta all;
    for (const auto &file : files) {
        delta::Delta one;
        std::string error;
        if (xml::parse(file.second, &one, &error) != Status::Ok) throw DeltaError("cannot parse " + file.first + ": " + error);
        for (auto &op : one.ops) all.ops.push_back(std::move(op));
    }
    delta::Failure failure;
    if (delta::apply(image, all, &failure, links) != Status::Ok) throw DeltaError(delta::describe(failure));
}

std::string canonical_text(const Image &image, const std::string *module) {
    delta::Delta delta;
    std::string error, text;
    if (delta::export_image(image, &delta, &error, module) != Status::Ok) throw DeltaError("cannot export: " + error);
    if (xml::write(delta, &text, &error) != Status::Ok) throw DeltaError("cannot write: " + error);
    return text;
}

} // namespace

/* Deltas: building an image from them and writing it as one. */
void bind_delta(nb::module_ &m, nb::class_<Image> &image) {
    [[maybe_unused]] nb::exception<DeltaError> error(m, "DeltaError", PyExc_RuntimeError);

    nb::class_<delta::Editor> editor(
        m, "Edit",
        "One edit of the object world (docs/27): operations collect in the currency of the text "
        "form (paths, never handles) and commit is one delta — all of it or none of it — written "
        "down as one .rxfd, the history entry of the delta chain. Until commit the image does not "
        "change; rollback (or an abandoned `with`) drops the operations.");
    editor
        .def(nb::init<Image &>(), nb::arg("image"), nb::keep_alive<1, 2>())
        .def("create", &delta::Editor::create, nb::arg("type"), nb::arg("parent"), nb::arg("name"),
             "A new cell of the named Type below the named parent ('' is the root). Its path: the "
             "handle comes with the commit.")
        .def("rename", &delta::Editor::rename, nb::arg("path"), nb::arg("name"))
        .def("move", &delta::Editor::move, nb::arg("path"), nb::arg("parent"))
        .def("delete", &delta::Editor::remove, nb::arg("path"))
        .def(
            "put",
            [](delta::Editor &self, const std::string &path, const std::string &field, const nb::object &value) {
                self.put(path, field, value_text(value));
            },
            nb::arg("path"), nb::arg("field"), nb::arg("value"))
        .def("ref", &delta::Editor::ref, nb::arg("path"), nb::arg("field"), nb::arg("target"),
             "A reference is written by its target path (it may point forward in the edit).")
        .def("unset", &delta::Editor::unset, nb::arg("path"), nb::arg("field"))
        .def("entry", &delta::Editor::entry, nb::arg("path"),
             "Names the entry Function of the image (the empty path: none).")
        .def(
            "commit",
            [](delta::Editor &self) {
                std::string text, error;
                if (self.commit(&text, &error) != Status::Ok) throw DeltaError(error);
                return text;
            },
            "ONE .rxfd: the operations replay as a whole — all of it or none of it — and the text "
            "that spells them is the answer.")
        .def("rollback", &delta::Editor::rollback, "The operations are dropped; the image is as it was.")
        .def_prop_ro("open", &delta::Editor::open)
        .def_prop_ro("size", &delta::Editor::size)
        .def("__enter__", [](delta::Editor &self) -> delta::Editor & { return self; },
             nb::rv_policy::reference_internal)
        .def(
            "__exit__",
            [](delta::Editor &self, nb::args) {
                if (self.open()) self.rollback(); // an abandoned edit is not a mutation
                return false;
            });

    image
        .def_static(
            "empty",
            [](uint64_t limit, uint64_t index_capacity) {
                std::unique_ptr<Image> created;
                check(delta::make_image(limit, index_capacity, &created));
                return created;
            },
            nb::arg("limit") = object::kDefaultLimit, nb::arg("index_capacity") = 0,
            "An image with the ground (root, Type, Field, uint32_t, uint64_t) and nothing else.")
        .def(
            "apply",
            [](Image &self, const std::string &text, bool links) { apply_texts(self, {{"the delta", text}}, links); },
            nb::arg("delta"), nb::arg("links") = true,
            "Applies a delta in XML. The image changes only when the whole delta applies and links; "
            "with `links=False` the operations must still hold but the result is not required to link, "
            "which is what the physical compiler needs on a live image: the image checks the links when it takes the delta.")
        .def(
            "apply_all",
            [](Image &self, const std::vector<std::pair<std::string, std::string>> &files, bool links) {
                apply_texts(self, files, links);
            },
            nb::arg("files"), nb::arg("links") = true,
            "Applies several deltas (name, XML) as one: the operations run in the order given, atomically. "
            "With `links=False` the result is not required to link (see `apply`).")
        .def(
            "export",
            [](const Image &self, const std::optional<std::string> &module) {
                return canonical_text(self, module ? &*module : nullptr);
            },
            nb::arg("module") = nb::none(),
            "The canonical delta of the image as XML; with `module`, only that Module object without its sub-modules ('' is the root).")
        .def(
            "delta_digest",
            [](const Image &self) {
                const std::string text = canonical_text(self, nullptr);
                return hash::to_hex(hash::sha256(text.data(), text.size()));
            },
            "SHA-256 of the canonical delta text.")
        /* The edit set (docs/27): one mutation is one delta. Every verb is typed and refuses
         * before the delta exists; what it applies is the one delta vocabulary, so the
         * refusals are the link check's codes and the text form is the same ops written down. */
        .def(
            "create",
            [](Image &self, const nb::object &type, const nb::object &parent, const std::string &name) {
                std::string error;
                pymergetic_rxf_handle out = 0;
                const Status s = delta::edit_create(self, target_handle(self, type),
                                                    parent.is_none() ? PYMERGETIC_RXF_HANDLE_ROOT
                                                                     : target_handle(self, parent),
                                                    name, &out, &error);
                if (s != Status::Ok) throw DeltaError(error);
                return out;
            },
            nb::arg("type"), nb::arg("parent"), nb::arg("name"),
            "A new cell of `type` below `parent` (none: the root); size and align come from the "
            "Type. Paths name things, handles identify them: either. The handle of the new cell.")
        .def(
            "rename",
            [](Image &self, const nb::object &target, const std::string &name) {
                std::string error;
                if (delta::edit_rename(self, target_handle(self, target), name, &error) != Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"), nb::arg("name"),
            "The name of an object changes; the name is an identifier and the schema checks it.")
        .def(
            "move",
            [](Image &self, const nb::object &target, const nb::object &parent) {
                std::string error;
                if (delta::edit_move(self, target_handle(self, target), target_handle(self, parent), &error) !=
                    Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"), nb::arg("parent"),
            "The object sits under another parent; the place is the sibling order (docs/15).")
        .def(
            "delete",
            [](Image &self, const nb::object &target) {
                std::string error;
                if (delta::edit_delete(self, target_handle(self, target), &error) != Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"),
            "Removes the object and everything below it; refused while anything else points at it.")
        .def(
            "put",
            [](Image &self, const nb::object &target, const std::string &field, const nb::object &value) {
                std::string error;
                if (delta::edit_put(self, target_handle(self, target), field, value_text(value), &error) !=
                    Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"), nb::arg("field"), nb::arg("value"),
            "One leaf is written: width, sign and enum come from the Field. Derived and declared "
            "lists take no value and no place; a reference is written by `ref`.")
        .def(
            "ref",
            [](Image &self, const nb::object &target, const std::string &field, const nb::object &to) {
                std::string error;
                if (delta::edit_ref(self, target_handle(self, target), field, target_handle(self, to), &error) !=
                    Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"), nb::arg("field"), nb::arg("to"),
            "The ONLY place a handle may be written: the reference field of one object points at "
            "another.")
        .def(
            "unset",
            [](Image &self, const nb::object &target, const std::string &field) {
                std::string error;
                if (delta::edit_unset(self, target_handle(self, target), field, &error) != Status::Ok)
                    throw DeltaError(error);
            },
            nb::arg("target"), nb::arg("field"),
            "An OPTIONAL field goes back to unset; anything else refuses.")
        .def(
            "edit",
            [](Image &self) { return delta::Editor(self); },
            nb::keep_alive<0, 1>(),
            "The transaction (docs/27): the verbs collect operations in the currency of the text "
            "form — paths, never handles — and commit is ONE delta: all of it or none of it.");


}

} // namespace pymergetic::rxf::core::bindings
