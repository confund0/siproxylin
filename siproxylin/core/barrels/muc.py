"""
MucBarrel - Handles Multi-User Chat (MUC) operations.

Responsibilities:
- Joining and leaving rooms (XEP-0045)
- Auto-join bookmarked rooms on session start
- Bookmark synchronization (XEP-0402)
- MAM history retrieval for rooms (XEP-0313)
- Room feature detection (XEP-0030) for OMEMO compatibility
- MUC invitations handling
- Room configuration updates
"""

import logging
import base64
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from dataclasses import dataclass

from ...db.database import get_db


# =============================================================================
# Data Classes for MUC Service Layer
# =============================================================================

@dataclass
class RoomInfo:
    """Encapsulates MUC room information."""
    jid: str
    name: Optional[str]
    description: Optional[str]
    subject: Optional[str]
    # Room features (from conversation table)
    nonanonymous: bool
    membersonly: bool
    persistent: bool = False
    public: bool = False
    moderated: bool = False
    password_protected: bool = False
    # Extended config fields (v13 schema)
    max_users: Optional[int] = None  # None = unlimited
    allow_invites: bool = True
    allow_subject_change: bool = False
    enable_logging: bool = False
    whois: str = 'moderators'  # 'anyone' or 'moderators' - who can see real JIDs
    # Additional info
    omemo_compatible: bool = False
    participant_count: int = 0
    # Config metadata
    config_fetched: Optional[int] = None  # Timestamp (None=never, 0=failed, >0=success)


@dataclass
class RoomSettings:
    """Per-room local settings (from conversation + bookmark tables)."""
    room_jid: str
    # From conversation table
    notification: int = 1  # 0=off, 1=all, 2=mentions
    send_typing: bool = True
    send_marker: bool = True
    encryption: int = 0  # 0=plain, 1=OMEMO
    # From bookmark table
    autojoin: bool = False
    # From conversation_settings
    local_alias: str = ''
    history_limit: int = 100


@dataclass
class Participant:
    """MUC room participant."""
    nick: str
    jid: Optional[str]  # Real JID (None if anonymous/hidden)
    role: str  # moderator, participant, visitor, none
    affiliation: str  # owner, admin, member, outcast, none


@dataclass
class Bookmark:
    """MUC bookmark data."""
    room_jid: str
    name: Optional[str]
    nick: str
    password: Optional[str]  # Base64 encoded in DB, decoded here
    autojoin: bool


class MucBarrel:
    """Manages MUC operations for an account."""

    def __init__(self, account_id: int, client, db, logger, signals: dict, account_data: dict):
        """
        Initialize MUC barrel.

        Args:
            account_id: Account ID
            client: DrunkXMPP client instance (will be set after connection)
            db: Database singleton (direct access)
            logger: Account logger instance
            signals: Dict of Qt signal references for emitting events
            account_data: Dict with account data (for alias, bare_jid, etc.)
        """
        self.account_id = account_id
        self.client = client  # Will be None initially, set by brewery after connection
        self.db = db
        self.logger = logger
        self.signals = signals
        self.account_data = account_data

        # Track rooms waiting for MAM retrieval (after self-presence received)
        self._pending_mam_rooms = set()

        # In-memory cache for room configurations (XEP-0045 §10)
        # Format: {room_jid: {persistent: bool, public: bool, ...}}
        self.room_configs: Dict[str, Dict[str, Any]] = {}

        # Track voice request timestamps per room (for throttling)
        # Format: {room_jid: timestamp}
        self._voice_request_timestamps: Dict[str, float] = {}

    async def add_and_join_room(self, room_jid: str, nick: str, password: str = None):
        """
        Add a room to the client's configuration and join it.

        This is typically called when user manually joins a room via GUI.
        Room metadata (features, config) will be fetched in on_muc_joined callback.

        Args:
            room_jid: Room JID
            nick: Nickname to use
            password: Room password (optional)
        """
        if not self.client:
            raise RuntimeError("Not connected")

        if self.logger:
            self.logger.info(f"Adding and joining room: {room_jid} as {nick}")

        # Start database transaction for atomicity
        db = get_db()
        try:
            db.execute("BEGIN")

            # Perform the join (adds to rooms dict, sends presence, marks for MAM)
            await self._perform_room_join(room_jid, nick, password)

            # Commit transaction
            db.commit()

        except Exception as e:
            # Rollback on error
            db.execute("ROLLBACK")
            if self.logger:
                self.logger.error(f"Failed to join room {room_jid}: {e}")
            raise

    async def _update_room_features_from_dict(self, room_jid: str, features: dict):
        """
        Update conversation table with MUC features from pre-fetched features dict.

        Args:
            room_jid: Room JID
            features: Features dict from get_room_features()
        """
        db = get_db()
        jid_row = db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
        if jid_row:
            jid_id = jid_row['id']
            try:
                db.execute("""
                    UPDATE conversation
                    SET muc_nonanonymous = ?,
                        muc_membersonly = ?
                    WHERE account_id = ? AND jid_id = ? AND type = 1
                """, (
                    1 if features.get('muc_nonanonymous') else 0,
                    1 if features.get('muc_membersonly') else 0,
                    self.account_id,
                    jid_id
                ))

                if self.logger:
                    omemo_status = "✓ supports" if features.get('supports_omemo') else "✗ does NOT support"
                    self.logger.info(
                        f"Room {room_jid}: nonanonymous={features.get('muc_nonanonymous')}, "
                        f"membersonly={features.get('muc_membersonly')} - {omemo_status} OMEMO"
                    )
            except Exception as db_err:
                if self.logger:
                    self.logger.warning(f"Failed to update room features in DB (migration pending?): {db_err}")

    async def _update_bookmark_name(self, room_jid: str, room_name: str):
        """
        Update bookmark name in database.

        Args:
            room_jid: Room JID
            room_name: Room name
        """
        db = get_db()
        jid_row = db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
        if jid_row:
            db.execute("""
                UPDATE bookmark
                SET name = ?
                WHERE account_id = ? AND jid_id = ? AND (name IS NULL OR name = '')
            """, (room_name, self.account_id, jid_row['id']))

            if self.logger:
                self.logger.info(f"Updated bookmark name for {room_jid} to '{room_name}'")

            # Emit roster_updated to refresh GUI
            self.signals['roster_updated'].emit(self.account_id)

    async def fetch_and_store_room_config(self, room_jid: str) -> bool:
        """
        Fetch room configuration from server and store in memory.

        This queries the room owner configuration form (XEP-0045 §10) and
        caches the result for the session. Requires owner permissions.

        Args:
            room_jid: Room JID

        Returns:
            True if config was successfully fetched and cached, False otherwise
        """
        if not self.client:
            if self.logger:
                self.logger.debug("Cannot fetch room config: not connected")
            return False

        try:
            # Query room configuration (requires owner permissions)
            config = await self.client.get_room_config(room_jid)

            if not config:
                if self.logger:
                    self.logger.debug(f"No config returned for {room_jid}")
                return False

            # Check for errors
            if config.get('error'):
                if self.logger:
                    self.logger.debug(f"Failed to fetch room config for {room_jid}: {config['error']}")
                return False

            # Store config in memory
            self.room_configs[room_jid] = {
                'persistent': config.get('persistent', False),
                'public': config.get('public', False),
                'moderated': config.get('moderated', False),
                'membersonly': config.get('membersonly', False),
                'password_protected': config.get('password_protected', False),
                'description': config.get('roomdesc'),
                'subject': config.get('roomname'),
                'max_users': config.get('max_users'),
                'allow_invites': config.get('allow_invites', True),
                'allow_subject_change': config.get('allow_subject_change', False),
                'enable_logging': config.get('enable_logging', False),
                'whois': config.get('whois', 'moderators'),
            }

            if self.logger:
                self.logger.info(f"✓ Cached room config for {room_jid} (persistent={config.get('persistent')}, public={config.get('public')}, moderated={config.get('moderated')})")

            return True

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to fetch/cache room config for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            return False

    async def set_room_config(self, room_jid: str, config: Dict[str, Any]) -> bool:
        """
        Submit room configuration to server.

        Args:
            room_jid: Room JID
            config: Dict with configuration fields:
                {
                    'roomname': str,
                    'roomdesc': str,
                    'membersonly': bool,
                    'moderatedroom': bool,
                    'passwordprotectedroom': bool,
                    'roomsecret': str (password),
                    'maxusers': int or None,
                    'persistentroom': bool,
                    'publicroom': bool,
                    'enablelogging': bool,
                }

        Returns:
            True if config was successfully submitted, False otherwise
        """
        if not self.client:
            if self.logger:
                self.logger.debug("Cannot set room config: not connected")
            return False

        try:
            # Submit config via DrunkXMPP
            success = await self.client.set_room_config(room_jid, config)

            if success and self.logger:
                self.logger.info(f"✓ Room configuration updated for {room_jid}")

            return success

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to set room config for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            return False

    async def _perform_room_join(self, room_jid: str, nick: str, password: str = None) -> bool:
        """
        Core room join logic (no metadata fetching).

        This is a helper method that handles the mechanics of joining a room:
        - Adds to client.rooms dict
        - Sends join presence
        - Marks for MAM retrieval

        Metadata fetching (features, config) happens later in on_muc_joined callback.

        Args:
            room_jid: Room JID
            nick: Nickname to use
            password: Room password (optional)

        Returns:
            True if this is a NEW join (not already joined), False if already joined
        """
        if not self.client:
            raise RuntimeError("Not connected")

        # Check if room is already joined (to avoid duplicate MAM retrieval)
        already_joined = room_jid in self.client.rooms

        if self.logger:
            if already_joined:
                self.logger.debug(f"Room already joined: {room_jid}, skipping duplicate join")
            else:
                self.logger.info(f"Joining room: {room_jid} as {nick}")

        # Add to client's rooms dictionary
        self.client.rooms[room_jid] = {
            'nick': nick,
            'password': password
        }

        # Join the room (DrunkXMPP handles duplicate join protection)
        await self.client.join_room(room_jid, nick, password)

        # Mark room for MAM retrieval after self-presence is received
        # (only for new joins, not re-joins of already joined rooms)
        if not already_joined:
            self._pending_mam_rooms.add(room_jid)
            if self.logger:
                self.logger.debug(f"Room {room_jid} marked for MAM retrieval after join completes")

        return not already_joined  # True = new join, False = already joined

    async def on_muc_joined(self, room_jid: str, nick: str):
        """
        Callback fired when MUC room join is complete (self-presence received).
        This is called by DrunkXMPP after status code 110 presence is received,
        ensuring OMEMO device sessions are established before MAM retrieval.

        Fetches room metadata (features, config) and then retrieves MAM history.

        Args:
            room_jid: Room JID
            nick: Our nickname in the room
        """
        if self.logger:
            self.logger.debug(f"MUC join complete for {room_jid} as {nick}")

        # Phase 1: Fetch room metadata (features and config)
        # Do this AFTER join is confirmed (we're now an occupant)
        try:
            # Query room features (disco#info) for OMEMO compatibility
            room_features = await self.client.get_room_features(room_jid)
            await self._update_room_features_from_dict(room_jid, room_features)

            # Cache disco#info in client for use by dialog (includes allow_subject_change)
            if not hasattr(self.client, 'disco_cache'):
                self.client.disco_cache = {}
            self.client.disco_cache[room_jid] = room_features

            # Update bookmark name if available
            room_name = room_features.get('name')
            if room_name:
                await self._update_bookmark_name(room_jid, room_name)

            if self.logger:
                self.logger.debug(f"✓ Room features updated for {room_jid}")

        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to fetch room features for {room_jid}: {e}")

        # Phase 2: Fetch room configuration (owner-only, may fail gracefully)
        try:
            await self.fetch_and_store_room_config(room_jid)
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Could not fetch room config for {room_jid}: {e}")

        # Phase 3: Retrieve MAM history (if this was a new join)
        if room_jid in self._pending_mam_rooms:
            self._pending_mam_rooms.remove(room_jid)
            if self.logger:
                self.logger.info(f"Retrieving MAM for {room_jid} (after join complete)")

            try:
                await self._retrieve_muc_history(room_jid)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to retrieve MAM for {room_jid}: {e}")

    async def _retrieve_muc_history(self, room_jid: str, max_messages: Optional[int] = None):
        """
        Retrieve MAM history for a MUC room and store in database.

        Args:
            room_jid: Room JID
            max_messages: Maximum number of messages to retrieve (None = unlimited)
        """
        if not self.client:
            return

        try:
            if self.logger:
                self.logger.info(f"Retrieving MAM history for {room_jid}...")

            # Check if room supports MAM
            mam_supported = await self.client.check_mam_support(room_jid)
            if not mam_supported:
                if self.logger:
                    self.logger.warning(f"Room {room_jid} does not support MAM")
                return

            # Get most recent message timestamp from DB to avoid re-downloading old messages
            # Use 5-minute overlap for minimal duplication (handles clock skew without excessive re-fetching)
            start_time = None
            latest_msg = self.db.fetchone("""
                SELECT MAX(time) as latest_time
                FROM message
                WHERE counterpart_id = (SELECT id FROM jid WHERE bare_jid = ?)
            """, (room_jid,))

            if latest_msg and latest_msg['latest_time']:
                # Start from 5 minutes before latest message for minimal overlap
                # IMPORTANT: Must use UTC timezone for MAM compliance (XEP-0313)
                start_time = datetime.fromtimestamp(latest_msg['latest_time'] - 300, tz=timezone.utc)  # 5 min (was 1h)
                if self.logger:
                    self.logger.debug(f"Querying MAM since {start_time} (5min overlap from latest msg)")
            else:
                max_str = str(max_messages) if max_messages else "all available"
                if self.logger:
                    self.logger.debug(f"No existing messages, querying {max_str} messages from MAM")

            # Retrieve history from MAM (only NEW messages if start_time is set)
            history = await self.client.retrieve_history(
                jid=room_jid,
                start=start_time,
                max_messages=max_messages
            )

            if self.logger:
                self.logger.info(f"Retrieved {len(history)} messages from MAM for {room_jid}")

            if not history:
                return

            # Get or create JID entry for room
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if jid_row:
                jid_id = jid_row['id']
            else:
                cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                jid_id = cursor.lastrowid

            # Filter out our own messages
            our_nick = self.client.rooms[room_jid].get('nick') if room_jid in self.client.rooms else None

            # OPTIMIZATION: Batch duplicate detection - check BOTH message and file_transfer tables
            # Collect all archive_ids and query DB once instead of 500 times
            # (prevents duplicates when OMEMO encrypted files fail to decrypt on re-retrieval)
            archive_ids = [msg_data.get('archive_id') for msg_data in history if msg_data.get('archive_id')]

            existing_stanza_ids = set()
            if archive_ids:
                # Query all existing messages in one go
                placeholders = ','.join('?' * len(archive_ids))

                # Check message table
                existing_msgs = self.db.fetchall(f"""
                    SELECT stanza_id FROM message
                    WHERE account_id = ? AND counterpart_id = ? AND stanza_id IN ({placeholders})
                """, (self.account_id, jid_id, *archive_ids))

                # Check file_transfer table
                existing_files = self.db.fetchall(f"""
                    SELECT stanza_id FROM file_transfer
                    WHERE account_id = ? AND counterpart_id = ? AND stanza_id IN ({placeholders})
                """, (self.account_id, jid_id, *archive_ids))

                # Combine both sets
                existing_stanza_ids = {row['stanza_id'] for row in existing_msgs} | {row['stanza_id'] for row in existing_files}

            # Early duplicate detection - stop if we hit 10 consecutive duplicates
            # This means we've caught up to already-synced range
            consecutive_duplicates = 0
            MAX_CONSECUTIVE_DUPLICATES = 10

            # Store messages in database
            inserted_count = 0
            for msg_data in history:
                # Extract data from MAM result
                sender_jid = msg_data['jid']  # Bare JID
                nick = msg_data.get('nick', '')  # MUC sender nickname (from resource)
                body = msg_data['body']
                timestamp = int(msg_data['timestamp'].timestamp())
                is_encrypted = msg_data.get('is_encrypted', False)
                occupant_id = msg_data.get('occupant_id')  # XEP-0421

                # Get MAM archive result ID (stored as stanza_id)
                archive_id = msg_data.get('archive_id')
                archived_msg = msg_data.get('message')  # Raw stanza from MAM

                # Extract XEP-0359 IDs from archived message for reactions
                origin_id = None
                stanza_id = None
                if archived_msg:
                    try:
                        origin_id = archived_msg['origin_id']['id'] if archived_msg['origin_id']['id'] else None
                    except (KeyError, TypeError):
                        pass
                    stanza_id = archived_msg.get('id')

                # Skip our own messages using occupant-id (most reliable) or nickname fallback
                is_our_message = False
                if occupant_id and room_jid in self.client.own_occupant_ids:
                    is_our_message = (occupant_id == self.client.own_occupant_ids[room_jid])
                elif nick and our_nick:
                    is_our_message = (nick.lower() == our_nick.lower())

                if is_our_message:
                    if self.logger:
                        self.logger.debug(f"Skipping own MAM message from {nick}")
                    continue

                # Check if message already exists (using pre-loaded set)
                if archive_id and archive_id in existing_stanza_ids:
                    consecutive_duplicates += 1
                    if consecutive_duplicates >= MAX_CONSECUTIVE_DUPLICATES:
                        if self.logger:
                            self.logger.info(f"Hit {MAX_CONSECUTIVE_DUPLICATES} consecutive duplicates for {room_jid}, stopping early (already synced)")
                        break  # Stop early - we've caught up
                    continue
                else:
                    consecutive_duplicates = 0  # Reset on new message

                # Fallback: Check by timestamp+body if no archive_id
                if not archive_id:
                    existing = self.db.fetchone("""
                        SELECT id FROM message
                        WHERE account_id = ? AND counterpart_id = ? AND time = ? AND body = ?
                    """, (self.account_id, jid_id, timestamp, body))
                    if existing:
                        if self.logger:
                            self.logger.debug(f"MAM message already exists (by timestamp+body), skipping")
                        continue

                # Insert message
                conversation_id = self.db.get_or_create_conversation(self.account_id, jid_id, 1)  # type=1 MUC
                result = self.db.insert_message_atomic(
                    account_id=self.account_id,
                    counterpart_id=jid_id,
                    conversation_id=conversation_id,
                    direction=0,  # direction=0 (received)
                    msg_type=1,  # type=1 (groupchat/MUC)
                    time=timestamp,
                    local_time=timestamp,
                    body=body,
                    encryption=1 if is_encrypted else 0,
                    marked=0,  # marked=0 (MAM MUC messages not marked)
                    is_carbon=0,  # MUC messages never carbons
                    message_id=stanza_id,  # Sender's message ID (for reactions)
                    origin_id=origin_id,  # Sender's origin-id (XEP-0359, for reactions)
                    stanza_id=archive_id,  # MAM archive result ID (for dedup)
                    counterpart_resource=nick  # MUC nickname
                )

                if result != (None, None):
                    inserted_count += 1

            self.db.commit()

            if self.logger:
                self.logger.info(f"Stored {inserted_count} new MAM messages for {room_jid}")

            # We do NOT emit message_received signal for MAM history
            # MAM messages are historical archives, not new messages
            # They should not trigger notifications or unread counts

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to retrieve MAM history for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def auto_join_bookmarked_rooms(self):
        """
        Auto-join rooms marked for autojoin in database.

        Called on session start (after OMEMO ready).
        Room metadata (features, config) will be fetched in on_muc_joined callback.
        """
        if self.logger:
            self.logger.info("Checking for rooms to auto-join...")

        # Get rooms marked for autojoin
        autojoin_rooms = self.db.fetchall("""
            SELECT j.bare_jid, b.nick, b.password
            FROM bookmark b
            JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND b.autojoin = 1
        """, (self.account_id,))

        for room in autojoin_rooms:
            room_jid = room['bare_jid']
            nick = room['nick']

            # Decode password if present
            password = None
            if room['password']:
                try:
                    password = base64.b64decode(room['password']).decode()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to decode password for {room_jid}: {e}")

            if self.logger:
                self.logger.info(f"Auto-joining room: {room_jid} as {nick}")

            try:
                # Perform the join (adds to rooms dict, sends presence, marks for MAM)
                await self._perform_room_join(room_jid, nick, password)

            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to auto-join {room_jid}: {e}")

    async def sync_bookmarks(self, bookmarks: list):
        """
        Handle bookmarks received from server (XEP-0402).
        Syncs bookmarks to database. Does NOT auto-join (that's done separately on session start).

        Args:
            bookmarks: List of bookmark dicts with keys: jid, name, nick, autojoin
        """
        if self.logger:
            self.logger.info(f"📚 Received {len(bookmarks)} bookmarks from server (XEP-0402)")
            if bookmarks:
                self.logger.info("Bookmark details:")
                for bm in bookmarks:
                    self.logger.info(f"  - {bm.get('jid')}: name='{bm.get('name', '')}', nick='{bm.get('nick', '')}', autojoin={bm.get('autojoin', False)}, password={'***' if bm.get('password') else 'None'}")

        try:
            db = get_db()

            for bm in bookmarks:
                room_jid = bm.get('jid')
                name = bm.get('name', '')
                nick = bm.get('nick') or self.account_data.get('muc_nickname') or self.account_data.get('nickname') or self.account_data.get('bare_jid', '').split('@')[0] or 'User'
                autojoin = bm.get('autojoin', False)

                if not room_jid:
                    continue

                # Check if this is a new bookmark or an update
                existing = db.fetchone("""
                    SELECT b.autojoin, b.name FROM bookmark b
                    JOIN jid j ON b.jid_id = j.id
                    WHERE b.account_id = ? AND j.bare_jid = ?
                """, (self.account_id, room_jid))

                if self.logger:
                    if existing:
                        if existing['autojoin'] != (1 if autojoin else 0):
                            self.logger.info(f"📝 Bookmark UPDATED: {room_jid} autojoin changed: {bool(existing['autojoin'])} → {autojoin}")
                        else:
                            self.logger.debug(f"Syncing bookmark: {room_jid} (autojoin={autojoin})")
                    else:
                        self.logger.info(f"➕ NEW bookmark from server: {room_jid} (autojoin={autojoin})")

                # Get or create JID entry
                jid_row = db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
                if jid_row:
                    jid_id = jid_row['id']
                else:
                    cursor = db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                    jid_id = cursor.lastrowid

                # Insert or update bookmark
                # Only update name/nick if server provides non-empty values (preserve local values otherwise)
                db.execute("""
                    INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)
                    VALUES (?, ?, ?, ?, NULL, ?)
                    ON CONFLICT (account_id, jid_id) DO UPDATE SET
                        name = CASE WHEN excluded.name != ? THEN excluded.name ELSE bookmark.name END,
                        nick = CASE WHEN excluded.nick != '' THEN excluded.nick ELSE bookmark.nick END,
                        autojoin = excluded.autojoin
                """, (self.account_id, jid_id, name or room_jid, nick, 1 if autojoin else 0, room_jid))

            db.commit()

            # Detect removed bookmarks (in local DB but not on server)
            local_bookmarks = db.fetchall("""
                SELECT j.bare_jid FROM bookmark b
                JOIN jid j ON b.jid_id = j.id
                WHERE b.account_id = ?
            """, (self.account_id,))

            server_jids = {bm.get('jid') for bm in bookmarks if bm.get('jid')}
            local_jids = {row['bare_jid'] for row in local_bookmarks}
            removed_jids = local_jids - server_jids

            if removed_jids and self.logger:
                for jid in removed_jids:
                    self.logger.info(f"🗑️  Bookmark REMOVED from server: {jid}")
                # We don't delete from local DB - server is source of truth
                # But we could detect this as a phone "leave room" action

            # Emit roster_updated to refresh GUI
            self.signals['roster_updated'].emit(self.account_id)

            if self.logger:
                self.logger.info(f"✅ Bookmarks synced successfully ({len(bookmarks)} on server, {len(removed_jids)} removed)")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to sync bookmarks: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def on_muc_invite(self, room_jid: str, inviter_jid: str, reason: str, password: str):
        """
        Handle MUC invitation (XEP-0045).
        Emits signal for GUI to show invite dialog.

        Args:
            room_jid: MUC room JID
            inviter_jid: JID of person who sent the invite
            reason: Invitation reason (may be empty)
            password: Room password (may be None)
        """
        if self.logger:
            self.logger.info(f"MUC invite: {room_jid} from {inviter_jid}")

        # Emit signal for GUI to handle
        self.signals['muc_invite_received'].emit(
            self.account_id,
            room_jid,
            inviter_jid,
            reason or '',
            password or ''
        )

    async def on_room_config_changed(self, room_jid: str, room_name: str):
        """
        Handle room configuration change (status code 104/173/174).

        Refreshes cached room config and updates bookmark name in database.
        This is called automatically when the server sends status codes.

        Args:
            room_jid: The bare JID of the room
            room_name: The new room name from disco#info
        """
        if self.logger:
            self.logger.info(f"Room config changed (status 104/173/174): {room_jid} -> {room_name}")

        # Refresh room configuration cache (owner-only, may fail gracefully)
        try:
            await self.fetch_and_store_room_config(room_jid)
            if self.logger:
                self.logger.info(f"✓ Refreshed room config cache for {room_jid}")
        except Exception as e:
            if self.logger:
                self.logger.debug(f"Could not refresh room config for {room_jid}: {e}")

        # Emit roster_updated IMMEDIATELY after disco_cache update
        # This ensures encryption button updates even if bookmark update fails
        self.signals['roster_updated'].emit(self.account_id)

        # Update bookmark name in database
        try:
            db = get_db()

            # Get or create JID entry
            jid_row = db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if not jid_row:
                if self.logger:
                    self.logger.warning(f"Room {room_jid} not in jid table, creating entry")
                cursor = db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                jid_id = cursor.lastrowid
            else:
                jid_id = jid_row['id']

            # Update bookmark name
            db.execute("""
                UPDATE bookmark
                SET name = ?
                WHERE account_id = ? AND jid_id = ?
            """, (room_name, self.account_id, jid_id))

            db.commit()

            if self.logger:
                self.logger.info(f"Updated bookmark name for {room_jid} to '{room_name}'")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to update room name for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

    async def on_muc_join_error(self, room_jid: str, error_condition: str, error_text: str):
        """
        Handle MUC join errors and emit Qt signal for UI.

        Called by DrunkXMPP when joining a room fails due to server rejection.

        Args:
            room_jid: Room JID that failed to join
            error_condition: XMPP error condition (e.g., 'registration-required', 'forbidden')
            error_text: Human-readable error message from server
        """
        if self.logger:
            self.logger.warning(f"MUC join error for {room_jid}: {error_condition} - {error_text}")

        # Map XMPP error conditions to user-friendly messages
        error_map = {
            'registration-required': 'Membership required to join this room',
            'forbidden': 'You are banned from this room',
            'not-authorized': 'Password incorrect or authorization failed',
            'conflict': 'Nickname already in use',
            'service-unavailable': 'Room does not exist or is unavailable',
            'item-not-found': 'Room does not exist',
            'not-allowed': 'You are not allowed to join this room',
            'jid-malformed': 'Invalid room address',
        }

        # Get user-friendly message (fallback to generic message if not mapped)
        friendly_msg = error_map.get(error_condition, 'Failed to join room')

        # Build server details for second line (always include condition code)
        server_details = f"Server message: {error_condition}"
        if error_text:
            server_details += f": {error_text}"

        # Emit signal for GUI to display error dialog
        if 'muc_join_error' in self.signals:
            self.signals['muc_join_error'].emit(room_jid, friendly_msg, server_details)
        else:
            if self.logger:
                self.logger.warning("muc_join_error signal not registered - error not propagated to UI")

    # =========================================================================
    # MUC Service Layer API (for GUI abstraction)
    # =========================================================================

    def get_room_info(self, room_jid: str) -> Optional[RoomInfo]:
        """
        Get comprehensive room information from database, in-memory cache, and live roster.

        Args:
            room_jid: Room JID

        Returns:
            RoomInfo object or None if room not found
        """
        try:
            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if not jid_row:
                return None

            jid_id = jid_row['id']

            # Get room data from conversation + bookmark (or just bookmark if conversation doesn't exist yet)
            room_data = self.db.fetchone("""
                SELECT
                    b.name
                FROM bookmark b
                LEFT JOIN conversation c ON c.account_id = b.account_id AND c.jid_id = b.jid_id AND c.type = 1
                WHERE b.account_id = ? AND b.jid_id = ?
            """, (self.account_id, jid_id))

            if not room_data:
                return None

            room_name = room_data['name'] or room_jid

            # Read muc_nonanonymous and muc_membersonly from disco_cache (in-memory)
            # instead of database for always-fresh data
            disco_info = {}
            if self.client and hasattr(self.client, 'disco_cache'):
                disco_info = self.client.disco_cache.get(room_jid, {})

            nonanonymous = disco_info.get('muc_nonanonymous', False)
            membersonly = disco_info.get('muc_membersonly', False)

            # Get config from in-memory cache (if available)
            config = self.room_configs.get(room_jid, {})
            has_config = bool(config)

            persistent = config.get('persistent', False)
            public = config.get('public', False)
            moderated = config.get('moderated', False)
            password_protected = config.get('password_protected', False)
            description = config.get('description')
            max_users = config.get('max_users')
            allow_invites = config.get('allow_invites', True)
            enable_logging = config.get('enable_logging', False)
            whois = config.get('whois', 'moderators')

            # Get allow_subject_change from config if available (owner-only query)
            # Otherwise fall back to disco#info (available to all participants)
            allow_subject_change = config.get('allow_subject_change', None)
            if allow_subject_change is None and self.client:
                # Config not available - try disco#info
                if hasattr(self.client, 'disco_cache'):
                    disco_info = self.client.disco_cache.get(room_jid, {})
                    allow_subject_change = disco_info.get('allow_subject_change', False)
                else:
                    allow_subject_change = False

            # Get subject from live tracking (not from config - subject is dynamic)
            subject = None
            if self.client and hasattr(self.client, 'room_subjects'):
                subject = self.client.room_subjects.get(room_jid)

            # Override membersonly from config if available (config is source of truth)
            if has_config and 'membersonly' in config:
                membersonly = config['membersonly']

            # Get participant count from live roster
            participant_count = 0
            if self.client and room_jid in self.client.joined_rooms:
                try:
                    xep_0045 = self.client.plugin['xep_0045']
                    roster = xep_0045.get_roster(room_jid)
                    if roster:
                        participant_count = len(roster)
                except Exception:
                    pass  # Non-critical, just skip count

            # Build RoomInfo with in-memory config
            return RoomInfo(
                jid=room_jid,
                name=room_name,
                description=description,
                subject=subject,
                nonanonymous=nonanonymous,
                membersonly=membersonly,
                persistent=persistent,
                public=public,
                moderated=moderated,
                password_protected=password_protected,
                max_users=max_users,
                allow_invites=allow_invites,
                allow_subject_change=allow_subject_change,
                enable_logging=enable_logging,
                whois=whois,
                omemo_compatible=(nonanonymous and membersonly),
                participant_count=participant_count,
                config_fetched=1 if has_config else None  # Simplified: 1 if cached, None if not
            )

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get room info for {room_jid}: {e}")
            return None

    def get_room_settings(self, room_jid: str) -> Optional[RoomSettings]:
        """
        Get all room settings (conversation + bookmark + conversation_settings).

        Args:
            room_jid: Room JID

        Returns:
            RoomSettings object or None if room not found
        """
        try:
            # Get jid_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if not jid_row:
                return None

            jid_id = jid_row['id']

            # Get conversation settings
            conv_data = self.db.fetchone("""
                SELECT
                    id,
                    notification,
                    send_typing,
                    send_marker,
                    encryption
                FROM conversation
                WHERE account_id = ? AND jid_id = ? AND type = 1
            """, (self.account_id, jid_id))

            if not conv_data:
                return None

            conversation_id = conv_data['id']

            # Get bookmark autojoin
            bookmark_data = self.db.fetchone("""
                SELECT autojoin
                FROM bookmark
                WHERE account_id = ? AND jid_id = ?
            """, (self.account_id, jid_id))

            autojoin = bool(bookmark_data['autojoin']) if bookmark_data else False

            # Get conversation_settings
            local_alias = self.db.get_conversation_setting(conversation_id, 'local_alias', default='')
            history_limit = int(self.db.get_conversation_setting(conversation_id, 'history_limit', default='100'))

            return RoomSettings(
                room_jid=room_jid,
                notification=conv_data['notification'] or 1,
                send_typing=bool(conv_data['send_typing']),
                send_marker=bool(conv_data['send_marker']),
                encryption=conv_data['encryption'] or 0,
                autojoin=autojoin,
                local_alias=local_alias,
                history_limit=history_limit
            )

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get room settings for {room_jid}: {e}")
            return None

    async def update_room_settings(self, room_jid: str, **kwargs):
        """
        Update room settings in database and sync to server if needed.

        Args:
            room_jid: Room JID
            **kwargs: Settings to update (notification, send_typing, autojoin, etc.)

        Raises:
            RuntimeError: If room not found or update fails
        """
        try:
            # Get jid_id and conversation_id
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if not jid_row:
                raise RuntimeError(f"Room {room_jid} not found in database")

            jid_id = jid_row['id']
            conversation_id = self.db.get_or_create_conversation(self.account_id, jid_id, 1)

            # Update conversation table fields
            conv_updates = {}
            if 'notification' in kwargs:
                conv_updates['notification'] = int(kwargs['notification'])
            if 'send_typing' in kwargs:
                conv_updates['send_typing'] = 1 if kwargs['send_typing'] else 0
            if 'send_marker' in kwargs:
                conv_updates['send_marker'] = 1 if kwargs['send_marker'] else 0
            if 'encryption' in kwargs:
                conv_updates['encryption'] = int(kwargs['encryption'])

            if conv_updates:
                set_clause = ', '.join([f"{key} = ?" for key in conv_updates.keys()])
                values = list(conv_updates.values()) + [conversation_id]
                self.db.execute(f"""
                    UPDATE conversation
                    SET {set_clause}
                    WHERE id = ?
                """, values)

            # Update conversation_settings
            if 'local_alias' in kwargs:
                self.db.set_conversation_setting(conversation_id, 'local_alias', str(kwargs['local_alias']))
            if 'history_limit' in kwargs:
                self.db.set_conversation_setting(conversation_id, 'history_limit', str(kwargs['history_limit']))

            # Handle autojoin (requires bookmark sync)
            if 'autojoin' in kwargs:
                autojoin = bool(kwargs['autojoin'])

                # Get current bookmark or create with defaults
                bookmark = self.db.fetchone("""
                    SELECT name, nick, password
                    FROM bookmark
                    WHERE account_id = ? AND jid_id = ?
                """, (self.account_id, jid_id))

                if bookmark or autojoin:
                    # Determine values for bookmark
                    room_name = bookmark['name'] if (bookmark and bookmark['name']) else room_jid

                    # Get nick from bookmark or account default
                    if bookmark and bookmark['nick']:
                        nick = bookmark['nick']
                    else:
                        nick = (self.account_data.get('muc_nickname') or
                               self.account_data.get('nickname') or
                               self.account_data.get('bare_jid', '').split('@')[0] or
                               'User')

                    # Decode password if exists
                    password = None
                    if bookmark and bookmark['password']:
                        try:
                            password = base64.b64decode(bookmark['password']).decode()
                        except Exception as e:
                            if self.logger:
                                self.logger.warning(f"Failed to decode bookmark password: {e}")

                    # Update local DB
                    password_b64 = bookmark['password'] if bookmark else None
                    self.db.execute("""
                        INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)
                        VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (account_id, jid_id) DO UPDATE SET
                            autojoin = excluded.autojoin
                    """, (self.account_id, jid_id, room_name, nick, password_b64, 1 if autojoin else 0))

                    # Sync to server if connected
                    if self.client and hasattr(self.client, 'add_bookmark'):
                        try:
                            await self.client.add_bookmark(
                                jid=room_jid,
                                name=room_name,
                                nick=nick,
                                password=password,
                                autojoin=autojoin
                            )
                            if self.logger:
                                self.logger.info(f"Synced bookmark to server: {room_jid} (autojoin={autojoin})")
                        except Exception as e:
                            if self.logger:
                                self.logger.warning(f"Failed to sync bookmark to server: {e}")

            self.db.commit()

            # Refresh GUI roster to update star indicator
            self.signals['roster_updated'].emit(self.account_id)

            if self.logger:
                self.logger.info(f"Updated settings for room {room_jid}: {kwargs}")

        except Exception as e:
            self.db.execute("ROLLBACK")
            if self.logger:
                self.logger.error(f"Failed to update room settings for {room_jid}: {e}")
            raise

    def get_participants(self, room_jid: str) -> List[Participant]:
        """
        Get list of current participants from live slixmpp roster.

        Args:
            room_jid: Room JID

        Returns:
            List of Participant objects (empty if not joined or no roster yet)
        """
        participants = []

        try:
            if not self.client or room_jid not in self.client.joined_rooms:
                return participants

            # Query slixmpp's in-memory MUC roster (XEP-0045)
            xep_0045 = self.client.plugin['xep_0045']
            roster = xep_0045.get_roster(room_jid)

            if not roster:
                return participants

            # Convert roster dict to list of Participant objects
            from slixmpp.jid import JID
            room_jid_obj = JID(room_jid)

            for nick in roster:
                # Get real JID if available (depends on room configuration)
                real_jid_str = xep_0045.get_jid_property(room_jid_obj, nick, 'jid')
                bare_jid = str(real_jid_str).split('/')[0] if real_jid_str else None

                # Get role and affiliation from presence
                role = xep_0045.get_jid_property(room_jid_obj, nick, 'role') or 'participant'
                affiliation = xep_0045.get_jid_property(room_jid_obj, nick, 'affiliation') or 'none'

                participants.append(Participant(
                    nick=nick,
                    jid=bare_jid,
                    role=role,
                    affiliation=affiliation
                ))

            # Sort by nickname (case-insensitive)
            participants.sort(key=lambda p: p.nick.lower())

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get participants for {room_jid}: {e}")

        return participants

    def get_own_affiliation(self, room_jid: str) -> Optional[str]:
        """
        Get our own affiliation in a room.

        Args:
            room_jid: Room JID

        Returns:
            Affiliation string (owner, admin, member, none, outcast) or None if not joined
        """
        if not self.client:
            return None
        return self.client.own_affiliations.get(room_jid)

    def get_own_role(self, room_jid: str) -> Optional[str]:
        """
        Get our own role in a room.

        Args:
            room_jid: Room JID

        Returns:
            Role string (moderator, participant, visitor, none) or None if not joined
        """
        if not self.client:
            return None
        return self.client.own_roles.get(room_jid)

    def is_room_owner(self, room_jid: str) -> bool:
        """
        Check if we have owner affiliation in a room.

        Args:
            room_jid: Room JID

        Returns:
            True if we are an owner, False otherwise
        """
        return self.get_own_affiliation(room_jid) == 'owner'

    def is_room_admin(self, room_jid: str) -> bool:
        """
        Check if we have admin affiliation in a room.

        Args:
            room_jid: Room JID

        Returns:
            True if we are an admin (or owner), False otherwise
        """
        affiliation = self.get_own_affiliation(room_jid)
        return affiliation in ('owner', 'admin')

    def request_voice(self, room_jid: str) -> dict:
        """
        Request voice (participant role) in a moderated room.

        Use this when you are a visitor and want to be able to send messages.
        Moderators will receive the request and can approve/deny it.

        Implements throttling: only allows one request per hour per room.

        Args:
            room_jid: Room JID to request voice in

        Returns:
            dict with keys:
                - success (bool): Whether request was sent
                - message (str): User-friendly status message
                - cooldown_remaining (int): Seconds until next request allowed (0 if allowed now)

        Note:
            - Only works if you're currently a visitor in the room
            - Request is sent to room moderators
            - Throttled to prevent spam (1 hour cooldown)
        """
        import time

        # Check throttling (1 hour = 3600 seconds)
        COOLDOWN_SECONDS = 3600
        now = time.time()
        last_request = self._voice_request_timestamps.get(room_jid, 0)
        time_since_last = now - last_request

        if time_since_last < COOLDOWN_SECONDS:
            cooldown_remaining = int(COOLDOWN_SECONDS - time_since_last)
            minutes_remaining = cooldown_remaining // 60
            if self.logger:
                self.logger.warning(f"Voice request throttled for {room_jid}: {cooldown_remaining}s remaining")
            return {
                'success': False,
                'message': f"Please wait {minutes_remaining} minute(s) before requesting again.",
                'cooldown_remaining': cooldown_remaining
            }

        if not self.client:
            if self.logger:
                self.logger.error(f"Cannot request voice in {room_jid}: client not connected")
            return {
                'success': False,
                'message': "Not connected to server.",
                'cooldown_remaining': 0
            }

        xep_0045 = self.client.plugin.get('xep_0045', None)
        if not xep_0045:
            if self.logger:
                self.logger.error("XEP-0045 plugin not loaded")
            return {
                'success': False,
                'message': "MUC plugin not available.",
                'cooldown_remaining': 0
            }

        try:
            xep_0045.request_voice(room_jid, role='participant')
            # Record timestamp for throttling
            self._voice_request_timestamps[room_jid] = now
            if self.logger:
                self.logger.info(f"Requested voice in room: {room_jid}")
            return {
                'success': True,
                'message': "Voice request sent to moderators.",
                'cooldown_remaining': 0
            }
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to request voice in {room_jid}: {e}", exc_info=True)
            return {
                'success': False,
                'message': f"Request failed: {str(e)}",
                'cooldown_remaining': 0
            }

    def reset_voice_request_timer(self, room_jid: str) -> None:
        """
        Reset the voice request throttling timer for a room.

        Called when role changes (promoted or demoted) to allow immediate
        new request if needed.

        Args:
            room_jid: Room JID to reset timer for
        """
        if room_jid in self._voice_request_timestamps:
            del self._voice_request_timestamps[room_jid]
            if self.logger:
                self.logger.debug(f"Voice request timer reset for {room_jid}")

    def get_bookmark(self, room_jid: str) -> Optional[Bookmark]:
        """
        Get bookmark data for a room.

        Args:
            room_jid: Room JID

        Returns:
            Bookmark object or None if not found
        """
        try:
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if not jid_row:
                return None

            bookmark_data = self.db.fetchone("""
                SELECT name, nick, password, autojoin
                FROM bookmark
                WHERE account_id = ? AND jid_id = ?
            """, (self.account_id, jid_row['id']))

            if not bookmark_data:
                return None

            # Decode password if present
            password = None
            if bookmark_data['password']:
                try:
                    password = base64.b64decode(bookmark_data['password']).decode()
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to decode bookmark password: {e}")

            return Bookmark(
                room_jid=room_jid,
                name=bookmark_data['name'],
                nick=bookmark_data['nick'],
                password=password,
                autojoin=bool(bookmark_data['autojoin'])
            )

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to get bookmark for {room_jid}: {e}")
            return None

    async def request_membership(self, room_jid: str, nickname: str, reason: str = "") -> Dict[str, Any]:
        """
        Request membership in a members-only MUC room.

        Uses XEP-0077 in-band registration to request membership. The room
        may auto-approve or queue the request for admin approval.

        Args:
            room_jid: Room JID to request membership from
            nickname: Desired nickname for the room
            reason: Optional reason/message for room admin

        Returns:
            Dict with 'success' (bool) and 'error' (str or None)

        Example:
            result = await muc.request_membership(
                'room@conference.example.com',
                nickname='mynick',
                reason='I would like to join this group'
            )
            if result['success']:
                print("Membership requested successfully")
            else:
                print(f"Failed: {result['error']}")
        """
        if not self.client:
            return {
                'success': False,
                'error': 'Not connected'
            }

        try:
            # Call DrunkXMPP's wrapper
            result = await self.client.request_room_membership(room_jid, nickname, reason)

            if result['success']:
                if self.logger:
                    self.logger.info(f"Membership request sent successfully to {room_jid}")
            else:
                if self.logger:
                    self.logger.warning(f"Membership request failed for {room_jid}: {result.get('error')}")

            return result

        except Exception as e:
            if self.logger:
                self.logger.error(f"Error requesting membership for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
            return {
                'success': False,
                'error': f"Unexpected error: {str(e)}"
            }

    async def create_or_update_bookmark(
        self,
        room_jid: str,
        name: Optional[str] = None,
        nick: Optional[str] = None,
        password: Optional[str] = None,
        autojoin: bool = False
    ):
        """
        Create or update bookmark in local DB and sync to server.

        Args:
            room_jid: Room JID
            name: Room name (defaults to JID if not provided)
            nick: Nickname (defaults to account's muc_nickname)
            password: Room password (plain text, will be base64 encoded)
            autojoin: Whether to auto-join on connect

        Raises:
            RuntimeError: If update fails
        """
        try:
            # Get or create JID entry
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
            if jid_row:
                jid_id = jid_row['id']
            else:
                cursor = self.db.execute("INSERT INTO jid (bare_jid) VALUES (?)", (room_jid,))
                jid_id = cursor.lastrowid

            # Use provided values or defaults
            room_name = name or room_jid
            room_nick = nick or (
                self.account_data.get('muc_nickname') or
                self.account_data.get('nickname') or
                self.account_data.get('bare_jid', '').split('@')[0] or
                'User'
            )

            # Encode password if provided
            password_b64 = base64.b64encode(password.encode()).decode() if password else None

            # Insert or update in local DB
            self.db.execute("""
                INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (account_id, jid_id) DO UPDATE SET
                    name = excluded.name,
                    nick = excluded.nick,
                    password = excluded.password,
                    autojoin = excluded.autojoin
            """, (self.account_id, jid_id, room_name, room_nick, password_b64, 1 if autojoin else 0))

            self.db.commit()

            # Sync to server if connected
            if self.client and hasattr(self.client, 'add_bookmark'):
                try:
                    await self.client.add_bookmark(
                        jid=room_jid,
                        name=room_name,
                        nick=room_nick,
                        password=password,
                        autojoin=autojoin
                    )
                    if self.logger:
                        self.logger.info(f"Created/updated bookmark and synced to server: {room_jid}")
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to sync bookmark to server: {e}")

            # Refresh GUI
            self.signals['roster_updated'].emit(self.account_id)

        except Exception as e:
            self.db.execute("ROLLBACK")
            if self.logger:
                self.logger.error(f"Failed to create/update bookmark for {room_jid}: {e}")
            raise

    def invite_to_room(self, room_jid: str, invitee_jid: str, reason: str = ''):
        """
        Invite a user to a MUC room (XEP-0045 §7.8.2).

        Sends a mediated invitation through the room. Passwords are NOT included
        in invites - if the room is password-protected, communicate the password
        separately.

        Args:
            room_jid: Room JID to invite user to
            invitee_jid: Bare JID of user to invite
            reason: Optional invitation reason/message
        """
        if not self.client:
            if self.logger:
                self.logger.error(f"Cannot send invite - client not connected")
            return

        self.client.send_muc_invite(room_jid, invitee_jid, reason)
        if self.logger:
            self.logger.info(f"Invited {invitee_jid} to {room_jid}")

    async def destroy_room(self, room_jid: str, reason: str = ''):
        """
        Destroy a MUC room (owner only, XEP-0045 §10.9).

        Permanently deletes the room from the server. Performs full cleanup:
        1. Destroy room on server (kicks all participants, removes room)
        2. Remove bookmark from server (XEP-0402)
        3. Clean local database (bookmark, roster, conversation with messages)
        4. Emit roster_updated signal to refresh UI

        Args:
            room_jid: Room JID to destroy
            reason: Optional reason shown to participants before destruction

        Raises:
            RuntimeError: If not connected or if user is not room owner
            Exception: If server destroy operation fails
        """
        if not self.client:
            raise RuntimeError("Not connected")

        if not self.is_room_owner(room_jid):
            raise RuntimeError("Only room owners can destroy rooms")

        if self.logger:
            self.logger.info(f"Destroying room {room_jid}" + (f" with reason: {reason}" if reason else ""))

        # Destroy room on server (raises exception on error)
        await self.client.destroy_room(room_jid, reason)

        # Remove bookmark from server (XEP-0402)
        if hasattr(self.client, 'remove_bookmark'):
            try:
                await self.client.remove_bookmark(room_jid)
                if self.logger:
                    self.logger.debug(f"Removed bookmark from server: {room_jid}")
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"Failed to remove bookmark from server: {e}")

        # Clean local database
        jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (room_jid,))
        if jid_row:
            jid_id = jid_row['id']

            # Delete bookmark
            self.db.execute("DELETE FROM bookmark WHERE account_id = ? AND jid_id = ?",
                          (self.account_id, jid_id))

            # Delete roster entry (if present)
            self.db.execute("DELETE FROM roster WHERE account_id = ? AND jid_id = ?",
                          (self.account_id, jid_id))

            # Delete conversation (CASCADE will delete all content_items/messages)
            self.db.execute("DELETE FROM conversation WHERE account_id = ? AND jid_id = ? AND type = 1",
                          (self.account_id, jid_id))

            self.db.commit()

            if self.logger:
                self.logger.debug(f"Cleaned local database for destroyed room: {room_jid}")

        # Refresh UI
        self.signals['roster_updated'].emit(self.account_id)

        if self.logger:
            self.logger.info(f"Room destroyed and cleaned up: {room_jid}")
