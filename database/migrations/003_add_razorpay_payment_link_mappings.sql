-- Apply after 002_add_razorpay_external_ids.sql.
ALTER TABLE payments
    ADD COLUMN IF NOT EXISTS razorpay_success_payment_id TEXT UNIQUE;

ALTER TABLE recovery_cases
    ADD COLUMN IF NOT EXISTS razorpay_payment_link_id TEXT UNIQUE;

CREATE INDEX IF NOT EXISTS idx_payments_razorpay_success_payment_id
    ON payments (razorpay_success_payment_id);

CREATE INDEX IF NOT EXISTS idx_recovery_cases_razorpay_payment_link_id
    ON recovery_cases (razorpay_payment_link_id);
