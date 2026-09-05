-- ---------------------------------------------------------------------------
-- Migration 016: Add merchant password reset tokens table
-- Stores cryptographically hashed, single-use, time-limited reset tokens.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS merchant_password_reset_tokens (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id   UUID NOT NULL REFERENCES merchants (id) ON DELETE CASCADE,
    token_hash    TEXT NOT NULL UNIQUE,
    expires_at    TIMESTAMPTZ NOT NULL,
    used_at       TIMESTAMPTZ,
    requested_ip  TEXT,
    user_agent    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_merchant_password_reset_tokens_merchant_id
    ON merchant_password_reset_tokens (merchant_id);

CREATE INDEX IF NOT EXISTS idx_merchant_password_reset_tokens_token_hash
    ON merchant_password_reset_tokens (token_hash);

CREATE INDEX IF NOT EXISTS idx_merchant_password_reset_tokens_expires_at
    ON merchant_password_reset_tokens (expires_at);
