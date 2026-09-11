"""
Shared query/computation logic for the Market Dashboard and the
Competitive Intelligence Report, used by both the firm-facing routes
(app/blueprints/market.py, scoped to the logged-in firm's own world) and
the teacher-facing routes (app/blueprints/teacher.py, scoped to whichever
world_id the teacher is looking at). Kept in one place so the two call
sites can never drift on what "market share," "cumulative," or "leading
track" means.

Edge cases considered:
 1. Unregistered/unclaimed firm slots never appear anywhere here -- they
    have no RoundResult rows (per the earlier fix excluding them from
    process_round), and cumulative/standings queries only ever look at
    RoundResult, so this is automatic, not a separate filter to remember.
 2. Zero rounds processed yet -- every function here degrades to an empty
    list / None rather than crashing; templates render an explicit
    "no data yet" state for that case.
 3. Market share divides by the round's TOTAL units sold across all firms
    -- guarded against division by zero (a round where literally nobody
    sold anything, e.g. universal extreme overpricing) by returning 0% for
    everyone rather than crashing.
 4. Cumulative Price has no sensible meaning (summing a price across
    rounds is nonsensical) -- cumulative_standings() reports Price as a
    current/latest-round snapshot, while Units Sold/Revenue/Profit are
    true lifetime sums. Confirmed with the user.
 5. Ties in cumulative profit are broken by firm slot_number for a stable,
    deterministic sort -- arbitrary but consistent, not a locked
    requirement.
 6. "Leading Track" per segment needs a track breakdown that RoundResult
    doesn't store directly (it only stores units sold by SEGMENT, not by
    segment+track) -- reconstructed by pairing each firm's per-segment
    units for the round with that same firm's RoundDecision.track for the
    same round, then summing per track. A segment with zero total sales
    that round has no leading track (None), not an arbitrary pick.
 7. The Competitive Intelligence Report deliberately OMITS plant capacity,
    cash balance, and R&D spend from its returned rows entirely (not just
    hidden by the template) -- so a future column added elsewhere can't
    get copy-pasted into this report and leak a number that's supposed to
    stay hidden from rival firms.
 8. The Competitive Intelligence Report's "loan flag" is a boolean (is this
    firm carrying debt at all), never the dollar amount -- matching the
    locked spec's "no dollar amount shown, just a flag."
"""

from app.constants import SEGMENT_BUYER_COUNT, SEGMENTS, TRACKS
from app.extensions import db
from app.models import Firm, RoundDecision, RoundResult, SegmentRoundResult

# 8 muted, industrial-palette hues -- distinguishable enough for up to 8
# firms in a pie legend, but no bright saturated defaults (the old set was
# literally a charting library's stock blue/orange/green/red/purple/cyan/
# yellow/pink). Single source of truth: the pie itself (build_pie_gradient),
# its on-chart percentage labels, and its legend swatches (all in
# market_dashboard.html, via pie_slices() below) use this same tuple -- no
# second hardcoded copy to drift out of sync.
PIE_COLORS = (
    "#6FA8A0",  # accent-primary teal
    "#C9A227",  # muted gold
    "#8FBF8F",  # sage green
    "#B5651D",  # rust orange
    "#7A8FA6",  # steel blue-grey
    "#A85C7A",  # dusty rose
    "#4A5A5E",  # panel-alt slate
    "#D9A05B",  # warm tan
)

# Text color for a label drawn ON TOP of the matching PIE_COLORS entry
# (same index) -- computed once from each color's relative luminance
# (standard 0.2126R+0.7152G+0.0722B sRGB weighting) rather than guessed, so
# every on-chart label stays readable against its own slice regardless of
# whether that slice's color happens to be light or dark.
PIE_TEXT_COLORS = (
    "#14171A",  # on #6FA8A0 (bg-base -- dark text, that teal is fairly light)
    "#14171A",  # on #C9A227
    "#14171A",  # on #8FBF8F
    "#F0F0EC",  # on #B5651D (text-primary -- light text, that rust is darker)
    "#14171A",  # on #7A8FA6
    "#F0F0EC",  # on #A85C7A
    "#F0F0EC",  # on #4A5A5E
    "#14171A",  # on #D9A05B
)


def latest_processed_round(world):
    """The highest round_number with at least one RoundResult in this
    world, or None if no round has been processed yet."""
    return (
        db.session.query(db.func.max(RoundResult.round_number))
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .scalar()
    )


def latest_round_results(world):
    """Returns (results, round_shown) for the most recently processed
    round. round_shown is None (results []) if none processed yet."""
    round_shown = latest_processed_round(world)
    if round_shown is None:
        return [], None
    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == round_shown)
        .all()
    )
    return results, round_shown


def cumulative_standings(world):
    """Returns a list of dicts, one per REGISTERED firm that has played at
    least one round, sorted by cumulative profit descending (ties broken
    by slot_number). Each dict: firm, latest_price, cum_units, cum_revenue,
    cum_profit, bar_pct (0-100, this firm's |cum_profit| as a percentage of
    the largest |cum_profit| across all firms -- for the Market Dashboard's
    animated standings bar chart; 0 for every firm if the whole field is at
    exactly $0). Empty list if no round has been processed yet."""
    latest_round = latest_processed_round(world)
    if latest_round is None:
        return []

    cumulative_rows = (
        db.session.query(
            RoundResult.firm_id,
            db.func.sum(RoundResult.units_sold_total).label("cum_units"),
            db.func.sum(RoundResult.revenue).label("cum_revenue"),
            db.func.sum(RoundResult.profit).label("cum_profit"),
        )
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .group_by(RoundResult.firm_id)
        .all()
    )
    cumulative_by_firm = {row.firm_id: row for row in cumulative_rows}

    # "Latest price" is that firm's most recent submitted/auto decision,
    # not necessarily from the same round for every firm (a firm could be
    # bankrupt and frozen while others keep playing) -- so it's looked up
    # per-firm as "their own most recent decision," not "the world's
    # current round's decision."
    latest_decision_by_firm = {}
    for firm_id in cumulative_by_firm:
        d = (
            RoundDecision.query.filter_by(firm_id=firm_id)
            .order_by(RoundDecision.round_number.desc())
            .first()
        )
        if d:
            latest_decision_by_firm[firm_id] = d

    firms_by_id = {f.id: f for f in Firm.query.filter_by(world_id=world.id).all()}

    rows = []
    for firm_id, c in cumulative_by_firm.items():
        firm = firms_by_id.get(firm_id)
        if firm is None or not firm.is_registered:
            continue
        d = latest_decision_by_firm.get(firm_id)
        rows.append({
            "firm": firm,
            "latest_price": d.price if d else None,
            "cum_units": c.cum_units or 0,
            "cum_revenue": c.cum_revenue or 0,
            "cum_profit": c.cum_profit or 0,
        })

    rows.sort(key=lambda row: (-row["cum_profit"], row["firm"].slot_number))

    max_abs_profit = max((abs(row["cum_profit"]) for row in rows), default=0)
    for row in rows:
        row["bar_pct"] = (abs(row["cum_profit"]) / max_abs_profit * 100) if max_abs_profit > 0 else 0

    return rows


def round_totals(world, round_number):
    """Returns a list of dicts, one per registered firm with a RoundResult
    for this specific round, sorted by THAT round's profit descending.
    Each dict: firm, price, units, revenue, profit."""
    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == round_number)
        .all()
    )
    decisions_by_firm = {
        d.firm_id: d
        for d in RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == round_number)
    }

    rows = []
    for r in results:
        if not r.firm.is_registered:
            continue
        d = decisions_by_firm.get(r.firm_id)
        rows.append({
            "firm": r.firm,
            "price": d.price if d else None,
            "units": r.units_sold_total,
            "revenue": r.revenue,
            "profit": r.profit,
        })

    rows.sort(key=lambda row: (-row["profit"], row["firm"].slot_number))
    return rows


def market_shares_for_round(world, round_number):
    """Returns a list of dicts (firm, units, share_pct) for firms with a
    result in this round, share_pct out of the round's total units sold
    across all (registered) firms. All-zero (nobody sold anything) yields
    0% for everyone rather than dividing by zero."""
    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == round_number)
        .all()
    )
    registered = [r for r in results if r.firm.is_registered]
    total_units = sum(r.units_sold_total for r in registered)

    rows = []
    for r in registered:
        share_pct = (r.units_sold_total / total_units * 100) if total_units > 0 else 0
        rows.append({"firm": r.firm, "units": r.units_sold_total, "share_pct": share_pct})

    rows.sort(key=lambda row: (-row["share_pct"], row["firm"].slot_number))
    return rows


def build_pie_gradient(shares):
    """Takes [{"share_pct": float, ...}] (as produced by
    market_shares_for_round) and returns a CSS conic-gradient() string,
    cycling PIE_COLORS if there are more firms than colors. Returns a flat
    neutral gradient if every share is 0 (nobody sold anything)."""
    total_pct = sum(row["share_pct"] for row in shares)
    if not shares or total_pct <= 0:
        return "conic-gradient(#4A5A5E 0% 100%)"  # --panel-alt -- was a light-theme grey, unreadable on the dark theme

    stops = []
    cursor = 0.0
    for i, row in enumerate(shares):
        color = PIE_COLORS[i % len(PIE_COLORS)]
        start = cursor
        cursor += row["share_pct"]
        stops.append(f"{color} {start:.4f}% {cursor:.4f}%")
    return "conic-gradient(" + ", ".join(stops) + ")"


def pie_slices(shares):
    """Takes the same [{"firm":.., "share_pct":..}, ...] as
    build_pie_gradient/market_shares_for_round and returns one dict per
    slice with everything the template needs to draw an on-chart
    percentage label directly on top of the pie (not just in the separate
    legend list) -- color/text_color (paired for contrast, see
    PIE_TEXT_COLORS), and mid_deg: the angle, in degrees clockwise from 12
    o'clock, at this slice's midpoint. A label is positioned at that angle
    with a single CSS transform -- rotate(mid_deg) translate(0, -R)
    rotate(-mid_deg) -- rotate to the angle, push outward along it, rotate
    back so the text itself stays upright; no trig needed in the template.
    Skips slices under min_pct (default 4%) entirely -- a label crammed
    into a sliver a few px wide is illegible clutter, not information; that
    firm is still fully represented in the external legend list."""
    slices = []
    cursor = 0.0
    for i, row in enumerate(shares):
        start = cursor
        cursor += row["share_pct"]
        if row["share_pct"] >= 4:
            slices.append({
                "firm": row.get("firm"),
                "share_pct": row["share_pct"],
                "color": PIE_COLORS[i % len(PIE_COLORS)],
                "text_color": PIE_TEXT_COLORS[i % len(PIE_TEXT_COLORS)],
                "mid_deg": (start + row["share_pct"] / 2) / 100 * 360,
            })
    return slices


def segment_overview(world, round_number):
    """Returns a list of dicts, one per SEGMENTS entry (in SEGMENTS order):
    name, relative_size_pct (static -- a segment's fixed share of the total
    buyer pool, unrelated to any round), units_sold_this_round (summed
    across all firms), leading_track (whichever of Budget/Standard/Premium
    captured the most units in that segment this round, or None if the
    segment had zero sales this round), avg_consumer_surplus and
    unsold_buyers_pct (from the willingness-to-pay sweep -- see
    consumer_surplus_by_segment; merged in here so the Market Dashboard's
    existing Customer Segments card can show it without a second query at
    each call site -- None/0 if that round's segment stats aren't
    available for any reason)."""
    total_buyers = sum(SEGMENT_BUYER_COUNT.values())
    surplus_by_name = {row["name"]: row for row in consumer_surplus_by_segment(world, round_number)}

    overview = []
    if round_number is None:
        for seg in SEGMENTS:
            overview.append({
                "name": seg,
                "relative_size_pct": SEGMENT_BUYER_COUNT[seg] / total_buyers * 100,
                "units_sold_this_round": 0,
                "leading_track": None,
                "avg_consumer_surplus": None,
                "unsold_buyers_pct": 0,
            })
        return overview

    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == round_number)
        .all()
    )
    decisions_by_firm = {
        d.firm_id: d
        for d in RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == round_number)
    }

    for seg in SEGMENTS:
        units_by_track = {t: 0.0 for t in TRACKS}
        total_units = 0.0
        for r in results:
            if not r.firm.is_registered:
                continue
            seg_units = (r.units_sold_by_segment or {}).get(seg, 0)
            total_units += seg_units
            d = decisions_by_firm.get(r.firm_id)
            if d and d.track in units_by_track:
                units_by_track[d.track] += seg_units

        leading_track = None
        best = max(units_by_track.values()) if units_by_track else 0
        if best > 0:
            for track in TRACKS:  # deterministic tie-break order
                if units_by_track[track] == best:
                    leading_track = track
                    break

        surplus_row = surplus_by_name.get(seg)
        overview.append({
            "name": seg,
            "relative_size_pct": SEGMENT_BUYER_COUNT[seg] / total_buyers * 100,
            "units_sold_this_round": total_units,
            "leading_track": leading_track,
            "avg_consumer_surplus": surplus_row["avg_consumer_surplus"] if surplus_row else None,
            "unsold_buyers_pct": surplus_row["unsold_buyers_pct"] if surplus_row else 0,
        })

    return overview


def consumer_surplus_by_segment(world, round_number):
    """Returns a list of dicts (in SEGMENTS order), one per segment, for the
    Teacher Dashboard's Average Consumer Surplus by Segment card: name,
    avg_consumer_surplus (None if nobody in that segment could afford
    anyone that round -- see engine.SegmentDemandStats), unsold_buyers
    (fractional headcount), unsold_buyers_pct. Returns [] (same "nothing to
    show yet" convention as build_scouting_report) if round_number is None
    or that round simply hasn't been processed/persisted yet -- the caller
    doesn't need to check latest_processed_round() separately."""
    if round_number is None:
        return []

    rows_by_segment = {
        row.segment: row
        for row in SegmentRoundResult.query.filter_by(world_id=world.id, round_number=round_number)
    }
    if not rows_by_segment:
        return []

    return [
        {
            "name": seg,
            "avg_consumer_surplus": rows_by_segment[seg].avg_consumer_surplus if seg in rows_by_segment else None,
            "unsold_buyers": rows_by_segment[seg].unsold_buyers if seg in rows_by_segment else 0,
            "unsold_buyers_pct": rows_by_segment[seg].unsold_buyers_pct if seg in rows_by_segment else 0,
        }
        for seg in SEGMENTS
    ]


def competitive_intel_rows(world, round_number):
    """Returns a list of dicts, one per registered firm with a result in
    this round -- ONLY the fields the locked spec says are visible:
    team_name, price, track, quality_level, ad_spend, units_sold,
    market_share_pct, carrying_debt (bool). Plant capacity, cash balance,
    and R&D spend are never included in this dict at all -- deliberately,
    not just left out of a template."""
    shares = market_shares_for_round(world, round_number)
    share_by_firm = {row["firm"].id: row["share_pct"] for row in shares}

    results = (
        RoundResult.query
        .join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id, RoundResult.round_number == round_number)
        .all()
    )
    decisions_by_firm = {
        d.firm_id: d
        for d in RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == round_number)
    }

    rows = []
    for r in results:
        if not r.firm.is_registered:
            continue
        d = decisions_by_firm.get(r.firm_id)
        rows.append({
            "team_name": r.firm.team_name,
            "price": d.price if d else None,
            "track": d.track if d else None,
            "quality_level": r.quality_level,
            "ad_spend": d.ad_spend if d else None,
            "units_sold": r.units_sold_total,
            "market_share_pct": share_by_firm.get(r.firm_id, 0),
            "carrying_debt": r.loan_outstanding_after > 0,
        })

    rows.sort(key=lambda row: -row["market_share_pct"])
    return rows
