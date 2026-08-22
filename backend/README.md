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

## Test health

```bash
curl http://127.0.0.1:8000/health
```

Expected: `{"status":"ok"}`
