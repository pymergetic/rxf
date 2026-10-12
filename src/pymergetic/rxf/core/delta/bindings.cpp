#include <nanobind/stl/optional.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/delta/delta.hpp>
#include <pymergetic/rxf/core/hash/sha256.hpp>
#include <pymergetic/rxf/core/xml/xml.hpp>

namespace pymergetic::rxf::core::bindings {

namespace {

using object::Image;
using object::Status;

/* Parses each text (error messages carry its name) and applies all operations as one delta. */
void apply_texts(Image &image, const std::vector<std::pair<std::string, std::string>> &files) {
    delta::Delta all;
    for (const auto &file : files) {
        delta::Delta one;
        std::string error;
        if (xml::parse(file.second, &one, &error) != Status::Ok) throw DeltaError("cannot parse " + file.first + ": " + error);
        for (auto &op : one.ops) all.ops.push_back(std::move(op));
    }
    delta::Failure failure;
    if (delta::apply(image, all, &failure) != Status::Ok) throw DeltaError(delta::describe(failure));
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
            [](Image &self, const std::string &text) { apply_texts(self, {{"the delta", text}}); }, nb::arg("delta"),
            "Applies a delta in XML. The image changes only when the whole delta applies and links.")
        .def(
            "apply_all",
            [](Image &self, const std::vector<std::pair<std::string, std::string>> &files) { apply_texts(self, files); },
            nb::arg("files"),
            "Applies several deltas (name, XML) as one: the operations run in the order given, atomically.")
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
            "SHA-256 of the canonical delta text.");
}

} // namespace pymergetic::rxf::core::bindings
