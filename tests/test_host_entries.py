"""The host entries of the image and the seat each proves (docs/31 §5).

A distribution is source + code + capabilities + entry, and a host is just the
outermost capability set it services. The image carries one ``HostEntry`` per
host shape -- ``unix-program``, ``bare-metal-os``, ``wasm`` -- each naming the
entry the host runs (``pymergetic.rxf.application.main``, the image's entry)
and the capability requirements that are its boundary. Here those entries are
pinned, and the unix-program entry is proven like the firmware seats
(``tests/test_boot_qemu.py``) and the glue seats (``tests/test_boot_smoke.py``):
the built artifact runs as a process and reports the interpreter result byte
for byte.

The run needs the toolchain of ``native/wrap`` (clang, lld). Without it the
test skips loudly and says so; no test pretends a run happened.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from pymergetic.rxftk.binary import builder, package
from pymergetic.rxftk.core import tree

from . import image_world as run
from .snippets import STARTER
from .support import resolved

MAIN = "pymergetic.rxf.application.main"
# the host shapes of docs/31 §5 and the kind each carries (tools/native/host_sync.py)
HOSTS = {
    "bare_metal_os": "BareMetalOs",
    "unix_program": "UnixProgram",
    "wasm": "Wasm",
}
TOOLS = ["clang-18", "ld.lld-18"]  # what native/wrap builds with

needs_toolchain = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS),
    reason="needs clang-18 and ld.lld-18",
)


def test_the_image_carries_one_host_entry_per_host_shape():
    image = tree.load(STARTER)
    found = {
        str(image.get(handle)["name"]): handle
        for handle in range(image.handle_count)
        if image.live(handle) and image.type_name(handle) == "HostEntry"
    }
    assert set(found) == set(HOSTS)
    for name, handle in found.items():
        kind = str(dict(image.fields(handle))["kind"]).rsplit(".", 1)[-1]
        assert kind == HOSTS[name]


def test_every_host_entry_names_the_entry_of_the_image():
    image = tree.load(STARTER)
    for name in HOSTS:
        fields = dict(image.fields(resolved(image, f"pymergetic.rxf.hosts.{name}")))
        assert fields["entry"] == image.entry_path == MAIN


def test_a_host_entry_carries_the_outermost_capability_set():
    """The set is the world's whole demand: every requirement is in every entry."""
    image = tree.load(STARTER)
    wanted = {
        image.path(handle)
        for handle in range(image.handle_count)
        if image.live(handle) and image.type_name(handle) == "CapabilityRequirement"
    }
    assert wanted
    for name in HOSTS:
        handle = resolved(image, f"pymergetic.rxf.hosts.{name}")
        capabilities = {image.path(int(ref[0])) for ref in image.get(handle)["refs"]}
        assert capabilities == wanted
        assert int(dict(image.fields(handle))["capability_count"]) == len(wanted)


@needs_toolchain
def test_the_unix_program_runs_and_reports_the_interpreter_result(tmp_path):
    """the ``unix-program`` host entry, proven as a process on this machine."""
    image = tree.load(STARTER)
    built = builder.build("elf", image, tmp_path)
    linked = package.link_module().link(image, "x86_64", package.BASE)
    done = subprocess.run(
        [built["artifact"]], capture_output=True, timeout=120, check=False
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout == run.report_line(image, linked, MAIN)
