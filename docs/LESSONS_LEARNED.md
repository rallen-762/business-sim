# business-sim — Lessons Learned

Institutional memory. Every entry below is established from the code, the git
history, or a test that exists in this repo — the evidence is cited so a future
reader can check it rather than take it on faith.

Lessons that would generalise beyond this simulator live in
`../CLAUDE_MASTER_LESSONS.md` instead. Conventions that came out of these
lessons are stated once in [PROJECT_INSTRUCTIONS.md](PROJECT_INSTRUCTIONS.md)
and referenced here.

---

## 1. A visible, non-functional "password" field got the site flagged as phishing

*Evidence: commit `613ebae`; the removed markup in `app/templates/sandbox_home.html`.*

### Problem
Chrome showed a full "dangerous site" interstitial on `/sandbox/new`. Students
could not reach the sandbox at all without clicking through a security warning.

### Cause
The sandbox setup form carried a **visible** `<input type="text" name="password"
value="sandbox">` — prefilled, named `password`, posting to a freshly created
URL on a shared `*.onrender.com` domain. That combination reads as a credential
harvesting form to Safe Browsing heuristics, which are aggressive about free
shared-hosting subdomains whose reputation is shaped by every other tenant.

The site itself was clean: same-origin content only, no external scripts,
nothing injected. It was a false positive — triggered by shape, not content.

The field was also doing nothing. The sandbox password already authenticated at
the door and Resume signed the player in directly, so no code path ever checked
it.

### Solution
Removed the field. The player firm now gets a random hash it never sees, so the
slot is still not claimable. Two tests pin it: the setup form collects no
password, and the firm still receives a real credential rather than a blank or
shared one. A Search Console verification tag was added (`8bb5b7c`) so a review
could be requested.

### Rule for Future Changes
**Never ship a visible input that resembles a credential field unless it truly
is one**, and delete any input no code path reads. On shared hosting, the
*shape* of a form is enough to get flagged — content and intent do not protect
you. A real password field on a real login page is fine; a decorative one is a
liability. If a flag appears, fix the shape before requesting review, because a
re-crawl of the unchanged page just confirms the flag.

---

## 2. "Where did my feature go?" — it never went anywhere

*Evidence: `git show 8a687a1 -- app/templates/sandbox_home.html` removes only the
Bots-Only card. Checking the whole path rather than one string — the
`@bp.route("/new")` handler, the form's `sandbox.new_game` action, the
"New Single-Player Game" card, and the sandbox link on `/login` — all four are
present at every sandbox-era commit (`55112b4`, `613ebae`, `8bb5b7c`, `8a687a1`,
`75332f4`, `fe0dc02`, `6428cb1`, `e2812a7`, `7a3090b`, `319ff93`, `62bf9a1`).
Re-runnable: loop those commits and `grep -c` each of the four.*

### Problem
After the bots-only Balance Run was relocated to the Teacher Dashboard,
single-player sandbox mode was reported as having disappeared from the student
side. A fix was requested to "restore" it.

### Cause
**Nothing had been removed.** The diff touched only the Bots-Only card. The
single-player card was present in every commit before and after. The report was
a stale page — the app sent no `Cache-Control` header at all at the time, so
browsers were free to cache HTML heuristically (see #4, which was found and
fixed for exactly this reason).

Checking first cost one command. Had the report been taken at face value, the
"fix" would have added a second copy of a card that already existed.

### Solution
Verified the live pages served the card before changing anything, reported that
it was present, and fixed the real cause — the missing cache headers — instead.
The investigation also surfaced a genuine problem nobody had reported: the
sandbox page listed *every* sandbox world with a Resume button that signed you
into it, so on a shared device one student could enter another's game. That list
was removed (`62bf9a1`).

### Rule for Future Changes
**Confirm a regression exists before fixing it.** Check the git diff and the
served page first. A bug report describes a *symptom*; the cause is frequently
somewhere else entirely — caching, an unpushed commit, a stale process. Fixing
the reported thing without confirming it can add duplicate code and leave the
actual fault in place.

---

## 3. A global constant was nearly repurposed for one mode's needs

*Evidence: `ROUNDS_PER_WORLD = 10` in `app/constants.py`, unchanged since the
initial commit `10a92de`; `World.rounds` added in `55112b4`; call sites now read
`world.rounds or ROUNDS_PER_WORLD`.*

### Problem
Sandbox mode needed a configurable game length. The round-completion rule
(`current_round >= ROUNDS_PER_WORLD → status "complete"`) read a single global
constant shared by every world, including live classroom games mid-play.

### Cause
The constant was doing two different jobs: *the default length of a game*, and
*the length of this particular game*. Only the first is global. Changing it to
serve sandbox would have silently altered the finish line for Period 1 and
Period 2 while they were in progress.

### Solution
Added `World.rounds`, defaulting to 10, and moved the completion rule and the UI
onto it. `ROUNDS_PER_WORLD` remains the default and is otherwise untouched. The
migration backfills every existing world to 10, and a test asserts a pre-change
world still reports 10 rounds with its progress intact.

The field was added even though nothing exposes a way to change it yet —
opening it up later is now a UI change rather than a re-plumbing.

### Rule for Future Changes
**When a global constant needs to vary per entity, add a field — never
repurpose the constant.** Ask specifically: *is this value the default, or is it
this instance's value?* On a system with live data, that distinction is the
difference between a feature and a silent corruption. Backfill existing rows to
preserve current behaviour exactly, and test that they are unchanged.

---

## 4. Logged-in pages had no cache headers, so devices served stale copies

*Evidence: commit `6428cb1`; the `after_request` hook in `app/__init__.py`.*

### Problem
An iPad kept rendering an old Market Dashboard after the fix had already
deployed, while a desktop showed the new one. It looked exactly like a failed
deploy.

### Cause
The app sent **no `Cache-Control` header at all** on dynamic responses — only
`Vary: Cookie`. With no explicit directive, browsers may cache HTML
heuristically, and iOS Safari does so aggressively.

The staleness was the visible symptom. The real exposure was worse: every page
is scoped to whoever is signed in, so on a shared classroom iPad or Chromebook a
cached copy can be re-displayed to the **next** person — after a logout, or via
the back button — showing them another team's dashboard or the teacher's.
`Vary: Cookie` does not reliably prevent that.

### Solution
An `after_request` hook sets `no-store, max-age=0, must-revalidate` on
everything except static assets, which stay cacheable because they are identical
for every user and re-fetching them on classroom wifi is a real cost. Tests
assert both halves.

### Rule for Future Changes
**Any app serving per-user pages must send `no-store` explicitly**, especially
on shared devices. And when a deployed fix "does not work" on one device but
does on another, **check `Cache-Control` before assuming the deploy failed** —
the two are indistinguishable from the user's side.

---

## 5. A committed fix is not a deployed fix

*Evidence: `e2812a7` "Make R&D and Advertising spend dropdown-only" was committed
at 19:44 and sat unpushed; the same issue was then reported a second time. The
sandbox-list removal repeated the pattern — reported as still present while the
change sat uncommitted in the working tree.*

### Problem
The same bug was reported twice. The code was correct both times. Production had
simply never received it.

### Cause
Work stopped at `git commit`. Because this project deploys by pushing to `main`,
a commit changes nothing a user can see. The local server, meanwhile, *was*
running the fix — so every local check passed and looked like proof.

### Solution
Pushed, then verified against the live site rather than the local one.

### Rule for Future Changes
**When someone reports that a fix did not work, check what production is
actually running before re-diagnosing.** `git log origin/main..HEAD` answers it
in one command. Verifying a fix on localhost proves the code is right, not that
anyone else can see it — and those are different claims.

---

## 6. A balance finding from one opponent field was confidently wrong

*Evidence: the endorsement multipliers in `app/constants.py` and the balance
note recorded in `master-variable-table.md` §12.3.*

### Problem
A strategy study produced clear conclusions — celebrity endorsement is harmful,
R&D hurts Entry tier, one build dominates — that reversed when re-run.

### Cause
Each conclusion came from a **single fixed opponent field**, or a single price
point. A strategy that beats one particular lineup looks dominant until it meets
another. Averaging a bimodal effect into one number was worse still: endorsement
measured as "mildly positive, +7.5%" at one price, while actually swinging from
−$2.5M to +$1.8M depending almost entirely on price. The average described
nothing that ever happened.

### Solution
Re-ran every claim across 150–300 **randomized** opponent fields, and reported
effects as a function of the variable that drives them rather than as a single
figure. Conclusions that survived were kept; the ones that reversed were
retracted explicitly, including in the published report.

### Rule for Future Changes
**Never draw a balance conclusion from one configuration.** Randomize the
opponent field and sweep the variable that plausibly interacts. If an effect is
bimodal, report the split — an average across it is not a weaker answer, it is a
wrong one. Before "fixing" an option that looks weak, check whether it is simply
strong in a case you did not test: in this game, every endorsement option
including *none* turned out to be the best choice somewhere.

---

## 7. A SQLAlchemy JSON column ignored an in-place mutation

*Evidence: the comment and rewrite in `app/__init__.py` (~line 281); test
`test_init_db_rewrites_old_track_and_segment_values` in `tests/test_cli.py`.*

### Problem
A one-time data migration rewriting segment keys inside
`round_results.units_sold_by_segment` appeared to succeed and changed nothing.

### Cause
The code mutated the dict in place and reassigned the same object. SQLAlchemy
does not detect that as a change on a plain JSON column — there is no
`MutableDict` wrapper — so no UPDATE was ever emitted.

### Solution
Build a genuinely new dict. A CLI test now proves the rewrite lands.

### Rule for Future Changes
**Never mutate-then-reassign the same object into a SQLAlchemy JSON column.**
Always construct a new object. And **assert that a data migration actually
changed the data** — a migration that silently no-ops is worse than one that
fails loudly, because it looks finished.

---

## 8. Deleting assets orphaned the rows that referenced them

*Evidence: the icon self-heal in `app/__init__.py`; test
`test_init_db_repairs_icons_pointing_at_deleted_files` in `tests/test_cli.py`.*

### Problem
An asset-pack swap replaced the avatar set and deleted the old files. Every firm
registered beforehand still named a deleted filename, rendering as a broken
image — not as "no icon".

### Cause
The filenames were persisted per firm. Deleting the files did not update the
rows pointing at them, and there is no foreign key to catch it.

### Solution
`init-db` repairs any icon not in the current choice list, deterministically
from `firm.id` so it is stable across re-runs, and leaves genuinely unclaimed
slots with no icon rather than inventing one.

### Rule for Future Changes
**Deleting a file that a database column names is a migration, not a cleanup.**
Before removing assets, find the rows referencing them and plan the repair in
the same change. Prefer a validated fallback at render time or a self-heal at
migration time over trusting stored filenames.

---

## 9. The form and the engine disagreed about capacity

*Evidence: commit `20aa4ba`; `Firm.effective_capacity` in `app/models.py`.*

### Problem
A team that paid $100,000 to expand its plant could not use the capacity it had
bought. Bots, in the same game, could use theirs.

### Cause
Capacity was computed by hand at three call sites. The engine granted
`plant_capacity + pending_capacity_increase`; bots were told the same; but the
student dashboard used `plant_capacity` alone **and capped the production input
at it**. A matured expansion was invisible to the only party who had paid for
it.

### Solution
One `Firm.effective_capacity` property, used everywhere. A regression test
asserts the rendered JS cap matches what the engine allows.

### Rule for Future Changes
**A value derived in more than one place will eventually disagree with
itself.** Put it behind one property or function the moment there is a second
call site — especially when one of those sites is a UI constraint and another is
a server-side rule, because then the UI can silently forbid something the engine
would have allowed.

---

## 10. A mechanic nobody could see was a mechanic nobody used

*Evidence: `units_demanded_total` / `units_lost_to_capacity` on `RoundResult`;
the sold-out callout in `firm_dashboard.html`; commit `20aa4ba`.*

### Problem
Plant Investment was effectively dead content. Measured across ten simulated
games, 42% of firms were pinned at their capacity ceiling by round 3 and 75% by
round 8, sitting on far more cash than an expansion costs — and still nobody
expanded.

### Cause
Selling out was **invisible**. The engine computed the demand that capacity
destroyed and then discarded it. A firm that could have sold 63,000 units saw
"Units Sold: 45,000" and a healthy profit, with nothing indicating a ceiling had
been hit. Meanwhile R&D and Advertising gave loud immediate feedback, so that is
where the money went.

### Solution
Capture the lost demand and surface it. Crucially, shortfall is attributed to
capacity **only when capacity actually bound** — a cash-limited team that chose a
short run gets no banner, because a bigger factory would not have sold it one
extra unit.

### Rule for Future Changes
**If a decision has consequences the player cannot see, the mechanic does not
exist.** Before adding a new option, ask what feedback tells someone it
mattered. And when explaining an outcome, **attribute it to the constraint that
actually bound** — plausible-but-wrong advice teaches the wrong lesson more
effectively than silence.

---

## 11. "Optional" did not say whether it starts on or off

*Evidence: commit `93e688c` shipped `World.show_standings_to_students` defaulting
to off; commit `eeaf216` flipped it to on. The one-time flip is the
`SET DEFAULT true` step in `init-db` (`app/__init__.py`); test
`test_standings_are_on_by_default_for_a_new_classroom_game`.*

### Problem
The request was to show the standings board on student screens after a round,
"optional/teacher-controlled". It shipped **off** by default. Robert meant
**on**, so students saw nothing new until a second deploy.

### Cause
"Optional" only says a teacher *can* switch it. It does not say which way it
starts — and most teachers never touch a setting, so the starting position is
what nearly every class actually gets. The default was guessed instead of asked.

### Solution
Changed the default to on. Existing games needed flipping too, but a plain
"set every game to on" would run on every deploy and undo any teacher who later
turned it off. So the flip runs **once**: the column's own database default is
the marker. It changes from false to true in the same step, and once it reads
true the step never runs again.

### Rule for Future Changes
**When a feature is described as optional, ask whether it starts on or off
before building it.** And when changing a default on live data, flip existing
rows once, with a marker — never on every run, or deliberate choices get
silently reverted.

---

## 12. A JSON column saved `None` as the text `null`, so the backfill found nothing

*Evidence: `JSON(none_as_null=True)` and its comment on `Firm.rd_spend_by_track`
in `app/models.py`; test
`test_init_db_rebuilds_per_tier_rd_from_processed_rounds_only` in
`tests/test_tier_quality_and_standings.py`, which failed before the fix.*

### Problem
Making quality tier-bound added `firms.rd_spend_by_track`, filled once by
`init-db` for every firm whose value was still empty. The test set a firm's value
to `None`, ran `init-db`, and nothing was rebuilt.

### Cause
SQLAlchemy's JSON type writes Python `None` as the JSON value `null` — a real
stored value — not as an empty database cell. The backfill looked for empty
cells (`IS NULL`), so a row the code had "cleared" was invisible to it. In
production the column is added empty, so the deploy itself would have worked;
anything that ever wrote `None` later would not have been.

### Solution
`JSON(none_as_null=True)`, so `None` always means an empty cell and the backfill
and the code agree on what "not built yet" looks like.

### Rule for Future Changes
**Before a backfill keys on "empty", check what the code actually stores for
empty.** A JSON `null`, `""`, `0` and a real empty cell all look empty to a
person and are different to a query. This is #7's failure again — a migration
that silently does nothing — reached by a different route; test that it changed
rows, not just that it ran.

---

## 13. The local server loaded the new model before the database had the column

*Evidence: observed 13 Sept 2026 while building `93e688c`. The local Teacher
Dashboard returned 500 with `no such column: worlds.show_standings_to_students`;
running `flask init-db` fixed it with no code change. The production form of the
same failure is the `init-db` warning in `CLAUDE.md`.*

### Problem
The local Teacher Dashboard stopped loading partway through a feature, before
anything had been committed or deployed.

### Cause
The dev server runs with `--debug`, which reloads the moment a Python file is
saved. It picked up a model with two new columns while `dev.db` still had the
old shape. SQLAlchemy selects every column on every query, so every page that
read a world failed. The error points at a missing column, which looks like a
bug in the new code rather than a migration that simply has not run.

### Solution
Backed up `dev.db`, ran `flask init-db`, and every page loaded again.

### Rule for Future Changes
**After adding a model column, run `flask init-db` locally straight away** — the
reloader will not wait. A sudden site-wide 500 reading `no such column` right
after editing `models.py` means "migration not run", not "code broken". It is the
same failure `CLAUDE.md` warns about on Render, arriving on localhost first.

---

## 14. "The exit button doesn't show" was a design problem, not a missing button

*Evidence: the `.present-exit` history comment in `app/static/css/style.css`;
commits `93e688c` (persistent button) and `eaaf049` (moved top-right); test
`test_projector_exit_is_a_persistent_button_on_every_classroom_board`.*

### Problem
On the sandbox results board, the exit button was reported as not appearing and
not flashing.

### Cause
Checked before fixing (#2): the button was in the served HTML with the right
class, above every layer, and the CSS reaching browsers was current. Nothing was
broken. It was **hard to see**: its one attention pulse played in the same second
the standings rows slid in, it rested at 75% opacity, and it was sized in screen
widths — about 8px tall on an iPad in portrait.

### Solution
A solid, always-coloured button with no pulse to miss, sized with a
minimum so it stays readable on small screens. A first placement at bottom-centre
covered the standings bars, so it moved to the top right, with header space
reserved for it.

### Rule for Future Changes
**"It isn't there" can mean "nobody can see it".** Once the markup and CSS check
out, look at timing (what else moves at that moment), resting contrast, and the
real size on the smallest device. Anything sized in `vw` must be checked on a
student-sized screen, not only on a wall.

---

## 15. Removing a button left its route able to play a round for the player

*Evidence: commit `eaaf049`; the submission check in `sandbox.play_round`; test
`test_a_stale_run_round_post_does_not_play_a_round_for_the_player` in
`tests/test_sandbox.py`.*

### Problem
Sandbox rounds were changed to run the moment a player submits, so the separate
"Run Round" button was removed.

### Cause
The button's route was still there. Its old job was "process whatever round is
open", which made sense when it could only be pressed after submitting. Once
submitting already ran the round, a repeated click, back-button re-send or
bookmarked request would process the **next** round too — with an auto-decision
the player never made.

### Solution
The route now acts only if the current round has a real submission waiting,
which covers games left mid-round before the change and nothing else. A test
posts to it after an automatic round and asserts nothing further is processed.

### Rule for Future Changes
**When a flow changes, re-check what every old entry point would now do**, not
only whether anything still links to it. A route that was safe because of the
page around it is not safe once that page is gone.
