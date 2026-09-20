"""Generate exact AArch64 typed-CFG proof bytes."""

from pathlib import Path

from pymergetic.rxf.compiler.cfg import (
    BranchBool,
    ConstOp,
    ControlBlock,
    ControlFunction,
    ReturnControl,
)
from pymergetic.rxf.compiler.control_native import emit_control
from pymergetic.rxf.compiler.ir import TypeRef, ValueClass, VirtualValue
from pymergetic.rxf.compiler.native import Architecture

T = TypeRef(7, 4, ValueClass.INTEGER)


def v(i, b=1):
    return VirtualValue(i, T, i, b)


c, s1, s2, one, two = v(1), v(2), v(5), v(3, 2), v(4, 3)
fn = ControlFunction(
    70000,
    (),
    (
        ControlBlock(1, (), (ConstOp(c, 1),), BranchBool(c, 2, 3)),
        ControlBlock(2, (), (ConstOp(one, 1), ConstOp(s1, 0)), ReturnControl(s1, one)),
        ControlBlock(3, (), (ConstOp(two, 2), ConstOp(s2, 0)), ReturnControl(s2, two)),
    ),
    1,
    T,
)
raw = emit_control(fn, Architecture.AARCH64).text
lines = [
    '.section .text.compiler_cfg,"ax"',
    ".balign 16",
    ".global control_if",
    ".type control_if, %function",
    "control_if:",
]
for i in range(0, len(raw), 16):
    lines.append("    .byte " + ",".join(f"0x{x:02x}" for x in raw[i : i + 16]))
lines.append(".size control_if, .-control_if")
Path(__file__).parents[1].joinpath("generated/compiler_cfg_qemu_aarch64.S").write_text(
    "\n".join(lines) + "\n"
)
