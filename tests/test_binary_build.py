"""The artifacts of the three seats, read back from their bytes.

A BIOS image starts with the boot sector the firmware jumps to, a UEFI application is
a PE/COFF of the EFI application subsystem, and the ELF is a static x86-64 executable.
Each carries the same RXFW package in a section of its own. These tests build every
target of the packaging layer and read the artifact back (the readers of
pymergetic.rxftk.binary.formats), so the proof does not trust the build.
"""

import json
from pathlib import Path

import pytest

from pymergetic.rxftk import certify, source
from pymergetic.rxftk.binary import builder, cli, formats, package
from pymergetic.rxftk.certify import (
    NotEnoughVoices,
    Policy,
    TrustKey,
    TrustStore,
    WrongDigest,
)
from pymergetic.rxftk.core import tree

from .snippets import STARTER

MAIN = "pymergetic.rxf.application.main"
NOW = 1700000000


@pytest.fixture(scope="module")
def image():
    return tree.load(STARTER)


@pytest.fixture(scope="module")
def built(image) -> bytes:
    return package.build(image)


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory, image):
    out = tmp_path_factory.mktemp("dist")
    made = {target: builder.build(target, image, out) for target in builder.TARGETS}
    files = {target: Path(info["artifact"]).read_bytes() for target, info in made.items()}
    return made, files


def test_the_bios_image_starts_with_the_boot_sector(artifacts):
    _, files = artifacts
    image = files["bios"]
    assert image[510:512] == b"\x55\xaa"  # the signature the firmware checks
    assert len(image) > 512


def test_the_bios_image_carries_the_package_on_a_sector(artifacts, built):
    _, files = artifacts
    at = files["bios"].index(built)
    assert at % 512 == 0  # the loader streams the package sector by sector
    assert files["bios"][at : at + len(built)] == built


def test_the_uefi_application_is_an_efi_pe_of_amd64(artifacts, built):
    _, files = artifacts
    pe = formats.read_pe(files["uefi"])
    assert pe.machine == formats.PE_MACHINE_AMD64
    assert pe.subsystem == formats.PE_SUBSYSTEM_EFI_APPLICATION
    assert pe.entry != 0
    assert pe.sections[".rxfpkg"] == built


def test_the_elf_is_a_static_executable_that_carries_the_package(artifacts, built):
    _, files = artifacts
    elf = formats.read_elf(files["elf"])
    assert elf.machine == formats.EM_X86_64
    assert elf.entry != 0
    assert elf.sections[".rxfpkg"] == built
    assert elf.symbols["rxf_wrap_enter"] != 0


def test_every_seat_carries_the_package_of_the_same_image(artifacts, built):
    made, _ = artifacts
    for target, info in made.items():
        assert info["package_sha256"] == package.sha256(built), target
        assert info["base"] == package.BASE
        assert info["entry"] == MAIN


def test_the_cli_builds_an_artifact_and_reports_it(tmp_path, image):
    report = cli.build("bios", str(STARTER), str(tmp_path))
    artifact = Path(report["artifact"])
    assert artifact.name == "rxf-bios.img"
    assert artifact.stat().st_size == report["bytes"]
    assert report["package_sha256"] == package.sha256(package.build(image))


def test_an_unknown_target_is_refused(tmp_path):
    with pytest.raises(builder.BuildError):
        cli.build("floppy", "", str(tmp_path))


def _trust(quorum: int, *seeds: bytes) -> TrustStore:
    keys = tuple(
        TrustKey(f"release-{index + 1}", certify.public_of(seed))
        for index, seed in enumerate(seeds)
    )
    return TrustStore(Policy(now=NOW, max_age=3600, skew=60, quorum=quorum), keys, frozenset())


def test_the_activation_quorum_is_the_gate_of_the_build(tmp_path, image, built):
    """The seam (docs/11): a voice over other words refuses the build by name
    (the `WrongDigest` rides in the quorum's report) and nothing is written;
    the right voice lets the build through."""
    seed = bytes(range(32))
    trust = _trust(1, seed)
    wrong = certify.issue("00" * 32, "11" * 32, "22" * 32, "release-1", seed, NOW)
    with pytest.raises(NotEnoughVoices) as caught:
        builder.build("elf", image, tmp_path, trust=trust, statements=[wrong])
    assert isinstance(caught.value.refused[0][1], WrongDigest)
    assert list(tmp_path.iterdir()) == []
    right = certify.issue(
        image.digest(), image.delta_digest(), package.block_digest(built), "release-1", seed, NOW
    )
    made = builder.build("elf", image, tmp_path, trust=trust, statements=[right])
    assert Path(made["artifact"]).is_file()
    assert made["block_sha256"] == package.block_digest(built)  # the word a voice signs
    elf = formats.read_elf(Path(made["artifact"]).read_bytes())
    assert package.statements(elf.sections[".rxfpkg"]) == (right,)  # the tail travels


def test_the_activation_quorum_needs_every_voice_of_its_policy(tmp_path, image, built):
    """M-of-N at the gate: one voice where the policy wants two is `NotEnoughVoices`."""
    signed = certify.issue(
        image.digest(),
        image.delta_digest(),
        package.block_digest(built),
        "release-1",
        bytes(range(32)),
        NOW,
    )
    with pytest.raises(NotEnoughVoices):
        builder.build(
            "elf",
            image,
            tmp_path,
            trust=_trust(2, bytes(range(32)), bytes(range(32, 64))),
            statements=[signed],
        )
    assert list(tmp_path.iterdir()) == []


def test_the_cli_activates_a_build_with_statement_files(tmp_path):
    """The face's wires: STORE and statement files guard `rxf binary build`."""
    seed = bytes(range(32))
    image = source.load(str(STARTER))
    signed = certify.issue(
        image.digest(),
        image.delta_digest(),
        package.block_digest(package.build(image)),
        "release-1",
        seed,
        NOW,
    )
    store_file = tmp_path / "trust.json"
    voice_file = tmp_path / "release-1.json"
    store_file.write_text(json.dumps(certify.store_wire(_trust(1, seed))), encoding="utf-8")
    voice_file.write_text(json.dumps(certify.statement_wire(signed)), encoding="utf-8")
    report = cli.build(
        "elf", str(STARTER), str(tmp_path / "dist"), str(store_file), str(voice_file)
    )
    assert Path(report["artifact"]).is_file()
