"""What serves a live image to a peer: what runs on the other end of a link in a test.

`serve_shared` runs in a spawned child, so it is a module-level function; `serve_descriptor` runs in a
thread of the test, the way an emulator holds the master of a pty whose slave the host opens.
"""

import os
import select
import time
from threading import Event
from typing import Any

from pymergetic.rxftk.core import live
from pymergetic.rxftk.live import link


def serve_shared(name: str, stop: Any, ready: Any) -> None:
    """The image side of a shared-memory segment: attach the area and serve it until told to stop."""
    segment = link.attach_segment(name)
    area = live.Area.attach(segment.buf)
    ready.set()
    while not stop.is_set():
        if not area.pump(64):
            time.sleep(0.0005)
    del area
    segment.close()


def serve_descriptor(
    fd: int, image: bytes, stop: Event, ready: Event, arena: int, emit: int
) -> None:
    """The image side of a serial line: what `uart.pump` and the live pump do on a machine."""
    from pymergetic.rxftk.live.harness import HostImage

    host = HostImage(image, arena=arena, emit=emit)
    ready.set()
    while not stop.is_set():
        readable, _, _ = select.select([fd], [], [], 0.01)
        if readable:
            data = os.read(fd, 1 << 16)
            if data:
                host.write(data)
        host.pump()
        out = host.read()
        if out:
            os.write(fd, out)
