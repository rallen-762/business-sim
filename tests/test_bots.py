"""
Unit tests for app/bots.py -- pure functions, no DB/app needed, same as
engine.py's own tests. A FixedRng stand-in makes _noise() a no-op (uniform()
returns the exact midpoint of its range, i.e. 0 offset) so assertions can
check exact values instead of ranges wherever that's practical.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bots import (
    BOT_PROFILES,
    UNDERBIDDER_PRICE_FLOOR,
    UNDERBIDDER_START_PRICE,
    UNDERBIDDER_TRACK,
    _AD_LEVEL_9_THRESHOLD,
    decide,
)
from app.constants import AD_LADDER, BOOTSTRAP_DEFAULT_TRACK, TRACKS


class FixedRng:
    """uniform() returns the exact midpoint (no noise); choice() returns the
    first option; random() returns a settable fixed value."""

    def __init__(self, random_value=0.4):
        self._random_value = random_value

    def uniform(self, a, b):
        return (a + b) / 2

    def choice(self, seq):
        return seq[0]

    def random(self):
        return self._random_value


AD_LEVEL_10_THRESHOLD = next(t for lvl, t, _ in AD_LADDER if lvl == 10)


def _decide(profile, rng=None, **overrides):
    base = dict(
        firm_id=1, round_number=1, cash=1_000_000, capacity=45_000,
        cumulative_rd_spend=0, cumulative_ad_spend=0, loan_outstanding=0,
        last_price=None, last_profit=None,
    )
    base.update(overrides)
    return decide(profile, rng=rng or FixedRng(), **base)


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

def test_all_four_profiles_are_dispatchable():
    for profile in BOT_PROFILES:
        d = _decide(profile)
        assert d.firm_id == 1
        assert d.is_auto is True


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        _decide("not-a-real-profile")


# --------------------------------------------------------------------------- #
# Underbidder
# --------------------------------------------------------------------------- #

def test_underbidder_round_one_uses_start_price():
    d = _decide("underbidder", last_price=None, last_profit=None)
    assert d.price == UNDERBIDDER_START_PRICE
    # A cost leader sells the cheapest tier to build -- it used to undercut
    # everyone while paying Mid-tier unit costs.
    assert d.track == UNDERBIDDER_TRACK


def test_underbidder_lowers_price_after_a_profitable_round():
    d = _decide("underbidder", last_price=100.0, last_profit=5_000.0)
    assert d.price == pytest.approx(95.0)


def test_underbidder_raises_price_after_an_unprofitable_round():
    d = _decide("underbidder", last_price=100.0, last_profit=-5_000.0)
    assert d.price == pytest.approx(105.0)


def test_underbidder_raises_price_after_a_breakeven_round():
    d = _decide("underbidder", last_price=100.0, last_profit=0.0)
    assert d.price == pytest.approx(105.0)


def test_underbidder_price_never_drops_below_the_floor():
    d = _decide("underbidder", last_price=UNDERBIDDER_PRICE_FLOOR + 1, last_profit=1.0)
    assert d.price >= UNDERBIDDER_PRICE_FLOOR


def test_underbidder_never_spends_on_anything_but_price():
    for round_number in (1, 5, 10):
        d = _decide("underbidder", round_number=round_number, last_price=80.0, last_profit=1.0)
        assert d.ad_spend == 0.0
        assert d.rd_spend == 0.0
        assert d.celebrity_on is False
        assert d.plant_investment == 0
        assert d.track == UNDERBIDDER_TRACK


# --------------------------------------------------------------------------- #
# Marketing
# --------------------------------------------------------------------------- #

def test_marketing_ramps_ad_spend_toward_level_9():
    d = _decide("marketing", cumulative_ad_spend=0.0)
    assert 0 < d.ad_spend <= _AD_LEVEL_9_THRESHOLD / 6 + 1  # ~1/6th of the Level-9 target
    assert d.rd_spend == 0.0
    assert d.track == BOOTSTRAP_DEFAULT_TRACK


def test_marketing_never_crosses_into_the_level_10_trap():
    almost_there = _AD_LEVEL_9_THRESHOLD - 10_000
    d = _decide("marketing", cumulative_ad_spend=almost_there, cash=10_000_000)
    assert almost_there + d.ad_spend <= _AD_LEVEL_9_THRESHOLD
    assert almost_there + d.ad_spend < AD_LEVEL_10_THRESHOLD


def test_marketing_spends_nothing_more_once_at_level_9():
    d = _decide("marketing", cumulative_ad_spend=_AD_LEVEL_9_THRESHOLD)
    assert d.ad_spend == 0.0


def test_marketing_turns_on_celebrity_once_cash_comfortably_allows_it():
    d = _decide("marketing", cash=2_000_000, cumulative_ad_spend=_AD_LEVEL_9_THRESHOLD)
    assert d.celebrity_on is True


def test_marketing_skips_celebrity_when_cash_is_tight():
    d = _decide("marketing", cash=50_000, cumulative_ad_spend=_AD_LEVEL_9_THRESHOLD)
    assert d.celebrity_on is False


def test_marketing_skips_celebrity_while_indebted():
    d = _decide("marketing", cash=2_000_000, cumulative_ad_spend=_AD_LEVEL_9_THRESHOLD, loan_outstanding=1.0)
    assert d.celebrity_on is False


# --------------------------------------------------------------------------- #
# Elite
# --------------------------------------------------------------------------- #

def test_elite_stays_at_mid_tier_through_round_three():
    for round_number in (1, 2, 3):
        d = _decide("elite", round_number=round_number)
        assert d.track == "Mid"


def test_elite_moves_to_top_tier_from_round_four():
    for round_number in (4, 7, 10):
        d = _decide("elite", round_number=round_number)
        assert d.track == "Premium"


def test_elite_does_not_front_load_rd_in_round_one():
    d = _decide("elite", round_number=1, cumulative_rd_spend=0.0)
    # Full Level-10 target is $700,000 -- Round 1 should invest only a
    # fraction of that, not try to reach it immediately.
    assert 0 < d.rd_spend < 150_000


def test_elite_rd_spend_is_gated_by_cash_not_just_round_number():
    # A "bad start" -- almost no cash left despite being late-game, where
    # the round-number-only target would be huge.
    d = _decide("elite", round_number=9, cash=10_000.0, cumulative_rd_spend=0.0)
    assert d.rd_spend <= 10_000.0 * 0.5


def test_elite_only_considers_celebrity_from_round_seven():
    for round_number in (1, 3, 6):
        d = _decide("elite", round_number=round_number, cash=5_000_000)
        assert d.celebrity_on is False
    d = _decide("elite", round_number=7, cash=5_000_000)
    assert d.celebrity_on is True


def test_elite_never_touches_ad_spend_or_plant_investment():
    d = _decide("elite", round_number=8, cash=5_000_000)
    assert d.ad_spend == 0.0
    assert d.plant_investment == 0


# --------------------------------------------------------------------------- #
# Random
# --------------------------------------------------------------------------- #

def test_random_picks_a_valid_track():
    import random as real_random
    rng = real_random.Random(42)
    for _ in range(20):
        d = _decide("random", rng=rng)
        assert d.track in TRACKS


def test_random_price_and_spend_stay_within_bounds():
    import random as real_random
    rng = real_random.Random(7)
    cash = 1_000_000
    for _ in range(20):
        d = _decide("random", rng=rng, cash=cash)
        assert 30.0 <= d.price <= 150.0
        assert 0 <= d.rd_spend <= cash * 0.15 + 1
        assert 0 <= d.ad_spend <= cash * 0.15 + 1
        assert d.plant_investment == 0


def test_random_never_plans_more_spend_than_it_has():
    import random as real_random
    rng = real_random.Random(99)
    cash = 200_000
    for _ in range(20):
        d = _decide("random", rng=rng, cash=cash, capacity=45_000)
        celebrity = 50_000 if d.celebrity_on else 0
        assert d.rd_spend + d.ad_spend + celebrity <= cash + 1  # +1 for float rounding


def test_underbidder_never_prices_below_its_own_unit_cost():
    """Its rule cuts price every profitable round, so without a cost-aware
    floor it ratchets itself down to selling at a loss -- the old flat $15
    floor was below the unit cost it was actually paying."""
    from app.constants import track_unit_cost
    unit_cost = track_unit_cost(UNDERBIDDER_TRACK)
    price = 400.0
    for _ in range(60):  # far more cuts than a 10-round game could produce
        d = _decide("underbidder", last_price=price, last_profit=1.0)
        price = d.price
        assert price > unit_cost, f"priced {price} at or below unit cost {unit_cost}"
    assert price == UNDERBIDDER_PRICE_FLOOR  # settles on the floor, not below it
