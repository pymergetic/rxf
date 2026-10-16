cmake_minimum_required(VERSION 3.26)
project(pymergetic_rxf LANGUAGES C CXX)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# One tree: C and C++ sit next to the Python modules under src/pymergetic/rxftk/core/
# and are included as <pymergetic/rxftk/core/...>; the C++ namespace stays
# pymergetic::rxf::core (the format's core, not the host toolchain's name).
set(RXF_SRC ${CMAKE_CURRENT_SOURCE_DIR}/src)
set(RXF_PKG ${RXF_SRC}/pymergetic/rxftk)
set(RXF_CORE ${RXF_PKG}/core)

# Ground types: header only, shared by the C++ layers, the nanobind module, and native/.
add_library(rxf_ground INTERFACE)
target_include_directories(rxf_ground INTERFACE ${RXF_SRC})

find_package(Python 3.12 REQUIRED COMPONENTS Interpreter Development.Module)
execute_process(
  COMMAND "${Python_EXECUTABLE}" -m nanobind --cmake_dir
  OUTPUT_VARIABLE nanobind_DIR
  OUTPUT_STRIP_TRAILING_WHITESPACE
  COMMAND_ERROR_IS_FATAL ANY)
find_package(nanobind CONFIG REQUIRED)

# pymergetic::rxf::core::kinds: the names of the enumerations of the format.
add_library(rxf_kinds STATIC ${RXF_CORE}/kinds/kinds.cpp)
target_link_libraries(rxf_kinds PUBLIC rxf_ground)
target_compile_options(rxf_kinds PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_kinds PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::arena is freestanding: no exceptions, no RTTI, nothing from the standard library.
add_library(rxf_arena STATIC ${RXF_CORE}/arena/arena.cpp)
target_link_libraries(rxf_arena PUBLIC rxf_ground)
target_compile_options(rxf_arena PRIVATE -fno-exceptions -fno-rtti -Wall -Wextra -Werror)
set_target_properties(rxf_arena PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::object is the host layer: derived lookups, name rules, compaction.
add_library(rxf_object STATIC ${RXF_CORE}/object/image.cpp)
target_link_libraries(rxf_object PUBLIC rxf_arena rxf_kinds)
target_compile_options(rxf_object PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_object PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::hash: SHA-256 for digests.
add_library(rxf_hash STATIC ${RXF_CORE}/hash/sha256.cpp)
target_link_libraries(rxf_hash PUBLIC rxf_ground)
target_compile_options(rxf_hash PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_hash PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::doc: the text of documentation and the LZ4 block format behind it.
add_library(rxf_doc STATIC ${RXF_CORE}/doc/codec.cpp)
target_link_libraries(rxf_doc PUBLIC rxf_ground)
target_compile_options(rxf_doc PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_doc PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::meta: self-description (Type and Field objects, payload codec, link check).
# core/doc/provenance.cpp compiles with it: the provenance facts of docs/22 create typed Doc
# objects and are checked against the ground the self-description knows.
add_library(rxf_meta STATIC ${RXF_CORE}/meta/types.cpp ${RXF_CORE}/doc/provenance.cpp)
target_link_libraries(rxf_meta PUBLIC rxf_object rxf_doc)
target_compile_options(rxf_meta PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_meta PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::delta: operations by path, atomic replay, canonical export.
add_library(rxf_delta STATIC ${RXF_CORE}/delta/delta.cpp)
target_link_libraries(rxf_delta PUBLIC rxf_meta)
target_compile_options(rxf_delta PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_delta PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pugixml 1.14 (MIT), vendored unmodified: the XML parser and printer.
add_library(pugixml STATIC third_party/pugixml/pugixml.cpp)
target_include_directories(pugixml SYSTEM PUBLIC third_party/pugixml)
target_compile_definitions(pugixml PUBLIC PUGIXML_NO_XPATH PUGIXML_NO_EXCEPTIONS)
set_target_properties(pugixml PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::xml: the text form of a delta.
add_library(rxf_xml STATIC ${RXF_CORE}/xml/xml.cpp)
target_link_libraries(rxf_xml PUBLIC rxf_delta pugixml)
target_compile_options(rxf_xml PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_xml PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic::rxf::core::tree: an image as one file per module, parsed and printed in parallel.
find_package(Threads REQUIRED)
add_library(rxf_tree STATIC ${RXF_CORE}/tree/tree.cpp)
target_link_libraries(rxf_tree PUBLIC rxf_xml Threads::Threads)
target_compile_options(rxf_tree PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxf_tree PROPERTIES POSITION_INDEPENDENT_CODE ON)

# The live layer: freestanding like the arena (and compiled to the native leaf rxf_live_pump).
add_library(rxf_live STATIC ${RXF_CORE}/live/live.cpp)
target_link_libraries(rxf_live PUBLIC rxf_arena)
target_compile_options(rxf_live PRIVATE -fno-exceptions -fno-rtti -Wall -Wextra -Werror)
set_target_properties(rxf_live PROPERTIES POSITION_INDEPENDENT_CODE ON)

# pymergetic.rxftk.core: the compiled package body. The extension is the __init__ of the
# package; each C++ module brings its Python face (bindings.cpp) as a submodule.
nanobind_add_module(
  core NB_STATIC
  ${RXF_CORE}/module.cpp
  ${RXF_CORE}/kinds/bindings.cpp
  ${RXF_CORE}/arena/bindings.cpp
  ${RXF_CORE}/hash/bindings.cpp
  ${RXF_CORE}/object/bindings.cpp
  ${RXF_CORE}/meta/bindings.cpp
  ${RXF_CORE}/delta/bindings.cpp
  ${RXF_CORE}/tree/bindings.cpp
  ${RXF_CORE}/mount/types.cpp
  ${RXF_CORE}/mount/modules.cpp
  ${RXF_CORE}/mount/bindings.cpp
  ${RXF_CORE}/live/bindings.cpp)
target_link_libraries(core PRIVATE rxf_tree rxf_hash rxf_live)
set_target_properties(core PROPERTIES OUTPUT_NAME "__init__")

install(TARGETS core LIBRARY DESTINATION pymergetic/rxftk/core)
