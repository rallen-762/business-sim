"""
Tests for the headless bot-vs-bot balance-testing harness (app/bot_sim.py)
and the `flask simulate-bots` CLI command that wraps it.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from app.bot_sim import run_many_trials, run_trial, summarize
from app.bots import BOT_PROFILES
from app.constants import ROUNDS_PER_WORLD
from app.extensions import db
from app.models import RoundDecision, RoundResult, World


@pytest.fixture
def app():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def test_run_trial_plays_a_full_game_with_one_firm_per_profile(app):
    with app.app_context():
        results = run_trial(seed=1)

        assert {r.profile for r in results} == set(BOT_PROFILES)
        assert sorted(r.rank for r in results) == [1, 2, 3, 4]

        world = World.query.one()
        assert world.status == "complete"
        assert world.current_round == ROUNDS_PER_WORLD
        assert RoundResult.query.count() == 4 * ROUNDS_PER_WORLD
        assert RoundDecision.query.count() == 4 * ROUNDS_PER_WORLD


def test_run_trial_is_reproducible_from_the_same_seed(app):
    with app.app_context():
        first = {r.profile: (r.rank, r.cum_revenue, r.cum_profit) for r in run_trial(seed=42)}
        second = {r.profile: (r.rank, r.cum_revenue, r.cum_profit) for r in run_trial(seed=42)}
        assert first == second


def test_run_trial_different_seeds_can_differ():
    # Not a strict guarantee for every possible pair, but seeds 1 and 2
    # should not coincidentally produce byte-identical cumulative profits
    # across all 4 profiles' Random-noise-driven decisions.
    app1 = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app1.app_context():
        db.create_all()
        a = {r.profile: r.cum_profit for r in run_trial(seed=1)}
    app2 = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    with app2.app_context():
        db.create_all()
        b = {r.profile: r.cum_profit for r in run_trial(seed=2)}
    assert a != b


def test_run_many_trials_and_summarize(app):
    with app.app_context():
        by_profile = run_many_trials(3, base_seed=100)
        assert set(by_profile) == set(BOT_PROFILES)
        for profile, data in by_profile.items():
            assert len(data["ranks"]) == 3
            assert all(1 <= r <= 4 for r in data["ranks"])

        summary = summarize(by_profile)
        for profile in BOT_PROFILES:
            assert 1 <= summary[profile]["avg_rank"] <= 4
            assert summary[profile]["stdev_rank"] >= 0


def test_summarize_handles_a_single_trial_without_crashing(app):
    with app.app_context():
        summary = summarize(run_many_trials(1, base_seed=5))
        for profile in BOT_PROFILES:
            assert summary[profile]["stdev_rank"] == 0.0


def test_simulate_bots_cli_command_runs_and_prints_a_table():
    app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
    runner = app.test_cli_runner()
    result = runner.invoke(args=["simulate-bots", "--trials", "2", "--seed", "3"])
    assert result.exit_code == 0
    for label in BOT_PROFILES.values():
        assert label in result.output
