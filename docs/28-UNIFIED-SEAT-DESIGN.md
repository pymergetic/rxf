# Unified seat design: the image, the face, the fill, and the shell

The boundary between "the OS", "the program" and "the browser" is where designs rot: every
seat grows its own words for the same thing until nothing means the same anywhere. This
document fixes one anatomy for every seat and one question that settles what may sit outside
the image. The seats themselves are named in [17](17-OPERATING-SYSTEM-PATH.md); this
document is the design they obey.

## The anatomy

A seat is four layers, in this order:

1. **The image.** The typed object world (the one truth: the graph, the words, the deltas).
   It is the same bytes on every seat. Nothing about it is per-platform.
2. **The face.** The module and Python/C surface (`pymergetic.*` modules, the `pm_*` C ABI).
   **Identical on every seat.** Same names, same semantics, same refusals. A face that
   differs per seat is two products.
3. **The fill.** Who actually services a capability of the face where the platform requires
   a different mechanism. The fill is the *only* thing allowed to differ, and it differs at
   predeclared seams only: `io.fetch` is POSIX on unix, a provider's park on firmware,
   `js.fetch` in the browser ([25](25-NETWORK-ORCHESTRATION.md)). A fill is
   a stub that **fails its prove** when it is missing — never a dark port.
4. **The shell.** Anything around the image that hosts or presents it: the page, the server,
   the world view, a HUD. The shell is **not** a fill and **never** carries the
   face. Where it exists, it calls the same face the other seats call.

```text
the image   ---- one truth, identical bytes
the face    ---- one surface, identical names and semantics
the fill    ---- per seat, per declared seam, proven or stubbed
the shell   ---- only where the platform's natural unit of execution is a page
```

## The glue rule

The main platforms are *the program* and *the OS*. On those seats the outside-the-image glue
is kept to the minimum that lets the image run at all: a wrapper that activates the image
([17](17-OPERATING-SYSTEM-PATH.md), [26](26-RECIPES.md) Recipe 3), the fills of the declared
seams, and nothing else. What the OS seat needs, the image itself provides as typed
functions; a second service outside the image is a second implementation.

The browser seat may carry a shell — the page, the viewer, the controls — because the
browser's natural unit of execution *is* a page and a bare guest of a page is unnatural to
run. The shell makes the execution natural; it does not extend the face. The test: if the
shell disappeared, the guest would still run its face on `js.fetch`, and every other seat
would be untouched. If a shell removal would change the *face*, something leaked out.

Concretely, in the world view (the shell of the browser seat): the canvas, the camera, the
HUD, the scene export are shell; `pymergetic.*` calls the view makes are the face; the fetch
of a scene document is the `js.fetch` fill. The firmware seat has no shell and needs none:
its "presentation" is a typed function of the image.

## The words first rule

Functionality is RXF first: what a seat does is the words of the image, written as source
(`.rxfs`, the language of `tools/native/rxfc.py`) and callable from the loader on — from the
boot wrapper and a CPython extension just as from a seat's fill. Native code around that is a
mini shim: it hands over the request and the memory and runs the words, nothing else. A shim
that carries logic is a second implementation of the image and stops existing.

The one window before this is the boot that must verify the image before it can run it
(`native/wrap/`); it ends where the arena exists. Everything after that is words with shims.

When the binary grows too fat, a module leaves the image as its XML delta and is pulled back
in when it is needed again. The export and the import of a delta are what the object world
already does (`rxpack`, `Image.apply`); trading size for a pull is a property of content, not
a second format.

## The seats

(the seat names follow [17](17-OPERATING-SYSTEM-PATH.md))

| Seat | The image runs as | Shell | Glue outside the image |
|---|---|---|---|
| host prove | a prove binary | none | the binary's own `main` |
| Unix | a program (µPy guest: [23](23-MICROPYTHON-EXPANSION.md)) | none | process bootstrap + fills |
| WebAssembly (browser) | a guest of a page | **allowed** (page, viewer, HUD) | the page + its `js.fetch` fill |
| BIOS | the OS | none | the wrapper ([17](17-OPERATING-SYSTEM-PATH.md), the activation seam) |
| UEFI | the OS | none | the wrapper (the same seam) |

Every face lands on **every** seat in one change, or it lands on none (the standing rule):
one suite run (`pytest tests/`) proves the core, the wrapped images (the BIOS and UEFI
boots), the wasm guest and the browser shell. A face that proves on one seat and is "later"
on the others is not a face.

## What settles a dispute

When something new wants to exist and it is unclear whether it is image, face, fill or
shell, in this order:

1. **Can it be a typed function of the image?** Then it is the image. No new layer.
2. **Is it a capability the platform must service differently at a declared seam?** Then it
   is a fill of that seam — and every seat that has the seam gets its fill in the same
   change.
3. **Is it presentation or hosting of an already-running guest?** Then it is shell — and
   only on a seat whose natural unit of execution is a page (today: the browser).
4. **Is it a second way to do a face that exists?** Then it does not exist.

## Status

- The anatomy and the glue rule are **[present]** as a design; this document is the first
  place they are written down.
- The fills of `io.fetch` are **[present]** on the seats named above
  ([25](25-NETWORK-ORCHESTRATION.md)); the browser
  shell (world view, HUD) is **[present]** and carries no face of its own.
- The firmware seat's shell-free presentation (typed functions instead of a view) is
  **[partial]**: the wrapper and the activation seam are present; the OS-side services of
  [17](17-OPERATING-SYSTEM-PATH.md) are still the open work of that document.
- A prove that asserts *no face code lives in a shell file* (a lint of the boundary itself)
  is **[missing]**; until it exists, the rule is enforced by review and by the seat proves.
- The words first rule is **[partial]**: the hash side of the crypto card is written and
  proven as words (SHA-256, HMAC-SHA-256, HKDF in `native/crypto/*.rxfs`,
  `tests/test_crypto_rxfs.py`: FIPS 180-4, RFC 4231, RFC 5869 on the simulator, the
  interpreter of every machine and the code the in-image compiler makes); the symmetric and
  the key-agreement primitives, and the shims that call the words, are the open work it names.

This design names no runtime provider. Providers are external and optional — Metal among
them ([integrations/metal.md](integrations/metal.md)); its concepts and maps stay in its own
project and are not normative here.
