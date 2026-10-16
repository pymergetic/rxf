"""The RXFW package of the starter: the header a wrapper reads and what it refuses.

The package is one 192-byte header (the ``rxf_wrap_package`` of native/wrap/wrap.h) and
the linked image behind it, nothing relocated. These tests pin the bytes of the header
where a wrapper finds them, the round trip of building and parsing, and every check of
the parser -- the mistakes a wrapper must refuse before it loads anything.
"""

import struct

import pytest

from pymergetic.rxf.binary import package
from pymergetic.rxf.core import tree

from .snippets import STARTER

MAIN = "pymergetic.rxf.application.main"


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def built(image) -> bytes:
    return package.build(image)


@pytest.fixture(scope="module")
def parsed(built):
    return package.parse(built)


def where(built: bytes, at: int, word: bytes) -> bytes:
    """The package with one span of its header replaced."""
    return built[:at] + word + built[at + len(word) :]


def test_the_package_is_the_header_of_the_wrapper_and_the_image_behind_it(built):
    assert len(built) > package.HEADER_BYTES
    assert built[: package.HEADER_BYTES] != bytes(package.HEADER_BYTES)
    assert built[:4] == b"RXFW" == package.MAGIC
    assert struct.unpack_from("<I", built, 4)[0] == 1 == package.VERSION
    assert package.HEADER_BYTES == 192


def test_the_words_sit_where_the_c_struct_has_them(built, image):
    # what the static asserts of native/wrap/wrap.h pin: the entry at 72, the
    # reserved words at 96
    handle = struct.unpack_from("<3Q", built, 72)
    linked = package.link_module().link(image, "x86_64", package.BASE)
    assert handle == struct.unpack("<3Q", linked.handle(f"{MAIN}.bytecode"))
    assert built[96 : package.HEADER_BYTES] == bytes(96)


def test_the_header_names_the_block_the_image_is_loaded_into(built, parsed):
    assert parsed.base == 0x04000000 == package.BASE
    assert parsed.image == package.HEADER_BYTES
    assert parsed.total == len(built)
    assert parsed.image_bytes >= package.CONTEXT_BYTES
    assert parsed.image + parsed.image_bytes == parsed.total


def test_every_address_of_the_header_is_inside_the_image(parsed):
    limit = parsed.base + parsed.image_bytes
    for address in (parsed.run, parsed.args, parsed.out, parsed.frame):
        assert parsed.base <= address <= limit - 8


def test_the_image_starts_with_the_runtime_context(built):
    words = struct.unpack_from("<2Q", built, package.HEADER_BYTES)
    assert words == (package.CONTEXT_VERSION, package.CONTEXT_BYTES)


def test_a_package_shorter_than_its_header_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(built[: package.HEADER_BYTES - 1])


def test_a_magic_that_is_not_rxw_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 0, b"RXFQ"))


def test_a_version_that_is_not_one_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 4, struct.pack("<I", 2)))


def test_reserved_words_that_are_not_zero_are_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 96, struct.pack("<Q", 1)))


def test_a_total_that_is_not_the_size_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(built + bytes(1))


def test_a_base_that_is_not_a_page_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 16, struct.pack("<Q", package.BASE + 1)))


def test_an_image_that_leaves_the_package_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 32, struct.pack("<Q", 1 << 40)))


def test_a_run_address_outside_the_image_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, 40, struct.pack("<Q", 0)))


def test_an_image_that_is_not_the_runtime_context_is_refused(built):
    with pytest.raises(package.PackageError):
        package.parse(where(built, package.HEADER_BYTES, struct.pack("<2Q", 0, 0)))


def test_building_a_package_needs_a_page_base(image):
    with pytest.raises(package.PackageError):
        package.build(image, base=package.BASE + 1)
