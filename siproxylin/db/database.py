"""
Database manager for DRUNK-XMPP-GUI.

Handles SQLite database initialization, schema management, and migrations.
"""

import sqlite3
import logging
import os
import fcntl
from pathlib import Path
from typing import Optional, Any, List, Dict
from contextlib import contextmanager

from ..utils.paths import get_paths


logger = logging.getLogger('siproxylin.database')


class Database:
    """
    Database manager for DRUNK-XMPP-GUI.
    Handles schema initialization, migrations, and query execution.
    """

    SCHEMA_VERSION = 16  # Current schema version (v16 = MAM catchup tracking)

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize database manager.

        Args:
            db_path: Path to database file (default: uses paths.database_path)
        """
        if db_path is None:
            paths = get_paths()
            db_path = paths.database_path

        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
        self._lock_file = None
        self._lock_fd = None

        # Ensure parent directory exists with secure permissions
        self.db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

        logger.info(f"Database manager initialized (path: {self.db_path})")

    def acquire_lock(self):
        """
        Acquire exclusive lock on database file.

        Raises:
            RuntimeError: If another instance is already running
        """
        lock_path = str(self.db_path) + '.lock'

        try:
            # Open lock file (create if doesn't exist)
            self._lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)

            # Try to acquire exclusive lock (non-blocking)
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Write PID to lock file
            os.ftruncate(self._lock_fd, 0)
            os.write(self._lock_fd, str(os.getpid()).encode())

            self._lock_file = lock_path
            logger.info(f"Database lock acquired: {lock_path}")

        except BlockingIOError:
            # Lock is held by another process
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None

            # Try to read PID from lock file
            try:
                with open(lock_path, 'r') as f:
                    pid = f.read().strip()
                error_msg = f"Another instance is already running (PID: {pid})"
            except:
                error_msg = "Another instance is already running"

            logger.error(error_msg)
            raise RuntimeError(error_msg)

        except Exception as e:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            logger.error(f"Failed to acquire database lock: {e}")
            raise

    def release_lock(self):
        """Release database lock."""
        if self._lock_fd is not None:
            try:
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
                self._lock_fd = None

                # Remove lock file
                if self._lock_file and os.path.exists(self._lock_file):
                    os.remove(self._lock_file)

                logger.info("Database lock released")
            except Exception as e:
                logger.error(f"Error releasing lock: {e}")

    @property
    def connection(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._connection is None:
            self._connection = sqlite3.connect(
                self.db_path,
                check_same_thread=False  # Allow multi-threaded access
            )
            # Enable foreign keys
            self._connection.execute("PRAGMA foreign_keys = ON")
            # Use Row factory for dict-like access
            self._connection.row_factory = sqlite3.Row

        return self._connection

    def close(self):
        """Close database connection and release lock."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            logger.debug("Database connection closed")

        # Release lock
        self.release_lock()

    @contextmanager
    def transaction(self):
        """
        Context manager for database transactions.

        Usage:
            with db.transaction():
                db.execute("INSERT INTO ...")
                db.execute("UPDATE ...")
        """
        conn = self.connection
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction rolled back: {e}")
            raise

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """
        Execute a SQL query.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            Cursor object
        """
        return self.connection.execute(query, params)

    def fetchone(self, query: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        """
        Execute query and fetch one row.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            Row or None
        """
        cursor = self.execute(query, params)
        return cursor.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """
        Execute query and fetch all rows.

        Args:
            query: SQL query
            params: Query parameters

        Returns:
            List of rows
        """
        cursor = self.execute(query, params)
        return cursor.fetchall()

    def commit(self):
        """Commit current transaction."""
        self.connection.commit()

    # =========================================================================
    # Schema Management
    # =========================================================================

    def initialize(self):
        """
        Initialize database.
        Creates tables if they don't exist, or runs migrations if needed.
        """
        if not self._tables_exist():
            logger.info("Database is empty, applying initial schema...")
            self._apply_schema()

            # After applying base schema, check if migrations are needed
            current_version = self._get_schema_version()
            if current_version < self.SCHEMA_VERSION:
                logger.info(f"Running migrations: v{current_version} → v{self.SCHEMA_VERSION}")
                self._migrate(current_version, self.SCHEMA_VERSION)
        else:
            current_version = self._get_schema_version()
            if current_version < self.SCHEMA_VERSION:
                logger.info(f"Schema upgrade needed: v{current_version} → v{self.SCHEMA_VERSION}")
                self._migrate(current_version, self.SCHEMA_VERSION)
            else:
                logger.info(f"Database schema up to date (v{current_version})")

        # Run maintenance tasks after initialization/migrations
        self.run_maintenance()

    def _tables_exist(self) -> bool:
        """Check if database tables exist."""
        result = self.fetchone(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='_meta'"
        )
        return result is not None

    def _get_schema_version(self) -> int:
        """Get current schema version from database."""
        try:
            result = self.fetchone(
                "SELECT int_val FROM _meta WHERE name = 'schema_version'"
            )
            if result:
                return result['int_val']
        except sqlite3.OperationalError:
            pass
        return 0

    def _apply_schema(self):
        """Apply initial schema from schema.sql."""
        schema_file = Path(__file__).parent / 'schema.sql'

        if not schema_file.exists():
            raise FileNotFoundError(f"Schema file not found: {schema_file}")

        logger.info(f"Applying schema from {schema_file}")

        with open(schema_file, 'r', encoding='utf-8') as f:
            schema_sql = f.read()

        with self.transaction():
            self.connection.executescript(schema_sql)

        logger.info("Schema applied successfully")

        # Verify schema version
        version = self._get_schema_version()
        if version != self.SCHEMA_VERSION:
            logger.warning(
                f"Schema version mismatch: expected {self.SCHEMA_VERSION}, got {version}"
            )

    def _migrate(self, from_version: int, to_version: int):
        """
        Migrate database schema from one version to another.

        Args:
            from_version: Current schema version
            to_version: Target schema version
        """
        migrations_dir = Path(__file__).parent / 'migrations'

        for version in range(from_version, to_version):
            next_version = version + 1
            migration_file = migrations_dir / f'v{version}_to_v{next_version}.sql'

            if not migration_file.exists():
                raise FileNotFoundError(f"Migration file not found: {migration_file}")

            logger.info(f"Applying migration: v{version} → v{next_version}")

            with open(migration_file, 'r', encoding='utf-8') as f:
                migration_sql = f.read()

            with self.transaction():
                self.connection.executescript(migration_sql)

            logger.info(f"Migration v{version} → v{next_version} complete")

        # Update schema version
        self.execute(
            "UPDATE _meta SET int_val = ? WHERE name = 'schema_version'",
            (to_version,)
        )
        self.commit()

        logger.info(f"Database migrated to v{to_version}")

    def run_maintenance(self):
        """
        Run database maintenance tasks on app startup.

        Tasks:
        - Clean up old recent_emojis (keep only 10 most recent unique)
        - Future: VACUUM, cleanup old messages, etc.
        """
        try:
            # Clean up recent_emojis table - keep only 10 most recent unique
            self._cleanup_recent_emojis()

            logger.debug("Database maintenance completed")
        except Exception as e:
            logger.error(f"Database maintenance failed: {e}", exc_info=True)

    def _cleanup_recent_emojis(self):
        """Delete emojis older than the 10 most recent unique ones."""
        try:
            # Check if table exists
            table_exists = self.fetchone(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='recent_emojis'"
            )
            if not table_exists:
                return

            # Delete all except the 10 most recent (by used_at timestamp)
            cursor = self.execute("""
                DELETE FROM recent_emojis
                WHERE id NOT IN (
                    SELECT id FROM recent_emojis
                    ORDER BY used_at DESC
                    LIMIT 10
                )
            """)

            deleted_count = cursor.rowcount
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old emoji(s) from recent_emojis")

            self.commit()

        except Exception as e:
            logger.warning(f"Failed to cleanup recent_emojis: {e}")

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def get_setting(self, key: str, default: Any = None) -> Any:
        """
        Get global setting value.

        Args:
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        result = self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        if result:
            return result['value']
        return default

    def set_setting(self, key: str, value: Any):
        """
        Set global setting value.

        Args:
            key: Setting key
            value: Setting value
        """
        self.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, str(value))
        )
        self.commit()

    def get_account_setting(self, account_id: int, key: str, default: Any = None) -> Any:
        """
        Get account-specific setting.

        Args:
            account_id: Account ID
            key: Setting key
            default: Default value if not found

        Returns:
            Setting value or default
        """
        result = self.fetchone(
            "SELECT value FROM account_settings WHERE account_id = ? AND key = ?",
            (account_id, key)
        )
        if result:
            return result['value']
        return default

    def set_account_setting(self, account_id: int, key: str, value: Any):
        """
        Set account-specific setting.

        Args:
            account_id: Account ID
            key: Setting key
            value: Setting value
        """
        self.execute(
            """
            INSERT OR REPLACE INTO account_settings (account_id, key, value)
            VALUES (?, ?, ?)
            """,
            (account_id, key, str(value))
        )
        self.commit()

    def get_or_create_jid(self, bare_jid: str) -> int:
        """
        Get or create a JID entry, return its ID.

        Args:
            bare_jid: Bare JID (e.g., user@example.com)

        Returns:
            JID ID
        """
        # Try to get existing
        result = self.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (bare_jid,))
        if result:
            return result['id']

        # Create new
        cursor = self.execute("INSERT INTO jid (bare_jid) VALUES (?)", (bare_jid,))
        self.commit()
        return cursor.lastrowid

    # =========================================================================
    # Message Retry Logic (Phase 4)
    # =========================================================================

    def get_pending_messages(self, account_id: int) -> List[sqlite3.Row]:
        """
        Get all pending messages for retry (marked=0, direction=1).

        Args:
            account_id: Account ID

        Returns:
            List of pending message rows with JID and display name
        """
        query = """
            SELECT
                m.*,
                j.bare_jid as counterpart_jid,
                COALESCE(r.name, j.bare_jid) as display_name
            FROM message m
            JOIN jid j ON m.counterpart_id = j.id
            LEFT JOIN roster r ON (r.account_id = m.account_id AND r.jid_id = m.counterpart_id)
            WHERE m.account_id = ?
              AND m.direction = 1
              AND m.marked = 0
            ORDER BY m.time ASC
        """
        return self.fetchall(query, (account_id,))

    def initialize_retry_tracking(self, message_id: int):
        """
        Initialize retry tracking fields on first retry attempt.

        Args:
            message_id: Message ID
        """
        import time
        now = int(time.time())
        self.execute("""
            UPDATE message
            SET first_retry_attempt = ?,
                last_retry_attempt = ?
            WHERE id = ?
        """, (now, now, message_id))
        self.commit()

    def increment_retry_count(self, message_id: int):
        """
        Increment retry count and update last attempt timestamp.

        Args:
            message_id: Message ID
        """
        import time
        now = int(time.time())
        self.execute("""
            UPDATE message
            SET retry_count = retry_count + 1,
                last_retry_attempt = ?
            WHERE id = ?
        """, (now, message_id))
        self.commit()

    def mark_message_delivered(self, message_id: int):
        """
        Mark message as delivered (marked=1).

        Args:
            message_id: Message ID
        """
        self.execute("UPDATE message SET marked = 1 WHERE id = ?", (message_id,))
        self.commit()
        logger.debug(f"Message {message_id} marked as delivered")

    def mark_message_delivered_by_origin_id(self, origin_id: str, account_id: int):
        """
        Mark message as delivered using origin_id (for server ACK callback).

        Args:
            origin_id: Origin ID from XEP-0198 server ACK
            account_id: Account ID
        """
        self.execute("""
            UPDATE message
            SET marked = 1
            WHERE origin_id = ? AND account_id = ? AND direction = 1
        """, (origin_id, account_id))
        self.commit()
        logger.debug(f"Message with origin_id {origin_id} marked as delivered")

    def mark_message_discarded(self, message_id: int):
        """
        Mark message as discarded by user (marked=8, won't retry).

        Args:
            message_id: Message ID
        """
        self.execute("UPDATE message SET marked = 8 WHERE id = ?", (message_id,))
        self.commit()
        logger.info(f"Message {message_id} marked as discarded")

    def update_message_origin_id(self, message_id: int, new_origin_id: str):
        """
        Update origin_id after resending message.

        Args:
            message_id: Message ID
            new_origin_id: New origin ID from resend
        """
        self.execute("""
            UPDATE message SET origin_id = ? WHERE id = ?
        """, (new_origin_id, message_id))
        self.commit()
        logger.debug(f"Message {message_id} origin_id updated to {new_origin_id}")

    # =========================================================================
    # Content Item & Conversation Management (for displayed markers)
    # =========================================================================

    def get_or_create_conversation(self, account_id: int, jid_id: int, conv_type: int) -> int:
        """
        Get or create conversation for account + jid.

        Args:
            account_id: Account ID
            jid_id: JID ID
            conv_type: Conversation type (0=chat, 1=groupchat/MUC)

        Returns:
            conversation.id
        """
        # Try to get existing conversation
        row = self.fetchone("""
            SELECT id FROM conversation
            WHERE account_id = ? AND jid_id = ?
        """, (account_id, jid_id))

        if row:
            return row['id']

        # Create new conversation
        cursor = self.execute("""
            INSERT INTO conversation (
                account_id, jid_id, type, active, encryption,
                read_up_to_item, send_typing, send_marker, notification
            ) VALUES (?, ?, ?, 1, 0, -1, 1, 1, 1)
        """, (account_id, jid_id, conv_type))
        self.commit()

        conversation_id = cursor.lastrowid
        logger.debug(f"Created conversation {conversation_id} for account {account_id}, jid_id {jid_id}")
        return conversation_id

    def insert_content_item_for_message(self, conversation_id: int, message_id: int,
                                       time: int, local_time: int) -> int:
        """
        Insert content_item entry for a message.

        Args:
            conversation_id: Conversation ID
            message_id: Message ID (foreign_id)
            time: Server timestamp
            local_time: Local timestamp

        Returns:
            content_item.id
        """
        cursor = self.execute("""
            INSERT INTO content_item (
                conversation_id, time, local_time, content_type, foreign_id, hide
            ) VALUES (?, ?, ?, 0, ?, 0)
        """, (conversation_id, time, local_time, message_id))

        content_item_id = cursor.lastrowid
        logger.debug(f"Created content_item {content_item_id} for message {message_id}")
        return content_item_id

    def insert_file_transfer_atomic(self, account_id: int, counterpart_id: int,
                                     conversation_id: int, direction: int,
                                     time: int, local_time: int,
                                     file_name: str, path: str,
                                     mime_type: str, size: int, state: int,
                                     encryption: int, provider: int, is_carbon: int,
                                     url: Optional[str] = None,
                                     message_id: Optional[str] = None,
                                     origin_id: Optional[str] = None,
                                     stanza_id: Optional[str] = None):
        """
        Atomically insert file_transfer + content_item with deduplication.

        Checks for duplicates using message IDs (priority: stanza_id → origin_id → message_id)
        to prevent MAM from creating duplicate file records on every restart.

        Args:
            account_id: Account ID
            counterpart_id: Counterpart JID ID
            conversation_id: Conversation ID (for content_item)
            direction: 0=received, 1=sent
            time: Server timestamp
            local_time: Local timestamp
            file_name: Original filename
            path: Local filesystem path
            mime_type: MIME type
            size: File size in bytes
            state: 0=pending, 1=transferring, 2=complete, 3=failed
            encryption: 0=plain, 1=OMEMO
            provider: 0=HTTP Upload
            is_carbon: 0=regular, 1=carbon from another device
            url: HTTP URL or aesgcm:// URL (optional, for received files)
            message_id: For deduplication
            origin_id: For deduplication
            stanza_id: For deduplication

        Returns:
            tuple: (file_transfer_id, content_item_id) or (None, None) if duplicate
        """
        with self.transaction():
            # Check for duplicates (XEP-0359 priority: stanza_id → origin_id → message_id)
            # IMPORTANT: Check BOTH file_transfer AND message tables to prevent duplicates
            # (same stanza might be stored as message OR file depending on OMEMO decryption state)
            if stanza_id:
                # Check file_transfer table
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND stanza_id = ?
                    LIMIT 1
                """, (account_id, stanza_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): stanza_id={stanza_id}, origin_id={origin_id}, message_id={message_id}")
                    return (None, None)

                # Check message table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND stanza_id = ?
                    LIMIT 1
                """, (account_id, stanza_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): stanza_id={stanza_id}, origin_id={origin_id}, message_id={message_id}")
                    return (None, None)

            if origin_id:
                # Check file_transfer table
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND origin_id = ?
                    LIMIT 1
                """, (account_id, origin_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): origin_id={origin_id}, stanza_id={stanza_id}, message_id={message_id}")
                    return (None, None)

                # Check message table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND origin_id = ?
                    LIMIT 1
                """, (account_id, origin_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): origin_id={origin_id}, stanza_id={stanza_id}, message_id={message_id}")
                    return (None, None)

            if message_id:
                # Check file_transfer table
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND message_id = ?
                    LIMIT 1
                """, (account_id, message_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): message_id={message_id}, origin_id={origin_id}, stanza_id={stanza_id}")
                    return (None, None)

                # Check message table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND message_id = ?
                    LIMIT 1
                """, (account_id, message_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): message_id={message_id}, origin_id={origin_id}, stanza_id={stanza_id}")
                    return (None, None)

            # Insert file_transfer record
            cursor = self.execute("""
                INSERT INTO file_transfer (
                    account_id, counterpart_id, direction, time, local_time,
                    file_name, path, url, mime_type, size, state, encryption, provider, is_carbon,
                    message_id, origin_id, stanza_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_id, counterpart_id, direction, time, local_time,
                file_name, path, url, mime_type, size, state, encryption, provider, is_carbon,
                message_id, origin_id, stanza_id
            ))
            file_transfer_id = cursor.lastrowid

            # Insert content_item (content_type=2 for file_transfer)
            cursor = self.execute("""
                INSERT INTO content_item (
                    conversation_id, time, local_time, content_type, foreign_id
                ) VALUES (?, ?, ?, 2, ?)
            """, (conversation_id, time, local_time, file_transfer_id))
            content_item_id = cursor.lastrowid

            logger.debug(f"Created file_transfer {file_transfer_id} and content_item {content_item_id}")
            return (file_transfer_id, content_item_id)

    def insert_message_atomic(self, account_id: int, counterpart_id: int,
                               conversation_id: int, direction: int, msg_type: int,
                               time: int, local_time: int,
                               body: str, encryption: int, marked: int, is_carbon: int,
                               message_id: Optional[str] = None,
                               origin_id: Optional[str] = None,
                               stanza_id: Optional[str] = None,
                               counterpart_resource: Optional[str] = None,
                               reply_to_id: Optional[str] = None,
                               reply_to_jid: Optional[str] = None):
        """
        Atomically insert message + content_item with deduplication.

        Checks for duplicates using message IDs (priority: stanza_id → origin_id → message_id)
        to prevent MAM/carbons from creating duplicate messages on every restart.

        Args:
            account_id: Account ID
            counterpart_id: Counterpart JID ID
            conversation_id: Conversation ID (for content_item)
            direction: 0=received, 1=sent
            msg_type: 0=chat, 1=groupchat/MUC
            time: Server timestamp
            local_time: Local timestamp
            body: Message text
            encryption: 0=plain, 1=OMEMO
            marked: 0=pending, 1=sent, 2=delivered, 7=displayed
            is_carbon: 0=regular, 1=carbon from another device
            message_id: Message ID from 'id' attribute (for deduplication)
            origin_id: Origin ID (XEP-0359, for deduplication)
            stanza_id: Server-assigned stanza-id (XEP-0359, for deduplication)
            counterpart_resource: MUC nickname (optional, only for groupchat)
            reply_to_id: Quoted message ID (optional, for XEP-0461 replies)
            reply_to_jid: Quoted message sender JID (optional, for XEP-0461 replies)

        Returns:
            tuple: (message_id, content_item_id) or (None, None) if duplicate
        """
        with self.transaction():
            # Check for duplicates (XEP-0359 priority: stanza_id → origin_id → message_id)
            # IMPORTANT: Check BOTH message AND file_transfer tables to prevent OMEMO forward secrecy duplicates
            # (encrypted file received live, then MAM sends same stanza but can't decrypt → becomes "Failed to decrypt" text)
            if stanza_id:
                # Check message table
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND stanza_id = ?
                    LIMIT 1
                """, (account_id, stanza_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): stanza_id={stanza_id}, origin_id={origin_id}, message_id={message_id}")
                    return (None, None)

                # Check file_transfer table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND stanza_id = ?
                    LIMIT 1
                """, (account_id, stanza_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): stanza_id={stanza_id}, origin_id={origin_id}, message_id={message_id}")
                    return (None, None)

            if origin_id:
                # Check message table
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND origin_id = ?
                    LIMIT 1
                """, (account_id, origin_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): origin_id={origin_id}, stanza_id={stanza_id}, message_id={message_id}")
                    return (None, None)

                # Check file_transfer table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND origin_id = ?
                    LIMIT 1
                """, (account_id, origin_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): origin_id={origin_id}, stanza_id={stanza_id}, message_id={message_id}")
                    return (None, None)

            if message_id:
                # Check message table
                existing = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND message_id = ?
                    LIMIT 1
                """, (account_id, message_id))
                if existing:
                    logger.debug(f"Caught duplicate (message table): message_id={message_id}, origin_id={origin_id}, stanza_id={stanza_id}")
                    return (None, None)

                # Check file_transfer table (cross-table dedup for OMEMO forward secrecy cases)
                existing = self.fetchone("""
                    SELECT id FROM file_transfer
                    WHERE account_id = ? AND message_id = ?
                    LIMIT 1
                """, (account_id, message_id))
                if existing:
                    logger.debug(f"Caught duplicate (file_transfer table): message_id={message_id}, origin_id={origin_id}, stanza_id={stanza_id}")
                    return (None, None)

            # Insert message record
            cursor = self.execute("""
                INSERT INTO message (
                    account_id, counterpart_id, counterpart_resource, direction, type, time, local_time,
                    body, encryption, marked, message_id, origin_id, stanza_id, is_carbon
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_id, counterpart_id, counterpart_resource, direction, msg_type, time, local_time,
                body, encryption, marked, message_id, origin_id, stanza_id, is_carbon
            ))
            db_message_id = cursor.lastrowid

            # Store reply metadata if this is a reply (XEP-0461)
            if reply_to_id:
                # Try to find the quoted message in our database
                quoted_msg = self.fetchone("""
                    SELECT id FROM message
                    WHERE account_id = ? AND (message_id = ? OR origin_id = ? OR stanza_id = ?)
                """, (account_id, reply_to_id, reply_to_id, reply_to_id))

                quoted_message_id = quoted_msg['id'] if quoted_msg else None

                # Insert reply record
                self.execute("""
                    INSERT INTO reply (message_id, quoted_message_id, quoted_message_stanza_id, quoted_message_from)
                    VALUES (?, ?, ?, ?)
                """, (db_message_id, quoted_message_id, reply_to_id, reply_to_jid))

            # Insert content_item (content_type=0 for message)
            cursor = self.execute("""
                INSERT INTO content_item (
                    conversation_id, time, local_time, content_type, foreign_id
                ) VALUES (?, ?, ?, 0, ?)
            """, (conversation_id, time, local_time, db_message_id))
            content_item_id = cursor.lastrowid

            logger.debug(f"Created message {db_message_id} and content_item {content_item_id}")
            return (db_message_id, content_item_id)

    def insert_call(self, account_id: int, counterpart_id: int, conversation_id: int,
                    direction: int, time: int, local_time: int, end_time: Optional[int],
                    encryption: int, state: int, call_type: int,
                    counterpart_resource: Optional[str] = None,
                    our_resource: Optional[str] = None):
        """
        Insert call record + content_item atomically.

        Args:
            account_id: Account ID
            counterpart_id: Counterpart JID ID
            conversation_id: Conversation ID (for content_item)
            direction: 0=incoming, 1=outgoing
            time: Call start timestamp
            local_time: Local timestamp
            end_time: Call end timestamp (None if ongoing)
            encryption: 0=plain, 1=DTLS-SRTP
            state: Call state (see CallState enum)
            call_type: 0=audio, 1=video, 2=screen_share
            counterpart_resource: Counterpart resource (optional)
            our_resource: Our resource (optional)

        Returns:
            tuple: (call_id, content_item_id)
        """
        with self.transaction():
            # Insert call record
            cursor = self.execute("""
                INSERT INTO call (
                    account_id, counterpart_id, counterpart_resource, our_resource,
                    direction, time, local_time, end_time, encryption, state, type
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                account_id, counterpart_id, counterpart_resource, our_resource,
                direction, time, local_time, end_time, encryption, state, call_type
            ))
            call_id = cursor.lastrowid

            # Insert content_item (content_type=3 for call)
            cursor = self.execute("""
                INSERT INTO content_item (
                    conversation_id, time, local_time, content_type, foreign_id
                ) VALUES (?, ?, ?, 3, ?)
            """, (conversation_id, time, local_time, call_id))
            content_item_id = cursor.lastrowid

            logger.debug(f"Created call {call_id} and content_item {content_item_id}")
            return (call_id, content_item_id)

    def update_call_state(self, call_id: int, state: int, end_time: Optional[int] = None):
        """
        Update call state and optionally end_time.

        Args:
            call_id: Call ID
            state: New state (see CallState enum)
            end_time: Call end timestamp (optional)
        """
        if end_time is not None:
            self.execute("""
                UPDATE call
                SET state = ?, end_time = ?
                WHERE id = ?
            """, (state, end_time, call_id))
        else:
            self.execute("""
                UPDATE call
                SET state = ?
                WHERE id = ?
            """, (state, call_id))
        self.commit()
        logger.debug(f"Updated call {call_id} state to {state}")

    def update_conversation_read_up_to(self, conversation_id: int, content_item_id: int):
        """
        Update conversation.read_up_to_item after sending displayed marker.

        Args:
            conversation_id: Conversation ID
            content_item_id: content_item.id of last displayed message
        """
        self.execute("""
            UPDATE conversation
            SET read_up_to_item = ?
            WHERE id = ?
        """, (content_item_id, conversation_id))
        self.commit()
        logger.debug(f"Updated conversation {conversation_id} read_up_to_item = {content_item_id}")

    # =========================================================================
    # Unread Message Tracking (for GUI indicators)
    # =========================================================================

    def get_unread_count_for_conversation(self, conversation_id: int) -> int:
        """
        Get count of unread messages for a conversation.

        Args:
            conversation_id: Conversation ID

        Returns:
            Count of unread messages
        """
        result = self.fetchone("""
            SELECT COUNT(ci.id) as unread_count
            FROM conversation c
            JOIN content_item ci ON (
                ci.conversation_id = c.id
                AND ci.id > c.read_up_to_item
                AND ci.hide = 0
            )
            JOIN message m ON (
                ci.foreign_id = m.id
                AND ci.content_type = 0
                AND m.direction = 0
            )
            WHERE c.id = ?
        """, (conversation_id,))

        return result['unread_count'] if result else 0

    def _count_unread_items(self, account_id: int = None, per_conversation: bool = False):
        """
        Count unread items - canonical definition (single source of truth).

        An item is considered "unread" when ALL of these conditions are met:
        - Content type is message (0) or file transfer (2)
        - Direction is incoming (0) - we only count received items
        - Item is visible (hide = 0) - hidden/deleted items are not unread
        - Item is beyond the read marker (ci.id > c.read_up_to_item)

        This method ensures consistent unread counts across:
        - Contact list (per-conversation and per-account totals)
        - Status bar (global total across all accounts)

        Args:
            account_id: Filter by account ID (None = all accounts)
            per_conversation: If True, return per-conversation breakdown; if False, return total count

        Returns:
            If per_conversation=True: List of dicts with {jid, conversation_id, type, unread_count}
            If per_conversation=False: Integer total count
        """
        if per_conversation:
            # Per-conversation breakdown with JID information
            query = """
                SELECT
                    j.bare_jid as jid,
                    c.id as conversation_id,
                    c.type,
                    COUNT(ci.id) as unread_count
                FROM conversation c
                JOIN jid j ON c.jid_id = j.id
                JOIN content_item ci ON ci.conversation_id = c.id
                LEFT JOIN message m ON ci.foreign_id = m.id AND ci.content_type = 0
                LEFT JOIN file_transfer ft ON ci.foreign_id = ft.id AND ci.content_type = 2
                WHERE (
                      (ci.content_type = 0 AND m.direction = 0) OR
                      (ci.content_type = 2 AND ft.direction = 0)
                  )
                  AND ci.id > c.read_up_to_item
                  AND ci.hide = 0
            """

            if account_id is not None:
                query += " AND c.account_id = ?"
                params = (account_id,)
            else:
                params = ()

            query += """
                GROUP BY c.id, j.bare_jid, c.type
                HAVING unread_count > 0
            """

            return self.fetchall(query, params)
        else:
            # Total count only
            query = """
                SELECT COUNT(ci.id) as total_unread
                FROM conversation c
                JOIN content_item ci ON ci.conversation_id = c.id
                LEFT JOIN message m ON ci.foreign_id = m.id AND ci.content_type = 0
                LEFT JOIN file_transfer ft ON ci.foreign_id = ft.id AND ci.content_type = 2
                WHERE (
                      (ci.content_type = 0 AND m.direction = 0) OR
                      (ci.content_type = 2 AND ft.direction = 0)
                  )
                  AND ci.id > c.read_up_to_item
                  AND ci.hide = 0
            """

            if account_id is not None:
                query += " AND c.account_id = ?"
                params = (account_id,)
            else:
                params = ()

            result = self.fetchone(query, params)
            return result['total_unread'] if result else 0

    def get_unread_conversations_for_account(self, account_id: int) -> List[sqlite3.Row]:
        """
        Get all conversations with unread content (messages + files) for an account.

        Args:
            account_id: Account ID

        Returns:
            List of rows with keys: jid, conversation_id, type, unread_count
        """
        return self._count_unread_items(account_id=account_id, per_conversation=True)

    def get_total_unread_for_account(self, account_id: int) -> int:
        """
        Get total unread content count (messages + files) for an account.

        Args:
            account_id: Account ID

        Returns:
            Total unread count across all conversations
        """
        return self._count_unread_items(account_id=account_id, per_conversation=False)

    def get_global_statistics(self) -> dict:
        """
        Get all global statistics in a single query.

        Returns:
            Dictionary with keys:
                - accounts: {'total': int, 'enabled': int}
                - messages: {'total': int, 'unread': int, 'unsent': int}
                - calls: {'total': int, 'incoming': int, 'outgoing': int, 'missed': int}
        """
        # Get account stats
        accounts_total = self.fetchone("SELECT COUNT(*) as count FROM account")['count']
        accounts_enabled = self.fetchone("SELECT COUNT(*) as count FROM account WHERE enabled = 1")['count']

        # Get message stats
        messages_total = self.fetchone("SELECT COUNT(*) as count FROM message")['count']

        # Count unread content (messages + files, incoming only, matches contact list logic)
        messages_unread = self._count_unread_items(account_id=None, per_conversation=False)

        # Count unsent messages (outgoing pending only)
        messages_unsent = self.fetchone("""
            SELECT COUNT(*) as count
            FROM message
            WHERE marked = 0 AND direction = 1
        """)['count']

        # Get call stats
        calls_total = self.fetchone("SELECT COUNT(*) as count FROM call")['count']
        calls_incoming = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE direction = 0
        """)['count']
        calls_outgoing = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE direction = 1
        """)['count']
        calls_missed = self.fetchone("SELECT COUNT(*) as count FROM call WHERE state = 6")['count']

        return {
            'accounts': {
                'total': accounts_total,
                'enabled': accounts_enabled
            },
            'messages': {
                'total': messages_total,
                'unread': messages_unread,
                'unsent': messages_unsent
            },
            'calls': {
                'total': calls_total,
                'incoming': calls_incoming,
                'outgoing': calls_outgoing,
                'missed': calls_missed
            }
        }

    def get_account_statistics(self, account_id: int) -> dict:
        """
        Get statistics for a specific account.

        Args:
            account_id: Account ID

        Returns:
            Dictionary with keys:
                - messages: {'total': int, 'unread': int, 'unsent': int}
                - calls: {'total': int, 'incoming': int, 'outgoing': int, 'missed': int}
        """
        # Get message stats for this account
        messages_total = self.fetchone("""
            SELECT COUNT(*) as count
            FROM message
            WHERE account_id = ?
        """, (account_id,))['count']

        # Use existing method for total unread (already filters incoming only)
        messages_unread = self.get_total_unread_for_account(account_id)

        # Count unsent messages for this account
        messages_unsent = self.fetchone("""
            SELECT COUNT(*) as count
            FROM message
            WHERE account_id = ? AND marked = 0 AND direction = 1
        """, (account_id,))['count']

        # Get call stats for this account
        calls_total = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE account_id = ?
        """, (account_id,))['count']

        calls_incoming = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE account_id = ? AND direction = 0
        """, (account_id,))['count']

        calls_outgoing = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE account_id = ? AND direction = 1
        """, (account_id,))['count']

        calls_missed = self.fetchone("""
            SELECT COUNT(*) as count
            FROM call
            WHERE account_id = ? AND state = 6
        """, (account_id,))['count']

        return {
            'messages': {
                'total': messages_total,
                'unread': messages_unread,
                'unsent': messages_unsent
            },
            'calls': {
                'total': calls_total,
                'incoming': calls_incoming,
                'outgoing': calls_outgoing,
                'missed': calls_missed
            }
        }

    # =========================================================================
    # Conversation Settings (spell check, etc.)
    # =========================================================================

    def get_conversation_setting(self, conversation_id: int, key: str, default=None):
        """
        Get a conversation setting.

        Args:
            conversation_id: Conversation ID
            key: Setting key (e.g., 'spell_check_language', 'spell_check_enabled')
            default: Default value if not set

        Returns:
            Setting value or default
        """
        result = self.fetchone("""
            SELECT value FROM conversation_settings
            WHERE conversation_id = ? AND key = ?
        """, (conversation_id, key))

        return result['value'] if result else default

    def set_conversation_setting(self, conversation_id: int, key: str, value: str):
        """
        Set a conversation setting.

        Args:
            conversation_id: Conversation ID
            key: Setting key
            value: Setting value (stored as string)
        """
        self.execute("""
            INSERT OR REPLACE INTO conversation_settings (conversation_id, key, value)
            VALUES (?, ?, ?)
        """, (conversation_id, key, value))
        self.commit()
        logger.debug(f"Set conversation {conversation_id} setting {key} = {value}")

    def delete_conversation_setting(self, conversation_id: int, key: str):
        """
        Delete a conversation setting (revert to default).

        Args:
            conversation_id: Conversation ID
            key: Setting key
        """
        self.execute("""
            DELETE FROM conversation_settings
            WHERE conversation_id = ? AND key = ?
        """, (conversation_id, key))
        self.commit()
        logger.debug(f"Deleted conversation {conversation_id} setting {key}")


# Global database instance
_db_instance: Optional[Database] = None


def get_db() -> Database:
    """
    Get global database instance.

    Note: This is a simple getter. Call db.initialize() explicitly
    at application startup to run migrations.

    Returns:
        Database instance

    Raises:
        RuntimeError: If another instance is already running
    """
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.acquire_lock()  # Acquire lock before any operations
    return _db_instance
