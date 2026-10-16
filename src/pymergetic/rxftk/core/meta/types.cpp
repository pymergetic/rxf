#include <pymergetic/rxftk/core/meta/types.hpp>

#include <pymergetic/rxftk/core/doc/codec.hpp>
#include <pymergetic/rxftk/core/kinds/kinds.hpp>

#include <algorithm>
#include <charconv>
#include <cstring>
#include <map>
#include <set>

static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "payloads are little-endian; a big-endian host needs byte swapping");

namespace pymergetic::rxf::core::meta {

namespace {

constexpr uint32_t kTypeTag = 0x54595045;  // "TYPE"
constexpr uint32_t kFieldTag = 0x464C4420; // "FLD "
constexpr int kMaxDepth = 6;
constexpr size_t kMaxLeaves = 1U << 16;          // values one payload may be split into
constexpr uint64_t kMaxFieldExtent = 1ULL << 31; // offset, count and element size of one Field

bool is_pow2(uint64_t v) {
    return v != 0 && (v & (v - 1)) == 0;
}

uint64_t load_uint(const uint8_t *p, uint64_t size) {
    uint64_t v = 0;
    std::memcpy(&v, p, size); // little-endian host
    return v;
}

void store_uint(uint8_t *p, uint64_t size, uint64_t v) {
    std::memcpy(p, &v, size);
}

bool all_zero(const uint8_t *p, uint64_t n) {
    for (uint64_t i = 0; i < n; i++)
        if (p[i] != 0)
            return false;
    return true;
}

} // namespace

std::string hex_of(const uint8_t *p, uint64_t n) {
    static const char digits[] = "0123456789abcdef";
    std::string out;
    out.reserve(n * 2);
    for (uint64_t i = 0; i < n; i++) {
        out += digits[p[i] >> 4];
        out += digits[p[i] & 15];
    }
    return out;
}

bool from_hex(const std::string &text, std::vector<uint8_t> *out) {
    if (text.size() % 2 != 0)
        return false;
    out->clear();
    out->reserve(text.size() / 2);
    const auto nibble = [](char c) -> int {
        if (c >= '0' && c <= '9')
            return c - '0';
        if (c >= 'a' && c <= 'f')
            return c - 'a' + 10;
        if (c >= 'A' && c <= 'F')
            return c - 'A' + 10;
        return -1;
    };
    for (size_t i = 0; i < text.size(); i += 2) {
        const int hi = nibble(text[i]), lo = nibble(text[i + 1]);
        if (hi < 0 || lo < 0)
            return false;
        out->push_back(static_cast<uint8_t>(hi * 16 + lo));
    }
    return true;
}

bool is_symbol(const std::string &n) {
    if (n.empty())
        return false;
    const auto start = [](char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
    };
    if (!start(n[0]))
        return false;
    for (char c : n)
        if (!(start(c) || (c >= '0' && c <= '9') || c == '-' || c == '.'))
            return false;
    return true;
}

bool is_reserved_attribute(const std::string &n) {
    static const char *const names[] = {"name", "path"};
    for (const char *r : names)
        if (n == r)
            return true;
    return false;
}

bool is_reserved_element(const std::string &n) {
    static const char *const names[] = {"delta"};
    for (const char *r : names)
        if (n == r)
            return true;
    return false;
}

// ---- Types ----------------------------------------------------------------

void Types::invalidate() {
    info_.clear();
    layouts_.clear();
    by_name_.clear();
    duplicates_.clear();
    names_valid_ = false;
    enums_.clear();
}

const TypeInfo *Types::get(Handle type) {
    const auto hit = info_.find(type);
    if (hit != info_.end())
        return &hit->second;
    const arena::Arena &arena = image_->arena();
    const pymergetic_rxf_cell *cell = arena.cell(type);
    if (cell == nullptr || cell->type != PYMERGETIC_RXF_HANDLE_TYPE ||
        cell->payload_size != sizeof(pymergetic_rxf_type))
        return nullptr;
    pymergetic_rxf_type raw;
    std::memcpy(&raw, arena.payload(type), sizeof(raw));
    if (raw.descriptor_tag != kTypeTag)
        return nullptr;
    TypeInfo t;
    t.id = type;
    t.name = arena.name(type);
    t.form = raw.form;
    t.size = raw.size;
    t.align = raw.align;
    t.flags = raw.flags;
    t.field_count = raw.field_count;
    t.node_kind = raw.node_kind;
    t.inner = raw.inner_type;
    t.ret = raw.return_type;
    for (Handle child : image_->children(type)) {
        const pymergetic_rxf_cell *fc = arena.cell(child);
        if (fc == nullptr || fc->type != PYMERGETIC_RXF_HANDLE_FIELD ||
            fc->payload_size != sizeof(pymergetic_rxf_field))
            continue;
        pymergetic_rxf_field f;
        std::memcpy(&f, arena.payload(child), sizeof(f));
        if (f.descriptor_tag != kFieldTag)
            continue;
        FieldInfo info;
        info.id = child;
        info.name = arena.name(child);
        info.value_type = f.value_type;
        info.offset = f.offset;
        info.count = f.count;
        info.flags = f.flags;
        info.via = f.via;
        (info.is_member() ? t.members : t.fields).push_back(std::move(info));
    }
    std::stable_sort(t.fields.begin(), t.fields.end(), [](const FieldInfo &a, const FieldInfo &b) {
        return a.offset != b.offset ? a.offset < b.offset : a.name < b.name;
    });
    std::stable_sort(t.members.begin(), t.members.end(),
                     [](const FieldInfo &a, const FieldInfo &b) { return a.name < b.name; });
    return &info_.emplace(type, std::move(t)).first->second;
}

void Types::build_names() {
    by_name_.clear();
    duplicates_.clear();
    const arena::Arena &arena = image_->arena();
    std::set<std::string> seen_dup;
    for (Handle h = 0; h < arena.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr || c->type != PYMERGETIC_RXF_HANDLE_TYPE)
            continue;
        const std::string name = arena.name(h);
        if (!by_name_.emplace(name, h).second && seen_dup.insert(name).second)
            duplicates_.push_back(name);
    }
    names_valid_ = true;
}

Handle Types::find(const std::string &name) {
    if (!names_valid_)
        build_names();
    const auto it = by_name_.find(name);
    return it == by_name_.end() ? kNone : it->second;
}

std::vector<std::string> Types::duplicate_names() {
    if (!names_valid_)
        build_names();
    return duplicates_;
}

const Layout &Types::layout(Handle type) {
    const auto hit = layouts_.find(type);
    if (hit != layouts_.end())
        return hit->second;
    Layout L;
    if (!build_layout(type, &L, 0)) {
        L.leaves.clear();
        L.raw = false;
    } else if (L.prefix > image_->limit()) {
        // No object of this type could exist; do not size anything by it.
        L.leaves.clear();
        L.raw = false;
        L.error = "the size of the type exceeds the limit of the image";
    }
    return layouts_.emplace(type, std::move(L)).first->second;
}

const EnumInfo *Types::enumeration(Handle type) {
    const auto hit = enums_.find(type);
    if (hit != enums_.end())
        return &hit->second;
    const TypeInfo *t = get(type);
    if (t == nullptr || t->form != kFormEnum)
        return nullptr;
    EnumInfo info;
    info.bits = t->bitset();
    const arena::Arena &arena = image_->arena();
    for (Handle child : image_->children(type)) {
        const pymergetic_rxf_cell *c = arena.cell(child);
        if (c == nullptr ||
            ((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK) !=
                PYMERGETIC_RXF_NODE_ENUMERATOR ||
            c->payload_size != sizeof(uint64_t))
            continue;
        info.items.emplace_back(arena.name(child),
                                load_uint(arena.payload(child), sizeof(uint64_t)));
    }
    std::stable_sort(info.items.begin(), info.items.end(), [](const auto &a, const auto &b) {
        return a.second != b.second ? a.second < b.second : a.first < b.first;
    });
    return &enums_.emplace(type, std::move(info)).first->second;
}

namespace {

LeafKind scalar_kind(const TypeInfo &v) {
    switch (v.form) {
    case kFormEnum:
        return LeafKind::Enum;
    case kFormUint:
        return LeafKind::Uint;
    case kFormSint:
        return LeafKind::Sint;
    case kFormBool:
        return LeafKind::Bool;
    case kFormFloat:
        return LeafKind::Float;
    default:
        return LeafKind::Hex;
    }
}

bool int_size(uint64_t s) {
    return s == 1 || s == 2 || s == 4 || s == 8;
}

} // namespace

// Adds the leaves of one field. Returns false and sets `error` when the field cannot be used.
static bool add_leaves(Types &types, const FieldInfo &f, const std::string &prefix, uint64_t base,
                       std::vector<Leaf> *out, std::string *error, bool &incomplete, int depth,
                       bool top_level_of_type, Handle owner) {
    const TypeInfo *v = types.get(f.value_type);
    if (v == nullptr) {
        *error = "field " + prefix + f.name + " has an unknown value type";
        incomplete = true;
        return false;
    }
    if (f.count == 0) {
        *error = "field " + prefix + f.name + " has a zero count";
        return false;
    }
    if (f.offset > kMaxFieldExtent || f.count > kMaxFieldExtent || v->size > kMaxFieldExtent) {
        *error = "field " + prefix + f.name + " is out of range";
        return false;
    }
    if (out->size() >= kMaxLeaves) {
        *error = "field " + prefix + f.name + ": the type has too many values";
        return false;
    }
    // Written by the machine, never by an author: the tags, the number of Field children, and the
    // owner of a Field (its parent).
    const bool derived =
        top_level_of_type && ((owner == PYMERGETIC_RXF_HANDLE_TYPE &&
                               (f.name == "field_count" || f.name == "descriptor_tag")) ||
                              (owner == PYMERGETIC_RXF_HANDLE_FIELD &&
                               (f.name == "descriptor_tag" || f.name == "owner_type")));
    if (f.flags & PYMERGETIC_RXF_FIELD_FLAG_REF) {
        if (v->form != kFormUint || v->size != 8 || f.count != 1) {
            *error = "reference field " + prefix + f.name + " must be one uint64";
            return false;
        }
        Leaf leaf;
        leaf.name = prefix + f.name;
        leaf.offset = base + f.offset;
        leaf.size = 8;
        leaf.kind = LeafKind::Ref;
        leaf.optional = (f.flags & PYMERGETIC_RXF_FIELD_FLAG_OPTIONAL) != 0;
        leaf.derived = derived;
        out->push_back(std::move(leaf));
        return true;
    }
    const bool nested = v->form == kFormStruct && !v->fields.empty();
    if (nested) {
        if (depth >= kMaxDepth) {
            *error = "field " + prefix + f.name + " nests too deeply";
            return false;
        }
        for (uint64_t i = 0; i < f.count; i++) {
            if (out->size() >= kMaxLeaves) {
                *error = "field " + prefix + f.name + ": the type has too many values";
                return false;
            }
            const std::string name =
                prefix + f.name + (f.count > 1 ? "." + std::to_string(i) : std::string()) + ".";
            for (const FieldInfo &g : v->fields)
                if (!add_leaves(types, g, name, base + f.offset + i * v->size, out, error,
                                incomplete, depth + 1, false, v->id))
                    return false;
        }
        return true;
    }
    LeafKind kind = scalar_kind(*v);
    if (v->size == 0) {
        *error = "field " + prefix + f.name + " embeds a variable-size type";
        return false;
    }
    if (kind == LeafKind::Float && v->size != 4 && v->size != 8)
        kind = LeafKind::Hex;
    if (kind != LeafKind::Hex && !int_size(v->size) && kind != LeafKind::Float)
        kind = LeafKind::Hex;
    if (kind == LeafKind::Bool && v->size != 1)
        kind = LeafKind::Hex;
    // An array of bytes is one hex string.
    if (f.count > 1 && v->size == 1 && (kind == LeafKind::Uint || kind == LeafKind::Hex)) {
        Leaf leaf;
        leaf.name = prefix + f.name;
        leaf.offset = base + f.offset;
        leaf.size = f.count;
        leaf.kind = LeafKind::Hex;
        out->push_back(std::move(leaf));
        return true;
    }
    if (kind == LeafKind::Hex) {
        Leaf leaf;
        leaf.name = prefix + f.name;
        leaf.offset = base + f.offset;
        leaf.size = v->size * f.count;
        leaf.kind = LeafKind::Hex;
        out->push_back(std::move(leaf));
        return true;
    }
    for (uint64_t i = 0; i < f.count; i++) {
        if (out->size() >= kMaxLeaves) {
            *error = "field " + prefix + f.name + ": the type has too many values";
            return false;
        }
        Leaf leaf;
        leaf.name = prefix + f.name + (f.count > 1 ? "." + std::to_string(i) : std::string());
        leaf.offset = base + f.offset + i * v->size;
        leaf.size = v->size;
        leaf.kind = kind;
        leaf.derived = derived;
        if (kind == LeafKind::Enum)
            leaf.enumeration = v->id;
        if (top_level_of_type && owner == PYMERGETIC_RXF_HANDLE_FIELD && f.name == "count")
            leaf.def = 1;
        out->push_back(std::move(leaf));
    }
    return true;
}

bool Types::build_layout(Handle type, Layout *L, int) {
    const TypeInfo *t = get(type);
    if (t == nullptr) {
        L->error = "not a Type object";
        return false;
    }
    const bool fixed_size = t->size != 0 && !t->variable();
    if (t->form == kFormUnion || t->fields.empty()) {
        L->raw = true;
        L->fixed = fixed_size;
        L->prefix = fixed_size ? t->size : 0;
        return true;
    }
    for (const FieldInfo &f : t->fields)
        if (!add_leaves(*this, f, "", 0, &L->leaves, &L->error, L->incomplete, 0, true, type))
            return false;
    std::stable_sort(L->leaves.begin(), L->leaves.end(),
                     [](const Leaf &a, const Leaf &b) { return a.offset < b.offset; });
    uint64_t end = 0;
    for (const Leaf &leaf : L->leaves) {
        if (leaf.offset < end) { // overlapping fields: a union in disguise, read as raw bytes
            L->leaves.clear();
            L->raw = true;
            L->fixed = fixed_size;
            L->prefix = fixed_size ? t->size : 0;
            return true;
        }
        end = leaf.offset + leaf.size;
    }
    L->prefix = std::max<uint64_t>(fixed_size ? t->size : 0, end);
    if (t->size != 0 && end > t->size && !t->variable()) {
        L->error = "fields exceed the size of the type";
        return false;
    }
    L->fixed = !t->variable();
    return true;
}

// ---- codec ----------------------------------------------------------------

namespace {

std::string format_leaf(Types &types, const Leaf &leaf, const uint8_t *p, bool *omit,
                        std::string *error) {
    *omit = false;
    switch (leaf.kind) {
    case LeafKind::Uint:
    case LeafKind::Bool: {
        const uint64_t v = load_uint(p, leaf.size);
        if (v == leaf.def)
            *omit = true;
        return std::to_string(v);
    }
    case LeafKind::Sint: {
        uint64_t v = load_uint(p, leaf.size);
        if (v == 0)
            *omit = true;
        const uint64_t sign = 1ULL << (leaf.size * 8 - 1);
        int64_t s =
            leaf.size == 8 ? static_cast<int64_t>(v) : static_cast<int64_t>((v ^ sign) - sign);
        return std::to_string(s);
    }
    case LeafKind::Float: {
        char buf[64];
        std::to_chars_result r;
        if (leaf.size == 4) {
            float f;
            std::memcpy(&f, p, 4);
            uint32_t bits;
            std::memcpy(&bits, p, 4);
            if (bits == 0)
                *omit = true;
            r = std::to_chars(buf, buf + sizeof(buf), f);
        } else {
            double d;
            std::memcpy(&d, p, 8);
            uint64_t bits;
            std::memcpy(&bits, p, 8);
            if (bits == 0)
                *omit = true;
            r = std::to_chars(buf, buf + sizeof(buf), d);
        }
        return std::string(buf, r.ptr);
    }
    case LeafKind::Hex:
        if (all_zero(p, leaf.size))
            *omit = true;
        return hex_of(p, leaf.size);
    case LeafKind::Enum: {
        const uint64_t v = load_uint(p, leaf.size);
        if (v == 0)
            *omit = true;
        const EnumInfo *e = types.enumeration(leaf.enumeration);
        if (e == nullptr) {
            *error = "field " + leaf.name + " is not of an enumeration type";
            return std::string();
        }
        if (v == 0)
            return std::string();
        // The first value is the path of its Enumerator; the rest of a bitset are names of the same
        // enumeration.
        const std::string head = types.image().path(leaf.enumeration) + ".";
        if (!e->bits) {
            for (const auto &item : e->items)
                if (item.second == v)
                    return head + item.first;
        } else {
            std::string text;
            uint64_t rest = v;
            for (const auto &item : e->items) {
                if (item.second == 0 || (item.second & rest) != item.second)
                    continue;
                text += text.empty() ? head : "|";
                text += item.first;
                rest &= ~item.second;
            }
            if (rest == 0)
                return text;
        }
        *error = "field " + leaf.name + " holds " + std::to_string(v) + ", which " +
                 types.image().path(leaf.enumeration) + " does not name";
        return std::string();
    }
    case LeafKind::Ref: {
        const uint64_t h = load_uint(p, 8);
        if (h == kNone) {
            *omit = true;
            return std::string();
        }
        const pymergetic_rxf_cell *c = types.image().arena().cell(h);
        if (c == nullptr) {
            *error = "reference " + leaf.name + " holds a dead handle";
            return std::string();
        }
        return types.image().path(h);
    }
    }
    return std::string();
}

bool parse_uint(const std::string &text, uint64_t *out) {
    const char *b = text.data(), *e = b + text.size();
    if (b == e)
        return false;
    const auto r = std::from_chars(b, e, *out, 10);
    return r.ec == std::errc() && r.ptr == e;
}

bool parse_sint(const std::string &text, int64_t *out) {
    const char *b = text.data(), *e = b + text.size();
    if (b == e)
        return false;
    const auto r = std::from_chars(b, e, *out, 10);
    return r.ec == std::errc() && r.ptr == e;
}

} // namespace

Status decode_payload(Types &types, Handle object, std::vector<Attr> *out, std::string *error) {
    out->clear();
    const arena::Arena &arena = types.image().arena();
    const pymergetic_rxf_cell *cell = arena.cell(object);
    if (cell == nullptr)
        return Status::BadHandle;
    const uint8_t *payload = arena.payload(object);
    const uint64_t size = cell->payload_size;
    if (cell->type == kNone) {
        if (size != 0) {
            *error = "an object without a type has a payload";
            return Status::BadImage;
        }
        return Status::Ok;
    }
    const Layout &L = types.layout(cell->type);
    if (!L.error.empty()) {
        *error = L.error;
        return Status::BadImage;
    }
    if (L.raw) {
        if (L.fixed && size != L.prefix) {
            *error = "payload size does not match its type";
            return Status::BadImage;
        }
        if (size != 0)
            out->push_back({kDataAttr, hex_of(payload, size)});
        return Status::Ok;
    }
    if (L.fixed ? size != L.prefix : size < L.prefix) {
        *error = "payload size does not match its type";
        return Status::BadImage;
    }
    uint64_t at = 0;
    for (const Leaf &leaf : L.leaves) {
        if (!all_zero(payload + at, leaf.offset - at)) {
            *error = "padding is not zero";
            return Status::BadImage;
        }
        at = leaf.offset + leaf.size;
    }
    if (!all_zero(payload + at, L.prefix - at)) {
        *error = "padding is not zero";
        return Status::BadImage;
    }
    for (const Leaf &leaf : L.leaves) {
        if (leaf.derived)
            continue;
        bool omit = false;
        std::string text = format_leaf(types, leaf, payload + leaf.offset, &omit, error);
        if (!error->empty())
            return Status::BadImage;
        if (!omit)
            out->push_back({leaf.name, std::move(text),
                            leaf.kind == LeafKind::Ref || leaf.kind == LeafKind::Enum});
    }
    if (size > L.prefix)
        out->push_back({kDataAttr, hex_of(payload + L.prefix, size - L.prefix)});
    return Status::Ok;
}

Status encode_payload(Types &types, Handle type, const std::vector<Attr> &attrs,
                      const Resolver &resolve, std::vector<uint8_t> *payload,
                      std::vector<PendingRef> *pending, std::string *error, Handle parent) {
    payload->clear();
    std::map<std::string, std::string> given;
    for (const Attr &a : attrs)
        if (!given.emplace(a.name, a.value).second) {
            *error = "attribute " + a.name + " is given twice";
            return Status::BadName;
        }
    const Layout &L = types.layout(type);
    if (!L.error.empty()) {
        *error = L.error;
        return L.incomplete ? Status::Dangling : Status::BadImage;
    }
    std::vector<uint8_t> data;
    const auto data_it = given.find(kDataAttr);
    if (data_it != given.end()) {
        if (!from_hex(data_it->second, &data)) {
            *error = std::string(kDataAttr) + " is not hexadecimal";
            return Status::BadName;
        }
        given.erase(data_it);
    }
    if (L.raw) {
        if (!given.empty()) {
            *error = "unknown attribute " + given.begin()->first;
            return Status::BadName;
        }
        if (L.fixed && data.size() != L.prefix) {
            *error = "payload size does not match its type";
            return Status::BadImage;
        }
        *payload = std::move(data);
        return Status::Ok;
    }
    payload->assign(L.prefix, 0);
    for (const Leaf &leaf : L.leaves) {
        if (leaf.kind == LeafKind::Ref)
            store_uint(payload->data() + leaf.offset, 8, kNone);
        else if (leaf.def != 0)
            store_uint(payload->data() + leaf.offset, leaf.size, leaf.def);
    }
    for (const Leaf &leaf : L.leaves) {
        const auto it = given.find(leaf.name);
        if (leaf.derived) {
            // Derived is never written (docs/27): a value for one is a refusal, not a warning.
            if (it != given.end()) {
                *error = "derived is never written: " + leaf.name;
                return Status::Protected;
            }
            if (leaf.name == "descriptor_tag")
                store_uint(payload->data() + leaf.offset, 4,
                           type == PYMERGETIC_RXF_HANDLE_TYPE ? kTypeTag : kFieldTag);
            else if (leaf.name == "owner_type")
                store_uint(payload->data() + leaf.offset, 8, parent);
            continue;
        }
        if (it == given.end())
            continue;
        const std::string &text = it->second;
        uint8_t *p = payload->data() + leaf.offset;
        switch (leaf.kind) {
        case LeafKind::Uint:
        case LeafKind::Bool: {
            uint64_t v = 0;
            if (leaf.kind == LeafKind::Bool && (text == "true" || text == "false"))
                v = text == "true";
            else if (!parse_uint(text, &v)) {
                *error = "attribute " + leaf.name + " is not an unsigned number";
                return Status::BadName;
            }
            if (leaf.size < 8 && (v >> (leaf.size * 8)) != 0) {
                *error = "attribute " + leaf.name + " does not fit its type";
                return Status::TooLarge;
            }
            store_uint(p, leaf.size, v);
            break;
        }
        case LeafKind::Sint: {
            int64_t v = 0;
            if (!parse_sint(text, &v)) {
                *error = "attribute " + leaf.name + " is not a number";
                return Status::BadName;
            }
            if (leaf.size < 8) {
                const int64_t lo = -(int64_t(1) << (leaf.size * 8 - 1)),
                              hi = (int64_t(1) << (leaf.size * 8 - 1)) - 1;
                if (v < lo || v > hi) {
                    *error = "attribute " + leaf.name + " does not fit its type";
                    return Status::TooLarge;
                }
            }
            store_uint(p, leaf.size, static_cast<uint64_t>(v));
            break;
        }
        case LeafKind::Float: {
            const char *b = text.data(), *e = b + text.size();
            if (leaf.size == 4) {
                float f = 0;
                const auto r = std::from_chars(b, e, f);
                if (r.ec != std::errc() || r.ptr != e) {
                    *error = "attribute " + leaf.name + " is not a number";
                    return Status::BadName;
                }
                std::memcpy(p, &f, 4);
            } else {
                double d = 0;
                const auto r = std::from_chars(b, e, d);
                if (r.ec != std::errc() || r.ptr != e) {
                    *error = "attribute " + leaf.name + " is not a number";
                    return Status::BadName;
                }
                std::memcpy(p, &d, 8);
            }
            break;
        }
        case LeafKind::Hex: {
            std::vector<uint8_t> bytes;
            if (!from_hex(text, &bytes) || bytes.size() != leaf.size) {
                *error = "attribute " + leaf.name + " is not " + std::to_string(leaf.size) +
                         " bytes of hexadecimal";
                return Status::BadName;
            }
            if (!bytes.empty())
                std::memcpy(p, bytes.data(), bytes.size());
            break;
        }
        case LeafKind::Enum: {
            const EnumInfo *e = types.enumeration(leaf.enumeration);
            if (e == nullptr) {
                *error = "attribute " + leaf.name + " is not of an enumeration type";
                return Status::BadName;
            }
            std::vector<std::string> words;
            if (!e->bits)
                words.push_back(text);
            else if (!text.empty())
                for (size_t at = 0;;) {
                    const size_t bar = text.find('|', at);
                    words.push_back(
                        text.substr(at, bar == std::string::npos ? std::string::npos : bar - at));
                    if (bar == std::string::npos)
                        break;
                    at = bar + 1;
                }
            uint64_t v = 0;
            for (size_t w = 0; w < words.size(); w++) {
                std::string name = words[w];
                if (w == 0) {
                    // The first word is the path of an Enumerator of this enumeration. The
                    // Enumerator may be added by a later operation of the same delta; the replay
                    // retries.
                    const Handle at = resolve(name);
                    if (at == kNone) {
                        *error = "attribute " + leaf.name + ": " + name + " is not an object (yet)";
                        return Status::Dangling;
                    }
                    const pymergetic_rxf_cell *ec = types.image().arena().cell(at);
                    if (ec == nullptr || ec->parent != leaf.enumeration) {
                        *error = "attribute " + leaf.name + ": " + name +
                                 " is not an Enumerator of " + types.image().path(leaf.enumeration);
                        return Status::BadName;
                    }
                    name = types.image().arena().name(at);
                }
                const auto hit = std::find_if(e->items.begin(), e->items.end(),
                                              [&](const auto &item) { return item.first == name; });
                if (hit == e->items.end()) {
                    *error = "attribute " + leaf.name + ": '" + name + "' is not a value of " +
                             types.image().path(leaf.enumeration);
                    return w == 0 ? Status::Dangling : Status::BadName;
                }
                v |= hit->second;
            }
            if (leaf.size < 8 && (v >> (leaf.size * 8)) != 0) {
                *error = "attribute " + leaf.name + " does not fit its type";
                return Status::TooLarge;
            }
            store_uint(p, leaf.size, v);
            break;
        }
        case LeafKind::Ref: {
            if (text.empty()) {
                // The empty value is the unset one (docs/27): an OPTIONAL field only, and the
                // one spelling that never resolves to anything.
                if (!leaf.optional) {
                    *error = "attribute " + leaf.name + " is not a path";
                    return Status::BadName;
                }
                store_uint(p, 8, kNone);
                break;
            }
            const Handle h = resolve(text);
            if (h != kNone)
                store_uint(p, 8, h);
            else
                pending->push_back({leaf.offset, text, leaf.optional});
            break;
        }
        }
        given.erase(it);
    }
    if (!given.empty()) {
        *error = "unknown attribute " + given.begin()->first;
        return Status::BadName;
    }
    if (!data.empty()) {
        if (L.fixed) {
            *error = "the type has a fixed size and no tail";
            return Status::BadImage;
        }
        payload->insert(payload->end(), data.begin(), data.end());
    }
    return Status::Ok;
}

// ---- references -----------------------------------------------------------

namespace {

// Calls fn(offset, optional) for each reference leaf of an object's payload.
template <typename Fn> void each_payload_ref(Types &types, Handle type, Fn fn) {
    if (type == kNone)
        return;
    const Layout &L = types.layout(type);
    if (!L.error.empty() || L.raw)
        return;
    for (const Leaf &leaf : L.leaves)
        if (leaf.kind == LeafKind::Ref)
            fn(leaf);
}

} // namespace

std::vector<std::string> referrers(Types &types, const std::vector<Handle> &targets) {
    std::vector<std::string> found;
    const std::set<Handle> inside(targets.begin(), targets.end());
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    for (Handle h = 0; h < arena.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr || inside.count(h))
            continue;
        const std::string where = image.path(h);
        if (inside.count(c->type))
            found.push_back(where + " has an object of the removed type");
        if (inside.count(c->parent))
            found.push_back(where + " is a child");
        const pymergetic_rxf_ref *refs = arena.refs(h);
        for (uint32_t k = 0; k < c->ref_count; k++)
            if (inside.count(refs[k].target))
                found.push_back(where + " references it");
            else if (inside.count(refs[k].role))
                found.push_back(where + " uses it as the role of a reference");
        const uint8_t *payload = arena.payload(h);
        each_payload_ref(types, c->type, [&](const Leaf &leaf) {
            if (leaf.offset + 8 <= c->payload_size &&
                inside.count(load_uint(payload + leaf.offset, 8)))
                found.push_back(where + "." + leaf.name + " references it");
        });
    }
    return found;
}

Status Remapper::remap(const Image &source, Handle object, uint8_t *payload, uint64_t size,
                       const std::function<Handle(Handle)> &map) const {
    const pymergetic_rxf_cell *c = source.arena().cell(object);
    if (c == nullptr)
        return Status::BadHandle;
    Status status = Status::Ok;
    each_payload_ref(*types_, c->type, [&](const Leaf &leaf) {
        if (status != Status::Ok || leaf.offset + 8 > size)
            return;
        const Handle old = load_uint(payload + leaf.offset, 8);
        if (old == kNone)
            return;
        const Handle now = map(old);
        if (now == kNone && !leaf.optional) {
            status = Status::Dangling;
            return;
        }
        store_uint(payload + leaf.offset, 8, now);
    });
    return status;
}

Status compact(const Image &image, uint64_t index_capacity, std::unique_ptr<Image> *out) {
    Types types(&image);
    Remapper remapper(&types);
    return image.compact(index_capacity, out, &remapper);
}

// ---- ground ----------------------------------------------------------------

bool is_ground(const Image &image, Handle object) {
    const arena::Arena &arena = image.arena();
    if (arena.cell(object) == nullptr)
        return false;
    if (object == PYMERGETIC_RXF_HANDLE_ROOT)
        return false; // the root takes children
    if (object <= PYMERGETIC_RXF_HANDLE_FIELD)
        return true;
    // The ground is a few objects directly under the root, and everything below them.
    Handle top = object;
    for (const pymergetic_rxf_cell *c = arena.cell(top);
         c != nullptr && c->parent != PYMERGETIC_RXF_HANDLE_ROOT; c = arena.cell(top)) {
        if (c->parent == kNone)
            return false;
        top = c->parent;
    }
    return top <= PYMERGETIC_RXF_HANDLE_FIELD || kinds::is_ground_name(arena.name(top));
}

// ---- aliases ---------------------------------------------------------------

Handle scope_of(const Image &image, Handle object) {
    const arena::Arena &arena = image.arena();
    for (Handle h = object; h != kNone;
         h = arena.cell(h) == nullptr ? kNone : arena.cell(h)->parent) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr)
            return kNone;
        if (((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK) ==
            PYMERGETIC_RXF_NODE_MODULE)
            return h;
    }
    return kNone;
}

namespace {

bool is_alias(const arena::Arena &arena, Handle h) {
    const pymergetic_rxf_cell *c = arena.cell(h);
    return c != nullptr && ((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) &
                            PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_ALIAS;
}

/* The object an Alias points at: the value of its reference field `target`, kNone while it is
 * unset. */
Handle alias_target(Types &types, Handle alias) {
    const arena::Arena &arena = types.image().arena();
    const pymergetic_rxf_cell *c = arena.cell(alias);
    if (c == nullptr || c->type == kNone)
        return kNone;
    const Layout &L = types.layout(c->type);
    if (!L.error.empty() || L.raw)
        return kNone;
    for (const Leaf &leaf : L.leaves)
        if (leaf.kind == LeafKind::Ref && leaf.name == "target" &&
            leaf.offset + 8 <= c->payload_size) {
            const Handle h = load_uint(arena.payload(alias) + leaf.offset, 8);
            return h != kNone && arena.cell(h) != nullptr ? h : kNone;
        }
    return kNone;
}

} // namespace

Handle resolve_in(Types &types, Handle object, const std::string &text) {
    const Image &image = types.image();
    if (text.empty() || text[0] != kAliasMark)
        return image.resolve(text);
    const size_t dot = text.find('.');
    const std::string name = text.substr(1, dot == std::string::npos ? std::string::npos : dot - 1);
    const Handle scope = scope_of(image, object);
    if (scope == kNone || name.empty())
        return kNone;
    const Handle alias = image.child(scope, name);
    if (alias == kNone || !is_alias(image.arena(), alias))
        return kNone;
    const Handle target = alias_target(types, alias);
    if (target == kNone || dot == std::string::npos)
        return target;
    return image.resolve(image.path(target) + text.substr(dot));
}

namespace {

/* The Alias objects of a Module that have a target, with their handles (the same order in both
 * lists). */
void collect_aliases(Types &types, Handle module,
                     std::vector<std::pair<std::string, std::string>> *names,
                     std::vector<Handle> *handles) {
    const Image &image = types.image();
    for (Handle child : image.children(module)) {
        if (!is_alias(image.arena(), child))
            continue;
        const Handle target = alias_target(types, child);
        if (target == kNone)
            continue;
        names->emplace_back(image.arena().name(child), image.path(target));
        handles->push_back(child);
    }
}

} // namespace

std::vector<std::pair<std::string, std::string>> aliases_of(Types &types, Handle module) {
    std::vector<std::pair<std::string, std::string>> names;
    std::vector<Handle> handles;
    collect_aliases(types, module, &names, &handles);
    return names;
}

int alias_for(const std::vector<std::pair<std::string, std::string>> &aliases,
              const std::string &path) {
    int best = -1;
    for (size_t i = 0; i < aliases.size(); i++) {
        const std::string &target = aliases[i].second;
        const bool inside = path == target || (path.size() > target.size() &&
                                               path.compare(0, target.size(), target) == 0 &&
                                               path[target.size()] == '.');
        if (inside &&
            (best < 0 || target.size() > aliases[static_cast<size_t>(best)].second.size()))
            best = static_cast<int>(i);
    }
    if (best < 0)
        return -1;
    const auto &alias = aliases[static_cast<size_t>(best)];
    return 1 + alias.first.size() + path.size() - alias.second.size() < path.size() ? best : -1;
}

std::string shorten(const std::vector<std::pair<std::string, std::string>> &aliases,
                    const std::string &path) {
    const int i = alias_for(aliases, path);
    if (i < 0)
        return path;
    const auto &alias = aliases[static_cast<size_t>(i)];
    return std::string(1, kAliasMark) + alias.first + path.substr(alias.second.size());
}

std::vector<std::string> written_paths(Types &types, Handle h) {
    std::vector<std::string> out;
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    const pymergetic_rxf_cell *c = arena.cell(h);
    if (c == nullptr)
        return out;
    std::vector<Attr> attrs;
    std::string problem;
    if (decode_payload(types, h, &attrs, &problem) == Status::Ok)
        for (const Attr &a : attrs)
            if (a.is_path && !a.value.empty())
                out.push_back(a.value);
    const pymergetic_rxf_ref *refs = arena.refs(h);
    for (uint32_t k = 0; k < c->ref_count; k++) {
        if (refs[k].target != kNone && arena.cell(refs[k].target) != nullptr)
            out.push_back(image.path(refs[k].target));
        if (refs[k].role != kNone && arena.cell(refs[k].role) != nullptr)
            out.push_back(image.path(refs[k].role));
    }
    return out;
}

std::vector<AliasUse> alias_uses(Types &types) {
    std::vector<AliasUse> out;
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    struct Scope {
        std::vector<std::pair<std::string, std::string>> names;
        std::vector<Handle> handles;
    };
    std::map<Handle, Scope> scopes;
    for (Handle h = 1; h < arena.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr || is_alias(arena, h) || is_ground(image, h) ||
            types.get(c->type) == nullptr)
            continue;
        const Handle scope = scope_of(image, h);
        if (scope == kNone)
            continue;
        auto found = scopes.find(scope);
        if (found == scopes.end()) {
            found = scopes.emplace(scope, Scope()).first;
            collect_aliases(types, scope, &found->second.names, &found->second.handles);
        }
        const Scope &aliases = found->second;
        if (aliases.names.empty())
            continue;
        std::set<int> seen;
        for (const std::string &path : written_paths(types, h)) {
            const int i = alias_for(aliases.names, path);
            if (i >= 0 && seen.insert(i).second)
                out.push_back({aliases.handles[static_cast<size_t>(i)], h});
        }
    }
    return out;
}

bool alias_reexported(Types &types, Handle alias) {
    const arena::Arena &arena = types.image().arena();
    const pymergetic_rxf_cell *c = arena.cell(alias);
    if (c == nullptr || c->type == kNone)
        return false;
    const Layout &L = types.layout(c->type);
    if (!L.error.empty() || L.raw)
        return false;
    for (const Leaf &leaf : L.leaves) {
        if (leaf.name != "flags" || leaf.kind != LeafKind::Enum || leaf.enumeration == kNone ||
            leaf.offset + leaf.size > c->payload_size)
            continue;
        const uint64_t value =
            load_uint(arena.payload(alias) + leaf.offset, static_cast<size_t>(leaf.size));
        const EnumInfo *e = types.enumeration(leaf.enumeration);
        if (e == nullptr)
            return false;
        for (const auto &item : e->items)
            if (item.first == kAliasReexport)
                return (value & item.second) != 0;
    }
    return false;
}

// ---- the hybrid module tree (docs/23 §4.6) ---------------------------------

const char *module_stat_word(ModuleStat stat) {
    switch (stat) {
    case ModuleStat::Package:
        return "package";
    case ModuleStat::Module:
        return "module";
    case ModuleStat::None:
        return "none";
    }
    return "none";
}

const std::vector<std::string> &module_roots() {
    // The Quad: extern/cpython, mpy, thirdparty, micro. Names, not objects — the roots are
    // ordinary Module cells of the image, made by a delta like any module.
    static const std::vector<std::string> roots{"px", "pm", "pt", "py"};
    return roots;
}

namespace {

bool is_module_cell(const arena::Arena &arena, Handle h) {
    const pymergetic_rxf_cell *c = arena.cell(h);
    return c != nullptr && ((c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) &
                            PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_MODULE;
}

std::string roots_line() {
    std::string line;
    for (const std::string &root : module_roots()) {
        if (!line.empty())
            line += ", ";
        line += root;
    }
    return line;
}

/* One module name walked to the named cell it stands for, kNone when it names nothing (the
   honest `none`). A name that does not spell one clear module is refused in `*error`: the walk
   never guesses a trichotomy for it. */
Handle module_cell(const Image &image, const std::string &name, std::string *error) {
    const arena::Arena &arena = image.arena();
    const Handle root = arena.header()->root;
    if (arena.cell(root) == nullptr) {
        if (error != nullptr)
            *error = "dead handle: the image has no live root to walk module names from";
        return kNone;
    }
    // The segments of the name; each must be one clear child name.
    std::vector<std::string> segments;
    for (size_t start = 0; start <= name.size();) {
        const size_t dot = name.find('.', start);
        const std::string segment =
            name.substr(start, dot == std::string::npos ? std::string::npos : dot - start);
        if (segment.empty()) {
            if (error != nullptr)
                *error = "ambiguous name: '" + name +
                         "' has an empty segment, so it names no one clear module";
            return kNone;
        }
        if (segment[0] == kAliasMark) {
            if (error != nullptr)
                *error =
                    "ambiguous name: '" + segment +
                    "' is an alias spelling (a nickname of its module); name the module itself";
            return kNone;
        }
        segments.push_back(segment);
        if (dot == std::string::npos)
            break;
        start = dot + 1;
    }

    Handle at = root;
    for (size_t i = 0; i < segments.size(); i++) {
        const bool first = i == 0;
        const bool last = i + 1 == segments.size();
        const Handle h = image.child(at, segments[i]);
        if (h == kNone) {
            // A dotted name claims a root first; a claim that fails is refused, never answered
            // as `none` (which would say the root exists and merely has no such child).
            if (first && !last && error != nullptr)
                *error = "unknown root: no module root named '" + segments[i] +
                         "' (the roots are " + roots_line() + ")";
            return kNone;
        }
        if (arena.cell(h) == nullptr) {
            if (error != nullptr)
                *error = "dead handle: '" + segments[i] + "' of '" + name +
                         "' names an object that is not live";
            return kNone;
        }
        if (first && !last && !is_module_cell(arena, h)) {
            if (error != nullptr)
                *error = "unknown root: '" + segments[i] +
                         "' is not a root of the module tree (the roots are " + roots_line() + ")";
            return kNone;
        }
        if (!last && !is_module_cell(arena, h))
            return kNone; // off the tree: only module cells have sub-names
        at = h;
    }
    return at;
}

} // namespace

ModuleStat module_stat(const Image &image, const std::string &name, std::string *error) {
    if (error != nullptr)
        error->clear();
    const arena::Arena &arena = image.arena();
    const Handle cell = module_cell(image, name, error);
    if (cell == kNone)
        return ModuleStat::None;
    if (!is_module_cell(arena, cell))
        return ModuleStat::None; // anything else: no module
    for (Handle child : image.children(cell))
        if (is_module_cell(arena, child))
            return ModuleStat::Package;
    return ModuleStat::Module;
}

std::vector<std::string> module_children(const Image &image, const std::string &name,
                                         std::string *error) {
    if (error != nullptr)
        error->clear();
    std::vector<std::string> out;
    const arena::Arena &arena = image.arena();
    const Handle cell = module_cell(image, name, error);
    if (cell == kNone)
        return out;
    if (!is_module_cell(arena, cell))
        return out;
    for (Handle child : image.children(cell)) // children come in name order
        if (is_module_cell(arena, child))
            out.emplace_back(arena.name(child));
    return out;
}

// ---- members ---------------------------------------------------------------

namespace {

uint64_t position_of(Types &types, Handle h) {
    const pymergetic_rxf_cell *c = types.image().arena().cell(h);
    if (c == nullptr)
        return 0;
    if (c->type != kNone) {
        const Layout &L = types.layout(c->type);
        for (const Leaf &leaf : L.leaves)
            if (leaf.name == "index" && leaf.kind == LeafKind::Uint &&
                leaf.offset + leaf.size <= c->payload_size)
                return load_uint(types.image().arena().payload(h) + leaf.offset, leaf.size);
    }
    return c->index;
}

/* `argument[2]` is ("argument", 2); a name without brackets is (name, -1), so it sorts before the
 * numbered ones of its own name. */
std::pair<std::string, int64_t> split_listed(const std::string &name) {
    const size_t close = name.size();
    if (close >= 3 && name[close - 1] == ']') {
        const size_t open = name.rfind('[');
        if (open != std::string::npos && open + 2 <= close - 1) {
            int64_t n = 0;
            bool digits = true;
            for (size_t i = open + 1; i + 1 < close; i++) {
                if (name[i] < '0' || name[i] > '9')
                    digits = false;
                else
                    n = n * 10 + (name[i] - '0');
            }
            if (digits)
                return {name.substr(0, open), n};
        }
    }
    return {name, -1};
}

} // namespace

std::vector<Handle> ordered(Types &types, std::vector<Handle> handles) {
    const arena::Arena &arena = types.image().arena();
    struct Key {
        uint64_t position;
        std::pair<std::string, int64_t> name;
        Handle handle;
    };
    std::vector<Key> keys;
    keys.reserve(handles.size());
    for (Handle h : handles)
        keys.push_back({position_of(types, h),
                        split_listed(arena.cell(h) == nullptr ? std::string() : arena.name(h)), h});
    std::stable_sort(keys.begin(), keys.end(), [](const Key &a, const Key &b) {
        if (a.position != b.position)
            return a.position < b.position;
        return a.name != b.name ? a.name < b.name : a.handle < b.handle;
    });
    for (size_t i = 0; i < keys.size(); i++)
        handles[i] = keys[i].handle;
    return handles;
}

std::vector<Member> members(Types &types, Handle object) {
    std::vector<Member> out;
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    const pymergetic_rxf_cell *c = arena.cell(object);
    if (c == nullptr || c->type == kNone)
        return out;
    const TypeInfo *t = types.get(c->type);
    if (t == nullptr)
        return out;
    for (const FieldInfo &m : t->members) {
        Member member;
        member.field = m.id;
        if (m.flags & PYMERGETIC_RXF_FIELD_FLAG_CHILDREN) {
            for (Handle child : image.children(object))
                if (arena.cell(child)->type == m.value_type)
                    member.items.push_back(child);
        } else if (m.via != kNone && arena.cell(m.via) != nullptr) {
            const pymergetic_rxf_cell *via = arena.cell(m.via);
            const bool field = via->type == PYMERGETIC_RXF_HANDLE_FIELD;
            const std::string name = field ? std::string(arena.name(m.via)) : std::string();
            for (Handle h = 0; h < arena.handle_count(); h++) {
                const pymergetic_rxf_cell *s = arena.cell(h);
                if (s == nullptr || s->type != m.value_type)
                    continue;
                bool hit = false;
                if (field) {
                    const Layout &L = types.layout(s->type);
                    for (const Leaf &leaf : L.leaves)
                        if (leaf.kind == LeafKind::Ref && leaf.name == name &&
                            leaf.offset + 8 <= s->payload_size &&
                            load_uint(arena.payload(h) + leaf.offset, 8) == object)
                            hit = true;
                } else {
                    const pymergetic_rxf_ref *refs = arena.refs(h);
                    for (uint32_t k = 0; k < s->ref_count && !hit; k++)
                        hit = refs[k].role == m.via && refs[k].target == object;
                }
                if (hit)
                    member.items.push_back(h);
            }
        }
        member.items = ordered(types, std::move(member.items));
        out.push_back(std::move(member));
    }
    return out;
}

// ---- check -----------------------------------------------------------------

// The ground is the same in every image: the objects the bootstrap makes, down to their fields.
static void check_ground(Types &types, std::vector<Issue> *issues) {
    const Image &image = types.image();
    std::unique_ptr<Image> reference;
    if (Image::create(1ULL << 24, 0, &reference) != Status::Ok ||
        reference->bootstrap() != Status::Ok)
        return;
    Types reference_types(reference.get());
    const arena::Arena &want = reference->arena();
    const arena::Arena &have = image.arena();
    const auto kind_of = [](const pymergetic_rxf_cell *c) {
        return (c->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) & PYMERGETIC_RXF_FLAG_KIND_MASK;
    };
    for (Handle w = 0; w < want.handle_count(); w++) {
        if (want.cell(w) == nullptr || w == PYMERGETIC_RXF_HANDLE_ROOT)
            continue;
        const std::string path = reference->path(w);
        const Handle h = image.resolve(path);
        if (h == kNone || have.cell(h) == nullptr) {
            issues->push_back({path, "the ground object is missing"});
            continue;
        }
        const pymergetic_rxf_cell *a = want.cell(w), *b = have.cell(h);
        const bool same_type =
            (a->type == kNone) == (b->type == kNone) &&
            (a->type == kNone || reference->path(a->type) == image.path(b->type));
        std::vector<Attr> expected, found;
        std::string error;
        const bool same_payload =
            decode_payload(reference_types, w, &expected, &error) == Status::Ok &&
            decode_payload(types, h, &found, &error) == Status::Ok &&
            expected.size() == found.size() &&
            std::equal(expected.begin(), expected.end(), found.begin(),
                       [](const Attr &x, const Attr &y) {
                           return x.name == y.name && x.value == y.value;
                       });
        if (kind_of(a) != kind_of(b) || !same_type || !same_payload || a->ref_count != b->ref_count)
            issues->push_back({path, "the ground object differs from the bootstrap"});
    }
    for (Handle h = 0; h < have.handle_count(); h++)
        if (have.cell(h) != nullptr && is_ground(image, h) &&
            reference->resolve(image.path(h)) == kNone)
            issues->push_back({image.path(h), "not part of the ground, but below it"});
}

std::vector<Issue> check(Types &types) {
    std::vector<Issue> issues;
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    const auto issue = [&](Handle h, const std::string &message) {
        issues.push_back({image.path(h), message});
    };
    for (const std::string &name : types.duplicate_names())
        issues.push_back({name, "more than one Type object has this name"});
    if (arena.header()->entry != kNone && arena.cell(arena.header()->entry) == nullptr)
        issues.push_back({"", "the entry is not a live object"});

    // Documentation is an object graph of its own (docs/22): the built-in Doc type, and the doc
    // roles its edges carry.
    const Handle doc_type = types.find(kinds::kDocType);
    const Handle doc_roles = types.find(kinds::kDocRoleType);
    const auto doc_role_of = [&](Handle role) -> int {
        const pymergetic_rxf_cell *rc = role == kNone ? nullptr : arena.cell(role);
        if (doc_roles == kNone || rc == nullptr || rc->parent != doc_roles ||
            rc->payload_size < sizeof(uint64_t))
            return -1;
        return static_cast<int>(load_uint(arena.payload(role), sizeof(uint64_t)));
    };

    for (Handle h = 0; h < arena.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr)
            continue;
        if (c->parent != kNone && arena.cell(c->parent) == nullptr)
            issue(h, "the parent is not a live object");
        if (h == PYMERGETIC_RXF_HANDLE_ROOT)
            continue;
        if (c->type == kNone || arena.cell(c->type) == nullptr ||
            arena.cell(c->type)->type != PYMERGETIC_RXF_HANDLE_TYPE) {
            issue(h, "the type is not a Type object");
            continue;
        }
        // Every word of the format must have a name, or the object cannot be written as XML.
        if (kinds::name_of(kinds::node_kinds, c->flags & PYMERGETIC_RXF_FLAG_KIND_MASK) == nullptr)
            issue(h, "the object has an unknown kind");
        if (kinds::name_of(kinds::owners, (c->flags >> PYMERGETIC_RXF_FLAG_OWNER_SHIFT) &
                                              PYMERGETIC_RXF_FLAG_OWNER_MASK) == nullptr)
            issue(h, "the object has an unknown owner");
        if (kinds::name_of(kinds::mobilities, (c->flags >> (PYMERGETIC_RXF_FLAG_STATE_SHIFT +
                                                            PYMERGETIC_RXF_STATE_MOBILITY_SHIFT)) &
                                                  3U) == nullptr)
            issue(h, "the object has an unknown mobility");
        const pymergetic_rxf_ref *refs = arena.refs(h);
        for (uint32_t k = 0; k < c->ref_count; k++) {
            if (kinds::name_of(kinds::ref_kinds, refs[k].kind) == nullptr)
                issue(h, "a reference has an unknown kind " + std::to_string(refs[k].kind));
            if (refs[k].target == kNone) {
                if (refs[k].binding == PYMERGETIC_RXF_BINDING_MANDATORY)
                    issue(h, "a mandatory reference is unset");
            } else if (arena.cell(refs[k].target) == nullptr) {
                issue(h, "a reference points at a dead handle");
            }
            if (refs[k].role != kNone && arena.cell(refs[k].role) == nullptr)
                issue(h, "a reference has a dead role");
            if (kinds::name_of(kinds::bindings, refs[k].binding) == nullptr)
                issue(h, "a reference has the unknown binding " + std::to_string(refs[k].binding));
            // A doc edge is editorial: its role is one of the doc roles, and its target is a Doc
            // object. A placement comes from anywhere; a topic link (Parent, Related) is between
            // docs. A doc role never labels a runtime edge.
            {
                const int role = doc_role_of(refs[k].role);
                if (refs[k].kind == PYMERGETIC_RXF_REF_DOC) {
                    if (role < 0)
                        issue(h, "a doc edge must carry a doc role (Narrates, Attaches, "
                                 "References, Parent, or Related)");
                    if (refs[k].index != 0)
                        issue(h, "a doc edge is editorial: it does not occupy a runtime slot");
                    const pymergetic_rxf_cell *tc =
                        refs[k].target == kNone ? nullptr : arena.cell(refs[k].target);
                    if (tc != nullptr && tc->type != doc_type)
                        issue(h, "a doc edge must name a Doc object");
                    if ((role == PYMERGETIC_RXF_DOC_ROLE_PARENT ||
                         role == PYMERGETIC_RXF_DOC_ROLE_RELATED) &&
                        c->type != doc_type)
                        issue(h, "a topic link (Parent or Related) leaves a Doc object");
                } else if (role >= 0) {
                    issue(h, "a doc role names a doc edge (RefKind.Doc)");
                }
            }
        }
        // The payload against its type.
        const Layout &L = types.layout(c->type);
        if (!L.error.empty()) {
            issue(h, "type " + image.path(c->type) + ": " + L.error);
            continue;
        }
        if (L.fixed ? c->payload_size != L.prefix : c->payload_size < L.prefix) {
            issue(h, "payload of " + std::to_string(c->payload_size) + " bytes does not fit type " +
                         image.path(c->type));
            continue;
        }
        const uint8_t *payload = arena.payload(h);
        each_payload_ref(types, c->type, [&](const Leaf &leaf) {
            const Handle target = load_uint(payload + leaf.offset, 8);
            if (target == kNone) {
                if (!leaf.optional)
                    issue(h, "field " + leaf.name + " is a mandatory reference and is unset");
            } else if (arena.cell(target) == nullptr) {
                issue(h, "field " + leaf.name + " references a dead handle");
            } else {
                // One edge, one place: a field already says what the reference would say.
                for (uint32_t k = 0; k < c->ref_count; k++)
                    if (refs[k].target == target)
                        issue(h, "a reference repeats the field " + leaf.name + " (" +
                                     image.path(target) + ")");
            }
        });
        if (!L.raw)
            for (const Leaf &leaf : L.leaves) {
                if (leaf.kind != LeafKind::Enum || leaf.offset + leaf.size > c->payload_size)
                    continue;
                const uint64_t v = load_uint(payload + leaf.offset, leaf.size);
                const EnumInfo *e = types.enumeration(leaf.enumeration);
                if (v == 0)
                    continue;
                uint64_t rest = v;
                bool named = false;
                if (e != nullptr)
                    for (const auto &item : e->items) {
                        if (!e->bits)
                            named = named || item.second == v;
                        else if (item.second != 0 && (item.second & rest) == item.second)
                            rest &= ~item.second;
                    }
                if (e != nullptr && e->bits)
                    named = rest == 0;
                if (!named)
                    issue(h, "field " + leaf.name + " holds " + std::to_string(v) + ", which " +
                                 image.path(leaf.enumeration) + " does not name");
            }
        // The text of a doc: the words of its payload must name the bytes behind them (docs/22).
        if (doc_type != kNone && c->type == doc_type &&
            c->payload_size >= sizeof(pymergetic_rxf_doc)) {
            pymergetic_rxf_doc words;
            std::memcpy(&words, payload, sizeof(words));
            std::string text, problem;
            if (!doc::unstore(words.codec, payload + sizeof(words), c->payload_size - sizeof(words),
                              &text, &problem))
                issue(h, "the text of the doc does not decode: " + problem);
            else if (text.empty() && words.codec == PYMERGETIC_RXF_DOC_CODEC_RAW)
                issue(h, "a doc with an empty body is not written in DocCodec.Raw");
            else if (!doc::valid_utf8(text))
                issue(h, "the text of the doc is not UTF-8");
        }
    }

    // The parent link of topics is containment: one parent, and no cycle in it (docs/22).
    if (doc_type != kNone) {
        std::map<Handle, Handle> parent;
        for (Handle h = 0; h < arena.handle_count(); h++) {
            const pymergetic_rxf_cell *c = arena.cell(h);
            if (c == nullptr || c->type != doc_type)
                continue;
            const pymergetic_rxf_ref *refs = arena.refs(h);
            for (uint32_t k = 0; k < c->ref_count; k++) {
                if (refs[k].kind != PYMERGETIC_RXF_REF_DOC ||
                    doc_role_of(refs[k].role) != PYMERGETIC_RXF_DOC_ROLE_PARENT)
                    continue;
                if (!parent.emplace(h, refs[k].target).second)
                    issue(h, "a topic has more than one parent");
            }
        }
        std::set<Handle> walking, done;
        for (const auto &entry : parent) {
            if (done.count(entry.first) != 0)
                continue;
            std::vector<Handle> trail;
            Handle at = entry.first;
            while (parent.count(at) != 0 && walking.count(at) == 0 && done.count(at) == 0) {
                walking.insert(at);
                trail.push_back(at);
                at = parent.at(at);
            }
            if (walking.count(at) != 0)
                issue(at, "the parent links of topics contain a cycle");
            for (Handle w : trail) {
                walking.erase(w);
                done.insert(w);
            }
        }
    }

    // Type and Field objects.
    for (Handle h = 0; h < arena.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena.cell(h);
        if (c == nullptr)
            continue;
        if (c->type == PYMERGETIC_RXF_HANDLE_TYPE) {
            const TypeInfo *t = types.get(h);
            if (t == nullptr) {
                issue(h, "not a valid Type descriptor");
                continue;
            }
            {
                const std::string name = arena.name(h);
                if (!is_symbol(name) || name[0] == '_' || is_reserved_element(name))
                    issue(h, "the name of a Type must be an identifier that does not start with "
                             "'_' and is not delta (it is an element name)");
            }
            if (t->form >= kFormCount)
                issue(h, "unknown form");
            if (!is_pow2(t->align))
                issue(h, "alignment is not a power of two");
            uint64_t kids = 0;
            if (t->node_kind != 0 && kinds::name_of(kinds::node_kinds, t->node_kind) == nullptr)
                issue(h, "unknown node kind");
            if (t->bitset() && t->form != kFormEnum)
                issue(h, "only an enumeration can be a bitset");
            std::set<uint64_t> values; // the values of a plain enumeration
            uint64_t bits = 0;         // the bits taken so far in a bitset
            for (Handle child : image.children(h)) {
                const pymergetic_rxf_cell *cc = arena.cell(child);
                const bool enumerator =
                    ((cc->flags >> PYMERGETIC_RXF_FLAG_KIND_SHIFT) &
                     PYMERGETIC_RXF_FLAG_KIND_MASK) == PYMERGETIC_RXF_NODE_ENUMERATOR;
                if (cc->type == PYMERGETIC_RXF_HANDLE_FIELD) {
                    kids++;
                } else if (enumerator && t->form == kFormEnum &&
                           cc->payload_size == sizeof(uint64_t)) {
                    const uint64_t v = load_uint(arena.payload(child), sizeof(uint64_t));
                    if (t->bitset()) {
                        if (v == 0 || (v & bits) != 0)
                            issue(child, "a bit must be non-zero and not shared with another");
                        bits |= v;
                    } else if (!values.insert(v).second) {
                        issue(child, "another enumerator has this value");
                    }
                    if (t->size < 8 && (v >> (t->size * 8)) != 0)
                        issue(child, "the value does not fit the enumeration");
                } else {
                    issue(child, "a child of a Type object must be a Field object, or an "
                                 "Enumerator of an enumeration");
                }
            }
            if (kids != t->field_count)
                issue(h, "declares " + std::to_string(t->field_count) + " fields and has " +
                             std::to_string(kids));
            uint64_t end = 0;
            for (const FieldInfo &f : t->fields) {
                const TypeInfo *v = types.get(f.value_type);
                if (v == nullptr) {
                    issue(f.id, "the value type is not a Type object");
                    continue;
                }
                if (f.count == 0)
                    issue(f.id, "zero count");
                if ((f.flags & PYMERGETIC_RXF_FIELD_FLAG_REF) &&
                    (v->form != kFormUint || v->size != 8 || f.count != 1))
                    issue(f.id, "a reference field must be one uint64");
                if (v->variable() && v->size == 0 &&
                    !(v->form == kFormStruct && !v->fields.empty()))
                    issue(f.id, "embeds a variable-size type");
                const uint64_t unit = v->size;
                if (unit != 0 && f.count > (~0ULL) / unit) {
                    issue(f.id, "extent overflows");
                    continue;
                }
                const uint64_t extent = f.offset + unit * f.count;
                if (extent < f.offset) {
                    issue(f.id, "extent overflows");
                    continue;
                }
                if (t->form != kFormUnion) {
                    if (f.offset < end)
                        issue(f.id, "overlaps the previous field");
                    if (t->size != 0 && !t->variable() && extent > t->size)
                        issue(f.id, "exceeds its type");
                    const uint64_t effective = std::min<uint64_t>(v->align, t->align);
                    if (effective != 0 && f.offset % effective != 0)
                        issue(f.id, "is misaligned");
                }
                end = std::max(end, extent);
            }
            // The declared relations: they hold no value, so no layout rule applies to them.
            const Handle role_type = types.find("Role");
            std::set<Handle> listed; // the child types that a Children field already names
            for (const FieldInfo &f : t->members) {
                const bool children = (f.flags & PYMERGETIC_RXF_FIELD_FLAG_CHILDREN) != 0;
                const bool inverse = (f.flags & PYMERGETIC_RXF_FIELD_FLAG_INVERSE) != 0;
                if (children && inverse)
                    issue(f.id, "a field lists children or is an inverse, not both");
                if (f.flags & (PYMERGETIC_RXF_FIELD_FLAG_REF | PYMERGETIC_RXF_FIELD_FLAG_REPEATED))
                    issue(f.id, "a declared relation holds no value");
                if (types.get(f.value_type) == nullptr) {
                    issue(f.id, "the value type is not a Type object");
                    continue;
                }
                if (children) {
                    if (f.via != kNone)
                        issue(f.id, "via belongs to an inverse");
                    if (!listed.insert(f.value_type).second)
                        issue(f.id, "another field already lists the children of this type");
                } else if (f.via == kNone) {
                    issue(f.id, "an inverse names the Field or Role it goes through");
                } else if (const pymergetic_rxf_cell *via = arena.cell(f.via); via == nullptr) {
                    issue(f.id, "via is not a live object");
                } else if (via->type == PYMERGETIC_RXF_HANDLE_FIELD) {
                    pymergetic_rxf_field vf;
                    if (via->payload_size != sizeof(vf))
                        continue;
                    std::memcpy(&vf, arena.payload(f.via), sizeof(vf));
                    if (via->parent != f.value_type || !(vf.flags & PYMERGETIC_RXF_FIELD_FLAG_REF))
                        issue(f.id, "via is not a reference Field of the value type");
                } else if (role_type == kNone || via->type != role_type) {
                    issue(f.id, "via is neither a Field nor a Role");
                }
            }
        } else if (c->type == PYMERGETIC_RXF_HANDLE_FIELD) {
            const pymergetic_rxf_cell *pc = arena.cell(c->parent);
            if (pc == nullptr || pc->type != PYMERGETIC_RXF_HANDLE_TYPE) {
                issue(h, "a Field object must sit below a Type object");
                continue;
            }
            {
                const std::string name = arena.name(h);
                if (!is_symbol(name) || name[0] == '_' || is_reserved_attribute(name))
                    issue(h, "the name of a Field must be an identifier that does not start with "
                             "'_' and is not name or path (it is an attribute name)");
            }
            if (c->payload_size != sizeof(pymergetic_rxf_field)) {
                issue(h, "not a valid Field descriptor");
                continue;
            }
            pymergetic_rxf_field f;
            std::memcpy(&f, arena.payload(h), sizeof(f));
            if (f.descriptor_tag != kFieldTag)
                issue(h, "not a valid Field descriptor");
            if (f.owner_type != c->parent)
                issue(h, "owner_type is not the parent");
            if (f.via != kNone && !(f.flags & PYMERGETIC_RXF_FIELD_FLAG_INVERSE))
                issue(h, "via belongs to an inverse field");
        }
    }
    check_ground(types, &issues);
    // An alias that no path of its module goes through is dead text, unless it is marked as a name
    // of the module.
    if (issues.empty()) {
        std::set<Handle> used;
        for (const AliasUse &use : alias_uses(types))
            used.insert(use.alias);
        for (Handle h = 1; h < arena.handle_count(); h++)
            if (is_alias(arena, h) && used.count(h) == 0 && !alias_reexported(types, h))
                issue(h, std::string("the alias is not used (mark it ") + kAliasReexport +
                             " to keep it)");
    }
    return issues;
}

Status doc_text(Types &types, Handle doc, std::string *out, std::string *error) {
    const Image &image = types.image();
    const arena::Arena &arena = image.arena();
    const pymergetic_rxf_cell *c = arena.cell(doc);
    if (c == nullptr) {
        if (error != nullptr)
            *error = "the doc is not a live object";
        return Status::BadHandle;
    }
    const Handle doc_type = types.find(kinds::kDocType);
    if (doc_type == kNone || c->type != doc_type) {
        if (error != nullptr)
            *error = image.path(doc) + " is not a Doc object";
        return Status::BadName;
    }
    if (c->payload_size < sizeof(pymergetic_rxf_doc)) {
        if (error != nullptr)
            *error = "the payload of the doc is shorter than its words";
        return Status::BadImage;
    }
    pymergetic_rxf_doc words;
    std::memcpy(&words, arena.payload(doc), sizeof(words));
    if (!doc::unstore(words.codec, arena.payload(doc) + sizeof(words),
                      c->payload_size - sizeof(words), out, error))
        return Status::BadImage;
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::meta
