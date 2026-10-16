#include <pymergetic/rxftk/core/doc/codec.hpp>

#include <pymergetic/rxftk/core/ground.h>

namespace pymergetic::rxf::core::doc {

namespace {

/* The LZ4 block format: sequences of (token, literal length, literals, offset,
 * match length). The constraints of the format hold: no offset is zero, the
 * last sequence is literals only, and the last five bytes of the text are
 * always literals. */

constexpr uint64_t kMinMatch = 4;
constexpr uint64_t kLastLiterals = 5; /* the tail of the text is literals */
constexpr uint64_t kMatchLimit = 12;  /* the last match starts this far before the end */
constexpr uint64_t kWindow = 0xFFFF;  /* an offset is two bytes, and never zero */

uint32_t read32(const uint8_t *p) {
    return static_cast<uint32_t>(p[0]) | (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) | (static_cast<uint32_t>(p[3]) << 24);
}

/* The length of a literal or match run in its extended form. */
void put_length(std::vector<uint8_t> *out, uint64_t length) {
    uint64_t v = length;
    while (v >= 255) {
        out->push_back(255);
        v -= 255;
    }
    out->push_back(static_cast<uint8_t>(v));
}

bool get_length(const uint8_t *bytes, uint64_t size, uint64_t *at, uint64_t *out) {
    uint64_t v = 0;
    while (true) {
        if (*at >= size)
            return false;
        const uint8_t byte = bytes[(*at)++];
        if (byte == 255) {
            v += 255;
        } else {
            v += byte;
            break;
        }
    }
    *out = v;
    return true;
}

/* A block never decodes past this: no byte of a block carries more than 255
 * bytes of text (a length extension), and the rest is text that was literals. */
bool over_budget(uint64_t size, uint64_t text) {
    return text > 255 * size + 64;
}

} // namespace

std::vector<uint8_t> lz4_compress(std::string_view text) {
    const uint8_t *src = reinterpret_cast<const uint8_t *>(text.data());
    const uint64_t n = text.size();
    std::vector<uint8_t> out;
    out.reserve(n / 2 + 32);
    if (n == 0)
        return out;

    // One candidate per four-byte hash: greedy, and the same bytes always make the same block.
    std::vector<int64_t> table(1 << 16, -1);
    const auto hash_of = [&](uint64_t at) -> size_t {
        return static_cast<size_t>((read32(src + at) * 2654435761ULL) >> 48); // the top 16 bits
    };
    const auto emit = [&](uint64_t anchor, uint64_t at, uint64_t matched, uint64_t offset) {
        const uint64_t literals = at - anchor;
        const uint64_t code = matched == 0 ? 0 : matched - kMinMatch;
        out.push_back(
            static_cast<uint8_t>((literals < 15 ? literals : 15) << 4 | (code < 15 ? code : 15)));
        if (literals >= 15)
            put_length(&out, literals - 15);
        out.insert(out.end(), src + anchor, src + at);
        if (matched != 0) {
            out.push_back(static_cast<uint8_t>(offset & 0xFF));
            out.push_back(static_cast<uint8_t>(offset >> 8));
            if (code >= 15)
                put_length(&out, code - 15);
        }
    };

    uint64_t anchor = 0;
    uint64_t at = 0;
    while (at + kMinMatch <= n && at + kMatchLimit <= n) {
        const size_t slot = hash_of(at);
        const int64_t candidate = table[slot];
        table[slot] = static_cast<int64_t>(at);
        uint64_t matched = 0, offset = 0;
        if (candidate >= 0 && at - static_cast<uint64_t>(candidate) <= kWindow &&
            read32(src + candidate) == read32(src + at)) {
            const uint64_t limit = n - kLastLiterals - at;
            uint64_t len = kMinMatch;
            while (len < limit && src[static_cast<uint64_t>(candidate) + len] == src[at + len])
                len++;
            if (len >= kMinMatch) {
                matched = len;
                offset = at - static_cast<uint64_t>(candidate);
            }
        }
        if (matched != 0) {
            emit(anchor, at, matched, offset);
            for (uint64_t i = at + 1; i <= at + matched - kMinMatch + 1 && i + kMinMatch <= n; i++)
                table[hash_of(i)] = static_cast<int64_t>(i);
            anchor = at + matched;
            at = anchor;
        } else {
            at++;
        }
    }
    emit(anchor, n, 0, 0);
    return out;
}

bool lz4_decompress(const uint8_t *bytes, uint64_t size, std::string *out, std::string *error) {
    const auto fail = [&](const char *message) {
        if (error != nullptr)
            *error = message;
        return false;
    };
    out->clear();
    out->reserve(size < (1ULL << 20) ? size * 2 : (1ULL << 20));
    uint64_t at = 0;
    while (at < size) {
        const uint8_t token = bytes[at++];
        uint64_t literals = token >> 4;
        if (literals == 15) {
            uint64_t more = 0;
            if (!get_length(bytes, size, &at, &more))
                return fail("a literal length runs past the block");
            literals += more;
        }
        if (literals > size - at)
            return fail("the literals of a sequence run past the block");
        if (over_budget(size, out->size() + literals))
            return fail("the block decodes past what its bytes carry");
        out->append(reinterpret_cast<const char *>(bytes + at), literals);
        at += literals;
        if (at == size)
            break; // the last sequence is literals only
        if (size - at < 2)
            return fail("a match has no offset");
        const uint64_t offset =
            static_cast<uint64_t>(bytes[at]) | (static_cast<uint64_t>(bytes[at + 1]) << 8);
        at += 2;
        if (offset == 0)
            return fail("a match has no offset");
        if (offset > out->size())
            return fail("a match reaches before the start of the text");
        uint64_t matched = token & 0xF;
        if (matched == 15) {
            uint64_t more = 0;
            if (!get_length(bytes, size, &at, &more))
                return fail("a match length runs past the block");
            matched += more;
        }
        matched += kMinMatch;
        if (over_budget(size, out->size() + matched))
            return fail("the block decodes past what its bytes carry");
        // Matches may overlap the bytes they copy, so this copies byte by byte.
        const uint64_t from = out->size() - offset;
        for (uint64_t i = 0; i < matched; i++)
            out->push_back((*out)[from + i]);
    }
    return true;
}

bool valid_utf8(std::string_view text) {
    size_t i = 0;
    while (i < text.size()) {
        const uint8_t c = static_cast<uint8_t>(text[i]);
        size_t extra;
        uint32_t cp;
        if (c < 0x80) {
            extra = 0;
            cp = c;
        } else if (c >= 0xC2 && c <= 0xDF) {
            extra = 1;
            cp = c & 0x1F;
        } else if (c >= 0xE0 && c <= 0xEF) {
            extra = 2;
            cp = c & 0x0F;
        } else if (c >= 0xF0 && c <= 0xF4) {
            extra = 3;
            cp = c & 0x07;
        } else {
            return false;
        }
        for (size_t k = 1; k <= extra; k++) {
            if (i + k >= text.size())
                return false;
            const uint8_t d = static_cast<uint8_t>(text[i + k]);
            if ((d & 0xC0) != 0x80)
                return false;
            cp = (cp << 6) | (d & 0x3F);
        }
        if ((extra == 2 && cp < 0x800) || (extra == 3 && cp < 0x10000) || cp > 0x10FFFF ||
            (cp >= 0xD800 && cp <= 0xDFFF))
            return false;
        i += extra + 1;
    }
    return true;
}

Stored store(uint32_t codec, std::string_view text) {
    Stored stored;
    if (text.empty()) {
        // The pin (docs/22): a doc with an empty body is not written in DocCodec.Raw.
        stored.codec = PYMERGETIC_RXF_DOC_CODEC_LZ4;
        return stored;
    }
    if (codec != PYMERGETIC_RXF_DOC_CODEC_RAW) {
        std::vector<uint8_t> compressed = lz4_compress(text);
        if (compressed.size() < text.size()) {
            stored.codec = PYMERGETIC_RXF_DOC_CODEC_LZ4;
            stored.bytes = std::move(compressed);
            return stored;
        }
    }
    stored.codec = PYMERGETIC_RXF_DOC_CODEC_RAW;
    stored.bytes.assign(text.begin(), text.end());
    return stored;
}

bool unstore(uint32_t codec, const uint8_t *tail, uint64_t tail_size, std::string *out,
             std::string *error) {
    if (codec == PYMERGETIC_RXF_DOC_CODEC_RAW) {
        if (tail_size == 0) {
            out->clear();
        } else {
            out->assign(reinterpret_cast<const char *>(tail), tail_size);
        }
        return true;
    }
    if (codec == PYMERGETIC_RXF_DOC_CODEC_LZ4)
        return lz4_decompress(tail, tail_size, out, error);
    if (error != nullptr)
        *error = "the codec of the text is not one the format names";
    return false;
}

} // namespace pymergetic::rxf::core::doc
