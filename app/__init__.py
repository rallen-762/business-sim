import os
from datetime import timedelta

import click
import sqlalchemy as sa
from flask import Flask

from app.extensions import db


def _normalized_database_url():
    """Render's DATABASE_URL env var uses the legacy 'postgres://' scheme,
    which SQLAlchemy's psycopg2 dialect rejects outright (it requires
    'postgresql://'). Flagging this here rather than letting it surface as a
    cryptic deploy-time crash -- this is a real infra constraint, not a
    style choice."""
    url = os.environ.get("DATABASE_URL", "sqlite:///dev.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def create_app(config_overrides=None):
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = _normalized_database_url()
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-change-me")
    # Single global teacher password (not per-World) -- confirmed as the
    # simplest option for one teacher running several class periods.
    app.config["TEACHER_PASSWORD"] = os.environ.get("TEACHER_PASSWORD", "dev-teacher-change-me")
    # True only when running locally: Render always injects a real DATABASE_URL
    # (see _normalized_database_url above), so this is never true in production
    # regardless of what TEACHER_PASSWORD happens to be set to. Used to skip
    # the teacher password gate entirely for local dev -- confirmed with the
    # user that repeatedly re-entering it while iterating locally was pure
    # friction with no one else on the machine to gate out.
    app.config["IS_LOCAL_DEV"] = "DATABASE_URL" not in os.environ
    # Flask's session cookie is non-permanent by default (no Expires/Max-Age
    # at all) -- some browsers, Chromebooks especially, treat that as safe
    # to drop when a tab is discarded/backgrounded for memory, which reads
    # to the user as "got logged out just from switching windows." Making
    # the session permanent with an explicit lifetime gives the cookie a
    # real expiry the browser is supposed to honor and keep around.
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)

    if config_overrides:
        app.config.update(config_overrides)

    # The test suite creates the app via config_overrides without ever
    # setting a real DATABASE_URL env var, so env-based IS_LOCAL_DEV
    # detection alone would silently bypass the password check under
    # pytest too -- forcing it off under TESTING keeps tests exercising
    # the real password flow regardless of the host machine's env.
    if app.config.get("TESTING"):
        app.config["IS_LOCAL_DEV"] = False

    db.init_app(app)

    with app.app_context():
        from app import models  # noqa: F401  -- registers models on db.metadata

        from app.blueprints import auth, firm, market, teacher
        app.register_blueprint(auth.bp)
        app.register_blueprint(firm.bp)
        app.register_blueprint(market.bp)
        app.register_blueprint(teacher.bp)

    @app.cli.command("init-db")
    def init_db_command():
        """Creates any missing tables (db.create_all() -- safe to run
        repeatedly, only adds tables that don't exist yet, never alters or
        drops existing ones) PLUS one targeted, hand-written ADD COLUMN
        check for firms.bot_profile -- the one additive schema change made
        so far that create_all() can't handle on its own, since it only
        creates missing TABLES, never adds a column to a table that
        already exists. Still no general migration tool (e.g. Alembic);
        this isn't trying to become one -- a FUTURE schema change needs
        its own one-off addition here, following this same pattern, not a
        reusable system. Safe to run repeatedly: guarded by an inspector
        check, so it's a no-op once the column exists. Meant to be run
        once per deploy via Render's Pre-Deploy Command, not on every
        gunicorn worker boot -- running it from every worker at once could
        race on the very first deploy."""
        with app.app_context():
            db.create_all()
            inspector = sa.inspect(db.engine)
            if "firms" in inspector.get_table_names():
                existing_columns = {c["name"] for c in inspector.get_columns("firms")}
                if "bot_profile" not in existing_columns:
                    with db.engine.connect() as conn:
                        conn.execute(sa.text("ALTER TABLE firms ADD COLUMN bot_profile VARCHAR(20)"))
                        conn.commit()
                    print("Added firms.bot_profile column.")
        print("Database tables created (or already existed).")

    @app.cli.command("simulate-bots")
    @click.option("--trials", default=10, show_default=True, help="Number of independent trials to run.")
    @click.option("--seed", default=0, show_default=True, help="Base random seed (trial i uses seed+i).")
    def simulate_bots_command(trials, seed):
        """Runs N headless ROUNDS_PER_WORLD-round trials (one firm per bot
        profile) using the real engine/round-processing code, against a
        throwaway in-memory DB -- NEVER this command invocation's own
        configured database (dev.db or Render's Postgres), regardless of
        what DATABASE_URL happens to be set to. Prints each profile's
        average finishing rank/stdev and average cumulative revenue/profit
        across all trials -- a balance/testing tool, not a classroom
        feature."""
        from app.bots import BOT_PROFILES
        from app.bot_sim import run_many_trials, summarize

        sim_app = create_app({"SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:", "TESTING": True})
        with sim_app.app_context():
            db.create_all()
            summary = summarize(run_many_trials(trials, base_seed=seed))

        click.echo(f"\n{trials} trial(s), base seed={seed}\n")
        header = f"{'Profile':<14}{'Avg Rank':>10}{'Rank StDev':>12}{'Avg Cum Revenue':>20}{'Avg Cum Profit':>20}"
        click.echo(header)
        click.echo("-" * len(header))
        for profile, label in BOT_PROFILES.items():
            s = summary[profile]
            click.echo(
                f"{label:<14}{s['avg_rank']:>10.2f}{s['stdev_rank']:>12.2f}"
                f"{'$' + format(s['avg_cum_revenue'], ',.0f'):>20}"
                f"{'$' + format(s['avg_cum_profit'], ',.0f'):>20}"
            )

    return app
