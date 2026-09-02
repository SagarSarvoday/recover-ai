-- Apply after 006_add_legacy_razorpay_account_mapping.sql.
-- Authentication identifies merchants by normalized email, so this must be unique.
UPDATE merchants
SET email = lower(trim(email))
WHERE email <> lower(trim(email));

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM merchants
        GROUP BY email
        HAVING COUNT(*) > 1
    ) THEN
        RAISE EXCEPTION 'Cannot enforce merchant email uniqueness: duplicate merchant emails exist.';
    END IF;
END $$;

ALTER TABLE merchants
    ADD CONSTRAINT merchants_email_key UNIQUE (email);
