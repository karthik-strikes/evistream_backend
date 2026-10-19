-- Tracks the last time a user made an authenticated request.
-- Updated at most once per 60 seconds per user (throttled in the auth dependency).
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ;
