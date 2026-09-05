# RecoverAI Production Deployment Guide

This guide describes the operational requirements and procedures for deploying RecoverAI to a production environment.

---

## 1. System Requirements & Topology

A complete production deployment of RecoverAI consists of:
1. **Managed PostgreSQL Database (e.g., Supabase / AWS RDS)** with SSL enabled.
2. **FastAPI Backend Service**: Python 3.11+ application running via ASGI server (`uvicorn` or `gunicorn` with `uvicorn.workers.UvicornWorker`).
3. **Next.js 15 Frontend Service**: Node.js 18+ server or static export hosted on any Node or Edge runtime platform.
4. **Razorpay Account & Webhooks**: Live or Test Mode API credentials and a public webhook endpoint URL.
5. **SMTP Mail Service**: SMTP provider (Amazon SES, SendGrid, Mailgun, or Google Workspace) for customer notifications and password resets.
6. **AI Provider Endpoint**: Local Ollama instance, or accessible LLM provider endpoint.

---

## 2. PostgreSQL / Supabase Database Setup

### Step 1: Database Provisioning
- Provision a PostgreSQL 15+ database.
- Obtain both the **Direct Connection URL** (port 5432) for running migrations and the **Transaction Pooler URL** (port 6543, e.g., Supabase transaction pooler) for API instances:
  ```
  DATABASE_URL=postgresql+psycopg://postgres.[PROJECT-REF]:[YOUR-PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?sslmode=require
  ```

### Step 2: Applying Migrations
Apply the baseline schema and all 16 incremental migrations in numerical order:
```bash
# Using psql with your direct connection string:
psql "$DIRECT_DATABASE_URL" -f database/schema.sql

# Alternatively, apply migrations sequentially:
for migration in database/migrations/*.sql; do
  echo "Applying $migration..."
  psql "$DIRECT_DATABASE_URL" -f "$migration"
done
```

---

## 3. Backend Deployment (FastAPI)

### Environment Configuration (`.env`)
Set the following production environment variables on the backend hosting service:

```bash
# Database
DATABASE_URL=postgresql+psycopg://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres?sslmode=require
APP_ENVIRONMENT=production

# Security & Auth
JWT_SECRET_KEY=<generate-strong-random-32-byte-hex>
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=60
PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=20

# Razorpay Integration
RAZORPAY_KEY_ID=rzp_live_... (or rzp_test_...)
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...

# Email & Notifications
EMAIL_ENABLED=true
SMTP_HOST=email-smtp.us-east-1.amazonaws.com
SMTP_PORT=587
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_USE_TLS=true
EMAIL_FROM="RecoverAI <notifications@yourstore.com>"

# Networking & CORS
CORS_ORIGINS=https://app.yourdomain.com
FRONTEND_BASE_URL=https://app.yourdomain.com

# AI Engine
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://internal-ollama-host:11434
OLLAMA_MODEL=llama3.2:3b

# Workers
RECOVERY_MAX_ATTEMPTS=3
RECOVERY_SCHEDULER_ENABLED=true
RECOVERY_SCHEDULER_POLL_SECONDS=15
RECOVERY_AGENT_WORKER_ENABLED=true
RECOVERY_AGENT_POLL_SECONDS=10
```

### Process Management
Run the application using Gunicorn with Uvicorn workers:
```bash
cd backend
pip install -r requirements.txt
gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 0.0.0.0:8000
```

### Health Check Endpoint
- Path: `GET /health`
- Expected Status: `200 OK`
- Expected Body: `{"status": "ok"}`
- Configure your load balancer or container orchestrator to poll this endpoint for liveness and readiness probes.

---

## 4. Frontend Deployment (Next.js 15)

### Environment Configuration
Provide the following environment variables during the build and runtime stages:

```bash
NEXT_PUBLIC_API_BASE_URL=https://api.yourdomain.com
NEXT_PUBLIC_RAZORPAY_KEY_ID=rzp_live_... (or rzp_test_...)
```

### Build & Run
```bash
cd frontend
npm ci
npm run build
npm start
```

---

## 5. Razorpay Webhook Configuration

1. Log in to the [Razorpay Dashboard](https://dashboard.razorpay.com).
2. Navigate to **Settings** → **Webhooks** → **Add New Webhook**.
3. **Webhook URL**: Enter your public backend endpoint:
   ```
   https://api.yourdomain.com/api/v1/webhooks/razorpay
   ```
4. **Secret**: Enter a high-entropy secret string and copy it to `RAZORPAY_WEBHOOK_SECRET` in your backend `.env`.
5. **Active Events**: Check the following events:
   - `payment.failed`
   - `payment_link.paid`
6. Click **Save**.

---

## 6. Production Security Checklist

- [ ] **SSL/TLS**: All incoming traffic must terminate on HTTPS; PostgreSQL requires `sslmode=require`.
- [ ] **Secret Isolation**: Ensure `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `JWT_SECRET_KEY`, and `SMTP_PASSWORD` are never exposed in client bundles or public repositories.
- [ ] **CORS Restrictions**: Set `CORS_ORIGINS` to the exact public frontend domain (do not use `*` in production).
- [ ] **Database Connection Pooling**: Utilize a connection pooler (e.g. Supabase port 6543 / PgBouncer) to prevent connection exhaustion from concurrent API requests and background worker threads.
- [ ] **Worker Redundancy & Leases**: Background worker processes utilize SQL atomic updates and in-memory leases to guarantee that multiple horizontal backend instances do not execute the same recovery case simultaneously.
- [ ] **Anti-Enumeration Verification**: Confirm that `POST /api/v1/auth/forgot-password` returns generic HTTP 200 responses regardless of email existence.
