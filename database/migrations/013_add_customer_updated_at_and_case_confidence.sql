-- Migration 013: Add updated_at to customers and ai_confidence to recovery_cases

ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

ALTER TABLE recovery_cases
    ADD COLUMN IF NOT EXISTS ai_confidence NUMERIC(3, 2);
