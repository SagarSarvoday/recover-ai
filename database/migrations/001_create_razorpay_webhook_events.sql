-- Apply this once to existing RecoverAI Supabase databases.
-- Raw Razorpay payloads are intentionally not stored.
CREATE TABLE IF NOT EXISTS razorpay_webhook_events (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    razorpay_event_id   TEXT NOT NULL UNIQUE,
    event_type          TEXT NOT NULL,
    external_entity_id  TEXT,
    processing_status   TEXT NOT NULL CHECK (
                            processing_status IN ('processed', 'ignored')
                         ),
    received_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_razorpay_webhook_events_event_type
    ON razorpay_webhook_events (event_type);
