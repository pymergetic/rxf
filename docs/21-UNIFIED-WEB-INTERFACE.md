# 21 — The unified web interface

**Status: binding for every web surface.** The inspector family, the model page, the editor,
the tutor, and the 3D world's HUD are **one application**, not a collection of pages that
happen to share a port. This document is the design system; surfaces may add components,
but they may not fork tokens, chrome, or navigation.

## The one look: dark tactical chrome

The identity comes from the world's HUD (and the image that goes out publicly): dark
tactical ground, lit text, monospace data, accent-colored state. One look everywhere —
the inspector reads like the cockpit of the same machine the world renders.

## Tokens (`server/static/ui/ui.css` — the single stylesheet)

```css
:root {
  color-scheme: dark;
  --bg:        #0f151c;   /* page ground (the world's arena floor) */
  --panel:     #16202b;   /* cards, headers, toolbars */
  --panel-2:   #1c2834;   /* nested panels, table stripes */
  --line:      #2b3b4c;   /* borders, dividers */
  --text:      #cfe4ef;   /* primary text */
  --muted:     #7d94a6;   /* secondary text, labels */
  --accent:    #2fd9c0;   /* links, live state (checksum teal) */
  --hot:       #d9a13a;   /* attention (generation gold) */
  --bad:       #e0525f;   /* refusals, poison, races (poison red) */
  --mono:      ui-monospace, "Cascadia Mono", Menlo, monospace;
  --radius:    6px;
}
```

Semantic mapping is fixed across surfaces: **teal = live/ok**, **gold = attention/generation**,
**red = refusal/poison/race**. The world's S1 payload coloring (checksum teal, poison red,
generation gold) is the same vocabulary — one meaning, one color.

## The shell (one header, one navigation)

Every page carries the same header bar: product mark (`RXF`) on the left, then the nav,
then the live summary slot on the right (session state / metrics when a world is attached).

```
RXF | Inspector · Model · Editor · Tutor · World | [live: session · cells · checksum ● ok]
```

- The nav is **one include** (`server/templates/ui/shell.html` or an equivalent fragment);
  adding a surface adds one nav entry, not a new chrome.
- The active surface is marked in the nav (accent underline or badge).
- The **live slot** is the same seam everywhere: it shows the attached session
  (`pymergetic.rxf.console.world`) or `no image` — one link, one truth, same wording.

## Component vocabulary (shared classes in `ui.css`)

- `.card` — panel with a small uppercase title row (like the inspector's cards today).
- `.badge` — pill for kinds/state; state colors from the semantic mapping.
- `.grid`/`table` — data tables with muted headers, 1px lines, no zebra noise.
- `.header-row` — the cell-header-word row style (offset · role · value) used by the model
  page's live overlay, the editor's poke panel, and the world's selection HUD — one style.
- `.mono` — data in `--mono` (all addresses, handles, words, digests).
- `.refusal` — red-bordered inline block naming the refusal code; refusals are first-class UI.
- `.tabs` — the tab bar, shared since the tutor joined: `button.tab[data-tab="<panel-id>"]` names its panel by id, the active tab is `.tab.active` + `aria-selected`, panels stay in the DOM and hide via `hidden`, and `.tabs.sub` is the second-level variant.
- Buttons/controls: flat, `--panel-2` on `--line`, accent on hover; no gradients.

## Rules

1. **One stylesheet.** Every page links `ui.css`; no page keeps a private copy of the tokens.
   Per-page CSS is allowed only for page-specific layout (e.g. the editor's split panes),
   never for colors, type, or chrome.
2. **One shell.** Header + nav come from the one include. A page without the nav is a bug.
3. **One vocabulary.** New components extend `ui.css`; they do not invent parallel ones
   (`card` vs `panel` vs `tile` for the same thing is forbidden).
4. **Typography:** system-ui for prose, `--mono` for data; 13px base, 11px uppercase
   labels (letter-spacing .06em) as today.
5. **The world** keeps its canvas (Babylon) but its DOM HUD uses the same tokens, header,
   and vocabulary — it is a surface of the app, not a second product.
6. **Accessibility floor:** contrast on `--text`/`--muted` over `--bg`/`--panel` stays
   legible; every interactive element is focus-visible.

## The seam story (what the UI says)

One **link** (a running image) may carry one **runner** (session); the surfaces are faces
of that link: Inspector = read, Editor = edit, Model = explain, Tutor = teach,
World = render, `rxf live`/`rxf console` = frames/execute. The UI makes this visible: the
live slot is identical everywhere, and each surface states (small muted line) which face
of the link it is.

## Acceptance

- `grep` shows no duplicated token blocks: `:root` variables are defined once (`ui.css`).
- Every template links `ui.css` and includes the shell; a pin test asserts both for every
  page in `server/templates/`.
- Screenshot consistency: all surfaces at 1440×900 read as one product (same header, same
  type, same colors).
- The tutor is born in this system; the retrofit of inspector/model/editor/world-HUD is one
  change together with this document.
