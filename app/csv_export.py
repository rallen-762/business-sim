"""
End-of-game CSV export -- one row per firm per round, flattening
RoundDecision + RoundResult together so a teacher can open a whole class
period's full history directly in Sheets/Excel.

Kept as a plain, DB-reading-but-otherwise-pure module (build_export_rows
takes a World and returns plain dicts; rows_to_csv_string takes plain dicts
and returns a string) so both halves are unit-testable without going
through a Flask request/response at all -- the route in teacher.py is just
a thin wrapper that sets the download headers.

Edge cases considered:
 1. No rounds processed yet -> header row only, no crash on an empty world.
 2. Unclaimed/never-registered firm slots have no RoundResult rows at all
    (they never played) -- naturally excluded, no special-casing needed.
 3. A bankrupt firm still gets a row for every round it was bankrupt in
    (the engine emits a frozen result row every round) -- included, not
    filtered out, matching the docs' "stays listed/marked" rule.
 4. Rows are sorted by (firm slot_number, round_number) so the export reads
    in a sensible order rather than arbitrary DB insertion order.
 5. Numbers are exported raw (no $ or comma formatting) so a teacher can
    sort/formula against them directly in a spreadsheet -- formatting
    belongs in the UI templates, not in data meant for re-analysis.
 6. units_sold_by_segment is flattened into one column per segment, always
    present (defaults to 0) even if a segment key is somehow missing, so
    every row has the same shape regardless of which segments sold.
 7. Available at any time, not gated on world.status == "complete" -- a
    teacher may want a mid-game snapshot; "end-of-game" describes the
    typical use, not a hard restriction the docs actually state.
"""

import csv
import io

from app.constants import SEGMENTS, segment_label
from app.models import Firm, RoundDecision, RoundResult

FIELDNAMES = (
    ["Firm Slot", "Team Name", "Round", "Bankrupt This Round"]
    + ["Price", "Tier", "Production Qty", "R&D Spend", "Ad Spend",
       "Celebrity On", "Plant Investment", "Auto (Non-Submission)"]
    + [f"Units Sold - {segment_label(seg)}" for seg in SEGMENTS]
    + ["Units Sold Total", "Revenue", "Production Cost",
       "Rent, Utilities & Labor", "Ad Cost", "R&D Cost", "Celebrity Cost",
       "Plant Investment Cost", "Total Cost", "Profit",
       "Cash Before", "Cash After", "Quality Level", "Ad Level",
       "Plant Capacity", "Loan Taken This Round", "Loan Principal Paid",
       "Loan Interest Charged", "Loan Outstanding After",
       "Went Bankrupt This Round", "Plant Investment Blocked", "Celebrity Blocked"]
)


def build_export_rows(world):
    """Returns a list of dict rows (keys matching FIELDNAMES) for every
    (firm, round) that has a RoundResult in this world, sorted by firm slot
    then round number. Safe to call at any point in the game, including
    before a single round has been processed (returns [])."""
    firms_by_id = {f.id: f for f in Firm.query.filter_by(world_id=world.id).all()}

    results = (
        RoundResult.query.join(Firm, Firm.id == RoundResult.firm_id)
        .filter(Firm.world_id == world.id)
        .all()
    )

    decisions_by_key = {
        (d.firm_id, d.round_number): d
        for d in RoundDecision.query.join(Firm, Firm.id == RoundDecision.firm_id)
        .filter(Firm.world_id == world.id).all()
    }

    sortable_rows = []
    for r in results:
        firm = firms_by_id.get(r.firm_id)
        d = decisions_by_key.get((r.firm_id, r.round_number))
        segment_units = r.units_sold_by_segment or {}

        row = {
            "Firm Slot": firm.slot_number if firm else "",
            "Team Name": firm.team_name if firm else "",
            "Round": r.round_number,
            "Bankrupt This Round": r.is_bankrupt,
            "Price": d.price if d else "",
            "Tier": d.track if d else "",
            "Production Qty": d.production_qty if d else "",
            "R&D Spend": d.rd_spend if d else "",
            "Ad Spend": d.ad_spend if d else "",
            "Celebrity On": d.celebrity_on if d else "",
            "Plant Investment": d.plant_investment if d else "",
            "Auto (Non-Submission)": d.is_auto if d else "",
            "Units Sold Total": r.units_sold_total,
            "Revenue": r.revenue,
            "Production Cost": r.production_cost,
            "Rent, Utilities & Labor": r.fixed_cost,
            "Ad Cost": r.ad_cost,
            "R&D Cost": r.rd_cost,
            "Celebrity Cost": r.celebrity_cost,
            "Plant Investment Cost": r.plant_investment_cost,
            "Total Cost": r.total_cost,
            "Profit": r.profit,
            "Cash Before": r.cash_before,
            "Cash After": r.cash_after,
            "Quality Level": r.quality_level,
            "Ad Level": r.ad_level,
            "Plant Capacity": r.plant_capacity,
            "Loan Taken This Round": r.loan_taken_this_round,
            "Loan Principal Paid": r.loan_principal_paid,
            "Loan Interest Charged": r.loan_interest_charged,
            "Loan Outstanding After": r.loan_outstanding_after,
            "Went Bankrupt This Round": r.went_bankrupt_this_round,
            "Plant Investment Blocked": r.plant_investment_blocked,
            "Celebrity Blocked": r.celebrity_blocked,
        }
        for seg in SEGMENTS:
            row[f"Units Sold - {segment_label(seg)}"] = segment_units.get(seg, 0)

        sort_key = (firm.slot_number if firm else 0, r.round_number)
        sortable_rows.append((sort_key, row))

    sortable_rows.sort(key=lambda pair: pair[0])
    return [row for _, row in sortable_rows]


def rows_to_csv_string(rows):
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=FIELDNAMES)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue()


def export_filename(world):
    safe_name = "".join(c if c.isalnum() else "_" for c in world.name).strip("_") or "world"
    return f"{safe_name}_{world.game_code}_export.csv"
