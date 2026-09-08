"""
Locked economic model constants for the Business Simulation game.

Source of truth: master-variable-table.md, teacher-reference-guide.txt,
demand-formula-visual.txt, and the design-update messages that amended them
(Celebrity flat cost, loan repayment/lifetime-limit/bankruptcy, Plant
Investment binary toggle, Rent/Utilities/Labor capacity scaling).

Every number in this file should be traceable to one of those. Do not add
or infer a number that wasn't explicitly given -- if something is needed
that isn't here, that's a sign a design question is still open, not a cue
to guess a default.
"""

from __future__ import annotations

# --------------------------------------------------------------------------- #
# Section 1: Starting conditions
# --------------------------------------------------------------------------- #

STARTING_CASH = 1_000_000
STARTING_PLANT_CAPACITY = 45_000
STARTING_QUALITY_LEVEL = 1
BASE_UNIT_COST = 50.00  # Standard track, before track multiplier
ROUNDS_PER_WORLD = 10
FIRMS_PER_WORLD_MIN = 7
FIRMS_PER_WORLD_MAX = 8

# --------------------------------------------------------------------------- #
# App-level bootstrap defaults -- NOT part of the locked economic model docs.
# The locked non-submission rule (Section 9) carries forward a firm's own
# last price/track, but says nothing about a firm that never submits even
# once (no "last" value exists yet, e.g. it skips Round 1 entirely). Confirmed
# with the user as a flat platform fallback rather than an invented number
# quietly folded into the locked model above -- keep it here, visibly
# separate, so it's never mistaken for something the source docs specified.
# --------------------------------------------------------------------------- #
BOOTSTRAP_DEFAULT_PRICE = 80.00
BOOTSTRAP_DEFAULT_TRACK = "Standard"

# --------------------------------------------------------------------------- #
# Section 4a: Track cost multipliers
# --------------------------------------------------------------------------- #

TRACKS = ("Budget", "Standard", "Premium")

TRACK_COST_MULTIPLIER = {
    "Budget": 0.75,
    "Standard": 1.00,
    "Premium": 1.40,
}


def track_unit_cost(track: str) -> float:
    return BASE_UNIT_COST * TRACK_COST_MULTIPLIER[track]


# --------------------------------------------------------------------------- #
# Section 4c: Quality descriptor labels (Quality Level + Track -> display label)
# --------------------------------------------------------------------------- #

def quality_descriptor(quality_level: int) -> str:
    if quality_level <= 2:
        return "Low"
    if quality_level <= 4:
        return "Modest"
    if quality_level <= 6:
        return "Solid"
    if quality_level <= 8:
        return "High"
    return "Elite"


def quality_track_label(quality_level: int, track: str) -> str:
    return f"{quality_descriptor(quality_level)} Quality {track}"


# --------------------------------------------------------------------------- #
# Section 5: R&D / Quality ladder -- cumulative spend -> quality level (1-10)
# --------------------------------------------------------------------------- #

QUALITY_LADDER = (
    (1, 0),
    (2, 50_000),
    (3, 100_000),
    (4, 150_000),
    (5, 200_000),
    (6, 300_000),
    (7, 400_000),
    (8, 500_000),
    (9, 600_000),
    (10, 700_000),
)


def quality_level_from_cumulative_rd(cumulative_rd: float) -> int:
    """Step function: the highest level whose cumulative-spend threshold has
    been fully met. Caps at 10 -- spend beyond the level-10 threshold has no
    further effect (no per-round cap, per the master doc; price is the
    constraint, not a rule ceiling)."""
    level = 1
    for lvl, threshold in QUALITY_LADDER:
        if cumulative_rd >= threshold:
            level = lvl
    return level


def rd_spend_presets(cumulative_rd_spend: float) -> list[tuple[int, float]]:
    """Returns [(level, additional_spend_needed)] for every quality level
    still ABOVE the firm's current one -- additional_spend_needed is what
    they'd need to add THIS round, on top of what's already been spent, to
    cross that level's cumulative threshold. Used to offer the Firm
    Dashboard's R&D preset quick-fill options; already-reached levels are
    omitted since $0 more is needed for those. Empty list once a firm is
    already at Level 10 (nothing left to reach)."""
    current_level = quality_level_from_cumulative_rd(cumulative_rd_spend)
    return [
        (level, threshold - cumulative_rd_spend)
        for level, threshold in QUALITY_LADDER
        if level > current_level
    ]


# --------------------------------------------------------------------------- #
# Section 6: Advertising ladder -- cumulative spend -> level -> multiplier
# --------------------------------------------------------------------------- #

AD_LADDER = (
    (1, 0, 1.000),
    (2, 125_000, 1.180),
    (3, 225_000, 1.330),
    (4, 350_000, 1.450),
    (5, 500_000, 1.540),
    (6, 675_000, 1.610),
    (7, 875_000, 1.660),
    (8, 1_100_000, 1.690),
    (9, 1_350_000, 1.705),
    (10, 1_625_000, 1.705),  # the "trap" -- no gain over level 9
)


def ad_level_and_multiplier(cumulative_ad: float) -> tuple[int, float]:
    level, multiplier = 1, 1.000
    for lvl, threshold, mult in AD_LADDER:
        if cumulative_ad >= threshold:
            level, multiplier = lvl, mult
    return level, multiplier


def ad_spend_presets(cumulative_ad_spend: float) -> list[tuple[int, float]]:
    """Same idea as rd_spend_presets() but for the Advertising ladder --
    [(level, additional_spend_needed)] for every ad level still above the
    firm's current one. Deliberately does NOT flag the Level 10 "trap" here
    (same multiplier as Level 9 for more money) -- that's an intentional,
    secret diminishing-returns curve per Section 6, not something the UI
    should tip students off to."""
    current_level, _ = ad_level_and_multiplier(cumulative_ad_spend)
    return [
        (level, threshold - cumulative_ad_spend)
        for level, threshold, _ in AD_LADDER
        if level > current_level
    ]


# --------------------------------------------------------------------------- #
# Section 7: Fixed costs & capacity ("Rent, Utilities & Labor")
# --------------------------------------------------------------------------- #

BASE_FIXED_COST = 100_000            # covers the starting 45,000 capacity
CAPACITY_BLOCK_SIZE = 15_000         # units gained per expansion block
CAPACITY_BLOCK_FIXED_COST = 15_000   # extra $/round per block beyond base

# Plant Investment: binary choice each round, no larger preset tiers.
PLANT_INVESTMENT_COST = 100_000
PLANT_INVESTMENT_CAPACITY_GAIN = 15_000
PLANT_INVESTMENT_CHOICES = (0, PLANT_INVESTMENT_COST)


def fixed_cost_for_capacity(plant_capacity: int) -> float:
    """"Rent, Utilities & Labor" -- $100,000/round base + $15,000/round per
    15,000-unit expansion block beyond the starting 45,000 capacity.
    e.g. 60,000 -> $115,000; 90,000 -> $145,000."""
    extra_blocks = (plant_capacity - STARTING_PLANT_CAPACITY) / CAPACITY_BLOCK_SIZE
    extra_blocks = round(extra_blocks)  # defensive: capacity should only ever move in whole blocks
    return BASE_FIXED_COST + CAPACITY_BLOCK_FIXED_COST * max(0, extra_blocks)


# --------------------------------------------------------------------------- #
# Section 7b: Unsold inventory -- destroyed/scrapped, no rollover (LOCKED,
# master table overrides the teacher guide's stale "deferred" note). This has
# no constant of its own -- it's structural: the engine never persists a
# units-produced-but-unsold figure across rounds.
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Section 8 (as amended): Loan / bankruptcy mechanism
# --------------------------------------------------------------------------- #

LOAN_AMOUNT = 500_000                # flat, once per firm per world, ever
LOAN_REPAYMENT_PRINCIPAL = 100_000   # flat $/round while a PRE-EXISTING balance remains
LOAN_INTEREST_RATE = 0.10            # applied AFTER that round's repayment, to what's left

# Loan repayment/interest timing: a loan taken THIS round sits untouched (no
# repayment, no interest) until NEXT round, when it becomes a "pre-existing
# balance" like any other. Confirmed with the user; see engine.py.


# --------------------------------------------------------------------------- #
# Celebrity Endorsement (as amended: flat, recurring, no ladder)
# --------------------------------------------------------------------------- #

CELEBRITY_COST_PER_ROUND = 50_000


# --------------------------------------------------------------------------- #
# Section 12: Market segment model
# --------------------------------------------------------------------------- #

BUYER_POOL_BASE = 8_000
BUYER_POOL_SCALE_FACTOR = 51

SEGMENTS = ("Low Income", "NBA Fans", "Basketball Players", "Wealthy", "Casual/Fashion")

SEGMENT_BASE_COUNT = {
    "Low Income": 2_500,
    "NBA Fans": 1_500,
    "Basketball Players": 1_000,
    "Wealthy": 800,
    "Casual/Fashion": 2_200,
}

SEGMENT_BUYER_COUNT = {seg: count * BUYER_POOL_SCALE_FACTOR for seg, count in SEGMENT_BASE_COUNT.items()}

TRACK_PREFERENCE_MULTIPLIER = {
    "Low Income":         {"Budget": 1.3, "Standard": 0.9, "Premium": 0.5},
    "NBA Fans":           {"Budget": 0.5, "Standard": 0.9, "Premium": 1.4},
    "Basketball Players": {"Budget": 0.7, "Standard": 1.0, "Premium": 1.2},
    "Wealthy":            {"Budget": 0.4, "Standard": 0.8, "Premium": 1.5},
    "Casual/Fashion":     {"Budget": 0.8, "Standard": 1.3, "Premium": 0.9},
}

# (weight at Quality 1, weight at Quality 10) -- linearly interpolated between.
QUALITY_WEIGHT_ENDPOINTS = {
    "Low Income": (1.0, 1.0),
    "NBA Fans": (0.9, 1.3),
    "Basketball Players": (0.7, 1.8),
    "Wealthy": (0.8, 1.6),
    "Casual/Fashion": (0.95, 1.05),
}


def quality_weight(segment: str, quality_level: int) -> float:
    q1, q10 = QUALITY_WEIGHT_ENDPOINTS[segment]
    q = max(1, min(10, quality_level))
    return q1 + (q10 - q1) * (q - 1) / 9


CELEBRITY_MULTIPLIER = {
    "Low Income": 1.0,
    "NBA Fans": 1.5,
    "Basketball Players": 1.1,
    "Wealthy": 1.2,
    "Casual/Fashion": 1.0,
}

# Standard-segment elasticity coefficients (Wealthy uses its own piecewise rule).
ELASTICITY_COEFFICIENT = {
    "Low Income": 1.4,
    "NBA Fans": 0.3,
    "Basketball Players": 0.7,
    "Casual/Fashion": 1.0,
}

WEALTHY_CEILING_PRICE = 250
WEALTHY_FLAT_CUTOFF_PRICE = 200
WEALTHY_FLAT_COEFFICIENT = 0.1


def price_multiplier(segment: str, price: float) -> float:
    """Elasticity-based price multiplier. Pricing below the $50 base cost is
    NOT capped at 1.0 -- the formula is only floored at 0 on the high end
    (locked, confirmed against the exact formula given twice in the docs)."""
    if segment == "Wealthy":
        return _wealthy_price_multiplier(price)
    pct_above_base = (price - BASE_UNIT_COST) / BASE_UNIT_COST
    coefficient = ELASTICITY_COEFFICIENT[segment]
    return max(0.0, 1 - pct_above_base * coefficient)


def _wealthy_price_multiplier(price: float) -> float:
    if price > WEALTHY_CEILING_PRICE:
        return 0.0
    if price <= WEALTHY_FLAT_CUTOFF_PRICE:
        pct_above_base = (price - BASE_UNIT_COST) / BASE_UNIT_COST
        return max(0.0, 1 - pct_above_base * WEALTHY_FLAT_COEFFICIENT)
    # 200 < price <= 250: steeper squared taper to zero at the ceiling.
    return max(0.0, 0.70 * ((WEALTHY_CEILING_PRICE - price) / 50) ** 2)


# --------------------------------------------------------------------------- #
# Section 10: Competitive Intelligence Report -- visible vs. hidden fields
# --------------------------------------------------------------------------- #

COMPETITIVE_INTEL_VISIBLE_FIELDS = (
    "price", "track", "quality_level", "ad_spend", "units_sold", "market_share", "loan_flag",
)
COMPETITIVE_INTEL_HIDDEN_FIELDS = ("plant_capacity", "cash", "rd_spend")
