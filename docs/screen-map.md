# Business Simulation — Screen Map (V1, in progress)

Companion to master-variable-table.md. This document maps every screen/interface, not the underlying economic formulas.


## 1. Login Screen (Universal Entry Point)

Single screen for everyone — students and teacher — routing differs by which fields are filled.

Fields:

- Game Code — identifies the specific class-period / 10-round session (one code per "world")
- Team Password — student entry path
- Teacher Code — separate input field on the same screen, routes to Teacher/Admin view instead of a firm

First-time student flow:

New team logging in for the first time is forced to:
- Create a Company Name
- Create a Password

No pre-assigned credentials — teams self-register on first login within a valid Game Code.

Teacher-only Team Lookup Screen (separate from login):

- Organized by Game Session / class period
- Lists all teams within that session
- Teacher can view team info
- Teacher can manually override/reset a team's password


## 2. Firm Dashboard (Student View)

Two-column layout: state on the right, decisions on the left. This is the screen a team lives in each round.

RIGHT SIDE — Persistent Stats / Current State (read-only display)

- Cash on hand
- Plant Capacity (current units)
- Quality Level (numeric + descriptor label)
- Track (Budget / Standard / Premium)
- Celebrity Endorsement status (on/off)
- Loan Outstanding (if any)
- Loan Interest Expense (this round) — permanent line item shown from Round 1 onward, displays $0 when no loan is outstanding (per master doc Section 8)

LEFT SIDE — This Round's Decision Inputs (editable, per Section 2 of master doc)

- Price
- Production Quantity
- Advertising Spend
- R&D Spend
- Track selection
- Celebrity Endorsement toggle
- Plant Investment amount

Live Cost Calculator (hard-block, per master doc Section 11):

A running total displayed just above the Lock In button, recalculating live as any input changes: Total Planned Spend (Production cost at current Track's cost/unit x quantity, plus Advertising Spend, plus R&D Spend, plus Plant Investment amount, plus Celebrity Endorsement cost if toggled on) vs. Available Cash.

All spend types count toward the same total, including Plant Investment and Celebrity Endorsement — anything that pulls cash out this round.

If Total Planned Spend exceeds Available Cash: the total display turns red, a short in-universe message appears (e.g., "Reduce spending to submit"), and the Lock In button disables — student physically cannot submit an unaffordable plan.

BOTTOM — Running Ledger

- One line per completed round: Price, Units Sold, Revenue, Cost, Profit
- Rounds stack chronologically as the game progresses
- Totals row below the ledger: cumulative Revenue, cumulative Cost, cumulative Profit, and cumulative Cash on Hand

Own-Firm Segment Breakdown Table (per round, sits alongside the ledger):

- Shows this firm's own units sold broken out across all 5 segments (Low Income, NBA Fans, Basketball Players, Wealthy, Casual/Fashion) for the most recently completed round.
- Table format, consistent with the Running Ledger above it — not a chart.
- Reveals results only (this firm's actual units sold per segment), never the underlying formulas (elasticity coefficients, quality weights, track multipliers stay hidden forever per Section 12).
- Intentional design choice: lets sharp students reverse-engineer segment behavior over multiple rounds by observing how their own mix shifts when they change price/track/etc. — treated as a teaching feature, not a leak.

Team-ranking box explicitly does NOT go here — it lives on the Market Dashboard instead.

Non-Submission Status Card (per master doc Section 9):

- If a firm fails to submit before the teacher advances the round, this stylized in-universe card appears at the top of that firm's dashboard for the round it happened — above the Round Transition Notice banner if both are showing.
- Example wording: "Emergency Production Directive Issued — Command was unreachable last cycle. All available capital was redirected to production."
- No explicit error/warning language — stays in-universe per the master doc's tone.

Avatar selection (first-time team setup, tied to this screen's identity):

- During initial company setup (alongside Company Name + Password creation), each team picks one avatar image from a set of 20 pre-made options, sourced from Robert's graphics pack.
- This avatar is permanent for the team for the season and is what displays on the Market Dashboard's podium (see below).


## 3. Market Dashboard (Shared / All-Firms View)

Navigation note: Both the Firm Dashboard and the Teacher Dashboard must include a visible link/button to this Market Dashboard, since it's a shared class-wide view distinct from either of those screens. Without it, firms and the teacher have no way to reach it.

The class-wide view all teams can see — shows how every firm is doing, not just their own.

Podium (top of screen):

- Shows the avatars of the top 3 firms ranked by total cumulative profit (not net worth/cash — profit specifically).
- Images are sized by rank: largest = 1st place, medium = 2nd place, smallest = 3rd place. Not rendered as a literal podium/stand graphic — sizing alone conveys rank.
- Each of the 3 images has the firm's Company Name displayed underneath it.

Ranking Table (below podium):

- Single combined table — no separate P&L table elsewhere on this screen.
- Sorted by total cumulative profit, includes ALL firms (not just top 3).
- Columns: Rank, Company Name, Price, Units Sold, Revenue, Profit.

Market Share Pie Chart:

- Always visible on this screen (not click-through).
- Visualizes current market share split across all firms.

Competitive Intelligence Report:

- NOT shown inline on this screen — accessed via a click-through link/button that opens a separate view/window.
- Keeps the main Market Dashboard simple; report itself can be denser/more detailed.

Customer Segment Overview (all 5 segments):

- One panel/card per segment (Low Income, NBA Fans, Basketball Players, Wealthy, Casual/Fashion) — visual treatment TBD, no hidden-formula numbers exposed.
- Per segment, shows: Segment Name; Relative Size (as a share of total buyer pool, not a raw headcount, so it reads intuitively); Units Sold This Round (total across the whole market into that segment); Leading Track (whichever of Budget/Standard/Premium is currently capturing the most sales in that segment).
- Purpose: gives students a directional signal on where demand is moving without leaking the underlying elasticity/track/quality formulas.


## 4. Teacher Dashboard

Entry flow: Teacher logs in via Teacher Code on the universal login screen, lands on a simple World Picker (the 4 class-period worlds listed by name/period), selects one, then enters that specific world's Teacher Dashboard below. Each world's dashboard is fully isolated from the others.

Single continuous screen, no tabs — everything stacked vertically:

Round Control Bar (top)

- Round-advance button
- (Submission status is no longer a separate count here — it's read directly off the status icons in the Firms Decision Table below, so the teacher never advances blind without needing a duplicate summary)

Firms Decision Table (with Round Selector)

- Round selector (dropdown or numbered tabs, Round 1 through Round 10) sits directly above the table, defaulting to whichever round is currently live/active.
- One row per firm in this world
- Columns — every variable for that firm/round, both submitted decisions AND resulting outputs, side by side: status icon, firm name, Price, Production Qty, Ad Spend, R&D Spend, Track, Celebrity toggle, Plant Investment, Units Sold, Revenue, Total Cost, Profit, Cash on Hand, Loan Outstanding, Quality Level.
- Status icon per row: a green checkmark for firms that submitted normally that round, or an "Auto" badge for firms that were auto-handled by the Non-Submission mechanism — every row gets one or the other, so the teacher can read submission status for the whole class at a glance without a separate count anywhere else
- Running Total row/column: in addition to the selected round's per-round figures, the table also shows each firm's cumulative running totals across all rounds played so far (at minimum: cumulative Revenue, cumulative Profit, cumulative Units Sold — cumulative Cost optional if it fits cleanly). This lets the teacher see both "how did this round go" and "how's this firm doing overall" without leaving the table.
- Selecting a past (completed) round loads that round's historical decision and output data into the same table — same columns, same status icons, just read-only/historical instead of live. Running totals shown alongside a past round reflect the cumulative total through that round, not through the current live round. This stays on the single continuous Teacher Dashboard screen rather than becoming a separate page, keeping the "no tabs" philosophy intact.

Segment Breakdown — Bar Chart (not a table)

- One bar per customer segment (5 total), showing units sold into that segment this round
- Leading track within each segment indicated via color/pattern coding on the bar
- Gives the teacher a fast visual read on which segments are hot and which track is winning them, more detail than the Market Dashboard's segment cards since this view is teacher-facing

End-of-Game Export (appears once this world's 10 rounds are complete):

- A CSV export button/link on the Teacher Dashboard, available once a world finishes its 10th round.
- Exports one row per firm per round across the full game: all submitted decisions plus all resulting outputs for that firm/round (see master doc Section 14 for exact field list).
- Teacher-facing only — not connected to any LLM or external API; a clean data pull for the teacher to review or analyze on their own.


## 5. Round Transition Notice (not a separate screen)

Not a standalone page — a banner/modal that appears on the Firm Dashboard itself the moment a new round opens for that team.

Purpose: gives clear notice that "Round X is complete" plus a quick summary of that firm's own results (price, units sold, revenue, profit for the round just finished), so the team doesn't just see dashboard numbers silently change without realizing a round closed.

Dismissible — once closed, team proceeds straight into the now-current round's decision inputs on the same Firm Dashboard.

Market Dashboard and Teacher Dashboard need no equivalent notice — they're shared/live views where updated numbers are self-explanatory on refresh.


## Screen map complete

All screens mapped: Login (incl. first-time team creation flow), Firm Dashboard, Market Dashboard, Teacher Dashboard, Round Transition Notice. No further pending screens.


---

## Known conflicts with the actual build (as of 2026-09-08)

Flagged during a full audit against the live app -- see the session where this doc was (re-)pasted for full detail. Not yet resolved; listed here so they survive future compaction too.

1. **Login Screen** -- app has two separate pages (`/login` game-code entry, `/teacher/login` password-only), not one universal 3-field screen routing by which fields are filled.
2. **Student registration** -- this doc says teams self-register with no pre-assigned slots. The app was built with a slot-picker flow (`Firm.slot_number`, pick an unclaimed "Firm N" slot, then register) per an explicit decision made mid-session. These two are in direct conflict and were never reconciled -- confirm which is actually current before touching auth again.
3. **Two-column Firm Dashboard layout** -- app is single-column/stacked, not state-right/decisions-left.
4. **Running Ledger** (per-round history + cumulative totals row) on the Firm Dashboard -- not built. A firm currently only sees the most recent round's result via the transition banner.
5. **Own-Firm Segment Breakdown Table** on the Firm Dashboard -- not built.
6. **Round Transition Notice** -- this doc describes a dismissible per-firm banner shown once a new round is ALREADY open underneath it. The app instead built a two-step, world-wide gate: teacher processes a round (status -> "transition", blocking everyone), then must click a second "open next round" button before anyone can submit again. Real architecture difference, not yet reconciled.
7. **Teacher Dashboard status icons** -- doc wants a checkmark (submitted) / "Auto" badge (non-submission) per row. App shows a different status concept (bankrupt/unclaimed/active) instead.
8. **Teacher Dashboard submission count** -- doc says remove the separate "X/Y submitted" text now that status icons carry that info. App still shows it.
9. **Teacher Dashboard Segment Breakdown bar chart** -- not built at all (a different, denser view than the Market Dashboard's segment cards).
10. **Team Lookup password reset** -- not built (Team Lookup can view history but not reset a team's password).
11. **CSV export gating** -- doc says available only once a world completes round 10. The app makes it available anytime (a deliberate call at the time, now a confirmed conflict).
12. **Live Cost Calculator hard-block** -- since resolved: auto-calculated Production Quantity + a server-side overspend hard-block were added in a later session pass.
13. **Pricing Guidance Tool** (master-variable-table.md Section 13) -- entire feature not built.
14. Avatar set is the Kenney "City Kit Industrial" pack (34 images), not "20 pre-made options from Robert's graphics pack" -- open question whether a different, specific asset set was intended.
