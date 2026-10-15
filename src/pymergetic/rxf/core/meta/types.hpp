#ifndef PYMERGETIC_RXF_META_TYPES_HPP
#define PYMERGETIC_RXF_META_TYPES_HPP

/*
 * Self-description. Every payload is described by the Type object of its
 * object and the Field objects below it; nothing else in the core knows a
 * payload layout. This header holds
 *
 *   - Types:  the Type and Field objects of one image, read as plain structs,
 *   - Layout: the leaf values of a payload (scalars, references, raw bytes),
 *   - the codec between payload bytes and text attributes, where a field
 *     flagged REFERENCE is written as the path of its target,
 *   - the PayloadRemap that rewrites those handles when an image is compacted.
 *
 * Nothing here is part of the format; it is derived from the image on demand.
 */

#include <cstdint>
#include <functional>
#include <string>
#include <unordered_map>
#include <vector>

#include <pymergetic/rxf/core/object/image.hpp>

namespace pymergetic::rxf::core::meta {

using object::Image;
using object::Status;
using Handle = pymergetic_rxf_handle;

constexpr Handle kNone = PYMERGETIC_RXF_HANDLE_NONE;

/* The forms of a Type: the values of ground.h, under the names the code uses. */
enum Form : uint32_t {
    kFormMeta = PYMERGETIC_RXF_FORM_META, kFormVoid = PYMERGETIC_RXF_FORM_VOID, kFormBool = PYMERGETIC_RXF_FORM_BOOL,
    kFormUint = PYMERGETIC_RXF_FORM_UINT, kFormSint = PYMERGETIC_RXF_FORM_SINT, kFormFloat = PYMERGETIC_RXF_FORM_FLOAT,
    kFormBytes = PYMERGETIC_RXF_FORM_BYTES, kFormStruct = PYMERGETIC_RXF_FORM_STRUCT, kFormUnion = PYMERGETIC_RXF_FORM_UNION,
    kFormEnum = PYMERGETIC_RXF_FORM_ENUM, kFormArray = PYMERGETIC_RXF_FORM_ARRAY, kFormRef = PYMERGETIC_RXF_FORM_REF,
    kFormFunction = PYMERGETIC_RXF_FORM_FUNCTION, kFormControl = PYMERGETIC_RXF_FORM_CONTROL,
    kFormExpression = PYMERGETIC_RXF_FORM_EXPRESSION, kFormExecution = PYMERGETIC_RXF_FORM_EXECUTION,
    kFormReserved16 = PYMERGETIC_RXF_FORM_RESERVED_16, kFormOpaque = PYMERGETIC_RXF_FORM_OPAQUE, kFormCount = PYMERGETIC_RXF_FORM_COUNT
};

constexpr uint32_t kTypeVariable = PYMERGETIC_RXF_TYPE_FLAG_VARIABLE; // payload may be longer than `size`
constexpr uint32_t kTypeSelfDescribing = PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING;
constexpr uint32_t kTypeBitset = PYMERGETIC_RXF_TYPE_FLAG_BITSET;

struct FieldInfo {
    Handle id = kNone;
    std::string name;
    Handle value_type = kNone;
    uint64_t offset = 0;
    uint64_t count = 1;
    uint32_t flags = 0;
    Handle via = kNone; // INVERSE: the Field or Role the other objects point through

    bool is_member() const { return (flags & (PYMERGETIC_RXF_FIELD_FLAG_CHILDREN | PYMERGETIC_RXF_FIELD_FLAG_INVERSE)) != 0; }
};

struct TypeInfo {
    Handle id = kNone;
    std::string name;
    uint32_t form = 0;
    uint64_t size = 0;
    uint32_t align = 1;
    uint32_t flags = 0;
    uint32_t field_count = 0;
    uint32_t node_kind = 0; // the kind of the objects of this type; 0 means DATA
    Handle inner = kNone;
    Handle ret = kNone;
    std::vector<FieldInfo> fields;  // the values of the payload, by offset, then name
    std::vector<FieldInfo> members; // the declared relations (Children, Inverse): no payload, by name
    bool variable() const { return (flags & kTypeVariable) != 0; }
    bool self_describing() const { return (flags & kTypeSelfDescribing) != 0; }
    bool bitset() const { return (flags & kTypeBitset) != 0; }
};

/* The named values of an enumeration Type: its Enumerator children, by value. */
struct EnumInfo {
    bool bits = false;
    std::vector<std::pair<std::string, uint64_t>> items;
};

enum class LeafKind : uint8_t { Uint, Sint, Bool, Float, Hex, Ref, Enum };

/* One value of a payload as a text attribute. */
struct Leaf {
    std::string name;     // "a", "a.2" (element 2), "inner.x" (member); valid in an XML attribute name
    uint64_t offset = 0;  // inside the payload
    uint64_t size = 0;
    LeafKind kind = LeafKind::Hex;
    bool optional = false; // Ref: the field may be unset
    bool derived = false;  // recomputed on load, never written
    uint64_t def = 0;      // value of an omitted attribute (scalars); the count of a Field is 1
    Handle enumeration = kNone; // Enum: the Type object whose Enumerator children name the values
};

struct Layout {
    bool raw = false;      // no usable field list: the whole payload is one `_data` value
    uint64_t prefix = 0;   // bytes covered by leaves; the rest is the `_data` tail
    bool fixed = false;    // payload size must equal `prefix`
    std::vector<Leaf> leaves;
    std::string error;     // why the type cannot be used (empty when it can)
    bool incomplete = false; // the error is a Field or value type that does not exist (yet)
};

struct Attr {
    std::string name;
    std::string value;
    bool is_path = false; // the value starts with the path of an object (a reference field, an enumerator), which an alias may shorten
};

/* Attribute of the raw or tail bytes of a payload. */
inline constexpr const char *kDataAttr = "data";

/* Hexadecimal text to bytes and back: how payload bytes sit in an attribute. */
bool from_hex(const std::string &text, std::vector<uint8_t> *out);
std::string hex_of(const uint8_t *p, uint64_t n);

class Types {
public:
    explicit Types(const Image *image) : image_(image) {}
    const Image &image() const { return *image_; }
    void rebind(const Image *image) { image_ = image; invalidate(); }
    /* The image changed in a Type or Field object. */
    void invalidate();

    /* The description of a Type object, nullptr when `type` is not one. */
    const TypeInfo *get(Handle type);
    /* The handle of the Type object with this name, kNone when there is none. */
    Handle find(const std::string &name);
    /* Names used by more than one Type object (the element name must be unique). */
    std::vector<std::string> duplicate_names();
    const Layout &layout(Handle type);
    /* The named values of an enumeration Type, nullptr when `type` is not one. */
    const EnumInfo *enumeration(Handle type);

private:
    const Image *image_;
    std::unordered_map<Handle, TypeInfo> info_;
    std::unordered_map<Handle, Layout> layouts_;
    std::unordered_map<Handle, EnumInfo> enums_;
    std::unordered_map<std::string, Handle> by_name_;
    std::vector<std::string> duplicates_;
    bool names_valid_ = false;
    void build_names();
    bool build_layout(Handle type, Layout *out, int depth);
};

/* A reference inside a payload whose target is not known yet. */
struct PendingRef {
    uint64_t offset = 0; // inside the payload
    std::string path;
    bool optional = false;
};

using Resolver = std::function<Handle(const std::string &)>;

/* Payload bytes to attributes. A unset reference and a zero scalar are omitted. */
Status decode_payload(Types &types, Handle object, std::vector<Attr> *out, std::string *error);
/* Attributes to payload bytes. A reference whose path `resolve` cannot answer is
 * left unset and listed in `pending`. Unknown attributes are an error. `parent`
 * is the handle of the parent object, which a Field takes as its owner. */
Status encode_payload(Types &types, Handle type, const std::vector<Attr> &attrs, const Resolver &resolve,
                      std::vector<uint8_t> *payload, std::vector<PendingRef> *pending, std::string *error,
                      Handle parent = kNone);

/* Everything that points at `targets` from outside it: parent, type, payload handles, and references. */
std::vector<std::string> referrers(Types &types, const std::vector<Handle> &targets);

/* The compaction hook: rewrites payload handles of every object. */
class Remapper : public object::PayloadRemap {
public:
    explicit Remapper(Types *types) : types_(types) {}
    Status remap(const Image &source, Handle object, uint8_t *payload, uint64_t size,
                 const std::function<Handle(Handle)> &map) const override;

private:
    Types *types_;
};

/* A successor with remapped payload handles. */
Status compact(const Image &image, uint64_t index_capacity, std::unique_ptr<Image> *out);

/* Structural problems of an image: dangling handles, payloads that do not fit
 * their type, malformed Type and Field objects. Empty when the image links. */
struct Issue {
    std::string path;
    std::string message;
};
std::vector<Issue> check(Types &types);

/* The text of a Doc object, decompressed on demand: the text face of a doc
 * (docs/22). BadName when `doc` is not a Doc object, BadImage when its bytes
 * are not the text its codec words name. */
Status doc_text(Types &types, Handle doc, std::string *out, std::string *error);

/* The paths that the ground fixes: Type, Field, uint32_t, uint64_t, Enumerator, the enumerations of the
 * format, and everything below them. */
bool is_ground(const Image &image, Handle object);

/* Aliases. A path in an object that starts with '@' names an Alias object of the enclosing Module:
 * `@roles.call.body` is the alias `roles` followed by `.call.body`. */
inline constexpr char kAliasMark = '@';
/* The nearest Module that is `object` or above it, kNone when there is none. */
Handle scope_of(const Image &image, Handle object);
/* The handle at `text` as seen from `object`: an absolute path, or an alias path. kNone when it does not
 * resolve (yet): the alias or its target is missing. */
Handle resolve_in(Types &types, Handle object, const std::string &text);
/* The Alias objects of a Module as (name, absolute target path), for shortening paths on the way out. */
std::vector<std::pair<std::string, std::string>> aliases_of(Types &types, Handle module);
/* The index in `aliases` of the alias that writes `path` shorter (the longest one that is a prefix), -1 when none does. */
int alias_for(const std::vector<std::pair<std::string, std::string>> &aliases, const std::string &path);
/* `path` as short as the aliases allow ("@name.rest"), or `path` itself. */
std::string shorten(const std::vector<std::pair<std::string, std::string>> &aliases, const std::string &path);
/* An object whose written paths go through an Alias: `user` is written with `alias`. Each pair once. */
/* The paths of an object that the text form writes and an alias may shorten: reference fields, enumerators, the
 * targets and roles of its references. Absolute, in the order they are written. */
std::vector<std::string> written_paths(Types &types, Handle object);
struct AliasUse {
    Handle alias;
    Handle user;
};
std::vector<AliasUse> alias_uses(Types &types);

/* A declared relation of an object and what is in it. */
struct Member {
    Handle field = kNone;        // the Field object that declares it
    std::vector<Handle> items;   // in order
};

/* The siblings in their order: by position (the `index` field of the type when it has one, else the `index` of the cell),
 * then by name with a trailing `[n]` read as a number. */
std::vector<Handle> ordered(Types &types, std::vector<Handle> handles);

/* The Children and Inverse fields of the type of the object, each with its objects. */
std::vector<Member> members(Types &types, Handle object);
/* The Alias is marked as a deliberate name of its module (the enumerator REEXPORT in its `flags`), used or not. */
inline constexpr const char *kAliasReexport = "Reexport";
bool alias_reexported(Types &types, Handle alias);

// ---- the hybrid module tree (docs/23 §4.6) --------------------------------
//
// The module tree IS the image tree: a module is a named cell under a root, no filesystem, no
// `.mpy`, no second storage. The four roots are the Quad. The walk answers the trichotomy
// `mp_import_stat` returns (package = MP_IMPORT_STAT_DIR, module = MP_IMPORT_STAT_FILE,
// none = MP_IMPORT_STAT_NO), so the MicroPython port's fill is one call into `module_stat`.

/* The one answer mp_import_stat gives. */
enum class ModuleStat : uint32_t { Package, Module, None };

/* The word of a trichotomy answer: "package", "module" or "none". */
const char *module_stat_word(ModuleStat stat);

/* The Quad: the four roots of the module tree (px = extern/cpython, pm = mpy, pt = thirdparty,
   py = micro) and nothing else. They are ordinary Module cells like any other. */
const std::vector<std::string> &module_roots();

/* The trichotomy of one module name over the named cells of the image. Each segment is a child
   name of a Module cell (NodeKind 19); the root plays the Module role at the top level. A
   package answers Package (it has sub-modules), a leaf module or a container with program
   children answers Module, anything else None. A name that does not spell one clear module is
   refused in `*error` (unknown root, ambiguous name, dead handle) — never a wrong trichotomy. */
ModuleStat module_stat(const Image &image, const std::string &name, std::string *error);

/* The sub-module names of a package (what makes it one), in name order; empty for a leaf module
   and for none. Refused exactly like `module_stat`. */
std::vector<std::string> module_children(const Image &image, const std::string &name, std::string *error);

/* A name an XML element or attribute can carry: letters, digits, '_', '-', '.', not starting with a digit. */
bool is_symbol(const std::string &name);
/* The attributes of the text form that are not in its namespace (name, path); no Field may have these names. */
bool is_reserved_attribute(const std::string &name);
/* The one element of the text form that is not in its namespace (delta); no Type may have this name. */
bool is_reserved_element(const std::string &name);


} // namespace pymergetic::rxf::core::meta

#endif
