#ifndef PYMERGETIC_RXF_KINDS_KINDS_HPP
#define PYMERGETIC_RXF_KINDS_KINDS_HPP

/*
 * The names of the enumerations of the format. The values are the enums and
 * macros of ground.h; this is the one place that gives them names, and the
 * text form, the ground enumeration types, and the Python enums all read these
 * tables. Nothing here is stored in an image except through the bootstrap.
 */

#include <cstdint>
#include <span>
#include <string>
#include <string_view>

#include <pymergetic/rxf/core/ground.h>

namespace pymergetic::rxf::core::kinds {

struct Name {
    const char *name;
    uint32_t value;
};

using Table = std::span<const Name>;

/* What an object is (`rxf:kind`), the kind of a reference, and the other words of the format. */
extern const Table node_kinds;
extern const Table ref_kinds;
extern const Table bindings;
extern const Table owners;
extern const Table mobilities;
extern const Table forms;
extern const Table type_flags;
extern const Table field_flags;
extern const Table doc_kinds;
extern const Table doc_statuses;
extern const Table doc_codecs;
extern const Table doc_roles;

/* The name of a value, nullptr when the table has none. */
const char *name_of(Table table, uint32_t value);
/* The value of a name; false when the table has none. */
bool value_of(Table table, std::string_view name, uint32_t *out);

/* The names of the enumeration Types of the ground. Their enumerators are written as paths: `Form.Struct`. */
inline constexpr const char *kNodeKindType = "NodeKind";
inline constexpr const char *kRefKindType = "RefKind";
inline constexpr const char *kBindingType = "Binding";
inline constexpr const char *kOwnerKindType = "OwnerKind";
inline constexpr const char *kMobilityType = "Mobility";
inline constexpr const char *kFormType = "Form";
inline constexpr const char *kTypeFlagsType = "TypeFlags";
inline constexpr const char *kFieldFlagsType = "FieldFlags";
inline constexpr const char *kDocKindType = "DocKind";
inline constexpr const char *kDocStatusType = "DocStatus";
inline constexpr const char *kDocCodecType = "DocCodec";
inline constexpr const char *kDocRoleType = "DocRole";

/* The built-in type of a doc: its text, with the codec of that text (docs/22). */
inline constexpr const char *kDocType = "Doc";

/* An enumeration the ground carries as a Type object, so that Type and Field payloads are written by name. */
struct GroundEnum {
    const char *type;
    Table table;
    bool bits; // the values are bits that combine
};
std::span<const GroundEnum> ground_enums();

/* The path of a value of one of the ground enumerations (`Type.Name`), empty when the table has no name for it. */
std::string path_of(const char *type, Table table, uint32_t value);
/* The value of such a path; false when it is not `Type.Name` of this table. */
bool value_of_path(const char *type, Table table, std::string_view text, uint32_t *out);

/* The names of the objects of the ground that sit directly under the root. */
bool is_ground_name(std::string_view name);

} // namespace pymergetic::rxf::core::kinds

#endif
