"""
Pure economic engine for the Business Simulation game.

Deliberately has ZERO database or Flask dependency -- every function here
takes plain dataclasses/values in and returns plain dataclasses/values out.
This is what makes the highest-risk part of the app (the round-processing
math) fully unit-testable without a running Postgres instance, per the
project's standing requirement to test the economic model thoroughly.

The DB-touching orchestration layer (reads a round's submissions, calls
process_round(), writes the results back) belongs in a separate module once
the schema/routes exist -- it should be a thin wrapper around this file.

--------------------------------------------------------------------------
Edge cases considered before writing process_round() (per the project's
"list edge cases before writing each feature's code" requirement):
--------------------------------------------------------------------------
1. A firm's demand-pull-implied units across all 5 segments exceed what they
   actually produced -> scale all 5 segments down proportionally to fit;
   unmet demand is lost, not redistributed to competitors (locked decision).
2. A firm producing 0 units (no submission possible, or post-bankruptcy) ->
   0 sold everywhere; guarded against division by zero in the scaling step.
3. A segment where every firm's demand pull is 0 (e.g. all priced it dead) ->
   guarded against division by zero; 0 sold to that segment by anyone.
4. Wealthy segment price > $250 -> excluded from that segment outright,
   regardless of the willingness-to-pay curve (absolute hard rule, kept
   from the pre-redesign model).
6. R&D/Ad spend submitted THIS round count toward THIS round's Quality/Ad
   level (no lag). Plant Investment alone has the explicit 1-round lag.
7. A loan taken THIS round (to cover THIS round's shortfall) is not itself
   repaid or charged interest this same round -- that starts next round,
   once it's a "pre-existing balance" like any other.
8. A firm's cash could still be negative even after taking its one lifetime
   loan (the loan is flat $500k, not shortfall-sized) -- that is NOT a
   bankruptcy trigger by itself; bankruptcy is specifically "went negative
   again after already having used the loan."
9. A firm that is bankrupt is fully excluded from demand-pull competition
   (never included in segment totals) and produces nothing -- it still gets
   a frozen, zeroed result row every remaining round so the Teacher
   Dashboard/CSV always has one row per firm per round.
10. A firm currently carrying loan debt attempts Plant Investment or
    Celebrity Endorsement (the "soft penalty") -- defensively zeroed out
    inside the engine itself, not just relied on as a UI-side restriction.
11. Non-submission: price/track carry forward, 100% of cash to production
    (capped at capacity), $0 to R&D/Ads/Plant Investment, Celebrity forced
    OFF (confirmed) even if it was on before.
12. R&D/Ad spend beyond the level-10 threshold has no further effect but is
    still deducted from cash as a real (if wasted) expense.

--------------------------------------------------------------------------
Sept 2026 buyer-model redesign -- individual willingness-to-pay ceilings
replace "the full segment headcount always gets allocated to someone"
(Step 4b below). The old elasticity-based price_multiplier()/
ELASTICITY_COEFFICIENT is GONE entirely, not just superseded -- keeping it
alongside the new affordability gate would double-penalize higher prices.
Additional edge cases from this redesign:
--------------------------------------------------------------------------
13. A buyer whose willingness-to-pay ceiling clears NO competing firm's
    price on that firm's track -> doesn't buy from anyone this round.
    Tracked explicitly as SegmentDemandStats.unsold_buyers/
    unsold_buyers_pct, not silently dropped.
14. A segment where literally no firm is priced within reach of anyone (or
    no firm competes in it at all, e.g. everyone priced out of Wealthy) ->
    0 units for everyone, 100% unsold, avg_consumer_surplus=None (not
    0.0 -- there's no one to average over) -- no divide-by-zero.
15. Consumer surplus is computed from the EXACT closed-form integral of
    (ceiling(r) - price) over each r-interval a firm is affordable in, not
    a sampled approximation -- ceiling(r) is linear in r, so the integral
    reduces to (average of the two endpoint ceilings) x interval width x
    that firm's demand-pull share of the interval. Deterministic, no
    simulated individuals, no new randomness introduced into what has
    always been a fully deterministic function of its inputs.
16. A single buyer's Budget/Standard/Premium ceilings are driven by ONE
    shared percentile r (see constants.wtp_threshold_r's docstring), not
    three independent random draws -- correlated on purpose, since every
    segment's Budget < Standard < Premium centers keep a buyer's own three
    ceilings consistently ordered regardless of r.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.constants import (
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    CELEBRITY_COST_PER_ROUND,
    CELEBRITY_MULTIPLIER,
    TRACK_COST_MULTIPLIER,
    LOAN_AMOUNT,
    LOAN_INTEREST_RATE,
    LOAN_REPAYMENT_PRINCIPAL,
    MAX_QUALITY_LEVEL_GAIN_PER_ROUND,
    PLANT_INVESTMENT_CAPACITY_GAIN,
    SEGMENT_BUYER_COUNT,
    SEGMENTS,
    TRACK_PREFERENCE_MULTIPLIER,
    WEALTHY_CEILING_PRICE,
    ad_level_and_multiplier,
    fixed_cost_for_capacity,
    quality_level_from_cumulative_rd,
    quality_weight,
    track_unit_cost,
    wtp_ceiling_at_r,
    wtp_threshold_r,
)


# --------------------------------------------------------------------------- #
# Data shapes
# --------------------------------------------------------------------------- #

@dataclass
class FirmState:
    """A firm's persistent state as of the START of a round, i.e. exactly
    what process_round() for the PRIOR round produced as cash_after / etc."""
    firm_id: int
    cash: float
    plant_capacity: int
    pending_capacity_increase: int  # matures (is added) at the start of THIS round
    cumulative_rd_spend: float
    cumulative_ad_spend: float
    loan_outstanding: float
    loan_used_ever: bool
    bankrupt: bool


@dataclass
class FirmDecision:
    """One firm's fully-resolved decision for a round -- either what they
    actually submitted, or the non-submission auto-decision synthesized by
    synthesize_non_submission_decision() below. The engine treats both
    identically except for the is_auto flag, which only affects reporting."""
    firm_id: int
    price: float
    production_qty: int
    ad_spend: float
    rd_spend: float
    track: str
    celebrity_on: bool
    plant_investment: int  # 0 or PLANT_INVESTMENT_COST
    is_auto: bool = False


@dataclass
class FirmRoundResult:
    firm_id: int
    units_sold_by_segment: dict = field(default_factory=dict)
    units_sold_total: float = 0.0
    revenue: float = 0.0
    production_cost: float = 0.0
    fixed_cost: float = 0.0
    ad_cost: float = 0.0
    rd_cost: float = 0.0
    celebrity_cost: float = 0.0
    plant_investment_cost: float = 0.0
    total_cost: float = 0.0
    profit: float = 0.0
    cash_before: float = 0.0
    cash_after: float = 0.0
    quality_level: int = 1
    ad_level: int = 1
    plant_capacity: int = 0  # capacity in effect for this round
    new_pending_capacity_increase: int = 0  # matures next round
    # Demand this firm won BEFORE Step 7's capacity scaling, and the part of
    # it that capacity destroyed. units_demanded_total > units_sold_total
    # exactly when the firm sold out. Kept so the student can be TOLD they
    # sold out -- the scaling used to discard this and the loss was invisible.
    units_demanded_total: float = 0.0
    units_lost_to_capacity: float = 0.0
    loan_taken_this_round: float = 0.0
    loan_principal_paid: float = 0.0
    loan_interest_charged: float = 0.0
    loan_outstanding_after: float = 0.0
    went_bankrupt_this_round: bool = False
    is_bankrupt: bool = False  # entered this round already bankrupt (frozen)
    is_auto: bool = False
    plant_investment_blocked: bool = False  # soft-penalty correction applied
    celebrity_blocked: bool = False


@dataclass
class SegmentDemandStats:
    """Aggregate, SEGMENT-level (not tied to any one firm) outcome of the
    individual buyer willingness-to-pay sweep for one segment this round.
    total_buyers/unsold_buyers are fractional -- the sweep works in
    continuous buyer-percentile space (see process_round Step 4b), same
    rationale as FirmRoundResult.units_sold_total being fractional.
    avg_consumer_surplus is None (not 0.0) when nobody in this segment
    could afford anybody -- 0.0 would misleadingly claim "buyers broke
    even" instead of "there was no one to measure.\""""
    segment: str
    total_buyers: float
    unsold_buyers: float
    unsold_buyers_pct: float
    avg_consumer_surplus: float | None


class RoundResults(dict):
    """dict[int, FirmRoundResult] -- EXACTLY what process_round() has always
    returned, so every existing caller/test doing results[firm_id] needs no
    changes at all. Also carries `.segment_stats` (dict[str,
    SegmentDemandStats]), the new per-segment consumer-surplus/unsold-buyer
    data the same Step 4b sweep produces as a natural byproduct of computing
    raw_units -- attached here rather than duplicating that sweep in a
    second function (which would risk the two drifting out of sync) or
    changing process_round()'s return shape to a tuple (which would have
    forced every one of the ~20 existing engine tests to change how they
    unpack the result for a piece of data most of them don't even care
    about)."""
    segment_stats: dict[str, SegmentDemandStats]


# --------------------------------------------------------------------------- #
# Non-submission handling (Section 9)
# --------------------------------------------------------------------------- #

def synthesize_non_submission_decision(
    firm_id: int, last_price: float, last_track: str, cash: float, plant_capacity: int
) -> FirmDecision:
    """Price/Track carry forward unchanged. 100% of available cash goes to
    Production (up to capacity). $0 to R&D/Advertising/Plant Investment.
    Celebrity Endorsement is forced OFF regardless of its prior state.

    Defensive fallback on last_price/last_track: every real slot gets the
    BOOTSTRAP defaults at world creation, so these should always be set --
    but an unset or no-longer-valid value used to raise straight out of
    track_unit_cost() (KeyError), and this runs inside the teacher's
    round-advance, so one bad row would 500 the round for the WHOLE class
    mid-lesson. A stale tier name is a live possibility right after the
    Track -> Tier rename (a database that hasn't had init-db's rename step
    run against it yet), which is exactly when a hard failure would be
    worst. Falling back to the platform defaults keeps the round
    processable; the firm just gets the same deal a never-submitted slot
    would have gotten anyway."""
    if last_track not in TRACK_COST_MULTIPLIER:
        last_track = BOOTSTRAP_DEFAULT_TRACK
    if last_price is None:
        last_price = BOOTSTRAP_DEFAULT_PRICE
    unit_cost = track_unit_cost(last_track)
    max_affordable_units = int(cash // unit_cost) if unit_cost > 0 and cash > 0 else 0
    production_qty = max(0, min(max_affordable_units, plant_capacity))
    return FirmDecision(
        firm_id=firm_id,
        price=last_price,
        production_qty=production_qty,
        ad_spend=0.0,
        rd_spend=0.0,
        track=last_track,
        celebrity_on=False,
        plant_investment=0,
        is_auto=True,
    )


# --------------------------------------------------------------------------- #
# The batch round processor
# --------------------------------------------------------------------------- #

def process_round(states: dict[int, FirmState], decisions: dict[int, FirmDecision]) -> RoundResults:
    """Runs one round's entire economic model in a single batch, exactly as
    specified: no per-submission processing, everything computed together
    here. Pure function -- no DB, no side effects, fully deterministic given
    its inputs. `states` and `decisions` must share the same firm_id keys
    for every non-bankrupt firm; bankrupt firms need only appear in `states`.

    Returns a RoundResults (dict[firm_id, FirmRoundResult] exactly as
    before, plus a `.segment_stats` attribute -- see that class)."""
    results = RoundResults()

    active_ids = [fid for fid, s in states.items() if not s.bankrupt]

    # --- Step 1: apply the 1-round lag -- last round's Plant Investment
    # matures into capacity available THIS round. ---
    effective_capacity = {fid: states[fid].plant_capacity + states[fid].pending_capacity_increase for fid in active_ids}

    # --- Step 2: soft-penalty enforcement -- a firm entering this round with
    # outstanding debt cannot spend on Plant Investment or Celebrity, even if
    # their submitted decision tried to. Defense in depth vs. UI validation. ---
    plant_blocked: dict[int, bool] = {}
    celebrity_blocked: dict[int, bool] = {}
    for fid in active_ids:
        d = decisions[fid]
        indebted = states[fid].loan_outstanding > 0
        plant_blocked[fid] = indebted and d.plant_investment > 0
        celebrity_blocked[fid] = indebted and d.celebrity_on
        if plant_blocked[fid]:
            d.plant_investment = 0
        if celebrity_blocked[fid]:
            d.celebrity_on = False

    # --- Step 3: this round's own R&D/Ad spend counts immediately (no lag,
    # unlike Plant Investment) toward this round's Quality/Ad level. ---
    quality_level: dict[int, int] = {}
    ad_level: dict[int, int] = {}
    ad_multiplier: dict[int, float] = {}
    for fid in active_ids:
        d = decisions[fid]
        s = states[fid]
        # A firm can climb at most MAX_QUALITY_LEVEL_GAIN_PER_ROUND levels in
        # one round, however much it spends. Spend beyond that still counts
        # toward cumulative_rd_spend, so it isn't burned -- it converts into
        # levels over the following rounds instead of all at once. The
        # submission route rejects an over-cap spend before it gets here; this
        # is the backstop that also binds bots and auto-decisions.
        level_before = quality_level_from_cumulative_rd(s.cumulative_rd_spend)
        level_after = quality_level_from_cumulative_rd(s.cumulative_rd_spend + d.rd_spend)
        quality_level[fid] = min(level_after, level_before + MAX_QUALITY_LEVEL_GAIN_PER_ROUND)
        lvl, mult = ad_level_and_multiplier(s.cumulative_ad_spend + d.ad_spend)
        ad_level[fid] = lvl
        ad_multiplier[fid] = mult

    # --- Step 4: each firm's demand-pull SCORE per segment -- Track
    # Preference x Quality Weight x Advertising x Celebrity ONLY. Price no
    # longer factors in here (the old elasticity-based price_multiplier is
    # gone entirely, per the Sept 2026 buyer-model redesign) -- it now only
    # gates WHICH firms a buyer can afford at all (Step 4b below), not how
    # appealing one affordable firm is versus another affordable one. ---
    demand_pull: dict[int, dict[str, float]] = {fid: {} for fid in active_ids}
    for fid in active_ids:
        d = decisions[fid]
        for seg in SEGMENTS:
            tpm = TRACK_PREFERENCE_MULTIPLIER[seg][d.track]
            qw = quality_weight(seg, quality_level[fid])
            cm = CELEBRITY_MULTIPLIER[seg] if d.celebrity_on else 1.0
            demand_pull[fid][seg] = tpm * qw * ad_multiplier[fid] * cm

    # --- Step 4b: individual buyer willingness-to-pay sweep -- REPLACES the
    # old "divide the full segment headcount proportionally regardless of
    # price" mechanic (former Steps 5-6). Per segment: each firm has an
    # r-threshold (0..1, a buyer-population percentile) at which it becomes
    # affordable to buyers at or above that percentile (see
    # constants.wtp_threshold_r). Sorting those thresholds partitions the
    # [0, 1] population into intervals within which the SET of affordable
    # firms is constant; within each interval, buyers split across just
    # that interval's affordable firms proportional to demand_pull -- the
    # exact same share formula as before, just scoped to a slice of the
    # population instead of the whole segment. Buyers below every firm's
    # threshold (an empty affordable set) buy nothing -- tracked as
    # unsold, not silently dropped, satisfying edge cases 13-14 below. ---
    raw_units: dict[int, dict[str, float]] = {fid: {seg: 0.0 for seg in SEGMENTS} for fid in active_ids}
    segment_stats: dict[str, SegmentDemandStats] = {}

    for seg in SEGMENTS:
        buyer_count = SEGMENT_BUYER_COUNT[seg]
        thresholds = []  # [(clamped_r, firm_id), ...]
        for fid in active_ids:
            d = decisions[fid]
            if seg == "Wealthy" and d.price > WEALTHY_CEILING_PRICE:
                continue  # absolute hard rule, kept from the old model -- excluded outright
            r = wtp_threshold_r(seg, d.track, d.price)
            thresholds.append((max(0.0, min(1.0, r)), fid))

        if not thresholds:
            # No firm is within reach of anyone here (or no firm competes
            # in this segment at all, e.g. every firm priced out of
            # Wealthy) -- 0 units for everyone, 100% unsold, no
            # divide-by-zero (edge case 14).
            segment_stats[seg] = SegmentDemandStats(
                segment=seg, total_buyers=buyer_count, unsold_buyers=buyer_count,
                unsold_buyers_pct=100.0 if buyer_count > 0 else 0.0, avg_consumer_surplus=None,
            )
            continue

        boundaries = sorted({0.0, 1.0} | {r for r, _ in thresholds})
        surplus_numerator = 0.0
        served_buyers = 0.0

        for lo, hi in zip(boundaries, boundaries[1:]):
            width = hi - lo
            if width <= 0:
                continue
            # Every threshold is exactly a boundary point by construction,
            # so the affordable set is constant throughout (lo, hi).
            affordable = [fid for r, fid in thresholds if r <= lo]
            if not affordable:
                continue  # this slice can't afford anyone yet (edge case 13: unsold)
            pulls = {fid: demand_pull[fid][seg] for fid in affordable}
            total_pull = sum(pulls.values())
            if total_pull <= 0:
                continue  # defensive -- TPM/Quality/Ad/Celebrity are always > 0 in practice
            interval_buyers = width * buyer_count
            served_buyers += interval_buyers
            for fid in affordable:
                share = pulls[fid] / total_pull
                raw_units[fid][seg] += interval_buyers * share
                d = decisions[fid]
                avg_ceiling = (wtp_ceiling_at_r(seg, d.track, lo) + wtp_ceiling_at_r(seg, d.track, hi)) / 2
                surplus_numerator += interval_buyers * share * (avg_ceiling - d.price)

        # Clamped at 0: summing each interval's share back up can land a
        # hair over buyer_count in floating point, which rendered as a
        # nonsensical "-0.0% unsold" on both dashboards and got persisted
        # that way. A segment can never have negative unsold buyers.
        unsold_buyers = max(0.0, buyer_count - served_buyers)
        segment_stats[seg] = SegmentDemandStats(
            segment=seg, total_buyers=buyer_count, unsold_buyers=unsold_buyers,
            unsold_buyers_pct=(unsold_buyers / buyer_count * 100) if buyer_count > 0 else 0.0,
            avg_consumer_surplus=(surplus_numerator / served_buyers) if served_buyers > 0 else None,
        )

    # --- Step 7: capacity-constrained scaling. Unmet demand is lost, not
    # redistributed to other firms (locked decision). ---
    actual_production: dict[int, int] = {}
    final_units: dict[int, dict[str, float]] = {}
    demanded_total: dict[int, float] = {}
    for fid in active_ids:
        d = decisions[fid]
        actual_production[fid] = max(0, min(d.production_qty, effective_capacity[fid]))
        total_raw = sum(raw_units[fid].values())
        demanded_total[fid] = total_raw
        if total_raw <= 0 or total_raw <= actual_production[fid]:
            final_units[fid] = dict(raw_units[fid])
        else:
            scale = actual_production[fid] / total_raw
            final_units[fid] = {seg: raw_units[fid][seg] * scale for seg in SEGMENTS}

    # --- Step 8: financials, loan/bankruptcy, capacity for next round. ---
    for fid in active_ids:
        s = states[fid]
        d = decisions[fid]
        units_sold_total = sum(final_units[fid].values())
        revenue = units_sold_total * d.price

        # Shortfall is only attributed to CAPACITY when capacity is what
        # actually bound -- i.e. the firm asked to build at least as much as
        # its plant allows. A firm that chose (or could only afford) a smaller
        # run has the same shortfall, but buying a bigger plant would not have
        # helped it, so telling it to expand would be actively wrong advice.
        shortfall = max(0.0, demanded_total[fid] - units_sold_total)
        capacity_bound = d.production_qty >= effective_capacity[fid]
        units_lost_to_capacity = shortfall if capacity_bound else 0.0

        unit_cost = track_unit_cost(d.track)
        production_cost = actual_production[fid] * unit_cost  # sunk even for unsold units -- they're destroyed
        fixed_cost = fixed_cost_for_capacity(effective_capacity[fid])
        ad_cost = d.ad_spend
        rd_cost = d.rd_spend
        celebrity_cost = CELEBRITY_COST_PER_ROUND if d.celebrity_on else 0.0
        plant_cost = d.plant_investment

        total_cost = production_cost + fixed_cost + ad_cost + rd_cost + celebrity_cost + plant_cost
        profit = revenue - total_cost
        cash_before = s.cash
        cash = cash_before + profit

        # Loan trigger / lifetime limit / bankruptcy.
        pre_existing_balance = s.loan_outstanding
        loan_taken = 0.0
        went_bankrupt = False
        if cash < 0:
            if not s.loan_used_ever:
                loan_taken = LOAN_AMOUNT
                cash += loan_taken
            else:
                went_bankrupt = True  # flat loan already used once -- no second loan, ever

        # Repayment + interest apply ONLY to the pre-existing balance -- a
        # loan taken this same round sits untouched until next round.
        if pre_existing_balance > 0:
            principal_paid = min(LOAN_REPAYMENT_PRINCIPAL, pre_existing_balance)
            remaining = pre_existing_balance - principal_paid
            interest_charged = remaining * LOAN_INTEREST_RATE
            cash -= principal_paid
            balance_from_pre_existing = remaining + interest_charged
        else:
            principal_paid = 0.0
            interest_charged = 0.0
            balance_from_pre_existing = 0.0

        loan_outstanding_after = balance_from_pre_existing + loan_taken
        loan_used_ever_after = s.loan_used_ever or (loan_taken > 0)

        new_capacity_gain = PLANT_INVESTMENT_CAPACITY_GAIN if d.plant_investment > 0 else 0

        results[fid] = FirmRoundResult(
            firm_id=fid,
            units_sold_by_segment=dict(final_units[fid]),
            units_sold_total=units_sold_total,
            revenue=revenue,
            production_cost=production_cost,
            fixed_cost=fixed_cost,
            ad_cost=ad_cost,
            rd_cost=rd_cost,
            celebrity_cost=celebrity_cost,
            plant_investment_cost=plant_cost,
            total_cost=total_cost,
            profit=profit,
            cash_before=cash_before,
            cash_after=cash,
            quality_level=quality_level[fid],
            ad_level=ad_level[fid],
            plant_capacity=effective_capacity[fid],
            new_pending_capacity_increase=new_capacity_gain,
            units_demanded_total=demanded_total[fid],
            units_lost_to_capacity=units_lost_to_capacity,
            loan_taken_this_round=loan_taken,
            loan_principal_paid=principal_paid,
            loan_interest_charged=interest_charged,
            loan_outstanding_after=loan_outstanding_after,
            went_bankrupt_this_round=went_bankrupt,
            is_bankrupt=False,
            is_auto=d.is_auto,
            plant_investment_blocked=plant_blocked[fid],
            celebrity_blocked=celebrity_blocked[fid],
        )
        # Surface the resolved loan_used_ever state via the same object for
        # the orchestration layer to persist (kept off the dataclass schema
        # above since it's identical to loan_taken_this_round > 0 or already
        # True -- callers can derive it, but attach it for convenience).
        results[fid].loan_used_ever_after = loan_used_ever_after  # type: ignore[attr-defined]

    # --- Already-bankrupt firms: frozen, zeroed, still get a row. ---
    for fid, s in states.items():
        if s.bankrupt:
            results[fid] = FirmRoundResult(
                firm_id=fid,
                units_sold_by_segment={seg: 0.0 for seg in SEGMENTS},
                cash_before=s.cash,
                cash_after=s.cash,
                quality_level=quality_level_from_cumulative_rd(s.cumulative_rd_spend),
                ad_level=ad_level_and_multiplier(s.cumulative_ad_spend)[0],
                plant_capacity=s.plant_capacity,
                loan_outstanding_after=s.loan_outstanding,
                is_bankrupt=True,
            )
            results[fid].loan_used_ever_after = s.loan_used_ever  # type: ignore[attr-defined]

    results.segment_stats = segment_stats
    return results
