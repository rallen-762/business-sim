# Aesthetic Facelift 1 — Cancelled

**Status:** built, shipped, rejected, reverted. 2026-09-19.

| | |
|---|---|
| Built in | `8e8f6f6` — "Port the makeover mockup's visual system" |
| Undone by | `470f4a8` — the revert |
| Reference mockup | `docs/option2-mockup.html` |
| Files it touched | `app/static/css/style.css`, `app/templates/firm_dashboard.html` |

To see the full diff at any time: `git show 8e8f6f6`.
To try it again as a starting point: `git revert 470f4a8`.

---

## Why it was cancelled

Robert's verdict, in full: **"its too busy and hard to look at and read."**

That is the single most important line in this document. Everything below
is only useful in service of not landing there again. The build matched the
mockup accurately — the problem was not fidelity, it was the result.

Two candidate causes, neither confirmed, both worth testing separately next
time rather than shipping together:

1. **Density went up everywhere at once.** The panel padding dropped from
   20px to 14px, card margins from 20px to 10px, stat tiles from 14/16px to
   9/11px, and body text from ~16px to 12px. Individually each matches the
   mockup. Together they put noticeably more on screen at a smaller size,
   which is the most likely source of "too busy" and "hard to read."
2. **Everything changed in one commit.** Surfaces, border language and
   typography all moved at the same moment, so there was no way to tell
   which one was wrong — hence the three-way question that followed, and
   hence this note.

## What was actually asked for

The brief, verbatim in substance:

- Presentation layer only. No component, widget, animation, layout
  structure or behaviour changes. Nothing added, removed, reordered,
  merged or renamed. No JavaScript. The walk-in animation was mid-diagnosis
  and explicitly off-limits.
- The mockup is a **token reference, not a layout spec**. Take its colours,
  type scale, spacing scale and border language. Ignore its arrangement
  entirely; keep the existing structure and apply styling to it.
- Fixed and not up for debate: dark backgrounds throughout (no light panels
  anywhere — the sprite art has dark baked into its RGB), teal/steel as the
  primary accent, Press Start 2P for titles only, sharp corners, no JS
  libraries.
- Step 1 audit the token leaks and report before changing anything.
  Step 2 adopt the tokens. Step 3 border language. Step 4 type.
- Green and amber are semantic accents for **text and 3px edge bars only**,
  never panel backgrounds and never behind a sprite.
- Verify three edge states before calling it done: round 1 with no history,
  a loss-making firm, and an over-long team name.
- One self-contained commit, hash reported, so it could be reverted in one
  step. (This is the part that worked, and is why the revert was clean.)

Scope started as firm-dashboard-only and was widened mid-build, on request,
to every page.

## The mockup's token set

Lifted verbatim from `docs/option2-mockup.html`. Preserved here because the
mockup is a saved web page and may not survive.

```css
:root {
  /* Surfaces — all dark, never light. Sprite art has dark baked into RGB. */
  --surface-page:    #0a1826;  /* page background */
  --surface-panel:   #0f2437;  /* section panel */
  --surface-cell:    #123247;  /* inset cell inside a panel */
  --surface-well:    #0a1826;  /* gap/well colour between cells */

  /* Accents */
  --accent-teal:     #2e8fb0;  /* primary structural accent */
  --accent-teal-lt:  #6fd3e8;  /* emphasised teal value text */
  --accent-green:    #97c459;  /* profit / positive — TEXT + 3px bars only */
  --accent-amber:    #ef9f27;  /* money spent or wasted — TEXT + 3px bars only */
  --accent-red:      #e24b4a;  /* loss / bankrupt — TEXT + 3px bars only */

  /* Text */
  --text-bright:     #e2f2fa;  /* primary values */
  --text-body:       #8fb8cc;  /* labels, body */
  --text-dim:        #6b95ad;  /* supporting context */
  --text-faint:      #4a7a95;  /* section eyebrows, disabled */

  /* Type */
  --font-title: 'Press Start 2P', monospace;  /* TITLES ONLY */
  --font-data:  ui-monospace, 'Cascadia Mono', 'Consolas', monospace;

  /* Spacing scale — use these, never ad-hoc padding */
  --sp-1: 2px;  --sp-2: 6px;  --sp-3: 10px;
  --sp-4: 14px; --sp-5: 18px; --sp-6: 24px;

  /* Type scale */
  --fs-eyebrow:  10px;  /* uppercase, letter-spacing 2px */
  --fs-label:    11px;
  --fs-body:     12px;
  --fs-value:    16px;
  --fs-value-lg: 19px;
}
```

The mockup's border language, which the build adopted:

```css
.panel {
  background: var(--surface-panel);
  border-left: 3px solid var(--accent-teal);   /* NOT a full border */
  padding: var(--sp-4);
  margin-bottom: var(--sp-3);
}
.panel--warn    { border-left-color: var(--accent-amber); }
.panel--bad     { border-left-color: var(--accent-red); }
.panel--neutral { border-left-color: var(--surface-panel); }
.panel--faint   { border-left-color: var(--text-faint); }
```

## Token-leak audit — still valid, still unfixed

The revert put every one of these back. They are real problems independent
of any restyle, and worth fixing on their own without touching a colour.

| Leak | Count | Note |
|---|---|---|
| `#0a0a0a` | 11 | "dark text on a light accent fill". No token. |
| `#000` | 8 | incl. `--shadow-hard: 4px 4px 0 #000` |
| `#FFB020` | 5 | an amber in no palette at all |
| `rgba(111,168,160,…)` | 5 | the teal re-typed at four alphas |
| `rgba(20,23,26,…)` / `rgba(46,58,64,…)` | 4 | `--bg-base` / `--panel` re-typed for gradients |
| `#5B6A70`, `#7FC2B9` | 2 | icon-picker frame colours |
| `#0c2a2e` | 1 | billboard name colour |
| legacy aliases | 3 in template | `var(--bg)` ×1, `var(--danger)` ×2 |

Plus **16 hex values in `app/market_data.py`** (`SEGMENT_ACCENTS` and its
paired text colours, `SEGMENT_TRAIT_DOTS`), which reach the page through a
`--segment-accent` inline style in `firm_dashboard.html`. That is the one
place a palette value lives outside the stylesheet, and fixing it is a
Python change, not a CSS one.

`firm_dashboard.html` carried **38** inline `style=` attributes; **9** of
them had colour in them. The cancelled build converted 8 to classes
(`.banner--bad`, `.card--cell`, `.card--well`, `.card--faint`,
`.input--locked` ×3, `.text-bad`, `.spend-summary`). Those class names are
in `8e8f6f6` if wanted again.

## What survived the revert

These were separate commits and are still live:

- `e9f7481` — hero sprites no longer overflow their panels on a narrow
  window. A real bug fix, unrelated to the facelift.
- `73d1719` — the detailed brand badges.
- `5f4356e` — `tools/shoot.py` gained `--stop-after` (mid-game seeds),
  `--then` (navigate after login) and automatic tour dismissal.

## If there is a facelift 2

1. **Change one axis at a time and ship it alone.** Surfaces, then border
   language, then type — three commits, each looked at on Render before
   the next. The whole reason this one could not be salvaged is that
   "I don't like it" had three possible causes.
2. **Do not take the mockup's density.** It was designed as an isolated
   fragment, not as a page carrying five segment cards, a decision form and
   three tables. Keep the current 20px padding and ~16px body text, and
   take only the colours if colour is what is wanted.
3. **Legibility first.** 12px monospace body text on a dark navy panel was
   a downgrade from 16px sans on near-black. These pages are read by
   students on Chromebooks at the back of a classroom.
4. **The audit above is free value.** Fixing the leaks changes no pixels —
   it just moves colours into tokens. Doing that first makes any future
   restyle a handful of token edits rather than a 150-line diff.
