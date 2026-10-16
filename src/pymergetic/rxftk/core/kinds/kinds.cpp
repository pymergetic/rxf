#include <pymergetic/rxftk/core/kinds/kinds.hpp>

namespace pymergetic::rxf::core::kinds {

namespace {

constexpr Name kNodeKinds[] = {
    {"Root", PYMERGETIC_RXF_NODE_ROOT},
    {"Group", PYMERGETIC_RXF_NODE_GROUP},
    {"Namespace", PYMERGETIC_RXF_NODE_NAMESPACE},
    {"Card", PYMERGETIC_RXF_NODE_CARD},
    {"Type", PYMERGETIC_RXF_NODE_TYPE},
    {"Field", PYMERGETIC_RXF_NODE_FIELD},
    {"Fn", PYMERGETIC_RXF_NODE_FN},
    {"Code", PYMERGETIC_RXF_NODE_CODE},
    {"Data", PYMERGETIC_RXF_NODE_DATA},
    {"Section", PYMERGETIC_RXF_NODE_SECTION},
    {"Limit", PYMERGETIC_RXF_NODE_LIMIT},
    {"Import", PYMERGETIC_RXF_NODE_IMPORT},
    {"Export", PYMERGETIC_RXF_NODE_EXPORT},
    {"View", PYMERGETIC_RXF_NODE_VIEW},
    {"Tool", PYMERGETIC_RXF_NODE_TOOL},
    {"Blob", PYMERGETIC_RXF_NODE_BLOB},
    {"Channel", PYMERGETIC_RXF_NODE_CHANNEL},
    {"Transaction", PYMERGETIC_RXF_NODE_TRANSACTION},
    {"Module", PYMERGETIC_RXF_NODE_MODULE},
    {"Enumerator", PYMERGETIC_RXF_NODE_ENUMERATOR},
    {"Alias", PYMERGETIC_RXF_NODE_ALIAS},
};

constexpr Name kRefKinds[] = {
    {"Call", PYMERGETIC_RXF_REF_CALL},
    {"Data", PYMERGETIC_RXF_REF_DATA},
    {"Entry", PYMERGETIC_RXF_REF_ENTRY},
    {"Type", PYMERGETIC_RXF_REF_TYPE},
    {"Import", PYMERGETIC_RXF_REF_IMPORT},
    {"Implementation", PYMERGETIC_RXF_REF_IMPLEMENTATION},
    {"Owner", PYMERGETIC_RXF_REF_OWNER},
    {"Doc", PYMERGETIC_RXF_REF_DOC},
};

constexpr Name kBindings[] = {
    {"Mandatory", PYMERGETIC_RXF_BINDING_MANDATORY},
    {"Optional", PYMERGETIC_RXF_BINDING_OPTIONAL},
};

constexpr Name kOwners[] = {
    {"System", PYMERGETIC_RXF_OWNER_SYSTEM},     {"User", PYMERGETIC_RXF_OWNER_USER},
    {"Application", PYMERGETIC_RXF_OWNER_APPLICATION}, {"Build", PYMERGETIC_RXF_OWNER_BUILD},
    {"Device", PYMERGETIC_RXF_OWNER_DEVICE},     {"External", PYMERGETIC_RXF_OWNER_EXTERNAL},
    {"Shared", PYMERGETIC_RXF_OWNER_SHARED},
};

constexpr Name kMobilities[] = {
    {"Movable", PYMERGETIC_RXF_MOBILITY_MOVABLE},
    {"Pinned", PYMERGETIC_RXF_MOBILITY_PINNED},
    {"Relocatable", PYMERGETIC_RXF_MOBILITY_RELOCATABLE},
};

constexpr Name kForms[] = {
    {"Meta", PYMERGETIC_RXF_FORM_META},         {"Void", PYMERGETIC_RXF_FORM_VOID},
    {"Bool", PYMERGETIC_RXF_FORM_BOOL},         {"Uint", PYMERGETIC_RXF_FORM_UINT},
    {"Sint", PYMERGETIC_RXF_FORM_SINT},         {"Float", PYMERGETIC_RXF_FORM_FLOAT},
    {"Bytes", PYMERGETIC_RXF_FORM_BYTES},       {"Struct", PYMERGETIC_RXF_FORM_STRUCT},
    {"Union", PYMERGETIC_RXF_FORM_UNION},       {"Enum", PYMERGETIC_RXF_FORM_ENUM},
    {"Array", PYMERGETIC_RXF_FORM_ARRAY},       {"Ref", PYMERGETIC_RXF_FORM_REF},
    {"Function", PYMERGETIC_RXF_FORM_FUNCTION}, {"Control", PYMERGETIC_RXF_FORM_CONTROL},
    {"Expression", PYMERGETIC_RXF_FORM_EXPRESSION}, {"Execution", PYMERGETIC_RXF_FORM_EXECUTION},
    {"Reserved16", PYMERGETIC_RXF_FORM_RESERVED_16}, {"Opaque", PYMERGETIC_RXF_FORM_OPAQUE},
};

constexpr Name kTypeFlags[] = {
    {"Variable", PYMERGETIC_RXF_TYPE_FLAG_VARIABLE},
    {"Signed", PYMERGETIC_RXF_TYPE_FLAG_SIGNED},
    {"SelfDescribing", PYMERGETIC_RXF_TYPE_FLAG_SELF_DESCRIBING},
    {"Bitset", PYMERGETIC_RXF_TYPE_FLAG_BITSET},
};

constexpr Name kFieldFlags[] = {
    {"Mutable", PYMERGETIC_RXF_FIELD_FLAG_MUTABLE},
    {"Optional", PYMERGETIC_RXF_FIELD_FLAG_OPTIONAL},
    {"Repeated", PYMERGETIC_RXF_FIELD_FLAG_REPEATED},
    {"Ref", PYMERGETIC_RXF_FIELD_FLAG_REF},
    {"Children", PYMERGETIC_RXF_FIELD_FLAG_CHILDREN},
    {"Inverse", PYMERGETIC_RXF_FIELD_FLAG_INVERSE},
};

constexpr Name kDocKinds[] = {
    {"Narrates", PYMERGETIC_RXF_DOC_KIND_NARRATES},
    {"Topic", PYMERGETIC_RXF_DOC_KIND_TOPIC},
    {"Todo", PYMERGETIC_RXF_DOC_KIND_TODO},
};

constexpr Name kDocStatuses[] = {
    {"Open", PYMERGETIC_RXF_DOC_STATUS_OPEN},
    {"Resolved", PYMERGETIC_RXF_DOC_STATUS_RESOLVED},
};

constexpr Name kDocCodecs[] = {
    {"Lz4", PYMERGETIC_RXF_DOC_CODEC_LZ4},
    {"Raw", PYMERGETIC_RXF_DOC_CODEC_RAW},
};

constexpr Name kDocRoles[] = {
    {"Narrates", PYMERGETIC_RXF_DOC_ROLE_NARRATES},
    {"Attaches", PYMERGETIC_RXF_DOC_ROLE_ATTACHES},
    {"References", PYMERGETIC_RXF_DOC_ROLE_REFERENCES},
    {"Parent", PYMERGETIC_RXF_DOC_ROLE_PARENT},
    {"Related", PYMERGETIC_RXF_DOC_ROLE_RELATED},
};

constexpr GroundEnum kGroundEnums[] = {
    {kNodeKindType, Table(kNodeKinds), false},
    {kRefKindType, Table(kRefKinds), false},
    {kBindingType, Table(kBindings), false},
    {kOwnerKindType, Table(kOwners), false},
    {kMobilityType, Table(kMobilities), false},
    {kFormType, Table(kForms), false},
    {kTypeFlagsType, Table(kTypeFlags), true},
    {kFieldFlagsType, Table(kFieldFlags), true},
    {kDocKindType, Table(kDocKinds), false},
    {kDocStatusType, Table(kDocStatuses), false},
    {kDocCodecType, Table(kDocCodecs), false},
    {kDocRoleType, Table(kDocRoles), false},
};

} // namespace

const Table node_kinds = kNodeKinds;
const Table ref_kinds = kRefKinds;
const Table bindings = kBindings;
const Table owners = kOwners;
const Table mobilities = kMobilities;
const Table forms = kForms;
const Table type_flags = kTypeFlags;
const Table field_flags = kFieldFlags;
const Table doc_kinds = kDocKinds;
const Table doc_statuses = kDocStatuses;
const Table doc_codecs = kDocCodecs;
const Table doc_roles = kDocRoles;

const char *name_of(Table table, uint32_t value) {
    for (const Name &entry : table)
        if (entry.value == value) return entry.name;
    return nullptr;
}

bool value_of(Table table, std::string_view name, uint32_t *out) {
    for (const Name &entry : table)
        if (name == entry.name) {
            *out = entry.value;
            return true;
        }
    return false;
}

std::span<const GroundEnum> ground_enums() { return kGroundEnums; }

std::string path_of(const char *type, Table table, uint32_t value) {
    const char *name = name_of(table, value);
    return name == nullptr ? std::string() : std::string(type) + "." + name;
}

bool value_of_path(const char *type, Table table, std::string_view text, uint32_t *out) {
    const std::string_view head(type);
    if (text.size() <= head.size() + 1 || text.substr(0, head.size()) != head || text[head.size()] != '.') return false;
    return value_of(table, text.substr(head.size() + 1), out);
}

bool is_ground_name(std::string_view name) {
    if (name == "uint32_t" || name == "uint64_t" || name == "Enumerator" || name == kDocType) return true;
    for (const GroundEnum &entry : kGroundEnums)
        if (name == entry.type) return true;
    return false;
}

} // namespace pymergetic::rxf::core::kinds
