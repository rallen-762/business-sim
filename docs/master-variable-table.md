# Business Simulation — Master Variable Table (V1)

*Consolidated reference of all locked design decisions. Items still pinned for later are noted at the bottom — they are NOT reflected in these numbers yet.*

---

## 1. Starting Conditions (All Firms, All Worlds)

| Variable | Value |
|---|---|
| Starting Cash | $1,000,000 (flat, identical across all 4 worlds, no reset between rounds) |
| Starting Plant Capacity | 45,000 units |
| Starting Quality Level | 1 |
| Base Product | Shoes |
| Base Unit Cost (Standard track, before multiplier) | $50/unit |
| Rounds per World | 10 |

---

## 2. Round-by-Round Decision Inputs (Per Firm)

- Price
- Production Quantity
- Advertising Spend
- R&D Spend
- Track (Budget / Standard / Premium)
- Celebrity Endorsement (on/off — separate from Track)
- Plant Investment (pre-packaged capacity toggles)

---

## 3. Persistent Stocks (Carry Forward Round-to-Round, No Reset Within a World)

| Stock | Starting Value | Notes |
|---|---|---|
| Available Cash | $1,000,000 | Rolls forward; can go negative → triggers loan |
| Plant Capacity | 45,000 units | 1-round lag on new investment; no decay |
| Quality Index (1–10) | 1 | Driven purely by cumulative R&D spend; NOT capped by Track |
| Loan Outstanding | $0 | See Section 8 |

---

## 4. Track Economics

### 4a. Cost Multiplier (flat, no economies of scale, no quality effect)

| Track | Multiplier | Cost/Unit |
|---|---|---|
| Budget | 0.75× | $37.50 |
| Standard | 1.00× | $50.00 |
| Premium | 1.40× | $70.00 |

### 4b. [REMOVED — superseded by Section 12 segment model]

*This generic Track/Quality demand multiplier table has been retired. Quality's effect on demand is now handled entirely by each segment's own Quality Weight (Section 12), multiplied by that segment's Track Preference Multiplier. Keeping both would have double-counted quality's impact on demand. See Section 12 for the current, locked mechanism.*

### 4c. Quality Descriptor Labels (combine with Track name)

| Quality Level | Descriptor |
|---|---|
| 1–2 | Low |
| 3–4 | Modest |
| 5–6 | Solid |
| 7–8 | High |
| 9–10 | Elite |

*Example label: "Solid Quality Standard" or "Elite Quality Premium."*

---

## 5. R&D / Quality Ladder (Cumulative Spend to Reach Each Level)

| Quality Level | Cumulative R&D Spend | Cost from Prior Level |
|---|---|---|
| 1 | $0 | — |
| 2 | $50,000 | $50,000 |
| 3 | $100,000 | $50,000 |
| 4 | $150,000 | $50,000 |
| 5 | $200,000 | $50,000 |
| 6 | $300,000 | $100,000 |
| 7 | $400,000 | $100,000 |
| 8 | $500,000 | $100,000 |
| 9 | $600,000 | $100,000 |
| 10 | $700,000 | $100,000 |

No per-round spending cap — the price itself is the constraint. Quality stock persists even across Track switches.

---

## 6. Advertising Ladder

| Ad Level | Cost This Level | Cumulative Cost | Demand Multiplier |
|---|---|---|---|
| 1 | — | $0 | 1.000 |
| 2 | $75,000 | $125,000 | 1.180 |
| 3 | $100,000 | $225,000 | 1.330 |
| 4 | $125,000 | $350,000 | 1.450 |
| 5 | $150,000 | $500,000 | 1.540 |
| 6 | $175,000 | $675,000 | 1.610 |
| 7 | $200,000 | $875,000 | 1.660 |
| 8 | $225,000 | $1,100,000 | 1.690 |
| 9 | $250,000 | $1,350,000 | 1.705 |
| 10 | $275,000 | $1,625,000 | 1.705 *(the "trap" — no gain over Level 9)* |

*Diminishing-returns curve is intentional and secret — not disclosed to students.*

---

## 7. Fixed Costs & Capacity

| Item | Value |
|---|---|
| Rent, Utilities & Labor | $100,000/round base (covers starting 45,000 capacity), + $15,000/round per additional 15,000-unit capacity block invested beyond the base. E.g., 60,000 capacity (1 expansion) = $115,000/round; 90,000 capacity (3 expansions) = $145,000/round. Renamed from "Rent & Utilities" to reflect that plant expansion requires ongoing labor cost, not just a one-time capital cost — this is the recurring cost of running additional capacity, not just building it. |
| Base Plant Capacity | 45,000 units |
| Plant Expansion Rate | Binary toggle each round: $0 (no investment) or $100,000 (one-time capital cost, +15,000 capacity, 1-round lag as always). No larger single-round investment option — a firm wanting more capacity must invest $100,000 again in a later round. Each block also adds $15,000/round to Rent, Utilities & Labor going forward. |
| Celebrity Endorsement Cost | Flat $50,000/round, recurring while toggled on (same rate all 10 rounds) |

**Reconciliation note:** Total buyer pool is locked at 408,000 (base 8,000 × scale factor 51 — see Section 12 pending). With 7 firms at 45,000 capacity each, total industry capacity (315,000) stays below the buyer pool, so capacity — not raw demand — is the binding constraint on sales in a typical round. This was a corrected design flaw: an earlier pass sized the buyer pool without checking it against real production capacity/cash, which allowed firms to "sell" far more units than they could ever produce. Standing note: always cross-check any demand-side or revenue-target math against capacity and cash constraints before locking numbers.

---

## 7b. Unsold Inventory (LOCKED)

Any units a firm produces but does not sell in a round are **destroyed/scrapped** — no rollover to future rounds, no storage cost mechanic. Production cost for unsold units is a sunk cost (already deducted from cash; no revenue offsets it). Each round starts clean: this round's Units Sold can only come from this round's production. Rationale: rollover would let firms "bank" cheap overproduction and dump it into a high-demand round later, letting market-timing override the actual quality/price/segment strategy the game is meant to teach. Destroying unsold units also reinforces a real lesson (overproduction risk) with no new mechanics required.

---

## 8. Loan / Bankruptcy Mechanism

| Item | Value |
|---|---|
| Trigger | Cash would go negative after round processes |
| Loan Amount | Flat $500,000 (not shortfall-sized) |
| Interest Rate | 10% per round on outstanding balance, compounding until paid off |
| Repayment | Automatic — flat $100,000 principal deducted from cash each round while balance remains; 10% interest applies to remaining balance after that round's repayment |
| Lifetime Limit | **One loan per firm per world, ever.** A firm may only receive this bailout loan once. |
| Bankruptcy Trigger | If a firm's cash would go negative again after it has already used its one loan, the firm is declared **bankrupt** instead of receiving a second loan. |
| Bankruptcy Handling | Firm's dashboard freezes for the remainder of the world — no further decisions, no further production. Firm remains visible on Teacher/Market Dashboards marked as bankrupt. |
| Soft Penalty While Indebted | Blocked from new Plant Investment and Celebrity Endorsement purchases |
| Dashboard Display | "Loan Outstanding" and "Loan Interest Expense (this round)" shown as permanent line items from Round 1, even at $0. Once a firm's one loan has been used, dashboard should reflect no further loan is available if cash goes negative again. |

---

## 9. Non-Submission Handling

If a firm fails to submit a round's decisions:
- Price and Track carry forward unchanged from prior round.
- 100% of available cash defaults to Production (up to capacity limit).
- $0 goes to R&D, Advertising, or Plant Investment that round.
- Firm dashboard shows a stylized in-universe status card (e.g., "Emergency Production Directive Issued") rather than an explicit error/warning.

---

## 10. Post-Round Competitive Intelligence Report (Market Dashboard)

**Visible for every firm:**
- Price
- Track
- Quality Level
- Advertising Spend
- Units Sold
- Market Share
- Loan flag (simple note if carrying debt — no dollar amount)

**Hidden for every firm:**
- Exact Plant Capacity
- Exact Cash Balance
- R&D Spend

---

## 11. Dashboards (Confirmed Structure)

- **Teacher Dashboard** — master control/monitoring view
- **Firm Dashboard** — individual team's private decisions, live cost calculator (hard-blocks submission if planned spend exceeds cash), loan status, non-submission status card
- **Market Dashboard** — macro view; hosts the Competitive Intelligence report; pie chart of market share across all firms; list of all firms' profit/loss and key stats

---

## 12. Market Segment Model (Locked)

**Buyer pool.** Total buyer pool = base 8,000 × scale factor 51 = **408,000 buyers/world**, split across 5 segments:

| Segment | Base Count | Scaled Count (×51) |
|---|---|---|
| Low Income | 2,500 | 127,500 |
| NBA Fans | 1,500 | 76,500 |
| Basketball Players | 1,000 | 51,000 |
| Wealthy | 800 | 40,800 |
| Casual/Fashion | 2,200 | 112,200 |
| **Total** | **8,000** | **408,000** |

With 7 firms at 45,000 units capacity each (315,000 industry capacity), the buyer pool exceeds total capacity, so **capacity — not raw demand — is the binding constraint** on sales in a typical round (see Section 7 reconciliation note).

**Demand-pull mechanic.** Each firm generates a relative attractiveness *score* per segment — termed **Firm's Demand Pull per Segment** — not an actual buyer count. A firm's real share of a segment = Firm's Demand Pull per Segment ÷ sum of all firms' Demand Pull in that segment. That share is applied against the segment's real fixed buyer count to yield actual units sold to that segment. (Worked example: Firm A Demand Pull 2,304 vs. Firm B Demand Pull 400 in the Wealthy segment (800 real buyers) → Firm A ≈ 85% share ≈ 680 units, Firm B ≈ 15% ≈ 120 units.)

**Advertising note (LOCKED):** Advertising Multiplier is flat and applies identically across all 5 segments — pulled straight from the Advertising Ladder (Section 6), no per-segment variation. Decided to keep the model simpler.

**Formulas for student guide (LOCKED terminology):**
- Firm's Demand Pull per Segment = Track Preference Multiplier × Quality Weight × Price Multiplier × Advertising Multiplier × Celebrity Multiplier
- Units Sold (Firm, Segment) = (Firm's Demand Pull per Segment ÷ Σ all firms' Demand Pull in that segment) × Segment's Real Buyer Count

**1. Track Preference Multiplier**

| Segment | Budget | Standard | Premium |
|---|---|---|---|
| Low Income | 1.3 | 0.9 | 0.5 |
| NBA Fans | 0.5 | 0.9 | 1.4 |
| Basketball Players | 0.7 | 1.0 | 1.2 |
| Wealthy | 0.4 | 0.8 | 1.5 |
| Casual/Fashion | 0.8 | 1.3 | 0.9 |

**2. Quality Weight** (Quality 1 → Quality 10, linear interpolation between)

| Segment | Q1 | Q10 |
|---|---|---|
| Low Income | 1.0 | 1.0 *(flat — doesn't care)* |
| NBA Fans | 0.9 | 1.3 |
| Basketball Players | 0.7 | 1.8 *(steepest — dominant factor)* |
| Wealthy | 0.8 | 1.6 |
| Casual/Fashion | 0.95 | 1.05 *(nearly flat)* |

**3. Celebrity Endorsement Multiplier** (applies only if celebrity is ON)

| Segment | Multiplier |
|---|---|
| Low Income | 1.0 |
| NBA Fans | 1.5 *(primary driver)* |
| Basketball Players | 1.1 |
| Wealthy | 1.2 |
| Casual/Fashion | 1.0 |

**4. Price Elasticity of Demand Coefficient** (LOCKED — final)

| Segment | Elasticity Coefficient | Character |
|---|---|---|
| Low Income | 1.4 | Highly elastic — very price-sensitive |
| NBA Fans | 0.3 | Highly inelastic — buying brand/celebrity, barely price-sensitive |
| Basketball Players | 0.7 | Moderately inelastic — cares more about quality than price |
| Wealthy | 0.1 | Near-perfectly inelastic below $200, then tapers hard to a $250 ceiling — see mechanism below |
| Casual/Fashion | 1.0 | Unit elastic — baseline, proportional response |

**Elasticity mechanism (LOCKED):** demand multiplier = 1 − (% price above the $50 base cost) × segment coefficient, floored at 0. E.g., a firm pricing at $80 (60% above base) against Low Income (1.4): 1 − 0.60×1.4 = **0.16** multiplier — badly hurt but not instantly zeroed like it was at coefficient 2.0 (which wiped Low Income out entirely by $75). Full wipeout for Low Income now lands around $85.70 (≈71% markup) instead of $75. Checked against the $80 Round-1 anchor price: Low Income 0.16, Casual/Fashion 0.40, Basketball Players 0.58, NBA Fans 0.82, Wealthy 0.94 — every segment still has *some* live demand near the target price, no segment goes mathematically unreachable at the profit-target price point.

**Wealthy segment $250 ceiling (LOCKED):** piecewise curve, not a single slope.
- **Below $200:** normal elasticity formula applies (coefficient 0.1) — demand barely moves. At $200 (300% above $50 base): multiplier = 1 − 3.0×0.1 = 0.70.
- **From $200 to $250:** switches to a steeper decay so it visibly breaks from the flat part of the curve: `multiplier = 0.70 × ((250 − price) / 50)²`. At $250 exactly, multiplier = 0.
- Shape: $200→0.70, $210→0.45, $225→0.18, $240→0.03, $250→0.00. Reads as "the rich don't care, until suddenly they really do." Price above $250 is disallowed for this segment (multiplier locked at 0).

*Note (RESOLVED): Section 4b's generic Track/Quality table has been removed. Quality Weight (this section) is now the sole quality-effect layer, applied together with the Track Preference Multiplier — no double-counting.*

## 13. Pricing Guidance Tool (Firm Dashboard, LOCKED mechanism)

Purpose: help students reason about price changes without exposing the hidden segment formulas (elasticity coefficients, quality weights, track multipliers, Wealthy ceiling curve all stay secret forever).

**Mechanism:**
- Baseline = the firm's own actual result from last round (actual price, actual units sold). Real numbers the firm already has and trusts.
- Toggle lets the firm enter a hypothetical new price for the upcoming round.
- Tool applies the firm's own price elasticity (given their current quality/track) to that baseline and projects an estimated units-sold range if only price changes — everything else (competitor behavior, own quality, own track, own ads) assumed to hold as it did last round.
- Output framed as a plain comparison, e.g., "Last round you sold approximately 9,000 units at $70. At $90, we estimate approximately 6,000 units, assuming similar competitor behavior."
- Explicitly labeled as an estimate with the "assumes similar competitor behavior" caveat shown to the student — the tool does not, and should not, react live to hypothetical competitor moves or this round's in-progress decisions.
- **Round 1 fallback (LOCKED):** no prior-round baseline exists yet, so the tool shows a generic neutral starting projection instead (e.g., assumes an even split of the buyer pool relative to industry capacity at a flat $50 baseline price), clearly flagged with a message like "Round 1 has no prior results yet — this is a generic starting estimate." From Round 2 onward, the tool always uses the firm's own actual prior-round results as normal.

---

## PINNED FOR LATER (not yet designed — do not assume default values)

*Note: the advertising diminishing-returns explanation moved out of "pinned" — it now belongs in the separate Teacher Reference Guide (not the dashboard itself). See that document for the full write-up.*
4. **Teacher dashboard segment-breakdown tab** — revenue/units by segment, agreed conceptually, not yet designed in detail.
5. **Student user guide** — segment/customer profiles (qualitative only, no hidden numbers), plus a clear explanation of the three Track price/cost points ($37.50 Budget / $50 Standard / $70 Premium).

---

*No code, database schema, or architecture has been built yet — this document is the economic-model spec only.*

---

## 14. Round Processing, Submission, and Reporting Model (LOCKED — classroom operations)

**Batch round processing (not live/real-time):**
- No round math (demand pull, units sold, revenue, cost, profit, quality/loan updates) is calculated the instant a firm submits.
- All submissions for a round are simply collected and stored as they come in. The entire round's economic model runs in a single batch, once, only when the teacher presses the round-advance button on the Teacher Dashboard.
- This means simultaneous/near-simultaneous submissions from multiple firms are a non-issue — there is no live processing to collide during.

**Submission persistence (immediate, no separate save step):**
- The moment a firm presses Lock In, their submitted decisions are written to the database immediately and reflected on the Teacher Dashboard's Firms Decision Table right away — this is a hard requirement, not an assumption to leave implicit.
- Because of this, a firm's submission is safe the instant they lock in, regardless of what happens to their own browser/session afterward (Chromebook logout, tab crash, etc. — their problem, not a data-loss risk to the app).
- No requirement to persist a firm's unsaved, in-progress (not-yet-submitted) inputs across sessions — if they get logged out before locking in, they simply re-enter and resubmit. Not a bug to solve for.

**No edit-after-submit window (LOCKED):**
- Once a firm presses Lock In for a round, their submission is final for that round. No unlock/resubmit flow before the teacher advances the round.

**End-of-Game Export (LOCKED, new requirement):**
- Once a world's 10-round game is complete, the Teacher Dashboard must offer a CSV export of that world's full game history.
- Shape: one row per firm per round, covering all 10 rounds for all firms in that world — every submitted decision (Price, Production Qty, Ad Spend, R&D Spend, Track, Celebrity toggle, Plant Investment) plus every resulting output (Units Sold, Revenue, Cost, Profit, Cash on Hand, Quality Level, Loan status) for that firm in that round.
- Purpose: teacher-facing review/grading only. Not connected to any LLM/API inside the app — the teacher will run their own analysis externally (e.g., uploading the CSV to an LLM themselves) if desired. The app's only job is to produce a clean, complete export.
