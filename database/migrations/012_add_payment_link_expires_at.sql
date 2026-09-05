-- Migration 012: Add payment_link_expires_at to recovery_cases
-- Tracks the expiry timestamp for Razorpay recovery payment links to enable deterministic
-- reuse of active links and reassessment/recreation when links expire.

ALTER TABLE recovery_cases
    ADD COLUMN IF NOT EXISTS payment_link_expires_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_recovery_cases_payment_link_expires_at
    ON recovery_cases (payment_link_expires_at);
