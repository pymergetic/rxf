#ifndef PYMERGETIC_RXF_CORE_DOC_CODEC_HPP
#define PYMERGETIC_RXF_CORE_DOC_CODEC_HPP

/*
 * The text of documentation, and the codec behind it (docs/22). A doc payload
 * opens with the words of `pymergetic_rxf_doc`; the rest is its text in the
 * codec those words name. This module implements the LZ4 block format itself:
 * no third-party codec sits in the tree, and every seat reads the same bytes.
 */

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace pymergetic::rxf::core::doc {

/* The LZ4 block format: a block of sequences of literals and matches. */
std::vector<uint8_t> lz4_compress(std::string_view text);
/* Decodes one block. False when the bytes are not a well-formed block, or when
 * they ask for more text than the block can carry. */
bool lz4_decompress(const uint8_t *bytes, uint64_t size, std::string *out, std::string *error);

/* Well-formed UTF-8: no overlong forms, no surrogates, nothing above U+10FFFF. */
bool valid_utf8(std::string_view text);

/* The tail of a doc payload: `text` in the codec the words name. `Raw` keeps
 * the text as it was written; `Lz4` compresses where it pays and falls back to
 * raw where it does not, so the codec always names the bytes behind it. */
struct Stored {
    uint32_t codec = 0; // the codec of `bytes`: Raw or Lz4
    std::vector<uint8_t> bytes;
};
Stored store(uint32_t codec, std::string_view text);
/* The text behind a stored tail. False when the tail is not that codec's text. */
bool unstore(uint32_t codec, const uint8_t *tail, uint64_t tail_size, std::string *out,
             std::string *error);

} // namespace pymergetic::rxf::core::doc

#endif
