#ifndef PYMERGETIC_RXF_HASH_SHA256_HPP
#define PYMERGETIC_RXF_HASH_SHA256_HPP

/* SHA-256 (FIPS 180-4). Digests of canonical deltas and images use it. */

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <string>

namespace pymergetic::rxf::core::hash {

using Digest = std::array<uint8_t, 32>;

class Sha256 {
public:
    Sha256();
    void update(const void *data, size_t size);
    Digest finish();

private:
    void block(const uint8_t *chunk);
    uint32_t state_[8];
    uint8_t buffer_[64];
    uint64_t length_ = 0;
    size_t fill_ = 0;
};

Digest sha256(const void *data, size_t size);
std::string to_hex(const Digest &digest);

} // namespace pymergetic::rxf::core::hash

#endif
