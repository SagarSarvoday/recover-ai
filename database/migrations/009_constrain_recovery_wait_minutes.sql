-- WAIT timing is AI-provided but is constrained at the persistence boundary too.
ALTER TABLE recovery_cases
    ADD CONSTRAINT recovery_cases_ai_wait_minutes_range_chk
    CHECK (ai_wait_minutes IS NULL OR ai_wait_minutes BETWEEN 1 AND 10080);
