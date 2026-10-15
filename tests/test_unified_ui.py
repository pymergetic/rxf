"""The unified web interface (docs/21): the pins that keep every web surface on one design system.

What these pins hold, greppably:

- (a) every page — each template in ``server/templates/`` and the world page
  (``world/templates/world.html``) — links the ONE stylesheet ``/static/ui/ui.css``, the
  live-slot script, and carries the ONE shell — byte-identical to ``server/static/ui/shell.html``
  except for the one allowed rewrite (the page's own nav entry becomes ``class="active"``);
- (b) the design tokens are defined in exactly one file (``server/static/ui/ui.css``), and their
  values are the ones docs/21 spells in its spec block;
- (c) no template keeps a private color/type token block: no ``:root``, no custom-property
  definitions, and no literal palette colors (hex/rgb/hsl in a property value or a color
  attribute). Canvas art derives its palette from ui.css at runtime (`cssvar`) and keeps only
  per-kind hues, which are data, not chrome.

The shell mechanism: plain template files are read with ``read_text()`` and no Python-side
include is allowed (another lane owns app.py), so the shell is one HTML fragment
(``server/static/ui/shell.html``) that every page inlines verbatim; THIS file is the pin that
keeps the copies one. The fragment lives under ``static/ui/`` with the stylesheet because that
tree is this lane's; it is never fetched by a browser.

The ``/static`` mount for ``server/static/`` is in place (``app.mount("/static", …)`` in app.py,
written by its lane); the pages' links resolve against it. The tutor joined the system in the
integration step of docs/21: its page CSS (``static/tutor/tutor.css``) is layout only and keeps
no custom-property definition (its :root copy is commented prose, pinned to the spec by
``tests/test_tutor.py``).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVER = ROOT / "src" / "pymergetic" / "rxf" / "server"
TEMPLATES = SERVER / "templates"
WORLD_PAGE = ROOT / "src" / "pymergetic" / "rxf" / "world" / "templates" / "world.html"
SHELL = SERVER / "static" / "ui" / "shell.html"
UI_CSS = SERVER / "static" / "ui" / "ui.css"
SPEC = ROOT / "docs" / "21-UNIFIED-WEB-INTERFACE.md"

# The covered pages by name: the retrofit templates and the world page (served at /world/ by the
# same app — one service, one shell; the world page is where the shell once went missing).
PAGES = {path.name: path for path in [*sorted(TEMPLATES.glob("*.html")), WORLD_PAGE]}
COVERED = sorted(PAGES)

SHELL_BEGIN = "<!-- ui:shell:begin"
SHELL_END = "<!-- ui:shell:end -->"
ACTIVE_RE = re.compile(r'<a href="([^"]*)" class="active" aria-current="page">')
INACTIVE = '<a class="button" href="{url}">'

SURFACES = [
    ("Inspector", "/"),
    ("Model", "/model"),
    ("Editor", "/editor"),
    ("Tutor", "/tutor"),
    ("World", "/world"),
]
FACES = {
    "inspector.html": "Inspector = read",
    "model.html": "Model = explain",
    "editor.html": "Editor = edit",
    "tutor.html": "Tutor = teach",
    "world.html": "World = render",
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def uncommented(text: str) -> str:
    """The text with ``/* … */`` comments blanked out: a comment defines nothing, so a
    documented copy of the token block in prose is not a custom-property definition (a
    comment-separated ``--x/**/:`` still reads as one — comments are replaced by one space,
    never deleted)."""
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)


def shell_block(text: str) -> str:
    """The shell a page carries: everything between the ui:shell markers, markers included."""
    begin = text.index(SHELL_BEGIN)
    end = text.index(SHELL_END) + len(SHELL_END)
    return text[begin:end]


def nav_of(block: str) -> str:
    """Just the <nav> … the shell's doc comment shows the active markup as an example, and that
    example must not count as an entry."""
    nav = re.search(r"<nav[^>]*>(.*?)</nav>", block, re.DOTALL)
    assert nav, "the shell carries no nav (docs/21: a page without the nav is a bug)"
    return nav.group(1)


def normalized(block: str) -> str:
    """The shell with the active entry rewritten back to its inactive form — the one allowed
    difference between two copies. (The doc comment's markup example is rewritten too; it is
    identical in every copy, so it stays harmless.)"""
    return ACTIVE_RE.sub(lambda m: INACTIVE.format(url=m.group(1)), block)


# ---- (a) one stylesheet, one shell, per page ----------------------------------------------------


def test_every_page_links_the_one_stylesheet_and_the_live_slot():
    for name in COVERED:
        text = read(PAGES[name])
        assert 'href="/static/ui/ui.css"' in text, (
            f"{name} does not link the one stylesheet"
        )
        assert 'src="/static/ui/ui.js"' in text, (
            f"{name} does not link the live-slot script"
        )
        assert text.count("<script>") <= 1, (
            f"{name} must keep its inline code in at most one <script> block "
            "(a page whose code is all external keeps zero)"
        )


def test_every_page_carries_the_one_shell():
    canonical = normalized(shell_block(read(SHELL)))
    for name in COVERED:
        block = shell_block(read(PAGES[name]))
        assert normalized(block) == canonical, (
            f"{name} forked the shell; the one source of truth is {SHELL.relative_to(ROOT)}"
        )
        active = ACTIVE_RE.findall(nav_of(block))
        assert len(active) == 1, (
            f"{name} must mark exactly one nav entry active: {active}"
        )
        own = dict(SURFACES)[FACES[name].split(" =")[0]]
        assert active == [own], f"{name} marks {active} active, not its own {own}"


def test_the_nav_is_the_five_surfaces_in_order():
    entries = re.findall(
        r'href="([^"]*)"[^>]*>([^<]+)</a>', nav_of(shell_block(read(SHELL)))
    )
    assert entries == [(url, name) for name, url in SURFACES]


def test_the_live_slot_says_no_image_until_something_is_attached():
    for name in [*COVERED, SHELL.name]:
        text = read(SHELL) if name == SHELL.name else read(PAGES[name])
        assert '<div class="live" id="live-slot">no image</div>' in text, (
            f"{name} does not carry the pinned default of the live slot (docs/21)"
        )


def test_every_page_states_which_face_of_the_link_it_is():
    for name in COVERED:
        assert f'class="face-note">{FACES[name]}' in read(PAGES[name]), (
            f"{name} does not state its face of the link (docs/21, the seam story)"
        )


# ---- (b) one token block, the spec's values ------------------------------------------------------


def _tokens(css_block: str) -> dict[str, str]:
    return {
        name: re.sub(r"/\*.*?\*/", "", value).strip()
        for name, value in re.findall(r"--([a-z0-9-]+):\s*([^;]+);", css_block)
    }


def test_the_tokens_are_defined_in_exactly_one_file():
    definers = sorted(
        path.relative_to(ROOT).as_posix()
        for path in SERVER.rglob("*")
        if path.is_file()
        and path.suffix in {".html", ".css", ".js"}
        and re.search(r"--[a-z][\w-]*\s*:", uncommented(read(path)))
    )
    expected = [UI_CSS.relative_to(ROOT).as_posix()]
    assert definers == expected, (
        f"design tokens are defined in {definers}; only {expected[0]} may define them"
    )


def test_the_one_stylesheet_carries_the_spec_tokens():
    spec = re.search(r"```css(.*?)```", read(SPEC), re.DOTALL)
    assert spec, "docs/21 no longer spells its token block in css"
    root = re.search(r":root\s*\{(.*?)\}", read(UI_CSS), re.DOTALL)
    assert root, "ui.css no longer carries its :root token block"
    assert _tokens(root.group(1)) == _tokens(spec.group(1)), (
        "ui.css forked the token values docs/21 pins"
    )


# ---- (c) no private token block, no private palette -----------------------------------------------

COLOR_IN_PROPERTY = re.compile(r"[-a-z]+\s*:\s*[\"']?#[0-9a-fA-F]{3,8}\b")
COLOR_IN_ATTRIBUTE = re.compile(r"(?<!href)(?<!xlink:href)=\"#[0-9a-fA-F]{3,8}\"")
FUNC_COLOR = re.compile(r"\b(?:rgb|rgba|hsl|hsla)\(\s*\d")
CUSTOM_PROPERTY = re.compile(r"--[a-z][\w-]*\s*:")


def test_no_template_keeps_a_private_color_or_type_block():
    for name in COVERED:
        text = read(PAGES[name])
        assert ":root" not in text, f"{name} keeps a private token block (:root)"
        assert not CUSTOM_PROPERTY.search(text), (
            f"{name} defines its own custom properties"
        )
        for where, found in (
            ("property value", COLOR_IN_PROPERTY.search(text)),
            ("attribute", COLOR_IN_ATTRIBUTE.search(text)),
            ("function", FUNC_COLOR.search(text)),
        ):
            assert found is None, (
                f"{name} paints with a private palette literal ({where}): "
                f"...{text[max(0, found.start() - 30) : found.end() + 10]!r}"
            )


# ---- the vocabulary closes: every class a page uses has one definition ---------------------------

CLASS_ATTR = re.compile(r'class="([^"]*)"')


def _classes_used(html: str) -> set[str]:
    """Static class tokens in the markup. Dynamic values (``class="${…}"``) are out of scope —
    this is a tripwire over the vocabulary, not a parser."""
    used = set()
    for raw in CLASS_ATTR.findall(html):
        if "${" not in raw:
            used.update(raw.split())
    return used


def _classes_defined(css: str) -> set[str]:
    return set(re.findall(r"\.([A-Za-z][\w-]*)", css))


def _page_layout_css(html: str, page: Path | None = None) -> str:
    """The page's own layout CSS: its inline ``<style>`` blocks and the stylesheets it links
    under its own mounts (``/static/…``, ``/<page>/static/…``, or relative to the mount it is
    served under). docs/21 rule 1 allows per-page layout CSS; the tutor keeps that layout in
    ``static/tutor/tutor.css`` while the retrofit pages keep theirs inline — one tripwire, both
    shapes. (ui.css resolves too and is already counted on its own.)"""
    found = [
        m.group(1) for m in re.finditer(r"<style[^>]*>(.*?)</style>", html, re.DOTALL)
    ]
    for href in re.findall(r'<link rel="stylesheet" href="([^"]+)"', html):
        href = href.split("?", 1)[0]  # a cache-buster query names no file
        if href.startswith("/static/"):
            sheet = SERVER / "static" / href.removeprefix("/static/")
        elif href.startswith("/"):
            mount = re.match(r"^/([^/]+)/static/(.+)$", href)
            if not mount:
                continue
            sheet = SERVER / "static" / mount.group(1) / mount.group(2)
            if not sheet.is_file():
                # a mount may live in its own package instead of the server tree (the world:
                # `app.mount("/world/static", world/static)`)
                sheet = ROOT / "src" / "pymergetic" / "rxf" / mount.group(1) / "static" / mount.group(2)
        elif page is not None:
            # a page served under its own mount may link its assets relative to that mount
            # (the world page at /world/: `static/scene/rxf-world.css` -> world/static/scene/…)
            sheet = page.parent.parent / href
        else:
            continue
        if sheet.is_file():
            found.append(read(sheet))
    return " ".join(found)


def test_every_class_a_page_uses_is_defined_in_the_system():
    """No dark corner: a class named in a covered page is defined either in the ONE stylesheet
    (chrome, components, data face) or in that page's own layout CSS (inline ``<style>`` or its
    linked layout sheet). This is what keeps the pages from silently forking a second design
    system again."""
    in_ui = _classes_defined(read(UI_CSS))
    for name in [*COVERED, SHELL.name]:
        html = read(SHELL) if name == SHELL.name else read(PAGES[name])
        missing = sorted(
            _classes_used(html)
            - in_ui
            - _classes_defined(_page_layout_css(html, None if name == SHELL.name else PAGES[name]))
        )
        assert not missing, f"{name} uses classes no stylesheet defines: {missing}"
