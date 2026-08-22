-- RecoverAI — PostgreSQL schema (hackathon MVP)
-- Tables: customers, payments, recovery_cases, audit_logs

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- customers
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    phone         TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------------
-- payments
-- A customer can have many payments (successful and failed).
-- Previous successful payments are rows with status = 'succeeded'.
-- Failed payments carry a failure_reason used by recovery logic later.
-- ---------------------------------------------------------------------------
CREATE TABLE payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id     UUID NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency        TEXT NOT NULL DEFAULT 'INR',
    status          TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    failure_reason  TEXT,
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT payments_failure_reason_chk CHECK (
        (status = 'failed' AND failure_reason IS NOT NULL)
        OR (status <> 'failed' AND failure_reason IS NULL)
    )
);

CREATE INDEX idx_payments_customer_id ON payments (customer_id);
CREATE INDEX idx_payments_status ON payments (status);

-- ---------------------------------------------------------------------------
-- recovery_cases
-- One case per failed payment that RecoverAI tries to collect.
-- amount_at_risk  = revenue at risk (typically the failed payment amount)
-- amount_recovered = money recovered so far
-- attempt_count   = how many recovery attempts have been made
-- ai_decision     = latest agent decision (retry, wait, contact, skip, close)
-- ---------------------------------------------------------------------------
CREATE TABLE recovery_cases (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id       UUID NOT NULL REFERENCES customers (id) ON DELETE CASCADE,
    payment_id        UUID NOT NULL UNIQUE REFERENCES payments (id) ON DELETE CASCADE,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'in_progress', 'recovered', 'closed')),
    amount_at_risk    NUMERIC(12, 2) NOT NULL CHECK (amount_at_risk >= 0),
    amount_recovered  NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (amount_recovered >= 0),
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ai_decision       TEXT CHECK (
                          ai_decision IS NULL
                          OR ai_decision IN ('retry', 'wait', 'contact', 'skip', 'close')
                      ),
    ai_decision_note  TEXT,
    last_attempt_at   TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT recovery_cases_recovered_lte_risk_chk CHECK (
        amount_recovered <= amount_at_risk
    )
);

CREATE INDEX idx_recovery_cases_customer_id ON recovery_cases (customer_id);
CREATE INDEX idx_recovery_cases_status ON recovery_cases (status);

-- ---------------------------------------------------------------------------
-- audit_logs
-- Append-only history of payment events, AI decisions, and recovery attempts.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('customer', 'payment', 'recovery_case')),
    entity_id     UUID NOT NULL,
    action        TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'system',
    details       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs (created_at);
