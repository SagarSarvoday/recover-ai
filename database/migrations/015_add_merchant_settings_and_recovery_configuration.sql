-- Migration 015: Add merchant settings, support contact, and recovery configuration
-- Allows merchants to customize recovery parameters, branding, and contact details.

ALTER TABLE merchants
    ADD COLUMN IF NOT EXISTS business_name TEXT,
    ADD COLUMN IF NOT EXISTS support_email TEXT,
    ADD COLUMN IF NOT EXISTS support_phone TEXT,
    ADD COLUMN IF NOT EXISTS max_recovery_attempts INTEGER NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS default_payment_link_expiry_hours INTEGER NOT NULL DEFAULT 48,
    ADD COLUMN IF NOT EXISTS default_wait_minutes INTEGER NOT NULL DEFAULT 60,
    ADD COLUMN IF NOT EXISTS auto_notify_customer BOOLEAN NOT NULL DEFAULT TRUE;

-- Enforce sensible ranges on merchant recovery configuration
ALTER TABLE merchants
    DROP CONSTRAINT IF EXISTS merchants_max_recovery_attempts_chk,
    ADD CONSTRAINT merchants_max_recovery_attempts_chk
        CHECK (max_recovery_attempts >= 1 AND max_recovery_attempts <= 5);

ALTER TABLE merchants
    DROP CONSTRAINT IF EXISTS merchants_default_payment_link_expiry_hours_chk,
    ADD CONSTRAINT merchants_default_payment_link_expiry_hours_chk
        CHECK (default_payment_link_expiry_hours >= 1 AND default_payment_link_expiry_hours <= 168);

ALTER TABLE merchants
    DROP CONSTRAINT IF EXISTS merchants_default_wait_minutes_chk,
    ADD CONSTRAINT merchants_default_wait_minutes_chk
        CHECK (default_wait_minutes >= 15 AND default_wait_minutes <= 1440);
