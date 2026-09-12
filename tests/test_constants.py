import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants as c


# --------------------------------------------------------------------------- #
# Track costs
# --------------------------------------------------------------------------- #

def test_track_unit_cost():
    assert c.track_unit_cost("Entry") == 37.5
    assert c.track_unit_cost("Mid") == 50.0
    assert c.track_unit_cost("Premium") == 70.0


# --------------------------------------------------------------------------- #
# Quality (R&D) ladder
# --------------------------------------------------------------------------- #

def test_quality_ladder_exact_thresholds():
    assert c.quality_level_from_cumulative_rd(0) == 1
    assert c.quality_level_from_cumulative_rd(50_000) == 2
    assert c.quality_level_from_cumulative_rd(100_000) == 3
    assert c.quality_level_from_cumulative_rd(700_000) == 10


def test_quality_ladder_step_function_between_thresholds():
    # $75k is between the $50k (level 2) and $100k (level 3) thresholds --
    # should stay at level 2 (step function, no fractional interpolation).
    assert c.quality_level_from_cumulative_rd(75_000) == 2
    assert c.quality_level_from_cumulative_rd(99_999) == 2


def test_quality_ladder_caps_at_10_beyond_max_spend():
    assert c.quality_level_from_cumulative_rd(700_000) == 10
    assert c.quality_level_from_cumulative_rd(10_000_000) == 10


def test_rd_spend_presets_stop_at_the_per_round_level_cap():
    # A firm can only climb MAX_QUALITY_LEVEL_GAIN_PER_ROUND levels in one
    # round, so offering a preset for Level 10 from Level 1 would just invite
    # a team to buy levels the round can't give them.
    presets = c.rd_spend_presets(0)
    assert presets[0] == (2, 50_000)
    assert len(presets) == c.MAX_QUALITY_LEVEL_GAIN_PER_ROUND
    assert presets[-1][0] == 1 + c.MAX_QUALITY_LEVEL_GAIN_PER_ROUND


def test_max_rd_spend_this_round_lands_exactly_on_the_reachable_level():
    # From Level 1 (nothing spent), 3 levels up is Level 4 -> $150,000.
    assert c.max_rd_spend_this_round(0) == 150_000
    # From $150,000 (Level 4), 3 up is Level 7 -> $400,000 threshold,
    # i.e. $250,000 more.
    assert c.max_rd_spend_this_round(150_000) == 250_000
    # Near the top the cap is whatever remains, not 3 full levels.
    assert c.max_rd_spend_this_round(600_000) == 100_000  # Level 9 -> 10
    # At max quality there is no cap to state -- the field is locked instead.
    assert c.max_rd_spend_this_round(700_000) is None


def test_rd_spend_presets_only_shows_levels_above_current():
    # Already spent $150,000 -- sitting at Level 4, needs $50,000 MORE to
    # hit Level 5 ($200,000 threshold), not the full $200,000 again.
    presets = c.rd_spend_presets(150_000)
    assert presets[0] == (5, 50_000)
    assert all(level > 4 for level, _ in presets)


def test_rd_spend_presets_empty_once_at_level_10():
    assert c.rd_spend_presets(700_000) == []
    assert c.rd_spend_presets(50_000_000) == []


# --------------------------------------------------------------------------- #
# Advertising ladder
# --------------------------------------------------------------------------- #

def test_ad_ladder_levels_and_multipliers():
    assert c.ad_level_and_multiplier(0) == (1, 1.000)
    assert c.ad_level_and_multiplier(125_000) == (2, 1.180)
    assert c.ad_level_and_multiplier(1_350_000) == (9, 1.705)


def test_ad_ladder_level_10_is_the_trap_no_extra_gain():
    level9 = c.ad_level_and_multiplier(1_350_000)
    level10 = c.ad_level_and_multiplier(1_625_000)
    assert level9[1] == level10[1] == 1.705
    assert level9[0] == 9 and level10[0] == 10


def test_ad_ladder_caps_beyond_level_10():
    assert c.ad_level_and_multiplier(50_000_000) == (10, 1.705)


def test_ad_spend_presets_from_zero_lists_every_level_above_1():
    presets = c.ad_spend_presets(0)
    assert presets[0] == (2, 125_000)
    assert presets[-1] == (10, 1_625_000)
    assert len(presets) == 9


def test_ad_spend_presets_only_shows_levels_above_current():
    presets = c.ad_spend_presets(225_000)  # already at Level 3
    assert presets[0] == (4, 125_000)  # $350,000 - $225,000
    assert all(level > 3 for level, _ in presets)


def test_ad_spend_presets_empty_once_at_level_10():
    assert c.ad_spend_presets(1_625_000) == []


# --------------------------------------------------------------------------- #
# Quality Weight interpolation
# --------------------------------------------------------------------------- #

def test_quality_weight_endpoints():
    assert c.quality_weight("Athletes", 1) == 0.7
    assert c.quality_weight("Athletes", 10) == 1.8
    assert c.quality_weight("Low Income", 1) == 1.0
    assert c.quality_weight("Low Income", 10) == 1.0  # flat, doesn't care


def test_quality_weight_midpoint_interpolation():
    # Athletes: 0.7 at Q1, 1.8 at Q10 -> at Q5: 0.7 + 1.1*(4/9)
    expected = 0.7 + (1.8 - 0.7) * (5 - 1) / 9
    assert math.isclose(c.quality_weight("Athletes", 5), expected)


# --------------------------------------------------------------------------- #
# Fixed cost ("Rent, Utilities & Labor") scaling with capacity
# --------------------------------------------------------------------------- #

def test_fixed_cost_at_base_capacity():
    assert c.fixed_cost_for_capacity(45_000) == 100_000


def test_fixed_cost_matches_given_examples():
    assert c.fixed_cost_for_capacity(60_000) == 115_000  # 1 expansion block
    assert c.fixed_cost_for_capacity(90_000) == 145_000  # 3 expansion blocks


# --------------------------------------------------------------------------- #
# Willingness-to-pay ceilings (replaces the old elasticity price_multiplier)
# --------------------------------------------------------------------------- #

def test_wtp_threshold_r_at_the_low_end_of_the_spread_is_zero():
    # Mid/Low Income center is $68; the low end of a +/-20% spread is
    # exactly $68 * 0.8 = $54.40 -- priced there, even the LEAST generous
    # buyer can afford it (r == 0, i.e. the entire population affords it).
    r = c.wtp_threshold_r("Low Income", "Mid", 68 * c.WTP_SPREAD_LOW)
    assert math.isclose(r, 0.0, abs_tol=1e-9)


def test_wtp_threshold_r_at_the_high_end_of_the_spread_is_one():
    r = c.wtp_threshold_r("Low Income", "Mid", 68 * c.WTP_SPREAD_HIGH)
    assert math.isclose(r, 1.0, abs_tol=1e-9)


def test_wtp_threshold_r_below_the_spread_is_negative_everyone_affords_it():
    r = c.wtp_threshold_r("Wealthy", "Premium", 1.0)  # way below $230's low end
    assert r < 0


def test_wtp_threshold_r_above_the_spread_exceeds_one_nobody_affords_it():
    r = c.wtp_threshold_r("Wealthy", "Premium", 10_000)
    assert r > 1


def test_wtp_ceiling_at_r_is_the_inverse_of_wtp_threshold_r():
    for segment, track, price in [("NBA Fans", "Premium", 110), ("Casual/Fashion", "Entry", 78)]:
        r = c.wtp_threshold_r(segment, track, price)
        assert math.isclose(c.wtp_ceiling_at_r(segment, track, r), price)


def test_a_single_buyers_three_ceilings_are_always_ordered_budget_to_premium():
    # Every segment's Entry < Mid < Premium center means a buyer's own
    # three ceilings stay consistently ordered regardless of r (locked
    # design property, not just true at the centers).
    for segment in c.SEGMENTS:
        for r in (0.0, 0.37, 1.0):
            budget = c.wtp_ceiling_at_r(segment, "Entry", r)
            standard = c.wtp_ceiling_at_r(segment, "Mid", r)
            premium = c.wtp_ceiling_at_r(segment, "Premium", r)
            assert budget < standard < premium


def test_wtp_ceiling_centers_match_the_locked_baseline_table():
    assert c.WTP_CEILING_CENTER["Wealthy"] == {"Entry": 50, "Mid": 140, "Premium": 230}
    assert c.WTP_CEILING_CENTER["Athletes"] == {"Entry": 35, "Mid": 75, "Premium": 130}


def test_price_multiplier_and_elasticity_coefficient_are_gone():
    # The old elasticity-based formula was REPLACED, not kept alongside the
    # new affordability gate (confirmed with the user -- double-penalizing
    # price would result from keeping both).
    assert not hasattr(c, "price_multiplier")
    assert not hasattr(c, "ELASTICITY_COEFFICIENT")


def test_wealthy_hard_ceiling_price_constant_is_kept():
    # The $250 absolute cutoff itself survives the redesign -- only the old
    # smooth $200-250 taper curve is gone.
    assert c.WEALTHY_CEILING_PRICE == 250
