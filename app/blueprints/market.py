"""
Market Dashboard: cross-firm comparison for the most recently processed
round in the current world.

Edge cases considered:
 1. No round has been processed yet (world still on Round 1, "collecting")
    -- shows an explicit "no market data yet" state rather than an empty
    table that looks broken.
 2. Always shows the LATEST processed round regardless of world.status, so
    it reads sensibly whether the world is mid-round ("collecting" round 2,
    showing round 1's market) or between rounds ("transition").
 3. A bankrupt firm still has a result row for the round it went bankrupt
    in (and every round after) -- included in the comparison, not hidden,
    since the docs say a bankrupt firm stays listed/marked on dashboards.
"""

from flask import Blueprint, render_template

from app.auth import current_world, firm_login_required
from app.extensions import db
from app.models import Firm, RoundResult

bp = Blueprint("market", __name__)


@bp.route("/market")
@firm_login_required
def dashboard():
    world = current_world()

    latest_round = (
        db.session.query(db.func.max(RoundResult.round_number))
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .scalar()
    )

    results = []
    if latest_round is not None:
        results = (
            RoundResult.query
            .join(Firm, Firm.id == RoundResult.firm_id)
            .filter(Firm.world_id == world.id, RoundResult.round_number == latest_round)
            .all()
        )

    return render_template(
        "market_dashboard.html", world=world, results=results, round_shown=latest_round,
    )
