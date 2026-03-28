-- Migration from schema version 16 to 17
-- Add XEP-0428: Message Fallback Indication support
-- Enables proper reply chain handling without string parsing

-- Fallback markers for messages (XEP-0428)
-- Stores character ranges that indicate fallback text (e.g., quoted text in replies)
-- This allows extracting clean message content without relying on "> " string patterns
CREATE TABLE IF NOT EXISTS fallback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,        -- Foreign key to message table
    ns_uri TEXT NOT NULL,               -- Namespace URI (e.g., "urn:xmpp:reply:0" for XEP-0461)
    from_char INTEGER NOT NULL,         -- Start character position (inclusive, 0-based)
    to_char INTEGER NOT NULL,           -- End character position (exclusive, 0-based)
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);

-- Index for efficient lookup by message
CREATE INDEX IF NOT EXISTS idx_fallback_message ON fallback(message_id);

-- Update schema version
UPDATE _meta SET int_val = 17 WHERE name = 'schema_version';
