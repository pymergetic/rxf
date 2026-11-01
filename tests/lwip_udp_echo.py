"""The real UDP echo over the SLIP line: the harness of its differential (docs/35, docs/23 4.4).

A peer stack (this file, with the RFC 1055 model of ``pymergetic.rxftk.net.link.slip``) puts
SLIP-framed IPv4 datagrams on the line; the card's ``slipif_poll`` reads them through the line
face, ``ip4_input`` / ``udp_input`` / ``icmp_input`` answer, and the reply leaves through
``slipif_output_v4`` and the face back onto the line. The same scenarios run twice -- the
imported graphs of ``tests/lwip_corpus/udp_echo.c`` and the same C compiled natively (the test
reference, never a seat fill) -- through the same line face and the same host fill, and the
bytes of every reply, and the counters of the line, must be equal.

``tests/test_lwip_slip.py`` runs it in process on the interpreter the repository ships: the
budget of that interpreter carries the echo path with margin (the path measures 15072
frame bytes at 9 links, ``echo_path_budget``; ``native/graph.h`` declares 32 KiB / 16 links).
Run as a script it prints the result as JSON::

    python -m tests.lwip_udp_echo
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from pymergetic.rxftk.net.link import slip

if TYPE_CHECKING:
    from .test_lwip_import import Host

ROOT = Path(__file__).resolve().parents[1]
PEER = bytes((10, 0, 0, 1))
CARD = bytes((10, 0, 0, 2))
ECHO_PORT = 7
BOOT = "slip_echo_boot"
POLL = "slip_echo_poll"
BODIES = (BOOT, POLL)

# the refusals of a run that does not fit the machine (native/graph.h)
CALL_DEPTH = 17
CELL_BUDGET = 18


# ---- the peer's packets -----------------------------------------------------------------------


def checksum(data: bytes) -> int:
    """The Internet checksum (RFC 1071) of ``data``."""
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack(f">{len(data) // 2}H", data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return ~total & 0xFFFF


def ipv4(
    proto: int, payload: bytes, ident: int = 1, src: bytes = PEER, dst: bytes = CARD
) -> bytes:
    header = struct.pack(
        ">BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), ident, 0, 64, proto, 0, src, dst
    )
    return header[:10] + struct.pack(">H", checksum(header)) + header[12:] + payload


def udp(sport: int, dport: int, data: bytes, good: bool = True) -> bytes:
    body = struct.pack(">HHHH", sport, dport, 8 + len(data), 0) + data
    pseudo = PEER + CARD + struct.pack(">BBH", 0, 17, len(body))
    value = checksum(pseudo + body) or 0xFFFF
    value = value if good else value ^ 0x5555
    return body[:6] + struct.pack(">H", value) + body[8:]


def icmp_echo(ident: int, seq: int, data: bytes) -> bytes:
    body = struct.pack(">BBHHH", 8, 0, 0, ident, seq) + data
    return body[:2] + struct.pack(">H", checksum(body)) + body[4:]


@dataclass(frozen=True)
class Scenario:
    name: str
    packet: bytes
    reply: str  # "udp" (echoed), "icmp-echo", "icmp-unreachable", or "none"
    payload: bytes = b""


def scenarios() -> list[Scenario]:
    stuffed = (
        bytes([0xC0, 0xDB, 0xDC, 0xDD, 0x00, 0xC0, 0xDB]) * 3
    )  # END and ESC as payload
    return [
        Scenario(
            "udp text",
            ipv4(17, udp(5000, ECHO_PORT, b"hello slip echo")),
            "udp",
            b"hello slip echo",
        ),
        Scenario(
            "udp one byte", ipv4(17, udp(5001, ECHO_PORT, b"\x01"), 2), "udp", b"\x01"
        ),
        Scenario(
            "udp stuffed", ipv4(17, udp(5002, ECHO_PORT, stuffed), 3), "udp", stuffed
        ),
        Scenario(
            "udp 900 bytes",
            ipv4(17, udp(5003, ECHO_PORT, bytes(range(256)) * 3 + bytes(132)), 4),
            "udp",
            bytes(range(256)) * 3 + bytes(132),
        ),
        Scenario(
            "icmp echo request",
            ipv4(1, icmp_echo(7, 1, b"ping through the line"), 5),
            "icmp-echo",
            b"ping through the line",
        ),
        Scenario(
            "udp to a closed port",
            ipv4(17, udp(5004, 9, b"nobody home"), 6),
            "icmp-unreachable",
        ),
        Scenario(
            "udp with a bad checksum",
            ipv4(17, udp(5005, ECHO_PORT, b"damaged", good=False), 7),
            "none",
        ),
        Scenario(
            "udp again",
            ipv4(17, udp(5000, ECHO_PORT, b"second datagram"), 8),
            "udp",
            b"second datagram",
        ),
    ]


def check_reply(scenario: Scenario, packets: tuple[bytes, ...]) -> None:
    """The reply is what the scenario says it is, with valid checksums (the peer's own reading)."""
    if scenario.reply == "none":
        assert packets == (), (
            f"{scenario.name}: a damaged datagram is dropped, not answered"
        )
        return
    assert len(packets) == 1, f"{scenario.name}: {len(packets)} replies"
    reply = packets[0]
    assert reply[0] == 0x45 and checksum(reply[:20]) == 0, (
        f"{scenario.name}: IP header checksum"
    )
    assert reply[12:16] == CARD and reply[16:20] == PEER, (
        f"{scenario.name}: addresses swap"
    )
    proto, body = reply[9], reply[20 : struct.unpack(">H", reply[2:4])[0]]
    if scenario.reply == "udp":
        assert proto == 17
        sport, dport, length, _sum = struct.unpack(">HHHH", body[:8])
        assert (sport, length) == (ECHO_PORT, 8 + len(scenario.payload))
        assert dport == struct.unpack(">H", scenario.packet[20:22])[0], (
            "the reply goes to the sender's port"
        )
        assert body[8:] == scenario.payload, (
            f"{scenario.name}: the payload comes back whole"
        )
        pseudo = CARD + PEER + struct.pack(">BBH", 0, 17, len(body))
        assert checksum(pseudo + body) == 0, f"{scenario.name}: UDP checksum"
    elif scenario.reply == "icmp-echo":
        assert proto == 1 and body[0] == 0 and checksum(body) == 0
        assert body[8:] == scenario.payload
    else:
        assert (
            proto == 1 and body[:2] == bytes((3, 3)) and checksum(body) == 0
        )  # port unreachable


# ---- the run -----------------------------------------------------------------------------------


@dataclass
class Outcome:
    """What one side did with the scenarios."""

    status: int = 0  # the first refusal of a poll run (0: none)
    replies: dict[str, bytes] = field(
        default_factory=dict
    )  # line bytes out, by scenario
    counters: tuple[int, int, int, int] = (
        0,
        0,
        0,
        0,
    )  # the line cell's four words at the end


def exchange(face, seat, poll, packet: bytes) -> tuple[int, bytes]:
    """One datagram in over the line, the stack polled, the line's reply bytes out."""
    seat.peer_recv()
    assert seat.peer_send(slip.encode(packet)) == len(slip.encode(packet))
    seat.pump(face)
    status = poll()
    seat.pump(face)
    return status, seat.peer_recv()


def run_both(host: Host) -> tuple[Outcome, Outcome]:
    """The scenarios on the imported graphs and on the native reference."""
    import ctypes

    from . import rxf_native
    from .test_lwip_slip import HostSeat, ImportedFace, NativeFace

    native = host.native
    native.slip_echo_boot.restype = ctypes.c_uint64
    native.slip_echo_poll.restype = ctypes.c_uint64
    seat_imported, seat_native = HostSeat(native), HostSeat(native)
    imported, reference = Outcome(), Outcome()
    try:
        with rxf_native.Memory(1 << 16) as memory:
            face = ImportedFace(host, memory)
            assert host.run(memory, "__wires_boot") == (0, 0)
            assert host.run(memory, BOOT) == (0, 0), "the imported boot"
            assert native.slip_echo_boot() == 0, "the native boot"
            reference_face = NativeFace(native)

            def poll_imported() -> int:
                status, _ = host.run(memory, POLL)
                return status

            def poll_native() -> int:
                native.slip_echo_poll()
                return 0

            for scenario in scenarios():
                status, out = exchange(
                    face, seat_imported, poll_imported, scenario.packet
                )
                if status and not imported.status:
                    imported.status = status
                imported.replies[scenario.name] = out
                _, out = exchange(
                    reference_face, seat_native, poll_native, scenario.packet
                )
                reference.replies[scenario.name] = out
                if imported.status:
                    break
            imported.counters = face.counters
            reference.counters = reference_face.counters
    finally:
        seat_imported.close()
        seat_native.close()
    return imported, reference


def decode(line: bytes) -> tuple[bytes, ...]:
    return slip.SlipDecoder().feed(line)


def build_host(directory: Path) -> Host:
    from .test_lwip_import import CORPUS, Host
    from .test_lwip_slip import LINE

    return Host(
        directory, CORPUS / "udp_echo.c", BODIES, natives=(LINE / "slip_line_host.c",)
    )


# ---- the machine budget this path needs --------------------------------------------------------


def echo_path_budget(host: Host) -> tuple[int, int]:
    """(frame bytes, links) of the longest chain the echo takes at run time: the sum of the
    declared frames from the harness entry down the real call path of the C, and its depth.
    The path: slip_echo_poll -> slipif_poll -> ip4_input -> udp_input -> echo_recv ->
    udp_sendto -> udp_sendto_if_src -> then either the checksum leg (inet_chksum_pseudo) or the
    output leg (ip4_output_if_src -> slipif_output_v4), whichever is deeper."""
    trunk = (
        "slip_echo_poll",
        "slipif_poll",
        "ip4_input",
        "udp_input",
        "echo_recv",
        "udp_sendto",
        "udp_sendto_if_src",
    )
    legs = (("inet_chksum_pseudo",), ("ip4_output_if_src", "slipif_output_v4"))

    def frame(name: str) -> int:
        return struct.unpack_from("<Q", host.programs[f"lwip.{name}"], 8)[0]

    chains = [(*trunk, *leg) for leg in legs]
    return max((sum(map(frame, chain)), len(chain)) for chain in chains)


# ---- the interpreter with room: the experiment is gone -------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="the UDP echo differential, as JSON")
    parser.parse_args(argv)
    sys.path[:0] = [str(ROOT / "tools" / "native"), str(ROOT)]
    with tempfile.TemporaryDirectory() as directory:
        host = build_host(Path(directory))
        imported, reference = run_both(host)
    answer = {
        "status": imported.status,
        "same": {
            name: imported.replies.get(name) == out
            for name, out in reference.replies.items()
        },
        "replies": {name: out.hex() for name, out in reference.replies.items()},
        "counters": [list(imported.counters), list(reference.counters)],
    }
    sys.stdout.write(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
