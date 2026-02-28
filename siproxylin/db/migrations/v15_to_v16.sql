-- Migration from schema version 15 to 16
-- Add MAM (Message Archive Management) catchup tracking table
-- This enables efficient MAM synchronization without redundant fetching

-- MAM catchup state tracking (inspired by Dino's implementation)
-- Tracks continuous ranges of messages that have been successfully retrieved from MAM
-- One row per account+server_jid combination (simplified single-range approach)
CREATE TABLE IF NOT EXISTS mam_catchup (
    account_id INTEGER NOT NULL,
    server_jid TEXT NOT NULL,           -- Account bare JID for 1-1 chats, room JID for MUCs
    from_time INTEGER NOT NULL,         -- Oldest message timestamp in synced range (Unix time)
    from_id TEXT,                       -- Oldest message MAM archive ID
    to_time INTEGER NOT NULL,           -- Newest message timestamp in synced range (Unix time)
    to_id TEXT,                         -- Newest message MAM archive ID
    from_end INTEGER DEFAULT 0,         -- Boolean: 1 if server has no older messages
    last_updated INTEGER,               -- Timestamp when this range was last extended
    PRIMARY KEY (account_id, server_jid),
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);

-- Index for efficient queries by account
CREATE INDEX IF NOT EXISTS idx_mam_catchup_account ON mam_catchup(account_id);

-- Update schema version
UPDATE _meta SET int_val = 16 WHERE name = 'schema_version';
