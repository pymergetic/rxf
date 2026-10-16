#ifndef PYMERGETIC_RXF_HASH_SHA256_CORE_HPP
#define PYMERGETIC_RXF_HASH_SHA256_CORE_HPP

/*
 * SHA-256 (FIPS 180-4) with no library, no table in memory and no function the compiler could
 * leave out of line: the round constants are the cases of a switch (compiled to compares with
 * immediates, never to a table). The host class in sha256.hpp and the native snippets use this.
 */

#include <pymergetic/rxftk/core/arena/mem.hpp>

#include <stdint.h>

namespace pymergetic::rxf::core::hash {

PYMERGETIC_RXF_INLINE uint32_t rotr(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

PYMERGETIC_RXF_INLINE uint32_t round_constant(uint32_t i) {
    switch (i) {
    case 0: return 0x428a2f98;  case 1: return 0x71374491;  case 2: return 0xb5c0fbcf;  case 3: return 0xe9b5dba5;
    case 4: return 0x3956c25b;  case 5: return 0x59f111f1;  case 6: return 0x923f82a4;  case 7: return 0xab1c5ed5;
    case 8: return 0xd807aa98;  case 9: return 0x12835b01;  case 10: return 0x243185be; case 11: return 0x550c7dc3;
    case 12: return 0x72be5d74; case 13: return 0x80deb1fe; case 14: return 0x9bdc06a7; case 15: return 0xc19bf174;
    case 16: return 0xe49b69c1; case 17: return 0xefbe4786; case 18: return 0x0fc19dc6; case 19: return 0x240ca1cc;
    case 20: return 0x2de92c6f; case 21: return 0x4a7484aa; case 22: return 0x5cb0a9dc; case 23: return 0x76f988da;
    case 24: return 0x983e5152; case 25: return 0xa831c66d; case 26: return 0xb00327c8; case 27: return 0xbf597fc7;
    case 28: return 0xc6e00bf3; case 29: return 0xd5a79147; case 30: return 0x06ca6351; case 31: return 0x14292967;
    case 32: return 0x27b70a85; case 33: return 0x2e1b2138; case 34: return 0x4d2c6dfc; case 35: return 0x53380d13;
    case 36: return 0x650a7354; case 37: return 0x766a0abb; case 38: return 0x81c2c92e; case 39: return 0x92722c85;
    case 40: return 0xa2bfe8a1; case 41: return 0xa81a664b; case 42: return 0xc24b8b70; case 43: return 0xc76c51a3;
    case 44: return 0xd192e819; case 45: return 0xd6990624; case 46: return 0xf40e3585; case 47: return 0x106aa070;
    case 48: return 0x19a4c116; case 49: return 0x1e376c08; case 50: return 0x2748774c; case 51: return 0x34b0bcb5;
    case 52: return 0x391c0cb3; case 53: return 0x4ed8aa4a; case 54: return 0x5b9cca4f; case 55: return 0x682e6ff3;
    case 56: return 0x748f82ee; case 57: return 0x78a5636f; case 58: return 0x84c87814; case 59: return 0x8cc70208;
    case 60: return 0x90befffa; case 61: return 0xa4506ceb; case 62: return 0xbef9a3f7; default: return 0xc67178f2;
    }
}

struct Sha256Core {
    uint32_t state[8];
    uint8_t buffer[64];
    uint64_t length;
    uint32_t fill;

    PYMERGETIC_RXF_INLINE void init() {
        state[0] = 0x6a09e667; state[1] = 0xbb67ae85; state[2] = 0x3c6ef372; state[3] = 0xa54ff53a;
        state[4] = 0x510e527f; state[5] = 0x9b05688c; state[6] = 0x1f83d9ab; state[7] = 0x5be0cd19;
        length = 0;
        fill = 0;
    }

    PYMERGETIC_RXF_INLINE void block(const uint8_t *chunk) {
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = (uint32_t(chunk[4 * i]) << 24) | (uint32_t(chunk[4 * i + 1]) << 16) | (uint32_t(chunk[4 * i + 2]) << 8) |
                   uint32_t(chunk[4 * i + 3]);
        for (int i = 16; i < 64; i++) {
            const uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = state[0], b = state[1], c = state[2], d = state[3];
        uint32_t e = state[4], f = state[5], g = state[6], h = state[7];
        for (uint32_t i = 0; i < 64; i++) {
            const uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const uint32_t ch = (e & f) ^ (~e & g);
            const uint32_t t1 = h + s1 + ch + round_constant(i) + w[i];
            const uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            const uint32_t t2 = s0 + maj;
            h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        state[0] += a; state[1] += b; state[2] += c; state[3] += d;
        state[4] += e; state[5] += f; state[6] += g; state[7] += h;
    }

    PYMERGETIC_RXF_INLINE void update(const void *data, uint64_t size) {
        const uint8_t *bytes = static_cast<const uint8_t *>(data);
        length += size;
        while (size > 0) {
            const uint64_t room = 64 - fill;
            const uint64_t take = size < room ? size : room;
            mem::copy(buffer + fill, bytes, take);
            fill += static_cast<uint32_t>(take);
            bytes += take;
            size -= take;
            if (fill == 64) {
                block(buffer);
                fill = 0;
            }
        }
    }

    PYMERGETIC_RXF_INLINE void finish(uint8_t out[32]) {
        const uint64_t bits = length * 8;
        const uint8_t pad = 0x80;
        update(&pad, 1);
        const uint8_t zero = 0;
        while (fill != 56) update(&zero, 1);
        uint8_t tail[8];
        for (int i = 0; i < 8; i++) tail[i] = uint8_t(bits >> (56 - 8 * i));
        update(tail, 8);
        for (int i = 0; i < 8; i++) {
            out[4 * i] = uint8_t(state[i] >> 24);
            out[4 * i + 1] = uint8_t(state[i] >> 16);
            out[4 * i + 2] = uint8_t(state[i] >> 8);
            out[4 * i + 3] = uint8_t(state[i]);
        }
    }
};

} // namespace pymergetic::rxf::core::hash

#endif
