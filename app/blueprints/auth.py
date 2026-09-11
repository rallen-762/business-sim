"""
Login / registration routes.

Edge cases considered:
 1. Unknown game code -> friendly re-prompt, not a 404 (students mistype).
 2. A slot that's already claimed must not be re-registerable -- registering
    redirects to the password-login route instead if is_registered is True.
 3. Double-submitting the registration form for the same slot (e.g. double-
    click, or a second browser tab) -- the (world_id, team_name) and
    (world_id, slot_number) unique constraints are the DB-level backstop;
    the is_registered check above is the primary guard.
 4. Team name collision within the same world is caught explicitly with a
    friendly message before hitting the DB constraint (better error than a
    raw IntegrityError page).
 5. Wrong password -> generic "wrong password" message, doesn't reveal
    whether the team name/slot exists differently than a right one would.
 6. Teacher password is a single global secret (env var), not per-world --
    confirmed as the simplest option for a single teacher running multiple
    class periods; there is no per-world teacher account.
"""

import random
import string

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from app.auth import current_firm, current_world, is_teacher, log_in_firm, log_in_teacher, log_out
from app.avatars import AVATAR_CHOICES, BADGE_CHOICES
from app.extensions import db
from app.models import Firm, World

bp = Blueprint("auth", __name__)


def generate_game_code(length=6):
    alphabet = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(random.choices(alphabet, k=length))
        if not World.query.filter_by(game_code=code).first():
            return code
    raise RuntimeError("Could not generate a unique game code after 50 attempts")


@bp.route("/")
def index():
    if current_firm():
        return redirect(url_for("firm.dashboard"))
    if is_teacher():
        return redirect(url_for("teacher.dashboard"))
    return redirect(url_for("auth.game_code_entry"))


@bp.route("/login", methods=["GET", "POST"])
def game_code_entry():
    # Same already-logged-in check index() does -- without it, this page
    # (where the browser's address bar actually ends up after a "/" redirect)
    # kept re-prompting for a game code even with a perfectly valid session
    # underneath, if this was the URL revisited/bookmarked instead of "/".
    if current_firm():
        return redirect(url_for("firm.dashboard"))
    # Deliberately does NOT forward a TEACHER session on to the Teacher
    # Dashboard, even though index() does. This is a student-facing page,
    # and firm_login_required() redirects here whenever a student page is
    # hit without a firm session -- so forwarding teachers from here turned
    # "student's session went stale on a student page" into "student is now
    # looking at the Teacher Dashboard with teacher access" (reported bug:
    # clicking Market Dashboard after Round 1 Results logged them in as
    # teacher). A teacher who wants their dashboard still gets forwarded by
    # "/" (index) or can go to /teacher/ directly.
    if request.method == "POST":
        code = request.form.get("game_code", "").strip().upper()
        world = World.query.filter_by(game_code=code).first()
        if not world:
            flash("That game code wasn't found. Double-check with your teacher.")
            return render_template("login_gamecode.html")
        return redirect(url_for("auth.slot_list", world_id=world.id))
    return render_template("login_gamecode.html")


@bp.route("/login/<int:world_id>")
def slot_list(world_id):
    world = World.query.get_or_404(world_id)
    firms = Firm.query.filter_by(world_id=world.id).order_by(Firm.slot_number).all()
    return render_template("login_slots.html", world=world, firms=firms)


@bp.route("/login/<int:world_id>/<int:firm_id>", methods=["GET", "POST"])
def firm_login(world_id, firm_id):
    firm = Firm.query.filter_by(id=firm_id, world_id=world_id).first_or_404()
    if not firm.is_registered:
        return redirect(url_for("auth.register", world_id=world_id, firm_id=firm_id))

    if request.method == "POST":
        password = request.form.get("password", "")
        if firm.check_password(password):
            log_in_firm(firm)
            return redirect(url_for("firm.dashboard"))
        flash("Wrong password for that team.")

    return render_template("login_password.html", firm=firm)


@bp.route("/register/<int:world_id>/<int:firm_id>", methods=["GET", "POST"])
def register(world_id, firm_id):
    firm = Firm.query.filter_by(id=firm_id, world_id=world_id).first_or_404()
    if firm.is_registered:
        flash("That slot is already claimed -- pick another, or log in if it's yours.")
        return redirect(url_for("auth.slot_list", world_id=world_id))

    if request.method == "POST":
        team_name = request.form.get("team_name", "").strip()
        password = request.form.get("password", "")
        avatar = request.form.get("avatar", "")
        badge = request.form.get("badge", "")

        error = None
        if not team_name or not password:
            error = "Team name and password are both required."
        elif avatar not in AVATAR_CHOICES:
            error = "Please pick a factory."
        elif badge not in BADGE_CHOICES:
            error = "Please pick a brand logo."
        elif Firm.query.filter_by(world_id=world_id, team_name=team_name).first():
            error = "That team name is already taken in this class -- pick another."

        if error:
            flash(error)
            return render_template("register.html", firm=firm, avatars=AVATAR_CHOICES, badges=BADGE_CHOICES)

        # Both icons are the team's own choice now (the badge used to be
        # auto-assigned at random) -- bots still get a random one, since
        # nobody is there to pick for them. See models.py edge case 17.
        firm.register(team_name, password, avatar=avatar, badge=badge)
        db.session.commit()
        log_in_firm(firm)
        return redirect(url_for("firm.dashboard"))

    return render_template("register.html", firm=firm, avatars=AVATAR_CHOICES, badges=BADGE_CHOICES)


@bp.route("/logout")
def logout():
    log_out()
    return redirect(url_for("auth.game_code_entry"))


@bp.route("/teacher/login", methods=["GET", "POST"])
def teacher_login():
    if is_teacher():
        return redirect(url_for("teacher.dashboard"))

    # Local dev only (never true on Render -- see IS_LOCAL_DEV in
    # create_app): skip the password gate rather than ask for it every time
    # this page is hit -- BUT never silently when a team is logged in on
    # this browser. log_in_teacher() calls session.clear(), so an auto-login
    # here destroys that student's session and hands the browser teacher
    # access. Every teacher_login_required view redirects here on a miss, so
    # one stray /teacher/* hit (another tab, a bookmark, the back button)
    # was enough to trigger it -- the reported "student clicked Market
    # Dashboard and got logged in as teacher" bug. Switching roles out from
    # under a logged-in team now takes a deliberate ?force=1, never a bare
    # GET. A real password POST below is deliberate by definition, so it is
    # never gated this way.
    if current_app.config["IS_LOCAL_DEV"] and request.method == "GET":
        if current_firm() is None or request.args.get("force") == "1":
            log_in_teacher()
            return redirect(url_for("teacher.dashboard"))
        return render_template("teacher_login.html", logged_in_firm=current_firm())

    if request.method == "POST":
        password = request.form.get("password", "")
        if password and password == current_app.config["TEACHER_PASSWORD"]:
            log_in_teacher()
            return redirect(url_for("teacher.dashboard"))
        flash("Wrong teacher password.")
    return render_template("teacher_login.html")
