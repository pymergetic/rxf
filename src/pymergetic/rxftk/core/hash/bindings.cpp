#include <nanobind/stl/string.h>

#include <pymergetic/rxftk/core/bindings.hpp>
#include <pymergetic/rxftk/core/hash/sha256.hpp>

namespace pymergetic::rxf::core::bindings {

void bind_hash(nb::module_ &m) {
    m.def(
        "sha256",
        [](nb::bytes data) { return hash::to_hex(hash::sha256(data.c_str(), data.size())); },
        nb::arg("data"), "SHA-256 of the bytes as lower-case hex.");
}

} // namespace pymergetic::rxf::core::bindings
