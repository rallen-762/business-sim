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
BASE_UNIT_COST = 50.00  # Mid tier, before tier multiplier
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
BOOTSTRAP_DEFAULT_TRACK = "Mid"

# --------------------------------------------------------------------------- #
# Section 4a: Track/Tier cost multipliers
#
# Headphone Company Simulator reskin (confirmed with the user): this is a
# pure label swap -- Budget/Standard/Premium became Entry/Mid/Premium, same
# 3 tiers, same multipliers/WTP ceilings/preference weights below, nothing
# renumbered. The Python identifier names here (TRACKS, track_unit_cost(),
# etc.) deliberately keep the word "track" -- the user asked to flag rather
# than silently rename anything where "track" is embedded in a variable/
# field name (this is also the literal DB column name on RoundDecision/Firm),
# so only the STRING VALUES changed, not the identifiers that hold them.
# --------------------------------------------------------------------------- #

TRACKS = ("Entry", "Mid", "Premium")

TRACK_COST_MULTIPLIER = {
    "Entry": 0.75,
    "Mid": 1.00,
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


# A firm can climb at most this many Quality Levels in a single round.
# Without it, a Round-1 firm with enough cash could buy its way from Level
# 1 straight to Level 10 in one move, which made R&D a single up-front
# purchase rather than an ongoing strategic choice. Confirmed with the
# user. Enforced in three places: the presets below stop offering
# unreachable levels, firm.submit_decision rejects an over-cap spend
# outright (so nobody's money is silently wasted), and engine.process_round
# caps the level gain regardless of what reaches it.
MAX_QUALITY_LEVEL_GAIN_PER_ROUND = 3


def max_rd_spend_this_round(cumulative_rd_spend: float) -> float | None:
    """The most R&D a firm may submit this round -- the spend that lands
    exactly on its highest reachable level. None means "no cap applies"
    (already at max quality, so rd_spend_presets is empty anyway and the
    UI locks the field)."""
    current_level = quality_level_from_cumulative_rd(cumulative_rd_spend)
    target_level = min(QUALITY_LADDER[-1][0], current_level + MAX_QUALITY_LEVEL_GAIN_PER_ROUND)
    if target_level <= current_level:
        return None
    target_threshold = next(t for lvl, t in QUALITY_LADDER if lvl == target_level)
    return max(0.0, target_threshold - cumulative_rd_spend)


def rd_spend_presets(cumulative_rd_spend: float) -> list[tuple[int, float]]:
    """Returns [(level, additional_spend_needed)] for every quality level
    still ABOVE the firm's current one -- additional_spend_needed is what
    they'd need to add THIS round, on top of what's already been spent, to
    cross that level's cumulative threshold. Used to offer the Firm
    Dashboard's R&D preset quick-fill options; already-reached levels are
    omitted since $0 more is needed for those. Empty list once a firm is
    already at Level 10 (nothing left to reach).

    Capped at MAX_QUALITY_LEVEL_GAIN_PER_ROUND levels ahead -- offering a
    preset for a level the round can't actually reach would just be an
    invitation to waste money."""
    current_level = quality_level_from_cumulative_rd(cumulative_rd_spend)
    highest_offerable = current_level + MAX_QUALITY_LEVEL_GAIN_PER_ROUND
    return [
        (level, threshold - cumulative_rd_spend)
        for level, threshold in QUALITY_LADDER
        if current_level < level <= highest_offerable
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

SEGMENTS = ("Low Income", "NBA Fans", "Athletes", "Wealthy", "Casual/Fashion")

# --------------------------------------------------------------------------- #
# Display labels for the segments above.
#
# The SEGMENTS values are NOT just display text -- they are dict keys in
# every per-segment table below (SEGMENT_BASE_COUNT, TRACK_PREFERENCE_
# MULTIPLIER, QUALITY_WEIGHT_ENDPOINTS, CELEBRITY_MULTIPLIER,
# WTP_CEILING_CENTER) AND they are persisted: SegmentRoundResult.segment
# holds them verbatim, and they are the JSON keys inside
# RoundResult.units_sold_by_segment for every round ever played.
#
# So renaming the values themselves means a data migration over live game
# history (that's what the earlier Basketball Players -> Athletes rename
# needed). This layer instead maps key -> what students and the teacher
# actually read, which is what a label rename really wants: zero migration,
# zero risk to past rounds, and the model keeps its stable internal keys.
# Use segment_label() anywhere a segment name is shown to a human.
# --------------------------------------------------------------------------- #

SEGMENT_DISPLAY_NAMES = {
    "Low Income": "Budget Shoppers",
    "NBA Fans": "Music Enthusiasts",
    "Athletes": "Fitness/Active Users",
    "Wealthy": "Wealthy",
    "Casual/Fashion": "Casual/Style-Conscious",
}


def segment_label(segment: str) -> str:
    """Human-facing name for an internal segment key. Falls back to the key
    itself so an unmapped or historical segment still renders readably
    instead of blanking out."""
    return SEGMENT_DISPLAY_NAMES.get(segment, segment)

# "Basketball Players" -> "Athletes" (confirmed as part of the basketball-
# terminology sweep). Same segment, same buyer counts/weights/ceilings
# below -- just a label swap. "NBA Fans" was left as-is: it isn't one of
# the words the user asked to sweep (shoe/sneaker/basketball/track/title),
# and "NBA Fans buying headphones" still reads fine as a segment identity.
SEGMENT_BASE_COUNT = {
    "Low Income": 2_500,
    "NBA Fans": 1_500,
    "Athletes": 1_000,
    "Wealthy": 800,
    "Casual/Fashion": 2_200,
}

SEGMENT_BUYER_COUNT = {seg: count * BUYER_POOL_SCALE_FACTOR for seg, count in SEGMENT_BASE_COUNT.items()}

TRACK_PREFERENCE_MULTIPLIER = {
    "Low Income":     {"Entry": 1.3, "Mid": 0.9, "Premium": 0.5},
    "NBA Fans":       {"Entry": 0.5, "Mid": 0.9, "Premium": 1.4},
    "Athletes":       {"Entry": 0.7, "Mid": 1.0, "Premium": 1.2},
    "Wealthy":        {"Entry": 0.4, "Mid": 0.8, "Premium": 1.5},
    "Casual/Fashion": {"Entry": 0.8, "Mid": 1.3, "Premium": 0.9},
}

# (weight at Quality 1, weight at Quality 10) -- linearly interpolated between.
QUALITY_WEIGHT_ENDPOINTS = {
    "Low Income": (1.0, 1.0),
    "NBA Fans": (0.9, 1.3),
    "Athletes": (0.7, 1.8),
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
    "Athletes": 1.1,
    "Wealthy": 1.2,
    "Casual/Fashion": 1.0,
}

WEALTHY_CEILING_PRICE = 250  # absolute hard rule: priced above this, excluded from Wealthy entirely (kept from the old model)


# --------------------------------------------------------------------------- #
# Individual buyer willingness-to-pay ceilings (Sept 2026 buyer-model
# redesign) -- REPLACES the old price_multiplier()/ELASTICITY_COEFFICIENT
# smooth elasticity formula entirely. Confirmed with the user: keeping both
# would double-penalize higher prices, since affordability now does that
# job structurally instead of a separate continuous multiplier.
#
# Model: each buyer has ONE underlying percentile r, uniform on [0, 1],
# shared across all three of their own Budget/Standard/Premium ceilings for
# their segment -- NOT three independent random draws. A buyer generally
# willing to pay more sits at the same percentile on every track's ceiling,
# so ceiling(track, r) = center * (WTP_SPREAD_LOW + (WTP_SPREAD_HIGH -
# WTP_SPREAD_LOW) * r) is linear and INCREASING in r for a fixed track, and
# -- since every segment's Budget < Standard < Premium centers below are
# already ordered that way -- a single buyer's three ceilings never cross
# each other regardless of r (their Premium ceiling always exceeds their
# own Standard/Budget ceilings).
#
# This makes "affordable fraction of a segment" an exact closed-form
# fraction of buyers with r above a threshold, rather than something that
# needs literally simulating ~400,000 individual buyers -- confirmed with
# the user as the computation approach over true Monte Carlo simulation,
# to keep this fully deterministic (no new randomness, reproducible
# balance-testing trials) and avoid adding a numeric/array dependency this
# project has never needed before.
# --------------------------------------------------------------------------- #

WTP_CEILING_CENTER = {
    "Low Income":     {"Entry": 65, "Mid": 68, "Premium": 70},
    "NBA Fans":       {"Entry": 90, "Mid": 105, "Premium": 120},
    "Athletes":       {"Entry": 35, "Mid": 75, "Premium": 130},
    "Wealthy":        {"Entry": 50, "Mid": 140, "Premium": 230},
    "Casual/Fashion": {"Entry": 75, "Mid": 85, "Premium": 90},
}

# +/-20% uniform spread around each center above -- confirmed with the user
# (the spec gave center values but no spread; this is the platform's choice
# of how much buyer-to-buyer heterogeneity to model).
WTP_SPREAD_LOW = 0.8
WTP_SPREAD_HIGH = 1.2


def wtp_threshold_r(segment: str, track: str, price: float) -> float:
    """The percentile r at which a buyer's willingness-to-pay ceiling for
    `track` first clears `price` -- the fraction (1 - this, clamped to
    [0, 1] by the caller) of the segment's buyers can afford it.
    Deliberately NOT clamped here: a price above every buyer's ceiling
    yields r > 1 (nobody ever affords it), a price at/below the least
    generous buyer's ceiling yields r <= 0 (everybody affords it) -- both
    still carry meaningful ORDERING information relative to other firms'
    thresholds for a caller doing a multi-firm r-space sweep (see
    engine.py's segment demand step), so clamping belongs there, not here.
    """
    center = WTP_CEILING_CENTER[segment][track]
    ceiling_at_r0 = center * WTP_SPREAD_LOW
    ceiling_at_r1 = center * WTP_SPREAD_HIGH
    return (price - ceiling_at_r0) / (ceiling_at_r1 - ceiling_at_r0)


def wtp_ceiling_at_r(segment: str, track: str, r: float) -> float:
    """A buyer's actual willingness-to-pay ceiling at percentile r (0..1)
    for `track` in `segment` -- the inverse of wtp_threshold_r, used to
    compute consumer surplus (ceiling - price paid) for buyers landing at
    a given r."""
    center = WTP_CEILING_CENTER[segment][track]
    return center * (WTP_SPREAD_LOW + (WTP_SPREAD_HIGH - WTP_SPREAD_LOW) * r)


# --------------------------------------------------------------------------- #
# Section 10: Competitive Intelligence Report -- visible vs. hidden fields
# --------------------------------------------------------------------------- #

COMPETITIVE_INTEL_VISIBLE_FIELDS = (
    "price", "track", "quality_level", "ad_spend", "units_sold", "market_share", "loan_flag",
)
COMPETITIVE_INTEL_HIDDEN_FIELDS = ("plant_capacity", "cash", "rd_spend")
