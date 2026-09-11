"""
Rules-based decision logic for the 4 computer-controlled bot profiles
(Underbidder, Marketing, Elite, Random). Deliberately has ZERO database or
Flask dependency, mirroring engine.py's own "pure function" philosophy --
plain values in, a FirmDecision out, fully unit-testable without a running
app/DB.

Explicitly NOT an LLM call -- same standing project rule as Scouting Report
(README: "No LLM/AI API calls anywhere"). Pure rules-based logic.

Visibility rule (locked, per the user): a bot only ever sees ITS OWN round
history (own last price, own last round's profit, own current cash/capacity/
cumulative spend) -- never another firm's decisions, results, or the
underlying multiplier formulas. Nothing in this module queries or accepts
any other firm's data; every function signature below is scoped to exactly
one firm's own state.

"The field average" (referenced in the original feature request for
Marketing's and Elite's pricing) would require peeking at competitors'
actual submitted decisions, which directly contradicts the rule above. Bots
instead price relative to BOOTSTRAP_DEFAULT_PRICE ($80) -- the same fixed
platform reference every human firm's Round 1 default is built from -- not
a live-computed average of real opponents. Flagged with the user; see the
feature's implementation notes.

Numeric choices below (start prices, step sizes, ramp schedules, celebrity
cash thresholds, Random's value ranges) were not given exact numbers in the
spec, only qualitative direction ("modest low price," "steadily increases,"
"comfortably allows," "moderately above," "normal allowed range"). Each is
called out at its definition below rather than silently invented -- same
convention as constants.py's BOOTSTRAP_DEFAULT_PRICE.

Edge cases considered:
 1. Round 1 (no last_price/last_profit yet) -- every profile has an
    explicit Round-1 branch rather than assuming a "last round" exists.
 2. cash <= 0 -- _affordable_production_qty returns 0 rather than a
    negative or divide-by-zero result; a bot in this state simply produces
    nothing and pays whatever fixed/loan costs the engine applies, exactly
    like a struggling human firm would.
 3. Every profile's spend is capped by its own affordability check before
    production is sized off what's left -- so total planned spend can
    never exceed a bot's own cash (mirrors the human submission route's
    hard affordability block, even though the engine itself doesn't
    re-validate bot decisions the way it validates a human POST).
 4. Noise is applied AFTER computing a base value and BEFORE any floor/cap,
    so a floor (e.g. Underbidder's price floor) can't be defeated by an
    unlucky noise roll pushing it back under.
 5. rng defaults to the stdlib `random` module (true randomness for live
    class play) but accepts any object with .uniform/.choice/.random (e.g.
    a seeded random.Random(seed) instance) -- this is what makes the
    balance-testing harness's trials reproducible.
"""

from __future__ import annotations

import random as _random_module

from app.constants import (
    AD_LADDER,
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    CELEBRITY_COST_PER_ROUND,
    QUALITY_LADDER,
    ROUNDS_PER_WORLD,
    TRACKS,
    track_unit_cost,
)
from app.engine import FirmDecision

BOT_PROFILES = {
    "underbidder": "Underbidder",
    "marketing": "Marketing",
    "elite": "Elite",
    "random": "Random",
}


_AD_LEVEL_9_THRESHOLD = next(threshold for level, threshold, _ in AD_LADDER if level == 9)  # 1,350,000
_RD_LEVEL_10_THRESHOLD = QUALITY_LADDER[-1][1]  # 700,000


def _noise(rng, value, pct=0.075):
    """+/- pct multiplicative random noise -- default 7.5%, the midpoint of
    the requested "roughly +/-5-10%" range."""
    return value * (1 + rng.uniform(-pct, pct))


def _affordable_production_qty(cash, track, capacity):
    """Shared production-sizing rule for every profile: spend whatever cash
    is left (after that profile's other planned spend) on Production,
    capped at capacity -- same "100% of leftover cash, capped at capacity"
    idea as engine.synthesize_non_submission_decision, since none of the 4
    profiles specify their own Production rule and Random's spec explicitly
    omits Production Qty from its randomized field list."""
    if cash <= 0:
        return 0
    unit_cost = track_unit_cost(track)
    max_units = int(cash // unit_cost) if unit_cost > 0 else 0
    return max(0, min(max_units, capacity))


def _clamp_to_affordable(desired_spend, cash, max_fraction=0.6):
    """Caps a bot's planned R&D/Ad spend at a fraction of its CURRENT cash,
    leaving room for fixed costs and production -- the general "gated by
    cash affordability, not just a schedule" guard the spec calls out
    explicitly for Elite, applied here to every profile that ramps spend."""
    if cash <= 0 or desired_spend <= 0:
        return 0.0
    return min(desired_spend, cash * max_fraction)


def decide(
    profile,
    firm_id,
    round_number,
    cash,
    capacity,
    cumulative_rd_spend,
    cumulative_ad_spend,
    loan_outstanding,
    last_price,
    last_profit,
    rng=None,
) -> FirmDecision:
    """Dispatches to one profile's decision function and returns a fully-
    formed FirmDecision (is_auto=True -- a bot's decision is never a real
    human submission, same flag meaning as a no-show's synthesized one).

    Only THIS firm's own state is ever passed in -- see the module
    docstring's visibility rule. `rng` defaults to the stdlib `random`
    module; pass a seeded random.Random(seed) for reproducible trials."""
    rng = rng if rng is not None else _random_module

    if profile == "underbidder":
        fields = _decide_underbidder(cash, capacity, last_price, last_profit, rng)
    elif profile == "marketing":
        fields = _decide_marketing(cash, capacity, cumulative_ad_spend, loan_outstanding, rng)
    elif profile == "elite":
        fields = _decide_elite(round_number, cash, capacity, cumulative_rd_spend, loan_outstanding, rng)
    elif profile == "random":
        fields = _decide_random(cash, capacity, rng)
    else:
        raise ValueError(f"Unknown bot profile: {profile!r}")

    return FirmDecision(firm_id=firm_id, is_auto=True, **fields)


# --------------------------------------------------------------------------- #
# Underbidder -- price-only, reactive to its own last round's profit.
# --------------------------------------------------------------------------- #

UNDERBIDDER_START_PRICE = 65.00  # "a modest low price" -- below the $80 platform reference
UNDERBIDDER_PRICE_STEP = 0.05    # "lower/raise ... slightly" -- 5% per round
UNDERBIDDER_PRICE_FLOOR = 15.00  # keeps price sane/positive under repeated cuts


def _decide_underbidder(cash, capacity, last_price, last_profit, rng):
    if last_price is None:
        base_price = UNDERBIDDER_START_PRICE
    elif last_profit is not None and last_profit > 0:
        base_price = last_price * (1 - UNDERBIDDER_PRICE_STEP)
    else:
        base_price = last_price * (1 + UNDERBIDDER_PRICE_STEP)

    price = max(UNDERBIDDER_PRICE_FLOOR, round(_noise(rng, base_price), 2))
    production_qty = _affordable_production_qty(cash, BOOTSTRAP_DEFAULT_TRACK, capacity)

    return dict(
        price=price, production_qty=production_qty, ad_spend=0.0, rd_spend=0.0,
        track=BOOTSTRAP_DEFAULT_TRACK, celebrity_on=False, plant_investment=0,
    )


# --------------------------------------------------------------------------- #
# Marketing -- ramps Ad spend to Level 9 (never 10), Celebrity once
# affordable, flat price near the platform reference.
# --------------------------------------------------------------------------- #

MARKETING_PRICE_BASE = BOOTSTRAP_DEFAULT_PRICE  # "near the field's typical starting price"
MARKETING_AD_RAMP_ROUNDS = 6                    # "over the first several rounds"
MARKETING_CELEBRITY_CASH_THRESHOLD = 300_000    # "once cash comfortably allows it" -- 6x the $50k/round cost


def _decide_marketing(cash, capacity, cumulative_ad_spend, loan_outstanding, rng):
    track = BOOTSTRAP_DEFAULT_TRACK  # "keeps Track ... at low/default"
    rd_spend = 0.0                   # "keeps ... R&D at low/default investment"

    remaining_to_level_9 = max(0.0, _AD_LEVEL_9_THRESHOLD - cumulative_ad_spend)
    if remaining_to_level_9 > 0:
        per_round_target = _AD_LEVEL_9_THRESHOLD / MARKETING_AD_RAMP_ROUNDS
        desired = min(per_round_target, remaining_to_level_9)
        ad_spend = _clamp_to_affordable(_noise(rng, desired), cash)
        ad_spend = min(ad_spend, remaining_to_level_9)  # never crosses into the Level 10 trap
    else:
        ad_spend = 0.0  # capped at Level 9, permanently

    celebrity_on = (
        loan_outstanding == 0
        and (cash - ad_spend) >= MARKETING_CELEBRITY_CASH_THRESHOLD
    )

    price = max(1.0, round(_noise(rng, MARKETING_PRICE_BASE), 2))

    remaining_cash = cash - ad_spend - (CELEBRITY_COST_PER_ROUND if celebrity_on else 0)
    production_qty = _affordable_production_qty(remaining_cash, track, capacity)

    return dict(
        price=price, production_qty=production_qty, ad_spend=round(ad_spend, 2), rd_spend=rd_spend,
        track=track, celebrity_on=celebrity_on, plant_investment=0,
    )


# --------------------------------------------------------------------------- #
# Elite -- gradual Track/R&D ramp (never front-loaded), Celebrity from Round
# 7 if affordable, priced moderately above the platform reference.
# --------------------------------------------------------------------------- #

ELITE_PRICE_PREMIUM = 1.15               # "moderately above" the $80 platform reference
ELITE_CELEBRITY_CASH_THRESHOLD = 300_000  # same reasoning as Marketing's
ELITE_CELEBRITY_START_ROUND = 7


def _decide_elite(round_number, cash, capacity, cumulative_rd_spend, loan_outstanding, rng):
    # Rounds 1-3: Standard (mid tier). Rounds 4+: Premium (top tier).
    track = "Standard" if round_number <= 3 else "Premium"

    # R&D ramps toward the Level-10 cumulative threshold roughly evenly
    # across all 10 rounds -- NOT front-loaded in Round 1 -- and every
    # round's actual spend is still capped at a fraction of THAT round's
    # real cash (the explicit "gated by cash affordability, not just round
    # number" requirement), so a bad early round can't force overspending.
    target_cumulative = _RD_LEVEL_10_THRESHOLD * min(1.0, round_number / ROUNDS_PER_WORLD)
    desired_rd = max(0.0, target_cumulative - cumulative_rd_spend)
    rd_spend = _clamp_to_affordable(_noise(rng, desired_rd) if desired_rd > 0 else 0.0, cash, max_fraction=0.5)

    celebrity_on = (
        round_number >= ELITE_CELEBRITY_START_ROUND
        and loan_outstanding == 0
        and (cash - rd_spend) >= ELITE_CELEBRITY_CASH_THRESHOLD
    )

    price = max(1.0, round(_noise(rng, BOOTSTRAP_DEFAULT_PRICE * ELITE_PRICE_PREMIUM), 2))

    remaining_cash = cash - rd_spend - (CELEBRITY_COST_PER_ROUND if celebrity_on else 0)
    production_qty = _affordable_production_qty(remaining_cash, track, capacity)

    return dict(
        price=price, production_qty=production_qty, ad_spend=0.0, rd_spend=round(rd_spend, 2),
        track=track, celebrity_on=celebrity_on, plant_investment=0,
    )


# --------------------------------------------------------------------------- #
# Random -- uniform random Track/Price/R&D/Ad/Celebrity every round.
# --------------------------------------------------------------------------- #

RANDOM_PRICE_MIN, RANDOM_PRICE_MAX = 30.0, 150.0  # "normal allowed range" -- no hard price ceiling
RANDOM_SPEND_MAX_FRACTION = 0.15                  # up to ~15% of current cash on R&D, and again on Ads


def _decide_random(cash, capacity, rng):
    track = rng.choice(TRACKS)
    price = round(rng.uniform(RANDOM_PRICE_MIN, RANDOM_PRICE_MAX), 2)
    rd_spend = round(rng.uniform(0, max(0.0, cash) * RANDOM_SPEND_MAX_FRACTION), 2)
    ad_spend = round(rng.uniform(0, max(0.0, cash) * RANDOM_SPEND_MAX_FRACTION), 2)
    celebrity_on = rng.random() < 0.5

    remaining_cash = cash - rd_spend - ad_spend - (CELEBRITY_COST_PER_ROUND if celebrity_on else 0)
    production_qty = _affordable_production_qty(remaining_cash, track, capacity)

    return dict(
        price=price, production_qty=production_qty, ad_spend=ad_spend, rd_spend=rd_spend,
        track=track, celebrity_on=celebrity_on, plant_investment=0,
    )
