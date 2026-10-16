#include <pymergetic/rxftk/core/hash/sha256.hpp>
#include <pymergetic/rxftk/core/hash/sha256_core.hpp>

namespace pymergetic::rxf::core::hash {

Sha256::Sha256() {
    Sha256Core core;
    core.init();
    std::copy(core.state, core.state + 8, state_);
    std::fill(buffer_, buffer_ + sizeof(buffer_), 0);
}

void Sha256::block(const uint8_t *chunk) {
    Sha256Core core;
    std::copy(state_, state_ + 8, core.state);
    core.block(chunk);
    std::copy(core.state, core.state + 8, state_);
}

void Sha256::update(const void *data, size_t size) {
    const uint8_t *bytes = static_cast<const uint8_t *>(data);
    length_ += size;
    while (size > 0) {
        const size_t take = size < 64 - fill_ ? size : 64 - fill_;
        std::copy(bytes, bytes + take, buffer_ + fill_);
        fill_ += take;
        bytes += take;
        size -= take;
        if (fill_ == 64) {
            block(buffer_);
            fill_ = 0;
        }
    }
}

Digest Sha256::finish() {
    const uint64_t bits = length_ * 8;
    const uint8_t pad = 0x80;
    update(&pad, 1);
    const uint8_t zero = 0;
    while (fill_ != 56)
        update(&zero, 1);
    uint8_t tail[8];
    for (int i = 0; i < 8; i++)
        tail[i] = uint8_t(bits >> (56 - 8 * i));
    update(tail, 8);
    Digest out;
    for (size_t i = 0; i < 8; i++) {
        out[4 * i] = uint8_t(state_[i] >> 24);
        out[4 * i + 1] = uint8_t(state_[i] >> 16);
        out[4 * i + 2] = uint8_t(state_[i] >> 8);
        out[4 * i + 3] = uint8_t(state_[i]);
    }
    return out;
}

Digest sha256(const void *data, size_t size) {
    Sha256 h;
    h.update(data, size);
    return h.finish();
}

std::string to_hex(const Digest &digest) {
    static const char hex[] = "0123456789abcdef";
    std::string out;
    out.reserve(64);
    for (uint8_t b : digest) {
        out += hex[b >> 4];
        out += hex[b & 15];
    }
    return out;
}

} // namespace pymergetic::rxf::core::hash
