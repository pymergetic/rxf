cmake_minimum_required(VERSION 3.26)

# rxfb: the slim reader of an RXFB v7 image, as a library (ROADMAP section 2, L1).
#
# One freestanding C translation unit and two headers: rxfb.c, rxfb.h, rxfb_layout.h. No libc, no
# allocation. This file builds standalone (`cmake -S rxfb -B build`) and from the root project
# (add_subdirectory(rxfb)); the library never includes the engine.
if(NOT PROJECT_NAME)
  project(rxfb LANGUAGES C CXX)
endif()

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# The core: freestanding, so a seat without a libc links it as it is.
add_library(rxfb STATIC rxfb.c)
target_include_directories(rxfb PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_compile_options(rxfb PRIVATE -ffreestanding -fno-builtin -Wall -Wextra -Werror)
set_target_properties(rxfb PROPERTIES POSITION_INDEPENDENT_CODE ON)

# The C++ face: rxfb.hpp, header-only, C++20, over the same C symbols (rxfb_cpp links the C core
# in). It is built the way the engine is built (no exceptions, no RTTI), and so is everything that
# links it. rxfb_cpp_probe is a program that touches the whole face with no standard library: a
# freestanding object that must need nothing but the rxfb_* symbols.
add_library(rxfb_cpp INTERFACE)
target_include_directories(rxfb_cpp INTERFACE ${CMAKE_CURRENT_SOURCE_DIR}/cpp)
target_compile_features(rxfb_cpp INTERFACE cxx_std_20)
target_compile_options(rxfb_cpp INTERFACE -fno-exceptions -fno-rtti)
target_link_libraries(rxfb_cpp INTERFACE rxfb)

add_library(rxfb_cpp_probe OBJECT cpp/test/probe.cpp)
target_link_libraries(rxfb_cpp_probe PRIVATE rxfb_cpp)
target_compile_options(rxfb_cpp_probe PRIVATE -ffreestanding -Wall -Wextra -Werror)

# The hosted example: maps an image and prints path/payload/digest. Its SHA-256 is the engine's own,
# so it is built where the engine's sources are (this repository); the library does not need it.
set(RXFB_ENGINE_SRC ${CMAKE_CURRENT_SOURCE_DIR}/../src)
if(EXISTS ${RXFB_ENGINE_SRC}/pymergetic/rxftk/core/hash/sha256.cpp)
  set(CMAKE_CXX_STANDARD 20)
  set(CMAKE_CXX_STANDARD_REQUIRED ON)
  add_executable(rxfb_dump
    example/rxfb_dump.c
    example/sha256_engine.cpp
    ${RXFB_ENGINE_SRC}/pymergetic/rxftk/core/hash/sha256.cpp)
  target_link_libraries(rxfb_dump PRIVATE rxfb)
  target_include_directories(rxfb_dump PRIVATE ${RXFB_ENGINE_SRC})
  target_compile_options(rxfb_dump PRIVATE -Wall -Wextra -Werror)

  # The same program against the C++ face: its output is the C program's, byte for byte.
  add_executable(rxfb_dump_cpp
    cpp/example/rxfb_dump.cpp
    example/sha256_engine.cpp
    ${RXFB_ENGINE_SRC}/pymergetic/rxftk/core/hash/sha256.cpp)
  target_link_libraries(rxfb_dump_cpp PRIVATE rxfb_cpp)
  target_include_directories(rxfb_dump_cpp PRIVATE ${RXFB_ENGINE_SRC})
  target_compile_options(rxfb_dump_cpp PRIVATE -Wall -Wextra -Werror)
endif()

# The layout pins (test/layout_pins.c): compile-time assertions that rxfb_layout.h is the engine's
# ground.h, struct for struct. There is nothing to run — the compiler is the test — but building it
# as an object both checks the layout at build time and gives an editor a compile-database entry
# with the engine's include root, so an editor without one resolves ground.h and the pins can never
# drift silently. tests/test_rxfb.py re-checks every pinned target; this is the host's own check.
if(EXISTS ${RXFB_ENGINE_SRC}/pymergetic/rxftk/core/ground.h)
  add_library(rxfb_layout_pins OBJECT test/layout_pins.c)
  target_include_directories(rxfb_layout_pins PRIVATE ${RXFB_ENGINE_SRC})
  target_compile_options(rxfb_layout_pins PRIVATE -ffreestanding -fno-builtin -Wall -Wextra -Werror)
endif()
