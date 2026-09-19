"""Screenshot or record a page of this app in a real browser.

Dev-only. Playwright is deliberately NOT in requirements.txt -- Render must
never install a browser. Set it up once with:

    venv/Scripts/python -m pip install playwright
    venv/Scripts/python -m playwright install chromium

Why this exists: the animations (mall walk-in, the end-game skyline) could
only be checked by hand before, or by re-implementing the CSS maths in PIL
and rendering the result -- which catches geometry errors but cannot catch a
CSS mistake, because it never runs the CSS. This runs the real page in a
real Chromium whose tab is always foregrounded, so animations actually
advance and what comes back is what a classroom projector would show.

    python tools/shoot.py finale --at 12 --out finale.png
    python tools/shoot.py mall   --at 3,6,9
    python tools/shoot.py path /sandbox/ --out sandbox.png
    python tools/shoot.py finale --video finale.webm --for 16

The world is seeded into a throwaway SQLite file, never the real database.
"""

import argparse
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class _Threaded(ThreadingMixIn, WSGIServer):
    """The page pulls a dozen images at once. On the stock single-threaded
    server those queue behind each other and Playwright's networkidle wait
    can time out before the last one lands."""
    daemon_threads = True


def build_app(db_path):
    from app import create_app
    from app.extensions import db

    app = create_app({
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{db_path}",
        "WTF_CSRF_ENABLED": False,
    })
    with app.app_context():
        db.create_all()
    return app


SHOOT_PW = "shoot-pw"


def seed_finished_world(app, firms=4, long_names=False, stop_after=None):
    """A bots-only world played to its last round, so the finale fires.

    `long_names` swaps in 40-character team names -- the worst case the
    billboards have to survive, and the one a class of students produces on
    its own."""
    from werkzeug.security import generate_password_hash

    from app.blueprints.sandbox import BOT_ORDER, _make_bot_firm, run_to_completion
    from app.constants import ROUNDS_PER_WORLD
    from app.extensions import db
    from app.models import World

    with app.app_context():
        world = World(
            name="Shoot", game_code="SHOOT1",
            planned_firm_slots=firms, mode="bots_only", rounds=ROUNDS_PER_WORLD,
        )
        db.session.add(world)
        db.session.flush()
        in_game = []
        for slot in range(1, firms + 1):
            bot = _make_bot_firm(world, slot, BOT_ORDER[(slot - 1) % len(BOT_ORDER)], in_game)
            in_game.append(bot)
            db.session.add(bot)
        # Slot 1 gets a password we know, so the firm-side pages (which are
        # behind a real team login, not the local-dev teacher shortcut) can
        # be opened too.
        in_game[0].password_hash = generate_password_hash(SHOOT_PW)
        if long_names:
            for i, bot in enumerate(in_game, start=1):
                bot.team_name = f"The Extremely Loud Headphone Co #{i}"
        db.session.commit()
        if stop_after is None:
            run_to_completion(world)
        else:
            # Mid-game: the decision form only exists while a round is open,
            # and round 1 has no history behind it -- both are states the
            # finished-world seed can never show.
            from app.blueprints.teacher import _open_next_round, _process_current_round
            for _ in range(stop_after):
                if world.status == "complete":
                    break
                _process_current_round(world)
                if world.status != "complete":
                    _open_next_round(world)
        return world.id, in_game[0].id


def serve(app, port):
    server = make_server("127.0.0.1", port, app,
                         server_class=_Threaded, handler_class=_Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", choices=["finale", "mall", "firm", "path"],
                    help="finale/mall/firm seed a played-out game; "
                         "path shoots any URL path")
    ap.add_argument("target", nargs="?", default="/", help="URL path when what=path")
    ap.add_argument("--at", default="12",
                    help="seconds after load to shoot; comma-separate for several")
    ap.add_argument("--out", default="shot.png", help="output png (numbered if --at has several)")
    ap.add_argument("--video", help="also record the whole visit to this .webm")
    ap.add_argument("--for", dest="duration", type=float, default=None,
                    help="seconds to keep recording (default: last --at + 2)")
    ap.add_argument("--firms", type=int, default=4)
    ap.add_argument("--stop-after", type=int, default=None,
                    help="play only N rounds, leaving the game mid-flight "
                         "(0 = round 1, nothing played yet)")
    ap.add_argument("--long-names", action="store_true",
                    help="give every firm a 40-character name (worst case)")
    ap.add_argument("--width", type=int, default=1536)
    ap.add_argument("--height", type=int, default=864)
    ap.add_argument("--port", type=int, default=5057)
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--then", default=None,
                    help="after signing in, navigate here instead "
                         "(e.g. --then /market/ for the standings)")
    ap.add_argument("--serve", action="store_true",
                    help="no browser at all: seed the game, print a URL, and "
                         "keep serving it so you can open it yourself")
    ap.add_argument("--watch", action="store_true",
                    help="just watch it: opens a real window, takes no "
                         "screenshot, and stays open until you press Enter "
                         "so the page's own Replay button is usable")
    args = ap.parse_args()

    if args.watch:
        args.headed = True
    shots = [] if args.watch else [float(x) for x in args.at.split(",") if x.strip()]
    duration = args.duration if args.duration is not None else (max(shots or [0]) + 2)

    tmp = tempfile.mkdtemp(prefix="shoot-")
    db_path = (Path(tmp) / "shoot.db").as_posix()
    app = build_app(db_path)

    if args.what == "path":
        path = args.target
    else:
        world_id, firm_id = seed_finished_world(
            app, args.firms, args.long_names, args.stop_after)
        path = (f"/login/{world_id}/{firm_id}" if args.what == "firm"
                else f"/teacher/worlds/{world_id}/present")

    server = serve(app, args.port)
    base = f"http://127.0.0.1:{args.port}"

    if args.serve:
        print()
        print("  1. sign in (one click, no password locally):")
        print(f"     {base}/teacher/login")
        print("  2. then the finale:")
        print(f"     {base}{path}")
        print()
        print("  Replay is on the page. Ctrl+C here when you are done.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)
        return

    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            ctx_args = {"viewport": {"width": args.width, "height": args.height}}
            if args.video:
                ctx_args["record_video_dir"] = tmp
                ctx_args["record_video_size"] = {"width": args.width, "height": args.height}
            ctx = browser.new_context(**ctx_args)
            page = ctx.new_page()

            # Local dev auto-logs the teacher in on a bare GET, so this is
            # the whole authentication step.
            if args.what == "firm":
                page.goto(base + path, wait_until="networkidle")
                page.fill("input[name=password]", SHOOT_PW)
                page.click("button[type=submit], input[type=submit]")
                page.wait_for_load_state("networkidle")
                if args.then:
                    page.goto(base + args.then, wait_until="networkidle")
            else:
                page.goto(f"{base}/teacher/login", wait_until="networkidle")
                page.goto(base + path, wait_until="networkidle")

            # The first-visit tour dims the whole page behind a backdrop,
            # which makes every screenshot look like a contrast bug.
            try:
                skip = page.get_by_text("Skip", exact=True)
                if skip.count():
                    skip.first.click(timeout=1500)
                    page.wait_for_timeout(300)
            except Exception:
                pass

            # Proof the tab is foregrounded and the clock is moving -- a
            # backgrounded Chrome freezes document.timeline at 0 and every
            # animation with it, which once nearly got a working finale
            # reported as broken.
            t0 = page.evaluate("document.timeline.currentTime")
            time.sleep(0.4)
            t1 = page.evaluate("document.timeline.currentTime")
            state = page.evaluate("document.visibilityState")
            print(f"visibility={state} timeline {t0:.0f} -> {t1:.0f}ms "
                  f"({'advancing' if t1 > t0 else 'FROZEN'})")

            started = time.monotonic()
            for i, at in enumerate(shots):
                wait = at - (time.monotonic() - started)
                if wait > 0:
                    page.wait_for_timeout(wait * 1000)
                out = Path(args.out)
                if len(shots) > 1:
                    out = out.with_name(f"{out.stem}-{at:g}s{out.suffix}")
                page.screenshot(path=str(out))
                print(f"t={at:g}s -> {out}")

            if args.watch:
                print(f"Watching {base}{path} -- Replay is on the page. "
                      "Press Enter here to close.")
                input()

            if args.video:
                left = duration - (time.monotonic() - started)
                if left > 0:
                    page.wait_for_timeout(left * 1000)
                src = page.video.path()
                ctx.close()
                shutil.move(src, args.video)
                print(f"video -> {args.video}")
            else:
                ctx.close()
            browser.close()
    finally:
        server.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    os.environ.pop("DATABASE_URL", None)   # keep IS_LOCAL_DEV true
    main()
