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
 3. No token refresh, no expiry logic -- deliberately simple per the
    project's standing requirement (Game Code + Team Password, Chromebook-
    friendly). Flask's signed session cookie is the only session mechanism.
"""

import functools

from flask import flash, redirect, session, url_for

from app.extensions import db
from app.models import Firm, World


def log_in_firm(firm):
    session.clear()
    session["firm_id"] = firm.id
    session["world_id"] = firm.world_id


def log_in_teacher():
    session.clear()
    session["is_teacher"] = True


def log_out():
    session.clear()


def current_firm():
    firm_id = session.get("firm_id")
    if firm_id is None:
        return None
    return db.session.get(Firm, firm_id)


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
