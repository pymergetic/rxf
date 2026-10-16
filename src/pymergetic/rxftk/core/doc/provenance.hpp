#ifndef PYMERGETIC_RXF_CORE_DOC_PROVENANCE_HPP
#define PYMERGETIC_RXF_CORE_DOC_PROVENANCE_HPP

/*
 * The recorded provenance of imported objects (docs/22): the facts an import
 * run writes at the level it declares. Per imported source set: an origin
 * record (from `cite` up). Per imported object: one citation -- a locator of
 * the source range it came from (from `cite` up). Per source file: the imported
 * source itself (only at `full`) or its content SHA (only at `hash`). The floor
 * is `hash`: below it is refused (the importer takes no anonymous input), and a
 * fact is never written above the tier that allows it -- an image says at which
 * level its objects were claimed, and never overclaims.
 *
 * The locator seam (decided in docs/22 and docs/15): a reference record has no
 * locator payload -- `pymergetic_rxf_ref` is 24 bytes, pinned in ground.h. The
 * locator of a citation lives in the text tail of the citing Doc, the same face
 * slice 1 put doc text in, so the ABI is untouched and a citation round-trips
 * through the text form like any doc. The record is `key=value` lines:
 * `path=py/obj.c`, `lines=120-180`, and an optional `sha256=<hex>`.
 *
 * Comment lifting is the same machinery: the import path turns every comment it
 * lifts into a Doc and places it on the object it documents.
 */

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

#include <pymergetic/rxftk/core/object/image.hpp>

namespace pymergetic::rxf::core::doc {

using object::Image;
using object::Status;
using Handle = pymergetic_rxf_handle;

/* The locator a citation carries: where the cited range sits in its source
 * file, and optionally the content hash of that file. */
struct Locator {
    std::string path;     // the source file the citation names
    uint32_t first_line = 0;
    uint32_t last_line = 0;
    std::string sha256;   // 64 lowercase hex digits, or empty (not claimed)
};

/* The canonical record text of a locator, and its parser. Export writes the
 * lines in the order above; parse accepts exactly those keys, each once. */
std::string locator_text(const Locator &locator);
bool parse_locator(std::string_view text, Locator *out, std::string *error);

/* The permalink a citation stands for:
 * `<repository>/blob/<commit>/<path>#L<first>-L<last>`. */
std::string permalink(std::string_view repository, std::string_view commit, const Locator &locator);

/* The per-import provenance level (`--provenance=full|cite|hash`). The floor is
 * hash: "" and words below it ("none") are refused, and so is a word the format
 * does not name. */
enum class Tier : uint32_t { kHash = 0, kCite = 1, kFull = 2 };
bool tier_of(std::string_view word, Tier *out, std::string *error);
const char *tier_name(Tier tier);

/* The origin record of one source set (docs/22): upstream name, repository
 * permalink, pinned commit, license, importer version. `upstream`,
 * `repository`, and `commit` are the identity and must be given; `license` and
 * `importer` are omitted from the record when empty. */
struct Origin {
    std::string upstream;
    std::string repository;
    std::string commit;
    std::string license;
    std::string importer; /* the version of the importer that wrote the run */
};

/* One fact: a Doc object below `parent`, its text stored in the codec `codec`
 * asks for (compressed where it pays). `kind`, `status`, and `codec` are the
 * words of docs/22 (DocKind, DocStatus, DocCodec); an unnamed value is refused. */
Status add(Image &image, Handle parent, std::string_view name, uint32_t kind, uint32_t status, uint32_t codec,
           std::string_view text, Handle *out, std::string *error);

/* The editorial placement (docs/22): a RefKind.Doc reference from `source` onto
 * the Doc `doc`, carrying the DocRole `role`. It takes no runtime ref slot, and
 * a role that names a topic link (Parent, Related) may leave only a Doc, at most
 * one parent, without a cycle. */
Status place(Image &image, Handle source, Handle doc, uint32_t role, std::string *error);

/* Comment lifting: a comment or docstring becomes a Doc (lz4 where it pays)
 * below the object it documents, placed on it (RefKind.Doc). This is what the
 * importer calls for every comment it lifts. */
Status lift_comment(Image &image, Handle target, std::string_view name, std::string_view text, uint32_t kind,
                    uint32_t status, uint32_t role, Handle *out, std::string *error);

/* The origin record of a source set: one Doc per source set, written once and
 * shared by every object it produced. Written from `cite` up; refused at hash
 * (hash is identity without location). */
Status origin(Image &image, Handle parent, std::string_view name, Tier tier, const Origin &facts, Handle *out,
              std::string *error);

/* The imported source itself: one lz4 Doc per source file, one stored payload
 * behind many placements. Written only at `full`; `origin` (may be kNone) is
 * the origin record the source belongs to (its Parent topic link). */
Status source(Image &image, Handle parent, std::string_view name, Tier tier, std::string_view text, Handle origin,
              Handle *out, std::string *error);

/* The content SHA of an imported source file: the only fact at `hash` --
 * identity without location, one hash per file, shared by every object of it.
 * Written only at hash; from `cite` up the hash rides in the citation's locator. */
Status hash(Image &image, Handle parent, std::string_view name, Tier tier, std::string_view sha256, Handle *out,
            std::string *error);

/* One citation: the locator of the range an imported object came from, as a
 * small Doc whose text tail is the locator (the seam). Placed on every object
 * of `placed_on` (RefKind.Doc, References) and referencing every provenance
 * fact it cites (the file's source Doc at full, the origin record at cite).
 * Written from `cite` up; refused at hash (a locator is location). */
Status cite(Image &image, Handle parent, std::string_view name, Tier tier, const Locator &locator,
            const std::vector<Handle> &placed_on, const std::vector<Handle> &provenance, Handle *out,
            std::string *error);

/* The locator a citing Doc carries (its text tail). BadImage when the text is
 * not a locator record. */
Status locator_of(const Image &image, Handle citation, Locator *out, std::string *error);

/* The permalink the citation stands for, from its locator and the origin record
 * it cites. BadName when it cites no origin record. */
Status citation_permalink(const Image &image, Handle citation, std::string *out, std::string *error);

} // namespace pymergetic::rxf::core::doc

#endif
