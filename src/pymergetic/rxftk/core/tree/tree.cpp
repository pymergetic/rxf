#include "tree.hpp"

#include <algorithm>
#include <atomic>
#include <fstream>
#include <iterator>
#include <set>
#include <string_view>
#include <thread>

#include <pymergetic/rxftk/core/xml/xml.hpp>

namespace pymergetic::rxf::core::tree {

namespace fs = std::filesystem;

using Handle = pymergetic_rxf_handle;

namespace {

constexpr const char *kInitStem = "__init__"; // kInitFile without the suffix

/* Runs job(0) .. job(count - 1) on up to `threads` threads; each index runs once. */
template <class Job> void parallel_for(size_t count, unsigned threads, Job &&job) {
    unsigned workers = threads != 0 ? threads : std::max(1U, std::thread::hardware_concurrency());
    workers = static_cast<unsigned>(std::min<size_t>(workers, count));
    std::atomic<size_t> next{0};
    const auto work = [&] {
        for (size_t i = next.fetch_add(1); i < count; i = next.fetch_add(1))
            job(i);
    };
    if (workers <= 1) {
        work();
        return;
    }
    std::vector<std::thread> pool;
    pool.reserve(workers - 1);
    for (unsigned t = 1; t < workers; t++)
        pool.emplace_back(work);
    work();
    for (std::thread &t : pool)
        t.join();
}

/* What one job leaves behind; each job writes only its own slot. */
struct Outcome {
    Status status = Status::Ok;
    std::string message;
};

bool is_module(const Image &image, Handle h) {
    const pymergetic_rxf_cell *c = image.arena().cell(h);
    return c != nullptr && ((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) &
                            PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_MODULE;
}

/* The path of every Module object, wherever it sits. */
std::vector<std::string> module_paths(const Image &image) {
    std::vector<std::string> paths;
    std::vector<Handle> stack = image.children(PYMERGETIC_RXF_HANDLE_ROOT);
    while (!stack.empty()) {
        const Handle h = stack.back();
        stack.pop_back();
        if (is_module(image, h))
            paths.push_back(image.path(h));
        const std::vector<Handle> kids = image.children(h);
        stack.insert(stack.end(), kids.begin(), kids.end());
    }
    return paths;
}

std::vector<std::string> split_path(const std::string &path) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        const size_t dot = path.find('.', start);
        parts.push_back(
            path.substr(start, dot == std::string::npos ? std::string::npos : dot - start));
        if (dot == std::string::npos)
            break;
        start = dot + 1;
    }
    return parts;
}

bool is_part(const std::string &part) {
    return !part.empty() && part != kInitStem && part.find_first_of("/\\") == std::string::npos &&
           part != "." && part != "..";
}

/* `a.b.c` is `a/b/c.rxfd`, or `a/b/c/__init__.rxfd` when it has sub-modules. A name that could
 * leave the directory has no file. */
bool file_for(const std::string &path, bool has_submodules, std::string *name) {
    std::string out;
    for (const std::string &part : split_path(path)) {
        if (!is_part(part))
            return false;
        if (!out.empty())
            out += '/';
        out += part;
    }
    *name = has_submodules ? out + "/" + kInitFile : out + kFileSuffix;
    return true;
}

/* The module a file defines, and the parent that its top-level elements sit in. `__init__.rxfd`
 * alone is the root. */
bool module_of(const std::string &name, std::string *module, std::string *parent, bool *is_root) {
    std::vector<std::string> parts;
    size_t start = 0;
    while (true) {
        const size_t slash = name.find('/', start);
        parts.push_back(
            name.substr(start, slash == std::string::npos ? std::string::npos : slash - start));
        if (slash == std::string::npos)
            break;
        start = slash + 1;
    }
    std::string last = parts.back();
    parts.pop_back();
    const std::string_view suffix = kFileSuffix;
    if (last == kInitFile) {
        // pkg/__init__.rxfd is the module `pkg`; the one at the top is the root
    } else if (last.size() > suffix.size() &&
               last.compare(last.size() - suffix.size(), suffix.size(), suffix) == 0) {
        parts.push_back(last.substr(0, last.size() - suffix.size()));
    } else {
        return false;
    }
    *is_root = parts.empty();
    module->clear();
    parent->clear();
    for (size_t i = 0; i < parts.size(); i++) {
        if (parts[i].find('.') != std::string::npos || !is_part(parts[i]))
            return false;
        if (i + 1 < parts.size())
            *parent += (parent->empty() ? "" : ".") + parts[i];
        *module += (module->empty() ? "" : ".") + parts[i];
    }
    return true;
}

} // namespace

Status files_of(const Image &image, std::vector<File> *out, std::string *error, unsigned threads) {
    out->clear();
    struct Job {
        std::string name;
        std::string module; // path; empty is the root
        std::string parent; // the path its top-level element sits in
    };
    const std::vector<std::string> modules = module_paths(image);
    const std::set<std::string> known(modules.begin(), modules.end());
    std::set<std::string> with_children; // modules that have a module below them
    for (const std::string &path : modules) {
        for (size_t dot = path.find('.'); dot != std::string::npos; dot = path.find('.', dot + 1))
            if (known.count(path.substr(0, dot)) != 0)
                with_children.insert(path.substr(0, dot));
    }
    std::vector<Job> jobs;
    for (const std::string &path : modules) {
        Job job{{}, path, {}};
        if (!file_for(path, with_children.count(path) != 0, &job.name)) {
            *error = "the module '" + path + "' cannot be a file name";
            return Status::BadName;
        }
        const size_t dot = path.rfind('.');
        if (dot != std::string::npos)
            job.parent = path.substr(0, dot);
        jobs.push_back(std::move(job));
    }
    std::sort(jobs.begin(), jobs.end(), [](const Job &a, const Job &b) { return a.name < b.name; });
    for (size_t i = 1; i < jobs.size(); i++)
        if (jobs[i].name == jobs[i - 1].name) {
            *error = "the modules '" + jobs[i - 1].module + "' and '" + jobs[i].module +
                     "' have the same file";
            return Status::NameTaken;
        }
    jobs.insert(jobs.begin(), Job{kInitFile, {}, {}});

    std::vector<File> files(jobs.size());
    std::vector<Outcome> outcomes(jobs.size());
    parallel_for(jobs.size(), threads, [&](size_t i) {
        delta::Delta delta;
        std::string problem;
        Outcome &outcome = outcomes[i];
        outcome.status = delta::export_image(image, &delta, &problem, &jobs[i].module);
        if (outcome.status == Status::Ok)
            outcome.status = xml::write(delta, &files[i].text, &problem, &jobs[i].parent);
        if (outcome.status != Status::Ok)
            outcome.message = jobs[i].name + ": " + problem;
        files[i].name = jobs[i].name;
    });
    for (const Outcome &outcome : outcomes) {
        if (outcome.status != Status::Ok) {
            *error = outcome.message;
            return outcome.status;
        }
    }
    *out = std::move(files);
    return Status::Ok;
}

Status write(const Image &image, const fs::path &directory, bool overwrite, size_t *count,
             std::string *error, unsigned threads) {
    std::vector<File> files;
    Status status = files_of(image, &files, error, threads);
    if (status != Status::Ok)
        return status;

    std::error_code code;
    if (fs::exists(directory, code)) {
        if (!fs::is_directory(directory, code)) {
            *error = directory.string() + " is not a directory";
            return Status::BadPath;
        }
        if (fs::directory_iterator(directory, code) != fs::directory_iterator()) {
            if (!overwrite) {
                *error = directory.string() + " is not empty";
                return Status::NameTaken;
            }
            std::vector<fs::path> old;
            for (const auto &entry : fs::recursive_directory_iterator(directory, code))
                if (entry.is_regular_file() && entry.path().extension() == kFileSuffix)
                    old.push_back(entry.path());
            for (const fs::path &path : old)
                fs::remove(path, code);
            std::vector<fs::path> folders;
            for (const auto &entry : fs::recursive_directory_iterator(directory, code))
                if (entry.is_directory())
                    folders.push_back(entry.path());
            std::sort(folders.rbegin(), folders.rend()); // deepest first
            for (const fs::path &folder : folders)
                if (fs::is_empty(folder, code))
                    fs::remove(folder, code);
        }
    }

    std::vector<Outcome> outcomes(files.size());
    parallel_for(files.size(), threads, [&](size_t i) {
        const fs::path target = directory / files[i].name;
        std::error_code local;
        fs::create_directories(target.parent_path(), local);
        std::ofstream stream(target, std::ios::binary | std::ios::trunc);
        stream.write(files[i].text.data(), static_cast<std::streamsize>(files[i].text.size()));
        stream.close();
        if (local || !stream)
            outcomes[i] = {Status::BadPath, "cannot write " + target.string()};
    });
    for (const Outcome &outcome : outcomes) {
        if (outcome.status != Status::Ok) {
            *error = outcome.message;
            return outcome.status;
        }
    }
    *count = files.size();
    return Status::Ok;
}

Status read(const fs::path &directory, delta::Delta *out, std::string *error, unsigned threads) {
    std::error_code code;
    if (!fs::is_directory(directory, code)) {
        *error = directory.string() + " is not a directory";
        return Status::BadPath;
    }
    std::vector<std::string> names;
    for (const auto &entry : fs::recursive_directory_iterator(directory, code))
        if (entry.is_regular_file() && entry.path().extension() == kFileSuffix)
            names.push_back(entry.path().lexically_relative(directory).generic_string());
    if (code) {
        *error = "cannot list " + directory.string() + ": " + code.message();
        return Status::BadPath;
    }
    if (names.empty()) {
        *error = directory.string() + " holds no " + kFileSuffix + " files";
        return Status::BadPath;
    }
    // The path of a file is the path of its module; the root file names the entry, which has to see
    // every module before it.
    struct Place {
        std::string module, parent;
        bool root = false;
    };
    std::vector<Place> places(names.size());
    std::set<std::string> defined;
    for (size_t i = 0; i < names.size(); i++) {
        if (!module_of(names[i], &places[i].module, &places[i].parent, &places[i].root)) {
            *error = names[i] + " is not the file of a module (a.b.rxfd, or a/b/__init__.rxfd)";
            return Status::BadName;
        }
        if (!defined.insert(places[i].module).second) {
            *error = names[i] + " defines the module '" + places[i].module + "' a second time";
            return Status::NameTaken;
        }
    }
    std::vector<size_t> order(names.size());
    for (size_t i = 0; i < order.size(); i++)
        order[i] = i;
    std::sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        return places[a].root != places[b].root ? places[b].root : names[a] < names[b];
    });

    std::vector<delta::Delta> parsed(names.size());
    std::vector<Outcome> outcomes(names.size());
    parallel_for(names.size(), threads, [&](size_t i) {
        std::ifstream stream(directory / names[i], std::ios::binary);
        const std::string text((std::istreambuf_iterator<char>(stream)),
                               std::istreambuf_iterator<char>());
        if (!stream && !stream.eof()) {
            outcomes[i] = {Status::BadPath, "cannot read " + names[i]};
            return;
        }
        std::string problem;
        const Place &place = places[i];
        const Status status = xml::parse(text, &parsed[i], &problem, &place.parent);
        if (status != Status::Ok) {
            outcomes[i] = {status, "cannot parse " + names[i] + ": " + problem};
            return;
        }
        if (place.root)
            return;
        // A module file holds its module, and only what is inside it.
        const std::string last = place.module.substr(
            place.module.rfind('.') == std::string::npos ? 0 : place.module.rfind('.') + 1);
        for (size_t k = 0; k < parsed[i].ops.size(); k++) {
            const delta::Create *c = std::get_if<delta::Create>(&parsed[i].ops[k]);
            const bool inside = c != nullptr && (c->parent == place.module ||
                                                 c->parent.compare(0, place.module.size() + 1,
                                                                   place.module + ".") == 0);
            const bool head =
                k == 0 && c != nullptr && c->name == last && c->parent == place.parent;
            if (!head && !inside) {
                outcomes[i] = {
                    Status::BadImage,
                    names[i] +
                        (k == 0 ? ": must start with the module '" + place.module + "'"
                                : ": defines something outside the module '" + place.module + "'")};
                return;
            }
        }
        if (parsed[i].ops.empty())
            outcomes[i] = {Status::BadImage,
                           names[i] + ": must define the module '" + place.module + "'"};
    });
    out->ops.clear();
    for (size_t index : order) {
        if (outcomes[index].status != Status::Ok) {
            *error = outcomes[index].message;
            return outcomes[index].status;
        }
        for (delta::Op &op : parsed[index].ops)
            out->ops.push_back(std::move(op));
    }
    return Status::Ok;
}

Status load(const fs::path &directory, uint64_t limit, std::unique_ptr<Image> *out,
            std::string *error, unsigned threads) {
    delta::Delta all;
    Status status = read(directory, &all, error, threads);
    if (status != Status::Ok)
        return status;
    std::unique_ptr<Image> image;
    status = delta::make_image(limit, 0, &image);
    if (status != Status::Ok) {
        *error = "cannot create the image";
        return status;
    }
    delta::Failure failure;
    status = delta::apply(*image, all, &failure);
    if (status != Status::Ok) {
        *error = delta::describe(failure);
        return status;
    }
    *out = std::move(image);
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::tree
