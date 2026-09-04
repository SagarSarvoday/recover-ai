-- Migration 011: Add merchant-level AI agent control and audit logging support

ALTER TABLE merchants
    ADD COLUMN IF NOT EXISTS ai_agent_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS ai_agent_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS ai_agent_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_merchants_ai_agent_enabled
    ON merchants (ai_agent_enabled)
    WHERE ai_agent_enabled IS TRUE;

ALTER TABLE audit_logs DROP CONSTRAINT IF EXISTS audit_logs_entity_type_chk;
ALTER TABLE audit_logs
    ADD CONSTRAINT audit_logs_entity_type_chk
    CHECK (entity_type IN ('customer', 'payment', 'recovery_case', 'merchant'));
