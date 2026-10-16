#ifndef PYMERGETIC_RXF_CORE_TREE_TREE_HPP
#define PYMERGETIC_RXF_CORE_TREE_TREE_HPP

/*
 * An image as a tree of files: one delta (XML) per Module object, laid out
 * exactly like Python modules.
 *
 *   __init__.rxfd                  what sits at the root (types, the entry)
 *   pymergetic/__init__.rxfd       module `pymergetic`, which has sub-modules
 *   pymergetic/rxf/__init__.rxfd   module `pymergetic.rxf`, which has sub-modules
 *   pymergetic/rxf/library.rxfd    module `pymergetic.rxf.library`, which has none
 *
 * A module with sub-modules is a folder with an `__init__.xml`, a module without
 * is a file of its own name. The path of the file is the path of the module, so
 * a file does not name its parent. It holds the Module object and everything
 * below it that is not a Module itself; every Module, wherever it sits, is a file
 * of its own. The files are independent texts, so they are printed and parsed on
 * several threads: the image is only read while writing, and every file is
 * parsed into its own operations before one atomic replay applies them (the
 * root file last, because it names the entry). The tree is canonical: the same
 * image always gives the same files.
 */

#include <cstdint>
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include <pymergetic/rxftk/core/delta/delta.hpp>
#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::tree {

using object::Image;
using object::Status;

inline constexpr const char *kInitFile = "__init__.rxfd"; // the root, or a module with sub-modules
inline constexpr const char *kFileSuffix = ".rxfd";

/* `threads` is the number of worker threads; 0 is one per core. */

struct File {
    std::string name; // relative, with '/', ends in .rxfd
    std::string text;
};

/* The canonical files of an image: __init__.rxfd first, then the modules by file name. */
Status files_of(const Image &image, std::vector<File> *out, std::string *error, unsigned threads = 0);

/* Writes the files below `directory` and returns how many. A directory that
 * holds anything is only used with `overwrite`, and then only `.rxfd` files
 * (and the folders they leave empty) are replaced. Nothing is touched when
 * the files cannot be produced. */
Status write(const Image &image, const std::filesystem::path &directory, bool overwrite, size_t *count, std::string *error,
             unsigned threads = 0);

/* Parses every `.rxfd` file below `directory` into one delta: the modules by file name, the root `__init__.rxfd` last.
 * Each file must define the module its path names, and nothing outside it. */
Status read(const std::filesystem::path &directory, delta::Delta *out, std::string *error, unsigned threads = 0);

/* A new image from the tree. */
Status load(const std::filesystem::path &directory, uint64_t limit, std::unique_ptr<Image> *out, std::string *error,
            unsigned threads = 0);

} // namespace pymergetic::rxf::core::tree

#endif
