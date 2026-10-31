cmake_minimum_required(VERSION 3.26)

# pymergetic.rxfb: the CPython face of the rxfb reader (ROADMAP section 2, L1c).
#
# One nanobind extension over the same C core: the `rxfb` library of ../CMakeLists.txt (rxfb.c, the
# freestanding translation unit) is linked as it is, so the reader is written once. The module is
# the __init__ of the package src/pymergetic/rxfb, which holds only its stub (__init__.pyi). This
# directory is added by the root project (it needs nanobind and a Python); the C core itself builds
# without either.
nanobind_add_module(rxfb_python NB_STATIC module.cpp)
target_link_libraries(rxfb_python PRIVATE rxfb)
target_compile_options(rxfb_python PRIVATE -Wall -Wextra -Werror)
set_target_properties(rxfb_python PROPERTIES OUTPUT_NAME "__init__")

install(TARGETS rxfb_python LIBRARY DESTINATION pymergetic/rxfb)
