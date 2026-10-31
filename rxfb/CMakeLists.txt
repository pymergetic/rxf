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
endif()
