"""The emulator harness itself: a call that returns, and a call that never will."""

import pytest

from .emulator import Emulator, GuestFault, Integer, RunawayCall

# x86_64 runs natively on an x86_64 Linux host and in unicorn when asked; both must agree
BACKENDS = [("x86_64", None), ("x86_64", False), ("aarch64", None)]

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


@pytest.mark.parametrize(("machine", "native"), BACKENDS)
def test_a_call_that_returns_leaves_its_status(machine: str, native: bool | None) -> None:
    emulator = Emulator(machine, native=native)
    assert emulator.call(emulator.load(RETURNED[machine]), []) == 7


@pytest.mark.parametrize(("machine", "native"), BACKENDS)
def test_a_guest_that_never_returns_fails_the_call_not_the_lane(
    machine: str, native: bool | None
) -> None:
    emulator = Emulator(machine, call_timeout=0.2, native=native)
    with pytest.raises(RunawayCall, match="did not return"):
        emulator.call(emulator.load(RUNAWAY[machine]), [])


# mov rax, [0]; ret: a read of an address nothing maps
FAULTS = bytes.fromhex("488b042500000000c3")


def test_a_native_guest_that_faults_kills_its_worker_not_the_lane() -> None:
    emulator = Emulator("x86_64", native=True)
    if not emulator.native:
        pytest.skip("this host does not run x86_64 natively")
    with pytest.raises(GuestFault, match="SIGSEGV"):
        emulator.call(emulator.load(FAULTS), [])


def test_the_native_guest_reads_back_what_the_host_wrote() -> None:
    """The memory is shared: a write of the host is what the guest loads, and a store of the
    guest is what the host reads (mov eax, [rdi]; add dword [rdi+4], 1; ret)."""
    emulator = Emulator("x86_64", native=True)
    if not emulator.native:
        pytest.skip("this host does not run x86_64 natively")
    emulator.map(0x2000000, 0x1000)
    emulator.write(0x2000000, (41).to_bytes(4, "little") + (9).to_bytes(4, "little"))
    code = emulator.load(bytes.fromhex("8b07" "83470401" "c3"))
    assert emulator.call(code, [Integer(0x2000000)]) == 41
    assert int.from_bytes(emulator.read(0x2000004, 4), "little") == 10
