"""
Headless N-round balance-testing harness: runs a full ROUNDS_PER_WORLD-round
game with exactly 4 firms (one per app.bots.BOT_PROFILES entry) with no
browser, no real users, and no separate reimplementation of the economic
model -- every round goes through the EXACT SAME
app.blueprints.teacher._process_current_round()/_open_next_round() the live
Teacher Dashboard's "Process Round" button calls, which itself is a thin
wrapper around app.engine.process_round(). If the real game's math changes,
this harness's results change with it automatically -- there is nothing here
to fall out of sync.

Not part of the classroom app's routes/UI -- this is a design/testing tool
only, run via `flask simulate-bots` (see the CLI command in app/__init__.py)
against a throwaway in-memory DB, never the real game/Render database.

Edge cases considered:
 1. Each trial gets its own fresh World inside a shared throwaway DB, not a
    brand-new DB per trial -- cheap, and rows are fully isolated from each
    other by world_id so trials can never cross-contaminate.
 2. One random.Random(seed) instance is threaded through every round of a
    trial (passed as the same `rng` to every _process_current_round call),
    so a trial's ENTIRE sequence of bot noise is reproducible from one
    number -- rerunning run_trial(seed=7) always plays out identically.
 3. Ranking reuses market_data.cumulative_standings() -- the same
    "sorted by cumulative profit, ties by slot number" definition of
    "who's winning" the Teacher/Market Dashboards already use, not a
    second definition invented just for this harness.
 4. summarize()'s stdev is 0.0 for a single trial (statistics.stdev raises
    on fewer than 2 data points) rather than crashing a `--trials 1` run.
"""

from __future__ import annotations

import random
import secrets
import statistics
from dataclasses import dataclass

from werkzeug.security import generate_password_hash

from app.blueprints.teacher import _generate_game_code, _open_next_round, _process_current_round
from app.bots import BOT_PROFILES
from app.constants import (
    BOOTSTRAP_DEFAULT_PRICE,
    BOOTSTRAP_DEFAULT_TRACK,
    ROUNDS_PER_WORLD,
    STARTING_CASH,
    STARTING_PLANT_CAPACITY,
)
from app.extensions import db
from app.market_data import cumulative_standings
from app.models import Firm, World


@dataclass
class TrialResult:
    profile: str
    rank: int
    cum_revenue: float
    cum_profit: float


def run_trial(seed=None) -> list[TrialResult]:
    """Runs one full game (all ROUNDS_PER_WORLD rounds) with 4 firms, one
    per bot profile, and returns each firm's final rank/cum_revenue/
    cum_profit. Must be called inside an active Flask app context with a
    DB already created (db.create_all()) -- see run_many_trials below and
    the `simulate-bots` CLI command for the throwaway app/DB setup."""
    rng = random.Random(seed)

    world = World(
        name=f"Bot Balance Sim (seed={seed})",
        game_code=_generate_game_code(),
        planned_firm_slots=len(BOT_PROFILES),
    )
    db.session.add(world)
    db.session.flush()  # assigns world.id before Firms reference it

    firm_id_to_profile = {}
    for slot, profile in enumerate(BOT_PROFILES, start=1):
        firm = Firm(
            world_id=world.id, slot_number=slot,
            team_name=f"Bot #{slot} ({BOT_PROFILES[profile]})",
            password_hash=generate_password_hash(secrets.token_hex(16)),  # unused, never logged into
            bot_profile=profile,
            cash=STARTING_CASH, plant_capacity=STARTING_PLANT_CAPACITY,
            last_price=BOOTSTRAP_DEFAULT_PRICE, last_track=BOOTSTRAP_DEFAULT_TRACK,
        )
        db.session.add(firm)
        db.session.flush()  # assigns firm.id
        firm_id_to_profile[firm.id] = profile
    db.session.commit()

    for _ in range(ROUNDS_PER_WORLD):
        _process_current_round(world, rng=rng)
        if world.status != "complete":
            _open_next_round(world)

    standings = cumulative_standings(world)  # already sorted by cum_profit desc
    return [
        TrialResult(
            profile=firm_id_to_profile[row["firm"].id],
            rank=rank,
            cum_revenue=row["cum_revenue"],
            cum_profit=row["cum_profit"],
        )
        for rank, row in enumerate(standings, start=1)
    ]


def run_many_trials(n_trials, base_seed=0):
    """Runs n_trials independent trials seeded base_seed, base_seed+1, ...
    (so the whole batch is reproducible from one number) and returns
    {profile: {"ranks": [...], "cum_revenues": [...], "cum_profits": [...]}}."""
    by_profile = {p: {"ranks": [], "cum_revenues": [], "cum_profits": []} for p in BOT_PROFILES}
    for i in range(n_trials):
        for result in run_trial(seed=base_seed + i):
            by_profile[result.profile]["ranks"].append(result.rank)
            by_profile[result.profile]["cum_revenues"].append(result.cum_revenue)
            by_profile[result.profile]["cum_profits"].append(result.cum_profit)
    return by_profile


def summarize(by_profile):
    """{profile: {"ranks": [...], ...}} -> {profile: {avg_rank, stdev_rank,
    avg_cum_revenue, avg_cum_profit}} -- the average finishing rank and
    standard deviation per bot profile across all trials, to spot a profile
    that's dominating (avg_rank near 1) or losing too consistently (avg_rank
    near 4, low stdev) using the real engine, not an approximation."""
    summary = {}
    for profile, data in by_profile.items():
        ranks = data["ranks"]
        summary[profile] = {
            "avg_rank": statistics.mean(ranks),
            "stdev_rank": statistics.stdev(ranks) if len(ranks) > 1 else 0.0,
            "avg_cum_revenue": statistics.mean(data["cum_revenues"]),
            "avg_cum_profit": statistics.mean(data["cum_profits"]),
        }
    return summary
