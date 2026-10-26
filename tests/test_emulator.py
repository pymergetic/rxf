"""The emulator harness itself: a call that returns, and a call that never will."""

import pytest

from .emulator import Emulator, RunawayCall

MACHINES = ["x86_64", "aarch64"]

# mov eax, 7; ret            |  mov w0, #7; ret
RETURNED = {
    "x86_64": bytes.fromhex("b807000000c3"),
    "aarch64": (0x528000E0).to_bytes(4, "little") + (0xD65F03C0).to_bytes(4, "little"),
}
# jmp $                      |  b .
RUNAWAY = {
    "x86_64": bytes.fromhex("ebfe"),
    "aarch64": (0x14000000).to_bytes(4, "little"),
}


@pytest.mark.parametrize("machine", MACHINES)
def test_a_call_that_returns_leaves_its_status(machine: str) -> None:
    emulator = Emulator(machine)
    assert emulator.call(emulator.load(RETURNED[machine]), []) == 7


@pytest.mark.parametrize("machine", MACHINES)
def test_a_guest_that_never_returns_fails_the_call_not_the_lane(machine: str) -> None:
    emulator = Emulator(machine, call_timeout=0.2)
    with pytest.raises(RunawayCall, match="did not return"):
        emulator.call(emulator.load(RUNAWAY[machine]), [])
