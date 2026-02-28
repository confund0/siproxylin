-- Migration: Add recent_emojis table for storing last 10 used emojis
-- Version: 14 to 15

CREATE TABLE IF NOT EXISTS recent_emojis (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emoji TEXT NOT NULL,          -- The emoji character
    used_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- When it was last used
    use_count INTEGER DEFAULT 1   -- How many times it's been used
);

-- Create indexes for faster lookups (IF NOT EXISTS to avoid errors if already created)
CREATE INDEX IF NOT EXISTS idx_recent_emojis_used_at ON recent_emojis(used_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_recent_emojis_emoji ON recent_emojis(emoji);
