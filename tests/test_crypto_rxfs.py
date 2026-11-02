"""The crypto words of the card (native/crypto/*.rxfs), pinned by the vectors.

The words are programs of ``native/graph.h`` like any other. What runs them is what runs
every program: the simulator of ``rxf_sim`` as the dev oracle, the interpreter of
``native/graph.c`` on x86_64, aarch64 and wasm32, and the in-image compiler of
``native/compile.c`` (docs/18) whose code must compute the same answers. The vectors are
their own: FIPS 180-4 and RFC 6234 for SHA-256, RFC 4231 for HMAC-SHA-256, RFC 5869 for
HKDF, RFC 7693 for BLAKE2s (the keyed cases pinned from hashlib.blake2s as the reference),
RFC 8439 for ChaCha20-Poly1305 (the A.3 reduction edge cases among its vectors), FIPS 197 and
the NIST CAVP GCMVS for AES-256-GCM (the FAIL records among its decrypt vectors),
and sweeps of lengths around every block and padding boundary held to hashlib and hmac as
the reference implementations.
"""

from __future__ import annotations

import hashlib
import hmac as hmac_module
import shutil
import struct
from pathlib import Path

import pytest
import rxfc

from pymergetic.rxftk.core import tree

from . import graph_world as world
from . import rxf_sim, rxf_world
from .snippets import STARTER

# The prove matrix: every case runs the program in a pure-Python simulator and on
# every machine's interpreter. One BLAKE2s case alone emulated for 8+ minutes
# before this mark. Nightly material like test_compile's machine matrix.
pytestmark = [pytest.mark.nightly, pytest.mark.slow]

ROOT = Path(__file__).parents[1]
CRYPTO = ROOT / "native" / "crypto"
SHA256 = rxfc.compile_file(CRYPTO / "sha256.rxfs")
HMAC_SHA256 = rxfc.compile_file(CRYPTO / "hmac_sha256.rxfs")
HMAC_BLAKE2S = rxfc.compile_file(CRYPTO / "hmac_blake2s.rxfs")
HKDF = rxfc.compile_file(CRYPTO / "hkdf.rxfs")
HKDF_BLAKE2S = rxfc.compile_file(CRYPTO / "hkdf_blake2s.rxfs")
BLAKE2S = rxfc.compile_file(CRYPTO / "blake2s.rxfs")
CHACHA20POLY1305 = rxfc.compile_file(CRYPTO / "chacha20poly1305.rxfs")
AES_GCM = rxfc.compile_file(CRYPTO / "aes_gcm.rxfs")
X25519 = rxfc.compile_file(CRYPTO / "x25519.rxfs")

MSG = rxf_world.DATA
OUT = MSG + 0x400
WORK = MSG + 0x800
WORK_BYTES = 0x800

MACHINES = [
    "x86_64",
    "aarch64",
    pytest.param(
        "wasm32",
        marks=pytest.mark.skipif(shutil.which("node") is None, reason="needs node"),
    ),
]


def pattern(length: int) -> bytes:
    """A message of mixed bytes, so no shortcut of a repeated byte passes."""
    return bytes(((i * 7) + 3) & 0xFF for i in range(length))


def arguments(*values: int) -> bytes:
    return struct.pack(f"<{len(values)}Q", *values)


# ---- SHA-256: the vectors of FIPS 180-4 and RFC 6234 --------------------------------------------

SHA_VECTORS = [
    (
        b"",
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    ),
    (
        b"abc",
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    ),
    (
        b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
        "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
    ),
    (
        (
            b"abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmno"
            b"ijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu"
        ),
        "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1",
    ),
]

SHA_LENS = [0, 1, 3, 54, 55, 56, 57, 63, 64, 65, 118, 119, 120, 121, 127, 128, 200]

# ---- HMAC-SHA-256: the cases of RFC 4231 --------------------------------------------------------

HMAC_VECTORS = [
    (
        b"\x0b" * 20,
        b"Hi There",
        "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7",
    ),
    (
        b"Jefe",
        b"what do ya want for nothing?",
        "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843",
    ),
    (
        b"\xaa" * 20,
        b"\xdd" * 50,
        "773ea91e36800e46854db8ebd09181a72959098b3ef8c122d9635514ced565fe",
    ),
    (
        bytes(range(1, 26)),
        b"\xcd" * 50,
        "82558a389a443c0ea4cc819899f2083a85f0faa3e578f8077a2e3ff46729665b",
    ),
    (
        b"\xaa" * 131,
        b"Test Using Larger Than Block-Size Key - Hash Key First",
        "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54",
    ),
    (
        b"\xaa" * 131,
        (
            b"This is a test using a larger than block-size key and a larger than "
            b"block-size data. The key needs to be hashed before being used by the "
            b"HMAC algorithm."
        ),
        "9b09ffa71b942fcb27635fbcd5b0e944bfdc63644f0713938a7f51535c3a35e2",
    ),
]

HMAC_KEY_LENS = [0, 1, 20, 63, 64, 65, 131]
HMAC_MSG_LENS = [0, 55, 64, 100]

# ---- HKDF: the cases of RFC 5869 ---------------------------------------------------------------

HKDF_VECTORS = [
    (
        bytes(range(13)),
        b"\x0b" * 22,
        bytes(range(0xF0, 0xFA)),
        42,
        "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5",
        (
            "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
            "34007208d5b887185865"
        ),
    ),
    (
        bytes(range(0x60, 0xB0)),
        bytes(range(0x50)),
        bytes(range(0xB0, 0x100)),
        82,
        "06a6b88c5853361a06104c9ceb35b45cef760014904671014a193f40c15fc244",
        (
            "b11e398dc80327a1c8e7f78c596a49344f012eda2d4efad8a050cc4c19afa97c"
            "59045a99cac7827271cb41c65e590e09da3275600c2f09b8367793a9aca3db71"
            "cc30c58179ec3e87c14c01d5c1f3434f1d87"
        ),
    ),
    (
        b"",
        b"\x0b" * 22,
        b"",
        42,
        "19ef24a32c717b167f33a91d6f648bdf96596776afdb6377ac434c1c293ccb04",
        (
            "8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d"
            "9d201395faa4b61a96c8"
        ),
    ),
]

HKDF_SWEEP = [
    (b"", b"\x11" * 32, b"", 1),
    (b"salt", b"\x22" * 16, b"", 31),
    (b"salt", pattern(40), b"info", 33),
    (pattern(70), pattern(70), pattern(70), 100),
]

# HKDF-BLAKE2s (RFC 5869's construction over HMAC-BLAKE2s-256) has no published vectors;
# this triple is pinned from the standard library's own hmac over blake2s and is the same
# one tests/test_crypto_card.py pins for the module.
HKDF_BLAKE2S_VECTORS = [
    (
        b"cap.crypto hkdf-blake2s pin: salt",
        b"cap.crypto hkdf-blake2s pin: ikm",
        b"cap.crypto hkdf-blake2s pin: info",
        42,
        "6c6fe5e799e3addd8506a1d8d29209c9d3067a6bbf24059ddb6877acb109023e",
        (
            "076d127611325d03ea8c849fd471bb6e1ab73b435da3aef2909fc842c7544a29"
            "261f5b33e51978ba14cc"
        ),
    ),
]


def hkdf_extract_reference(salt: bytes, ikm: bytes, which=hashlib.sha256) -> bytes:
    key = salt if salt else bytes(32)
    return hmac_module.new(key, ikm, which).digest()


def hkdf_reference(
    salt: bytes, ikm: bytes, info: bytes, length: int, which=hashlib.sha256
) -> bytes:
    prk = hkdf_extract_reference(salt, ikm, which)
    out, piece, counter = b"", b"", 1
    while len(out) < length:
        piece = hmac_module.new(
            prk, piece + info + bytes([counter]), which
        ).digest()
        out += piece
        counter += 1
    return out[:length]


# ---- BLAKE2s: the cases of RFC 7693 ------------------------------------------------------------

BLAKE2S_VECTORS = [
    (
        b"",
        "69217a3079908094e11121d042354a7c1f55b6482ca1a51e1b250dfd1ed0eef9",
    ),
    (
        b"abc",
        "508c5e8c327c14e2e1a72ba34eeb452f37458b209ed63a294d999b4c86675982",
    ),
]

# the keyed cases, pinned from hashlib.blake2s(key=...) as the reference
BLAKE2S_KEYED = [
    (
        b"\x0b" * 32,
        b"",
        "d1081a6bda7a43e7048350586af628dfea204f0f1e230537f33086dde9707fb0",
    ),
    (
        b"\x0b" * 32,
        b"abc",
        "8fd7818cf0505ff30a47475b652e1c13dd171f4c7bb7798fcf558c46df518754",
    ),
    (
        b"\x0b" * 32,
        pattern(64),
        "9c0a141d0d7bd3d1f96a099ad7ff228ce408764f3a07ce5827f3f2eda00b4636",
    ),
    (
        b"\x0b" * 32,
        pattern(65),
        "dcd0a8f94d38fe5b2ceecca3d3cab2b8f7e53e52e9e1ae3ca775463d6d5ed0c0",
    ),
    (
        bytes(range(1, 33)),
        b"Hi There",
        "3f53d3d3e8d99bccc2082b07e06f6417ff0858875612a7a893b8a18f601467a8",
    ),
    (
        b"K",
        b"abc",
        "ae0aafc00a962f0741fc286640a7088bb7295b57db7f68c70b845c6a23cafe3a",
    ),
]

# the digest width rides the parameter block (1..=32 bytes): the 16-byte keyed form is the
# WireGuard MAC, and it is not the 32-byte digest cut short
BLAKE2S_WIDTHS = [
    (b"", b"abc", 16, "aa4938119b1dc7b87cbad0ffd200d0ae"),
    (b"\x0b" * 32, b"abc", 16, "4644c1889210f5dee9a5b04155b64acf"),
    (bytes(range(1, 33)), b"Hi There", 16, "172e70a462a9e49abf928af8e0a85bbf"),
]

BLAKE2S_LENS = [0, 1, 3, 55, 63, 64, 65, 127, 128, 129, 200]
BLAKE2S_KEY_LENS = [1, 20, 32]
BLAKE2S_MSG_LENS = [0, 1, 63, 64, 65]
BLAKE2S_DIGEST_LENS = [1, 16, 24, 32]


# ---- ChaCha20-Poly1305: the vectors of RFC 8439 ------------------------------------------------
#
# 2.3.2 the block function, 2.4.2 the cipher, 2.5.2 the MAC, 2.8.2 the AEAD, then the
# appendices: A.1 the block function on five inputs, A.2 the cipher at the counters 0, 1
# and 42, A.3 the MAC and its seven reduction edge cases, A.4 the one-time key the AEAD
# derives from block zero, and A.5 the decryption that authenticates before it opens.

FACE_BLOCK, FACE_APPLY, FACE_POLY1305, FACE_SEAL, FACE_OPEN = 1, 2, 3, 4, 5

BLOCK_KEY = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"  # 00:01:...:1f of 2.3.2 and 2.4.2
BLOCK_NONCE = "000000090000004a00000000"
BLOCK_ANSWER = (
    "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
    "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e"
)  # 2.3.2 at the block counter 1
SUNSCREEN_NONCE = "000000000000004a00000000"
SUNSCREEN = (
    "4c616469657320616e642047656e746c656d656e206f662074686520636c6173"
    "73206f66202739393a204966204920636f756c64206f6666657220796f75206f"
    "6e6c79206f6e652074697020666f7220746865206675747572652c2073756e73"
    "637265656e20776f756c642062652069742e"
)
SUNSCREEN_ANSWER = (
    "6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0b"
    "f91b65c5524733ab8f593dabcd62b3571639d624e65152ab8f530c359f0861d8"
    "07ca0dbf500d6a6156a38e088a22b65e52bc514d16ccf806818ce91ab7793736"
    "5af90bbf74a35be6b40b8eedf2785e42874d"
)  # 2.4.2 at the initial counter 1

MAC_KEY = "85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b"  # 2.5.2 key material: r, then s
MAC_TEXT = (
    "43727970746f6772617068696320466f72756d2052657365617263682047726f"
    "7570"
)
MAC_TAG = "a8061dc1305136c6c22b8baf0c0127a9"

AEAD_KEY = "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f"  # 2.8.2
AEAD_NONCE = "070000004041424344454647"  # the fixed common part 07 00 00 00, then the IV
AEAD_AAD = "50515253c0c1c2c3c4c5c6c7"
AEAD_TEXT = (
    "4c616469657320616e642047656e746c656d656e206f662074686520636c6173"
    "73206f66202739393a204966204920636f756c64206f6666657220796f75206f"
    "6e6c79206f6e652074697020666f7220746865206675747572652c2073756e73"
    "637265656e20776f756c642062652069742e"
)
AEAD_CT = (
    "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
    "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
    "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
    "3ff4def08e4b7a9de576d26586cec64b6116"
)
AEAD_TAG = "1ae10b594f09e26a7e902ecbd0600691"
AEAD_OTK = "7bac2b252db447af09b67a55a4e955840ae1d6731075d9eb2a9375783ed553ff"  # the Poly1305 key of block 0

A5_KEY = "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0"  # A.5
A5_NONCE = "000000000102030405060708"
A5_AAD = "f33388860000000000004e91"
A5_CT = (
    "64a0861575861af460f062c79be643bd5e805cfd345cf389f108670ac76c8cb2"
    "4c6cfc18755d43eea09ee94e382d26b0bdb7b73c321b0100d4f03b7f355894cf"
    "332f830e710b97ce98c8a84abd0b948114ad176e008d33bd60f982b1ff37c855"
    "9797a06ef4f0ef61c186324e2b3506383606907b6a7c02b0f9f6157b53c867e4"
    "b9166c767b804d46a59b5216cde7a4e99040c5a40433225ee282a1b0a06c523e"
    "af4534d7f83fa1155b0047718cbc546a0d072b04b3564eea1b422273f548271a"
    "0bb2316053fa76991955ebd63159434ecebb4e466dae5a1073a6727627097a10"
    "49e617d91d361094fa68f0ff77987130305beaba2eda04df997b714d6c6f2c29"
    "a6ad5cb4022b02709b"
)
A5_TAG = "eead9d67890cbb22392336fea1851f38"
A5_TEXT = (
    "496e7465726e65742d4472616674732061726520647261667420646f63756d65"
    "6e74732076616c696420666f722061206d6178696d756d206f6620736978206d"
    "6f6e74687320616e64206d617920626520757064617465642c207265706c6163"
    "65642c206f72206f62736f6c65746564206279206f7468657220646f63756d65"
    "6e747320617420616e792074696d652e20497420697320696e617070726f7072"
    "6961746520746f2075736520496e7465726e65742d4472616674732061732072"
    "65666572656e6365206d6174657269616c206f7220746f206369746520746865"
    "6d206f74686572207468616e206173202fe2809c776f726b20696e2070726f67"
    "726573732e2fe2809d"
)
A5_OTK = "bdf04aa95ce4de8995b14bb6a18fecaf26478f50c054f563dbc0a21e261572aa"

A1_BLOCKS = [
    (
        0,
        "0000000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000000",
        (
            "76b8e0ada0f13d90405d6ae55386bd28bdd219b8a08ded1aa836efcc8b770dc7"
            "da41597c5157488d7724e03fb8d84a376a43b8f41518a11cc387b669b2ee6586"
        ),
    ),
    (
        1,
        "0000000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000000",
        (
            "9f07e7be5551387a98ba977c732d080dcb0f29a048e3656912c6533e32ee7aed"
            "29b721769ce64e43d57133b074d839d531ed1f28510afb45ace10a1f4b794d6f"
        ),
    ),
    (
        1,
        "0000000000000000000000000000000000000000000000000000000000000001",
        "000000000000000000000000",
        (
            "3aeb5224ecf849929b9d828db1ced4dd832025e8018b8160b82284f3c949aa5a"
            "8eca00bbb4a73bdad192b5c42f73f2fd4e273644c8b36125a64addeb006c13a0"
        ),
    ),
    (
        2,
        "00ff000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000000",
        (
            "72d54dfbf12ec44b362692df94137f328fea8da73990265ec1bbbea1ae9af0ca"
            "13b25aa26cb4a648cb9b9d1be65b2c0924a66c54d545ec1b7374f4872e99f096"
        ),
    ),
    (
        0,
        "0000000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000002",
        (
            "c2c64d378cd536374ae204b9ef933fcd1a8b2288b3dfa49672ab765b54ee27c7"
            "8a970e0e955c14f3a88e741b97c286f75f8fc299e8148362fa198a39531bed6d"
        ),
    ),
]

A2_STREAMS = [
    (
        0,
        "0000000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000000",
        (
            "0000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000"
        ),
        (
            "76b8e0ada0f13d90405d6ae55386bd28bdd219b8a08ded1aa836efcc8b770dc7"
            "da41597c5157488d7724e03fb8d84a376a43b8f41518a11cc387b669b2ee6586"
        ),
    ),
    (
        1,
        "0000000000000000000000000000000000000000000000000000000000000001",
        "000000000000000000000002",
        (
            "416e79207375626d697373696f6e20746f20746865204945544620696e74656e"
            "6465642062792074686520436f6e7472696275746f7220666f72207075626c69"
            "636174696f6e20617320616c6c206f722070617274206f6620616e2049455446"
            "20496e7465726e65742d4472616674206f722052464320616e6420616e792073"
            "746174656d656e74206d6164652077697468696e2074686520636f6e74657874"
            "206f6620616e204945544620616374697669747920697320636f6e7369646572"
            "656420616e20224945544620436f6e747269627574696f6e222e205375636820"
            "73746174656d656e747320696e636c756465206f72616c2073746174656d656e"
            "747320696e20494554462073657373696f6e732c2061732077656c6c20617320"
            "7772697474656e20616e6420656c656374726f6e696320636f6d6d756e696361"
            "74696f6e73206d61646520617420616e792074696d65206f7220706c6163652c"
            "207768696368206172652061646472657373656420746f"
        ),
        (
            "a3fbf07df3fa2fde4f376ca23e82737041605d9f4f4f57bd8cff2c1d4b7955ec"
            "2a97948bd3722915c8f3d337f7d370050e9e96d647b7c39f56e031ca5eb6250d"
            "4042e02785ececfa4b4bb5e8ead0440e20b6e8db09d881a7c6132f420e527950"
            "42bdfa7773d8a9051447b3291ce1411c680465552aa6c405b7764d5e87bea85a"
            "d00f8449ed8f72d0d662ab052691ca66424bc86d2df80ea41f43abf937d3259d"
            "c4b2d0dfb48a6c9139ddd7f76966e928e635553ba76c5c879d7b35d49eb2e62b"
            "0871cdac638939e25e8a1e0ef9d5280fa8ca328b351c3c765989cbcf3daa8b6c"
            "cc3aaf9f3979c92b3720fc88dc95ed84a1be059c6499b9fda236e7e818b04b0b"
            "c39c1e876b193bfe5569753f88128cc08aaa9b63d1a16f80ef2554d7189c411f"
            "5869ca52c5b83fa36ff216b9c1d30062bebcfd2dc5bce0911934fda79a86f6e6"
            "98ced759c3ff9b6477338f3da4f9cd8514ea9982ccafb341b2384dd902f3d1ab"
            "7ac61dd29c6f21ba5b862f3730e37cfdc4fd806c22f221"
        ),
    ),
    (
        42,
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
        "000000000000000000000002",
        (
            "2754776173206272696c6c69672c20616e642074686520736c6974687920746f"
            "7665730a446964206779726520616e642067696d626c6520696e207468652077"
            "6162653a0a416c6c206d696d737920776572652074686520626f726f676f7665"
            "732c0a416e6420746865206d6f6d65207261746873206f757467726162652e"
        ),
        (
            "62e6347f95ed87a45ffae7426f27a1df5fb69110044c0d73118effa95b01e5cf"
            "166d3df2d721caf9b21e5fb14c616871fd84c54f9d65b283196c7fe4f60553eb"
            "f39c6402c42234e32a356b3e764312a61a5532055716ead6962568f87d3f3f77"
            "04c6a8d1bcd1bf4d50d6154b6da731b187b58dfd728afa36757a797ac188d1"
        ),
    ),
]

A3_MACS = [
    (
        "0000000000000000000000000000000000000000000000000000000000000000",
        (
            "0000000000000000000000000000000000000000000000000000000000000000"
            "0000000000000000000000000000000000000000000000000000000000000000"
        ),
        "00000000000000000000000000000000",
    ),
    (
        "0000000000000000000000000000000036e5f6b5c5e06070f0efca96227a863e",
        (
            "416e79207375626d697373696f6e20746f20746865204945544620696e74656e"
            "6465642062792074686520436f6e7472696275746f7220666f72207075626c69"
            "636174696f6e20617320616c6c206f722070617274206f6620616e2049455446"
            "20496e7465726e65742d4472616674206f722052464320616e6420616e792073"
            "746174656d656e74206d6164652077697468696e2074686520636f6e74657874"
            "206f6620616e204945544620616374697669747920697320636f6e7369646572"
            "656420616e20224945544620436f6e747269627574696f6e222e205375636820"
            "73746174656d656e747320696e636c756465206f72616c2073746174656d656e"
            "747320696e20494554462073657373696f6e732c2061732077656c6c20617320"
            "7772697474656e20616e6420656c656374726f6e696320636f6d6d756e696361"
            "74696f6e73206d61646520617420616e792074696d65206f7220706c6163652c"
            "207768696368206172652061646472657373656420746f"
        ),
        "36e5f6b5c5e06070f0efca96227a863e",
    ),
    (
        "36e5f6b5c5e06070f0efca96227a863e00000000000000000000000000000000",
        (
            "416e79207375626d697373696f6e20746f20746865204945544620696e74656e"
            "6465642062792074686520436f6e7472696275746f7220666f72207075626c69"
            "636174696f6e20617320616c6c206f722070617274206f6620616e2049455446"
            "20496e7465726e65742d4472616674206f722052464320616e6420616e792073"
            "746174656d656e74206d6164652077697468696e2074686520636f6e74657874"
            "206f6620616e204945544620616374697669747920697320636f6e7369646572"
            "656420616e20224945544620436f6e747269627574696f6e222e205375636820"
            "73746174656d656e747320696e636c756465206f72616c2073746174656d656e"
            "747320696e20494554462073657373696f6e732c2061732077656c6c20617320"
            "7772697474656e20616e6420656c656374726f6e696320636f6d6d756e696361"
            "74696f6e73206d61646520617420616e792074696d65206f7220706c6163652c"
            "207768696368206172652061646472657373656420746f"
        ),
        "f3477e7cd95417af89a6b8794c310cf0",
    ),
    (
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
        (
            "2754776173206272696c6c69672c20616e642074686520736c6974687920746f"
            "7665730a446964206779726520616e642067696d626c6520696e207468652077"
            "6162653a0a416c6c206d696d737920776572652074686520626f726f676f7665"
            "732c0a416e6420746865206d6f6d65207261746873206f757467726162652e"
        ),
        "4541669a7eaaee61e708dc7cbcc5eb62",
    ),
]

# A.3 #5..#11: the 130-bit partial reduction, the overflow of the addition of s, the
# carry through an all-ones limb, and the results at and beside 2^130-5 (r, then s)
A3_EDGES = [
    (
        "02000000000000000000000000000000",
        "00000000000000000000000000000000",
        "ffffffffffffffffffffffffffffffff",
        "03000000000000000000000000000000",
    ),
    (
        "02000000000000000000000000000000",
        "ffffffffffffffffffffffffffffffff",
        "02000000000000000000000000000000",
        "03000000000000000000000000000000",
    ),
    (
        "01000000000000000000000000000000",
        "00000000000000000000000000000000",
        (
            "fffffffffffffffffffffffffffffffff0ffffffffffffffffffffffffffffff"
            "11000000000000000000000000000000"
        ),
        "05000000000000000000000000000000",
    ),
    (
        "01000000000000000000000000000000",
        "00000000000000000000000000000000",
        (
            "fffffffffffffffffffffffffffffffffbfefefefefefefefefefefefefefefe"
            "01010101010101010101010101010101"
        ),
        "00000000000000000000000000000000",
    ),
    (
        "02000000000000000000000000000000",
        "00000000000000000000000000000000",
        "fdffffffffffffffffffffffffffffff",
        "faffffffffffffffffffffffffffffff",
    ),
    (
        "01000000000000000400000000000000",
        "00000000000000000000000000000000",
        (
            "e33594d7505e43b900000000000000003394d7505e4379cd0100000000000000"
            "0000000000000000000000000000000001000000000000000000000000000000"
        ),
        "14000000000000005500000000000000",
    ),
    (
        "01000000000000000400000000000000",
        "00000000000000000000000000000000",
        (
            "e33594d7505e43b900000000000000003394d7505e4379cd0100000000000000"
            "00000000000000000000000000000000"
        ),
        "13000000000000000000000000000000",
    ),
]

A4_OTKS = [
    (
        "0000000000000000000000000000000000000000000000000000000000000000",
        "000000000000000000000000",
        "76b8e0ada0f13d90405d6ae55386bd28bdd219b8a08ded1aa836efcc8b770dc7",
    ),
    (
        "0000000000000000000000000000000000000000000000000000000000000001",
        "000000000000000000000002",
        "ecfa254f845f647473d3cb140da9e87606cb33066c447b87bc2666dde3fbb739",
    ),
    (
        "1c9240a5eb55d38af333888604f6b5f0473917c1402b80099dca5cbc207075c0",
        "000000000000000000000002",
        "965e3bc6f9ec7ed9560808f4d229f94b137ff275ca9b3fcbdd59deaad23310ae",
    ),
]

# ---- the cases of the four programs: (name, program, args, writes, out length, expected) --------

CASES: list[tuple[str, bytes, bytes, tuple[tuple[int, bytes], ...], int, bytes]] = []

for message, digest in SHA_VECTORS:
    CASES.append(
        (
            f"sha256({len(message)})",
            SHA256,
            arguments(MSG, len(message), OUT, WORK),
            ((MSG, message), (WORK, bytes(WORK_BYTES))),
            32,
            bytes.fromhex(digest),
        )
    )
for length in SHA_LENS:
    for message in (pattern(length), b"\x00" * length):
        CASES.append(
            (
                f"sha256({length})",
                SHA256,
                arguments(MSG, len(message), OUT, WORK),
                ((MSG, message), (WORK, bytes(WORK_BYTES))),
                32,
                hashlib.sha256(message).digest(),
            )
        )

for key, message, tag in HMAC_VECTORS:
    CASES.append(
        (
            f"hmac({len(key)},{len(message)})",
            HMAC_SHA256,
            arguments(MSG, len(key), MSG + 0x120, len(message), OUT, WORK),
            (
                (MSG, key),
                (MSG + 0x120, message),
                (WORK, bytes(WORK_BYTES)),
            ),
            32,
            bytes.fromhex(tag),
        )
    )
for key_len in HMAC_KEY_LENS:
    for message_len in HMAC_MSG_LENS:
        key, message = pattern(key_len), pattern(message_len)
        CASES.append(
            (
                f"hmac({key_len},{message_len})",
                HMAC_SHA256,
                arguments(MSG, key_len, MSG + 0x120, message_len, OUT, WORK),
                (
                    (MSG, key),
                    (MSG + 0x120, message),
                    (WORK, bytes(WORK_BYTES)),
                ),
                32,
                hmac_module.new(key, message, hashlib.sha256).digest(),
            )
        )

# ---- HMAC-BLAKE2s: the RFC 2104 construction over BLAKE2s-256 -----------------------------------
#
# No RFC names this instantiation. The cases are the RFC 4231 pairs (they are
# construction-agnostic) pinned from the standard library's own hmac over blake2s, plus
# sweeps held to it; the digest is always the 32 bytes of BLAKE2s-256.

HMAC_BLAKE2S_VECTORS = [
    (bytes.fromhex("0b" * 20), b"Hi There", "65a8b7c5cc9136d424e82c37e2707e74e913c0655b99c75f40edf387453a3260"),
    (b"Jefe", b"what do ya want for nothing?", "90b6281e2f3038c9056af0b4a7e763cae6fe5d9eb4386a0ec95237890c104ff0"),
    (bytes.fromhex("aa" * 20), bytes.fromhex("dd" * 50), "fcc4f59529502e34c3d8da3ffdab82966a2cb637ff5e9bd701135c2e9469e790"),
    (bytes.fromhex("01" * 25), bytes.fromhex("cd" * 50), "c20ac59ab44f9a398ed455d8b2a3a037e025b2448a84f62e677c15b458ac47bb"),
    (bytes.fromhex("aa" * 131), b"Test Using Larger Than Block-Size Key - Hash Key First", "d23d79394f53d536a096e6514447eeaabb05ded01be32c1937da6a8f7103bc4e"),
]
HMAC_BLAKE2S_KEY_LENS = [0, 1, 20, 64, 131]
HMAC_BLAKE2S_MSG_LENS = [0, 55, 100]

for key, message, tag in HMAC_BLAKE2S_VECTORS:
    CASES.append(
        (
            f"hmac-b2s({len(key)},{len(message)})",
            HMAC_BLAKE2S,
            arguments(MSG, len(key), MSG + 0x120, len(message), OUT, WORK),
            (
                (MSG, key),
                (MSG + 0x120, message),
                (WORK, bytes(WORK_BYTES)),
            ),
            32,
            bytes.fromhex(tag),
        )
    )
for key_len in HMAC_BLAKE2S_KEY_LENS:
    for message_len in HMAC_BLAKE2S_MSG_LENS:
        key, message = pattern(key_len), pattern(message_len)
        CASES.append(
            (
                f"hmac-b2s({key_len},{message_len})",
                HMAC_BLAKE2S,
                arguments(MSG, key_len, MSG + 0x120, message_len, OUT, WORK),
                (
                    (MSG, key),
                    (MSG + 0x120, message),
                    (WORK, bytes(WORK_BYTES)),
                ),
                32,
                hmac_module.new(key, message, hashlib.blake2s).digest(),
            )
        )

# The two faces of HKDF are separate runs of the program: extract pins the PRK of the
# vector, expand takes that PRK as its key and pins the OKM. The flavors are the two
# programs over the same words: hkdf.rxfs (HMAC-SHA-256) and hkdf_blake2s.rxfs.
def hkdf_extract_case(name, program, salt, ikm, prk):
    return (
        f"hkdf-extract({name})",
        program,
        arguments(1, MSG, len(salt), MSG + 0x80, len(ikm), 0, 0, 0, OUT, 32, WORK),
        (
            (MSG, salt),
            (MSG + 0x80, ikm),
            (WORK, bytes(WORK_BYTES)),
        ),
        32,
        prk,
    )


def hkdf_expand_case(name, program, prk, info, length, okm):
    return (
        f"hkdf-expand({name})",
        program,
        arguments(2, 0, 0, 0, 0, MSG, MSG + 0x120, len(info), OUT, length, WORK),
        (
            (MSG, prk),
            (MSG + 0x120, info),
            (WORK, bytes(WORK_BYTES)),
        ),
        length,
        okm,
    )


for salt, ikm, info, length, prk, okm in HKDF_VECTORS:
    prk, okm = bytes.fromhex(prk), bytes.fromhex(okm)
    name = f"{len(salt)},{len(ikm)},{len(info)},{length}"
    CASES.append(hkdf_extract_case(name, HKDF, salt, ikm, prk))
    CASES.append(hkdf_expand_case(name, HKDF, prk, info, length, okm))
for salt, ikm, info, length in HKDF_SWEEP:
    name = f"{len(salt)},{len(ikm)},{len(info)},{length}"
    prk = hkdf_extract_reference(salt, ikm)
    CASES.append(hkdf_extract_case(name, HKDF, salt, ikm, prk))
    CASES.append(
        hkdf_expand_case(
            name, HKDF, prk, info, length, hkdf_reference(salt, ikm, info, length)
        )
    )
for salt, ikm, info, length, prk, okm in HKDF_BLAKE2S_VECTORS:
    prk, okm = bytes.fromhex(prk), bytes.fromhex(okm)
    name = f"b2s,{length}"
    CASES.append(hkdf_extract_case(name, HKDF_BLAKE2S, salt, ikm, prk))
    CASES.append(hkdf_expand_case(name, HKDF_BLAKE2S, prk, info, length, okm))
for salt, ikm, info, length in HKDF_SWEEP:
    name = f"b2s,{len(salt)},{len(ikm)},{len(info)},{length}"
    prk = hkdf_extract_reference(salt, ikm, hashlib.blake2s)
    CASES.append(hkdf_extract_case(name, HKDF_BLAKE2S, salt, ikm, prk))
    CASES.append(
        hkdf_expand_case(
            name,
            HKDF_BLAKE2S,
            prk,
            info,
            length,
            hkdf_reference(salt, ikm, info, length, hashlib.blake2s),
        )
    )

for message, digest in BLAKE2S_VECTORS:
    CASES.append(
        (
            f"blake2s({len(message)})",
            BLAKE2S,
            arguments(MSG, 0, MSG + 0x120, len(message), OUT, 32, WORK),
            ((MSG, b""), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
            32,
            bytes.fromhex(digest),
        )
    )
for key, message, digest in BLAKE2S_KEYED:
    CASES.append(
        (
            f"blake2s({len(key)},{len(message)})",
            BLAKE2S,
            arguments(MSG, len(key), MSG + 0x120, len(message), OUT, 32, WORK),
            ((MSG, key), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
            32,
            bytes.fromhex(digest),
        )
    )
for key, message, width, digest in BLAKE2S_WIDTHS:
    CASES.append(
        (
            f"blake2s[{width}]({len(key)},{len(message)})",
            BLAKE2S,
            arguments(MSG, len(key), MSG + 0x120, len(message), OUT, width, WORK),
            ((MSG, key), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
            width,
            bytes.fromhex(digest),
        )
    )
for length in BLAKE2S_LENS:
    for message in (pattern(length), b"\x00" * length):
        CASES.append(
            (
                f"blake2s({length})",
                BLAKE2S,
                arguments(MSG, 0, MSG + 0x120, len(message), OUT, 32, WORK),
                ((MSG, b""), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
                32,
                hashlib.blake2s(message).digest(),
            )
        )
for key_len in BLAKE2S_KEY_LENS:
    for message_len in BLAKE2S_MSG_LENS:
        key, message = pattern(key_len), pattern(message_len)
        CASES.append(
            (
                f"blake2s({key_len},{message_len})",
                BLAKE2S,
                arguments(MSG, key_len, MSG + 0x120, message_len, OUT, 32, WORK),
                ((MSG, key), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
                32,
                hashlib.blake2s(message, key=key).digest(),
            )
        )
for width in BLAKE2S_DIGEST_LENS:
    message = pattern(70)
    CASES.append(
        (
            f"blake2s[{width}](70)",
            BLAKE2S,
            arguments(MSG, 0, MSG + 0x120, len(message), OUT, width, WORK),
            ((MSG, b""), (MSG + 0x120, message), (WORK, bytes(WORK_BYTES))),
            width,
            hashlib.blake2s(message, digest_size=width).digest(),
        )
    )


# ---- the cases of ChaCha20-Poly1305 -----------------------------------------------------------

def aead_case(
    name: str,
    op: int,
    key: bytes,
    nonce: bytes,
    counter: int = 0,
    aad: bytes = b"",
    text: bytes = b"",
    out_len: int = 0,
    expected: bytes = b"",
):
    return (
        name,
        CHACHA20POLY1305,
        arguments(
            op, MSG, MSG + 0x40, counter, MSG + 0x80, len(aad), MSG + 0x120, len(text), OUT, WORK
        ),
        (
            (MSG, key),
            (MSG + 0x40, nonce),
            (MSG + 0x80, aad),
            (MSG + 0x120, text),
            (WORK, bytes(WORK_BYTES)),
        ),
        out_len,
        expected,
    )


CASES.append(
    aead_case(
        "rfc8439 2.3.2 block",
        FACE_BLOCK, bytes.fromhex(BLOCK_KEY), bytes.fromhex(BLOCK_NONCE), 1,
        out_len=64, expected=bytes.fromhex(BLOCK_ANSWER),
    )
)
CASES.append(
    aead_case(
        "rfc8439 2.4.2 cipher",
        FACE_APPLY, bytes.fromhex(BLOCK_KEY), bytes.fromhex(SUNSCREEN_NONCE), 1,
        text=bytes.fromhex(SUNSCREEN),
        out_len=114, expected=bytes.fromhex(SUNSCREEN_ANSWER),
    )
)
CASES.append(
    aead_case(
        "rfc8439 2.5.2 mac",
        FACE_POLY1305, bytes.fromhex(MAC_KEY), bytes(12), text=bytes.fromhex(MAC_TEXT),
        out_len=16, expected=bytes.fromhex(MAC_TAG),
    )
)
CASES.append(
    aead_case(
        "rfc8439 2.8.2 otk",
        FACE_BLOCK, bytes.fromhex(AEAD_KEY), bytes.fromhex(AEAD_NONCE), 0,
        out_len=32, expected=bytes.fromhex(AEAD_OTK),
    )
)
CASES.append(
    aead_case(
        "rfc8439 2.8.2 seal",
        FACE_SEAL, bytes.fromhex(AEAD_KEY), bytes.fromhex(AEAD_NONCE),
        aad=bytes.fromhex(AEAD_AAD), text=bytes.fromhex(AEAD_TEXT),
        out_len=130, expected=bytes.fromhex(AEAD_CT) + bytes.fromhex(AEAD_TAG),
    )
)
CASES.append(
    aead_case(
        "rfc8439 2.8.2 open",
        FACE_OPEN, bytes.fromhex(AEAD_KEY), bytes.fromhex(AEAD_NONCE),
        aad=bytes.fromhex(AEAD_AAD),
        text=bytes.fromhex(AEAD_CT) + bytes.fromhex(AEAD_TAG),
        out_len=114, expected=bytes.fromhex(AEAD_TEXT),
    )
)
for number, (counter, key, nonce, keystream) in enumerate(A1_BLOCKS, 1):
    CASES.append(
        aead_case(
            f"rfc8439 A.1#{number} block",
            FACE_BLOCK, bytes.fromhex(key), bytes.fromhex(nonce), counter,
            out_len=64, expected=bytes.fromhex(keystream),
        )
    )
for number, (counter, key, nonce, plain, cipher) in enumerate(A2_STREAMS, 1):
    CASES.append(
        aead_case(
            f"rfc8439 A.2#{number} cipher",
            FACE_APPLY, bytes.fromhex(key), bytes.fromhex(nonce), counter,
            text=bytes.fromhex(plain),
            out_len=len(plain) // 2, expected=bytes.fromhex(cipher),
        )
    )
for number, (key, text, tag) in enumerate(A3_MACS, 1):
    CASES.append(
        aead_case(
            f"rfc8439 A.3#{number} mac",
            FACE_POLY1305, bytes.fromhex(key), bytes(12), text=bytes.fromhex(text),
            out_len=16, expected=bytes.fromhex(tag),
        )
    )
for number, (r, s, text, tag) in enumerate(A3_EDGES, 5):
    CASES.append(
        aead_case(
            f"rfc8439 A.3#{number} mac",
            FACE_POLY1305, bytes.fromhex(r + s), bytes(12), text=bytes.fromhex(text),
            out_len=16, expected=bytes.fromhex(tag),
        )
    )
for number, (key, nonce, otk) in enumerate(A4_OTKS, 1):
    CASES.append(
        aead_case(
            f"rfc8439 A.4#{number} otk",
            FACE_BLOCK, bytes.fromhex(key), bytes.fromhex(nonce), 0,
            out_len=32, expected=bytes.fromhex(otk),
        )
    )
CASES.append(
    aead_case(
        "rfc8439 A.5 otk",
        FACE_BLOCK, bytes.fromhex(A5_KEY), bytes.fromhex(A5_NONCE), 0,
        out_len=32, expected=bytes.fromhex(A5_OTK),
    )
)
CASES.append(
    aead_case(
        "rfc8439 A.5 open",
        FACE_OPEN, bytes.fromhex(A5_KEY), bytes.fromhex(A5_NONCE),
        aad=bytes.fromhex(A5_AAD),
        text=bytes.fromhex(A5_CT) + bytes.fromhex(A5_TAG),
        out_len=len(A5_TEXT) // 2, expected=bytes.fromhex(A5_TEXT),
    )
)

# ---- AES-256-GCM: the vectors of FIPS 197 and the NIST CAVP GCMVS -------------------------------

GCM_ENCBLOCK, GCM_SEAL, GCM_OPEN = 1, 2, 3

# FIPS 197, appendix C.3, AES-256: the block cipher on its own
FIPS197_KEY = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"
FIPS197_BLOCK = "00112233445566778899aabbccddeeff"
FIPS197_ANSWER = "8ea2b7ca516745bfeafc49904b496089"

# NIST CAVP AES-GCM Validation System (GCMVS), gcmEncryptExtIV256.rsp and gcmDecrypt256.rsp
# ([Keylen=256][IVlen=96][Taglen=128] sections): the .rsp files ship as megabyte binaries, so
# these records are quoted byte-for-byte from the pins of native/crypto/tests/vectors.rs (the
# verbatim mirror in google/quiche's aes_256_gcm_encrypter_test.cc, CAVP 2017-09-27)
GCM_EMPTY_KEY = "b52c505a37d78eda5dd34f20c22540ea1b58963cf8e5bf8ffa85f9f2492505b4"
GCM_EMPTY_NONCE = "516c33929df5a3284ff463d7"
GCM_EMPTY_TAG = "bdc1ac884d332457a1d2664f168c76f0"  # [PTlen=0][AADlen=0] Count 0
GCM_PT_KEY = "31bdadd96698c204aa9ce1448ea94ae1fb4a9a0b3c9d773b51bb1822666b8f22"
GCM_PT_NONCE = "0d18e06c7c725ac9e362e1ce"
GCM_PT_TEXT = "2db5168e932556f8089a0622981d017d"
GCM_PT_CT = "fa4362189661d163fcd6a56d8bf0405a"
GCM_PT_TAG = "d636ac1bbedd5cc3ee727dc2ab4a9489"  # [PTlen=128][AADlen=0] Count 0
GCM_AAD_KEY = "54e352ea1d84bfe64a1011096111fbe7668ad2203d902a01458c3bbd85bfce14"
GCM_AAD_NONCE = "df7c3bca00396d0c018495d9"
GCM_AAD_AAD = "7e968d71b50c1f11fd001f3fef49d045"
GCM_AAD_CT = "426e0efc693b7be1f3018db7ddbb7e4d"
GCM_AAD_TAG = "ee8257795be6a1164d7e1d2d6cac77a7"  # [PTlen=128][AADlen=128] Count 0
GCM_AAD_TEXT = "85fc3dfad9b5a8d3258e4fc44571bd3b"
GCM_FAIL_KEY = "9a0343f850a6427120f764789ffec6d237447b898fbf51d2182f065d3861497d"
GCM_FAIL_NONCE = "3deef6f453dd70d92143adcd"
GCM_FAIL_AAD = "dbb8226a624520863db6897017b2a4f8"
GCM_FAIL_SEALED = "e93165935ac18e3a2845d15fe31a9286f5fc50d18766bc3d9e16dd136d45816b"  # Count 2, FAIL


def gcm_case(
    name: str,
    op: int,
    key: bytes,
    nonce: bytes,
    aad: bytes = b"",
    text: bytes = b"",
    out_len: int = 0,
    expected: bytes = b"",
):
    return (
        name,
        AES_GCM,
        arguments(
            op, MSG, MSG + 0x40, MSG + 0x80, len(aad), MSG + 0x120, len(text), OUT, WORK
        ),
        (
            (MSG, key),
            (MSG + 0x40, nonce),
            (MSG + 0x80, aad),
            (MSG + 0x120, text),
            (WORK, bytes(WORK_BYTES)),
        ),
        out_len,
        expected,
    )


CASES.append(
    gcm_case(
        "fips197 C.3 encblock",
        GCM_ENCBLOCK, bytes.fromhex(FIPS197_KEY), bytes(12),
        text=bytes.fromhex(FIPS197_BLOCK),
        out_len=16, expected=bytes.fromhex(FIPS197_ANSWER),
    )
)
CASES.append(
    gcm_case(
        "gcmvs empty seal",
        GCM_SEAL, bytes.fromhex(GCM_EMPTY_KEY), bytes.fromhex(GCM_EMPTY_NONCE),
        out_len=16, expected=bytes.fromhex(GCM_EMPTY_TAG),
    )
)
CASES.append(
    gcm_case(
        "gcmvs pt128 seal",
        GCM_SEAL, bytes.fromhex(GCM_PT_KEY), bytes.fromhex(GCM_PT_NONCE),
        text=bytes.fromhex(GCM_PT_TEXT),
        out_len=32, expected=bytes.fromhex(GCM_PT_CT) + bytes.fromhex(GCM_PT_TAG),
    )
)
CASES.append(
    gcm_case(
        "gcmvs pt128 open",
        GCM_OPEN, bytes.fromhex(GCM_PT_KEY), bytes.fromhex(GCM_PT_NONCE),
        text=bytes.fromhex(GCM_PT_CT) + bytes.fromhex(GCM_PT_TAG),
        out_len=16, expected=bytes.fromhex(GCM_PT_TEXT),
    )
)
CASES.append(
    gcm_case(
        "gcmvs aad open",
        GCM_OPEN, bytes.fromhex(GCM_AAD_KEY), bytes.fromhex(GCM_AAD_NONCE),
        aad=bytes.fromhex(GCM_AAD_AAD),
        text=bytes.fromhex(GCM_AAD_CT) + bytes.fromhex(GCM_AAD_TAG),
        out_len=16, expected=bytes.fromhex(GCM_AAD_TEXT),
    )
)
CASES.append(
    gcm_case(
        "gcmvs aad seal",
        GCM_SEAL, bytes.fromhex(GCM_AAD_KEY), bytes.fromhex(GCM_AAD_NONCE),
        aad=bytes.fromhex(GCM_AAD_AAD), text=bytes.fromhex(GCM_AAD_TEXT),
        out_len=32, expected=bytes.fromhex(GCM_AAD_CT) + bytes.fromhex(GCM_AAD_TAG),
    )
)


def x25519_case(
    name: str,
    op: int,
    scalar: bytes,
    point: bytes,
    expected: bytes,
):
    return (
        name,
        X25519,
        arguments(op, MSG, MSG + 0x80, OUT, WORK),
        (
            (MSG, scalar),
            (MSG + 0x80, point),
            (WORK, bytes(WORK_BYTES)),
        ),
        32,
        expected,
    )


# ---- X25519: the vectors of RFC 7748 §5.2 and the §6.1 exchange ------------------------------
#
# The ladder is the heaviest word of the tree (255 iterations of twelve field words), so the
# matrix carries the two faces of the §6.1 exchange (every seat and every interpreter proves
# them) and the rest of the vectors pin the words on the compiled path below.

CASES.append(
    x25519_case(
        "x25519 rfc 6.1 alice public",
        2,
        bytes.fromhex(
            "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
        ),
        bytes(32),
        bytes.fromhex(
            "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
        ),
    )
)
CASES.append(
    x25519_case(
        "x25519 rfc 6.1 alice shared",
        1,
        bytes.fromhex(
            "77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a"
        ),
        bytes.fromhex(
            "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
        ),
        bytes.fromhex(
            "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"
        ),
    )
)

X25519_VECTORS = [
    (
        "x25519 rfc 5.2 #1",
        1,
        bytes.fromhex(
            "a546e36bf0527c9d3b16154b82465edd62144c0ac1fc5a18506a2244ba449ac4"
        ),
        bytes.fromhex(
            "e6db6867583030db3594c1a424b15f7c726624ec26b3353b10a903a6d0ab1c4c"
        ),
        bytes.fromhex(
            "c3da55379de9c6908e94ea4df28d084f32eccf03491c71f754b4075577a28552"
        ),
    ),
    (
        "x25519 rfc 5.2 #2",
        1,
        bytes.fromhex(
            "4b66e9d4d1b4673c5ad22691957d6af5c11b6421e0ea01d42ca4169e7918ba0d"
        ),
        bytes.fromhex(
            "e5210f12786811d3f4b7959d0538ae2c31dbe7106fc03c3efc4cd549c715a493"
        ),
        bytes.fromhex(
            "95cbde9476e8907d7aade45cb4b873f88b595a68799fa152e6f8f7647aac7957"
        ),
    ),
    (
        "x25519 rfc 6.1 bob public",
        2,
        bytes.fromhex(
            "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
        ),
        bytes(32),
        bytes.fromhex(
            "de9edb7d7b7dc1b4d35b61c2ece435373f8343c85b78674dadfc7e146f882b4f"
        ),
    ),
    (
        "x25519 rfc 6.1 bob shared",
        1,
        bytes.fromhex(
            "5dab087e624a8a4b79e17f8b83800ee66f3bb1292618b6fd1c2f8b27ff88e0eb"
        ),
        bytes.fromhex(
            "8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a"
        ),
        bytes.fromhex(
            "4a5d9d5ba4ce2de1728e3bf480350f25e07e21c947d19e3376f09b3c1e161742"
        ),
    ),
]


# ---- what runs them -----------------------------------------------------------------------------

# The ladder of x25519 walks 255 iterations of twelve field words: one run is ~130 million
# steps, an order of magnitude over the default budget of the simulator. Its cases carry the
# budget they need.
STEP_BUDGET = {X25519: 2_000_000_000}


def answer_of_sim(
    program: bytes,
    args: bytes,
    writes: tuple[tuple[int, bytes], ...],
    out_len: int,
) -> bytes:
    memory = rxf_sim.Memory()
    for address, data in writes:
        memory.write(address, data)
    status, value = rxf_sim.run(
        program, args, memory, limit=STEP_BUDGET.get(program, 50_000_000)
    )
    assert (status, value) == (0, 0)
    return memory.read(OUT, out_len)


def answer_of_session(
    session, program, args, writes, out_len: int, execute: bool = False
) -> bytes:
    session.install(program, args)
    for address, data in writes:
        session.write(address, data)
    if execute:
        compiled, size, at = session.compile()
        assert compiled == 0, at
        result = session.execute(size)
        assert size > 0
    else:
        result = session.run()
    assert result.status == 0
    return session.read(OUT, out_len)


@pytest.fixture(scope="module")
def code():
    return world.code_of(tree.load(STARTER))


@pytest.mark.parametrize(
    ("name", "program", "args", "writes", "out_len", "expected"), CASES, ids=[c[0] for c in CASES]
)
def test_the_simulator_computes_the_answer(name, program, args, writes, out_len, expected):
    assert answer_of_sim(program, args, writes, out_len) == expected


@pytest.mark.parametrize("machine", MACHINES)
def test_the_interpreter_of_every_machine_computes_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for name, program, args, writes, out_len, expected in CASES:
            assert answer_of_session(session, program, args, writes, out_len) == expected, (
                machine,
                name,
            )
    finally:
        session.close()


@pytest.mark.parametrize("machine", MACHINES)
def test_the_compiler_of_the_image_makes_code_of_it_too(machine, code):
    session = rxf_world.session_of(machine, code)
    try:
        for name, program, args, writes, out_len, expected in CASES:
            got = answer_of_session(
                session, program, args, writes, out_len, execute=True
            )
            assert got == expected, (machine, name)
    finally:
        session.close()


@pytest.mark.parametrize(
    ("name", "op", "scalar", "point", "expected"),
    X25519_VECTORS,
    ids=[v[0] for v in X25519_VECTORS],
)
def test_the_rest_of_the_x25519_vectors(name, op, scalar, point, expected, code):
    """The §5.2 pair and bob's half of §6.1: more vectors of the same words, compiled."""
    session = rxf_world.session_of("x86_64", code)
    try:
        got = answer_of_session(
            session,
            X25519,
            arguments(op, MSG, MSG + 0x80, OUT, WORK),
            ((MSG, scalar), (MSG + 0x80, point), (WORK, bytes(WORK_BYTES))),
            32,
            execute=True,
        )
        assert got == expected, name
    finally:
        session.close()


def test_x25519_refuses_the_low_order_point(code):
    """RFC 7748 §6.1: the all-zero output of a low-order point is not a secret."""
    session = rxf_world.session_of("x86_64", code)
    try:
        session.install(
            X25519,
            arguments(1, MSG, MSG + 0x80, OUT, WORK),
        )
        session.write(MSG, bytes(32))
        session.write(MSG + 0x80, bytes(32))
        session.write(WORK, bytes(WORK_BYTES))
        compiled, size, at = session.compile()
        assert compiled == 0, at
        result = session.execute(size)
        assert result.status == 6  # RXF_REFUSE_OUT_OF_RANGE
    finally:
        session.close()
def test_the_aead_open_refuses_a_tampered_tag():
    """A.5 with one bit of the tag flipped: the words refuse instead of opening."""
    key, nonce, aad = bytes.fromhex(A5_KEY), bytes.fromhex(A5_NONCE), bytes.fromhex(A5_AAD)
    tag = bytearray.fromhex(A5_TAG)
    tag[0] ^= 1
    tampered = bytes.fromhex(A5_CT) + bytes(tag)
    args = arguments(
        FACE_OPEN, MSG, MSG + 0x40, 0, MSG + 0x80, len(aad),
        MSG + 0x120, len(tampered), OUT, WORK,
    )
    memory = rxf_sim.Memory()
    for address, data in (
        (MSG, key),
        (MSG + 0x40, nonce),
        (MSG + 0x80, aad),
        (MSG + 0x120, tampered),
        (WORK, bytes(WORK_BYTES)),
    ):
        memory.write(address, data)
    status, value = rxf_sim.run(CHACHA20POLY1305, args, memory)
    assert (status, value) == (6, 0)


@pytest.mark.parametrize("machine", MACHINES)
def test_the_aead_open_refuses_the_tampered_tag_on_every_machine(machine, code):
    key, nonce, aad = bytes.fromhex(A5_KEY), bytes.fromhex(A5_NONCE), bytes.fromhex(A5_AAD)
    tag = bytearray.fromhex(A5_TAG)
    tag[0] ^= 1
    tampered = bytes.fromhex(A5_CT) + bytes(tag)
    session = rxf_world.session_of(machine, code)
    try:
        session.install(
            CHACHA20POLY1305,
            arguments(
                FACE_OPEN, MSG, MSG + 0x40, 0, MSG + 0x80, len(aad),
                MSG + 0x120, len(tampered), OUT, WORK,
            ),
        )
        for address, data in (
            (MSG, key),
            (MSG + 0x40, nonce),
            (MSG + 0x80, aad),
            (MSG + 0x120, tampered),
            (WORK, bytes(WORK_BYTES)),
        ):
            session.write(address, data)
        result = session.run()
        assert result.status == 6
    finally:
        session.close()


def test_the_gcm_open_refuses_the_cavp_fail_record():
    """gcmDecrypt256 Count 2 is a FAIL entry: the words refuse it instead of opening."""
    key, nonce = bytes.fromhex(GCM_FAIL_KEY), bytes.fromhex(GCM_FAIL_NONCE)
    aad, sealed = bytes.fromhex(GCM_FAIL_AAD), bytes.fromhex(GCM_FAIL_SEALED)
    args = arguments(
        GCM_OPEN, MSG, MSG + 0x40, MSG + 0x80, len(aad), MSG + 0x120, len(sealed), OUT, WORK
    )
    memory = rxf_sim.Memory()
    for address, data in (
        (MSG, key),
        (MSG + 0x40, nonce),
        (MSG + 0x80, aad),
        (MSG + 0x120, sealed),
        (WORK, bytes(WORK_BYTES)),
    ):
        memory.write(address, data)
    status, value = rxf_sim.run(AES_GCM, args, memory)
    assert (status, value) == (6, 0)


def test_the_gcm_open_refuses_a_flipped_tag_bit():
    """the verifying record with one bit of its tag flipped: the words refuse that too."""
    key, nonce = bytes.fromhex(GCM_AAD_KEY), bytes.fromhex(GCM_AAD_NONCE)
    aad = bytes.fromhex(GCM_AAD_AAD)
    tag = bytearray.fromhex(GCM_AAD_TAG)
    tag[0] ^= 1
    tampered = bytes.fromhex(GCM_AAD_CT) + bytes(tag)
    args = arguments(
        GCM_OPEN, MSG, MSG + 0x40, MSG + 0x80, len(aad), MSG + 0x120, len(tampered), OUT, WORK
    )
    memory = rxf_sim.Memory()
    for address, data in (
        (MSG, key),
        (MSG + 0x40, nonce),
        (MSG + 0x80, aad),
        (MSG + 0x120, tampered),
        (WORK, bytes(WORK_BYTES)),
    ):
        memory.write(address, data)
    status, value = rxf_sim.run(AES_GCM, args, memory)
    assert (status, value) == (6, 0)


@pytest.mark.parametrize("machine", MACHINES)
def test_the_gcm_open_refuses_on_every_machine(machine, code):
    key, nonce = bytes.fromhex(GCM_AAD_KEY), bytes.fromhex(GCM_AAD_NONCE)
    aad = bytes.fromhex(GCM_AAD_AAD)
    tag = bytearray.fromhex(GCM_AAD_TAG)
    tag[0] ^= 1
    tampered = bytes.fromhex(GCM_AAD_CT) + bytes(tag)
    session = rxf_world.session_of(machine, code)
    try:
        session.install(
            AES_GCM,
            arguments(
                GCM_OPEN, MSG, MSG + 0x40, MSG + 0x80, len(aad), MSG + 0x120,
                len(tampered), OUT, WORK,
            ),
        )
        for address, data in (
            (MSG, key),
            (MSG + 0x40, nonce),
            (MSG + 0x80, aad),
            (MSG + 0x120, tampered),
            (WORK, bytes(WORK_BYTES)),
        ):
            session.write(address, data)
        result = session.run()
        assert result.status == 6
    finally:
        session.close()
