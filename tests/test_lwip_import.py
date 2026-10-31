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

The UDP echo of the gate (slipif -> ip4_input -> udp_input -> udp_sendto -> slipif) is NOT
imported yet: ``test_the_echo_path_is_refused_where_docs_35_says`` pins where it stops.
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

# tier 1: refused by rximport, and the class of the importer's own reason
REFUSED = {
    "lwip_htons": "intrinsic",
    "lwip_htonl": "intrinsic",
    "lwip_itoa": "intrinsic",
    "lwip_standard_chksum": "intrinsic",
    "inet_chksum_pseudo": "intrinsic",
    "ip_chksum_pseudo": "intrinsic",
    "inet_chksum_pseudo_partial": "intrinsic",
    "ip_chksum_pseudo_partial": "intrinsic",
    "inet_chksum": "intrinsic",
    "inet_chksum_pbuf": "intrinsic",
    "lwip_init": "constant-expression",
    "mem_init": "constant-expression",
    "icmp_input": "intrinsic",
    "pbuf_alloc": "intrinsic",
    "ip4_output_if": "intrinsic",
    "icmp_dest_unreach": "intrinsic",
    "ip4_input": "intrinsic",
    "udp_input": "intrinsic",
    "ip4_output_if_src": "intrinsic",
    "ip4_output": "intrinsic",
    "ip4_addr_netmask_valid": "intrinsic",
    "ipaddr_addr": "local-address",
    "ip4addr_aton": "local-address",
    "ip4addr_ntoa": "local-address",
    "ip4addr_ntoa_r": "local-address",
    "netif_input": "intrinsic",
    "netif_add": "out-pointer",
    "netif_index_to_name": "intrinsic",
    "pbuf_dechain": "out-pointer",
    "pbuf_copy_partial": "intrinsic",
    "pbuf_get_contiguous": "intrinsic",
    "pbuf_clone": "intrinsic",
    "sys_timeouts_sleeptime": "intrinsic",
    "udp_send": "intrinsic",
    "udp_sendto": "intrinsic",
    "udp_sendto_if": "intrinsic",
    "udp_sendto_if_src": "intrinsic",
    "slipif_poll": "intrinsic",
}
UNLINKED = {
    "lwip_strnstr": ("strlen", "strncmp"),
    "lwip_strnistr": ("strlen",),
    "netif_add_noaddr": ("lwip.netif_add",),
    "netif_name_to_index": ("strtol",),
    "netif_find": ("strtol",),
    "pbuf_strstr": ("strlen",),
    "sys_check_timeouts": ("lwip.slipif_output_v4",),
    "slipif_init": ("sio_open",),
    "slipif_output_v4": ("sio_send",),
}
COMPOSED = frozenset(
    {
        "lwip_strnicmp",
        "lwip_stricmp",
        "memp_init",
        "netif_init",
        "udp_init",
        "sys_timeouts_init",
        "ip4_addr_isbroadcast_u32",
        "pbuf_free",
        "pbuf_add_header",
        "pbuf_remove_header",
        "pbuf_copy",
        "ip4_route",
        "pbuf_realloc",
        "pbuf_header_force",
        "mem_free",
        "mem_trim",
        "mem_malloc",
        "mem_calloc",
        "memp_init_pool",
        "memp_malloc_pool",
        "memp_malloc",
        "memp_free_pool",
        "memp_free",
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
        "pbuf_skip",
        "pbuf_take",
        "pbuf_take_at",
        "pbuf_coalesce",
        "pbuf_get_at",
        "pbuf_try_get_at",
        "pbuf_put_at",
        "pbuf_memcmp",
        "pbuf_memfind",
        "sys_timeout",
        "sys_now",
        "sys_untimeout",
        "sys_restart_timeouts",
        "udp_bind",
        "udp_bind_netif",
        "udp_connect",
        "udp_disconnect",
        "udp_recv",
        "udp_remove",
        "udp_new",
        "udp_new_ip_type",
        "udp_netif_ip_addr_changed",
        "rxf_lwip_set_now",
        "__wires_boot",
    }
)

# the module: every body of native/lwip/core_slice.c at clang-18 -O1
MODULE_BODIES = 115

# the echo path (docs/35): the first refusal of each body is the byte swap of the header fields
ECHO_PATH_REFUSED = {
    "slipif_poll": "llvm.umin.i16",
    "netif_input": "llvm.bswap.i16",
    "ip4_input": "llvm.bswap.i16",
    "udp_input": "llvm.bswap.i16",
    "udp_sendto": "llvm.bswap.i16",
    "ip4_output_if_src": "llvm.bswap.i16",
}


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
            str(lwip_import.SLICE), str(CORPUS / "native_line.c"), "-o", str(shared),
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
    for name in ("lwip_htons", "ip4_input", "udp_input", "inet_chksum"):
        assert "the intrinsic llvm.bswap.i" in reasons[name], name
    assert "llvm.umin.i16" in reasons["pbuf_alloc"]
    assert "llvm.abs.i32" in reasons["lwip_itoa"]
    assert "llvm.smax.i32" in reasons["sys_timeouts_sleeptime"]
    assert "frame slot" in reasons["ip4addr_aton"]
    assert "a pointer to a value" in reasons["netif_add"]
    assert "an instruction outside the subset" in reasons["mem_init"]
    # the libc, the SLIP line and the refused siblings are what the unlinked bodies wait for
    assert lane.unresolved["sio_send"] == ["slipif_output_v4"]
    assert lane.unresolved["sio_open"] == ["slipif_init"]
    assert set(lane.unresolved["strlen"]) == {
        "lwip_strnistr",
        "lwip_strnstr",
        "pbuf_strstr",
    }
    assert lane.unresolved["lwip.netif_add"] == ["netif_add_noaddr"]


@pytest.mark.slow
def test_the_echo_path_is_refused_where_docs_35_says(lane):
    for name, intrinsic in ECHO_PATH_REFUSED.items():
        assert name in lane.refused, (
            f"{name} imports now: the echo path moved, update docs/35"
        )
        assert f"the intrinsic {intrinsic}" in lane.refused[name], name
    # the send side's last body lowers, and waits for the line of its seat
    assert "slipif_output_v4" in lane.unlinked
    assert lane.unlinked["slipif_output_v4"] == ["sio_send"]


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
    writes through, the programs a call card names are bound by their object id."""

    def __init__(self, tmp_path: Path) -> None:
        report, image, programs = lwip_import.analyze(ECHO, set(ECHO_BODIES))
        assert report.unlinked == {}, report.unlinked
        assert set(ECHO_BODIES) <= report.composed
        self.programs = programs
        composer = compose.Composer(image)
        self.objects: dict[int, bytes | bytearray] = {}
        calls: dict[str, set[str]] = {}
        for handle in range(image.handle_count):
            if not image.live(handle):
                continue
            path = image.path(handle)
            if not path.startswith(f"{lwip_import.MODULE}."):
                continue
            values = composer.fields(handle)
            if compose.word(values.get("kind")) == "Object" and "object_id" in values:
                self.objects[int(values["object_id"])] = bytearray(
                    bytes.fromhex(values.get("data", ""))
                )
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
        todo = [*ECHO_BODIES, "__wires_boot"]
        while todo:
            name = todo.pop()
            if name not in reach:
                reach.add(name)
                todo.extend(calls.get(name, ()))
        for name in reach - {"__wires_boot", *ECHO_BODIES}:
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
                str(ECHO), str(CORPUS / "native_line.c"), "-o", str(shared),
            ],
            check=True,
        )  # fmt: skip
        self.native = ctypes.CDLL(str(shared))
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
