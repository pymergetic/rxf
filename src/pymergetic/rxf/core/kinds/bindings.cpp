#include <nanobind/nanobind.h>

#include <pymergetic/rxf/core/bindings.hpp>
#include <pymergetic/rxf/core/kinds/kinds.hpp>

namespace pymergetic::rxf::core::bindings {

namespace {

/* One Python enum per table of kinds/kinds.hpp. The enum types are empty on the C++ side: the
 * members come from the table, so the names exist once. */
enum class NodeKind : uint32_t {};
enum class RefKind : uint32_t {};
enum class Binding : uint32_t {};
enum class OwnerKind : uint32_t {};
enum class Mobility : uint32_t {};
enum class Form : uint32_t {};
enum class TypeFlags : uint32_t {};
enum class FieldFlags : uint32_t {};

template <class E, class... Extra> void bind_enum(nb::module_ &m, const char *name, kinds::Table table, const char *doc, Extra... extra) {
    nb::enum_<E> e(m, name, doc, extra...);
    for (const kinds::Name &entry : table) e.value(entry.name, static_cast<E>(entry.value));
}

} // namespace

/* The words of the format as enums: what an object is, what a reference is, how a Type is shaped. */
void bind_kinds(nb::module_ &m) {
    bind_enum<NodeKind>(m, "NodeKind", kinds::node_kinds, "What an object is (`rxf:kind`).", nb::is_arithmetic());
    bind_enum<RefKind>(m, "RefKind", kinds::ref_kinds, "What a reference is.", nb::is_arithmetic());
    bind_enum<Binding>(m, "Binding", kinds::bindings, "Whether a reference must be set.", nb::is_arithmetic());
    bind_enum<OwnerKind>(m, "OwnerKind", kinds::owners, "Who owns an object.", nb::is_arithmetic());
    bind_enum<Mobility>(m, "Mobility", kinds::mobilities, "Whether an object may move in memory.", nb::is_arithmetic());
    bind_enum<Form>(m, "Form", kinds::forms, "The form of a Type.", nb::is_arithmetic());
    bind_enum<TypeFlags>(m, "TypeFlags", kinds::type_flags, "The flags of a Type.", nb::is_flag());
    bind_enum<FieldFlags>(m, "FieldFlags", kinds::field_flags, "The flags of a Field.", nb::is_flag());
}

} // namespace pymergetic::rxf::core::bindings
