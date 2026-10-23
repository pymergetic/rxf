# Third-Party Notices

This file lists the open-source components used by **pymergetic-rxf** (this
repository), how each one enters the product, and what its license requires of
us. It covers three audiences at once: anyone receiving a wheel or sdist of
this package, anyone building it from source, and anyone running its test and
image toolchain.

pymergetic-rxf itself is licensed under **Apache-2.0** (see `LICENSE`).

Terminology used in the "Role" column:

- **shipped** — the component's code is inside a distributed artifact (the
  wheel or sdist) of pymergetic-rxf. Its license terms apply to that artifact.
- **linked** — compiled or linked into the built `pymergetic.rxftk.core`
  extension. Same obligations as shipped.
- **runtime dep** — installed alongside the package at run time, distributed
  separately in its own package.
- **build tool** / **test tool** — executed as separate programs during build
  or testing. Not redistributed with this package; their licenses impose no
  obligations on our artifacts.

The `VENDOR.json` files next to vendored trees record provenance (source URL,
retrieval date, sha256) for every vendored file.

## A. Vendored and shipped

| Component | Version | License | Role | Location |
|---|---|---|---|---|
| pugixml | 1.14 | MIT | linked (statically, unmodified) | `third_party/pugixml/` |
| nanobind | 3.1.0 | BSD-3-Clause | linked (headers + generated binding code) | build-time, via pip |
| Babylon.js | 9.30.0 | Apache-2.0 | shipped (unmodified UMD bundle) | `src/pymergetic/rxftk/world/static/vendor/babylon/` |

### pugixml (MIT)

The XML parser/printer behind the `.rxfd` delta files. Vendored unmodified
(`PUGIXML_NO_XPATH`, `PUGIXML_NO_EXCEPTIONS` are compile-time feature flags,
not source changes), statically linked into the core extension.

> Copyright (c) 2006-2023 Arseny Kapoulkine
>
> Permission is hereby granted, free of charge, to any person obtaining a copy
> of this software and associated documentation files (the "Software"), to deal
> in the Software without restriction, including without limitation the rights
> to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
> copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in
> all copies or substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
> IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
> FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
> AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
> LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
> OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
> SOFTWARE.

Full text: `third_party/pugixml/LICENSE.md`.

### nanobind (BSD-3-Clause)

The C++/Python binding layer of `pymergetic.rxftk.core`. Its headers are
included at build time and its generated binding sources are compiled into the
extension, so its terms apply to our binaries.

> Copyright (c) 2022 Wenzel Jakob \<wenzel.jakob@epfl.ch\>, All rights reserved.
>
> Redistribution and use in source and binary forms, with or without
> modification, are permitted provided that the following conditions are met:
>
> 1. Redistributions of source code must retain the above copyright notice,
>    this list of conditions and the following disclaimer.
> 2. Redistributions in binary form must reproduce the above copyright notice,
>    this list of conditions and the following disclaimer in the documentation
>    and/or other materials provided with the distribution.
> 3. Neither the name of the copyright holder nor the names of its contributors
>    may be used to endorse or promote products derived from this software
>    without specific prior written permission.
>
> THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
> AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
> IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
> ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
> LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
> CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
> SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
> INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
> CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
> ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
> POSSIBILITY OF SUCH DAMAGE.

This document is the "documentation and/or other materials" required by
clause 2. Full text: `nanobind` package `LICENSE` in the build environment.

### Babylon.js (Apache-2.0)

The WebGL2/WebGPU renderer behind `pymergetic.rxftk.world`'s scene page.
Vendored as one unmodified UMD bundle; sha256-pinned in `VENDOR.json`
(retrieved 2026-10-14 from the npm registry). The world page loads it from
`/world/static/vendor/babylon/babylon.js` — no CDN script tag anywhere.

Apache-2.0 §4(d) requires the attribution notices of a distributed NOTICE file
to travel with derivative works. The upstream notice is preserved verbatim at
`src/pymergetic/rxftk/world/static/vendor/babylon/NOTICE.md` and repeated here:

> Babylon.js
> Copyright 2023 The Babylon.js team

Licensing terms: Apache License, Version 2.0 (full text:
`src/pymergetic/rxftk/world/static/vendor/babylon/license.md` and `LICENSE`).
The bundle is distributed unmodified; no source files were changed.

## B. Python dependencies (installed, not vendored)

### Runtime

| Package | Version | License |
|---|---|---|
| fire | 0.7.1 | Apache-2.0 (Google Inc.) |

### `server` extra (the `rxf serve` surface)

| Package | License |
|---|---|
| fastapi | MIT |
| uvicorn[standard] | BSD-3-Clause |
| starlette | BSD-3-Clause |
| h11 | MIT |
| click | BSD-3-Clause |
| idna | BSD-3-Clause |
| anyio | MIT |
| typing-extensions | PSF-2.0 |
| pydantic | MIT |
| pydantic-core | MIT |
| annotated-types | MIT |
| typing-inspection | MIT |
| python-multipart | Apache-2.0 |
| jinja2 | BSD-3-Clause |
| markupsafe | BSD-3-Clause |
| httptools | MIT |
| uvloop | Apache-2.0 |
| watchfiles | MIT |
| websockets | BSD-3-Clause |
| python-dotenv | BSD-3-Clause |
| pyyaml | MIT |

### `test` extra

| Package | License |
|---|---|
| pytest | MIT |
| pytest-xdist | MIT |
| execnet | MIT |
| fastapi | MIT |
| unicorn | BSD (as declared by the PyPI metadata of 2.1.4) |
| httpx | BSD-3-Clause |
| certifi | MPL-2.0 |

`unicorn` is used only to execute CPU emulation in tests and is never
redistributed; before redistributing anything that links it, confirm its
license against the upstream `unicorn-engine/unicorn` repository.

### Developer tooling in the venv (never shipped)

playwright (Apache-2.0), pillow (MIT-CMU), py-spy (MIT), termcolor (MIT),
Pygments (BSD-2-Clause), PyYAML (MIT), vcs-versioning (MIT), pathspec
(MPL-2.0), greenlet (MIT AND PSF-2.0).

## C. Build toolchain (executed as tools, never linked or shipped)

| Tool | Version here | License | Used for |
|---|---|---|---|
| scikit-build-core | 1.1.1 | Apache-2.0 | PEP 517 build backend |
| setuptools-scm | ≥8 | MIT | version from git tags |
| CMake | ≥3.26 (3.28.3) | BSD-3-Clause | build driver for the core extension |
| Ninja | 1.11.1 | Apache-2.0 | CMake generator |
| CPython | 3.12–3.14 | PSF-2.0 | interpreter, headers (Development.Module) |
| GCC / G++ | 13.3.0 | GPL-3.0-or-later, with GCC Runtime Library Exception for libgcc | default host compiler |
| GNU binutils (ld, objcopy, gas) | 2.42 | GPL-3.0-or-later | linking, boot image assembly |
| Clang / LLD | 18.1.3 | Apache-2.0 with LLVM Exception | wasm32 seat, UEFI PE stub (`lld-link`) |
| Node.js | 18.19.1 | MIT (itself; bundled deps vary) | wasm32 seat runner |
| GNU Make | — | GPL-3.0-or-later | `boot/Makefile` driver |
| coreutils (`dd`) | — | GPL-3.0-or-later | raw disk image layout |
| git | 2.43.0 | GPL-2.0-only | source management, versioning |
| uv | 0.11.6 | Apache-2.0 OR MIT | environment/bootstrap tooling |

## D. Test and image tools (executed as tools, never linked or shipped)

| Tool | Version here | License | Used for |
|---|---|---|---|
| QEMU (qemu-system-x86_64, qemu-system-aarch64) | 8.2.2 | GPL-2.0-only-or-later | boot smoke tests, emulated lanes |
| mtools | 4.0.43 | GPL-3.0-or-later | FAT population of `disk.img` |
| dosfstools (mkfs.fat) | 4.2 | GPL-3.0-or-later | FAT formatting of `disk.img` |
| MicroPython unix port | workspace `packages/micropython`, `packages/micropython-wasmmod` | MIT | reference interpreter in the `test_rxpy` differential |

## E. License compliance notes

1. **GPL tools do not affect our artifacts.** QEMU, mtools, dosfstools, GNU
   binutils, GCC, GNU Make and git run as separate, unmodified programs at
   build or test time. Nothing from them is linked into the wheel, the sdist,
   the core extension, the boot images or `disk.img`; the outputs they produce
   (object files assembled from *our* sources, FAT images containing *our*
   files) carry our license, not theirs.
2. **MPL-2.0 components** (certifi, pathspec) are file-level copyleft and
   appear only in test/developer environments. They are unmodified and never
   redistributed by us; MPL-2.0 obligations would only apply if we shipped
   modified versions of their files.
3. **BSD/MIT components** require exactly what this file provides: the
   copyright notice and the license text travel with any redistribution.
   `THIRD-PARTY-NOTICES.md` is shipped in both the sdist and the wheel (see
   `license-files` in `pyproject.toml`).
4. **Apache-2.0 components** (this project, fire, python-multipart, uvloop,
   scikit-build-core, Babylon.js) grant an express patent license; §4(d)
   NOTICE propagation for Babylon.js is satisfied by the preserved and quoted
   notice in §A. No vendored source file was modified; `VENDOR.json` pins
   hashes as proof.
5. **The MicroPython reference binary** is a workspace sibling, consumed as an
   external process. It is MIT; its own tree carries its own `LICENSE`.

## F. First-party components that look like third-party

Listed here because auditors regularly ask; all of them are original code of
this repository, covered by its Apache-2.0 license, with no upstream of their
own:

- **`tiny`** (`native/tiny/*.rxfs`, `tools/native/rxfc.py`) — the tiny
  compiler language and its self-hosting program (docs/19, WP12). First-party.
- **The crypto primitives** (`native/crypto/*.rxfs`,
  `native/wrap/wrap_crypto.c`) — implementations of published standards
  written from their specifications: Merkle-Damgård hashing with the FIPS
  180-4 constants, HMAC per RFC 2104 over BLAKE2s-256 per RFC 7693, HKDF per
  RFC 5869, edwards25519 signature verification per RFC 8032. Standards
  specify algorithms and test vectors (facts), not copyrightable code; no
  third-party implementation was ported or translated.
- **The `rxpy` corpus** (`tests/rxpy_corpus/`) — original differential-test
  cases, not borrowed CPython or MicroPython tests.
