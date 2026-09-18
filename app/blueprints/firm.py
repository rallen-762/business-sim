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

from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from app.auth import current_firm, current_world, firm_login_required
from app.avatars import TIER_ICONS
from app.market_data import (
    SEGMENT_ACCENTS,
    SEGMENT_ACCENT_FALLBACK,
    SEGMENT_TRAIT_DOTS,
    mall_bay_width_css,
    TIER_ACCENTS,
    affordability_breakdown,
    affordability_curve,
    latest_processed_round,
    segment_profiles,
    mall_scene,
    standings_with_rank_delta,
)
from app.constants import (
    CAPACITY_BLOCK_FIXED_COST,
    CELEBRITIES,
    CELEBRITY_COST_PER_ROUND,
    CELEBRITY_ICONS,
    CELEBRITY_LABELS,
    LOAN_INTEREST_RATE,
    LOAN_REPAYMENT_PRINCIPAL,
    MAX_PLANT_CAPACITY,
    MAX_QUALITY_LEVEL_GAIN_PER_ROUND,
    PLANT_INVESTMENT_CAPACITY_GAIN,
    PLANT_INVESTMENT_COST,
    ROUNDS_PER_WORLD,
    TRACKS,
    ad_spend_presets,
    factory_level_for_capacity,
    plant_upgrade_cost,
    quality_descriptor,
    quality_levels_by_track,
    max_rd_spend_this_round,
    rd_spend_in_track,
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

    # Sandbox advances to the next round in the same action that processes
    # the current one, so the player never sits in the "transition" state
    # where the firm-level results card lives -- they'd sail past their own
    # affordability table and the sold-out warning entirely. Hand the
    # dashboard the round they just played so it can recap it above the
    # next decision form. Classroom is untouched: it still uses the
    # transition state for this.
    recap_result = None
    recap_decision = None
    if world.mode == "sandbox" and last_result is None and world.current_round > 1:
        previous_round = world.current_round - 1
        recap_result = RoundResult.query.filter_by(
            firm_id=firm.id, round_number=previous_round
        ).first()
        recap_decision = RoundDecision.query.filter_by(
            firm_id=firm.id, round_number=previous_round
        ).first()

    # Classroom, teacher opted in: the first dashboard load after a round is
    # processed goes to the standings board. Once per round -- the board's
    # exit comes back here, and that must land on the Results card, not loop.
    if (
        world.mode == "classroom" and world.show_standings_to_students
        and last_result is not None
        and session.get("standings_seen") != _standings_key(world)
    ):
        return redirect(url_for("firm.standings"))

    track_unit_costs = {t: track_unit_cost(t) for t in TRACKS}

    # Quality is tier-bound. The form opens on the tier the firm sold last
    # (the browser's default is the first option when there isn't one), so
    # the level and R&D options rendered server-side are for THAT tier; the
    # page's JS swaps them when the tier select changes, from rd_by_tier.
    form_track = firm.last_track if firm.last_track in TRACKS else TRACKS[0]
    tier_quality = quality_levels_by_track(firm.rd_spend_by_track)
    quality_level = tier_quality[form_track]
    rd_by_tier = {}
    for t in TRACKS:
        spend = rd_spend_in_track(firm.rd_spend_by_track, t)
        rd_by_tier[t] = {
            "level": tier_quality[t],
            "label": f"{quality_descriptor(tier_quality[t])} Quality",
            "presets": [[level, round(amount)] for level, amount in rd_spend_presets(spend)],
            "cap": max_rd_spend_this_round(spend),
        }

    # Projected interest for the UPCOMING round, computed from the firm's
    # CURRENT pre-existing balance -- mirrors engine.py's Step 8 exactly
    # (principal comes off first, then 10% on what's left), so this is a
    # true preview, not a guess. A loan taken mid-round-in-progress (there
    # isn't one yet, since this round hasn't processed) never applies here.
    remaining_after_principal = max(0.0, firm.loan_outstanding - LOAN_REPAYMENT_PRINCIPAL)
    projected_loan_interest = remaining_after_principal * LOAN_INTEREST_RATE

    # Factory art level. firm.factory_level already counts an expansion
    # bought in a PREVIOUS round (it reads effective_capacity, which includes
    # the pending increase). It cannot see one submitted in the round still
    # in progress, because that only becomes pending_capacity_increase when
    # the round is processed -- so the art would sit unchanged until the
    # teacher advanced, which is exactly the wait this feature exists to
    # remove. Counting the submitted buy here is what makes the picture
    # change the moment a team commits to the upgrade.
    committed_capacity = firm.effective_capacity
    if decision is not None and (decision.plant_investment or 0) > 0:
        committed_capacity += PLANT_INVESTMENT_CAPACITY_GAIN
    committed_capacity = min(committed_capacity, MAX_PLANT_CAPACITY)
    factory_sprite = (
        f"{firm.avatar.rsplit('.', 1)[0]}-L{factory_level_for_capacity(committed_capacity)}.png"
        if firm.avatar else None
    )

    return render_template(
        "firm_dashboard.html",
        firm=firm, world=world, decision=decision, last_result=last_result,
        factory_sprite=factory_sprite,
        factory_capacity_committed=committed_capacity,
        # None at the cap, which the form uses to swap the picker for a
        # "fully upgraded" state rather than offering a purchase that the
        # engine would refuse.
        upgrade_cost=plant_upgrade_cost(firm.effective_capacity),
        round_reopened=(world.reopened_round == world.current_round),
        cumulative=cumulative, rounds_per_world=(world.rounds or ROUNDS_PER_WORLD),
        track_unit_costs=track_unit_costs, tracks=TRACKS, tier_icons=TIER_ICONS,
        rd_presets=rd_spend_presets(rd_spend_in_track(firm.rd_spend_by_track, form_track)),
        rd_cap=rd_by_tier[form_track]["cap"],
        rd_by_tier=rd_by_tier, tier_quality=tier_quality, form_track=form_track,
        tier_accents=TIER_ACCENTS,
        segments=segment_profiles(), segment_accents=SEGMENT_ACCENTS,
        segment_accent_fallback=SEGMENT_ACCENT_FALLBACK, trait_dots=SEGMENT_TRAIT_DOTS,
        standings_available=(
            world.mode == "classroom" and world.show_standings_to_students
            and last_result is not None
        ),
        max_quality_gain=MAX_QUALITY_LEVEL_GAIN_PER_ROUND,
        ad_presets=ad_spend_presets(firm.cumulative_ad_spend),
        celebrity_cost=CELEBRITY_COST_PER_ROUND,
        celebrities=CELEBRITIES, celebrity_labels=CELEBRITY_LABELS,
        celebrity_icons=CELEBRITY_ICONS,
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
        last_result_celebrity=(
            CELEBRITY_LABELS.get(last_decision.celebrity)
            if last_decision and last_decision.celebrity_on else None
        ),
        recap_celebrity=(
            CELEBRITY_LABELS.get(recap_decision.celebrity)
            if recap_decision and recap_decision.celebrity_on else None
        ),
        recap_result=recap_result,
        recap_round=(world.current_round - 1) if recap_result else None,
        recap_price=recap_decision.price if recap_decision else None,
        recap_affordability=affordability_breakdown(recap_decision, recap_result),
    )


def _standings_key(world):
    """Marks "this team has seen the board for this round" in the session.
    World id included so a team's next game can't inherit a stale mark."""
    return f"{world.id}:{world.current_round}"


@bp.route("/firm/standings")
@firm_login_required
def standings():
    """The full-screen standings board on a team's own screen, in a
    classroom game whose teacher has turned it on.

    The same present.html the wall and sandbox use, not a copy. Frozen
    (hold=True, no auto-reload): a team reads it at their own pace, and a
    reload would replay the reveal. Exit returns to the dashboard, where
    the Results card is waiting.
    """
    world = current_world()
    if not (world.mode == "classroom" and world.show_standings_to_students):
        return redirect(url_for("firm.dashboard"))
    latest_round = latest_processed_round(world)
    if latest_round is None:
        return redirect(url_for("firm.dashboard"))

    session["standings_seen"] = _standings_key(world)
    scene = mall_scene(world)
    return render_template(
        "present.html",
        world=world,
        standings=standings_with_rank_delta(world),
        latest_round=latest_round,
        rounds_per_world=(world.rounds or ROUNDS_PER_WORLD),
        mall=scene,
        bay_width=mall_bay_width_css(len(scene)),
        hold=True,
        mall_intro=True,
        exit_url=url_for("firm.dashboard"),
        exit_title="Back to My Results",
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

    # "One submission per round, no edits" still holds in normal play. The
    # single exception is a round the teacher rewound with Undo Last Round:
    # their submission was deliberately kept, so editing it is the point.
    reopened = world.reopened_round == world.current_round
    existing = RoundDecision.query.filter_by(firm_id=firm.id, round_number=world.current_round).first()
    if existing and not reopened:
        flash("You've already submitted this round -- decisions can't be edited after submitting.")
        return redirect(url_for("firm.dashboard"))

    try:
        price = float(request.form["price"])
        production_qty = int(request.form["production_qty"])
        ad_spend = float(request.form.get("ad_spend") or 0)
        rd_spend = float(request.form.get("rd_spend") or 0)
        track = request.form["track"]
        # One endorser per round, or none. An unrecognised value is treated
        # as "none" rather than rejected -- the multipliers behind these are
        # hidden, so a bad value is a tampered form, not a student mistake
        # worth an error page.
        celebrity = request.form.get("celebrity") or None
        if celebrity not in CELEBRITIES:
            celebrity = None
        celebrity_on = celebrity is not None
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
    # Quality is tier-bound, so the cap is measured in the tier being SUBMITTED,
    # not the one the firm sold last round.
    rd_cap = max_rd_spend_this_round(rd_spend_in_track(firm.rd_spend_by_track, track))
    if rd_cap is None and rd_spend > 0:
        flash(f"You're already at the maximum Quality Level in the {track} tier -- more R&D there has no effect.")
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

    if existing:
        # Overwrite in place -- RoundDecision is "exactly one row per firm
        # per round" (models.py schema contract), so a reopened round must
        # update the kept row, never add a second one.
        existing.price = price
        existing.production_qty = production_qty
        existing.ad_spend = ad_spend
        existing.rd_spend = rd_spend
        existing.track = track
        existing.celebrity_on = celebrity_on
        existing.celebrity = celebrity
        existing.plant_investment = plant_investment
        existing.is_auto = False
    else:
        db.session.add(RoundDecision(
            firm_id=firm.id, round_number=world.current_round, price=price,
            production_qty=production_qty, ad_spend=ad_spend, rd_spend=rd_spend,
            track=track, celebrity_on=celebrity_on, celebrity=celebrity,
            plant_investment=plant_investment,
            is_auto=False,
        ))
    firm.last_price = price
    firm.last_track = track
    db.session.commit()

    # Sandbox: the player is the whole class, so there's nobody to wait for --
    # the round runs the moment they submit, straight to the results board.
    # Imported here, not at the top: sandbox imports the teacher blueprint,
    # and this keeps the firm module free of that dependency at load time.
    if world.mode == "sandbox":
        from app.blueprints.sandbox import run_sandbox_round
        return redirect(run_sandbox_round(world))

    flash("Decision updated!" if existing else "Decision submitted!")
    return redirect(url_for("firm.dashboard"))
