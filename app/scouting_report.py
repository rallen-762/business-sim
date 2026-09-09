"""
Scouting Report: a deterministic, rule-based summary of what the top 3
firms (by cumulative Revenue, cumulative Profit as tiebreak) are doing
differently, for the Teacher Dashboard.

Explicitly NOT an LLM call. An early instruction draft proposed generating
these summaries via the Anthropic API; that directly contradicts this
project's standing rule (confirmed at kickoff, restated in
master-variable-table.md Section 14: "Not connected to any LLM/API inside
the app"). Confirmed with the user to build this as deterministic template
logic instead -- same "what's driving this firm's results" narrative, zero
API dependency, zero per-round cost, nothing that can fail mid-class.

Edge cases considered:
 1. No round has been processed yet -> returns [] ; caller shows a
    "will appear after Round 1" placeholder rather than an empty table.
 2. Fewer than 3 registered firms with results -> reports however many
    exist (1, 2, or 3), never assumes exactly 3.
 3. "The field average" for a firm's decision inputs (Price, R&D spend, Ad
    spend, Plant Investment) is computed from THAT SAME round's decisions
    across all registered firms that submitted one -- "the field" means
    this round's competitors, not lifetime/cumulative behavior, since
    Price/Track/Celebrity are inherently per-round choices.
 4. A firm's "leading segment" is whichever segment (if any) it holds the
    single strictly-highest market share in among all firms, for the most
    recent round. A tie for the top share in a segment means no firm gets
    credited with leading it -- omitted, not arbitrarily picked.
 5. Division-by-zero guarded throughout (field averages on an empty firm
    list, percent-difference math against a zero average, segment share
    against zero total units sold).
 6. A top-3 firm that didn't submit a decision the most recent round
    (frozen/bankrupt, or somehow missing a row) still gets a rank and a
    short fallback sentence instead of crashing on a None decision.
"""

from app.constants import SEGMENTS
from app.models import Firm, RoundDecision, RoundResult


def _field_average(values):
    values = list(values)
    return sum(values) / len(values) if values else 0.0


def _pct_diff(value, average):
    if average == 0:
        return None
    return (value - average) / average * 100


def _segment_shares_for_round(round_results):
    """round_results: list of RoundResult for one round. Returns
    {segment: {firm_id: share_pct}} -- each firm's % of that segment's
    total units sold across all given firms."""
    totals = {seg: 0.0 for seg in SEGMENTS}
    for r in round_results:
        seg_units = r.units_sold_by_segment or {}
        for seg in SEGMENTS:
            totals[seg] += seg_units.get(seg, 0)

    shares = {seg: {} for seg in SEGMENTS}
    for r in round_results:
        seg_units = r.units_sold_by_segment or {}
        for seg in SEGMENTS:
            total = totals[seg]
            shares[seg][r.firm_id] = (seg_units.get(seg, 0) / total * 100) if total > 0 else 0.0
    return shares


def _leading_segment_for_firm(firm_id, segment_shares):
    """Returns (segment_name, share_pct) for the one segment this firm
    strictly leads (highest share, no tie), or None if it leads none."""
    best = None
    for seg, shares in segment_shares.items():
        my_share = shares.get(firm_id, 0)
        if my_share <= 0:
            continue
        max_share = max(shares.values())
        tied_leaders = sum(1 for s in shares.values() if s == max_share)
        if my_share == max_share and tied_leaders == 1:
            if best is None or my_share > best[1]:
                best = (seg, my_share)
    return best


def _build_summary(firm, decision, avg_price, avg_rd, avg_ad, segment_shares):
    if decision is None:
        return (
            f"{firm.team_name} didn't submit a decision last round, but still ranks "
            f"among the top performers on cumulative results."
        )

    parts = []

    price_diff = _pct_diff(decision.price, avg_price)
    if price_diff is not None and abs(price_diff) >= 1:
        direction = "above" if price_diff > 0 else "below"
        parts.append(
            f"Priced at ${decision.price:,.2f} on the {decision.track} track, "
            f"{abs(price_diff):.0f}% {direction} the field average of ${avg_price:,.2f}."
        )
    else:
        parts.append(f"Priced at ${decision.price:,.2f} on the {decision.track} track, in line with the field average.")

    rd_diff = _pct_diff(decision.rd_spend, avg_rd)
    if rd_diff is not None and rd_diff > 10:
        parts.append(f"Invested ${decision.rd_spend:,.0f} in R&D, well above the field average of ${avg_rd:,.0f}.")
    elif rd_diff is not None and rd_diff < -10:
        parts.append(f"Spent only ${decision.rd_spend:,.0f} on R&D, below the field average of ${avg_rd:,.0f}.")

    ad_diff = _pct_diff(decision.ad_spend, avg_ad)
    if ad_diff is not None and ad_diff > 10:
        parts.append(f"Outspent the field on advertising (${decision.ad_spend:,.0f} vs. ${avg_ad:,.0f} average).")
    elif ad_diff is not None and ad_diff < -10:
        parts.append(f"Spent less than the field on advertising (${decision.ad_spend:,.0f} vs. ${avg_ad:,.0f} average).")

    if decision.celebrity_on:
        parts.append("Ran a Celebrity Endorsement this round.")

    if decision.plant_investment > 0:
        parts.append("Invested in expanding plant capacity this round.")

    leading = _leading_segment_for_firm(firm.id, segment_shares)
    if leading:
        seg_name, share = leading
        parts.append(f"Their strongest segment was {seg_name}, capturing {share:.0f}% of that segment's sales.")

    return " ".join(parts)


def build_scouting_report(world):
    """Returns up to 3 dicts (top firms by cumulative Revenue, cumulative
    Profit as tiebreak), each: rank, team_name, cum_revenue, cum_profit,
    summary. Returns [] if no round has been processed yet."""
    from app.market_data import cumulative_standings, latest_processed_round

    latest_round = latest_processed_round(world)
    if latest_round is None:
        return []

    standings = cumulative_standings(world)
    if not standings:
        return []

    # cumulative_standings() sorts by profit; this feature ranks by
    # revenue first, profit as the tiebreak -- re-sort explicitly.
    ranked = sorted(standings, key=lambda row: (-row["cum_revenue"], -row["cum_profit"]))
    top_firms = ranked[:3]

    round_decisions = {
        d.firm_id: d
        for d in RoundDecision.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundDecision.round_number == latest_round)
    }
    round_results = [
        r for r in RoundResult.query.join(Firm)
        .filter(Firm.world_id == world.id, RoundResult.round_number == latest_round)
        if r.firm.is_registered
    ]
    segment_shares = _segment_shares_for_round(round_results)

    decisions_list = list(round_decisions.values())
    avg_price = _field_average(d.price for d in decisions_list)
    avg_rd = _field_average(d.rd_spend for d in decisions_list)
    avg_ad = _field_average(d.ad_spend for d in decisions_list)

    report = []
    for rank, row in enumerate(top_firms, start=1):
        firm = row["firm"]
        decision = round_decisions.get(firm.id)
        report.append({
            "rank": rank,
            "team_name": firm.team_name,
            "cum_revenue": row["cum_revenue"],
            "cum_profit": row["cum_profit"],
            "summary": _build_summary(firm, decision, avg_price, avg_rd, avg_ad, segment_shares),
        })
    return report
