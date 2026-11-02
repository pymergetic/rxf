"""Machine code of the starter run in an emulator (unicorn), under its C calling convention.

x86_64 follows System V: integers in rdi, rsi, rdx, rcx, r8, r9 and then on the
stack, floating point in xmm0..7, a 24-byte structure by value on the stack.
aarch64 follows AAPCS64: integers in x0..x7, floating point in s0..s7/d0..d7,
a structure larger than 16 bytes as a copy whose address takes an integer slot.
"""

import ctypes
import mmap
import os
import platform
import select
import signal
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

unicorn = pytest.importorskip("unicorn")
arm64 = pytest.importorskip("unicorn.arm64_const")
x86 = pytest.importorskip("unicorn.x86_const")

PAGE = 0x1000
CODE = 0x800000
STACK = 0x3800000  # the code area keeps every leaf of an image: the seed's one-pass bodies
# (native/compile.rxfs's compiler) are much larger than clang's, so the gap is generous
STACK_SIZE = 16 * PAGE
SCRATCH = 0x3900000  # aarch64: copies of structures passed by value
RETURN = 0x3A00000
CPACR_FP_ENABLED = 3 << 20
X86_INTEGERS = (
    x86.UC_X86_REG_RDI,
    x86.UC_X86_REG_RSI,
    x86.UC_X86_REG_RDX,
    x86.UC_X86_REG_RCX,
    x86.UC_X86_REG_R8,
    x86.UC_X86_REG_R9,
)
ARM_INTEGERS = 8


@dataclass(frozen=True)
class Integer:
    bits: int  # as the register holds it, already extended


@dataclass(frozen=True)
class Real:
    width: int  # 4 or 8
    bits: int


@dataclass(frozen=True)
class Blob:
    data: bytes  # a structure passed by value


Argument = Integer | Real | Blob

# One call must come back: a guest that never returns to the sentinel fails its
# test with the program counter it was left at, instead of eating a test lane
# until somebody kills the run. The budget is unicorn's own timeout on ONE emu_start. The call
# is never run in timed slices that are restarted at the program counter: on aarch64 an
# asynchronous stop (the timer thread's uc_emu_stop) can land on a load and leave the registers
# the instructions before it wrote behind the program counter (for example x0 of the address of
# `ldr x0, [x0]`), and the restart then faults with UC_ERR_READ_UNMAPPED on an address the guest
# never computed. x86_64 does not show it. A call that overruns the budget fails anyway, so
# the state an asynchronous stop leaves does not matter.
#
# The budget sits above the heaviest job that completes: a dump of the running
# image measures ~370s here (unicorn, one pump call), and a full-image apply is
# two dumps (the base check and the answer) plus the replay and the relink,
# ~1275s measured. A budget below that healthy cost fails healthy tests as
# runaways; a guest that really loops still fails its test well inside a lane.
CALL_TIMEOUT = 2400.0


class RunawayCall(RuntimeError):
    """The guest did not return to the sentinel inside the call budget."""


# ---- the native x86_64 backend ------------------------------------------------------------------
#
# The x86_64 machine code of the starter is machine code of the host this suite runs on, so it need
# not be emulated: unicorn runs a guest on one thread at a fraction of native speed (the tiny
# compiler compiling itself, three nested interpretations deep, takes 34 minutes of it on the
# x86_64 seat and 30 seconds on wasm32, where node compiles the module).
#
# The guest memory is a set of memfds. A forked worker process maps them at the guest's own
# addresses (MAP_FIXED_NOREPLACE) and runs a call through ctypes; the test process maps the same
# memfds anywhere, so a read or a write is a plain memory access and two emulators of one process
# never fight over the guest's fixed addresses. A guest that crashes or never returns kills its
# worker, not the test process. Anything that needs the emulator itself (unicorn hooks on an
# instruction or on a device port) asks for it with ``RXF_EMULATOR=unicorn``.

EMULATOR_ENV = "RXF_EMULATOR"
WORKER_SOURCE = Path(__file__).parent / "host" / "native_x86_worker.c"

_LIBC = ctypes.CDLL(None, use_errno=True)


class GuestFault(RuntimeError):
    """The native guest died on a signal (a bad address, an illegal instruction, a division)."""


_SYS_MEMFD_CREATE_X86_64 = 319


def _memfd(name: str) -> int:
    """An anonymous file for guest memory. Not every Python build has os.memfd_create (the
    standalone builds uv installs do not), the system call is the same."""
    if hasattr(os, "memfd_create"):
        return os.memfd_create(name)
    fd = int(_LIBC.syscall(_SYS_MEMFD_CREATE_X86_64, name.encode(), 0))
    if fd < 0:
        raise OSError(ctypes.get_errno(), "memfd_create")
    return fd


def native_x86_available() -> bool:
    """Whether the x86_64 seat may run natively here: an x86_64 Linux host that was not asked
    for the emulator."""
    return (
        sys.platform == "linux"
        and platform.machine() in ("x86_64", "AMD64")
        and os.environ.get(EMULATOR_ENV, "auto") != "unicorn"
        and _worker_binary() is not None
    )


def _worker_binary() -> Path | None:
    """The native worker (tests/host/native_x86_worker.c), built once per source and compiler; None
    where it cannot be built, and then the x86_64 seat is emulated."""
    try:
        from . import host_build

        return host_build.binary(
            [WORKER_SOURCE], "native_x86_worker", extra=("-fPIE", "-pie")
        )
    except (OSError, subprocess.CalledProcessError, ImportError):
        return None


class _NativeX86:
    """x86_64 machine code run by the host in a worker process, over shared guest memory."""

    OP_MAP = 1
    OP_CALL = 2
    KIND_INTEGER, KIND_REAL, KIND_STRUCTURE = 0, 1, 2

    def __init__(self, call_timeout: float) -> None:
        worker = _worker_binary()
        assert worker is not None  # native_x86_available() said so
        self.call_timeout = call_timeout
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        self.proc = subprocess.Popen(
            [str(worker), str(child.fileno())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            pass_fds=[child.fileno()],
        )
        child.close()
        self.sock = parent
        self.regions: list[tuple[int, int, mmap.mmap]] = []  # (guest base, size, our view)

    def close(self) -> None:
        try:
            self.sock.close()
            self.proc.kill()
            self.proc.wait()
        except OSError:
            pass

    def __del__(self) -> None:
        self.close()

    def _ask(self, message: bytes, fds: list[int] | None = None) -> tuple[int, int]:
        """One request and its reply (status, value); the worker is killed when the guest does not
        answer in time, and a worker that died is a fault of the guest."""
        socket.send_fds(self.sock, [message], fds or [])
        ready, _, _ = select.select([self.sock], [], [], self.call_timeout)
        if not ready:
            self.proc.kill()
            self.proc.wait()
            raise RunawayCall(
                f"the x86_64 guest did not return within {self.call_timeout:.0f}s (native)"
            )
        data = self.sock.recv(64)
        if not data:
            code = self.proc.wait()
            how = f"signal {signal.Signals(-code).name}" if code < 0 else f"exit {code}"
            raise GuestFault(f"the x86_64 guest worker died: {how}")
        status, value = struct.unpack("<iI", data)
        return status, value

    def map(self, base: int, size: int) -> None:
        for known, length, _view in self.regions:
            if base < known + length and known < base + size:
                raise ValueError(f"0x{base:x}+0x{size:x} overlaps a mapped guest region")
        fd = _memfd("rxf-guest")
        try:
            os.ftruncate(fd, size)
            view = mmap.mmap(fd, size)
            status, _ = self._ask(struct.pack("<IIQQ", self.OP_MAP, 0, base, size), [fd])
        finally:
            os.close(fd)
        if status != 0:
            view.close()
            raise OSError(status, f"cannot map the guest at 0x{base:x}: {os.strerror(status)}")
        self.regions.append((base, size, view))

    def _pieces(self, address: int, size: int):
        """(view, offset, length) pieces that cover [address, address+size), region by region."""
        end = address + size
        while address < end:
            for base, length, view in self.regions:
                if base <= address < base + length:
                    take = min(end, base + length) - address
                    yield view, address - base, take
                    address += take
                    break
            else:
                raise ValueError(f"0x{address:x} is not mapped in the guest")

    def write(self, address: int, data: bytes) -> None:
        done = 0
        for view, offset, take in self._pieces(address, len(data)):
            view[offset : offset + take] = data[done : done + take]
            done += take

    def read(self, address: int, size: int) -> bytes:
        return b"".join(
            bytes(view[offset : offset + take]) for view, offset, take in self._pieces(address, size)
        )

    def call(self, address: int, arguments: list[Argument]) -> int:
        table = b""
        data = b""
        for argument in arguments:
            if isinstance(argument, Real):
                table += struct.pack("<IIQ", self.KIND_REAL, argument.width, argument.bits)
            elif isinstance(argument, Integer):
                table += struct.pack("<IIQ", self.KIND_INTEGER, 8, argument.bits)
            else:
                table += struct.pack("<IIQ", self.KIND_STRUCTURE, len(argument.data), 0)
                data += argument.data
        header = struct.pack("<IIQQ", self.OP_CALL, len(arguments), address, 0)
        status, value = self._ask(header + table + data)
        if status != 0:
            raise GuestFault(f"the x86_64 worker refused the call: {os.strerror(status)}")
        return value


def pages(size: int) -> int:
    return (size + PAGE - 1) // PAGE * PAGE


class Emulator:
    def __init__(
        self,
        machine: str,
        shift: int = 0,
        call_timeout: float = CALL_TIMEOUT,
        native: bool | None = None,
    ) -> None:
        """``shift`` moves the code, the stack and the rest behind the memory of a world that needs
        more than the 8 MB below them. ``call_timeout`` bounds one call (see ``RunawayCall``).

        x86_64 on an x86_64 Linux host runs natively (see ``_NativeX86``); ``native=False`` or
        ``RXF_EMULATOR=unicorn`` asks for the emulator, which a test needs when it hooks an
        instruction or a device port (``uc`` is the unicorn engine and exists only then)."""
        self.machine = machine
        self.call_timeout = call_timeout
        self.code_at, self.stack, self.scratch, self.back = (
            CODE + shift,
            STACK + shift,
            SCRATCH + shift,
            RETURN + shift,
        )
        self.next_code = self.code_at
        self._native: _NativeX86 | None = None
        self._uc: Any = None
        if machine == "x86_64" and native is not False and native_x86_available():
            self._native = _NativeX86(call_timeout)
            return
        if machine == "x86_64":
            self._uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        elif machine == "aarch64":
            self._uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
            self.uc.reg_write(arm64.UC_ARM64_REG_CPACR_EL1, CPACR_FP_ENABLED)
        else:
            raise ValueError(machine)
        self.uc.mem_map(self.stack, STACK_SIZE)
        self.uc.mem_map(self.scratch, PAGE)
        self.uc.mem_map(self.back, PAGE)

    @property
    def native(self) -> bool:
        return self._native is not None

    @property
    def uc(self) -> Any:
        """The unicorn engine: only an emulated machine has one."""
        if self._uc is None:
            raise RuntimeError(
                "this x86_64 machine runs natively and has no emulator to hook: "
                f"set {EMULATOR_ENV}=unicorn before it is made"
            )
        return self._uc

    def map(self, base: int, size: int) -> None:
        if self._native is not None:
            self._native.map(base, pages(size))
        else:
            self.uc.mem_map(base, pages(size))

    def write(self, address: int, data: bytes) -> None:
        if self._native is not None:
            self._native.write(address, data)
        else:
            self.uc.mem_write(address, data)

    def flush(self) -> None:
        """Code written over code that already ran must not be taken from the translation cache."""
        if self._native is None:
            self.uc.ctl_flush_tb()

    def read(self, address: int, size: int) -> bytes:
        if self._native is not None:
            return self._native.read(address, size)
        return bytes(self.uc.mem_read(address, size))

    def load(self, code: bytes) -> int:
        """Place a function in memory; its address."""
        address = self.next_code
        size = pages(max(len(code), 1))
        self.map(address, size)
        self.write(address, code)
        self.next_code += size
        return address

    def call(self, address: int, arguments: list[Argument]) -> int:
        """Run the function at ``address``; its 32-bit status."""
        if self._native is not None:
            return self._native.call(address, arguments)
        if self.machine == "x86_64":
            return self.call_sysv(address, arguments)
        return self.call_aapcs64(address, arguments)

    def run(self, address: int) -> None:
        """Run to the sentinel at ``self.back``, refusing a guest that never returns."""
        program_counter = (
            x86.UC_X86_REG_RIP if self.machine == "x86_64" else arm64.UC_ARM64_REG_PC
        )
        self.uc.emu_start(
            address, self.back, timeout=max(1, int(self.call_timeout * 1_000_000))
        )
        current = self.uc.reg_read(program_counter)
        if current != self.back:
            raise RunawayCall(
                f"the {self.machine} guest did not return within {self.call_timeout:.0f}s; "
                f"pc=0x{current:x}"
            )

    def call_sysv(self, address: int, arguments: list[Argument]) -> int:
        registers = iter(X86_INTEGERS)
        real = 0
        stack = bytearray()
        for argument in arguments:
            if isinstance(argument, Real):
                self.uc.reg_write(x86.UC_X86_REG_XMM0 + real, argument.bits)
                real += 1
            elif isinstance(argument, Integer):
                register = next(registers, None)
                if register is None:
                    stack += argument.bits.to_bytes(8, "little")
                else:
                    self.uc.reg_write(register, argument.bits)
            else:
                stack += argument.data.ljust(
                    len(argument.data) + -len(argument.data) % 8, b"\0"
                )
        top = self.stack + STACK_SIZE - PAGE
        self.uc.mem_write(top, self.back.to_bytes(8, "little") + bytes(stack))
        self.uc.reg_write(x86.UC_X86_REG_RSP, top)
        self.run(address)
        return self.uc.reg_read(x86.UC_X86_REG_EAX)

    def call_aapcs64(self, address: int, arguments: list[Argument]) -> int:
        slot = 0
        real = 0
        copies = 0
        stack = bytearray()

        def integer(bits: int) -> None:
            nonlocal slot
            if slot < ARM_INTEGERS:
                self.uc.reg_write(arm64.UC_ARM64_REG_X0 + slot, bits)
                slot += 1
            else:
                stack.extend(bits.to_bytes(8, "little"))

        for argument in arguments:
            if isinstance(argument, Real):
                first = (
                    arm64.UC_ARM64_REG_S0
                    if argument.width == 4
                    else arm64.UC_ARM64_REG_D0
                )
                self.uc.reg_write(first + real, argument.bits)
                real += 1
            elif isinstance(argument, Integer):
                integer(argument.bits)
            else:
                where = self.scratch + copies
                self.uc.mem_write(where, argument.data)
                copies += (len(argument.data) + 15) // 16 * 16
                integer(where)
        top = self.stack + STACK_SIZE - PAGE
        self.uc.mem_write(top, bytes(stack) or b"\0")
        self.uc.reg_write(arm64.UC_ARM64_REG_SP, top)
        self.uc.reg_write(arm64.UC_ARM64_REG_X30, self.back)
        self.run(address)
        return self.uc.reg_read(arm64.UC_ARM64_REG_W0)
