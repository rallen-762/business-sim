"""
Session-based auth helpers.

Edge cases considered:
 1. A browser tab is either a logged-in Firm or a logged-in Teacher, never
    both at once -- logging into one role clears any prior session state
    (log_in_firm/log_in_teacher both call session.clear() first). Matches
    reality: a person using this app is either a student on one team or the
    teacher, never simultaneously both in the same browser tab.
 2. A stale/invalid firm_id or world_id in the session (e.g. the teacher
    deleted a World mid-session) must not crash the app -- current_firm()/
    current_world() return None via .get(), and every protected view treats
    None as "not logged in" rather than assuming the row still exists.
 3. No token refresh, no expiry logic beyond a single long-lived cookie --
    deliberately simple per the project's standing requirement (Game Code +
    Team Password, Chromebook-friendly). Flask's signed session cookie is
    the only session mechanism.
 4. session.permanent is explicitly set True on login so the cookie carries
    a real Expires/Max-Age (app.config["PERMANENT_SESSION_LIFETIME"], see
    app/__init__.py) -- without this, Flask issues a session-only cookie
    that some browsers (Chromebooks especially) drop when a backgrounded
    tab gets discarded for memory, which read to users as "got logged out
    just from switching windows."
"""

import functools

from flask import flash, redirect, session, url_for

from app.extensions import db
from app.models import Firm, World


def log_in_firm(firm):
    """Signs in as a TEAM, leaving any teacher session in this browser
    intact (and vice versa -- see log_in_teacher).

    These both used to session.clear() first, on the reasoning that a person
    is either a student or the teacher, never both. In practice the teacher
    is both: they check a student view, and their dashboard session dies --
    reported as being logged out constantly in both roles. Only the role
    being signed into is replaced now.

    The trade-off, confirmed with the user: on a SHARED browser a teacher who
    signs in as a team leaves their teacher access behind for the next
    person. That's why the student dashboard renders an unmissable "you're
    also signed in as Teacher" banner with a one-click sign-out -- the
    leftover access is visible and trivially dropped rather than silent."""
    session.permanent = True
    for key in FIRM_SESSION_KEYS:
        session.pop(key, None)
    session["firm_id"] = firm.id
    session["world_id"] = firm.world_id


def log_in_teacher():
    """Signs in as the TEACHER, leaving any team session intact. See
    log_in_firm() for why these no longer clear the whole cookie."""
    session.permanent = True
    for key in TEACHER_SESSION_KEYS:
        session.pop(key, None)
    session["is_teacher"] = True


FIRM_SESSION_KEYS = ("firm_id", "world_id")
TEACHER_SESSION_KEYS = ("is_teacher", "reset_passwords")


def log_out_firm():
    """Signs out of the TEAM role only, leaving any teacher session alone.

    Logging out used to clear the whole cookie, so a teacher who had looked
    at a student view (or shared a browser with one) got signed out of the
    Teacher Dashboard too, and vice versa -- reported as constantly being
    logged out in both roles. Dropping only your own role's keys can't grant
    anyone access they didn't already have in this browser."""
    for key in FIRM_SESSION_KEYS:
        session.pop(key, None)


def log_out_teacher():
    """Signs out of the TEACHER role only -- see log_out_firm(). Also drops
    the session-scoped plaintext reset passwords, which are teacher-only and
    must never outlive the teacher session that created them."""
    for key in TEACHER_SESSION_KEYS:
        session.pop(key, None)


def log_out():
    """Signs out of everything. Still used where a clean slate is the point."""
    session.clear()


def current_firm():
    firm_id = session.get("firm_id")
    if firm_id is None:
        return None
    firm = db.session.get(Firm, firm_id)
    # A session pointing at an UNCLAIMED slot isn't a logged-in team. This
    # happens with a stale cookie whose firm_id now refers to a since-
    # unclaimed (or entirely different) row -- it used to sail through every
    # is-someone-logged-in check and render as "logged in as None", because
    # an unclaimed slot's team_name is NULL. Every path that calls
    # log_in_firm() has already registered or password-checked the firm, so
    # requiring is_registered here never rejects a legitimate session.
    if firm is None or not firm.is_registered:
        return None
    return firm


def current_world():
    world_id = session.get("world_id")
    if world_id is None:
        return None
    return db.session.get(World, world_id)


def is_teacher():
    return bool(session.get("is_teacher"))


def firm_login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if current_firm() is None:
            # Spell out the teacher case rather than saying "please log in"
            # to someone who IS logged in -- this is the state a student hits
            # when a teacher login elsewhere in the same browser replaced
            # their session (one session cookie per browser, not per tab).
            if is_teacher():
                flash("This browser is logged in as the teacher, not a team. Log out to join as a team.")
            else:
                flash("Please log in first.")
            return redirect(url_for("auth.game_code_entry"))
        return view(*args, **kwargs)
    return wrapped


def teacher_login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_teacher():
            flash("Teacher login required.")
            return redirect(url_for("auth.teacher_login"))
        return view(*args, **kwargs)
    return wrapped
