"""The lwIP card, slice 1 (docs/23 4.4, docs/35): the vendored source, the import lane over it,
and the differential of what imports.

Four things are pinned:

* the vendored tree (``third_party/lwip``): the pinned release, its commit, the digest of every
  file, the BSD-3 notice -- a vendored file that changes changes this test;
* the card compiles (``native/lwip/core_slice.c``: clang-18, the importer's own entrance) with
  its options, and is the NO_SYS=1 IPv4 + UDP + ICMP + SLIP core and nothing more;
* exactly what ``tools/native/rximport.py`` imports and exactly what it refuses, body by body
  (``REFUSED``, ``UNLINKED``, ``COMPOSED``), and the generated block of docs/35 equals a fresh
  run -- a change of the importer that moves one body shows up here, in either direction;
* the differential on the host seat: the imported graphs of ``tests/lwip_corpus/payload_echo.c``
  (the pcb setup and one datagram's payload copied through the card's pbuf cells) answer the
  same as the same C compiled natively (the TEST REFERENCE of docs/23 3, never a seat fill).

The UDP echo of the gate (slipif -> ip4_input -> udp_input -> udp_sendto -> slipif) composes body
by body with the SLIP line face imported in the same unit:
``test_the_echo_path_composes_with_the_line_face`` pins it. The second
differential (``tests/lwip_corpus/wire_words.c``) runs the words the byte-swap and min/max
lowerings brought in: htons/htonl, the Internet checksum, pbuf_alloc from the pool and
pbuf_copy_partial across its chain.
"""

from __future__ import annotations

import ctypes
import hashlib
import re
import struct
import subprocess
from pathlib import Path

import compose
import lwip_import
import pytest

from . import rxf_native

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "third_party" / "lwip"
CORPUS = Path(__file__).parent / "lwip_corpus"
DOC = ROOT / "docs" / "35-LWIP-IMPORT-TABLE.md"
CLANG = "clang-18"
OK = 0

# ---- what the importer does with the card, body by body (docs/35) ---------------------------

# tier 1: refused by rximport, and the class of the importer's own reason. The class that
# used to head this table (intrinsic: lwip_itoa and netif_index_to_name on llvm.memmove)
# emptied when the overlap-safe copy lowered as a direction-aware loop (docs/35 3.1); what
# remains is a named permanent wall (docs/23 4.1: the pass does no arithmetic on the
# address of a local's own bytes), each entry with its reason
REFUSED = {
    "ipaddr_addr": "local-address",
    "ip4addr_aton": "local-address",
    "ip4addr_ntoa": "local-address",
    "ip4addr_ntoa_r": "local-address",
}
UNLINKED = {
    "lwip_strnstr": ("strlen", "strncmp"),
    "lwip_strnistr": ("strlen",),
    "netif_name_to_index": ("strtol",),
    "netif_find": ("strtol",),
    "pbuf_strstr": ("strlen",),
}
COMPOSED = frozenset(
    {
        "lwip_htons",
        "lwip_htonl",
        "lwip_strnicmp",
        "lwip_stricmp",
        "lwip_itoa",
        "lwip_standard_chksum",
        "inet_chksum_pseudo",
        "ip_chksum_pseudo",
        "inet_chksum_pseudo_partial",
        "ip_chksum_pseudo_partial",
        "inet_chksum",
        "inet_chksum_pbuf",
        "lwip_init",
        "mem_init",
        "netif_add",
        "netif_add_noaddr",
        "pbuf_dechain",
        "memp_init",
        "netif_init",
        "udp_init",
        "sys_timeouts_init",
        "icmp_input",
        "ip4_addr_isbroadcast_u32",
        "pbuf_free",
        "pbuf_add_header",
        "pbuf_alloc",
        "pbuf_remove_header",
        "pbuf_copy",
        "ip4_output_if",
        "icmp_dest_unreach",
        "ip4_route",
        "ip4_input",
        "pbuf_realloc",
        "udp_input",
        "pbuf_header_force",
        "ip4_output_if_src",
        "ip4_output",
        "ip4_addr_netmask_valid",
        "mem_free",
        "mem_trim",
        "mem_malloc",
        "mem_calloc",
        "memp_init_pool",
        "memp_malloc_pool",
        "memp_malloc",
        "memp_free_pool",
        "memp_free",
        "netif_input",
        "netif_null_output_ip4",
        "netif_set_addr",
        "netif_set_ipaddr",
        "netif_set_netmask",
        "netif_set_gw",
        "netif_remove",
        "netif_set_down",
        "netif_set_default",
        "netif_set_up",
        "netif_set_link_up",
        "netif_set_link_down",
        "netif_index_to_name",
        "netif_get_by_index",
        "pbuf_alloc_reference",
        "pbuf_add_header_force",
        "pbuf_header",
        "pbuf_free_header",
        "pbuf_clen",
        "pbuf_ref",
        "pbuf_cat",
        "pbuf_chain",
        "pbuf_copy_partial_pbuf",
        "pbuf_copy_partial",
        "pbuf_get_contiguous",
        "pbuf_skip",
        "pbuf_take",
        "pbuf_take_at",
        "pbuf_coalesce",
        "pbuf_clone",
        "pbuf_get_at",
        "pbuf_try_get_at",
        "pbuf_put_at",
        "pbuf_memcmp",
        "pbuf_memfind",
        "sys_timeout",
        "sys_now",
        "sys_untimeout",
        "sys_check_timeouts",
        "sys_restart_timeouts",
        "sys_timeouts_sleeptime",
        "udp_send",
        "udp_sendto",
        "udp_sendto_if",
        "udp_sendto_if_src",
        "udp_bind",
        "udp_bind_netif",
        "udp_connect",
        "udp_disconnect",
        "udp_recv",
        "udp_remove",
        "udp_new",
        "udp_new_ip_type",
        "udp_netif_ip_addr_changed",
        "slipif_init",
        "slipif_output_v4",
        "sio_open",
        "slipif_poll",
        "sio_tryread",
        "rxf_slip_line_close",
        "sio_send",
        "rxf_slip_line_ready",
        "rxf_lwip_set_now",
        "__wires_boot",
    }
)

# the module: every body of native/lwip/core_slice.c at clang-18 -O1
MODULE_BODIES = 120

# the echo path (docs/35): every body of it lowers, links and composes, with the SLIP line face
# (native/lwip/slip_line_face.c) imported in the same unit
ECHO_PATH = (
    "slipif_poll",
    "netif_input",
    "ip4_input",
    "udp_input",
    "udp_sendto",
    "ip4_output_if_src",
    "slipif_output_v4",
)
# the bodies of the line face, composed with the card
LINE_FACE = (
    "sio_open",
    "sio_send",
    "sio_tryread",
    "rxf_slip_line_close",
    "rxf_slip_line_ready",
)


def tree_digest() -> str:
    """The digest ``third_party/lwip/VERSION`` records: the sorted ``sha256  path`` lines of every
    file of the tree but VERSION itself, hashed again."""
    lines = [
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(VENDOR).as_posix()}"
        for path in sorted(VENDOR.rglob("*"))
        if path.is_file() and path.name != "VERSION"
    ]
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


# ---- the vendored source of record ----------------------------------------------------------


def test_the_vendored_tree_is_the_pinned_release_unmodified():
    version = (VENDOR / "VERSION").read_text()
    assert "STABLE-2_2_0_RELEASE" in version
    assert "0a0452b2c39bdd91e252aef045c115f88f6ca773" in version
    recorded = re.search(r"Digest: ([0-9a-f]{64})", version)
    assert recorded is not None, "VERSION records the tree digest"
    assert tree_digest() == recorded.group(1), (
        "a file of third_party/lwip changed: the tree is the unmodified upstream release "
        "(the port lives in native/lwip/)"
    )


def test_the_license_and_the_notice_travel_with_the_source():
    copying = (VENDOR / "COPYING").read_text()
    assert "Swedish Institute of Computer Science" in copying
    assert "Redistributions of source code must retain" in copying
    notices = (ROOT / "THIRD-PARTY-NOTICES.md").read_text()
    assert "### lwIP (BSD-3-Clause)" in notices
    assert "0a0452b" in notices
    assert "third_party/lwip/COPYING" in notices


# ---- the card compiles, and is the slice it says it is --------------------------------------


@pytest.fixture(scope="module")
def card_ir() -> str:
    return lwip_import.compile_slice()


def defined(ir: str) -> set[str]:
    return set(re.findall(r"^define [^@\n]*@([\w.]+)\(", ir, flags=re.MULTILINE))


def test_the_card_is_the_no_sys_ipv4_udp_icmp_slip_core(card_ir: str):
    bodies = defined(card_ir)
    # in: the one-thread core and its SLIP netif
    for name in (
        "ip4_input",
        "udp_input",
        "icmp_input",
        "slipif_init",
        "pbuf_free",
        "sys_now",
    ):
        assert name in bodies, name
    # out: no TCP, no sockets/netconn, no ARP/Ethernet, no IPv6, no DHCP/DNS, no tcpip thread
    for name in (
        "tcp_input", "lwip_socket", "netconn_new", "etharp_input", "ethernet_input",
        "ip6_input", "dhcp_start", "dns_gethostbyname", "tcpip_init",
    ):  # fmt: skip
        assert name not in bodies, f"{name} is outside slice 1"
    # the module of the importer is the unit's bodies and the boot of its wires
    assert len(bodies) + 1 == MODULE_BODIES


def test_the_native_reference_compiles_the_same_unit_with_the_same_options(
    tmp_path: Path,
):
    """The reference side of every differential: the card compiled natively, warning-free."""
    shared = tmp_path / "libcard.so"
    done = subprocess.run(
        [
            CLANG, "-O1", "-Wall", "-Wno-unused-function", "-Wno-unused-but-set-variable", "-shared", "-fPIC",
            *(f"-I{path}" for path in lwip_import.INCLUDES),
            str(lwip_import.SLICE), "-o", str(shared),
        ],
        capture_output=True,
        text=True,
        check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert done.stderr == "", done.stderr


# ---- the import lane over the card ----------------------------------------------------------


@pytest.fixture(scope="module")
def lane():
    report, _image, _programs = lwip_import.analyze()
    return report


@pytest.mark.slow
def test_exactly_what_imports_and_what_is_refused(lane):
    assert len(lane.functions) == MODULE_BODIES
    assert {
        name: lwip_import.classify(reason) for name, reason in lane.refused.items()
    } == REFUSED
    assert {name: tuple(callees) for name, callees in lane.unlinked.items()} == UNLINKED
    assert lane.composed == COMPOSED
    assert lane.linked == COMPOSED
    # every body is in exactly one of: refused by the importer, lowered but unlinked, composed
    assert set(lane.functions) == set(REFUSED) | set(UNLINKED) | COMPOSED
    assert not (set(REFUSED) & set(UNLINKED)) and not (set(REFUSED) & COMPOSED)


@pytest.mark.slow
def test_the_refusal_reasons_are_the_importers_own_words(lane):
    reasons = lane.refused
    # the intrinsic class is empty now: bswap, umin/umax/smin/smax/abs, and finally
    # llvm.memmove (the overlap-safe copy lowers as one byte per step of a direction-aware
    # loop -- lwip_itoa and netif_index_to_name compose with it, docs/35 3.1)
    assert not [
        name for name, text in reasons.items() if "bswap" in text or "umin" in text
    ]
    assert not [name for name in reasons if "memmove" in reasons[name]]
    assert {"lwip_itoa", "netif_index_to_name"} <= lane.composed
    # what is refused is the named permanent wall of docs/23 4.1: address arithmetic on a
    # local's own bytes (a frame slot, not flat memory)
    assert "frame slot" in reasons["ip4addr_aton"]
    # the out-parameter cards compose now (docs/23 4.1: the cell route)
    assert "netif_add" not in reasons and "pbuf_dechain" not in reasons
    # the libc and the refused siblings are what the unlinked bodies wait for: the SLIP line
    # is no longer one of them (its face is in the unit)
    assert not [name for name in lane.unresolved if name.startswith("sio_")]
    assert set(lane.unresolved["strlen"]) == {
        "lwip_strnistr",
        "lwip_strnstr",
        "pbuf_strstr",
    }
    assert "lwip.netif_add" not in lane.unresolved


@pytest.mark.slow
def test_the_echo_path_composes_with_the_line_face(lane):
    """Every body of the UDP echo path, and the face of the SLIP line under it, is an imported
    program of the image: slipif_poll reads the line through ``sio_tryread``, slipif_output_v4
    writes it through ``sio_send``, and the bodies in between compose bottom-up."""
    for name in (*ECHO_PATH, *LINE_FACE):
        assert name in lane.composed, (
            f"{name} does not compose: the echo path moved, update docs/35"
        )
        assert name not in lane.refused and name not in lane.unlinked, name
    # what stays out of the UDP echo is named (the libc faces of the unlinked bodies); the
    # out-param netif_add composes now and udp_echo.c calls the real one (docs/23 4.1)
    assert "netif_add" in lane.composed and "netif_add" not in lane.refused


@pytest.mark.slow
def test_docs_35_is_the_fresh_run(lane):
    document = DOC.read_text()
    assert lwip_import.splice(document, lwip_import.render(lane)) == document, (
        "docs/35 is stale: run `python tools/native/lwip_import.py --write "
        "docs/35-LWIP-IMPORT-TABLE.md`"
    )


# ---- the differential: the imported graphs against the natively compiled reference ----------

ECHO = CORPUS / "payload_echo.c"
ECHO_BODIES = ("echo_boot", "echo_payload", "echo_byte", "echo_close")
DATAGRAMS = (
    b"echo",
    b"hello udp echo\x00\xff",
    bytes(range(256)),
    b"\x00",
    bytes(1000),  # the SLIP MTU of docs/25 13: one datagram, one pbuf
)


class Host:
    """The imported echo bodies on the host seat: the card's cells are live bytearrays the run
    writes through, the programs a call card names are bound by their object id. ``natives``
    are native files linked into the reference library beside the source (a seat's fill)."""

    def __init__(
        self,
        tmp_path: Path,
        source: Path = ECHO,
        bodies: tuple[str, ...] = ECHO_BODIES,
        natives: tuple[Path, ...] = (),
    ) -> None:
        report, image, programs = lwip_import.analyze(source, set(bodies))
        assert report.unlinked == {}, report.unlinked
        assert set(bodies) <= report.composed
        self.programs = programs
        composer = compose.Composer(image)
        self.objects: dict[int, bytes | bytearray] = {}
        self.named: dict[
            str, bytearray
        ] = {}  # the cells of the card's globals, by C name
        calls: dict[str, set[str]] = {}
        for handle in range(image.handle_count):
            if not image.live(handle):
                continue
            path = image.path(handle)
            if not path.startswith(f"{lwip_import.MODULE}."):
                continue
            values = composer.fields(handle)
            if compose.word(values.get("kind")) == "Object" and "object_id" in values:
                cell = bytearray(bytes.fromhex(values.get("data", "")))
                self.objects[int(values["object_id"])] = cell
                self.named[path.rsplit(".", 1)[-1].removeprefix("global_")] = cell
            callee = values.get("callee")
            target = image.resolve(callee) if callee else None
            if target is not None and image.path(target).startswith(
                f"{lwip_import.MODULE}."
            ):
                calls.setdefault(path.split(".")[1], set()).add(
                    image.path(target).split(".")[1]
                )
        # the host runner binds a bounded table (tests/host/runner.c): the callees the bodies
        # reach, not every program of the image
        reach: set[str] = set()
        todo = [*bodies, "__wires_boot"]
        while todo:
            name = todo.pop()
            if name not in reach:
                reach.add(name)
                todo.extend(calls.get(name, ()))
        for name in reach - {"__wires_boot", *bodies}:
            handle = image.resolve(f"{lwip_import.MODULE}.{name}")
            assert handle is not None, name
            self.objects[compose.object_id(image, handle)] = programs[
                f"{lwip_import.MODULE}.{name}"
            ]
        self.reached = reach
        shared = tmp_path / "libecho.so"
        subprocess.run(
            [
                CLANG, "-O1", "-shared", "-fPIC",
                *(f"-I{path}" for path in lwip_import.INCLUDES),
                str(source), *map(str, natives), "-o", str(shared),
            ],
            check=True,
        )  # fmt: skip
        self.native = ctypes.CDLL(str(shared))
        # the signatures of a corpus other than the echo are its test's
        if source != ECHO:
            return
        for name in ("echo_boot", "echo_close"):
            getattr(self.native, name).restype = ctypes.c_uint64
        self.native.echo_payload.restype = ctypes.c_uint64
        self.native.echo_payload.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint64,
            ctypes.c_void_p,
        ]
        self.native.echo_byte.restype = ctypes.c_uint64
        self.native.echo_byte.argtypes = [ctypes.c_void_p, ctypes.c_uint64]

    def run(self, memory: rxf_native.Memory, name: str, *words: int) -> tuple[int, int]:
        return rxf_native.run(
            self.programs[f"{lwip_import.MODULE}.{name}"],
            struct.pack(f"<{len(words)}Q", *words),
            memory,
            objects=self.objects,
        )


@pytest.fixture(scope="module")
def host(tmp_path_factory: pytest.TempPathFactory) -> Host:
    return Host(tmp_path_factory.mktemp("lwip_echo"))


@pytest.mark.slow
def test_the_payload_echo_matches_the_native_reference(host: Host):
    """The boot, one datagram's payload through the card's pbuf cells, and the close: the same
    answers on both sides, and the same bytes in the output buffer."""
    native = host.native
    with rxf_native.Memory(1 << 16) as memory:
        status, wired = host.run(
            memory, "__wires_boot"
        )  # the boot of the module's wires
        assert (status, wired) == (OK, 0), f"__wires_boot refused ({status})"

        booted = native.echo_boot()
        assert booted == 0x700, (
            f"the native boot answers err 0 and port 7, not {booted:#x}"
        )
        assert host.run(memory, "echo_boot") == (OK, booted), (
            "echo_boot differs from native"
        )

        for datagram in DATAGRAMS:
            source, sink = 0x200, 0x800
            memory.write(source, datagram)
            memory.write(sink, bytes(len(datagram) + 8))
            held_in = ctypes.create_string_buffer(datagram, len(datagram) + 8)
            held_out = ctypes.create_string_buffer(len(datagram) + 8)
            expected = native.echo_payload(held_in, len(datagram), held_out)
            status, answer = host.run(
                memory,
                "echo_payload",
                memory.at(source),
                len(datagram),
                memory.at(sink),
            )
            assert (status, answer) == (OK, expected), (
                f"echo_payload({len(datagram)} bytes): the imported graph answered "
                f"({status}, {answer:#x}), natively it is ({OK}, {expected:#x})"
            )
            assert expected & 0xFFFF == 0, "the copy is err 0 and the compare is equal"
            wrote = bytes(memory.read(sink, len(datagram)))
            assert wrote == held_out.raw[: len(datagram)] == datagram, (
                f"the echo of {len(datagram)} bytes differs from the datagram"
            )
            assert bytes(memory.read(sink + len(datagram), 8)) == bytes(8), (
                "wrote past the end"
            )
            for index in sorted({0, len(datagram) // 2, len(datagram) - 1}):
                want = native.echo_byte(held_out, index)
                got = host.run(memory, "echo_byte", memory.at(sink), index)
                assert got == (OK, want), f"echo_byte[{index}]: {got}, natively {want}"

        closed = native.echo_close()
        assert closed == 1
        assert host.run(memory, "echo_close") == (OK, closed), (
            "echo_close differs from native"
        )


@pytest.mark.slow
def test_the_differential_runs_the_bodies_it_claims(host: Host):
    """The call cards the echo reaches are imported bodies, not a native fill: the bodies the host
    run binds are programs of the image, and the leaves they call are the interpreter's."""
    assert {"pbuf_copy_partial_pbuf", "mem_free"} <= host.reached
    for name in host.reached:
        assert f"{lwip_import.MODULE}.{name}" in host.programs, name


# ---- the second differential: the words the intrinsic lowerings brought into the import -----

WIRE = CORPUS / "wire_words.c"
WIRE_BODIES = (
    "wire_boot",
    "wire_htons",
    "wire_htonl",
    "wire_chksum",
    "wire_chksum_chain",
    "wire_alloc",
    "wire_chain_partial",
    "wire_init",
    "wire_ram",
    "wire_ram_reuse",
    "wire_dechain",
)
SWAPPED = (
    0, 1, 0xFF, 0x100, 0x1234, 0x7FFF, 0x8000, 0xABCD, 0xFFFF, 0x10000, 0x12345678, 0x80000000,
    0xDEADBEEF, 0xFFFFFFFF,
)  # fmt: skip


def pattern(length: int, seed: int) -> bytes:
    """Deterministic bytes that are not all alike (a checksum of zeros proves nothing)."""
    state = seed
    out = bytearray()
    for _ in range(length):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append((state >> 16) & 0xFF)
    return bytes(out)


@pytest.fixture(scope="module")
def wire(tmp_path_factory: pytest.TempPathFactory) -> Host:
    made = Host(tmp_path_factory.mktemp("lwip_wire"), WIRE, WIRE_BODIES)
    for name in ("wire_htons", "wire_htonl", "wire_alloc"):
        getattr(made.native, name).restype = ctypes.c_uint64
        getattr(made.native, name).argtypes = [ctypes.c_uint64]
    made.native.wire_boot.restype = ctypes.c_uint64
    made.native.wire_init.restype = ctypes.c_uint64
    made.native.wire_ram.restype = ctypes.c_uint64
    made.native.wire_ram.argtypes = [ctypes.c_uint64]
    made.native.wire_dechain.restype = ctypes.c_uint64
    made.native.wire_dechain.argtypes = [ctypes.c_uint64]
    made.native.wire_ram_reuse.restype = ctypes.c_uint64
    made.native.wire_ram_reuse.argtypes = [ctypes.c_uint64, ctypes.c_uint64]
    made.native.wire_chksum.restype = ctypes.c_uint64
    made.native.wire_chksum.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    made.native.wire_chksum_chain.restype = ctypes.c_uint64
    made.native.wire_chksum_chain.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint64,
        ctypes.c_uint64,
    ]
    made.native.wire_chain_partial.restype = ctypes.c_uint64
    made.native.wire_chain_partial.argtypes = [
        ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint64,
    ]  # fmt: skip
    return made


@pytest.mark.slow
def test_the_byte_swap_words_match_the_native_reference(wire: Host):
    """lwip_htons / lwip_htonl (llvm.bswap.i16/i32 of the card) answer what the natively
    compiled card answers, on the edges of both widths and words past them."""
    with rxf_native.Memory(1 << 16) as memory:
        for name in ("wire_htons", "wire_htonl"):
            for value in SWAPPED:
                expected = getattr(wire.native, name)(value)
                assert wire.run(memory, name, value) == (OK, expected), (
                    name,
                    hex(value),
                )


@pytest.mark.slow
def test_the_internet_checksum_matches_the_native_reference(wire: Host):
    """inet_chksum over flat buffers (odd and even lengths, an aligned and an odd start) and
    inet_chksum_pbuf over a two-pbuf chain split at every kind of boundary."""
    with rxf_native.Memory(1 << 16) as memory:
        for length in (0, 1, 2, 3, 4, 7, 20, 21, 64, 255, 256, 999, 1000, 1401):
            for skew in (0, 1):
                data = pattern(length + 2, length + skew)[skew : skew + length]
                place = 0x400 + skew
                memory.write(place, data)
                held = ctypes.create_string_buffer(b"\0" + data + b"\0", length + 2)
                at = ctypes.addressof(held) + 1
                expected = wire.native.wire_chksum(at, length)
                got = wire.run(memory, "wire_chksum", memory.at(place), length)
                assert got == (OK, expected), (length, skew, got, hex(expected))
        for length, split in (
            (2, 1),
            (7, 3),
            (20, 11),
            (64, 33),
            (255, 128),
            (1000, 501),
        ):
            data = pattern(length, split)
            memory.write(0x400, data)
            held = ctypes.create_string_buffer(data, length)
            expected = wire.native.wire_chksum_chain(held, length, split)
            got = wire.run(memory, "wire_chksum_chain", memory.at(0x400), length, split)
            assert got == (OK, expected), (length, split, got, hex(expected))


@pytest.mark.slow
def test_the_pool_pbufs_match_the_native_reference(wire: Host):
    """pbuf_alloc from the pool (the length of each pbuf is the smaller of what is left and the
    room of a cell: llvm.umin.i16) and pbuf_copy_partial across the chain it makes."""
    with rxf_native.Memory(1 << 18) as memory:
        status, wired = wire.run(memory, "__wires_boot")
        assert (status, wired) == (OK, 0)
        assert wire.native.wire_boot() == 0
        assert wire.run(memory, "wire_boot") == (OK, 0)
        for length in (0, 1, 100, 999, 1000, 1024, 1500, 2048, 3000, 7000, 9000):
            expected = wire.native.wire_alloc(length)
            assert wire.run(memory, "wire_alloc", length) == (OK, expected), length
        taken = 0
        for length, want, offset in (
            (1, 1, 0), (100, 100, 0), (100, 30, 70), (100, 200, 50), (100, 5, 100),
            (1500, 1500, 0), (1500, 300, 900), (3000, 2000, 500), (3000, 3000, 0),
            (3000, 100, 2990),
        ):  # fmt: skip
            data = pattern(length, length + offset)
            memory.write(0x400, data)
            memory.write(0x4000, bytes(want + 8))
            held = ctypes.create_string_buffer(data, length)
            out = ctypes.create_string_buffer(want + 8)
            expected = wire.native.wire_chain_partial(held, length, out, want, offset)
            got = wire.run(
                memory, "wire_chain_partial",
                memory.at(0x400), length, memory.at(0x4000), want, offset,
            )  # fmt: skip
            assert got == (OK, expected), (length, want, offset, got, hex(expected))
            assert bytes(memory.read(0x4000, want + 8)) == out.raw[: want + 8], (
                length,
                want,
                offset,
            )
            taken += expected & 0xFFFF
        assert taken > 0


@pytest.mark.slow
def test_the_heap_boot_and_ram_pbufs_match_the_native_reference(wire: Host):
    """lwip_init (mem_init over the aligned ram_heap: the constant expression the importer
    carries now) boots the whole stack, and the PBUF_RAM pbufs live in the heap it made: the
    lengths pbuf_alloc answers, the failure when the heap cannot hold a block, and the free
    list (a freed block comes back, a live neighbour survives) equal the natively compiled
    card's. The heap is placed at an address of each side's own, so only address-free answers
    compare."""
    with rxf_native.Memory(1 << 18) as memory:
        status, wired = wire.run(memory, "__wires_boot")
        assert (status, wired) == (OK, 0)
        assert wire.native.wire_init() == 0
        assert wire.run(memory, "wire_init") == (OK, 0), "lwip_init refused or differs"
        for length in (0, 1, 100, 1000, 1500, 3000, 6000, 8000, 9000, 60000):
            expected = wire.native.wire_ram(length)
            assert wire.run(memory, "wire_ram", length) == (OK, expected), length
        for first, second in (
            (10, 20),
            (100, 100),
            (500, 1000),
            (1000, 500),
            (2000, 3000),
        ):
            expected = wire.native.wire_ram_reuse(first, second)
            assert wire.run(memory, "wire_ram_reuse", first, second) == (
                OK,
                expected,
            ), (
                first,
                second,
            )
            assert expected & 0xF == 0xF, (
                f"the native heap reuses the freed block ({expected:#x})"
            )


@pytest.mark.slow
def test_pbuf_dechain_the_out_parameter_card_matches_the_native_reference(wire: Host):
    """pbuf_dechain writes through the pbuf its caller gave and calls programs of its own (the
    out-parameter card of docs/23 4.1, refused by the importer until the cell route): a pool
    chain of one, two and three pbufs is cut after its head, and the head's remaining length,
    the tail's total and chain length equal the natively compiled card's."""
    with rxf_native.Memory(1 << 18) as memory:
        status, wired = wire.run(memory, "__wires_boot")
        assert (status, wired) == (OK, 0)
        assert wire.native.wire_boot() == 0
        assert wire.run(memory, "wire_boot") == (OK, 0)
        for length in (1, 100, 1000, 1500, 2500, 3500, 9000):
            expected = wire.native.wire_dechain(length)
            assert wire.run(memory, "wire_dechain", length) == (OK, expected), length
