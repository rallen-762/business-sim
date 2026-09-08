"""
Market Dashboard (firm-facing): cross-firm comparison for the most recently
processed round in the logged-in firm's own World. See app/market_data.py
for the shared query logic and app/blueprints/teacher.py's `market` route
for the teacher-facing equivalent (same template, different world lookup).
"""

from flask import Blueprint, render_template

from app.auth import current_world, firm_login_required
from app.market_data import latest_round_results

bp = Blueprint("market", __name__)


@bp.route("/market")
@firm_login_required
def dashboard():
    world = current_world()
    results, round_shown = latest_round_results(world)
    return render_template(
        "market_dashboard.html", world=world, results=results, round_shown=round_shown,
    )
