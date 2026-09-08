import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import constants as c


# --------------------------------------------------------------------------- #
# Track costs
# --------------------------------------------------------------------------- #

def test_track_unit_cost():
    assert c.track_unit_cost("Budget") == 37.5
    assert c.track_unit_cost("Standard") == 50.0
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


# --------------------------------------------------------------------------- #
# Quality Weight interpolation
# --------------------------------------------------------------------------- #

def test_quality_weight_endpoints():
    assert c.quality_weight("Basketball Players", 1) == 0.7
    assert c.quality_weight("Basketball Players", 10) == 1.8
    assert c.quality_weight("Low Income", 1) == 1.0
    assert c.quality_weight("Low Income", 10) == 1.0  # flat, doesn't care


def test_quality_weight_midpoint_interpolation():
    # Basketball Players: 0.7 at Q1, 1.8 at Q10 -> at Q5: 0.7 + 1.1*(4/9)
    expected = 0.7 + (1.8 - 0.7) * (5 - 1) / 9
    assert math.isclose(c.quality_weight("Basketball Players", 5), expected)


# --------------------------------------------------------------------------- #
# Fixed cost ("Rent, Utilities & Labor") scaling with capacity
# --------------------------------------------------------------------------- #

def test_fixed_cost_at_base_capacity():
    assert c.fixed_cost_for_capacity(45_000) == 100_000


def test_fixed_cost_matches_given_examples():
    assert c.fixed_cost_for_capacity(60_000) == 115_000  # 1 expansion block
    assert c.fixed_cost_for_capacity(90_000) == 145_000  # 3 expansion blocks


# --------------------------------------------------------------------------- #
# Price multiplier -- standard segments (elasticity formula)
# --------------------------------------------------------------------------- #

def test_price_multiplier_at_base_cost_is_one():
    for seg in ("Low Income", "NBA Fans", "Basketball Players", "Casual/Fashion"):
        assert c.price_multiplier(seg, 50) == 1.0


def test_price_multiplier_matches_the_80_dollar_anchor_sanity_check():
    # Directly from the docs' own worked sanity check at the $80 Round-1 anchor.
    assert math.isclose(c.price_multiplier("Low Income", 80), 0.16, abs_tol=1e-9)
    assert math.isclose(c.price_multiplier("Casual/Fashion", 80), 0.40, abs_tol=1e-9)
    assert math.isclose(c.price_multiplier("Basketball Players", 80), 0.58, abs_tol=1e-9)
    assert math.isclose(c.price_multiplier("NBA Fans", 80), 0.82, abs_tol=1e-9)


def test_price_multiplier_floors_at_zero_for_extreme_overpricing():
    assert c.price_multiplier("Low Income", 1000) == 0.0


def test_price_multiplier_underpricing_is_a_bonus_not_capped_at_one():
    # Locked: pricing below the $50 base cost is NOT clamped to 1.0.
    mult = c.price_multiplier("Low Income", 40)  # 20% below base
    assert mult > 1.0
    expected = 1 - (-0.20) * 1.4
    assert math.isclose(mult, expected)


# --------------------------------------------------------------------------- #
# Wealthy segment piecewise price multiplier
# --------------------------------------------------------------------------- #

def test_wealthy_price_multiplier_matches_documented_shape():
    # The docs' shape table rounds to 2 decimals (e.g. exact math at $210 is
    # 0.70*(40/50)**2 = 0.448, shown there as "0.45") -- tolerance reflects
    # that rounding, not slack in the formula itself.
    assert math.isclose(c.price_multiplier("Wealthy", 200), 0.70, abs_tol=0.006)
    assert math.isclose(c.price_multiplier("Wealthy", 210), 0.45, abs_tol=0.006)
    assert math.isclose(c.price_multiplier("Wealthy", 225), 0.18, abs_tol=0.006)
    assert math.isclose(c.price_multiplier("Wealthy", 240), 0.03, abs_tol=0.006)
    assert math.isclose(c.price_multiplier("Wealthy", 250), 0.0, abs_tol=0.006)


def test_wealthy_price_above_ceiling_is_dead():
    assert c.price_multiplier("Wealthy", 251) == 0.0
    assert c.price_multiplier("Wealthy", 10_000) == 0.0


def test_wealthy_price_multiplier_continuous_at_the_200_seam():
    # Both branches should agree at exactly $200 (no discontinuity/bug at the seam).
    below = 1 - ((200 - 50) / 50) * 0.1
    above_formula_at_200 = 0.70 * ((250 - 200) / 50) ** 2
    assert math.isclose(below, above_formula_at_200, abs_tol=1e-9)
