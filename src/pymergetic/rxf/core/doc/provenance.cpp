#include <pymergetic/rxf/core/doc/provenance.hpp>

#include <charconv>
#include <cstring>

#include <pymergetic/rxf/core/doc/codec.hpp>
#include <pymergetic/rxf/core/kinds/kinds.hpp>
#include <pymergetic/rxf/core/meta/types.hpp>

namespace pymergetic::rxf::core::doc {

namespace {

using Handle = pymergetic_rxf_handle;
constexpr Handle kNone = PYMERGETIC_RXF_HANDLE_NONE;

bool refuse(std::string *error, std::string message) {
    if (error != nullptr) *error = std::move(message);
    return false;
}

Status denied(std::string *error, std::string message) {
    refuse(error, std::move(message));
    return Status::BadName;
}

bool number_of(std::string_view text, uint32_t *out) {
    const char *b = text.data(), *e = b + text.size();
    uint64_t v = 0;
    const auto r = std::from_chars(b, e, v);
    if (text.empty() || r.ec != std::errc() || r.ptr != e || v > 0xFFFFFFFFULL) return false;
    *out = static_cast<uint32_t>(v);
    return true;
}

/* A record value is one line of text: no line break, no NUL, no control character. */
bool good_value(std::string_view value) {
    if (value.find('\0') != std::string_view::npos) return false;
    for (const char c : value) {
        const uint8_t u = static_cast<uint8_t>(c);
        if (u < 0x20 || u == 0x7F) return false;
    }
    return true;
}

/* A content hash: 64 lowercase hex digits, the form every writer emits. */
bool good_sha256(std::string_view text) {
    if (text.size() != 64) return false;
    for (const char c : text)
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}

/* One `key=value` line of a record; a record is its lines joined by '\n', no trailing newline. */
void line(std::string *out, std::string_view key, std::string_view value) {
    if (!out->empty()) *out += '\n';
    out->append(key);
    out->append("=");
    out->append(value);
}

/* The value of `key` in a record, false when the record holds no such line. */
bool record_field(std::string_view text, std::string_view key, std::string *out) {
    size_t at = 0;
    while (at <= text.size()) {
        size_t end = text.find('\n', at);
        if (end == std::string_view::npos) end = text.size();
        const std::string_view row = text.substr(at, end - at);
        const size_t eq = row.find('=');
        if (eq != std::string_view::npos && row.substr(0, eq) == key) {
            *out = std::string(row.substr(eq + 1));
            return true;
        }
        if (end == text.size()) break;
        at = end + 1;
    }
    return false;
}

Handle doc_type_of(const Image &image, std::string *error) {
    const Handle type = image.resolve(kinds::kDocType);
    if (type == kNone) {
        refuse(error, "the image has no Doc type");
        return kNone;
    }
    return type;
}

/* The handle of a doc role (`DocRole.Narrates`), kNone when the value is not one. */
Handle role_of(const Image &image, uint32_t role, std::string *error) {
    const char *name = kinds::name_of(kinds::doc_roles, role);
    if (name == nullptr) {
        refuse(error, "unknown DocRole value " + std::to_string(role));
        return kNone;
    }
    const Handle found = image.resolve(std::string(kinds::kDocRoleType) + "." + name);
    if (found == kNone) refuse(error, "the image has no DocRole." + std::string(name));
    return found;
}

bool named_words(uint32_t kind, uint32_t status, uint32_t codec, std::string *error) {
    if (kinds::name_of(kinds::doc_kinds, kind) == nullptr) return refuse(error, "unknown DocKind value " + std::to_string(kind));
    if (kinds::name_of(kinds::doc_statuses, status) == nullptr)
        return refuse(error, "unknown DocStatus value " + std::to_string(status));
    if (kinds::name_of(kinds::doc_codecs, codec) == nullptr) return refuse(error, "unknown DocCodec value " + std::to_string(codec));
    return true;
}

/* The one fact: a Doc object below `parent`, its text in the codec `codec` asks for. */
Status make(Image &image, Handle parent, std::string_view name, uint32_t kind, uint32_t status, uint32_t codec,
            std::string_view text, Handle *out, std::string *error) {
    if (name.empty()) return denied(error, "a doc needs a name");
    if (!named_words(kind, status, codec, error)) return Status::BadName;
    if (!valid_utf8(text) || text.find('\0') != std::string_view::npos)
        return denied(error, "the text of the doc is not valid UTF-8 without NUL bytes");
    const Handle type = doc_type_of(image, error);
    if (type == kNone) return Status::BadPath;
    if (!image.arena().live(parent)) return denied(error, "the parent is not a live object");
    if (meta::is_ground(image, parent)) return denied(error, "the ground cannot have new children");
    const Stored stored = store(codec, text);
    std::vector<uint8_t> payload(sizeof(pymergetic_rxf_doc) + stored.bytes.size());
    const pymergetic_rxf_doc words{kind, status, stored.codec};
    std::memcpy(payload.data(), &words, sizeof(words));
    if (!stored.bytes.empty()) std::memcpy(payload.data() + sizeof(words), stored.bytes.data(), stored.bytes.size());
    object::ObjectSpec spec;
    spec.type = type;
    spec.parent = parent;
    spec.name = std::string(name);
    spec.kind = PYMERGETIC_RXF_NODE_DATA;
    spec.state = object::image_state();
    spec.payload = std::move(payload);
    return image.add(spec, out);
}

/* The RefKind.Doc edge a placement is, and the rules it must follow. */
bool placement_ref(const Image &image, Handle source, Handle doc, uint32_t role, pymergetic_rxf_ref *out,
                   std::string *error) {
    if (!image.arena().live(source)) return refuse(error, "the source of the placement is not a live object");
    const Handle type = doc_type_of(image, error);
    if (type == kNone) return false;
    const pymergetic_rxf_cell *target = image.arena().cell(doc);
    if (target == nullptr) return refuse(error, "the doc is not a live object");
    if (target->type != type) return refuse(error, "a doc edge must name a Doc object");
    const Handle role_handle = role_of(image, role, error);
    if (role_handle == kNone) return false;
    const pymergetic_rxf_cell *from = image.arena().cell(source);
    if ((role == PYMERGETIC_RXF_DOC_ROLE_PARENT || role == PYMERGETIC_RXF_DOC_ROLE_RELATED) && from->type != type)
        return refuse(error, "a topic link (Parent or Related) leaves a Doc object");
    out->target = doc;
    out->role = role_handle;
    out->kind = PYMERGETIC_RXF_REF_DOC;
    out->binding = PYMERGETIC_RXF_BINDING_MANDATORY;
    out->index = 0;
    return true;
}

/* The Parent role and the rules of the containment it records (docs/22). */
bool parent_rules(const Image &image, Handle source, Handle doc, std::string *error) {
    const Handle parent_role = image.resolve(std::string(kinds::kDocRoleType) + ".Parent");
    const pymergetic_rxf_cell *cell = image.arena().cell(source);
    const pymergetic_rxf_ref *refs = image.arena().refs(source);
    for (uint32_t i = 0; i < cell->ref_count; i++)
        if (refs[i].kind == PYMERGETIC_RXF_REF_DOC && refs[i].role == parent_role)
            return refuse(error, "a topic has more than one parent");
    for (Handle at = doc; at != kNone;) {
        if (at == source) return refuse(error, "the parent link of topics is acyclic");
        const pymergetic_rxf_cell *c = image.arena().cell(at);
        Handle parent = kNone;
        const pymergetic_rxf_ref *rs = image.arena().refs(at);
        for (uint32_t i = 0; c != nullptr && i < c->ref_count; i++)
            if (rs[i].kind == PYMERGETIC_RXF_REF_DOC && rs[i].role == parent_role) parent = rs[i].target;
        at = parent;
    }
    return true;
}

} // namespace

std::string locator_text(const Locator &locator) {
    std::string out;
    line(&out, "path", locator.path);
    line(&out, "lines", std::to_string(locator.first_line) + "-" + std::to_string(locator.last_line));
    if (!locator.sha256.empty()) line(&out, "sha256", locator.sha256);
    return out;
}

bool parse_locator(std::string_view text, Locator *out, std::string *error) {
    Locator found;
    bool seen_path = false, seen_lines = false, seen_sha = false;
    size_t at = 0;
    while (at <= text.size()) {
        size_t end = text.find('\n', at);
        if (end == std::string_view::npos) end = text.size();
        const std::string_view row = text.substr(at, end - at);
        const size_t eq = row.find('=');
        if (eq == std::string_view::npos) return refuse(error, "the locator is not a record of key=value lines");
        const std::string_view key = row.substr(0, eq), value = row.substr(eq + 1);
        if (key == "path") {
            if (seen_path) return refuse(error, "the locator names its path twice");
            if (value.empty() || !good_value(value)) return refuse(error, "the locator path is not one line of text");
            found.path = std::string(value);
            seen_path = true;
        } else if (key == "lines") {
            if (seen_lines) return refuse(error, "the locator names its lines twice");
            const size_t dash = value.find('-');
            uint32_t first = 0, last = 0;
            if (dash == std::string_view::npos || !number_of(value.substr(0, dash), &first) ||
                !number_of(value.substr(dash + 1), &last) || first == 0 || last < first)
                return refuse(error, "the locator lines are not a line range (first-last)");
            found.first_line = first;
            found.last_line = last;
            seen_lines = true;
        } else if (key == "sha256") {
            if (seen_sha) return refuse(error, "the locator names its hash twice");
            if (!good_sha256(value)) return refuse(error, "the locator hash is not 64 lowercase hex digits");
            found.sha256 = std::string(value);
            seen_sha = true;
        } else {
            return refuse(error, "the locator has the unknown key " + std::string(key));
        }
        if (end == text.size()) break;
        at = end + 1;
    }
    if (!seen_path || !seen_lines) return refuse(error, "a locator names its path and its lines");
    *out = std::move(found);
    return true;
}

std::string permalink(std::string_view repository, std::string_view commit, const Locator &locator) {
    std::string out(repository);
    out += "/blob/";
    out += commit;
    out += "/";
    out += locator.path;
    out += "#L" + std::to_string(locator.first_line) + "-L" + std::to_string(locator.last_line);
    return out;
}

bool tier_of(std::string_view word, Tier *out, std::string *error) {
    if (word == "full") {
        *out = Tier::kFull;
        return true;
    }
    if (word == "cite") {
        *out = Tier::kCite;
        return true;
    }
    if (word == "hash") {
        *out = Tier::kHash;
        return true;
    }
    if (word.empty() || word == "none" || word == "off" || word == "no")
        return refuse(error, "the provenance floor is hash: the importer takes no anonymous input");
    return refuse(error,
                  "the provenance floor is hash: \"" + std::string(word) + "\" is not a provenance tier (full, cite, or hash)");
}

const char *tier_name(Tier tier) {
    switch (tier) {
    case Tier::kFull: return "full";
    case Tier::kCite: return "cite";
    case Tier::kHash: return "hash";
    }
    return "";
}

Status add(Image &image, Handle parent, std::string_view name, uint32_t kind, uint32_t status, uint32_t codec,
           std::string_view text, Handle *out, std::string *error) {
    return make(image, parent, name, kind, status, codec, text, out, error);
}

Status place(Image &image, Handle source, Handle doc, uint32_t role, std::string *error) {
    pymergetic_rxf_ref added{};
    if (!placement_ref(image, source, doc, role, &added, error)) return Status::BadName;
    const pymergetic_rxf_cell *cell = image.arena().cell(source);
    const pymergetic_rxf_ref *old = image.arena().refs(source);
    uint32_t first_other = 0;
    for (uint32_t i = 0; i < cell->ref_count; i++) {
        if (old[i].target == added.target && old[i].role == added.role && old[i].kind == added.kind)
            return denied(error, "this placement is already recorded (one edge, said once)");
        if (old[i].kind != PYMERGETIC_RXF_REF_DOC) break;
        first_other = i + 1;
    }
    // The duplicate scan is over every reference; only the leading placements stay in front.
    for (uint32_t i = first_other; i < cell->ref_count; i++)
        if (old[i].target == added.target && old[i].role == added.role && old[i].kind == added.kind)
            return denied(error, "this placement is already recorded (one edge, said once)");
    if (role == PYMERGETIC_RXF_DOC_ROLE_PARENT && !parent_rules(image, source, doc, error)) return Status::BadName;
    std::vector<pymergetic_rxf_ref> refs;
    refs.insert(refs.end(), old, old + first_other);
    refs.push_back(added);
    refs.insert(refs.end(), old + first_other, old + cell->ref_count);
    return image.set_refs(source, refs.data(), static_cast<uint32_t>(refs.size()));
}

Status lift_comment(Image &image, Handle target, std::string_view name, std::string_view text, uint32_t kind,
                    uint32_t status, uint32_t role, Handle *out, std::string *error) {
    if (text.empty()) return denied(error, "a lifted comment is not empty");
    if (!image.arena().live(target)) return denied(error, "the object the comment documents is not a live object");
    Status s = make(image, target, name, kind, status, PYMERGETIC_RXF_DOC_CODEC_LZ4, text, out, error);
    if (s != Status::Ok) return s;
    s = place(image, target, *out, role, error);
    if (s != Status::Ok) image.remove(*out);
    return s;
}

Status origin(Image &image, Handle parent, std::string_view name, Tier tier, const Origin &facts, Handle *out,
              std::string *error) {
    if (tier == Tier::kHash)
        return denied(error, "an origin record is written from cite up (docs/22): hash is identity without location");
    if (facts.upstream.empty() || facts.repository.empty() || facts.commit.empty())
        return denied(error, "an origin record names its upstream, its repository, and its pinned commit");
    for (const std::string *value : {&facts.upstream, &facts.repository, &facts.commit, &facts.license, &facts.importer})
        if (!good_value(*value)) return denied(error, "an origin record is one line per fact");
    std::string text;
    line(&text, "tier", tier_name(tier));
    line(&text, "upstream", facts.upstream);
    line(&text, "repository", facts.repository);
    line(&text, "commit", facts.commit);
    if (!facts.license.empty()) line(&text, "license", facts.license);
    if (!facts.importer.empty()) line(&text, "importer", facts.importer);
    return make(image, parent, name, PYMERGETIC_RXF_DOC_KIND_TOPIC, PYMERGETIC_RXF_DOC_STATUS_OPEN,
                PYMERGETIC_RXF_DOC_CODEC_LZ4, text, out, error);
}

Status source(Image &image, Handle parent, std::string_view name, Tier tier, std::string_view text, Handle origin,
              Handle *out, std::string *error) {
    if (tier != Tier::kFull)
        return denied(error, "the source itself is written only at full (docs/22): what is stored is what the tier grants");
    Status s = make(image, parent, name, PYMERGETIC_RXF_DOC_KIND_NARRATES, PYMERGETIC_RXF_DOC_STATUS_OPEN,
                    PYMERGETIC_RXF_DOC_CODEC_LZ4, text, out, error);
    if (s != Status::Ok) return s;
    if (origin != kNone) {
        s = place(image, *out, origin, PYMERGETIC_RXF_DOC_ROLE_PARENT, error);
        if (s != Status::Ok) image.remove(*out);
    }
    return s;
}

Status hash(Image &image, Handle parent, std::string_view name, Tier tier, std::string_view sha256, Handle *out,
            std::string *error) {
    if (tier != Tier::kHash)
        return denied(error,
                      "a hash record is written only at hash (docs/22): from cite up the hash rides in the citation's locator");
    if (!good_sha256(sha256)) return denied(error, "a content SHA is 64 lowercase hex digits");
    std::string text;
    line(&text, "tier", "hash");
    line(&text, "sha256", sha256);
    // The only fact at the floor: identity without location, tiny, stored raw.
    return make(image, parent, name, PYMERGETIC_RXF_DOC_KIND_NARRATES, PYMERGETIC_RXF_DOC_STATUS_OPEN,
                PYMERGETIC_RXF_DOC_CODEC_RAW, text, out, error);
}

Status cite(Image &image, Handle parent, std::string_view name, Tier tier, const Locator &locator,
            const std::vector<Handle> &placed_on, const std::vector<Handle> &provenance, Handle *out,
            std::string *error) {
    if (tier == Tier::kHash) return denied(error, "a citation is written from cite up (docs/22): hash carries no location");
    if (provenance.empty()) return denied(error, "a citation cites at least one provenance fact");
    if (placed_on.empty()) return denied(error, "a citation is placed on at least one object");
    if (locator.path.empty() || !good_value(locator.path)) return denied(error, "a locator path is one line of text");
    if (locator.first_line == 0 || locator.last_line < locator.first_line)
        return denied(error, "a locator names a line range (first-last), first at least 1");
    if (!locator.sha256.empty() && !good_sha256(locator.sha256))
        return denied(error, "a locator hash is 64 lowercase hex digits");
    Status s = make(image, parent, name, PYMERGETIC_RXF_DOC_KIND_NARRATES, PYMERGETIC_RXF_DOC_STATUS_OPEN,
                    PYMERGETIC_RXF_DOC_CODEC_RAW, locator_text(locator), out, error);
    if (s != Status::Ok) return s;
    for (size_t i = 0; s == Status::Ok && i < placed_on.size(); i++)
        s = place(image, placed_on[i], *out, PYMERGETIC_RXF_DOC_ROLE_REFERENCES, error);
    for (size_t i = 0; s == Status::Ok && i < provenance.size(); i++)
        s = place(image, *out, provenance[i], PYMERGETIC_RXF_DOC_ROLE_REFERENCES, error);
    if (s != Status::Ok) image.remove(*out);
    return s;
}

Status locator_of(const Image &image, Handle citation, Locator *out, std::string *error) {
    meta::Types types(&image);
    std::string text;
    const Status s = meta::doc_text(types, citation, &text, error);
    if (s != Status::Ok) return s;
    return parse_locator(text, out, error) ? Status::Ok : Status::BadImage;
}

Status citation_permalink(const Image &image, Handle citation, std::string *out, std::string *error) {
    Locator locator;
    Status s = locator_of(image, citation, &locator, error);
    if (s != Status::Ok) return s;
    meta::Types types(&image);
    const pymergetic_rxf_cell *cell = image.arena().cell(citation);
    const pymergetic_rxf_ref *refs = image.arena().refs(citation);
    for (uint32_t i = 0; cell != nullptr && i < cell->ref_count; i++) {
        if (refs[i].kind != PYMERGETIC_RXF_REF_DOC || refs[i].target == kNone) continue;
        std::string text, ignored;
        if (meta::doc_text(types, refs[i].target, &text, &ignored) != Status::Ok) continue;
        std::string repository, commit;
        if (!record_field(text, "repository", &repository) || !record_field(text, "commit", &commit)) continue;
        *out = permalink(repository, commit, locator);
        return Status::Ok;
    }
    return denied(error, "the citation cites no origin record");
}

} // namespace pymergetic::rxf::core::doc
