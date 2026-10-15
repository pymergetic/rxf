#include <pymergetic/rxf/core/xml/xml.hpp>

#include <algorithm>
#include <charconv>
#include <set>

#include <pugixml.hpp>

#include <pymergetic/rxf/core/doc/codec.hpp>
#include <pymergetic/rxf/core/kinds/kinds.hpp>

namespace pymergetic::rxf::core::xml {

namespace {

using delta::Create;
using delta::Delete;
using delta::Delta;
using delta::Entry;
using delta::RefSpec;
using delta::Set;
using meta::Attr;

constexpr int kMaxDepth = 256;

// What belongs to the format is in this namespace; everything else is the data of an object.
constexpr const char *kNamespace = "urn:pymergetic:rxf";
constexpr const char *kPrefix = "rxf:";

bool is_format(const std::string &name) { return name.compare(0, 4, kPrefix) == 0; }

/* A word of the format is the path of an enumerator of the ground (`RefKind.Data`), never a bare word or a number. */
bool by_path(const char *type, kinds::Table table, const std::string &text, uint32_t *out) { return kinds::value_of_path(type, table, text, out); }

bool number(const std::string &text, uint64_t *out) {
    const char *b = text.data(), *e = b + text.size();
    const auto r = std::from_chars(b, e, *out);
    return !text.empty() && r.ec == std::errc() && r.ptr == e;
}

// ---- reader: pugixml parses, this walks the tree -------------------------------------------------

class Builder {
public:
    Builder(const std::string &text, Delta *out, std::string *error, const std::string *default_parent)
        : text_(text), out_(out), error_(error), default_parent_(default_parent) {}

    bool root(const pugi::xml_document &doc) {
        pugi::xml_node root;
        for (pugi::xml_node n : doc.children()) {
            if (n.type() == pugi::node_doctype) return fail(n, "a DTD is not supported");
            if (n.type() != pugi::node_element) continue;
            if (root) return fail(n, "content after the root element");
            root = n;
        }
        if (!root) return fail(doc, "expected the root element");
        if (std::string(root.name()) != "delta") return fail(root, "the root element must be <delta>");
        for (pugi::xml_attribute a : root.attributes()) {
            if (std::string(a.name()) != "xmlns:rxf" || std::string(a.value()) != kNamespace)
                return fail(root, std::string("<delta> takes only xmlns:rxf=\"") + kNamespace + "\"");
        }
        for (pugi::xml_node e : root.children()) {
            if (!content(e)) return false;
            const std::string name = e.name();
            if (name == "rxf:set") {
                if (!set(e)) return false;
            } else if (name == "rxf:delete") {
                if (!del(e)) return false;
            } else if (name == "rxf:entry") {
                if (!entry(e)) return false;
            } else if (!object(e, std::string(), true, 1)) {
                return false;
            }
        }
        return true;
    }

private:
    const std::string &text_;
    Delta *out_;
    std::string *error_;
    const std::string *default_parent_; // the parent of a top-level element that names none; nullptr: the root

    size_t line_of(const pugi::xml_node &n) const {
        const ptrdiff_t at = n.offset_debug();
        const size_t end = at < 0 ? 0 : std::min<size_t>(static_cast<size_t>(at), text_.size());
        return 1 + static_cast<size_t>(std::count(text_.begin(), text_.begin() + static_cast<ptrdiff_t>(end), '\n'));
    }

    bool fail(const pugi::xml_node &n, const std::string &message) {
        *error_ = "line " + std::to_string(line_of(n)) + ": " + message;
        return false;
    }

    // Only elements may sit between elements: text (other than blanks, which pugixml drops) is an error.
    bool content(const pugi::xml_node &n) {
        switch (n.type()) {
        case pugi::node_element: return true;
        case pugi::node_pcdata: return fail(n.parent(), "text is not allowed here");
        case pugi::node_cdata: return fail(n.parent(), "CDATA is not supported");
        case pugi::node_doctype: return fail(n, "a DTD is not supported");
        default: return fail(n, "not supported inside an element");
        }
    }

    // The attributes of an element, in order, each name once.
    bool attributes(const pugi::xml_node &e, std::vector<std::pair<std::string, std::string>> *out) {
        std::set<std::string> seen;
        for (pugi::xml_attribute a : e.attributes()) {
            if (!seen.insert(a.name()).second) return fail(e, std::string("attribute ") + a.name() + " is given twice");
            const std::string n = a.name();
            if (n.find(':') != std::string::npos && !is_format(n)) return fail(e, "unknown attribute " + n);
            out->emplace_back(a.name(), a.value());
        }
        return true;
    }

    bool ref(const pugi::xml_node &e, RefSpec *r) {
        std::vector<std::pair<std::string, std::string>> attrs;
        if (!attributes(e, &attrs)) return false;
        for (const auto &a : attrs) {
            if (a.first == "kind") {
                if (!by_path(kinds::kRefKindType, kinds::ref_kinds, a.second, &r->kind)) return fail(e, "unknown reference kind " + a.second);
            } else if (a.first == "binding") {
                if (!by_path(kinds::kBindingType, kinds::bindings, a.second, &r->binding)) return fail(e, "unknown binding " + a.second);
            } else if (a.first == "role") {
                r->role = a.second;
            } else if (a.first == "index") {
                uint64_t v;
                if (!number(a.second, &v) || v > 0xFFFFFFFFULL) return fail(e, "bad index");
                r->index = static_cast<uint32_t>(v);
            } else if (a.first == "target") {
                r->target = a.second;
            } else {
                return fail(e, "unknown attribute " + a.first + " on <rxf:ref>");
            }
        }
        if (e.first_child()) return fail(e, "<rxf:ref> has no content");
        return true;
    }

    bool object(const pugi::xml_node &e, const std::string &parent, bool top, int depth) {
        if (depth > kMaxDepth) return fail(e, "elements nest too deeply");
        // <rxf:doc> is the docstring of a Doc object: its text is its content, its role says
        // where it is placed (docs/22). The type name <Doc> says the same thing. Everything
        // else is an ordinary object element.
        const bool is_doc = std::string(e.name()) == "rxf:doc" || std::string(e.name()) == kinds::kDocType;
        Create op;
        op.type = is_doc ? kinds::kDocType : e.name();
        op.parent = top && default_parent_ != nullptr ? *default_parent_ : parent;
        if (!is_doc && (is_format(op.type) || meta::is_reserved_element(op.type) || op.type.find(':') != std::string::npos))
            return fail(e, "unknown element <" + op.type + ">");
        std::vector<std::pair<std::string, std::string>> attrs;
        if (!attributes(e, &attrs)) return false;
        bool named = false;
        uint32_t codec = PYMERGETIC_RXF_DOC_CODEC_LZ4;
        std::string body;
        for (const auto &a : attrs) {
            const std::string &k = a.first;
            if (k == "name") {
                op.name = a.second;
                named = true;
            } else if (is_doc && k == "role") {
                // The placement is written on the object that holds the doc; see the children below.
                if (top) return fail(e, "a role places a doc on the object that holds it; nest <rxf:doc> in that object");
            } else if (is_doc && k == "codec") {
                if (!by_path(kinds::kDocCodecType, kinds::doc_codecs, a.second, &codec)) return fail(e, "unknown codec " + a.second);
            } else if (is_doc && k == "rxf:data") {
                return fail(e, "the text of a doc is its content, not rxf:data");
            } else if (k == "rxf:parent") {
                if (!top) return fail(e, "rxf:parent is only for top-level elements");
                op.parent = a.second;
            } else if (k == "rxf:kind") {
                uint32_t v;
                if (!by_path(kinds::kNodeKindType, kinds::node_kinds, a.second, &v)) return fail(e, "unknown kind " + a.second);
                op.kind = v;
            } else if (k == "rxf:owner") {
                if (!by_path(kinds::kOwnerKindType, kinds::owners, a.second, &op.owner_kind)) return fail(e, "unknown owner kind " + a.second);
            } else if (k == "rxf:mobility") {
                if (!by_path(kinds::kMobilityType, kinds::mobilities, a.second, &op.mobility)) return fail(e, "unknown mobility " + a.second);
            } else if (k == "rxf:generation") {
                if (!number(a.second, &op.generation)) return fail(e, "bad generation");
            } else if (k == "rxf:index") {
                uint64_t v = 0;
                if (!number(a.second, &v) || v > 0xFFFFFFFFULL) return fail(e, "bad index");
                op.index = static_cast<uint32_t>(v);
            } else if (k == "rxf:data") {
                op.attrs.push_back({meta::kDataAttr, a.second});
            } else if (is_format(k)) {
                return fail(e, "unknown attribute " + k);
            } else if (k == "path") {
                return fail(e, "path is not an attribute of an object");
            } else {
                op.attrs.push_back({k, a.second});
            }
        }
        if (!named) return fail(e, "<" + op.type + "> needs a name");
        const std::string path = op.parent.empty() ? op.name : op.parent + "." + op.name;
        std::vector<pugi::xml_node> nested;
        std::vector<RefSpec> placements;
        for (pugi::xml_node c : e.children()) {
            if (c.type() == pugi::node_pcdata) {
                if (!is_doc) return fail(e, "text is not allowed here");
                body += c.value();
                continue;
            }
            if (!content(c)) return false;
            const std::string name = c.name();
            if (name == "rxf:ref") {
                RefSpec r;
                if (!ref(c, &r)) return false;
                op.refs.push_back(std::move(r));
            } else if (name == "rxf:doc" || name == kinds::kDocType) {
                // A doc nested in an object is placed on it when it carries a role: one edge,
                // written on the child, said once on the parent as a RefKind.Doc reference.
                const pugi::xml_attribute role = c.attribute("role");
                if (role && role.value()[0] != '\0') {
                    const char *placed = c.attribute("name").value();
                    if (placed[0] == '\0') return fail(c, "<rxf:doc> needs a name");
                    RefSpec r;
                    r.kind = PYMERGETIC_RXF_REF_DOC;
                    r.role = role.value();
                    r.target = path + "." + placed;
                    placements.push_back(std::move(r));
                }
                nested.push_back(c);
            } else {
                nested.push_back(c);
            }
        }
        op.refs.insert(op.refs.begin(), placements.begin(), placements.end());
        if (is_doc) {
            // The pin (docs/22): a doc with an empty body is not written in DocCodec.Raw;
            // the import refuses it rather than storing a degenerate payload.
            if (codec == PYMERGETIC_RXF_DOC_CODEC_RAW && body.empty())
                return fail(e, "a doc with an empty body is not written in DocCodec.Raw");
            const doc::Stored stored = doc::store(codec, body);
            if (stored.codec != PYMERGETIC_RXF_DOC_CODEC_LZ4) {
                const std::string word = kinds::path_of(kinds::kDocCodecType, kinds::doc_codecs, stored.codec);
                if (word.empty()) return fail(e, "the text of the doc is stored in an unknown codec");
                op.attrs.push_back({"codec", word});
            }
            op.attrs.push_back({meta::kDataAttr, meta::hex_of(stored.bytes.data(), stored.bytes.size())});
        }
        out_->ops.push_back(std::move(op));
        for (const pugi::xml_node &c : nested)
            if (!object(c, path, false, depth + 1)) return false;
        return true;
    }

    bool set(const pugi::xml_node &e) {
        Set op;
        bool has_path = false;
        std::vector<std::pair<std::string, std::string>> attrs;
        if (!attributes(e, &attrs)) return false;
        for (const auto &a : attrs) {
            const std::string &k = a.first;
            if (k == "path") {
                op.path = a.second;
                has_path = true;
            } else if (k == "rxf:mobility") {
                uint32_t v;
                if (!by_path(kinds::kMobilityType, kinds::mobilities, a.second, &v)) return fail(e, "unknown mobility " + a.second);
                op.mobility = v;
            } else if (k == "rxf:owner") {
                uint32_t v;
                if (!by_path(kinds::kOwnerKindType, kinds::owners, a.second, &v)) return fail(e, "unknown owner kind " + a.second);
                op.owner_kind = v;
            } else if (k == "rxf:generation") {
                uint64_t v;
                if (!number(a.second, &v)) return fail(e, "bad generation");
                op.generation = v;
            } else if (k == "rxf:index") {
                uint64_t v = 0;
                if (!number(a.second, &v) || v > 0xFFFFFFFFULL) return fail(e, "bad index");
                op.index = static_cast<uint32_t>(v);
            } else if (k == "rxf:refs") {
                if (a.second != "none") return fail(e, "rxf:refs is \"none\" (to remove all references) or children <rxf:ref/>");
                op.has_refs = true;
            } else if (k == "rxf:data") {
                op.attrs.push_back({meta::kDataAttr, a.second});
            } else if (is_format(k) || k == "name") {
                return fail(e, k + " cannot be changed by <rxf:set>");
            } else {
                op.attrs.push_back({k, a.second});
            }
        }
        if (!has_path) return fail(e, "<rxf:set> needs a path");
        for (pugi::xml_node c : e.children()) {
            if (!content(c)) return false;
            if (std::string(c.name()) != "rxf:ref") return fail(c, "only <rxf:ref> may sit inside <rxf:set>");
            RefSpec r;
            if (!ref(c, &r)) return false;
            op.refs.push_back(std::move(r));
            op.has_refs = true;
        }
        out_->ops.push_back(std::move(op));
        return true;
    }

    bool entry(const pugi::xml_node &e) {
        Entry op;
        std::vector<std::pair<std::string, std::string>> attrs;
        if (!attributes(e, &attrs)) return false;
        for (const auto &a : attrs) {
            if (a.first != "path") return fail(e, "unknown attribute " + a.first + " on <rxf:entry>");
            op.path = a.second;
        }
        if (e.first_child()) return fail(e, "<rxf:entry> has no content");
        out_->ops.push_back(std::move(op));
        return true;
    }

    bool del(const pugi::xml_node &e) {
        Delete op;
        bool has_path = false;
        std::vector<std::pair<std::string, std::string>> attrs;
        if (!attributes(e, &attrs)) return false;
        for (const auto &a : attrs) {
            if (a.first != "path") return fail(e, "unknown attribute " + a.first + " on <rxf:delete>");
            op.path = a.second;
            has_path = true;
        }
        if (!has_path || e.first_child()) return fail(e, "<rxf:delete> is <rxf:delete path=\"...\"/>");
        out_->ops.push_back(std::move(op));
        return true;
    }
};

// ---- writer: the delta becomes a pugixml tree, pugixml prints it ---------------------------------

struct StringWriter : pugi::xml_writer {
    std::string *out;
    explicit StringWriter(std::string *o) : out(o) {}
    void write(const void *data, size_t size) override { out->append(static_cast<const char *>(data), size); }
};

class Printer {
public:
    Printer(std::string *error, const std::string *default_parent) : error_(error), default_parent_(default_parent) {}

    pugi::xml_document doc;

    bool start() {
        pugi::xml_node decl = doc.append_child(pugi::node_declaration);
        decl.append_attribute("version") = "1.0";
        decl.append_attribute("encoding") = "UTF-8";
        root_ = doc.append_child("delta");
        root_.append_attribute("xmlns:rxf") = kNamespace;
        return true;
    }

    bool add(const delta::Op &op) {
        if (const Create *c = std::get_if<Create>(&op)) return create(*c);
        if (const Set *s = std::get_if<Set>(&op)) return set(*s);
        if (const Entry *en = std::get_if<Entry>(&op)) {
            open_.clear();
            return text(root_.append_child("rxf:entry").append_attribute("path"), en->path);
        }
        open_.clear();
        return text(root_.append_child("rxf:delete").append_attribute("path"), std::get<Delete>(op).path);
    }

private:
    std::string *error_;
    const std::string *default_parent_; // top-level elements of this parent do not name it; nullptr: all name theirs
    pugi::xml_node root_;
    struct Open {
        std::string path;
        pugi::xml_node node;
    };
    std::vector<Open> open_; // the chain of elements an op may nest into

    bool bad(const std::string &message) {
        *error_ = message;
        return false;
    }

    // pugixml keeps C strings: a NUL would cut a value short.
    bool text(pugi::xml_attribute a, const std::string &value) {
        if (value.find('\0') != std::string::npos) return bad("a value contains a NUL byte");
        a = value.c_str();
        return true;
    }

    bool attribute(pugi::xml_node n, const char *key, const std::string &value) {
        return text(n.append_attribute(key), value);
    }

    bool write_refs(pugi::xml_node n, const std::vector<RefSpec> &refs) {
        for (const RefSpec &r : refs) {
            pugi::xml_node e = n.append_child("rxf:ref");
            if (!r.role.empty() && !attribute(e, "role", r.role)) return false;
            if (r.index != 0) e.append_attribute("index") = std::to_string(r.index).c_str();
            const std::string kind = kinds::path_of(kinds::kRefKindType, kinds::ref_kinds, r.kind);
            if (kind.empty()) return bad("a reference has the unknown kind " + std::to_string(r.kind));
            e.append_attribute("kind") = kind.c_str();
            if (r.binding != PYMERGETIC_RXF_BINDING_MANDATORY) {
                const std::string binding = kinds::path_of(kinds::kBindingType, kinds::bindings, r.binding);
                if (binding.empty()) return bad("a reference has the unknown binding " + std::to_string(r.binding));
                e.append_attribute("binding") = binding.c_str();
            }
            if (!attribute(e, "target", r.target)) return false;
        }
        return true;
    }

    bool field(pugi::xml_node n, const Attr &a, const std::string &owner) {
        if (a.name == meta::kDataAttr) return attribute(n, "rxf:data", a.value);
        if (!meta::is_symbol(a.name) || meta::is_reserved_attribute(a.name) || a.name[0] == '_')
            return bad("the field " + a.name + " of " + owner + " cannot be written as an attribute");
        return attribute(n, a.name.c_str(), a.value);
    }

    bool create(const Create &c) {
        if (!meta::is_symbol(c.type) || c.type[0] == '_' || meta::is_reserved_element(c.type)) {
            *error_ = "the type name " + c.type + " is not a valid element name";
            return false;
        }
        // The parent is the last open element of the chain, or an ancestor of it: nest there.
        size_t keep = open_.size();
        while (keep > 0 && open_[keep - 1].path != c.parent) keep--;
        if (c.parent.empty()) keep = 0;
        open_.resize(keep);
        const bool nested = !open_.empty();
        const std::string path = c.parent.empty() ? c.name : c.parent + "." + c.name;
        const bool is_doc = c.type == kinds::kDocType;
        // A doc is written as <rxf:doc>: its text is its content (docs/22), the one object
        // element that is not spelled with its type name.
        const char *element = is_doc ? "rxf:doc" : c.type.c_str();
        pugi::xml_node n = nested ? open_.back().node.append_child(element) : root_.append_child(element);
        if (!attribute(n, "name", c.name)) return false;
        std::string body;
        if (is_doc) {
            // The text of a doc is its content, decoded from the tail its codec words name.
            uint32_t codec = PYMERGETIC_RXF_DOC_CODEC_LZ4;
            std::vector<uint8_t> tail;
            for (const Attr &a : c.attrs) {
                if (a.name == "codec" && !by_path(kinds::kDocCodecType, kinds::doc_codecs, a.value, &codec))
                    return bad("the doc " + c.name + " is written in an unknown codec " + a.value);
                if (a.name == meta::kDataAttr && !meta::from_hex(a.value, &tail)) return bad("the text of the doc " + c.name + " is not hexadecimal");
            }
            std::string problem;
            if (!doc::unstore(codec, tail.data(), tail.size(), &body, &problem)) return bad("the text of the doc " + c.name + " does not decode: " + problem);
            if (body.find('\0') != std::string::npos) return bad("a value contains a NUL byte");
            if (nested && !fold(n, path)) return false;
        }
        if (!nested) {
            if (default_parent_ != nullptr && c.parent != *default_parent_) return bad(c.name + " sits in " + c.parent + ", not in " + *default_parent_);
            if (default_parent_ == nullptr && !c.parent.empty() && !attribute(n, "rxf:parent", c.parent)) return false;
        }
        if (c.kind != delta::kKindDerived) {
            const std::string k = kinds::path_of(kinds::kNodeKindType, kinds::node_kinds, c.kind);
            if (k.empty()) return bad(c.name + " has the unknown kind " + std::to_string(c.kind));
            if (!attribute(n, "rxf:kind", k)) return false;
        }
        if (c.owner_kind != PYMERGETIC_RXF_OWNER_SHARED) {
            const std::string k = kinds::path_of(kinds::kOwnerKindType, kinds::owners, c.owner_kind);
            if (k.empty()) return bad(c.name + " has the unknown owner " + std::to_string(c.owner_kind));
            n.append_attribute("rxf:owner") = k.c_str();
        }
        if (c.generation != 0) n.append_attribute("rxf:generation") = std::to_string(c.generation).c_str();
        if (c.index != 0) n.append_attribute("rxf:index") = std::to_string(c.index).c_str();
        if (c.mobility != PYMERGETIC_RXF_MOBILITY_MOVABLE) {
            const std::string k = kinds::path_of(kinds::kMobilityType, kinds::mobilities, c.mobility);
            if (k.empty()) return bad(c.name + " has the unknown mobility " + std::to_string(c.mobility));
            n.append_attribute("rxf:mobility") = k.c_str();
        }
        for (const Attr &a : c.attrs) {
            if (is_doc && a.name == meta::kDataAttr) continue; // the text is the content of <rxf:doc>
            if (!field(n, a, c.type)) return false;
        }
        if (is_doc && !body.empty()) n.append_child(pugi::node_pcdata).set_value(body.c_str());
        if (!write_refs(n, c.refs)) return false;
        open_.push_back({path, n});
        return true;
    }

    /* The placement of a doc child is written as its role attribute: one edge, said once.
     * The placement is the first reference of the object that holds the doc; a placement that
     * this cannot say that way (its index or binding says more) stays a <rxf:ref>. */
    bool fold(pugi::xml_node n, const std::string &path) {
        pugi::xml_node holder = open_.back().node;
        pugi::xml_node first = holder.child("rxf:ref");
        if (!first) return true;
        const char *role = first.attribute("role").value();
        if (role[0] == '\0' || first.attribute("index") || first.attribute("binding")) return true;
        if (std::string(first.attribute("kind").value()) != kinds::path_of(kinds::kRefKindType, kinds::ref_kinds, PYMERGETIC_RXF_REF_DOC)) return true;
        if (std::string(first.attribute("target").value()) != path) return true;
        if (!attribute(n, "role", role)) return false;
        holder.remove_child(first);
        return true;
    }

    bool set(const Set &s) {
        open_.clear();
        pugi::xml_node n = root_.append_child("rxf:set");
        if (!attribute(n, "path", s.path)) return false;
        if (s.mobility) {
            const std::string k = kinds::path_of(kinds::kMobilityType, kinds::mobilities, *s.mobility);
            if (k.empty()) return bad("<set> has an unknown mobility");
            n.append_attribute("rxf:mobility") = k.c_str();
        }
        if (s.owner_kind) {
            const std::string k = kinds::path_of(kinds::kOwnerKindType, kinds::owners, *s.owner_kind);
            if (k.empty()) return bad("<set> has an unknown owner");
            n.append_attribute("rxf:owner") = k.c_str();
        }
        if (s.generation) n.append_attribute("rxf:generation") = std::to_string(*s.generation).c_str();
        if (s.index) n.append_attribute("rxf:index") = std::to_string(*s.index).c_str();
        if (s.has_refs && s.refs.empty()) n.append_attribute("rxf:refs") = "none";
        for (const Attr &a : s.attrs)
            if (!field(n, a, "<set>")) return false;
        return write_refs(n, s.refs);
    }
};

} // namespace

Status parse(const std::string &text, Delta *out, std::string *error, const std::string *default_parent) {
    error->clear();
    out->ops.clear();
    pugi::xml_document doc;
    const size_t bom = text.compare(0, 3, "\xEF\xBB\xBF") == 0 ? 3 : 0;
    const unsigned int flags = pugi::parse_escapes | pugi::parse_wconv_attribute | pugi::parse_eol | pugi::parse_cdata | pugi::parse_doctype;
    const pugi::xml_parse_result result = doc.load_buffer(text.data() + bom, text.size() - bom, flags, pugi::encoding_utf8);
    if (!result) {
        const size_t at = std::min<size_t>(static_cast<size_t>(result.offset), text.size() - bom);
        const size_t line = 1 + static_cast<size_t>(std::count(text.begin() + static_cast<ptrdiff_t>(bom), text.begin() + static_cast<ptrdiff_t>(bom + at), '\n'));
        *error = "line " + std::to_string(line) + ": " + result.description();
        return Status::BadImage;
    }
    // Offsets of the nodes are relative to the buffer without the BOM.
    const std::string body = text.substr(bom);
    Builder builder(body, out, error, default_parent);
    if (!builder.root(doc)) return Status::BadImage;
    return Status::Ok;
}

Status write(const Delta &delta, std::string *out, std::string *error, const std::string *default_parent) {
    out->clear();
    Printer printer(error, default_parent);
    printer.start();
    for (const delta::Op &op : delta.ops)
        if (!printer.add(op)) return Status::BadName;
    StringWriter writer(out);
    printer.doc.save(writer, "  ", pugi::format_indent, pugi::encoding_utf8);
    return Status::Ok;
}

} // namespace pymergetic::rxf::core::xml
