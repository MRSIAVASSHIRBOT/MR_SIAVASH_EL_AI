-- Bound Telegram bridge retries and retain delivery-uncertainty evidence.
-- Existing jobs remain readable; new columns are nullable for backward compatibility.
ALTER TABLE telegram_jobs ADD COLUMN quarantined_at INTEGER;
ALTER TABLE telegram_jobs ADD COLUMN quarantine_reason TEXT;

CREATE INDEX idx_telegram_jobs_quarantine
    ON telegram_jobs (quarantined_at, state, created_at);
