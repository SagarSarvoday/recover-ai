# RecoverAI backend

FastAPI foundation for the RecoverAI API. Database tables already live in Supabase; this service connects to them and will host payment, recovery, agent, and webhook routes later.

## Virtual environment and install

From this directory (`backend/`):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

On Windows (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Environment

```bash
cp .env.example .env
```

Set `DATABASE_URL` to your Supabase Postgres URI (SQLAlchemy form with `postgresql+psycopg://` and `sslmode=require`).

## Run the server

With the venv active, from `backend/`:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

OpenAPI docs: http://127.0.0.1:8000/docs

## Initialize the migrated legacy merchant password (development only)

This is an operator-only, one-time command; it does not expose an HTTP endpoint,
does not create a merchant, and does not change Razorpay or recovery ownership.

Temporarily set these values in `.env`:

```env
APP_ENVIRONMENT=development
LEGACY_PASSWORD_BOOTSTRAP_ENABLED=true
```

Then run the command from `backend/` and enter the password interactively (it is
not placed in source code or shell history):

```bash
./.venv/bin/python -m app.scripts.bootstrap_legacy_merchant_password
```

The command only updates merchant `00000000-0000-0000-0000-000000000001` after
checking its email and Razorpay account ID. It refuses to run if a password hash
already exists. Remove `LEGACY_PASSWORD_BOOTSTRAP_ENABLED` afterwards.

## Observe a one-minute WAIT scheduler run (development/test only)

Choose an open or in-progress recovery case belonging to the legacy merchant
that has no `next_action_at`, no active scheduled retry, is below the attempt
limit, and has a non-recoverable failure reason. This command refuses the
already-scheduled case and uses the normal WAIT action dispatcher; it never
creates a Razorpay payment link.

Temporarily set `DEVELOPMENT_SCHEDULER_TEST_ENABLED=true` in `.env`, then run:

```bash
./.venv/bin/python -m app.scripts.schedule_development_wait_test --case-id <case UUID>
```

It creates a normal `retry` job due one minute later. Remove the flag after the
observation is complete.

## Test health

```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`
