"""
Message Retry Handler for DRUNK-XMPP-GUI.

Handles retrying failed/pending messages on account reconnect.
Implements Phase 4: Message Retry Logic (TODO-retry-logic.md).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional, TYPE_CHECKING
from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from ..db.database import Database

# Import will be resolved at runtime to avoid circular imports
DrunkXMPP = None


logger = logging.getLogger('siproxylin.message_retry')


class MessageRetryHandler(QObject):
    """
    Handler for retrying failed messages across all accounts.
    Triggered on account reconnect.
    """

    # Signals for UI feedback
    retry_started = Signal(int)  # account_id
    retry_completed = Signal(int, dict)  # account_id, stats
    retry_failed = Signal(int, str)  # account_id, error
    user_prompt_needed = Signal(dict, int)  # message, account_id

    # Configuration
    RETRY_DIALOG_THRESHOLD_HOURS = 24  # Show user prompt after 24h of failures
    MAM_QUERY_WINDOW_SECONDS = 600  # Query ±10 min around message timestamp

    def __init__(self):
        """Initialize message retry handler."""
        super().__init__()
        # Message row IDs with a send task that is still running.
        # The retry skips them: the old task can still send after a reconnect.
        self._in_flight = set()
        # Message row IDs the retry skipped because their send task still ran.
        # A failed send task retries its row once when this mark is set.
        self._skipped = set()
        logger.info("MessageRetryHandler initialized")

    def start_send(self, message_id: int):
        """Mark a message row as in flight (a send task runs for it)."""
        self._in_flight.add(message_id)

    def end_send(self, message_id: int):
        """Remove the in-flight mark. A failed row stays pending for the next retry."""
        self._in_flight.discard(message_id)

    def is_in_flight(self, message_id: int) -> bool:
        """True if a send task still runs for this message row."""
        return message_id in self._in_flight

    async def retry_pending_messages_for_account(
        self,
        account_id: int,
        xmpp_client,  # DrunkXMPP client
        db: 'Database'
    ) -> Dict[str, int]:
        """
        Retry all pending messages for a specific account.

        Args:
            account_id: Account ID
            xmpp_client: DrunkXMPP client instance
            db: Database instance

        Returns:
            Dict with stats: {"resent": 3, "found_in_mam": 1, "failed": 0, "skipped_user_prompt": 2, "discarded": 1}
        """
        from ..utils.logger import get_account_logger

        acc_logger = get_account_logger(account_id)
        acc_logger.info(f"Starting message retry for account {account_id}")

        stats = {
            "resent": 0,
            "found_in_mam": 0,
            "failed": 0,
            "skipped_user_prompt": 0,
            "discarded": 0  # Messages discarded due to deleted MUC/contact
        }

        try:
            # Query pending messages (marked=0, direction=1)
            pending = db.get_pending_messages(account_id)

            if not pending:
                acc_logger.info("No pending messages to retry")
                return stats

            acc_logger.info(f"Found {len(pending)} pending messages to retry")

            for msg in pending:
                if self.is_in_flight(msg['id']):
                    acc_logger.info(f"Message {msg['id']} skipped: its send task still runs")
                    self._skipped.add(msg['id'])
                    continue
                await self._retry_row(msg, xmpp_client, db, acc_logger, stats, account_id)

            acc_logger.info(f"Retry completed: {stats}")
            return stats

        except Exception as e:
            acc_logger.error(f"Message retry failed for account {account_id}: {e}", exc_info=True)
            raise

    async def _retry_row(self, msg, xmpp_client, db: 'Database', acc_logger, stats: Dict[str, int], account_id: int):
        """Retry one pending row: MAM check, then resend. Adds the result to stats."""
        self._skipped.discard(msg['id'])
        self.start_send(msg['id'])
        try:
            # Initialize retry tracking if first attempt
            if msg['first_retry_attempt'] is None:
                db.initialize_retry_tracking(msg['id'])
                acc_logger.debug(f"Initialized retry tracking for message {msg['id']}")

            # Check if message has been failing for >24h
            if self._should_prompt_user(msg):
                # Emit signal for UI dialog (will be handled by main window)
                self.user_prompt_needed.emit(dict(msg), account_id)
                acc_logger.info(f"User prompt needed for message {msg['id']} (failing >24h)")
                stats["skipped_user_prompt"] += 1
                return

            # Step 1: Check MAM for deduplication
            acc_logger.debug(f"Checking MAM for message {msg['id']} (origin_id: {msg['origin_id']})")
            exists_in_mam = await self._check_message_in_mam(
                msg, xmpp_client, acc_logger
            )

            if exists_in_mam:
                # Message already on server, just update DB
                db.mark_message_delivered(msg['id'])
                acc_logger.info(f"Message {msg['id']} found in MAM, marked as delivered")
                stats["found_in_mam"] += 1
                return

            # Step 2: Resend message
            acc_logger.info(f"Resending message {msg['id']} (attempt {msg['retry_count'] + 1})")
            result = await self._resend_message(msg, xmpp_client, db, acc_logger)

            if result == "discarded":
                stats["discarded"] += 1
            else:
                # Update retry count and timestamp
                db.increment_retry_count(msg['id'])
                stats["resent"] += 1

        except Exception as e:
            acc_logger.error(f"Failed to retry message {msg['id']}: {e}", exc_info=True)
            stats["failed"] += 1
        finally:
            self.end_send(msg['id'])

    def take_skipped(self, message_id: int) -> bool:
        """True if the last retry skipped this row while its send task ran. Clears the mark."""
        if message_id in self._skipped:
            self._skipped.discard(message_id)
            return True
        return False

    async def retry_message(self, account_id: int, message_id: int, xmpp_client, db: 'Database') -> Dict[str, int]:
        """
        Retry one row now, if it is still pending.

        For a send task that failed after the retry on reconnect skipped its row.
        Without this, the row waits for the next session start.
        """
        from ..utils.logger import get_account_logger

        acc_logger = get_account_logger(account_id)
        stats = {"resent": 0, "found_in_mam": 0, "failed": 0, "skipped_user_prompt": 0, "discarded": 0}
        if self.is_in_flight(message_id):
            return stats
        for msg in db.get_pending_messages(account_id):
            if msg['id'] == message_id:
                acc_logger.info(f"Message {message_id} retried: its send task failed after the retry skipped it")
                await self._retry_row(msg, xmpp_client, db, acc_logger, stats, account_id)
                acc_logger.info(f"Retry of message {message_id} completed: {stats}")
                break
        return stats

    def _should_prompt_user(self, msg: dict) -> bool:
        """
        Check if message has been failing for >24h and needs user prompt.

        Args:
            msg: Message row from database

        Returns:
            True if user prompt is needed
        """
        if msg['first_retry_attempt'] is None:
            return False

        now = time.time()
        hours_since_first_retry = (now - msg['first_retry_attempt']) / 3600

        return hours_since_first_retry >= self.RETRY_DIALOG_THRESHOLD_HOURS

    async def _check_message_in_mam(
        self,
        msg: dict,
        xmpp_client,
        acc_logger: logging.Logger
    ) -> bool:
        """
        Query MAM around message timestamp to check if it exists.
        Uses origin_id for matching.

        Args:
            msg: Message row from database
            xmpp_client: DrunkXMPP client instance
            acc_logger: Account logger

        Returns:
            True if message found in MAM, False otherwise
        """
        try:
            counterpart_jid = msg['counterpart_jid']

            # Query MAM: ±10 minutes around message timestamp
            start_time = datetime.fromtimestamp(msg['time'] - self.MAM_QUERY_WINDOW_SECONDS, tz=timezone.utc)
            end_time = datetime.fromtimestamp(msg['time'] + self.MAM_QUERY_WINDOW_SECONDS, tz=timezone.utc)

            acc_logger.debug(f"Querying MAM from {start_time} to {end_time}")

            # retrieve_history is an async generator: it yields pages.
            # include_own_ids: our own OMEMO messages cannot be decrypted, they come
            # as entries with the stanza only. 1:1: only the archive with this contact.
            # is_stored: skip OMEMO messages with other IDs without decryption (their
            # keys are used up; only the IDs are compared here).
            is_muc = counterpart_jid in xmpp_client.rooms
            origin_id = msg['origin_id']
            history = xmpp_client.retrieve_history(
                jid=counterpart_jid,
                start=start_time,
                end=end_time,
                max_messages=50,
                with_jid=None if is_muc else counterpart_jid,
                include_own_ids=True,
                is_stored=lambda _archive_id, msg_origin_id, message_id: origin_id not in (msg_origin_id, message_id)
            )

            # Look for our origin_id in the results
            try:
                async for page in history:
                    for archived_msg in page:
                        # Skip receipt/marker entries (no stanza)
                        if archived_msg.get('marker_type'):
                            continue
                        # Check if message IDs match
                        archived_stanza = archived_msg['message']
                        if archived_stanza.get('id') == origin_id:
                            acc_logger.debug(f"Found message in MAM with origin_id {origin_id}")
                            return True
            finally:
                await history.aclose()

            acc_logger.debug(f"Message with origin_id {origin_id} not found in MAM")
            return False

        except RuntimeError as e:
            # MAM not supported - can't deduplicate, assume not there
            acc_logger.warning(f"MAM check failed (not supported?): {e}")
            return False
        except Exception as e:
            # Query failed - assume not there, will resend (origin_id prevents dupes)
            acc_logger.error(f"MAM query error: {e}", exc_info=True)
            return False

    async def _resend_message(
        self,
        msg: dict,
        xmpp_client,
        db: 'Database',
        acc_logger: logging.Logger
    ) -> str:
        """
        Resend a message using the appropriate method from DrunkXMPP client.

        IMPORTANT: Ensures message is sent from the correct account's XMPP client!

        Args:
            msg: Message row from database
            xmpp_client: DrunkXMPP client instance (MUST match account_id)
            db: Database instance
            acc_logger: Account logger

        Returns:
            "discarded" if message was discarded (MUC deleted), "sent" if successfully resent
        """
        counterpart_jid = msg['counterpart_jid']
        body = msg['body']
        is_encrypted = msg['encryption'] == 1
        is_groupchat = msg['type'] == 1

        # Validate MUC still exists before attempting resend
        if is_groupchat:
            if counterpart_jid not in xmpp_client.rooms:
                # MUC has been deleted - mark message as discarded
                acc_logger.warning(
                    f"Message {msg['id']} for deleted MUC {counterpart_jid} - marking as discarded"
                )
                db.mark_message_discarded(msg['id'])
                return "discarded"  # Don't attempt resend, don't raise exception

        try:
            # Resend using appropriate method based on message type
            if is_groupchat:
                if is_encrypted:
                    msg_id = await xmpp_client.send_encrypted_to_muc(counterpart_jid, body)
                else:
                    msg_id = await xmpp_client.send_to_muc(counterpart_jid, body)
            else:
                if is_encrypted:
                    msg_id = await xmpp_client.send_encrypted_private_message(counterpart_jid, body)
                else:
                    msg_id = await xmpp_client.send_private_message(counterpart_jid, body)

            # Update origin_id in DB to track new message_id
            db.update_message_origin_id(msg['id'], msg_id)
            acc_logger.info(f"Message resent successfully with new origin_id {msg_id}")
            return "sent"

        except Exception as e:
            acc_logger.error(f"Failed to resend message: {e}", exc_info=True)
            raise


# Global singleton instance
_retry_handler: Optional[MessageRetryHandler] = None


def get_retry_handler() -> MessageRetryHandler:
    """
    Get the global message retry handler instance.

    Returns:
        MessageRetryHandler singleton
    """
    global _retry_handler
    if _retry_handler is None:
        _retry_handler = MessageRetryHandler()
    return _retry_handler
