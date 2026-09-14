# business-sim — Project Instructions

Durable instructions for working on this project. Read this before changing
anything. For *why* past decisions were made the hard way, see
[LESSONS_LEARNED.md](LESSONS_LEARNED.md). For what individual numbers mean and
what they touch, see [MASTER_VARIABLE_INDEX.md](MASTER_VARIABLE_INDEX.md). For
the locked economic design itself, see
[master-variable-table.md](master-variable-table.md).

---

## 1. What this is

A classroom economics simulator. Student teams run competing headphone
companies over ten rounds; a teacher controls pacing from a Teacher Dashboard.
Users are high-school students on school devices — Chromebooks and iPads,
small screens, shared between classes. One view, the projector board
(`present.html`), is sized for a wall; everything else is read at arm's length.
The same board also appears on student screens — always in sandbox, and in a
classroom game when the teacher turns it on — so it must stay usable there too.

- Live: https://business-sim-74hd.onrender.com
- Repo: `rallen-762/business-sim`
- Audience: teenagers under time pressure. **Clarity beats cleverness in every
  UI decision.** A control nobody understands is worse than no control.

### Three modes, one simulator

| Mode | `World.mode` | Who plays | Pacing |
|---|---|---|---|
| Classroom | `classroom` | Teams, teacher-run | Teacher processes each round in two deliberate steps |
| Sandbox | `sandbox` | One human vs bots | Player runs their own rounds, no waiting |
| Balance run | `bots_only` | Nobody — all bots | Plays to completion in one request |

All three use the **same** engine, bots, ladders and CSV export. Sandbox is a
mode, not a second build. If you find economics logic in
`app/blueprints/sandbox.py`, that is a bug.

---

## 2. Architecture

```
app/
  engine.py        PURE economic model. No Flask, no DB. The heart.
  constants.py     Every locked number. Section 12 lives here.
  bots.py          PURE bot decision logic. No Flask, no DB.
  models.py        SQLAlchemy schema
  auth.py          Session roles (firm / teacher / sandbox), scoped separately
  market_data.py   Shared query + presentation helpers
  scouting_report.py, csv_export.py, avatars.py, bot_sim.py
  blueprints/      auth, firm, market, teacher, sandbox
```

### The rule that matters most

`engine.py` and `bots.py` are **pure**. They take plain values and return plain
values — no database, no request context, no Flask imports. That is what makes
the riskiest part of the app (the round math) testable without a server, and
what lets a 250,000-game balance study run in seconds.

**Do not import Flask or the DB into either file.** If a change seems to need
it, the change belongs in a blueprint instead.

### How a round is processed

Nothing resolves when a student submits. A round is a **batch**:

```
teacher.py::_process_current_round(world)
  ├─ _capture_snapshot()            <- before ANY mutation, for Undo
  ├─ gather submitted decisions
  ├─ bots decide (bots.decide)      <- only from their OWN history
  ├─ synthesize no-show decisions
  ├─ engine.process_round(states, decisions)   <- the entire market, at once
  ├─ write RoundResult + SegmentRoundResult rows
  └─ update Firm state from results
```

`_process_current_round` and `_open_next_round` are plain functions with no
web-request coupling, which is why sandbox can call them back-to-back and
`bot_sim.py` can drive them headlessly. The teacher's two-step pacing lives in
the `advance_round` **route**, not in these functions. Keep it that way.

### The demand model

Willingness-to-pay. Each of five segments has a WTP distribution per tier. A
firm's price decides **who can afford it**; quality, tier fit, advertising and
endorsement decide **who among those buyers picks it**. Demand is pulled by
fit, not pushed by a fixed share. Unmet demand is lost, never redistributed.

---

## 3. Things you must NOT change casually

**Section 12 constants** (`app/constants.py`) — WTP ceilings, quality weights,
tier preferences, endorsement multipliers, the ladders. These are the locked
economic design. Changing one rebalances the whole game. Simulate before and
after (`app/bot_sim.py`, or a harness driving `engine.process_round`), and
update `master-variable-table.md` in the same change.

**`ROUNDS_PER_WORLD`** — global, shared by live classroom games. Per-world
length is `World.rounds`. Never repurpose the constant for one mode's needs.
See LESSONS_LEARNED #3.

**Password hashing** — one-way, always. A reset generates a new password and
shows it once; the plaintext lives only in the teacher's Flask session, never
in the database.

**Session role scoping** — `FIRM_SESSION_KEYS`, `TEACHER_SESSION_KEYS`,
`SANDBOX_SESSION_KEYS` in `app/auth.py`. Logging out of one role must never
call `session.clear()`. Roles coexist deliberately: a teacher can play a
sandbox game and still reach their dashboard. The firm dashboard shows an
unmissable banner when a teacher session is also present, because on a shared
classroom device silent teacher access is a real problem.

**Internal identifiers that carry old names** — `track` / `TRACKS` /
`track_unit_cost()` / `Firm.last_track` are the UI's **Tier**, and `SEGMENTS`
still holds keys like `"Low Income"`, `"NBA Fans"`, `"Casual/Fashion"` mapped
through `segment_label()`. These are persisted in the database and in JSON
keys inside `round_results.units_sold_by_segment`. Renaming a value means a
data migration over live game history. **Fix user-visible text freely; flag an
identifier or stored value rather than renaming it.**

**No LLM or external API calls anywhere.** The simulation is deterministic and
self-contained, including the bots and the scouting report. Keep it that way.

---

## 4. Database and deployment

Postgres in production, SQLite `dev.db` locally. gunicorn via `Procfile`.
**No Alembic.**

### `flask init-db` is load-bearing

Migrations are hand-written checks in `app/__init__.py`: `db.create_all()` for
new tables, then `sa.inspect()` + `ALTER TABLE ADD COLUMN` for new columns.
Render runs it as a Pre-Deploy Command configured **in the dashboard UI only** —
there is no `render.yaml`, so you cannot verify it from the repo.

**Any deploy that adds a column MUST add its step here.** SQLAlchemy SELECTs
every mapped column on every query, so a missed migration 500s the whole site
rather than degrading. Verify against the real `dev.db` before pushing, not
just against a fresh test database.

Prefer **additive** migrations. When a column's meaning changes, add a new
column and leave the old rows resolvable (see the celebrity/legacy-multiplier
pattern) rather than backfilling — backfilling rewrites what past rounds were
actually scored with.

### Deployment assumptions

- Push to `main` → Render auto-deploys, roughly 45–90 seconds.
- Environment variables, all dashboard-only: `TEACHER_PASSWORD`,
  `SANDBOX_PASSWORD`, `SECRET_KEY`, `DATABASE_URL`.
- `IS_LOCAL_DEV = "DATABASE_URL" not in os.environ`, forced False under
  TESTING. Local dev skips the teacher password gate; production does not.
- **Never commit or push unless asked.** And when asked to fix something,
  remember that committing is not deploying — see LESSONS_LEARNED #5.

---

## 5. Testing expectations

`./venv/Scripts/python -m pytest tests/ -q` — currently 368 tests, all passing.

Write tests **alongside** the feature, not afterwards. The bar is not coverage;
it is: *what would make this silently wrong, and does a test catch it?*

Patterns worth copying:

- **Pin the invariant, not the wording.** Assert an `href` or a field name,
  not the visible label — labels are free to change.
- **Guard the thing that can drift.** `test_snapshot_fields_cover_everything_processing_mutates`
  diffs every Firm column across a real round and fails if processing starts
  writing a field Undo does not restore. That is the only way that feature can
  corrupt a game rather than visibly break.
- **Test the migration, including the no-op case.** Build the pre-change table
  shape and assert `init-db` both adds the column and leaves old rows alone.
- **Test the security boundary in both directions.** The teacher password must
  not open the sandbox, and the sandbox password must not reach the Teacher
  Dashboard.

For economics changes, a test is not enough — run a simulation across
**randomized opponent fields**. A single fixed field produces confident,
wrong answers (LESSONS_LEARNED #6).

---

## 6. UI / UX conventions

- **Make the invisible visible.** Several real mechanics sat unused purely
  because nothing surfaced them — selling out, plant capacity, endorsement.
  If a decision has consequences the player cannot see, that is a bug in the
  teaching, not just the UI.
- **Say the mechanism, not the answer**, where discovery is the point. The
  endorsement hint explains what an endorsement does against your own price
  and never says which endorser suits you.
- **Ladder inputs are dropdowns.** R&D and Advertising offer only amounts that
  land exactly on a level — no free-entry box that can buy a fraction of a
  level and return nothing. Price stays free entry; it is the real decision.
- **Never ship a visible field that resembles a credential** unless it truly
  is one (LESSONS_LEARNED #1).
- The projector view (`present.html`) is deliberately inert — it changes no
  state. Its only control is the way out: a solid, always-coloured Exit button
  pinned top-right on every board (bottom-centre covered the standings bars). It was once a faint link with a one-time
  pulse and was reported invisible; don't dim it again.
- Every page is theme-aware via CSS tokens, and dynamic pages send
  `Cache-Control: no-store` — classroom devices are shared (LESSONS_LEARNED #4).

---

## 7. Game-design constraints

- **Ten rounds.** Short enough that a mechanic which only pays off after round
  eight is effectively dead content.
- **Price is the dominant lever** and should stay that way — it swings a result
  by roughly $10M within a tier, more than every other decision combined.
- **Each tier must stay viable at its own best price.** Current spread is ~19%.
- **Hidden multipliers stay hidden.** Section 12 values are discovered by
  playing, never printed in the UI.
- **Every option needs a niche.** Before adding a choice, simulate whether it
  is ever the best answer. Before "fixing" one that looks weak, check whether
  it is simply strong somewhere you did not test.
- **Bots see only their own history** — never another firm's decisions,
  results, or the underlying formulas. This is locked.

---

## 8. Local development

```bash
FLASK_APP="app:create_app" ./venv/Scripts/flask run --port 5000 --debug
./venv/Scripts/python -m pytest tests/ -q
FLASK_APP="app:create_app" ./venv/Scripts/flask init-db
FLASK_APP="app:create_app" ./venv/Scripts/flask simulate-bots
```

Windows notes:

- MSYS `mv` fails with "Device or resource busy" on this tree — use PowerShell
  `Move-Item`.
- Without `--debug`, template edits need a server restart. A stale-looking page
  is usually this.
- **Check for more than one Flask process on the port** before debugging
  anything that looks like stale behaviour: `netstat -ano | grep ":5000" | grep LISTENING`.
  The `--debug` reloader runs a parent and a child; killing only the parent
  leaves the child serving old code while a new server binds alongside it.
  This has already masqueraded as a wrong-password bug once.

---

## Documentation Maintenance

Four documents, four jobs. Keep them disjoint — put a fact in one place and
reference it from the others.

| File | What it holds | Update it when |
|---|---|---|
| **PROJECT_INSTRUCTIONS.md** (this file) | How to work on this project: architecture, conventions, constraints, what not to touch | Architecture changes, a new convention is set, a new "don't do this" rule is established, deployment or testing expectations change |
| **LESSONS_LEARNED.md** | Institutional memory: what broke, why, and the rule it produced | A real bug is fixed whose cause generalizes. **Only with evidence** — code, git history, or a reproduction. Never write a lesson from a hunch or an unverified report |
| **MASTER_VARIABLE_INDEX.md** | Every meaningful variable: type, where defined/modified/used, range, relationships, balance impact | A variable is added, removed, or changes meaning; a new call site changes who modifies it |
| **master-variable-table.md** | The locked *economic design* — the numbers themselves and their rationale | A Section 12 constant, ladder, cost or rule changes. This is the design source of truth; the index above points at the code |

Rules of thumb:

- A **number** and its design rationale → `master-variable-table.md`.
  Where that number lives in code and what it touches → `MASTER_VARIABLE_INDEX.md`.
- A **rule for behaviour** → this file. The **incident** that taught you the
  rule → `LESSONS_LEARNED.md`, with the rule stated once and cross-referenced.
- If a lesson would apply to a project that is not this simulator, it belongs
  in `../CLAUDE_MASTER_LESSONS.md` instead.
- When code and a document disagree, **the code is what runs**. Correct the
  document; do not change code to match a stale doc.
