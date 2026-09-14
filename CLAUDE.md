# business-sim — Headphone Company Simulator

Classroom economics simulator. Student teams run competing headphone companies over 10 rounds;
the teacher controls pacing from a Teacher Dashboard. Users are high-school students on school
devices — Chromebooks and iPads, small screens, shared between classes, and students working
fast. Clarity beats cleverness in the UI.

- Live: https://business-sim-74hd.onrender.com (Render, auto-deploys from `main`)
- Repo: `rallen-762/business-sim`

## Stack

Flask + SQLAlchemy. Postgres in production, SQLite `dev.db` locally. gunicorn via `Procfile`
(`web: gunicorn "app:create_app()"`). App factory in `app/__init__.py`. No Alembic. No LLM/API
calls anywhere — the simulation is deterministic and self-contained. Keep it that way.

## Commands

```bash
# local dev server
FLASK_APP="app:create_app" ./venv/Scripts/flask run

# tests (368 of them; run before saying anything works)
./venv/Scripts/python -m pytest tests/ -q

# migrations + one-time data repairs
FLASK_APP="app:create_app" ./venv/Scripts/flask init-db

# drive the bot firms through a simulated game
FLASK_APP="app:create_app" ./venv/Scripts/flask simulate-bots
```

Windows notes: MSYS `mv` fails with "Device or resource busy" on this tree — use PowerShell
`Move-Item`. `TaskStop` doesn't kill a running flask; find it with
`netstat -ano | grep ":5000" | grep LISTENING` then `taskkill //F //PID <pid>`. Without `--debug`,
**template edits need a server restart** — a stale-looking page is usually this, not a bug.

## Architecture

**Models** (`app/models.py`): `World` (one class period; holds current round + phase) → `Firm`
(a team, or a bot) → `RoundDecision` (what a firm submitted this round) → `RoundResult` and
`SegmentRoundResult` (what the engine produced).

**Round processing is a batch.** Nothing resolves when a student submits. The teacher advances the
round, which runs `app/blueprints/teacher.py::_process_current_round` → `app/engine.py::process_round`.
Firms that didn't submit get a synthesized decision. One call resolves the whole world at once.

**`app/engine.py` is a pure function library** — no DB, no Flask imports, deterministic. Economic
changes belong here and are testable in isolation. Don't reach into the DB from it.

**Demand model:** willingness-to-pay. Each of five segments has a WTP distribution; a firm's
price and quality determine which share of each segment can and will buy it, then supply is
allocated. Demand is pulled by segment fit, not pushed by a fixed share. `wtp_threshold_r()`
is the core; Wealthy has a hard $250 cutoff.

**`flask init-db` is self-healing and load-bearing.** No Alembic, so it uses `sa.inspect()` column
checks plus `ALTER TABLE ADD COLUMN`, plus one-time data rewrites. Render runs it as a Pre-Deploy
Command configured **in the Render dashboard UI only** — there is no `render.yaml`, so you cannot
verify it from the repo. Any deploy that adds a column *must* add the corresponding step here:
SQLAlchemy selects every column on every `Firm` query, so a missed migration 500s the entire site
rather than degrading.

## Gotchas

- **JSON columns:** never mutate-then-reassign the same object (e.g. `d["k"]=v; row.col=d`).
  SQLAlchemy won't mark a plain JSON column dirty. Always construct a new object.
- **Passwords are one-way hashed and stay that way.** Reset generates a new password, shows it once,
  and the plaintext lives only in the teacher's Flask session — never in the DB.
- **Two roles share one signed cookie.** Session keys are scoped per role (`FIRM_SESSION_KEYS`,
  `TEACHER_SESSION_KEYS`, `SANDBOX_SESSION_KEYS` in `app/auth.py`); logging out one role must not call `session.clear()`.
  A teacher and a student may be signed in at once — the firm dashboard shows a warning banner
  with one-click teacher sign-out, because on a shared classroom device silent teacher access is
  a real security problem.
- **Quality is tier-bound.** Quality Level comes from `Firm.rd_spend_by_track`
  (`{tier: R&D}`), not `cumulative_rd_spend` (now just the all-tier total). Read it
  through `quality_levels_by_track()` / `rd_spend_in_track()`. The column is
  `JSON(none_as_null=True)` so init-db's `IS NULL` backfill can find unbuilt rows.
- `IS_LOCAL_DEV = "DATABASE_URL" not in os.environ`, forced False under TESTING. Local dev skips
  the teacher login gate; production does not.

## Visual identity

Established look — match it rather than introducing a parallel style. All
tokens live in `app/static/css/style.css`; read them there rather than
hardcoding a colour.

- **Dark, slate-teal ground.** `--panel` #2E3A40 panels on a darker base.
- **Muted teal accent** `--accent-primary` #6FA8A0 for primary actions and
  highlights. Spend boldness here and keep everything around it quiet.
- **Semantic colour is separate from the accent**: `--accent-positive` #39FF14
  for cash-up/wins, `--accent-danger` #FF3355 for negative cash and loans.
  These are meant to pop; don't reuse them decoratively.
- **Pixel display font** `--font-display` ("Press Start 2P") for titles, hero
  numbers and button labels **only** — never for body text or tables, which
  stay in the readable sans. This restriction is deliberate; the font is
  unreadable at paragraph length.
- **Square corners and chunky offset shadows** (`box-shadow: 4px 4px 0`), not
  rounded cards with soft blur. Buttons, banners and pickers all share this.
- **Per-segment and per-tier accents** (`SEGMENT_ACCENTS`, `TIER_ACCENTS` in
  `app/market_data.py`) come from one palette — extend it rather than picking
  new hues.
- Controls that are a choice look like buttons (see `.celeb-choice`,
  `.avatar-choice`): radios styled as buttons, so the browser enforces the
  single selection and it works without JS.

**Sizing is per-view, not global.** The app is read on student devices at
normal screen distance and sizes in rem/px. The ONE exception is the projector
view (`present.html`), which is the only thing shown on a wall and the only
place that uses `vw` units — every `.present-*` rule scales to the viewport so
standings are legible across a room. Don't apply wall-scale sizing anywhere
else, and don't shrink the projector view to match the rest. The same board
also shows on student screens (sandbox, and classroom when the teacher enables
`show_standings_to_students`), so its controls use `clamp()` with a rem floor —
see `.present-exit`.

**What this replaced, and don't drift back to it.** The original look was a
conventional dashboard — generic semantic tokens (`--bg`, `--surface`,
`--primary`, `--danger`), rounded corners, soft shadows, no display face.
Robert didn't like it, and the Sept 2026 overhaul replaced it wholesale: hence
`--radius: 0` set site-wide in one place, and the hard offset shadows. The old
token names survive only as **aliases** to the new ones so existing inline
styles pick up the theme automatically — they are not a second palette, and
nothing new should be written against them.

If a change starts reintroducing rounded cards, blurred shadows, or a default
sans for headings, that is drift back toward the rejected design, not a neutral
choice.

## Project documentation

Not auto-loaded. Read the relevant one before substantial work:

- `docs/PROJECT_INSTRUCTIONS.md` — architecture, conventions, what not to
  change casually, testing expectations, game-design constraints.
- `docs/LESSONS_LEARNED.md` — what has broken here and the rule it produced.
  Each entry cites a commit or test so it can be verified.
- `docs/MASTER_VARIABLE_INDEX.md` — every meaningful variable: where defined,
  who modifies it, what it touches, and whether it moves game balance.
- `docs/master-variable-table.md` — the locked economic design and the numbers
  themselves. Source of truth for balance.

## Terminology

The app was reskinned from a basketball-shoe theme. **Display text is headphones now — internal
identifiers were deliberately left alone.** `track` / `TRACKS` / `track_unit_cost()` /
`Firm.last_track` still exist as identifiers for what the UI calls **Tier** (Entry/Mid/Premium),
and `SEGMENTS` still holds old keys (`"Low Income"`, `"NBA Fans"`, `"Casual/Fashion"`) mapped
through `SEGMENT_DISPLAY_NAMES` / `segment_label()` in `app/constants.py` to Budget Shoppers /
Music Enthusiasts / Casual/Style-Conscious.

If you find basketball/shoe/sneaker wording **in user-visible text**, that's a leftover — fix it.
If it's in an identifier or a stored string value, **flag it to Robert instead of renaming**;
those values are persisted in the DB and changing them needs a migration step.
