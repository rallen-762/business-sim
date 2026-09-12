"""
Firm Dashboard: viewing round state and submitting a round's decision.

Edge cases considered:
 1. No edit after submit -- if a RoundDecision already exists for this firm
    and the world's current_round, submit_decision refuses a second one
    (checked here; the DB's unique constraint is the backstop).
 2. Decisions can only be submitted while the world is "collecting" -- once
    the teacher has processed the round (status moves to "transition" or
    "complete"), submission is refused even if no decision was ever made
    (the auto-decision already covers that firm for the round in progress).
 3. A bankrupt firm is blocked from submitting anything at all, regardless
    of round status.
 4. Bad/missing form input (non-numeric price, missing track) is caught and
    re-shown with a flash message rather than crashing with a 500 or
    silently saving garbage.
 5. The dashboard must render sensibly at every world.status value:
    "collecting" + no decision yet -> show the form; "collecting" + already
    submitted -> show a waiting state; "transition" -> show the Round
    Transition Notice banner with last round's result; "complete" -> show a
    final summary, no form.
 6. Production Quantity is auto-computed client-side as leftover cash after
    R&D/Ad/Plant/Celebrity spend, divided by the selected track's unit
    cost, capped at plant capacity -- but the SERVER never trusts that
    computation. submit_decision() independently re-derives the same
    affordability check and rejects a submission whose total spend
    (production cost included) exceeds the firm's actual cash, regardless
    of what the client posted. A student with JS disabled, or bypassing the
    UI directly, cannot submit an unaffordable plan either way.
 7. R&D/Ad spend "preset" quick-fill options only make sense above a firm's
    CURRENT cumulative spend -- computed fresh per request from the firm's
    live cumulative_rd_spend/cumulative_ad_spend, never cached, so they're
    always correct even immediately after a round changes those totals.
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import current_firm, current_world, firm_login_required
from app.avatars import TIER_ICONS
from app.market_data import affordability_breakdown, affordability_curve
from app.constants import (
    CAPACITY_BLOCK_FIXED_COST,
    CELEBRITY_COST_PER_ROUND,
    LOAN_INTEREST_RATE,
    LOAN_REPAYMENT_PRINCIPAL,
    MAX_QUALITY_LEVEL_GAIN_PER_ROUND,
    PLANT_INVESTMENT_CAPACITY_GAIN,
    PLANT_INVESTMENT_COST,
    ROUNDS_PER_WORLD,
    TRACKS,
    ad_spend_presets,
    quality_descriptor,
    quality_level_from_cumulative_rd,
    max_rd_spend_this_round,
    rd_spend_presets,
    track_unit_cost,
)
from app.extensions import db
from app.models import RoundDecision, RoundResult

bp = Blueprint("firm", __name__)


@bp.route("/firm")
@firm_login_required
def dashboard():
    firm = current_firm()
    world = current_world()

    decision = RoundDecision.query.filter_by(firm_id=firm.id, round_number=world.current_round).first()

    # world.current_round is only incremented by _open_next_round() -- while
    # status is "transition", current_round IS the round that was just
    # processed (not yet bumped), so its result lives at current_round, not
    # current_round - 1. (Caught via a real HTTP smoke test: this used to
    # look up round 0 and silently show "waiting" instead of the results.)
    last_result = RoundResult.query.filter_by(
        firm_id=firm.id, round_number=world.current_round
    ).first()
    last_decision = RoundDecision.query.filter_by(
        firm_id=firm.id, round_number=world.current_round
    ).first()

    # This firm's own cumulative totals through the round just shown above
    # -- requested for the Round Results screen so a team sees "how am I
    # doing overall," not just this round's numbers.
    cumulative = (
        db.session.query(
            db.func.sum(RoundResult.revenue).label("cum_revenue"),
            db.func.sum(RoundResult.total_cost).label("cum_cost"),
            db.func.sum(RoundResult.profit).label("cum_profit"),
        )
        .filter(RoundResult.firm_id == firm.id, RoundResult.round_number <= world.current_round)
        .first()
    )

    track_unit_costs = {t: track_unit_cost(t) for t in TRACKS}
    quality_level = quality_level_from_cumulative_rd(firm.cumulative_rd_spend)

    # Projected interest for the UPCOMING round, computed from the firm's
    # CURRENT pre-existing balance -- mirrors engine.py's Step 8 exactly
    # (principal comes off first, then 10% on what's left), so this is a
    # true preview, not a guess. A loan taken mid-round-in-progress (there
    # isn't one yet, since this round hasn't processed) never applies here.
    remaining_after_principal = max(0.0, firm.loan_outstanding - LOAN_REPAYMENT_PRINCIPAL)
    projected_loan_interest = remaining_after_principal * LOAN_INTEREST_RATE

    return render_template(
        "firm_dashboard.html",
        firm=firm, world=world, decision=decision, last_result=last_result,
        cumulative=cumulative, rounds_per_world=ROUNDS_PER_WORLD,
        track_unit_costs=track_unit_costs, tracks=TRACKS, tier_icons=TIER_ICONS,
        rd_presets=rd_spend_presets(firm.cumulative_rd_spend),
        rd_cap=max_rd_spend_this_round(firm.cumulative_rd_spend),
        max_quality_gain=MAX_QUALITY_LEVEL_GAIN_PER_ROUND,
        ad_presets=ad_spend_presets(firm.cumulative_ad_spend),
        celebrity_cost=CELEBRITY_COST_PER_ROUND,
        quality_level=quality_level,
        # Descriptor ONLY (e.g. "Elite Quality"), not quality_track_label's
        # combined "Elite Quality Mid" -- the Current Tier stat right
        # next to this one already shows the tier name; showing it twice
        # was confusing, not informative.
        quality_label=f"{quality_descriptor(quality_level)} Quality",
        projected_loan_interest=projected_loan_interest,
        plant_investment_cost=PLANT_INVESTMENT_COST,
        plant_capacity_gain=PLANT_INVESTMENT_CAPACITY_GAIN,
        capacity_block_fixed_cost=CAPACITY_BLOCK_FIXED_COST,
        affordability=affordability_breakdown(last_decision, last_result),
        affordability_curve=affordability_curve(),
        last_result_price=last_decision.price if last_decision else None,
        last_result_track=last_decision.track if last_decision else None,
    )


@bp.route("/firm/decisions", methods=["POST"])
@firm_login_required
def submit_decision():
    firm = current_firm()
    world = current_world()

    if firm.bankrupt:
        flash("This firm is bankrupt and can no longer submit decisions.")
        return redirect(url_for("firm.dashboard"))

    if world.status != "collecting":
        flash("This round isn't open for decisions right now.")
        return redirect(url_for("firm.dashboard"))

    existing = RoundDecision.query.filter_by(firm_id=firm.id, round_number=world.current_round).first()
    if existing:
        flash("You've already submitted this round -- decisions can't be edited after submitting.")
        return redirect(url_for("firm.dashboard"))

    try:
        price = float(request.form["price"])
        production_qty = int(request.form["production_qty"])
        ad_spend = float(request.form.get("ad_spend") or 0)
        rd_spend = float(request.form.get("rd_spend") or 0)
        track = request.form["track"]
        celebrity_on = request.form.get("celebrity_on") == "on"
        plant_investment = int(request.form.get("plant_investment") or 0)
    except (KeyError, ValueError):
        flash("That form had an invalid value -- please check your entries and try again.")
        return redirect(url_for("firm.dashboard"))

    if track not in TRACKS:
        flash("Please choose a valid tier.")
        return redirect(url_for("firm.dashboard"))
    if price < 0 or production_qty < 0 or ad_spend < 0 or rd_spend < 0:
        flash("Values can't be negative.")
        return redirect(url_for("firm.dashboard"))

    # A firm can only climb MAX_QUALITY_LEVEL_GAIN_PER_ROUND levels per
    # round. Reject an over-cap submission rather than accepting it and
    # silently capping the gain in the engine -- the team would have paid
    # for levels they didn't get.
    rd_cap = max_rd_spend_this_round(firm.cumulative_rd_spend)
    if rd_cap is None and rd_spend > 0:
        flash("You're already at the maximum Quality Level -- more R&D has no effect.")
        return redirect(url_for("firm.dashboard"))
    if rd_cap is not None and rd_spend > rd_cap:
        flash(
            f"R&D is limited to {MAX_QUALITY_LEVEL_GAIN_PER_ROUND} Quality Levels per round "
            f"-- that's ${rd_cap:,.0f} maximum this round."
        )
        return redirect(url_for("firm.dashboard"))

    # Hard-block: total planned spend (production cost included) can never
    # exceed available cash. The dashboard auto-computes Production Quantity
    # client-side to make this true by construction, but the server never
    # trusts that -- this is the real, unbypassable enforcement.
    production_cost = production_qty * track_unit_cost(track)
    celebrity_cost = CELEBRITY_COST_PER_ROUND if celebrity_on else 0
    total_planned_spend = production_cost + ad_spend + rd_spend + plant_investment + celebrity_cost
    if total_planned_spend > firm.cash:
        flash(
            f"That plan costs ${total_planned_spend:,.0f} but you only have "
            f"${firm.cash:,.0f} on hand -- reduce spending to submit."
        )
        return redirect(url_for("firm.dashboard"))

    db.session.add(RoundDecision(
        firm_id=firm.id, round_number=world.current_round, price=price,
        production_qty=production_qty, ad_spend=ad_spend, rd_spend=rd_spend,
        track=track, celebrity_on=celebrity_on, plant_investment=plant_investment,
        is_auto=False,
    ))
    firm.last_price = price
    firm.last_track = track
    db.session.commit()

    flash("Decision submitted!")
    return redirect(url_for("firm.dashboard"))
