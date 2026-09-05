-- Migration 014: Recovery state machine and payment link lifecycle timestamps

ALTER TABLE recovery_cases
    DROP CONSTRAINT IF EXISTS recovery_cases_status_chk;

ALTER TABLE recovery_cases
    ADD CONSTRAINT recovery_cases_status_chk
    CHECK (status IN ('open', 'in_progress', 'waiting', 'payment_link_active', 'recovered', 'closed'));

ALTER TABLE recovery_cases
    ADD COLUMN IF NOT EXISTS payment_link_created_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payment_link_sent_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS payment_link_paid_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_recovery_cases_merchant_status
    ON recovery_cases (merchant_id, status);

CREATE INDEX IF NOT EXISTS idx_recovery_cases_next_action_at
    ON recovery_cases (next_action_at)
    WHERE next_action_at IS NOT NULL;
