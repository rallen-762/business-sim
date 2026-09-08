"""
Market Dashboard and Competitive Intelligence Report (firm-facing), scoped
to the logged-in firm's own World. See app/market_data.py for the shared
query/computation logic and app/blueprints/teacher.py for the teacher-
facing equivalents (same templates, different world lookup).
"""

from flask import Blueprint, render_template, request

from app.auth import current_world, firm_login_required
from app.market_data import (
    build_pie_gradient,
    competitive_intel_rows,
    cumulative_standings,
    latest_processed_round,
    market_shares_for_round,
    round_totals,
    segment_overview,
)

bp = Blueprint("market", __name__)


@bp.route("/market")
@firm_login_required
def dashboard():
    world = current_world()
    latest_round = latest_processed_round(world)

    standings = cumulative_standings(world)
    podium = standings[:3]

    selected_round = request.args.get("round", type=int)
    if latest_round is not None:
        selected_round = max(1, min(selected_round or latest_round, latest_round))
    totals_for_round = round_totals(world, selected_round) if latest_round else []

    shares = market_shares_for_round(world, latest_round) if latest_round else []
    pie_gradient = build_pie_gradient(shares)

    segments = segment_overview(world, latest_round)

    return render_template(
        "market_dashboard.html", world=world, latest_round=latest_round,
        standings=standings, podium=podium,
        selected_round=selected_round, totals_for_round=totals_for_round,
        shares=shares, pie_gradient=pie_gradient, segments=segments,
    )


@bp.route("/market/intel")
@firm_login_required
def intel():
    world = current_world()
    latest_round = latest_processed_round(world)
    rows = competitive_intel_rows(world, latest_round) if latest_round else []
    return render_template(
        "competitive_intel.html", world=world, latest_round=latest_round, rows=rows,
    )
