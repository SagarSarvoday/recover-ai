-- Apply after 007_add_merchant_email_uniqueness.sql.
ALTER TABLE recovery_cases ADD COLUMN IF NOT EXISTS ai_wait_minutes INTEGER;
ALTER TABLE recovery_cases ADD COLUMN IF NOT EXISTS next_action_at TIMESTAMPTZ;
ALTER TABLE recovery_cases ADD COLUMN IF NOT EXISTS scheduled_action TEXT;

CREATE TABLE IF NOT EXISTS scheduled_recovery_actions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    recovery_case_id UUID NOT NULL REFERENCES recovery_cases(id) ON DELETE CASCADE,
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('retry', 'contact')),
    scheduled_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'skipped', 'failed')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    error_message TEXT,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    executed_at TIMESTAMPTZ,
    CONSTRAINT scheduled_recovery_actions_case_action_time_key UNIQUE (recovery_case_id, action, scheduled_at)
);

CREATE INDEX IF NOT EXISTS idx_scheduled_recovery_actions_due
    ON scheduled_recovery_actions (status, scheduled_at);

CREATE UNIQUE INDEX IF NOT EXISTS scheduled_recovery_actions_one_active_case_action_key
    ON scheduled_recovery_actions (recovery_case_id, action)
    WHERE status IN ('pending', 'running');
