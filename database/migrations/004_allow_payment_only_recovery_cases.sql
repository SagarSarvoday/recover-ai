-- Apply after 003_add_razorpay_payment_link_mappings.sql.
-- Financial records may be ingested before a meaningful customer profile is available.
ALTER TABLE customers
    ALTER COLUMN name DROP NOT NULL,
    ALTER COLUMN email DROP NOT NULL;

ALTER TABLE payments
    ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE recovery_cases
    ALTER COLUMN customer_id DROP NOT NULL;

ALTER TABLE payments
    DROP CONSTRAINT IF EXISTS payments_customer_id_fkey,
    ADD CONSTRAINT payments_customer_id_fkey
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE SET NULL;

ALTER TABLE recovery_cases
    DROP CONSTRAINT IF EXISTS recovery_cases_customer_id_fkey,
    ADD CONSTRAINT recovery_cases_customer_id_fkey
        FOREIGN KEY (customer_id) REFERENCES customers (id) ON DELETE SET NULL;
