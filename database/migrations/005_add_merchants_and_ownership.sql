-- Apply after 004_allow_payment_only_recovery_cases.sql.
-- This project began as a single-merchant development system. Create one fixed,
-- documented legacy merchant, backfill every existing row to it, and then make
-- merchant ownership mandatory. Future onboarding must create real merchants.

CREATE TABLE IF NOT EXISTS merchants (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                TEXT NOT NULL,
    email               TEXT NOT NULL,
    password_hash       TEXT,
    razorpay_account_id TEXT UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO merchants (id, name, email)
VALUES (
    '00000000-0000-0000-0000-000000000001',
    'RecoverAI Legacy Development Merchant',
    'legacy@recoverai.local'
)
ON CONFLICT (id) DO NOTHING;

ALTER TABLE customers ADD COLUMN IF NOT EXISTS merchant_id UUID;
ALTER TABLE payments ADD COLUMN IF NOT EXISTS merchant_id UUID;
ALTER TABLE recovery_cases ADD COLUMN IF NOT EXISTS merchant_id UUID;

UPDATE customers SET merchant_id = '00000000-0000-0000-0000-000000000001'
WHERE merchant_id IS NULL;
UPDATE payments SET merchant_id = '00000000-0000-0000-0000-000000000001'
WHERE merchant_id IS NULL;
UPDATE recovery_cases SET merchant_id = '00000000-0000-0000-0000-000000000001'
WHERE merchant_id IS NULL;

ALTER TABLE customers
    ALTER COLUMN merchant_id SET NOT NULL,
    ADD CONSTRAINT customers_merchant_id_fkey
        FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE RESTRICT;
ALTER TABLE payments
    ALTER COLUMN merchant_id SET NOT NULL,
    ADD CONSTRAINT payments_merchant_id_fkey
        FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE RESTRICT;
ALTER TABLE recovery_cases
    ALTER COLUMN merchant_id SET NOT NULL,
    ADD CONSTRAINT recovery_cases_merchant_id_fkey
        FOREIGN KEY (merchant_id) REFERENCES merchants (id) ON DELETE RESTRICT;

-- Replace globally scoped provider/customer identities with tenant-scoped ones.
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_email_key;
ALTER TABLE customers DROP CONSTRAINT IF EXISTS customers_razorpay_customer_id_key;
ALTER TABLE customers
    ADD CONSTRAINT customers_merchant_email_key UNIQUE (merchant_id, email),
    ADD CONSTRAINT customers_merchant_razorpay_customer_id_key
        UNIQUE (merchant_id, razorpay_customer_id);

CREATE INDEX IF NOT EXISTS idx_customers_merchant_id ON customers (merchant_id);
CREATE INDEX IF NOT EXISTS idx_payments_merchant_id ON payments (merchant_id);
CREATE INDEX IF NOT EXISTS idx_recovery_cases_merchant_id ON recovery_cases (merchant_id);
