# business-sim — Master Variable Index

Where every meaningful value lives, who changes it, and whether touching it
moves game balance.

This is a **code map**. The locked economic design and the reasoning behind each
number live in [master-variable-table.md](master-variable-table.md); the rules
for working on the project live in
[PROJECT_INSTRUCTIONS.md](PROJECT_INSTRUCTIONS.md). Values are not repeated
here except where the value *is* the identifying fact.

**Balance column:** ● moves game balance — simulate before and after, and
update `master-variable-table.md` in the same change. ○ does not.

---

## 1. Starting conditions

| Variable | Purpose | Type / units | Defined | Used by | Range / default | Balance |
|---|---|---|---|---|---|---|
| `STARTING_CASH` | Every firm's opening cash | float, $ | `constants.py` | `teacher.create_world`, `sandbox`, `bot_sim` | 1,000,000 | ● |
| `STARTING_PLANT_CAPACITY` | Opening capacity | int, units/round | `constants.py` | same as above | 45,000 | ● |
| `STARTING_QUALITY_LEVEL` | Opening quality | int, level | `constants.py` | reference only | 1 | ● |
| `BASE_UNIT_COST` | Mid-tier cost before the tier multiplier | float, $/unit | `constants.py` | `track_unit_cost()` | 50.00 | ● |
| `ROUNDS_PER_WORLD` | **Default** game length | int, rounds | `constants.py` | default for `World.rounds`; bot pacing in `bots.py` | 10 | ● |
| `BOOTSTRAP_DEFAULT_PRICE` / `_TRACK` | Platform fallback for a firm that never submits | float / str | `constants.py` | `synthesize_non_submission_decision`, firm creation | 80.00 / "Mid" | ○ |
| `FIRMS_PER_WORLD_MIN` / `_MAX` | Intended class size | int | `constants.py` | reference only | 7 / 8 | ○ |

> `ROUNDS_PER_WORLD` is the **default**, not this game's length. Per-world
> length is `World.rounds`. See LESSONS_LEARNED #3.

---

## 2. Per-firm persistent state (`Firm`, `app/models.py`)

Carried round to round, never recomputed from history. Written in one place
only: the state-update block at the end of
`teacher.py::_process_current_round`.

| Field | Purpose | Type / units | Modified by | Read by | Balance |
|---|---|---|---|---|---|
| `cash` | Money on hand | float, $ | round processing; Undo restore | affordability checks, production sizing, bot decisions | ● |
| `plant_capacity` | Capacity in effect | int, units | round processing | `effective_capacity` | ● |
| `pending_capacity_increase` | Expansion maturing next round | int, units | round processing | `effective_capacity` | ● |
| `cumulative_rd_spend` | Lifetime R&D | float, $ | round processing (`+= d.rd_spend`) | `quality_level_from_cumulative_rd`, ladder presets | ● |
| `cumulative_ad_spend` | Lifetime advertising | float, $ | round processing | `ad_level_and_multiplier`, presets | ● |
| `loan_outstanding` | Debt balance | float, $ | round processing | soft-penalty gate, interest projection | ● |
| `loan_used_ever` | One loan per firm per world | bool | round processing | bankruptcy trigger | ● |
| `bankrupt` | Out of the game, permanent | bool | round processing | excluded from demand competition | ● |
| `last_price` / `last_track` | Carry-forward for a no-show | float / str | firm submit; bot decide | `synthesize_non_submission_decision` | ○ |
| `avatar`, `badge`, `product_icon` | Team identity | str filenames | registration; `init-db` self-heal | dashboards | ○ |
| `bot_profile` | Which bot drives this slot, or None | str | teacher assign/remove | round processing | ● |

### `Firm.effective_capacity` (property)

`plant_capacity + (pending_capacity_increase or 0)` — the capacity actually
usable **this** round. **Always read this rather than re-deriving the sum.**
Three call sites once disagreed and locked a paying team out of capacity it had
bought (LESSONS_LEARNED #9).

---

## 3. Per-round decision (`RoundDecision`)

Exactly one row per firm per round, submitted or synthesized.

| Field | Purpose | Type / units | Set by | Consumed by | Valid range | Balance |
|---|---|---|---|---|---|---|
| `price` | Unit selling price | float, $ | student form (free entry) | WTP affordability gate | ≥ 0; the dominant lever | ● |
| `production_qty` | Units to build | int, units | auto-computed, server-revalidated | capacity scaling, production cost | 0 … `effective_capacity` | ● |
| `rd_spend` | R&D this round | float, $ | dropdown only | cumulative R&D → quality | 0 … `max_rd_spend_this_round()` | ● |
| `ad_spend` | Advertising this round | float, $ | dropdown only | cumulative ads → ad multiplier | ≥ 0 | ● |
| `track` | Tier (**"track" is the internal name**) | str | student form | unit cost, tier preference, WTP ceiling | Entry / Mid / Premium | ● |
| `celebrity_on` | Is an endorsement running | bool | derived from `celebrity` | cost, multiplier gate | — | ● |
| `celebrity` | **Which** endorser | str, nullable | student form | `celebrity_multiplier()` | one of `CELEBRITIES`, or NULL | ● |
| `plant_investment` | Capacity purchase | int, $ | student form | capacity next round, cost | 0 or `PLANT_INVESTMENT_COST` | ● |
| `is_auto` | Bot or no-show, not a human submission | bool | round processing | reporting; Undo deletes only these | — | ○ |

> `celebrity = NULL` with `celebrity_on = True` means a round played before the
> four-endorser roster. It resolves to `LEGACY_CELEBRITY_MULTIPLIER` so old
> games stay reproducible — never backfill a name onto those rows.

---

## 4. Per-round outcome (`RoundResult`, `SegmentRoundResult`)

Written by round processing, never edited afterwards. Deleted by Undo.

Money and units: `units_sold_by_segment` (JSON), `units_sold_total`, `revenue`,
`production_cost`, `fixed_cost`, `ad_cost`, `rd_cost`, `celebrity_cost`,
`plant_investment_cost`, `total_cost`, `profit`, `cash_before`, `cash_after`.

Derived levels and capacity: `quality_level`, `ad_level`, `plant_capacity`
(the **effective** capacity that round), `new_pending_capacity_increase`.

Loan and status: `loan_taken_this_round`, `loan_principal_paid`,
`loan_interest_charged`, `loan_outstanding_after`, `loan_used_ever_after`,
`went_bankrupt_this_round`, `is_bankrupt`, `is_auto`,
`plant_investment_blocked`, `celebrity_blocked`.

Sold-out reporting:

| Field | Purpose | Notes |
|---|---|---|
| `units_demanded_total` | Demand won **before** capacity scaling | Float. Was computed and discarded before `20aa4ba` |
| `units_lost_to_capacity` | The part capacity destroyed | **Only non-zero when capacity actually bound.** A cash-limited short run reports 0, because a bigger factory would not have helped (LESSONS_LEARNED #10) |

`SegmentRoundResult` holds market-wide, per-segment stats: `total_buyers`,
`unsold_buyers`, `unsold_buyers_pct`, `avg_consumer_surplus` (NULL, not 0, when
nobody in that segment could afford anyone).

---

## 5. Demand and market calculation (Section 12 — all ● balance)

Demand pull per firm per segment:

```
Tier Preference × Quality Weight × Advertising Multiplier × Celebrity Multiplier
```

Price is **not** a factor here — it decides affordability separately, in the WTP
gate. Applied at `engine.py` Step 4a–4b.

| Variable | Purpose | Shape | Defined | Used by |
|---|---|---|---|---|
| `SEGMENTS` | The five internal segment keys | tuple[str] | `constants.py` | everywhere; also DB values and JSON keys |
| `SEGMENT_DISPLAY_NAMES` / `segment_label()` | Key → what a human reads | dict / fn | `constants.py` | all UI |
| `SEGMENT_BASE_COUNT` | Per-segment buyer base | dict[str,int] | `constants.py` | `SEGMENT_BUYER_COUNT` |
| `BUYER_POOL_BASE`, `BUYER_POOL_SCALE_FACTOR` | Scale the pool to real capacity | int | `constants.py` | `SEGMENT_BUYER_COUNT` |
| `SEGMENT_BUYER_COUNT` | Actual buyers per segment | dict[str,int] | `constants.py` | demand sweep; totals 408,000 |
| `TRACKS`, `TRACK_COST_MULTIPLIER` | Tiers and their unit costs | tuple / dict | `constants.py` | `track_unit_cost()` |
| `TRACK_PREFERENCE_MULTIPLIER` | How each segment rates each tier | dict[seg][tier] | `constants.py` | demand pull |
| `QUALITY_WEIGHT_ENDPOINTS`, `quality_weight()` | Quality sensitivity, Q1→Q10 | dict / fn | `constants.py` | demand pull |
| `WTP_CEILING_CENTER` | Per-segment, per-tier price ceiling centers | dict[seg][tier] | `constants.py` | affordability |
| `WTP_SPREAD_LOW` / `_HIGH` | Buyer-to-buyer spread around the center | float, ×0.8 … ×1.2 | `constants.py` | `wtp_threshold_r()` |
| `wtp_threshold_r()`, `wtp_ceiling_at_r()` | Closed-form affordability and surplus | fn | `constants.py` | engine Step 4b |
| `WEALTHY_CEILING_PRICE` | Hard exclusion above $250 | int, $ | `constants.py` | engine, absolute rule |

> **Hidden from players.** Section 12 values are discovered by playing and are
> never printed in the UI. A test asserts no segment name or multiplier leaks
> into the endorsement control.

---

## 6. Investment ladders (● balance)

| Variable | Purpose | Shape | Notes |
|---|---|---|---|
| `QUALITY_LADDER` | Cumulative R&D → quality level | tuple[(level, $)] | Levels 1–10, $0 → $700,000 |
| `quality_level_from_cumulative_rd()` | Lookup | fn | Reads the ladder |
| `MAX_QUALITY_LEVEL_GAIN_PER_ROUND` | Per-round climb cap | int, levels | **3.** Enforced in the engine *and* refused at submit |
| `max_rd_spend_this_round()` | The cap as dollars | fn → float or None | Bounds the dropdown |
| `rd_spend_presets()` | Reachable levels for this firm | fn | Dropdown options; no free entry |
| `AD_LADDER` | Cumulative ads → (level, multiplier) | tuple[(level, $, ×)] | Levels 1–10, ×1.0 → ×1.705 |
| `ad_level_and_multiplier()` | Lookup | fn | Demand pull |
| `ad_spend_presets()` | Reachable levels | fn | Dropdown options |

> The top rung of the ad ladder is a **trap**: measured effect peaks around
> Level 4–7 and Level 9 is worse than Level 7. Deliberate, and undocumented in
> the UI.

---

## 7. Celebrity endorsement (● balance)

| Variable | Purpose | Shape | Notes |
|---|---|---|---|
| `CELEBRITIES` | The four keys, in display order | tuple[str] | athlete, musician, star, influencer |
| `CELEBRITY_LABELS` / `CELEBRITY_ICONS` | Display name and asset | dict | UI only |
| `_CELEBRITY_TARGETS` | (strong, secondary) segment per endorser | dict | The design intent |
| `CELEBRITY_MULTIPLIERS` | Derived per-endorser × per-segment table | dict[key][seg] | Built from the two constants below |
| `CELEBRITY_STRONG` / `_SECONDARY` | The two multiplier tiers | float | 1.5 / 1.2 |
| `LEGACY_CELEBRITY_MULTIPLIER` | The original single celebrity | dict[seg] | **Do not use for new decisions.** Scores pre-roster rounds |
| `celebrity_multiplier(key, segment)` | Resolver, with legacy fallback | fn | Unknown key → legacy, never raises |
| `CELEBRITY_COST_PER_ROUND` | Flat cost | int, $/round | 50,000, identical for all four — the choice is fit, never price |

> Every option, **including running none**, is the best choice in some
> tier/price situation. Do not "fix" one for looking weak in isolation
> (LESSONS_LEARNED #6).

---

## 8. Capacity, fixed costs, and the loan

| Variable | Purpose | Type / units | Balance |
|---|---|---|---|
| `BASE_FIXED_COST` | Rent/utilities/labour covering the base 45,000 | int, $/round | ● |
| `CAPACITY_BLOCK_SIZE` | Units per expansion block | int, units | ● |
| `CAPACITY_BLOCK_FIXED_COST` | Added recurring cost per block, **forever** | int, $/round | ● |
| `fixed_cost_for_capacity()` | Base + per-block cost | fn | ● |
| `PLANT_INVESTMENT_COST` | One-time capital cost | int, $ | ● |
| `PLANT_INVESTMENT_CAPACITY_GAIN` | Capacity bought, matures **next** round | int, units | ● |
| `PLANT_INVESTMENT_CHOICES` | The binary toggle | tuple | ○ |
| `LOAN_AMOUNT` | Flat one-time bailout | int, $ | ● |
| `LOAN_REPAYMENT_PRINCIPAL` | Flat repayment per round | int, $/round | ● |
| `LOAN_INTEREST_RATE` | Charged on what remains **after** repayment | float | ● |

Debt blocks new Plant Investment and Celebrity (the "soft penalty"), enforced
defensively in the engine, not just in the UI.

---

## 9. World / game state (`World`, `app/models.py`)

| Field | Purpose | Type | Modified by | Balance |
|---|---|---|---|---|
| `mode` | classroom / sandbox / bots_only | str | creation only | ○ |
| `rounds` | **This** world's length | int | creation only | ● |
| `current_round` | Round in progress | int | `_open_next_round`, Undo | ○ |
| `status` | collecting / transition / complete | str | round processing, Undo | ○ |
| `reopened_round` | The one round Undo made editable again | int, nullable | Undo sets, processing clears | ○ |
| `game_code` | Globally unique join code | str | creation | ○ |
| `planned_firm_slots` | Pre-created empty slots | int | creation | ○ |

`RoundSnapshot` stores every firm's pre-round state as JSON so Undo can restore
it exactly. `SNAPSHOT_FIELDS` in `teacher.py` lists the fields captured — a test
diffs every Firm column across a real round and fails if processing starts
writing one that is not in that list.

---

## 10. Bots (● balance)

Pure logic in `app/bots.py`; a bot sees **only its own history**.

| Variable | Profile | Purpose |
|---|---|---|
| `BOT_PROFILES` | — | The four: Underbidder, Marketing, Elite, Random. **Slot order defines "Bot #N"** |
| `UNDERBIDDER_TRACK`, `_START_PRICE`, `_PRICE_STEP`, `_MIN_MARGIN`, `_PRICE_FLOOR` | Underbidder | Price floor is a real margin over its own unit cost, not a magic number |
| `MARKETING_PRICE_BASE`, `_AD_RAMP_ROUNDS`, `_CELEBRITY_CASH_THRESHOLD` | Marketing | Ramps ads to Level 9, never 10 |
| `MARKETING_EXPAND_MIN_LOST_UNITS`, `_EXPAND_CASH_BUFFER` | Marketing | The only profile that expands its plant, and only after actually selling out |
| `ELITE_CELEBRITY_CASH_THRESHOLD`, `_START_ROUND` | Elite | Signs a Movie Star from round 7 |
| `_AD_LEVEL_9_THRESHOLD`, `_RD_LEVEL_10_THRESHOLD` | shared | Ladder boundaries the profiles aim at |

---

## 11. Non-economic configuration (○ balance)

| Variable | Purpose | Where |
|---|---|---|
| `TEACHER_PASSWORD`, `SANDBOX_PASSWORD` | Two separate secrets, two separate capabilities | env; `app/__init__.py` |
| `SECRET_KEY` | Session signing | env |
| `DATABASE_URL` | Postgres in prod; absent locally, which is what sets `IS_LOCAL_DEV` | env |
| `IS_LOCAL_DEV` | Skips the teacher gate locally; forced False under TESTING | `app/__init__.py` |
| `PERMANENT_SESSION_LIFETIME`, `SESSION_COOKIE_*` | 8-hour permanent cookie, Lax, HttpOnly, Secure only in prod | `app/__init__.py` |
| `COMPETITIVE_INTEL_VISIBLE_FIELDS` / `_HIDDEN_FIELDS` | What one firm may learn about another | `constants.py` |
| `AVATAR_CHOICES`, `BADGE_CHOICES`, `PRODUCT_CHOICES`, `TIER_ICONS` | Icon pools, validated on submit | `app/avatars.py` |
| `SEGMENT_ACCENTS`, `TIER_ACCENTS`, `SEGMENT_TRAITS`, `PIE_COLORS` | Presentation only — never feeds the model | `app/market_data.py` |

> `SEGMENT_TRAITS` is the qualitative **translation** of Section 12 (price
> sensitivity / quality focus / brand pull as 1–3 dots). It shows tiers, never
> coefficients, and tests assert the dots track the real constants by ordering
> so the two cannot drift apart.
