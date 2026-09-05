# Contributing to RecoverAI

Thank you for your interest in contributing to RecoverAI! This guide outlines how to set up your local development environment, run test suites, and follow our development standards.

---

## 1. Code of Conduct & Invariants

When working on RecoverAI, adhere to our core system invariants:
- **Strict Recovery Invariant**: Neither AI reasoning, scheduled workers, payment-link generation, email dispatch, nor merchant dashboard actions can mark a recovery case as `recovered`. A case transitions to `recovered` **exclusively** when a verified Razorpay `payment_link.paid` webhook settles payment.
- **Tenant Isolation**: All database models, APIs, background jobs, and metrics must be strictly scoped to the authenticated merchant (`merchant_id`). Cross-tenant access must return `404 Not Found`.
- **Zero Secret Exposure**: Never store plaintext passwords, reset tokens, or API secrets. Never return secrets in API responses or audit logs.

---

## 2. Local Development Setup

### Prerequisites
- Python 3.11+ (Python 3.14 compatible)
- Node.js 18+ & npm
- PostgreSQL 15+ (or Supabase project)
- [Optional] [Ollama](https://ollama.ai) with `llama3.2:3b` for local AI decision engine execution

### Backend Setup
```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your PostgreSQL DATABASE_URL and test credentials
uvicorn app.main:app --reload --port 8000
```

### Frontend Setup
```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
```
The frontend will be available at `http://localhost:3000`.

---

## 3. Running Tests & Quality Checks

### Backend Tests
All PRs must pass the complete backend test suite:
```bash
cd backend
python3 -m pytest -v
```

### Frontend Build & Linting
Ensure type safety and production build integrity:
```bash
cd frontend
npm run build
npm run lint
```

---

## 4. Coding & Architecture Expectations

1. **Explicit State Machine**: All recovery case status changes must go through `transition_case_status` in `app/services/recovery_state_machine.py`. Never perform raw ad-hoc updates to `case.status`.
2. **Deterministic Fallbacks**: AI decision logic must have robust rule-based guardrails (`apply_guardrails`) ensuring graceful fallbacks if LLM output is malformed, unavailable, or exceeds configured attempt limits.
3. **Database Migrations**: Add incremental SQL migrations under `database/migrations/` sequentially numbered (e.g., `017_...sql`) and keep `database/schema.sql` synchronized.
4. **Clean Git Hygiene**:
   - Create focused branches (`feature/your-feature`, `fix/issue-description`).
   - Do not commit `.env`, test logs, or temporary scratch files.
   - Write clear, imperative commit messages.

---

## 5. Pull Request Process

1. Fork the repository and create your branch from `main`.
2. Implement your changes following existing architectural patterns.
3. Add unit and integration tests covering your new behavior.
4. Verify that `pytest` and `npm run build` pass completely with zero regressions.
5. Open a Pull Request with a clear description of the problem, implementation, and verification steps.
