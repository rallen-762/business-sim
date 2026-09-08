"""
Shared query logic for the Market Dashboard, used by both the firm-facing
route (app/blueprints/market.py, scoped to the logged-in firm's own world)
and the teacher-facing route (app/blueprints/teacher.py, scoped to whichever
world_id the teacher is looking at). Kept in one place so the two call
sites can never drift on what "latest round" or "market data" means.
"""

from app.extensions import db
from app.models import Firm, RoundResult


def latest_round_results(world):
    """Returns (results, round_shown) for the most recently processed round
    in this world. round_shown is None (and results []) if no round has
    been processed yet -- the template shows an explicit "no data" state
    for that case rather than an empty table."""
    latest_round = (
        db.session.query(db.func.max(RoundResult.round_number))
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .scalar()
    )

    if latest_round is None:
        return [], None

    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == latest_round)
        .all()
    )
    return results, latest_round
