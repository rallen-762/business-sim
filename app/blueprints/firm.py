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
"""

from flask import Blueprint, flash, redirect, render_template, request, url_for

from app.auth import current_firm, current_world, firm_login_required
from app.constants import ROUNDS_PER_WORLD
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

    return render_template(
        "firm_dashboard.html",
        firm=firm, world=world, decision=decision, last_result=last_result,
        rounds_per_world=ROUNDS_PER_WORLD,
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

    if track not in ("Budget", "Standard", "Premium"):
        flash("Please choose a valid track.")
        return redirect(url_for("firm.dashboard"))
    if price < 0 or production_qty < 0 or ad_spend < 0 or rd_spend < 0:
        flash("Values can't be negative.")
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
