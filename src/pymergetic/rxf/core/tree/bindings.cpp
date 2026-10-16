#include <nanobind/stl/filesystem.h>
#include <nanobind/stl/pair.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/unique_ptr.h>
#include <nanobind/stl/vector.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/tree/tree.hpp>

namespace pymergetic::rxf::core::bindings {

namespace {

using object::Image;
using object::Status;

[[noreturn]] void fail(const std::string &message) { throw TreeError(message); }

} // namespace

/* An image as one delta per module. The work runs on several threads without the GIL. */
void bind_tree(nb::module_ &m) {
    [[maybe_unused]] nb::exception<TreeError> error(m, "TreeError", PyExc_RuntimeError);
    m.attr("INIT_FILE") = tree::kInitFile;
    m.def(
        "files",
        [](const Image &image, unsigned threads) {
            std::vector<tree::File> files;
            std::string problem;
            Status status;
            {
                nb::gil_scoped_release release;
                status = tree::files_of(image, &files, &problem, threads);
            }
            if (status != Status::Ok) fail(problem);
            std::vector<std::pair<std::string, std::string>> out;
            out.reserve(files.size());
            for (tree::File &file : files) out.emplace_back(std::move(file.name), std::move(file.text));
            return out;
        },
        nb::arg("image"), nb::arg("threads") = 0,
        "The canonical files of the image as (name, XML): __init__.rxfd first, then one per module by file name.");
    m.def(
        "write",
        [](const Image &image, const std::filesystem::path &directory, bool overwrite, unsigned threads) {
            size_t count = 0;
            std::string problem;
            Status status;
            {
                nb::gil_scoped_release release;
                status = tree::write(image, directory, overwrite, &count, &problem, threads);
            }
            if (status != Status::Ok) fail(problem);
            return count;
        },
        nb::arg("image"), nb::arg("directory"), nb::arg("overwrite") = false, nb::arg("threads") = 0,
        nb::sig("def write(image: pymergetic.rxf.core.object.Image, directory: str | os.PathLike[str], overwrite: bool = False, "
                "threads: int = 0) -> int"),
        "Writes the files below the directory and returns how many. A used directory needs `overwrite`, and only .rxfd files are replaced.");
    m.def(
        "load",
        [](const std::filesystem::path &directory, uint64_t limit, unsigned threads) {
            std::unique_ptr<Image> image;
            std::string problem;
            Status status;
            {
                nb::gil_scoped_release release;
                status = tree::load(directory, limit, &image, &problem, threads);
            }
            if (status != Status::Ok) fail(problem);
            return image;
        },
        nb::arg("directory"), nb::arg("limit") = object::kDefaultLimit, nb::arg("threads") = 0,
        nb::sig("def load(directory: str | os.PathLike[str], limit: int = ..., threads: int = 0) -> "
                "pymergetic.rxf.core.object.Image"),
        "A new image from the tree: the files are parsed in parallel, then replayed as one atomic delta, the root __init__.rxfd last.");
}

} // namespace pymergetic::rxf::core::bindings
