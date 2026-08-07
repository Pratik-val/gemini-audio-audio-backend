-- db/schema.sql
-- Idempotent schema initialization for the audio backend.
-- Run automatically by config.database.init_db() on startup.

CREATE TABLE IF NOT EXISTS calls (
    id              SERIAL PRIMARY KEY,
    call_id         TEXT UNIQUE NOT NULL,
    interviewer_id  TEXT,
    interview_id    TEXT,
    dynamic_data    JSONB,
    transcripts     TEXT DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    status          TEXT DEFAULT 'registered',
    call_analysis   JSONB,
    start_timestamp BIGINT,
    end_timestamp   BIGINT
);

CREATE INDEX IF NOT EXISTS idx_calls_call_id ON calls (call_id);
CREATE INDEX IF NOT EXISTS idx_calls_interviewer_id ON calls (interviewer_id);