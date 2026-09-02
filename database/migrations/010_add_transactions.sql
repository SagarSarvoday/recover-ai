-- Merchant business payment intents. Historical payment attempts intentionally remain unlinked.
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    merchant_id UUID NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    customer_id UUID NOT NULL REFERENCES customers(id) ON DELETE RESTRICT,
    merchant_transaction_id TEXT NOT NULL,
    razorpay_order_id TEXT UNIQUE,
    amount NUMERIC(12, 2) NOT NULL CHECK (amount > 0),
    currency TEXT NOT NULL DEFAULT 'INR',
    status TEXT NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'pending', 'paid', 'cancelled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT transactions_merchant_external_key UNIQUE (merchant_id, merchant_transaction_id)
);

ALTER TABLE payments
    ADD COLUMN transaction_id UUID REFERENCES transactions(id) ON DELETE SET NULL;

ALTER TABLE payments
    ADD COLUMN payment_method TEXT;

CREATE INDEX idx_transactions_merchant_id ON transactions(merchant_id);
CREATE INDEX idx_transactions_customer_id ON transactions(customer_id);
CREATE INDEX idx_payments_transaction_id ON payments(transaction_id);
