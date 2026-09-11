# Headphone Company Simulator

A classroom economics simulation: teams of students run competing headphone
companies over 10 rounds per class period ("World"), with a teacher
controlling round pacing from a Teacher Dashboard.

Flask + SQLAlchemy + PostgreSQL, deployed on Render. No LLM/AI API calls
anywhere -- this is a deterministic, formula-driven simulation.

## Local development

```powershell
python -m venv venv
.\venv\Scripts\pip install -r requirements.txt
.\venv\Scripts\python -m pytest tests/ -v
```

Runs on SQLite by default (`dev.db`) when `DATABASE_URL` isn't set -- no
local Postgres needed for development.

To run the dev server locally:

```powershell
$env:FLASK_APP = "app:create_app"
.\venv\Scripts\flask run
```

## Deploying to Render

### 1. Create the Postgres database

Render dashboard -> **New** -> **PostgreSQL**. Once created, copy its
**Internal Database URL** (starts with `postgres://`) -- you'll paste this
into the web service's environment variables below.

### 2. Create the web service

Render dashboard -> **New** -> **Web Service** -> connect the
`business-sim` GitHub repo.

- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: leave blank (the `Procfile` supplies
  `gunicorn "app:create_app()"`)
- **Pre-Deploy Command**: `flask init-db`
  (creates any missing tables before the new version goes live -- safe to
  run on every deploy, including the very first one)

### 3. Environment variables (Render dashboard -> Environment)

| Key | Value |
|---|---|
| `DATABASE_URL` | the Postgres Internal Database URL from step 1 |
| `SECRET_KEY` | any long random string (used to sign session cookies) |
| `TEACHER_PASSWORD` | the real password you'll use to log into the Teacher Dashboard |
| `FLASK_APP` | `app:create_app` (needed for the Pre-Deploy Command above) |

`DATABASE_URL`'s `postgres://` prefix is rewritten to `postgresql://`
automatically in code (`app/__init__.py`) -- Render's own URL format and
SQLAlchemy's psycopg2 dialect disagree on this, so don't edit the URL
yourself.

### 4. Deploy

Push to `main` -- Render auto-deploys on every push once connected.

## Known limitations / not yet built

- No authentication beyond Game Code + Team Password / a single teacher
  password (no per-teacher accounts) -- acceptable for a single-teacher
  classroom tool, not for multi-teacher use without further work.
- No schema migration tool (e.g. Alembic) -- `flask init-db` only adds
  missing tables. A future schema *change* (not just a new table) will
  need a manual step, not just re-running this command.
- No avatar/graphics customization beyond the bundled Kenney "City Kit
  Industrial" image set.
