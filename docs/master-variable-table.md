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

**Demand-pull mechanic (REVISED Sept 2026 — individual buyer willingness-to-pay model).** Each firm generates a relative attractiveness *score* per segment — termed **Firm's Demand Pull per Segment** — not an actual buyer count. This is now a TWO-STEP allocation, not a single proportional split of the whole segment:

1. **Affordability gate.** Every buyer in a segment has their own willingness-to-pay ceiling, which varies by TRACK (Budget/Standard/Premium — see the table below), not one flat ceiling per buyer. A buyer can only ever be served by a firm whose price is at or below that buyer's ceiling for that firm's track. A buyer who can't afford any competing firm simply doesn't buy this round — this replaces the old rule that the full segment headcount always gets divided up among competitors regardless of price. A firm can now genuinely lose ALL demand in a segment by pricing too high, not just lose relative share.
2. **Preference scoring among only the affordable firms.** Among the firms a given buyer CAN afford, the existing Demand Pull score (Track Preference × Quality Weight × Advertising × Celebrity — see below) decides which one they actually buy from, exactly as before. Buyers are maximizing personal consumer surplus (their own ceiling minus the price paid), not simply picking the cheapest affordable option.

Price no longer appears in the Demand Pull score itself — the old Price Elasticity multiplier is GONE (see the willingness-to-pay table replacing old Section 12.4 below). Keeping both a smooth elasticity penalty AND the new affordability gate would double-penalize higher prices.

**Advertising note (LOCKED):** Advertising Multiplier is flat and applies identically across all 5 segments — pulled straight from the Advertising Ladder (Section 6), no per-segment variation. Decided to keep the model simpler.

**Formulas for student guide (LOCKED terminology):**
- Firm's Demand Pull per Segment = Track Preference Multiplier × Quality Weight × Advertising Multiplier × Celebrity Multiplier (price is NOT a factor here anymore)
- A buyer at willingness-to-pay percentile r can afford a firm iff that firm's price ≤ that buyer's ceiling for the firm's track
- Units Sold (Firm, Segment) = Σ, over every slice of the buyer population where this firm is among the affordable set, of (this firm's Demand Pull ÷ Σ affordable firms' Demand Pull in that slice) × that slice's buyer count

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

**4. Willingness-to-Pay Ceilings** (REPLACES the old Price Elasticity Coefficient — Sept 2026 redesign)

Each buyer's maximum willingness-to-pay ceiling varies by TRACK, not one flat number per buyer. Baseline centers:

| Segment | Budget Ceiling | Standard Ceiling | Premium Ceiling |
|---|---|---|---|
| Low Income | $65 | $68 | $70 |
| NBA Fans | $90 | $105 | $120 |
| Basketball Players | $35 | $75 | $130 |
| Wealthy | $50 | $140 | $230 |
| Casual/Fashion | $75 | $85 | $90 |

**Spread (platform choice, not given by the original design docs):** each buyer's actual ceiling for a track is uniformly spread ±20% around that track's center for their segment (e.g. Wealthy/Premium ranges $184–$276) — not every buyer in a segment is identical. A single buyer's Budget/Standard/Premium ceilings are correlated (driven by one underlying "how willing to pay is this buyer, generally" draw), not three independent random numbers, since every segment's Budget < Standard < Premium ordering above means a buyer generally willing to pay more is willing to pay more across every track, not randomly more generous on one track and less on another.

**Wealthy segment $250 hard ceiling (LOCKED, kept from the pre-redesign model):** regardless of the willingness-to-pay curve above, a firm priced above $250 is excluded from the Wealthy segment entirely — this is a simple exclusion rule now, not a smooth taper curve (the old $200–$250 piecewise taper is gone along with the rest of the elasticity formula).

*Note (RESOLVED): Section 4b's generic Track/Quality table has been removed. Quality Weight (this section) is now the sole quality-effect layer, applied together with the Track Preference Multiplier — no double-counting.*

## 13. Pricing Guidance Tool (Firm Dashboard, LOCKED mechanism)

**⚠️ FLAGGED Sept 2026 — needs revision before this tool is ever built.** This section still describes the PRE-redesign price elasticity model (see Section 12's replacement). Not yet implemented anywhere in the codebase as of this note, so there's no live code conflict today, but the mechanism below is now wrong on two counts:
1. **Round 1 fallback** ("assumes an even split of the buyer pool... at a flat $50 baseline price") — there's no more "even split" under the willingness-to-pay model; a buyer either can or can't afford a given price on a given track.
2. **Round 2+ elasticity projection** ("applies the firm's own price elasticity... to project units-sold if only price changes") assumed a smooth, continuous curve. Under the willingness-to-pay ceiling model, raising price can hit a CLIFF — a chunk of buyers falls off all at once at their ceiling, not a smooth taper — so a projection built on the old smooth-elasticity assumption could mislead a student badly, especially in a segment with a tight buyer-to-buyer spread. Whoever builds this tool needs a new projection mechanism (e.g. estimating where the firm's own price sits against typical ceilings) before shipping it, not just a formula swap.

Purpose: help students reason about price changes without exposing the hidden segment formulas (willingness-to-pay ceilings, quality weights, track multipliers all stay secret forever).

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
