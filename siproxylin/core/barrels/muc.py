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
from typing import Optional
from datetime import datetime, timezone

from ...db.database import get_db


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

    async def add_and_join_room(self, room_jid: str, nick: str, password: str = None):
        """
        Add a room to the client's configuration and join it.

        Args:
            room_jid: Room JID
            nick: Nickname to use
            password: Room password (optional)
        """
        if not self.client:
            raise RuntimeError("Not connected")

        # Check if room is already joined (to avoid duplicate MAM retrieval)
        already_joined = room_jid in self.client.rooms

        if self.logger:
            if already_joined:
                self.logger.debug(f"Room already joined: {room_jid}, skipping MAM retrieval")
            else:
                self.logger.info(f"Adding and joining room: {room_jid} as {nick}")

        # Start database transaction for atomicity
        db = get_db()
        try:
            db.execute("BEGIN")

            # Add to client's rooms dictionary
            self.client.rooms[room_jid] = {
                'nick': nick,
                'password': password
            }

            # Join the room (no-op if already joined in slixmpp)
            await self.client.join_room(room_jid, nick, password)

            # Query room features ONCE and reuse result
            disco_info = None
            room_features = None
            if not already_joined:
                try:
                    # Get room features (includes disco#info)
                    room_features = await self.client.get_room_features(room_jid)

                    # Update room OMEMO compatibility flags
                    await self._update_room_features_from_dict(room_jid, room_features)

                    # Extract room name if available
                    room_name = room_features.get('name')
                    if room_name:
                        await self._update_bookmark_name(room_jid, room_name)

                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to query room features for {room_jid}: {e}")

            # Mark room for MAM retrieval after self-presence is received
            # MAM will be retrieved by on_muc_joined callback to ensure OMEMO sessions are ready
            if not already_joined:
                self._pending_mam_rooms.add(room_jid)
                if self.logger:
                    self.logger.debug(f"Room {room_jid} marked for MAM retrieval after join completes")

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

    async def on_muc_joined(self, room_jid: str, nick: str):
        """
        Callback fired when MUC room join is complete (self-presence received).
        This is called by DrunkXMPP after status code 110 presence is received,
        ensuring OMEMO device sessions are established before MAM retrieval.

        Args:
            room_jid: Room JID
            nick: Our nickname in the room
        """
        if self.logger:
            self.logger.debug(f"MUC join complete for {room_jid} as {nick}, checking for pending MAM")

        # Check if this room has pending MAM retrieval
        if room_jid in self._pending_mam_rooms:
            self._pending_mam_rooms.remove(room_jid)
            if self.logger:
                self.logger.info(f"Retrieving MAM for {room_jid} (after join complete)")

            try:
                await self._retrieve_muc_history(room_jid)
            except Exception as e:
                if self.logger:
                    self.logger.error(f"Failed to retrieve MAM for {room_jid}: {e}")

    async def _retrieve_muc_history(self, room_jid: str, max_messages: int = 500):
        """
        Retrieve MAM history for a MUC room and store in database.

        Args:
            room_jid: Room JID
            max_messages: Maximum number of messages to retrieve
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
            # Use 1-hour overlap for safety (handles clock skew, delayed messages with old timestamps, etc.)
            start_time = None
            latest_msg = self.db.fetchone("""
                SELECT MAX(time) as latest_time
                FROM message
                WHERE counterpart_id = (SELECT id FROM jid WHERE bare_jid = ?)
            """, (room_jid,))

            if latest_msg and latest_msg['latest_time']:
                # Start from 1 hour before latest message for safe overlap
                # IMPORTANT: Must use UTC timezone for MAM compliance (XEP-0313)
                start_time = datetime.fromtimestamp(latest_msg['latest_time'] - 3600, tz=timezone.utc)
                if self.logger:
                    self.logger.debug(f"Querying MAM since {start_time} (1h overlap from latest msg)")
            else:
                if self.logger:
                    self.logger.debug(f"No existing messages, querying last {max_messages} from MAM")

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
                    if self.logger:
                        self.logger.debug(f"MAM message already exists (archive_id={archive_id}), skipping duplicate")
                    continue

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
        Called on session start.
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
                # Add to client's rooms dictionary first (so duplicate join detection works)
                self.client.rooms[room_jid] = {
                    'nick': nick,
                    'password': password
                }

                await self.client.join_room(room_jid, nick, password)

                # Mark room for MAM retrieval after self-presence is received
                # MAM will be retrieved by on_muc_joined callback to ensure OMEMO sessions are ready
                self._pending_mam_rooms.add(room_jid)
                if self.logger:
                    self.logger.debug(f"Room {room_jid} marked for MAM retrieval after join completes")

                # Query room features for OMEMO compatibility (XEP-0384)
                try:
                    room_features = await self.client.get_room_features(room_jid)
                    await self._update_room_features_from_dict(room_jid, room_features)

                    # Update room name if available
                    room_name = room_features.get('name')
                    if room_name:
                        await self._update_bookmark_name(room_jid, room_name)
                except Exception as e:
                    if self.logger:
                        self.logger.warning(f"Failed to query room features for {room_jid}: {e}")

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
        Handle room configuration change (status code 104).
        Updates the bookmark name in the database.

        Args:
            room_jid: The bare JID of the room
            room_name: The new room name from disco#info
        """
        if self.logger:
            self.logger.info(f"Room config changed: {room_jid} -> {room_name}")

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

            # Emit roster_updated to refresh GUI
            self.signals['roster_updated'].emit(self.account_id)

            if self.logger:
                self.logger.info(f"Updated bookmark name for {room_jid} to '{room_name}'")

        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to update room name for {room_jid}: {e}")
                import traceback
                self.logger.error(traceback.format_exc())
