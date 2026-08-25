-- Apply this after 001_create_razorpay_webhook_events.sql.
-- Nullable fields preserve existing RecoverAI customer and payment records.
ALTER TABLE customers
    ADD COLUMN IF NOT EXISTS razorpay_customer_id TEXT UNIQUE;

ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS razorpay_payment_id TEXT UNIQUE;

CREATE INDEX IF NOT EXISTS idx_payments_razorpay_payment_id
    ON payments (razorpay_payment_id);
