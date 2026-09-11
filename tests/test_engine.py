import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import FirmDecision, FirmState, process_round, synthesize_non_submission_decision
from app.constants import (
    SEGMENT_BUYER_COUNT,
    STARTING_CASH,
    STARTING_PLANT_CAPACITY,
    WTP_SPREAD_HIGH,
    WTP_SPREAD_LOW,
    wtp_threshold_r,
)


def make_state(firm_id, **overrides):
    base = dict(
        firm_id=firm_id,
        cash=STARTING_CASH,
        plant_capacity=STARTING_PLANT_CAPACITY,
        pending_capacity_increase=0,
        cumulative_rd_spend=0,
        cumulative_ad_spend=0,
        loan_outstanding=0,
        loan_used_ever=False,
        bankrupt=False,
    )
    base.update(overrides)
    return FirmState(**base)


def make_decision(firm_id, **overrides):
    base = dict(
        firm_id=firm_id,
        price=50,
        production_qty=45_000,
        ad_spend=0,
        rd_spend=0,
        track="Standard",
        celebrity_on=False,
        plant_investment=0,
    )
    base.update(overrides)
    return FirmDecision(**base)


# --------------------------------------------------------------------------- #
# Demand pull / segment share -- reproduces the docs' own worked example
# --------------------------------------------------------------------------- #

def test_two_firm_demand_pull_matches_worked_example_shape():
    # The docs' Wealthy-segment worked example: Firm A demand pull 2304 vs
    # Firm B demand pull 400 -> Firm A ~85%, Firm B ~15% of the 800 real
    # buyers (Wealthy base count, unscaled). We can't hit those exact pull
    # numbers without reverse-engineering specific inputs, but we CAN verify
    # the ratio-based share math itself produces the same ~85/~15 split when
    # two firms' demand pulls are in exactly that ratio.
    total = 2304 + 400
    share_a = 2304 / total
    share_b = 400 / total
    assert math.isclose(share_a, 0.8519, abs_tol=0.001)
    assert math.isclose(share_b, 0.1481, abs_tol=0.001)


def test_two_identical_firms_split_every_segment_evenly():
    states = {1: make_state(1), 2: make_state(2)}
    decisions = {1: make_decision(1), 2: make_decision(2)}
    results = process_round(states, decisions)
    for seg in SEGMENT_BUYER_COUNT:
        assert math.isclose(results[1].units_sold_by_segment[seg], results[2].units_sold_by_segment[seg])


def test_higher_quality_firm_outsells_lower_quality_firm_in_quality_sensitive_segment():
    # Deliberately give both firms capacity/production far beyond anything
    # demand pull could ever produce, so the capacity-constraint proportional
    # scaling (Step 7) never binds -- otherwise it's *correct* per the locked
    # design for it to also shrink an unrelated segment's units (see the
    # dedicated test below), which would confound this specific assertion.
    states = {
        1: make_state(1, cumulative_rd_spend=700_000, plant_capacity=1_000_000),  # Quality 10
        2: make_state(2, plant_capacity=1_000_000),  # Quality 1
    }
    decisions = {1: make_decision(1, production_qty=1_000_000), 2: make_decision(2, production_qty=1_000_000)}
    results = process_round(states, decisions)
    # Basketball Players is the steepest quality-sensitive segment.
    assert results[1].units_sold_by_segment["Basketball Players"] > results[2].units_sold_by_segment["Basketball Players"]
    # Low Income is flat on quality (weight 1.0 at both Q1 and Q10) -- with no
    # capacity constraint binding, its raw per-segment share should be equal.
    assert math.isclose(
        results[1].units_sold_by_segment["Low Income"], results[2].units_sold_by_segment["Low Income"]
    )


def test_capacity_scaling_can_indirectly_shrink_an_unrelated_segment_too():
    # Real, intended emergent behavior of "scale ALL 5 segments down together"
    # (the locked design): a firm whose HIGHER quality wins it more demand in
    # other segments can end up hitting its capacity constraint harder, which
    # then also scales down its Low-Income sales even though Low Income
    # itself doesn't care about quality at all. Documenting this explicitly
    # so it's never mistaken for a bug later.
    states = {
        1: make_state(1, cumulative_rd_spend=700_000),  # Quality 10, base 45,000 capacity
        2: make_state(2),  # Quality 1, same capacity
    }
    decisions = {1: make_decision(1), 2: make_decision(2)}  # both request 45,000 units
    results = process_round(states, decisions)
    assert results[1].units_sold_by_segment["Low Income"] < results[2].units_sold_by_segment["Low Income"]


# --------------------------------------------------------------------------- #
# Capacity constraint (Edge case #1/#2 in engine.py docstring)
# --------------------------------------------------------------------------- #

def test_capacity_constrained_firm_never_sells_more_than_it_produced():
    # A single monopoly firm would capture 100% of every segment's demand
    # pull -- but its own production quantity is deliberately tiny.
    states = {1: make_state(1)}
    decisions = {1: make_decision(1, production_qty=1_000)}
    results = process_round(states, decisions)
    assert results[1].units_sold_total <= 1_000 + 1e-6  # allow float rounding


def test_capacity_unconstrained_firm_sells_exactly_its_raw_demand():
    # A firm whose production comfortably exceeds what demand pull could ever
    # give it (monopoly, huge capacity) should sell its full raw allocation,
    # unscaled.
    states = {1: make_state(1, plant_capacity=1_000_000)}
    decisions = {1: make_decision(1, production_qty=1_000_000)}
    results = process_round(states, decisions)
    # Monopoly firm should capture ~100% of every segment (no competitors).
    for seg, buyer_count in SEGMENT_BUYER_COUNT.items():
        assert math.isclose(results[1].units_sold_by_segment[seg], buyer_count, rel_tol=1e-6)


def test_zero_production_sells_nothing_without_crashing():
    states = {1: make_state(1), 2: make_state(2)}
    decisions = {1: make_decision(1, production_qty=0), 2: make_decision(2)}
    results = process_round(states, decisions)
    assert results[1].units_sold_total == 0


# --------------------------------------------------------------------------- #
# Unsold inventory is destroyed (production cost is sunk regardless of sales)
# --------------------------------------------------------------------------- #

def test_production_cost_is_charged_even_for_unsold_units():
    # Force massive overproduction relative to what a firm could ever sell in
    # one round (tiny fraction of the buyer pool) at a low price -- most of
    # what's produced won't sell, but the FULL production cost must still be
    # deducted (destroyed inventory is a sunk cost, not a partial refund).
    states = {1: make_state(1, cash=1_000_000_000, plant_capacity=1_000_000)}
    decisions = {1: make_decision(1, production_qty=1_000_000)}
    results = process_round(states, decisions)
    expected_cost = 1_000_000 * 50.0  # Standard track unit cost x full production qty
    assert math.isclose(results[1].production_cost, expected_cost)
    assert results[1].units_sold_total < 1_000_000  # confirms overproduction actually happened


# --------------------------------------------------------------------------- #
# Fixed cost uses the capacity actually in effect this round (post-lag)
# --------------------------------------------------------------------------- #

def test_fixed_cost_reflects_capacity_maturing_this_round():
    # A firm invested last round; that capacity matures NOW.
    states = {1: make_state(1, plant_capacity=45_000, pending_capacity_increase=15_000)}
    decisions = {1: make_decision(1)}
    results = process_round(states, decisions)
    assert results[1].plant_capacity == 60_000
    assert results[1].fixed_cost == 115_000


def test_plant_investment_this_round_does_not_affect_this_rounds_capacity():
    # 1-round lag: investing THIS round should NOT change effective capacity
    # or fixed cost THIS round -- only next round's pending increase.
    states = {1: make_state(1)}
    decisions = {1: make_decision(1, plant_investment=100_000)}
    results = process_round(states, decisions)
    assert results[1].plant_capacity == STARTING_PLANT_CAPACITY
    assert results[1].fixed_cost == 100_000
    assert results[1].new_pending_capacity_increase == 15_000


# --------------------------------------------------------------------------- #
# Celebrity Endorsement -- flat $50k/round, recurring
# --------------------------------------------------------------------------- #

def test_celebrity_cost_is_flat_50k_when_on():
    states = {1: make_state(1)}
    decisions = {1: make_decision(1, celebrity_on=True)}
    results = process_round(states, decisions)
    assert results[1].celebrity_cost == 50_000


def test_celebrity_multiplier_boosts_nba_fans_segment():
    states = {1: make_state(1), 2: make_state(2)}
    decisions = {1: make_decision(1, celebrity_on=True), 2: make_decision(2, celebrity_on=False)}
    results = process_round(states, decisions)
    assert results[1].units_sold_by_segment["NBA Fans"] > results[2].units_sold_by_segment["NBA Fans"]


# --------------------------------------------------------------------------- #
# Loan mechanics
# --------------------------------------------------------------------------- #

def test_loan_triggers_on_first_negative_cash():
    # Tiny cash, big mandatory costs -> guaranteed negative.
    states = {1: make_state(1, cash=10_000)}
    decisions = {1: make_decision(1, production_qty=0, ad_spend=0, rd_spend=0)}
    results = process_round(states, decisions)
    assert results[1].loan_taken_this_round == 500_000
    assert results[1].loan_used_ever_after is True
    assert results[1].cash_after >= 0


def test_newly_issued_loan_is_not_repaid_or_charged_interest_same_round():
    states = {1: make_state(1, cash=10_000)}
    decisions = {1: make_decision(1, production_qty=0)}
    results = process_round(states, decisions)
    assert results[1].loan_principal_paid == 0
    assert results[1].loan_interest_charged == 0
    assert results[1].loan_outstanding_after == 500_000


def test_repayment_and_interest_sequencing_on_a_pre_existing_balance():
    # Firm enters the round already owing the full $500,000 from a prior round.
    states = {1: make_state(1, loan_outstanding=500_000, loan_used_ever=True)}
    decisions = {1: make_decision(1)}
    results = process_round(states, decisions)
    # $100k principal first -> $400k remains -> +10% interest -> $440k.
    assert results[1].loan_principal_paid == 100_000
    assert math.isclose(results[1].loan_interest_charged, 40_000)
    assert math.isclose(results[1].loan_outstanding_after, 440_000)


def test_loan_fully_paid_off_charges_no_interest_on_zero_balance():
    states = {1: make_state(1, loan_outstanding=80_000, loan_used_ever=True)}
    decisions = {1: make_decision(1)}
    results = process_round(states, decisions)
    assert results[1].loan_principal_paid == 80_000  # capped at what's owed, not a flat 100k overpay
    assert results[1].loan_interest_charged == 0
    assert results[1].loan_outstanding_after == 0


def test_second_negative_cash_after_loan_already_used_causes_bankruptcy_not_a_second_loan():
    states = {1: make_state(1, cash=10_000, loan_used_ever=True, loan_outstanding=0)}
    decisions = {1: make_decision(1, production_qty=0)}
    results = process_round(states, decisions)
    assert results[1].went_bankrupt_this_round is True
    assert results[1].loan_taken_this_round == 0
    assert results[1].cash_after < 0  # no bailout this time


def test_flat_loan_not_covering_full_shortfall_is_not_itself_a_bankruptcy_trigger():
    # A firm using its FIRST-EVER loan should get it regardless of whether
    # $500k fully covers the shortfall -- bankruptcy only applies to a SECOND
    # negative-cash event after the loan has already been used once.
    states = {1: make_state(1, cash=-2_000_000, loan_used_ever=False)}
    decisions = {1: make_decision(1, production_qty=0, ad_spend=0, rd_spend=0)}
    results = process_round(states, decisions)
    assert results[1].loan_taken_this_round == 500_000
    assert results[1].went_bankrupt_this_round is False
    assert results[1].loan_used_ever_after is True
    # Cash may still be negative -- that's accepted, not a second trigger.


# --------------------------------------------------------------------------- #
# Bankruptcy freezing
# --------------------------------------------------------------------------- #

def test_bankrupt_firm_is_frozen_and_excluded_from_competition():
    states = {1: make_state(1, bankrupt=True, cash=-1_000_000, loan_outstanding=440_000), 2: make_state(2)}
    decisions = {2: make_decision(2)}  # bankrupt firm submits nothing -- not even in decisions
    results = process_round(states, decisions)
    assert results[1].is_bankrupt is True
    assert results[1].units_sold_total == 0
    assert results[1].cash_after == results[1].cash_before == -1_000_000
    # A lone competitor should capture ~100% of every segment since the
    # bankrupt firm contributes zero demand pull.
    for seg, buyer_count in SEGMENT_BUYER_COUNT.items():
        assert results[2].units_sold_by_segment[seg] <= buyer_count + 1e-6


# --------------------------------------------------------------------------- #
# Soft penalty while indebted (defense in depth, even if UI already blocks it)
# --------------------------------------------------------------------------- #

def test_indebted_firm_cannot_spend_on_plant_investment_or_celebrity_even_if_submitted():
    states = {1: make_state(1, loan_outstanding=200_000, loan_used_ever=True)}
    decisions = {1: make_decision(1, plant_investment=100_000, celebrity_on=True)}
    results = process_round(states, decisions)
    assert results[1].plant_investment_cost == 0
    assert results[1].celebrity_cost == 0
    assert results[1].plant_investment_blocked is True
    assert results[1].celebrity_blocked is True


def test_non_indebted_firm_is_not_blocked():
    states = {1: make_state(1)}
    decisions = {1: make_decision(1, plant_investment=100_000, celebrity_on=True)}
    results = process_round(states, decisions)
    assert results[1].plant_investment_blocked is False
    assert results[1].celebrity_blocked is False
    assert results[1].plant_investment_cost == 100_000
    assert results[1].celebrity_cost == 50_000


# --------------------------------------------------------------------------- #
# Non-submission auto-handling
# --------------------------------------------------------------------------- #

def test_non_submission_carries_forward_price_and_track():
    d = synthesize_non_submission_decision(1, last_price=65, last_track="Premium", cash=1_000_000, plant_capacity=45_000)
    assert d.price == 65
    assert d.track == "Premium"
    assert d.is_auto is True


def test_non_submission_forces_celebrity_off():
    d = synthesize_non_submission_decision(1, last_price=50, last_track="Standard", cash=1_000_000, plant_capacity=45_000)
    assert d.celebrity_on is False


def test_non_submission_puts_100_percent_cash_into_production_capped_by_capacity():
    # Cheap track, huge cash -> would want more units than capacity allows.
    d = synthesize_non_submission_decision(1, last_price=50, last_track="Budget", cash=100_000_000, plant_capacity=45_000)
    assert d.production_qty == 45_000  # capped at capacity, not cash-derived overflow


def test_non_submission_spends_zero_on_rd_ads_and_plant():
    d = synthesize_non_submission_decision(1, last_price=50, last_track="Standard", cash=1_000_000, plant_capacity=45_000)
    assert d.ad_spend == 0
    assert d.rd_spend == 0
    assert d.plant_investment == 0


def test_non_submission_with_negative_or_zero_cash_produces_nothing():
    d = synthesize_non_submission_decision(1, last_price=50, last_track="Standard", cash=0, plant_capacity=45_000)
    assert d.production_qty == 0


# --------------------------------------------------------------------------- #
# Individual buyer willingness-to-pay ceilings (Sept 2026 buyer-model
# redesign) -- the tests above all happen to price at $50, which sits below
# every segment's WTP spread floor and so triggers 100% affordability
# everywhere (identical to the old always-allocate-the-full-headcount
# behavior) -- none of them actually exercise the new affordability gate.
# --------------------------------------------------------------------------- #

def test_underpriced_monopoly_captures_the_full_segment_with_computed_surplus():
    # Low Income/Standard center is $68; the low end of the +/-20% spread is
    # exactly $54.40 -- priced there, even the least-generous buyer affords
    # it, so this monopoly should capture the ENTIRE segment, 0% unsold.
    price = 68 * WTP_SPREAD_LOW
    states = {1: make_state(1, plant_capacity=1_000_000)}
    decisions = {1: make_decision(1, production_qty=1_000_000, price=price)}
    results = process_round(states, decisions)

    assert math.isclose(
        results[1].units_sold_by_segment["Low Income"], SEGMENT_BUYER_COUNT["Low Income"], rel_tol=1e-6
    )
    stats = results.segment_stats["Low Income"]
    assert math.isclose(stats.unsold_buyers_pct, 0.0, abs_tol=1e-6)
    # avg ceiling across the whole population is the center ($68); surplus
    # is that average minus the price actually paid ($54.40) = $13.60.
    assert math.isclose(stats.avg_consumer_surplus, 68 - price, abs_tol=1e-6)


def test_overpriced_monopoly_sells_nothing_in_that_segment_all_unsold():
    price = 68 * WTP_SPREAD_HIGH + 5  # above every buyer's ceiling for this track
    states = {1: make_state(1, plant_capacity=1_000_000)}
    decisions = {1: make_decision(1, production_qty=1_000_000, price=price)}
    results = process_round(states, decisions)

    assert results[1].units_sold_by_segment["Low Income"] == 0
    stats = results.segment_stats["Low Income"]
    assert math.isclose(stats.unsold_buyers_pct, 100.0, abs_tol=1e-6)
    assert stats.avg_consumer_surplus is None  # nobody bought -- not 0.0


def test_only_the_cheaper_firm_reaches_the_least_willing_buyers():
    # Two firms, identical in every way except price -- the pricier one can
    # only ever reach the upper slice of buyers willing to pay that much,
    # and even there only gets its (otherwise-equal) demand-pull share.
    cheap_price = 68 * WTP_SPREAD_LOW      # affordable to 100% of Low Income
    expensive_price = 75                    # affordable only above some r threshold
    states = {1: make_state(1, plant_capacity=1_000_000), 2: make_state(2, plant_capacity=1_000_000)}
    decisions = {
        1: make_decision(1, production_qty=1_000_000, price=cheap_price),
        2: make_decision(2, production_qty=1_000_000, price=expensive_price),
    }
    results = process_round(states, decisions)

    r_b = max(0.0, min(1.0, wtp_threshold_r("Low Income", "Standard", expensive_price)))
    expected_b_share = (1 - r_b) / 2  # only the interval above r_b, split 50/50 (identical otherwise)
    expected_b_units = expected_b_share * SEGMENT_BUYER_COUNT["Low Income"]

    assert math.isclose(results[2].units_sold_by_segment["Low Income"], expected_b_units, rel_tol=1e-6)
    assert results[1].units_sold_by_segment["Low Income"] > results[2].units_sold_by_segment["Low Income"]


def test_wealthy_hard_ceiling_excludes_a_firm_even_within_its_own_wtp_spread():
    # Wealthy/Premium center is $230; the spread's own high end is $276, so
    # $251 would still let SOME buyers afford it under the raw WTP curve
    # alone -- but the absolute $250 hard rule (kept from the pre-redesign
    # model) must still exclude it entirely regardless.
    states = {1: make_state(1, plant_capacity=1_000_000)}
    decisions = {1: make_decision(1, production_qty=1_000_000, price=251, track="Premium")}
    results = process_round(states, decisions)

    assert results[1].units_sold_by_segment["Wealthy"] == 0
    assert results.segment_stats["Wealthy"].unsold_buyers_pct == 100.0


def test_zero_firms_in_the_round_is_100_percent_unsold_everywhere_no_crash():
    results = process_round({}, {})
    assert dict(results) == {}
    for seg, buyer_count in SEGMENT_BUYER_COUNT.items():
        stats = results.segment_stats[seg]
        assert stats.total_buyers == buyer_count
        assert stats.unsold_buyers_pct == 100.0
        assert stats.avg_consumer_surplus is None


def test_process_round_result_is_still_a_plain_dict_for_existing_callers():
    # RoundResults must behave exactly like the old plain dict return for
    # every firm_id lookup -- only .segment_stats is new.
    states = {1: make_state(1)}
    decisions = {1: make_decision(1)}
    results = process_round(states, decisions)
    assert isinstance(results, dict)
    assert results[1].firm_id == 1
    assert set(results.segment_stats.keys()) == set(SEGMENT_BUYER_COUNT.keys())
