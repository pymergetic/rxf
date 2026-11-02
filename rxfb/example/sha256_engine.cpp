/*
 * The example's SHA-256: the engine's own (core/hash/sha256.cpp), behind a C symbol so the C
 * example does not need a second implementation. The library (rxfb.c) does no hashing; a digest
 * is a policy of the host, and this file is the host's.
 */

#include <pymergetic/rxftk/core/hash/sha256.hpp>

#include <cstddef>
#include <cstdint>

extern "C" void rxfb_example_sha256(const void *data, std::size_t size, std::uint8_t out[32]) {
    const auto sum = pymergetic::rxf::core::hash::sha256(data, size);
    for (std::size_t i = 0; i < sum.size(); i++)
        out[i] = sum[i];
}
