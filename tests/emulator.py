"""Machine code of the starter run in an emulator (unicorn), under its C calling convention.

x86_64 follows System V: integers in rdi, rsi, rdx, rcx, r8, r9 and then on the
stack, floating point in xmm0..7, a 24-byte structure by value on the stack.
aarch64 follows AAPCS64: integers in x0..x7, floating point in s0..s7/d0..d7,
a structure larger than 16 bytes as a copy whose address takes an integer slot.
"""

from dataclasses import dataclass
import time

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
# until somebody kills the run. The slices keep unicorn's timeout small enough
# to notice the budget without another thread.
CALL_TIMEOUT = 600.0
SLICE_TIMEOUT = 5.0


class RunawayCall(RuntimeError):
    """The guest did not return to the sentinel inside the call budget."""


def pages(size: int) -> int:
    return (size + PAGE - 1) // PAGE * PAGE


class Emulator:
    def __init__(self, machine: str, shift: int = 0, call_timeout: float = CALL_TIMEOUT) -> None:
        """``shift`` moves the code, the stack and the rest behind the memory of a world that needs
        more than the 8 MB below them. ``call_timeout`` bounds one call (see ``RunawayCall``)."""
        self.machine = machine
        self.call_timeout = call_timeout
        self.code_at, self.stack, self.scratch, self.back = (
            CODE + shift,
            STACK + shift,
            SCRATCH + shift,
            RETURN + shift,
        )
        if machine == "x86_64":
            self.uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        elif machine == "aarch64":
            self.uc = unicorn.Uc(unicorn.UC_ARCH_ARM64, unicorn.UC_MODE_ARM)
            self.uc.reg_write(arm64.UC_ARM64_REG_CPACR_EL1, CPACR_FP_ENABLED)
        else:
            raise ValueError(machine)
        self.uc.mem_map(self.stack, STACK_SIZE)
        self.uc.mem_map(self.scratch, PAGE)
        self.uc.mem_map(self.back, PAGE)
        self.next_code = self.code_at

    def map(self, base: int, size: int) -> None:
        self.uc.mem_map(base, pages(size))

    def write(self, address: int, data: bytes) -> None:
        self.uc.mem_write(address, data)

    def flush(self) -> None:
        """Code written over code that already ran must not be taken from the translation cache."""
        self.uc.ctl_flush_tb()

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.uc.mem_read(address, size))

    def load(self, code: bytes) -> int:
        """Place a function in memory; its address."""
        address = self.next_code
        size = pages(max(len(code), 1))
        self.uc.mem_map(address, size)
        self.uc.mem_write(address, code)
        self.next_code += size
        return address

    def call(self, address: int, arguments: list[Argument]) -> int:
        """Run the function at ``address``; its 32-bit status."""
        if self.machine == "x86_64":
            return self.call_sysv(address, arguments)
        return self.call_aapcs64(address, arguments)

    def run(self, address: int) -> None:
        """Run to the sentinel at ``self.back``, in slices, refusing a guest that never returns."""
        program_counter = (
            x86.UC_X86_REG_RIP if self.machine == "x86_64" else arm64.UC_ARM64_REG_PC
        )
        deadline = time.monotonic() + self.call_timeout
        current = address
        while True:
            left = deadline - time.monotonic()
            slice_us = max(1, int(min(SLICE_TIMEOUT, left) * 1_000_000))
            self.uc.emu_start(current, self.back, timeout=slice_us)
            current = self.uc.reg_read(program_counter)
            if current == self.back:
                return
            if time.monotonic() >= deadline:
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
