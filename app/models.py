"""
SQLAlchemy models for the Business Simulation Game.

Edge cases considered while designing this schema:
 1. Duplicate/resubmitted decisions: a (firm_id, round_number) unique
    constraint on RoundDecision blocks a second row at the DB level.
    "No edit after submit" is enforced in the route layer (check-then-insert)
    -- this constraint is the last-resort backstop against a race/bug, not
    the primary enforcement mechanism.
 2. Non-submitting firms still need a decision row for a complete history --
    the round-advance job inserts a synthesized RoundDecision (is_auto=True)
    for every firm that didn't submit, so RoundDecision is always exactly
    1 row per firm per round, submitted or not.
 3. Reprocessing a round (a teacher/bug-fix scenario) isn't a designed
    feature yet -- the unique constraints mean an app-level reprocess must
    delete the prior RoundResult/RoundDecision rows first; this schema does
    not try to silently support an overwrite-in-place.
 4. Bankruptcy is permanent once set (Firm.bankrupt) -- never cleared by
    any code path.
 5. The loan is usable once per firm PER WORLD, ever -- Firm.loan_used_ever
    lives on Firm (which already belongs to exactly one World), so the scope
    is automatically correct without extra bookkeeping.
 6. Team names only need to be unique WITHIN a world -- two different class
    periods can each have a team named "Nike". Enforced via a composite
    unique constraint (world_id, team_name), not a global unique column.
 7. Game codes must be globally unique -- it's the only thing identifying
    which World a login belongs to before a team is even known.
 8. Passwords are hashed (werkzeug.security), never stored in plaintext,
    even though the login flow itself is intentionally simple (Game Code +
    Team Password, no token refresh).
 9. Rounds are numbered 1..ROUNDS_PER_WORLD (not 0-based), matching how the
    teacher-facing docs refer to "Round 1" through "Round 10".
10. World.status distinguishes "collecting decisions this round" from
    "round just processed / showing transition notice" from "game complete"
    -- needed to gate the Firm Dashboard (lock the form after submit, or
    after the teacher has already advanced the round) and to drive the
    Round Transition Notice banner.
11. Firm's capacity/pending_capacity_increase/cumulative-spend/loan fields
    are the durable, persisted mirror of engine.FirmState -- they get
    updated from each round's RoundResult right after process_round() runs.
    They are NOT recomputed from history on every read.
12. Deleting a World cascades to its Firms, and deleting a Firm cascades to
    its RoundDecisions/RoundResults, so a teacher can discard a whole class
    period's game without leaving orphaned rows.
13. `avatar` is a nullable free-form string for now (graphics-pack
    integration is a later pass) so this schema won't need to change shape
    when that screen gets built.
14. Money fields use Float rather than Numeric/Decimal -- this is a
    classroom simulation, not a financial ledger, and the source docs'
    formulas already operate in floats; exact-decimal rounding isn't a
    stated requirement anywhere in the locked design.
15. Registration flow (confirmed): the teacher sets a firm count when
    creating a World, and that many empty Firm slots are pre-created
    (team_name/password_hash/avatar all null). First-time login shows the
    unclaimed slots for that Game Code; a team picks one, then sets its
    password, team name, and avatar together. So team_name and
    password_hash are both null pre-registration and both get set in the
    same registration step -- Firm.slot_number is what identifies a slot
    ("Firm 3") before a team name exists, and is unique per world so the
    unclaimed-slots list has a stable, non-guessable key. Multiple
    unclaimed slots in the same world all have team_name=NULL
    simultaneously; the (world_id, team_name) unique constraint still holds
    because SQL treats NULLs as distinct from each other for uniqueness
    purposes, in both SQLite and Postgres.
16. Bot-controlled firms (Firm.bot_profile) reuse the exact same
    is_registered/participation path as a human firm -- a bot slot gets a
    real (if unusable/random) password_hash and a placeholder team_name at
    assignment time, so process_round's "only registered firms compete"
    filter needs no bot-aware branching at all. Removing a bot clears
    team_name/password_hash/bot_profile back to null (a true unclaimed
    slot again) but deliberately leaves cash/plant_capacity/cumulative
    spend/RoundDecision/RoundResult history untouched on the same Firm
    row -- letting a real student register that slot afterward picks up
    exactly where the bot left off, which is the whole point of using a
    bot to fill a no-show's slot mid-game rather than a placeholder that
    gets discarded.
17. `badge` (added for the Headphone Company Simulator asset pack) mirrors
    `avatar`'s nullable-string, assigned-not-validated-here shape exactly
    -- a second per-firm icon (a logo/emblem, see app/avatars.py's
    BADGE_CHOICES) alongside the building/factory `avatar`, auto-assigned
    at registration/bot-assignment time rather than picked, so it has no
    picker UI of its own.
"""

from datetime import datetime, timezone

from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class World(db.Model):
    __tablename__ = "worlds"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)  # e.g. "Period 3"
    game_code = db.Column(db.String(20), unique=True, nullable=False, index=True)
    planned_firm_slots = db.Column(db.Integer, nullable=False)  # set at World creation; pre-creates this many empty Firm rows

    current_round = db.Column(db.Integer, nullable=False, default=1)
    # "collecting" (default, teams submitting) | "processing" (round-advance
    # job running) | "transition" (round just processed, showing the Round
    # Transition Notice banner) | "complete" (round 10 processed, game over)
    status = db.Column(db.String(20), nullable=False, default="collecting")

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    firms = db.relationship("Firm", backref="world", cascade="all, delete-orphan")
    segment_round_results = db.relationship("SegmentRoundResult", backref="world", cascade="all, delete-orphan")

    __table_args__ = (
        db.CheckConstraint("current_round >= 1", name="ck_world_current_round_min"),
        db.CheckConstraint("planned_firm_slots >= 1", name="ck_world_planned_firm_slots_min"),
    )

    def __repr__(self):
        return f"<World {self.game_code} round={self.current_round} status={self.status}>"


class Firm(db.Model):
    __tablename__ = "firms"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    slot_number = db.Column(db.Integer, nullable=False)  # 1..planned_firm_slots; identifies an unclaimed slot before team_name exists
    team_name = db.Column(db.String(80), nullable=True)  # null until first-time registration
    password_hash = db.Column(db.String(255), nullable=True)  # null until first-time registration
    avatar = db.Column(db.String(120), nullable=True)  # set at registration; null until then
    badge = db.Column(db.String(120), nullable=True)  # set at registration/bot-assignment; null until then
    # One of app.bots.BOT_PROFILES' keys ("underbidder"/"marketing"/"elite"/
    # "random"), or None for a human-controlled firm. A bot-assigned slot
    # also gets a placeholder team_name + unusable password_hash (see
    # teacher.assign_bot) so is_registered is True and it participates in
    # process_round like any other firm -- bot_profile is the single source
    # of truth for "is this a bot," never inferred from the password shape.
    bot_profile = db.Column(db.String(20), nullable=True)

    cash = db.Column(db.Float, nullable=False)
    plant_capacity = db.Column(db.Integer, nullable=False)
    pending_capacity_increase = db.Column(db.Integer, nullable=False, default=0)
    cumulative_rd_spend = db.Column(db.Float, nullable=False, default=0)
    cumulative_ad_spend = db.Column(db.Float, nullable=False, default=0)
    loan_outstanding = db.Column(db.Float, nullable=False, default=0)
    loan_used_ever = db.Column(db.Boolean, nullable=False, default=False)
    bankrupt = db.Column(db.Boolean, nullable=False, default=False)

    # Carried forward for synthesizing next round's auto-decision on non-submission.
    last_price = db.Column(db.Float, nullable=True)
    last_track = db.Column(db.String(20), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    decisions = db.relationship(
        "RoundDecision", backref="firm", cascade="all, delete-orphan", lazy="dynamic"
    )
    results = db.relationship(
        "RoundResult", backref="firm", cascade="all, delete-orphan", lazy="dynamic"
    )

    __table_args__ = (
        db.UniqueConstraint("world_id", "team_name", name="uq_firm_world_team_name"),
        db.UniqueConstraint("world_id", "slot_number", name="uq_firm_world_slot_number"),
    )

    def register(self, team_name, raw_password, avatar=None, badge=None):
        """First-time registration: claims this slot by setting team name,
        password, avatar, and badge together. Only valid on an unclaimed
        slot -- callers should check is_registered first
        (re-registration/renaming after the fact is a separate,
        not-yet-designed feature)."""
        self.team_name = team_name
        self.password_hash = generate_password_hash(raw_password)
        self.avatar = avatar
        self.badge = badge

    def set_password(self, raw_password):
        self.password_hash = generate_password_hash(raw_password)

    def check_password(self, raw_password):
        if not self.password_hash:
            return False
        return check_password_hash(self.password_hash, raw_password)

    @property
    def is_registered(self):
        return self.password_hash is not None

    def __repr__(self):
        return f"<Firm slot={self.slot_number} {self.team_name!r} world_id={self.world_id}>"


class RoundDecision(db.Model):
    __tablename__ = "round_decisions"

    id = db.Column(db.Integer, primary_key=True)
    firm_id = db.Column(db.Integer, db.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)

    price = db.Column(db.Float, nullable=False)
    production_qty = db.Column(db.Integer, nullable=False)
    ad_spend = db.Column(db.Float, nullable=False, default=0)
    rd_spend = db.Column(db.Float, nullable=False, default=0)
    track = db.Column(db.String(20), nullable=False)
    celebrity_on = db.Column(db.Boolean, nullable=False, default=False)
    plant_investment = db.Column(db.Integer, nullable=False, default=0)  # 0 or PLANT_INVESTMENT_COST
    is_auto = db.Column(db.Boolean, nullable=False, default=False)

    submitted_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("firm_id", "round_number", name="uq_decision_firm_round"),
    )

    def __repr__(self):
        return f"<RoundDecision firm_id={self.firm_id} round={self.round_number} auto={self.is_auto}>"


class RoundResult(db.Model):
    __tablename__ = "round_results"

    id = db.Column(db.Integer, primary_key=True)
    firm_id = db.Column(db.Integer, db.ForeignKey("firms.id", ondelete="CASCADE"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)

    units_sold_by_segment = db.Column(db.JSON, nullable=False)  # {segment_name: units}
    units_sold_total = db.Column(db.Float, nullable=False)  # fractional -- capacity-scaling (engine Step 7) legitimately produces non-integer units
    revenue = db.Column(db.Float, nullable=False)
    production_cost = db.Column(db.Float, nullable=False)
    fixed_cost = db.Column(db.Float, nullable=False)
    ad_cost = db.Column(db.Float, nullable=False)
    rd_cost = db.Column(db.Float, nullable=False)
    celebrity_cost = db.Column(db.Float, nullable=False)
    plant_investment_cost = db.Column(db.Float, nullable=False)
    total_cost = db.Column(db.Float, nullable=False)
    profit = db.Column(db.Float, nullable=False)
    cash_before = db.Column(db.Float, nullable=False)
    cash_after = db.Column(db.Float, nullable=False)

    quality_level = db.Column(db.Integer, nullable=False)
    ad_level = db.Column(db.Integer, nullable=False)
    plant_capacity = db.Column(db.Integer, nullable=False)
    new_pending_capacity_increase = db.Column(db.Integer, nullable=False, default=0)

    loan_taken_this_round = db.Column(db.Boolean, nullable=False, default=False)
    loan_principal_paid = db.Column(db.Float, nullable=False, default=0)
    loan_interest_charged = db.Column(db.Float, nullable=False, default=0)
    loan_outstanding_after = db.Column(db.Float, nullable=False, default=0)
    loan_used_ever_after = db.Column(db.Boolean, nullable=False, default=False)

    went_bankrupt_this_round = db.Column(db.Boolean, nullable=False, default=False)
    is_bankrupt = db.Column(db.Boolean, nullable=False, default=False)
    is_auto = db.Column(db.Boolean, nullable=False, default=False)
    plant_investment_blocked = db.Column(db.Boolean, nullable=False, default=False)
    celebrity_blocked = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("firm_id", "round_number", name="uq_result_firm_round"),
    )

    def __repr__(self):
        return f"<RoundResult firm_id={self.firm_id} round={self.round_number} profit={self.profit}>"


class SegmentRoundResult(db.Model):
    """One row per (world, round, segment) -- the SEGMENT-level (not
    firm-level) outcome of engine.process_round()'s individual buyer
    willingness-to-pay sweep (see engine.SegmentDemandStats), persisted so
    the Teacher Dashboard's Average Consumer Surplus by Segment card can
    read past rounds without re-running the engine. Lives on World directly
    (not Firm) since a segment's buyers aren't owned by any one firm."""
    __tablename__ = "segment_round_results"

    id = db.Column(db.Integer, primary_key=True)
    world_id = db.Column(db.Integer, db.ForeignKey("worlds.id", ondelete="CASCADE"), nullable=False)
    round_number = db.Column(db.Integer, nullable=False)
    segment = db.Column(db.String(40), nullable=False)

    total_buyers = db.Column(db.Float, nullable=False)
    unsold_buyers = db.Column(db.Float, nullable=False)
    unsold_buyers_pct = db.Column(db.Float, nullable=False)
    # Null when NO buyer in this segment could afford anyone this round --
    # not 0.0, which would misleadingly claim "buyers broke even" instead
    # of "there was no one to measure" (see engine.SegmentDemandStats).
    avg_consumer_surplus = db.Column(db.Float, nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("world_id", "round_number", "segment", name="uq_segment_result_world_round_segment"),
    )

    def __repr__(self):
        return f"<SegmentRoundResult world_id={self.world_id} round={self.round_number} segment={self.segment!r}>"
