-- RecoverAI — PostgreSQL schema (hackathon MVP)
-- Tables: merchants, customers, payments, recovery_cases, audit_logs, razorpay_webhook_events

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- merchants
-- Merchant ownership is mandatory for all customer, payment, and recovery data.
-- password_hash stays nullable until merchant authentication/onboarding is added.
-- ---------------------------------------------------------------------------
CREATE TABLE merchants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    email               TEXT NOT NULL UNIQUE,
    password_hash       TEXT,
    razorpay_account_id TEXT UNIQUE,
    ai_agent_enabled    BOOLEAN NOT NULL DEFAULT FALSE,
    ai_agent_started_at TIMESTAMPTZ,
    ai_agent_updated_at TIMESTAMPTZ,
    business_name       TEXT,
    support_email       TEXT,
    support_phone       TEXT,
    max_recovery_attempts INTEGER NOT NULL DEFAULT 3,
    default_payment_link_expiry_hours INTEGER NOT NULL DEFAULT 48,
    default_wait_minutes INTEGER NOT NULL DEFAULT 60,
    auto_notify_customer BOOLEAN NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT merchants_max_recovery_attempts_chk
        CHECK (max_recovery_attempts >= 1 AND max_recovery_attempts <= 5),
    CONSTRAINT merchants_default_payment_link_expiry_hours_chk
        CHECK (default_payment_link_expiry_hours >= 1 AND default_payment_link_expiry_hours <= 168),
    CONSTRAINT merchants_default_wait_minutes_chk
        CHECK (default_wait_minutes >= 15 AND default_wait_minutes <= 1440)
);

-- Compatibility merchant for the existing single-merchant development workflow.
INSERT INTO merchants (id, name, email, razorpay_account_id)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'RecoverAI Legacy Development Merchant',
    'legacy@recoverai.local',
    'acc_TTZWM0fniZWAbi'
)
ON CONFLICT (id) DO NOTHING;

-- ---------------------------------------------------------------------------
-- customers
-- ---------------------------------------------------------------------------
CREATE TABLE customers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id   UUID NOT NULL REFERENCES merchants (id) ON DELETE RESTRICT,
    name          TEXT,
    email         TEXT,
    phone         TEXT,
    razorpay_customer_id TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT customers_merchant_email_key UNIQUE (merchant_id, email),
    CONSTRAINT customers_merchant_razorpay_customer_id_key
        UNIQUE (merchant_id, razorpay_customer_id)
);

CREATE INDEX idx_customers_merchant_id ON customers (merchant_id);

-- ---------------------------------------------------------------------------
-- transactions
-- Merchant business payment intents. A transaction can have multiple Razorpay
-- payment attempts; existing historical payment rows may remain unlinked.
-- ---------------------------------------------------------------------------
CREATE TABLE transactions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id             UUID NOT NULL REFERENCES merchants (id) ON DELETE RESTRICT,
    customer_id             UUID NOT NULL REFERENCES customers (id) ON DELETE RESTRICT,
    merchant_transaction_id TEXT NOT NULL,
    razorpay_order_id       TEXT UNIQUE,
    amount                  NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency                TEXT NOT NULL DEFAULT 'INR',
    status                  TEXT NOT NULL DEFAULT 'created'
                            CHECK (status IN ('created', 'pending', 'paid', 'cancelled')),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT transactions_merchant_external_key
        UNIQUE (merchant_id, merchant_transaction_id)
);

CREATE INDEX idx_transactions_merchant_id ON transactions (merchant_id);
CREATE INDEX idx_transactions_customer_id ON transactions (customer_id);

-- ---------------------------------------------------------------------------
-- payments
-- A customer can have many payments (successful and failed).
-- Previous successful payments are rows with status = 'succeeded'.
-- Failed payments carry a failure_reason used by recovery logic later.
-- ---------------------------------------------------------------------------
CREATE TABLE payments (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id     UUID NOT NULL REFERENCES merchants (id) ON DELETE RESTRICT,
    transaction_id  UUID REFERENCES transactions (id) ON DELETE SET NULL,
    customer_id     UUID REFERENCES customers (id) ON DELETE SET NULL,
    amount          NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency        TEXT NOT NULL DEFAULT 'INR',
    status          TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    failure_reason  TEXT,
    payment_method  TEXT,
    razorpay_payment_id TEXT UNIQUE,
    razorpay_success_payment_id TEXT UNIQUE,
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT payments_failure_reason_chk CHECK (
        (status = 'failed' AND failure_reason IS NOT NULL)
        OR (status <> 'failed' AND failure_reason IS NULL)
    )
);

CREATE INDEX idx_payments_customer_id ON payments (customer_id);
CREATE INDEX idx_payments_merchant_id ON payments (merchant_id);
CREATE INDEX idx_payments_transaction_id ON payments (transaction_id);
CREATE INDEX idx_payments_status ON payments (status);
CREATE INDEX idx_payments_razorpay_payment_id ON payments (razorpay_payment_id);
CREATE INDEX idx_payments_razorpay_success_payment_id ON payments (razorpay_success_payment_id);

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
    merchant_id       UUID NOT NULL REFERENCES merchants (id) ON DELETE RESTRICT,
    customer_id       UUID REFERENCES customers (id) ON DELETE SET NULL,
    payment_id        UUID NOT NULL UNIQUE REFERENCES payments (id) ON DELETE CASCADE,
    razorpay_payment_link_id TEXT UNIQUE,
    payment_link_expires_at TIMESTAMPTZ,
    status            TEXT NOT NULL DEFAULT 'open'
                      CHECK (status IN ('open', 'in_progress', 'waiting', 'payment_link_active', 'recovered', 'closed')),
    amount_at_risk    NUMERIC(12, 2) NOT NULL CHECK (amount_at_risk >= 0),
    amount_recovered  NUMERIC(12, 2) NOT NULL DEFAULT 0 CHECK (amount_recovered >= 0),
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    ai_decision       TEXT CHECK (
                          ai_decision IS NULL
                          OR ai_decision IN ('retry', 'wait', 'contact', 'skip', 'close')
                      ),
    ai_decision_note  TEXT,
    ai_confidence     NUMERIC(3, 2),
    ai_wait_minutes   INTEGER CHECK (ai_wait_minutes IS NULL OR ai_wait_minutes BETWEEN 1 AND 10080),
    next_action_at    TIMESTAMPTZ,
    scheduled_action  TEXT,
    last_attempt_at   TIMESTAMPTZ,
    payment_link_created_at TIMESTAMPTZ,
    payment_link_sent_at TIMESTAMPTZ,
    payment_link_paid_at TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT recovery_cases_recovered_lte_risk_chk CHECK (
        amount_recovered <= amount_at_risk
    )
);

CREATE INDEX idx_recovery_cases_customer_id ON recovery_cases (customer_id);
CREATE INDEX idx_recovery_cases_merchant_id ON recovery_cases (merchant_id);
CREATE INDEX idx_recovery_cases_status ON recovery_cases (status);
CREATE INDEX idx_recovery_cases_razorpay_payment_link_id
    ON recovery_cases (razorpay_payment_link_id);
CREATE INDEX idx_recovery_cases_payment_link_expires_at
    ON recovery_cases (payment_link_expires_at);

-- ---------------------------------------------------------------------------
-- scheduled_recovery_actions
-- Durable delayed actions, claimed with row locks by the in-process worker.
-- ---------------------------------------------------------------------------
CREATE TABLE scheduled_recovery_actions (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recovery_case_id  UUID NOT NULL REFERENCES recovery_cases(id) ON DELETE CASCADE,
    merchant_id       UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    action            TEXT NOT NULL CHECK (action IN ('retry', 'contact')),
    scheduled_at      TIMESTAMPTZ NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'running', 'completed', 'skipped', 'failed')),
    attempt_count     INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_message     TEXT,
    lease_expires_at  TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at       TIMESTAMPTZ,
    CONSTRAINT scheduled_recovery_actions_case_action_time_key
        UNIQUE (recovery_case_id, action, scheduled_at)
);

CREATE INDEX idx_scheduled_recovery_actions_due
    ON scheduled_recovery_actions (status, scheduled_at);

CREATE UNIQUE INDEX scheduled_recovery_actions_one_active_case_action_key
    ON scheduled_recovery_actions (recovery_case_id, action)
    WHERE status IN ('pending', 'running');

-- ---------------------------------------------------------------------------
-- audit_logs
-- Append-only history of payment events, AI decisions, and recovery attempts.
-- ---------------------------------------------------------------------------
CREATE TABLE audit_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('customer', 'payment', 'recovery_case', 'merchant')),
    entity_id     UUID NOT NULL,
    action        TEXT NOT NULL,
    actor         TEXT NOT NULL DEFAULT 'system',
    details       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_audit_logs_entity ON audit_logs (entity_type, entity_id);
CREATE INDEX idx_audit_logs_created_at ON audit_logs (created_at);

-- ---------------------------------------------------------------------------
-- razorpay_webhook_events
-- Idempotency and minimal audit trail for external Razorpay webhook deliveries.
-- Raw payloads are deliberately not persisted here.
-- ---------------------------------------------------------------------------
CREATE TABLE razorpay_webhook_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_event_id   TEXT NOT NULL UNIQUE,
    event_type          TEXT NOT NULL,
    external_entity_id  TEXT,
    processing_status   TEXT NOT NULL CHECK (
                            processing_status IN ('processed', 'ignored')
                         ),
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_razorpay_webhook_events_event_type
    ON razorpay_webhook_events (event_type);

-- ---------------------------------------------------------------------------
-- merchant_password_reset_tokens
-- Stores cryptographically hashed, single-use, time-limited reset tokens.
-- ---------------------------------------------------------------------------
CREATE TABLE merchant_password_reset_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id   UUID NOT NULL REFERENCES merchants (id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ,
    requested_ip  TEXT,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_merchant_password_reset_tokens_merchant_id
    ON merchant_password_reset_tokens (merchant_id);

CREATE INDEX idx_merchant_password_reset_tokens_token_hash
    ON merchant_password_reset_tokens (token_hash);

CREATE INDEX idx_merchant_password_reset_tokens_expires_at
    ON merchant_password_reset_tokens (expires_at);

