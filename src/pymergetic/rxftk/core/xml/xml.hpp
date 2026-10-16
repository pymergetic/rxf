#ifndef PYMERGETIC_RXF_XML_XML_HPP
#define PYMERGETIC_RXF_XML_XML_HPP

/*
 * The text form of a delta. Element names are type names, `name` is the name
 * of the object, nesting is the parent, and every other unprefixed attribute
 * is a field of the type. What belongs to the format is in the namespace
 * `rxf` (the prefix is declared on <delta>): the elements rxf:ref, rxf:set,
 * rxf:delete, rxf:entry and the attributes rxf:parent, rxf:kind, rxf:owner,
 * rxf:mobility, rxf:generation, rxf:index, rxf:data. Field names can therefore never
 * collide with the format (only `name` and `path` are taken). A word of the
 * format or of an enumeration field is the path of its Enumerator object, never
 * a bare word or a number (`kind="RefKind.Call"`, `form="Form.Struct"`,
 * `flags="TypeFlags.Variable|Signed"`: a bitset names its enumeration once, on the
 * first value). A path that starts with '@' is an alias of the enclosing Module:
 * `@roles.call.Callee`, `@kinds.CapabilityKind.Read`.
 *
 *   <delta xmlns:rxf="urn:pymergetic:rxf">
 *     <Module name="app" rxf:parent="pymergetic.rxf" category="ModuleCategory.Application">
 *       <Alias name="roles" target="pymergetic.rxf.roles"/>
 *       <Function name="main" signature="pymergetic.rxf.app.main.sig">
 *         <rxf:ref role="@roles.call.Callee" kind="RefKind.Call"
 * target="pymergetic.rxf.lib.total"/>
 *       </Function>
 *     </Module>
 *     <rxf:set path="pymergetic.rxf.app" category="ModuleCategory.Library"/>
 *     <rxf:delete path="pymergetic.old"/>
 *     <rxf:entry path="pymergetic.rxf.app.main"/>
 *   </delta>
 *
 * Parsing and printing are pugixml's (third_party/pugixml); this file maps the
 * tree to operations and back. Not accepted: DTD, CDATA, text between
 * elements. The printer is canonical: the same delta always gives the same
 * bytes.
 */

#include <string>

#include <pymergetic/rxftk/core/delta/delta.hpp>

namespace pymergetic::rxf::core::xml {

using object::Status;

/* `default_parent` is the parent of a top-level element that names none (rxf:parent); without it
 * that is the root. The printer leaves rxf:parent out where it equals `default_parent`, and cannot
 * print an element of another parent. */
Status parse(const std::string &text, delta::Delta *out, std::string *error,
             const std::string *default_parent = nullptr);
Status write(const delta::Delta &delta, std::string *out, std::string *error,
             const std::string *default_parent = nullptr);

} // namespace pymergetic::rxf::core::xml

#endif
