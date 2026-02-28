-- DRUNK-XMPP-GUI Database Schema
-- Inspired by Dino's architecture with adaptations for ROADMAP-v1.txt requirements
-- Schema Version: 16

-- =============================================================================
-- Meta and Settings
-- =============================================================================

-- Metadata table for schema versioning and app state
CREATE TABLE _meta (
    name TEXT PRIMARY KEY,
    int_val INTEGER,
    text_val TEXT
);

-- Initialize schema version
INSERT INTO _meta (name, int_val) VALUES ('schema_version', 16);

-- Global application settings
CREATE TABLE settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    value TEXT
);

-- =============================================================================
-- Account Management
-- =============================================================================

-- XMPP accounts (multi-account support)
CREATE TABLE account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bare_jid TEXT NOT NULL UNIQUE,
    password TEXT,                      -- Base64 encoded (keytar replacement for now)
    nickname TEXT,                      -- XEP-0172 published nickname (visible to contacts)
    muc_nickname TEXT,                  -- Default nickname for MUC room joins
    enabled INTEGER NOT NULL DEFAULT 1, -- Account enabled/disabled

    -- Connection settings
    server_override TEXT,               -- Manual server override (broken SRV records)
    port INTEGER DEFAULT 5222,

    -- Proxy settings (per-account as per ROADMAP)
    proxy_type TEXT,                    -- 'socks5', 'http', or NULL
    proxy_host TEXT,
    proxy_port INTEGER,
    proxy_username TEXT,
    proxy_password TEXT,                -- Base64 encoded

    -- TLS settings
    ignore_tls_errors INTEGER NOT NULL DEFAULT 0,
    require_strong_tls INTEGER NOT NULL DEFAULT 1,
    client_cert_path TEXT,              -- Path to client certificate (unencrypted key only)
    client_cert_password TEXT,          -- Reserved for future use (encrypted certs not currently supported)

    -- OMEMO settings
    omemo_enabled INTEGER NOT NULL DEFAULT 1,
    omemo_mode TEXT DEFAULT 'default',  -- 'off', 'default', 'optional', 'required'
    omemo_blind_trust INTEGER NOT NULL DEFAULT 1,
    omemo_storage_path TEXT,            -- Path to OMEMO keys JSON

    -- Feature toggles
    webrtc_enabled INTEGER NOT NULL DEFAULT 0,  -- Disabled by default as per ROADMAP
    carbons_enabled INTEGER NOT NULL DEFAULT 1,
    typing_notifications INTEGER NOT NULL DEFAULT 1,
    read_receipts INTEGER NOT NULL DEFAULT 1,

    -- Logging settings (per-account)
    log_level TEXT DEFAULT 'INFO',          -- DEBUG, INFO, WARNING, ERROR, CRITICAL
    log_retention_days INTEGER DEFAULT 30,  -- How long to keep logs (0 = forever)
    log_app_enabled INTEGER NOT NULL DEFAULT 1,
    log_xml_enabled INTEGER NOT NULL DEFAULT 1,

    -- XMPP protocol state
    resource TEXT,
    roster_version TEXT,
    mam_earliest_synced INTEGER,       -- Timestamp of earliest MAM message

    -- Timestamps
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    last_connected INTEGER
);

-- Per-account settings (overrides global settings)
CREATE TABLE account_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    UNIQUE (account_id, key),
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);

-- =============================================================================
-- JID Management (Normalized)
-- =============================================================================

-- Normalized JID table (avoids duplication across tables)
CREATE TABLE jid (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bare_jid TEXT NOT NULL UNIQUE
);

CREATE INDEX jid_bare_jid_idx ON jid (bare_jid);

-- Entity tracking (presence, capabilities, last seen)
CREATE TABLE entity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    resource TEXT,
    caps_hash TEXT,                     -- XEP-0115 capabilities
    last_seen INTEGER,
    UNIQUE (account_id, jid_id, resource) ON CONFLICT IGNORE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX entity_account_jid_idx ON entity (account_id, jid_id);

-- =============================================================================
-- Roster / Contacts
-- =============================================================================

-- Roster (contact list)
CREATE TABLE roster (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    name TEXT,                          -- Display name / roster name
    -- Subscription state (RFC 6121) - boolean flags for clarity
    we_see_their_presence INTEGER NOT NULL DEFAULT 0,       -- 1 = we subscribed to them ('to' or 'both')
    they_see_our_presence INTEGER NOT NULL DEFAULT 0,       -- 1 = they subscribed to us ('from' or 'both')
    we_requested_subscription INTEGER NOT NULL DEFAULT 0,   -- 1 = pending outgoing request
    they_requested_subscription INTEGER NOT NULL DEFAULT 0, -- 1 = pending incoming request
    subscription TEXT,                  -- DEPRECATED: kept for backward compatibility, use boolean fields above
    blocked INTEGER NOT NULL DEFAULT 0, -- XEP-0191 blocking
    UNIQUE (account_id, jid_id) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX roster_account_idx ON roster (account_id);
CREATE INDEX roster_blocked_idx ON roster (account_id, blocked);

-- Contact avatars (XEP-0054 vCard, XEP-0153)
CREATE TABLE contact_avatar (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    jid_id INTEGER NOT NULL,
    account_id INTEGER NOT NULL,
    hash TEXT NOT NULL,
    type INTEGER NOT NULL,              -- 0=vCard, 1=PEP
    data BLOB,                          -- Avatar image data
    UNIQUE (jid_id, account_id, type) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX avatar_jid_account_idx ON contact_avatar (jid_id, account_id);

-- =============================================================================
-- Conversations
-- =============================================================================

-- Conversation (unified 1-to-1 and MUC)
CREATE TABLE conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    resource TEXT,                      -- For specific resource if needed

    -- Conversation type
    type INTEGER NOT NULL,              -- 0=chat, 1=groupchat (MUC)

    -- State
    active INTEGER NOT NULL DEFAULT 1,
    active_last_changed INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    last_active INTEGER,
    pinned INTEGER NOT NULL DEFAULT 0,

    -- Encryption (current setting for this conversation)
    encryption INTEGER NOT NULL DEFAULT 0,  -- 0=plain, 1=OMEMO

    -- Read state
    read_up_to_item INTEGER NOT NULL DEFAULT -1,  -- content_item.id of last read message

    -- Per-conversation feature toggles
    send_typing INTEGER NOT NULL DEFAULT 1,
    send_marker INTEGER NOT NULL DEFAULT 1,
    notification INTEGER NOT NULL DEFAULT 1,

    -- MUC-specific
    muc_nick TEXT,
    muc_password TEXT,
    muc_autojoin INTEGER NOT NULL DEFAULT 0,

    UNIQUE (account_id, jid_id, type) ON CONFLICT IGNORE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX conversation_account_idx ON conversation (account_id);
CREATE INDEX conversation_active_idx ON conversation (account_id, active);
CREATE INDEX conversation_last_active_idx ON conversation (account_id, last_active DESC);

-- Per-conversation settings (additional key-value pairs)
CREATE TABLE conversation_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    UNIQUE (conversation_id, key),
    FOREIGN KEY (conversation_id) REFERENCES conversation(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX conversation_settings_idx ON conversation_settings (conversation_id, key);

-- =============================================================================
-- Content Items (Unified timeline)
-- =============================================================================

-- Unified content timeline (messages, files, calls)
CREATE TABLE content_item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    time INTEGER NOT NULL,              -- Server timestamp
    local_time INTEGER NOT NULL,        -- Local receipt timestamp
    content_type INTEGER NOT NULL,      -- 0=message, 2=file_transfer, 3=call
    foreign_id INTEGER NOT NULL,        -- References message.id, file_transfer.id, or call.id
    hide INTEGER NOT NULL DEFAULT 0,    -- 0=visible, 1=hidden (for selective hiding, e.g., sensitive content)
                                        -- Future: Add hide/unhide buttons for privacy control
    UNIQUE (content_type, foreign_id) ON CONFLICT IGNORE,
    FOREIGN KEY (conversation_id) REFERENCES conversation(id) ON DELETE CASCADE
);

CREATE INDEX content_item_conversation_time_idx ON content_item (conversation_id, hide, time);
CREATE INDEX content_item_foreign_idx ON content_item (content_type, foreign_id);

-- =============================================================================
-- Messages
-- =============================================================================

-- Messages
CREATE TABLE message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Message IDs (XEP-0359: Unique and Stable Stanza IDs)
    message_id TEXT,                    -- Message 'id' attribute (msg.get('id'))
    origin_id TEXT,                     -- Sender-assigned stable ID (XEP-0359)
    stanza_id TEXT,                     -- Server-assigned stable ID (XEP-0359)

    -- Basic info
    account_id INTEGER NOT NULL,
    counterpart_id INTEGER NOT NULL,    -- JID of other party
    our_resource TEXT,
    counterpart_resource TEXT,

    -- Message metadata
    direction INTEGER NOT NULL,         -- 0=received, 1=sent
    type INTEGER NOT NULL,              -- 0=chat, 1=groupchat, 2=error
    time INTEGER NOT NULL,
    local_time INTEGER NOT NULL,

    -- Content
    body TEXT,

    -- Flags
    encryption INTEGER NOT NULL DEFAULT 0,  -- 0=plain, 1=OMEMO
    marked INTEGER NOT NULL DEFAULT 0,      -- Read/displayed marker received
    is_carbon INTEGER NOT NULL DEFAULT 0,   -- 0=regular, 1=carbon copy from another device

    -- Retry tracking (Phase 4)
    retry_count INTEGER NOT NULL DEFAULT 0,
    first_retry_attempt INTEGER,            -- Timestamp of first retry
    last_retry_attempt INTEGER,             -- Timestamp of last retry

    -- Error details
    error_text TEXT,                        -- Error message for failed sends (marked=8)

    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (counterpart_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX message_account_counterpart_time_idx ON message (account_id, counterpart_id, time);
CREATE INDEX message_message_id_idx ON message (account_id, counterpart_id, message_id);
CREATE INDEX message_origin_id_idx ON message (account_id, counterpart_id, origin_id);
CREATE INDEX message_stanza_id_idx ON message (account_id, counterpart_id, stanza_id);
CREATE INDEX message_marked_idx ON message (account_id, marked);
CREATE INDEX message_error_idx ON message (account_id, marked) WHERE marked = 8;

-- Full-text search on message bodies
CREATE VIRTUAL TABLE _fts_message USING fts4(
    tokenize=unicode61,
    content="message",
    body TEXT
);

-- FTS triggers to keep search index in sync
CREATE TRIGGER _fts_ai_message AFTER INSERT ON message BEGIN
    INSERT INTO _fts_message(docid, body) VALUES(new.rowid, new.body);
END;

CREATE TRIGGER _fts_au_message AFTER UPDATE ON message BEGIN
    DELETE FROM _fts_message WHERE docid=old.rowid;
    INSERT INTO _fts_message(docid, body) VALUES(new.rowid, new.body);
END;

CREATE TRIGGER _fts_bd_message BEFORE DELETE ON message BEGIN
    DELETE FROM _fts_message WHERE docid=old.rowid;
END;

-- Message corrections (XEP-0308)
CREATE TABLE message_correction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL UNIQUE, -- The corrected message
    to_stanza_id TEXT,                  -- Original message stanza_id
    correction_time INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);

CREATE INDEX message_correction_stanza_id_idx ON message_correction (to_stanza_id);

-- Message correction history (track all edits)
CREATE TABLE message_correction_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    old_body TEXT NOT NULL,
    correction_time INTEGER NOT NULL,
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE
);

CREATE INDEX message_correction_history_msg_idx ON message_correction_history (message_id, correction_time);

-- Message replies (XEP-0461)
CREATE TABLE reply (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL UNIQUE, -- The reply message
    quoted_message_id INTEGER,          -- Local message.id if found
    quoted_message_stanza_id TEXT,      -- Stanza ID being replied to
    quoted_message_from TEXT,           -- JID of quoted message sender
    FOREIGN KEY (message_id) REFERENCES message(id) ON DELETE CASCADE,
    FOREIGN KEY (quoted_message_id) REFERENCES message(id) ON DELETE SET NULL
);

CREATE INDEX reply_quoted_stanza_id_idx ON reply (quoted_message_stanza_id);

-- Reactions (XEP-0444)
CREATE TABLE reaction (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    content_item_id INTEGER NOT NULL,   -- The message being reacted to
    jid_id INTEGER,                     -- For 1-1 reactions (nullable for MUC)
    occupant_id INTEGER,                -- For MUC reactions (references occupant.id, v5→v6)
    time INTEGER NOT NULL,
    emojis TEXT,                        -- JSON array of emoji strings
    UNIQUE (account_id, content_item_id, jid_id) ON CONFLICT REPLACE,
    UNIQUE (account_id, content_item_id, occupant_id) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (content_item_id) REFERENCES content_item(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE,
    FOREIGN KEY (occupant_id) REFERENCES occupant(id) ON DELETE CASCADE
);

CREATE INDEX reaction_content_item_idx ON reaction (content_item_id);

-- MUC occupant tracking (for matching real JID to occupant-id)
CREATE TABLE occupant (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    room_jid_id INTEGER NOT NULL,
    nick TEXT NOT NULL,
    jid_id INTEGER,                     -- Real JID if known
    occupant_id TEXT,                   -- XEP-0421 occupant-id
    UNIQUE (account_id, room_jid_id, nick) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (room_jid_id) REFERENCES jid(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX occupant_room_idx ON occupant (account_id, room_jid_id);
CREATE INDEX occupant_id_idx ON occupant (account_id, room_jid_id, occupant_id);

-- =============================================================================
-- File Transfers
-- =============================================================================

-- File transfers (XEP-0363 HTTP Upload, XEP-0454 OMEMO Media)
CREATE TABLE file_transfer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    counterpart_id INTEGER NOT NULL,
    counterpart_resource TEXT,
    our_resource TEXT,

    -- Message IDs (XEP-0359: for deduplication, added v7, renamed v10)
    message_id TEXT,                    -- Message 'id' attribute (msg.get('id'))
    origin_id TEXT,                     -- Sender-assigned stable ID (XEP-0359)
    stanza_id TEXT,                     -- Server-assigned stable ID (XEP-0359)

    direction INTEGER NOT NULL,         -- 0=received, 1=sent
    time INTEGER NOT NULL,
    local_time INTEGER NOT NULL,

    -- File info
    file_name TEXT NOT NULL,
    path TEXT,                          -- Local filesystem path
    url TEXT,                           -- HTTP URL or aesgcm:// URL
    mime_type TEXT,
    size INTEGER,

    -- State
    state INTEGER NOT NULL,             -- 0=pending, 1=transferring, 2=complete, 3=failed
    encryption INTEGER NOT NULL DEFAULT 0,  -- 0=plain, 1=OMEMO (aesgcm://)
    is_carbon INTEGER NOT NULL DEFAULT 0,   -- 0=regular, 1=carbon copy from another device

    -- Provider info
    provider INTEGER,                   -- 0=HTTP Upload
    info TEXT,                          -- JSON metadata

    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (counterpart_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX file_transfer_account_counterpart_idx ON file_transfer (account_id, counterpart_id);
CREATE INDEX file_transfer_time_idx ON file_transfer (time);
CREATE INDEX file_transfer_message_id_idx ON file_transfer (message_id) WHERE message_id IS NOT NULL;
CREATE INDEX file_transfer_origin_id_idx ON file_transfer (origin_id) WHERE origin_id IS NOT NULL;
CREATE INDEX file_transfer_stanza_id_idx ON file_transfer (stanza_id) WHERE stanza_id IS NOT NULL;

-- =============================================================================
-- Calls (WebRTC)
-- =============================================================================

-- Call log
CREATE TABLE call (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    counterpart_id INTEGER NOT NULL,
    counterpart_resource TEXT,
    our_resource TEXT,

    direction INTEGER NOT NULL,         -- 0=incoming, 1=outgoing (Dino-compatible)
    time INTEGER NOT NULL,              -- Start time
    local_time INTEGER NOT NULL,
    end_time INTEGER,                   -- End time (NULL if ongoing)

    encryption INTEGER NOT NULL DEFAULT 0,  -- 0=plain, 1=DTLS-SRTP
    state INTEGER NOT NULL,             -- 0=ringing, 1=establishing, 2=in_progress, 3=other_device, 4=ended, 5=declined, 6=missed, 7=failed (Dino-compatible)

    -- Call type
    type INTEGER NOT NULL,              -- 0=audio, 1=video, 2=screen_share

    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (counterpart_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX call_account_counterpart_idx ON call (account_id, counterpart_id);
CREATE INDEX call_time_idx ON call (time DESC);

-- Call participants (for group calls - future)
CREATE TABLE call_participant (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    resource TEXT,
    joined_time INTEGER,
    left_time INTEGER,
    FOREIGN KEY (call_id) REFERENCES call(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX call_participant_call_idx ON call_participant (call_id);

-- =============================================================================
-- OMEMO Device/Key Management
-- =============================================================================

-- OMEMO key-value storage (internal OMEMO library crypto material, v2→v3)
-- Stores identity keys, sessions, pre-keys, etc.
CREATE TABLE omemo_storage (
    account_id INTEGER NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,  -- JSON-serialized value
    PRIMARY KEY (account_id, key),
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE
);

CREATE INDEX omemo_storage_account_idx ON omemo_storage (account_id);

-- OMEMO devices (track known devices for contacts)
CREATE TABLE omemo_device (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    identity_key TEXT NOT NULL,         -- Base64 encoded public key
    trust_level INTEGER NOT NULL DEFAULT 0,  -- 0=untrusted, 1=blind_trust, 2=verified, 3=compromised
    first_seen INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    last_seen INTEGER,
    label TEXT,                         -- Device label/name
    UNIQUE (account_id, jid_id, device_id) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX omemo_device_account_jid_idx ON omemo_device (account_id, jid_id);
CREATE INDEX omemo_device_trust_idx ON omemo_device (account_id, trust_level);

-- =============================================================================
-- MAM (Message Archive Management)
-- =============================================================================

-- MAM synchronization state tracking
-- MAM (Message Archive Management) catchup state tracking
-- Tracks continuous ranges of messages that have been successfully retrieved from MAM
-- Simplified single-range approach: one row per account+server_jid combination
CREATE TABLE mam_catchup (
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
CREATE INDEX mam_catchup_account_idx ON mam_catchup(account_id);

-- =============================================================================
-- Service Discovery Cache
-- =============================================================================

-- Entity capabilities cache (XEP-0115)
CREATE TABLE entity_identity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    category TEXT NOT NULL,
    name TEXT,
    type TEXT NOT NULL,
    UNIQUE (entity, category, type) ON CONFLICT IGNORE
);

CREATE INDEX entity_identity_entity_idx ON entity_identity (entity);

-- Entity features cache
CREATE TABLE entity_feature (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL,
    feature TEXT NOT NULL,
    UNIQUE (entity, feature) ON CONFLICT IGNORE
);

CREATE INDEX entity_feature_entity_idx ON entity_feature (entity);

-- =============================================================================
-- Bookmarks (XEP-0402)
-- =============================================================================

-- Bookmarks (server-side MUC room list)
CREATE TABLE bookmark (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    jid_id INTEGER NOT NULL,
    name TEXT,
    nick TEXT,
    password TEXT,                      -- Base64 encoded
    autojoin INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, jid_id) ON CONFLICT REPLACE,
    FOREIGN KEY (account_id) REFERENCES account(id) ON DELETE CASCADE,
    FOREIGN KEY (jid_id) REFERENCES jid(id) ON DELETE CASCADE
);

CREATE INDEX bookmark_account_idx ON bookmark (account_id);
CREATE INDEX bookmark_autojoin_idx ON bookmark (account_id, autojoin);
