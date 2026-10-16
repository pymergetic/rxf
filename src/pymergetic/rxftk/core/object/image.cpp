#include <pymergetic/rxftk/core/object/image.hpp>

#include <pymergetic/rxftk/core/kinds/kinds.hpp>

#include <algorithm>
#include <cstring>

#include <sys/mman.h>

namespace pymergetic::rxf::core::object {

namespace {

uint32_t pack_flags(uint32_t kind, uint32_t state, uint32_t owner_kind) {
    return ((kind & PYMERGETIC_RXF_FLAG_KIND_MASK) << PYMERGETIC_RXF_FLAG_KIND_SHIFT) |
           ((state & PYMERGETIC_RXF_FLAG_STATE_MASK) << PYMERGETIC_RXF_FLAG_STATE_SHIFT) |
           ((owner_kind & PYMERGETIC_RXF_FLAG_OWNER_MASK) << PYMERGETIC_RXF_FLAG_OWNER_SHIFT);
}

uint32_t state_of(const pymergetic_rxf_cell *c) {
    return (c->flags >> PYMERGETIC_RXF_FLAG_STATE_SHIFT) & PYMERGETIC_RXF_FLAG_STATE_MASK;
}

uint32_t disposition_of(const pymergetic_rxf_cell *c) {
    return (state_of(c) >> PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT) & 3U;
}

/* Well-formed UTF-8: no overlong forms, no surrogates, nothing above U+10FFFF. */
bool utf8_ok(const std::string &s) {
    size_t i = 0;
    while (i < s.size()) {
        const uint8_t c = static_cast<uint8_t>(s[i]);
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
            if (i + k >= s.size())
                return false;
            const uint8_t d = static_cast<uint8_t>(s[i + k]);
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

/* A name is text: UTF-8 without control characters, and without the path separator. */
bool good_name(const std::string &name) {
    // '@' leads the path of an alias, so no name starts with it.
    if (name.empty() || name.find('.') != std::string::npos || name[0] == '@')
        return false;
    for (const char c : name)
        if (static_cast<uint8_t>(c) < 0x20 || static_cast<uint8_t>(c) == 0x7F)
            return false;
    return utf8_ok(name);
}

bool commit_callback(void *ctx, uint64_t bytes) {
    return static_cast<HostMemory *>(ctx)->commit(bytes);
}

} // namespace

// ---- HostMemory ----------------------------------------------------------

std::unique_ptr<HostMemory> HostMemory::reserve(uint64_t limit) {
    if (limit == 0 || limit >= (1ULL << 40))
        return nullptr;
    int flags = MAP_PRIVATE | MAP_ANONYMOUS;
#ifdef MAP_NORESERVE
    flags |= MAP_NORESERVE;
#endif
    void *base = mmap(nullptr, limit, PROT_READ | PROT_WRITE, flags, -1, 0);
    if (base == MAP_FAILED)
        return nullptr;
    std::unique_ptr<HostMemory> memory(new HostMemory());
    memory->base_ = static_cast<uint8_t *>(base);
    memory->limit_ = limit;
    return memory;
}

HostMemory::~HostMemory() {
    if (base_ != nullptr)
        munmap(base_, limit_);
}

bool HostMemory::commit(uint64_t bytes) {
    if (bytes > limit_)
        return false;
    if (bytes > committed_)
        committed_ = bytes;
    return true;
}

Memory HostMemory::memory() {
    Memory m;
    m.base = base_;
    m.committed = committed_;
    m.limit = limit_;
    m.commit = commit_callback;
    m.ctx = this;
    return m;
}

// ---- Image ---------------------------------------------------------------

Status Image::create(uint64_t limit, uint64_t index_capacity, std::unique_ptr<Image> *out) {
    std::unique_ptr<Image> image(new Image());
    image->memory_ = HostMemory::reserve(limit);
    if (!image->memory_)
        return Status::OutOfMemory;
    Status status = image->arena_.init(image->memory_->memory(), index_capacity);
    if (status != Status::Ok)
        return status;
    *out = std::move(image);
    return Status::Ok;
}

Status Image::load(const uint8_t *bytes, uint64_t size, uint64_t limit,
                   std::unique_ptr<Image> *out) {
    if (size < sizeof(pymergetic_rxf_arena_header) || size > limit)
        return Status::BadImage;
    std::unique_ptr<Image> image(new Image());
    image->memory_ = HostMemory::reserve(limit);
    if (!image->memory_)
        return Status::OutOfMemory;
    if (!image->memory_->commit(align_up(size, kPageSize)))
        return Status::OutOfMemory;
    std::memcpy(image->memory_->base(), bytes, size);
    Status status = image->arena_.attach(image->memory_->memory());
    if (status != Status::Ok)
        return status;
    status = image->arena_.verify();
    if (status != Status::Ok)
        return status;
    status = image->rebuild_index();
    if (status != Status::Ok)
        return status;
    *out = std::move(image);
    return Status::Ok;
}

std::vector<uint8_t> Image::to_bytes() const {
    const pymergetic_rxf_arena_header *h = arena_.header();
    std::vector<uint8_t> bytes(h->body_size);
    std::memcpy(bytes.data(), arena_.memory().base, h->body_size);
    pymergetic_rxf_arena_header *copy =
        reinterpret_cast<pymergetic_rxf_arena_header *>(bytes.data());
    copy->committed_size = h->body_size;
    copy->limit = PYMERGETIC_RXF_LIMIT_UNKNOWN;
    return bytes;
}

Status Image::rebuild_index() {
    children_.clear();
    const uint64_t count = arena_.handle_count();
    for (pymergetic_rxf_handle h = 0; h < count; h++) {
        const pymergetic_rxf_cell *c = arena_.cell(h);
        if (c == nullptr)
            continue;
        if (c->parent == PYMERGETIC_RXF_HANDLE_NONE)
            continue;
        const std::string name = arena_.name(h);
        if (!good_name(name))
            return Status::BadName;
        if (!children_[c->parent].emplace(name, h).second)
            return Status::NameTaken;
    }
    // Every chain of parents ends: an image whose objects are their own ancestors has no paths.
    std::vector<uint8_t> state(count, 0); // 0 not seen, 1 on the walk, 2 known to end
    for (pymergetic_rxf_handle start = 0; start < count; start++) {
        std::vector<pymergetic_rxf_handle> walk;
        pymergetic_rxf_handle at = start;
        while (at != PYMERGETIC_RXF_HANDLE_NONE && state[at] == 0) {
            const pymergetic_rxf_cell *c = arena_.cell(at);
            if (c == nullptr)
                break;
            state[at] = 1;
            walk.push_back(at);
            at = c->parent;
        }
        if (at != PYMERGETIC_RXF_HANDLE_NONE && at < count && state[at] == 1)
            return Status::BadImage;
        for (pymergetic_rxf_handle seen : walk)
            state[seen] = 2;
    }
    return Status::Ok;
}

Status Image::add(const ObjectSpec &spec, pymergetic_rxf_handle *out) {
    const bool is_root = spec.parent == PYMERGETIC_RXF_HANDLE_NONE;
    if (is_root) {
        if (arena_.handle_count() != 0 || !spec.name.empty())
            return Status::BadName;
    } else {
        if (!good_name(spec.name))
            return Status::BadName;
        if (!arena_.live(spec.parent))
            return Status::BadHandle;
        const auto it = children_.find(spec.parent);
        if (it != children_.end() && it->second.count(spec.name))
            return Status::NameTaken;
    }
    CellSpec cell;
    cell.type = spec.type;
    cell.parent = spec.parent;
    cell.name = spec.name.data();
    cell.name_length = spec.name.size();
    cell.flags = pack_flags(spec.kind, spec.state, spec.owner_kind);
    cell.generation = spec.generation;
    cell.index = spec.index;
    cell.payload_size = spec.payload.size();
    cell.ref_count = static_cast<uint32_t>(spec.refs.size());
    if (spec.refs.size() > kMaxRefs)
        return Status::TooLarge;
    pymergetic_rxf_handle handle = 0;
    Status status = arena_.create(cell, &handle);
    if (status != Status::Ok)
        return status;
    if (!spec.payload.empty())
        std::memcpy(arena_.payload(handle), spec.payload.data(), spec.payload.size());
    if (!spec.refs.empty())
        std::memcpy(arena_.refs(handle), spec.refs.data(),
                    spec.refs.size() * sizeof(pymergetic_rxf_ref));
    if (is_root) {
        arena_.mutable_header()->root = handle;
    } else {
        children_[spec.parent].emplace(spec.name, handle);
    }
    *out = handle;
    return Status::Ok;
}

namespace {

constexpr uint32_t kTypeTag = 0x54595045;  // "TYPE"
constexpr uint32_t kFieldTag = 0x464C4420; // "FLD "

std::vector<uint8_t> bytes_of(const void *p, size_t n) {
    const uint8_t *b = static_cast<const uint8_t *>(p);
    return std::vector<uint8_t>(b, b + n);
}

std::vector<uint8_t> type_payload(uint32_t form, uint64_t size, uint32_t align, uint32_t flags,
                                  uint32_t field_count, uint32_t node_kind) {
    pymergetic_rxf_type t{};
    t.descriptor_tag = kTypeTag;
    t.form = form;
    t.size = size;
    t.inner_type = PYMERGETIC_RXF_HANDLE_NONE;
    t.return_type = PYMERGETIC_RXF_HANDLE_NONE;
    t.align = align;
    t.flags = flags;
    t.field_count = field_count;
    t.node_kind = node_kind;
    return bytes_of(&t, sizeof(t));
}

std::vector<uint8_t> field_payload(pymergetic_rxf_handle owner, pymergetic_rxf_handle value,
                                   uint64_t offset, uint32_t flags) {
    pymergetic_rxf_field f{};
    f.descriptor_tag = kFieldTag;
    f.flags = flags;
    f.owner_type = owner;
    f.value_type = value;
    f.offset = offset;
    f.count = 1;
    f.via = PYMERGETIC_RXF_HANDLE_NONE;
    return bytes_of(&f, sizeof(f));
}

/* How a field of Type or Field is typed: a number, or one of the ground enumerations. */
struct FieldDef {
    const char *name;
    bool wide; // uint64_t, else uint32_t
    uint64_t offset;
    uint32_t flags;
    const char *enumeration; // the name of a ground enumeration Type, or nullptr
};

constexpr uint32_t kRef = PYMERGETIC_RXF_FIELD_FLAG_REF;
constexpr uint32_t kRefOpt = PYMERGETIC_RXF_FIELD_FLAG_REF | PYMERGETIC_RXF_FIELD_FLAG_OPTIONAL;

constexpr FieldDef kTypeFields[] = {
    {"descriptor_tag", false, offsetof(pymergetic_rxf_type, descriptor_tag), 0, nullptr},
    {"form", false, offsetof(pymergetic_rxf_type, form), 0, "Form"},
    {"size", true, offsetof(pymergetic_rxf_type, size), 0, nullptr},
    {"inner_type", true, offsetof(pymergetic_rxf_type, inner_type), kRefOpt, nullptr},
    {"return_type", true, offsetof(pymergetic_rxf_type, return_type), kRefOpt, nullptr},
    {"align", false, offsetof(pymergetic_rxf_type, align), 0, nullptr},
    {"flags", false, offsetof(pymergetic_rxf_type, flags), 0, "TypeFlags"},
    {"field_count", false, offsetof(pymergetic_rxf_type, field_count), 0, nullptr},
    {"node_kind", false, offsetof(pymergetic_rxf_type, node_kind), 0, "NodeKind"},
};

constexpr FieldDef kFieldFields[] = {
    {"descriptor_tag", false, offsetof(pymergetic_rxf_field, descriptor_tag), 0, nullptr},
    {"flags", false, offsetof(pymergetic_rxf_field, flags), 0, "FieldFlags"},
    {"owner_type", true, offsetof(pymergetic_rxf_field, owner_type), kRef, nullptr},
    {"value_type", true, offsetof(pymergetic_rxf_field, value_type), kRef, nullptr},
    {"offset", true, offsetof(pymergetic_rxf_field, offset), 0, nullptr},
    {"count", true, offsetof(pymergetic_rxf_field, count), 0, nullptr},
    {"via", true, offsetof(pymergetic_rxf_field, via), kRefOpt, nullptr},
};

/* Doc: the text of documentation and the codec of that text (docs/22). The
 * words are here; the text is the variable tail after them. */
constexpr FieldDef kDocFields[] = {
    {"kind", false, offsetof(pymergetic_rxf_doc, kind), 0, "DocKind"},
    {"status", false, offsetof(pymergetic_rxf_doc, status), 0, "DocStatus"},
    {"codec", false, offsetof(pymergetic_rxf_doc, codec), 0, "DocCodec"},
};

} // namespace

Status Image::bootstrap() {
    const uint32_t state = image_state();
    ObjectSpec root;
    root.kind = PYMERGETIC_RXF_NODE_ROOT;
    root.state = state;
    pymergetic_rxf_handle h = 0;
    Status status = add(root, &h);
    if (status != Status::Ok)
        return status;
    if (h != PYMERGETIC_RXF_HANDLE_ROOT)
        return Status::BadImage;

    const auto object = [&](pymergetic_rxf_handle parent, const char *name, uint32_t kind,
                            pymergetic_rxf_handle type, std::vector<uint8_t> payload,
                            pymergetic_rxf_handle *made) -> Status {
        ObjectSpec spec;
        spec.kind = kind;
        spec.parent = parent;
        spec.name = name;
        spec.state = state;
        spec.type = type;
        spec.payload = std::move(payload);
        return add(spec, made);
    };
    const auto type_object = [&](const char *name, uint32_t form, uint64_t size, uint32_t align,
                                 uint32_t flags, uint32_t count, uint32_t node_kind,
                                 pymergetic_rxf_handle expect) -> Status {
        pymergetic_rxf_handle made = 0;
        // Type and Field describe themselves: their type is the handle they are about to take.
        const Status s = object(PYMERGETIC_RXF_HANDLE_ROOT, name, PYMERGETIC_RXF_NODE_TYPE,
                                PYMERGETIC_RXF_HANDLE_TYPE,
                                type_payload(form, size, align, flags, count, node_kind), &made);
        if (s != Status::Ok)
            return s;
        return expect == PYMERGETIC_RXF_HANDLE_NONE || made == expect ? Status::Ok
                                                                      : Status::BadImage;
    };
    constexpr pymergetic_rxf_handle kU32 = 3, kU64 = 4;
    constexpr pymergetic_rxf_handle kAny = PYMERGETIC_RXF_HANDLE_NONE;
    status = type_object("Type", PYMERGETIC_RXF_FORM_META, sizeof(pymergetic_rxf_type), 8,
                         PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING,
                         sizeof(kTypeFields) / sizeof(kTypeFields[0]), PYMERGETIC_RXF_NODE_TYPE,
                         PYMERGETIC_RXF_HANDLE_TYPE);
    if (status != Status::Ok)
        return status;
    status = type_object("Field", PYMERGETIC_RXF_FORM_STRUCT, sizeof(pymergetic_rxf_field), 8, 0,
                         sizeof(kFieldFields) / sizeof(kFieldFields[0]), PYMERGETIC_RXF_NODE_FIELD,
                         PYMERGETIC_RXF_HANDLE_FIELD);
    if (status != Status::Ok)
        return status;
    status = type_object("uint32_t", PYMERGETIC_RXF_FORM_UINT, 4, 4, 0, 0, 0, kU32);
    if (status != Status::Ok)
        return status;
    status = type_object("uint64_t", PYMERGETIC_RXF_FORM_UINT, 8, 8, 0, 0, 0, kU64);
    if (status != Status::Ok)
        return status;

    // Enumerator: the named value of an enumeration Type; the ground enumerations are made of it.
    status = type_object("Enumerator", PYMERGETIC_RXF_FORM_STRUCT, 8, 8, 0, 1,
                         PYMERGETIC_RXF_NODE_ENUMERATOR, kAny);
    if (status != Status::Ok)
        return status;
    const pymergetic_rxf_handle enumerator = resolve("Enumerator");
    {
        pymergetic_rxf_handle made = 0;
        status = object(enumerator, "value", PYMERGETIC_RXF_NODE_FIELD, PYMERGETIC_RXF_HANDLE_FIELD,
                        field_payload(enumerator, kU64, 0, 0), &made);
        if (status != Status::Ok)
            return status;
    }
    for (const kinds::GroundEnum &e : kinds::ground_enums()) {
        status = type_object(e.type, PYMERGETIC_RXF_FORM_ENUM, 4, 4,
                             e.bits ? PYMERGETIC_RXF_TYPE_FLAG_BITSET : 0, 0, 0, kAny);
        if (status != Status::Ok)
            return status;
        const pymergetic_rxf_handle type = resolve(e.type);
        for (const kinds::Name &n : e.table) {
            const uint64_t value = n.value;
            pymergetic_rxf_handle made = 0;
            status = object(type, n.name, PYMERGETIC_RXF_NODE_ENUMERATOR, enumerator,
                            bytes_of(&value, sizeof(value)), &made);
            if (status != Status::Ok)
                return status;
        }
    }

    const auto describe = [&](pymergetic_rxf_handle owner, const FieldDef *defs,
                              size_t n) -> Status {
        for (size_t i = 0; i < n; i++) {
            const pymergetic_rxf_handle value = defs[i].enumeration != nullptr
                                                    ? resolve(defs[i].enumeration)
                                                    : (defs[i].wide ? kU64 : kU32);
            pymergetic_rxf_handle made = 0;
            const Status s =
                object(owner, defs[i].name, PYMERGETIC_RXF_NODE_FIELD, PYMERGETIC_RXF_HANDLE_FIELD,
                       field_payload(owner, value, defs[i].offset, defs[i].flags), &made);
            if (s != Status::Ok)
                return s;
        }
        return Status::Ok;
    };
    status = describe(PYMERGETIC_RXF_HANDLE_TYPE, kTypeFields,
                      sizeof(kTypeFields) / sizeof(kTypeFields[0]));
    if (status != Status::Ok)
        return status;
    status = describe(PYMERGETIC_RXF_HANDLE_FIELD, kFieldFields,
                      sizeof(kFieldFields) / sizeof(kFieldFields[0]));
    if (status != Status::Ok)
        return status;
    // Doc: the built-in type of documentation; its text sits in the tail behind these words.
    status =
        type_object(kinds::kDocType, PYMERGETIC_RXF_FORM_STRUCT, sizeof(pymergetic_rxf_doc), 4,
                    PYMERGETIC_RXF_TYPE_FLAG_VARIABLE | PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING,
                    sizeof(kDocFields) / sizeof(kDocFields[0]), 0, kAny);
    if (status != Status::Ok)
        return status;
    return describe(resolve(kinds::kDocType), kDocFields,
                    sizeof(kDocFields) / sizeof(kDocFields[0]));
}

Status Image::remove(pymergetic_rxf_handle handle) {
    pymergetic_rxf_cell *c = arena_.cell(handle);
    if (c == nullptr)
        return Status::BadHandle;
    if (handle == PYMERGETIC_RXF_HANDLE_ROOT || handle == PYMERGETIC_RXF_HANDLE_TYPE ||
        handle == PYMERGETIC_RXF_HANDLE_FIELD)
        return Status::Protected;
    const auto kids = children_.find(handle);
    if (kids != children_.end() && !kids->second.empty())
        return Status::HasChildren;
    const pymergetic_rxf_handle parent = c->parent;
    const std::string name = arena_.name(handle);
    Status status = arena_.destroy(handle);
    if (status != Status::Ok)
        return status;
    if (parent != PYMERGETIC_RXF_HANDLE_NONE) {
        auto it = children_.find(parent);
        if (it != children_.end())
            it->second.erase(name);
    }
    children_.erase(handle);
    return Status::Ok;
}

Status Image::set_payload(pymergetic_rxf_handle handle, const uint8_t *bytes, uint64_t size) {
    pymergetic_rxf_cell *c = arena_.cell(handle);
    if (c == nullptr)
        return Status::BadHandle;
    if (c->payload_size != size) {
        Status status = arena_.resize(handle, size, c->ref_count);
        if (status != Status::Ok)
            return status;
    }
    if (size != 0) {
        uint8_t *to = arena_.payload_w(handle);
        if (to == nullptr)
            return Status::JournalFull;
        std::memcpy(to, bytes, size);
    }
    return Status::Ok;
}

Status Image::set_refs(pymergetic_rxf_handle handle, const pymergetic_rxf_ref *refs,
                       uint32_t count) {
    pymergetic_rxf_cell *c = arena_.cell(handle);
    if (c == nullptr)
        return Status::BadHandle;
    if (c->ref_count != count) {
        Status status = arena_.resize(handle, c->payload_size, count);
        if (status != Status::Ok)
            return status;
    }
    if (count != 0) {
        pymergetic_rxf_ref *to = arena_.refs_w(handle);
        if (to == nullptr)
            return Status::JournalFull;
        std::memcpy(to, refs, count * sizeof(pymergetic_rxf_ref));
    }
    return Status::Ok;
}

Status Image::set_parent(pymergetic_rxf_handle handle, pymergetic_rxf_handle parent) {
    pymergetic_rxf_cell *c = arena_.cell(handle);
    if (c == nullptr || !arena_.live(parent))
        return Status::BadHandle;
    if (handle == PYMERGETIC_RXF_HANDLE_ROOT)
        return Status::Protected;
    // No cycles: the new parent must not sit below the object.
    for (pymergetic_rxf_handle up = parent; up != PYMERGETIC_RXF_HANDLE_NONE;) {
        if (up == handle)
            return Status::BadPath;
        const pymergetic_rxf_cell *p = arena_.cell(up);
        up = p ? p->parent : PYMERGETIC_RXF_HANDLE_NONE;
    }
    const std::string name = arena_.name(handle);
    auto &siblings = children_[parent];
    if (siblings.count(name) && siblings.at(name) != handle)
        return Status::NameTaken;
    c = arena_.cell_w(handle);
    if (c == nullptr)
        return Status::JournalFull;
    if (c->parent != PYMERGETIC_RXF_HANDLE_NONE)
        children_[c->parent].erase(name);
    c->parent = parent;
    siblings[name] = handle;
    return Status::Ok;
}

Status Image::rename(pymergetic_rxf_handle handle, const std::string &name) {
    const pymergetic_rxf_cell *c = arena_.cell(handle);
    if (c == nullptr)
        return Status::BadHandle;
    if (c->parent == PYMERGETIC_RXF_HANDLE_NONE)
        return Status::Protected; // the root has no name
    if (!good_name(name))
        return Status::BadName;
    const std::string old = arena_.name(handle);
    if (old == name)
        return Status::Ok;
    auto &siblings = children_[c->parent];
    if (siblings.count(name))
        return Status::NameTaken;
    Status status = arena_.set_name(handle, name.data(), name.size());
    if (status != Status::Ok)
        return status;
    siblings.erase(old);
    siblings[name] = handle;
    return Status::Ok;
}

Status Image::set_type(pymergetic_rxf_handle handle, pymergetic_rxf_handle type) {
    if (!arena_.live(handle))
        return Status::BadHandle;
    pymergetic_rxf_cell *c = arena_.cell_w(handle);
    if (c == nullptr)
        return Status::JournalFull;
    c->type = type;
    return Status::Ok;
}

Status Image::set_state(pymergetic_rxf_handle handle, uint32_t state) {
    if (!arena_.live(handle))
        return Status::BadHandle;
    pymergetic_rxf_cell *c = arena_.cell_w(handle);
    if (c == nullptr)
        return Status::JournalFull;
    c->flags = (c->flags & ~(PYMERGETIC_RXF_FLAG_STATE_MASK << PYMERGETIC_RXF_FLAG_STATE_SHIFT)) |
               ((state & PYMERGETIC_RXF_FLAG_STATE_MASK) << PYMERGETIC_RXF_FLAG_STATE_SHIFT);
    return Status::Ok;
}

pymergetic_rxf_handle Image::resolve(const std::string &path) const {
    pymergetic_rxf_handle at = arena_.header()->root;
    if (at == PYMERGETIC_RXF_HANDLE_NONE || path.empty())
        return at;
    size_t start = 0;
    while (start <= path.size()) {
        size_t dot = path.find('.', start);
        if (dot == std::string::npos)
            dot = path.size();
        const auto it = children_.find(at);
        if (it == children_.end())
            return PYMERGETIC_RXF_HANDLE_NONE;
        const auto child = it->second.find(path.substr(start, dot - start));
        if (child == it->second.end())
            return PYMERGETIC_RXF_HANDLE_NONE;
        at = child->second;
        start = dot + 1;
    }
    return at;
}

std::string Image::path(pymergetic_rxf_handle handle) const {
    std::vector<std::string> parts;
    for (pymergetic_rxf_handle at = handle;
         at != PYMERGETIC_RXF_HANDLE_NONE && parts.size() < 4096;) {
        const pymergetic_rxf_cell *c = arena_.cell(at);
        if (c == nullptr)
            return std::string();
        if (c->parent == PYMERGETIC_RXF_HANDLE_NONE)
            break;
        parts.emplace_back(arena_.name(at));
        at = c->parent;
    }
    std::string joined;
    for (auto it = parts.rbegin(); it != parts.rend(); ++it) {
        if (!joined.empty())
            joined += '.';
        joined += *it;
    }
    return joined;
}

pymergetic_rxf_handle Image::child(pymergetic_rxf_handle handle, const std::string &name) const {
    const auto it = children_.find(handle);
    if (it == children_.end())
        return PYMERGETIC_RXF_HANDLE_NONE;
    const auto hit = it->second.find(name);
    return hit == it->second.end() ? PYMERGETIC_RXF_HANDLE_NONE : hit->second;
}

std::vector<pymergetic_rxf_handle> Image::children(pymergetic_rxf_handle handle) const {
    std::vector<pymergetic_rxf_handle> result;
    const auto it = children_.find(handle);
    if (it == children_.end())
        return result;
    for (const auto &entry : it->second)
        result.push_back(entry.second);
    return result;
}

Status Image::verify() const {
    Status status = arena_.verify();
    if (status != Status::Ok)
        return status;
    uint64_t indexed = 0;
    for (const auto &entry : children_)
        indexed += entry.second.size();
    uint64_t with_parent = 0;
    for (pymergetic_rxf_handle h = 0; h < arena_.handle_count(); h++) {
        const pymergetic_rxf_cell *c = arena_.cell(h);
        if (c && c->parent != PYMERGETIC_RXF_HANDLE_NONE)
            with_parent++;
    }
    return indexed == with_parent ? Status::Ok : Status::BadImage;
}

Status Image::compact(uint64_t index_capacity, std::unique_ptr<Image> *out,
                      const PayloadRemap *remap) const {
    const uint64_t n = arena_.handle_count();
    std::vector<char> keep(n, 0);
    uint64_t kept = 0;
    for (pymergetic_rxf_handle h = 0; h < n; h++) {
        const pymergetic_rxf_cell *c = arena_.cell(h);
        if (c != nullptr && disposition_of(c) == PYMERGETIC_RXF_DISPOSITION_RETAIN) {
            keep[h] = 1;
            kept++;
        }
    }
    for (pymergetic_rxf_handle fixed :
         {PYMERGETIC_RXF_HANDLE_ROOT, PYMERGETIC_RXF_HANDLE_TYPE, PYMERGETIC_RXF_HANDLE_FIELD})
        if (fixed >= n || !keep[fixed])
            return Status::Dangling;

    // Everything a retained object depends on must be retained, too.
    for (pymergetic_rxf_handle h = 0; h < n; h++) {
        if (!keep[h])
            continue;
        const pymergetic_rxf_cell *c = arena_.cell(h);
        if (c->parent != PYMERGETIC_RXF_HANDLE_NONE && (c->parent >= n || !keep[c->parent]))
            return Status::Dangling;
        if (c->type != PYMERGETIC_RXF_HANDLE_NONE && (c->type >= n || !keep[c->type]))
            return Status::Dangling;
        const pymergetic_rxf_ref *refs = arena_.refs(h);
        for (uint32_t k = 0; k < c->ref_count; k++) {
            const pymergetic_rxf_handle t = refs[k].target;
            const bool gone = t != PYMERGETIC_RXF_HANDLE_NONE && (t >= n || !keep[t]);
            if (gone && refs[k].binding == PYMERGETIC_RXF_BINDING_MANDATORY)
                return Status::Dangling;
            const pymergetic_rxf_handle role = refs[k].role;
            if (role != PYMERGETIC_RXF_HANDLE_NONE && (role >= n || !keep[role]))
                return Status::Dangling;
        }
    }

    // Canonical order: the fixed three, then the rest in path order (pre-order, children by name).
    std::vector<pymergetic_rxf_handle> order = {
        PYMERGETIC_RXF_HANDLE_ROOT, PYMERGETIC_RXF_HANDLE_TYPE, PYMERGETIC_RXF_HANDLE_FIELD};
    std::vector<pymergetic_rxf_handle> stack = {PYMERGETIC_RXF_HANDLE_ROOT};
    while (!stack.empty()) {
        const pymergetic_rxf_handle at = stack.back();
        stack.pop_back();
        if (at != PYMERGETIC_RXF_HANDLE_ROOT && at != PYMERGETIC_RXF_HANDLE_TYPE &&
            at != PYMERGETIC_RXF_HANDLE_FIELD)
            order.push_back(at);
        const auto it = children_.find(at);
        if (it == children_.end())
            continue;
        std::vector<pymergetic_rxf_handle> kids;
        for (const auto &entry : it->second)
            if (keep[entry.second])
                kids.push_back(entry.second);
        for (auto k = kids.rbegin(); k != kids.rend(); ++k)
            stack.push_back(*k);
    }
    if (order.size() != kept)
        return Status::BadImage; // a retained object not reachable from the root

    std::vector<pymergetic_rxf_handle> remap_table(n, PYMERGETIC_RXF_HANDLE_NONE);
    for (uint64_t i = 0; i < order.size(); i++)
        remap_table[order[i]] = i;
    const auto mapped = [&](pymergetic_rxf_handle old) -> pymergetic_rxf_handle {
        return old == PYMERGETIC_RXF_HANDLE_NONE || old >= n ? PYMERGETIC_RXF_HANDLE_NONE
                                                             : remap_table[old];
    };

    std::unique_ptr<Image> next;
    Status status =
        Image::create(memory_->limit(), std::max<uint64_t>(index_capacity, kept), &next);
    if (status != Status::Ok)
        return status;
    for (pymergetic_rxf_handle old : order) {
        const pymergetic_rxf_cell *c = arena_.cell(old);
        const pymergetic_rxf_ref *refs = arena_.refs(old);
        std::vector<pymergetic_rxf_ref> kept_refs;
        for (uint32_t k = 0; k < c->ref_count; k++) {
            if (refs[k].target != PYMERGETIC_RXF_HANDLE_NONE &&
                (refs[k].target >= n || !keep[refs[k].target]))
                continue; // optional, dropped
            pymergetic_rxf_ref r = refs[k];
            r.target = mapped(r.target);
            r.role = mapped(r.role);
            kept_refs.push_back(r);
        }
        const uint32_t mobility = (state_of(c) >> PYMERGETIC_RXF_STATE_MOBILITY_SHIFT) & 3U;
        const uint32_t state =
            (PYMERGETIC_RXF_PROVENANCE_IMAGE << PYMERGETIC_RXF_STATE_PROVENANCE_SHIFT) |
            (PYMERGETIC_RXF_DISPOSITION_RETAIN << PYMERGETIC_RXF_STATE_DISPOSITION_SHIFT) |
            (PYMERGETIC_RXF_CLEANLINESS_CLEAN << PYMERGETIC_RXF_STATE_CLEANLINESS_SHIFT) |
            (mobility << PYMERGETIC_RXF_STATE_MOBILITY_SHIFT);
        CellSpec spec;
        spec.type = mapped(c->type);
        spec.parent = mapped(c->parent);
        const char *name = arena_.name(old);
        spec.name = name;
        spec.name_length = std::strlen(name);
        spec.flags =
            (c->flags & ~(PYMERGETIC_RXF_FLAG_STATE_MASK << PYMERGETIC_RXF_FLAG_STATE_SHIFT)) |
            (state << PYMERGETIC_RXF_FLAG_STATE_SHIFT);
        spec.generation = c->generation;
        spec.index = c->index;
        spec.payload_size = c->payload_size;
        spec.ref_count = static_cast<uint32_t>(kept_refs.size());
        pymergetic_rxf_handle created = 0;
        status = next->arena_.create(spec, &created);
        if (status != Status::Ok)
            return status;
        if (c->payload_size) {
            std::memcpy(next->arena_.payload(created), arena_.payload(old), c->payload_size);
            if (remap != nullptr) {
                status = remap->remap(*this, old, next->arena_.payload(created), c->payload_size,
                                      mapped);
                if (status != Status::Ok)
                    return status;
            }
        }
        if (!kept_refs.empty())
            std::memcpy(next->arena_.refs(created), kept_refs.data(),
                        kept_refs.size() * sizeof(pymergetic_rxf_ref));
    }
    pymergetic_rxf_arena_header *header = next->arena_.mutable_header();
    header->flags = arena_.header()->flags;
    header->root = PYMERGETIC_RXF_HANDLE_ROOT;
    const pymergetic_rxf_handle entry = arena_.header()->entry;
    if (entry != PYMERGETIC_RXF_HANDLE_NONE && (entry >= n || !keep[entry]))
        return Status::Dangling;
    header->entry = mapped(entry);
    status = next->rebuild_index();
    if (status != Status::Ok)
        return status;
    *out = std::move(next);
    return Status::Ok;
}

void Image::swap(Image &other) noexcept {
    std::swap(memory_, other.memory_);
    std::swap(arena_, other.arena_);
    std::swap(children_, other.children_);
}

Status Image::begin(uint64_t log_limit) {
    if (log_limit == 0)
        log_limit = memory_->limit();
    if (!log_ || log_limit_ != log_limit) {
        log_ = HostMemory::reserve(log_limit);
        if (!log_)
            return Status::OutOfMemory;
        log_limit_ = log_limit;
    }
    return arena_.begin(log_->memory());
}

Status Image::commit() {
    return arena_.commit();
}

Status Image::rollback() {
    Status status = arena_.rollback();
    if (status != Status::Ok)
        return status;
    return rebuild_index();
}

Status Image::clone(std::unique_ptr<Image> *out) const {
    const std::vector<uint8_t> bytes = to_bytes();
    return Image::load(bytes.data(), bytes.size(), memory_->limit(), out);
}

} // namespace pymergetic::rxf::core::object
