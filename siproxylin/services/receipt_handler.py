"""
Receipt and marker handling for database updates.

Handles XEP-0184 (receipts), XEP-0333 (markers), and XEP-0198 (server ACKs).
Updates message `marked` status following this schema:
    0 = NONE (pending/not sent)
    1 = SENT (server ACK received - single ✓)
    2 = RECEIVED (delivery receipt - double ✓✓)
    7 = READ (displayed marker - double ✓✓ bold)
    8 = ERROR (won't send)
"""

import logging
from typing import Optional
from ..db.database import Database


logger = logging.getLogger('siproxylin.receipt_handler')


class ReceiptHandler:
    """Handles receipt and marker database updates."""

    def __init__(self, db: Database):
        """
        Initialize receipt handler.

        Args:
            db: Database instance
        """
        self.db = db

    def on_server_ack(self, account_id: int, message_id: str):
        """
        Handle server ACK (XEP-0198).
        Updates marked=1 if message is currently marked=0.

        Args:
            account_id: Account ID
            message_id: Message origin_id (our sent message ID)
        """
        try:
            # Only update if currently marked=0 (pending)
            # Don't downgrade if already received/read
            updated = self.db.execute(
                """
                UPDATE message
                SET marked = 1
                WHERE account_id = ?
                  AND origin_id = ?
                  AND marked = 0
                """,
                (account_id, message_id)
            )

            if updated.rowcount > 0:
                self.db.commit()
                logger.debug(f"Server ACK: marked message {message_id} as SENT (marked=1)")
            else:
                logger.debug(f"Server ACK: message {message_id} already marked or not found")

        except Exception as e:
            logger.error(f"Failed to update server ACK for {message_id}: {e}")

    def on_delivery_receipt(self, account_id: int, counterpart_jid: str, message_id: str) -> bool:
        """
        Handle delivery receipt (XEP-0184).
        Updates marked=2 if message is currently marked<=1.

        Args:
            account_id: Account ID
            counterpart_jid: Sender's bare JID
            message_id: origin_id or message_id of our sent message
                (messages from our other devices may have no origin-id)

        Returns:
            True if a message was updated
        """
        try:
            # Get counterpart JID ID
            jid_row = self.db.fetchone(
                "SELECT id FROM jid WHERE bare_jid = ?",
                (counterpart_jid,)
            )

            if not jid_row:
                logger.warning(f"Delivery receipt: JID {counterpart_jid} not found")
                return False

            counterpart_id = jid_row['id']

            # Update if currently marked<=1 (pending or sent)
            updated = self.db.execute(
                """
                UPDATE message
                SET marked = 2
                WHERE account_id = ?
                  AND counterpart_id = ?
                  AND (origin_id = ? OR message_id = ?)
                  AND direction = 1
                  AND marked <= 1
                """,
                (account_id, counterpart_id, message_id, message_id)
            )

            if updated.rowcount > 0:
                self.db.commit()
                logger.info(f"Delivery receipt: marked message {message_id} as RECEIVED (marked=2)")
                return True
            logger.debug(f"Delivery receipt: message {message_id} already marked or not found")

        except Exception as e:
            logger.error(f"Failed to update delivery receipt for {message_id}: {e}")
        return False

    def on_displayed_marker(self, account_id: int, counterpart_jid: str, message_id: str) -> bool:
        """
        Handle displayed marker (XEP-0333).
        Updates marked=7 for ALL messages up to and including this message (cumulative).

        Args:
            account_id: Account ID
            counterpart_jid: Sender's bare JID
            message_id: origin_id or message_id of our sent message that was displayed
                (messages from our other devices may have no origin-id)

        Returns:
            True if a message was updated
        """
        try:
            # Get counterpart JID ID
            jid_row = self.db.fetchone(
                "SELECT id FROM jid WHERE bare_jid = ?",
                (counterpart_jid,)
            )

            if not jid_row:
                logger.warning(f"Displayed marker: JID {counterpart_jid} not found")
                return False

            counterpart_id = jid_row['id']

            # First, get the timestamp of the marked message
            marked_msg = self.db.fetchone(
                """
                SELECT time
                FROM message
                WHERE account_id = ?
                  AND counterpart_id = ?
                  AND (origin_id = ? OR message_id = ?)
                  AND direction = 1
                """,
                (account_id, counterpart_id, message_id, message_id)
            )

            if not marked_msg:
                logger.warning(f"Displayed marker: message {message_id} not found")
                return False

            marked_time = marked_msg['time']

            # CUMULATIVE UPDATE: Mark all messages up to this timestamp as READ
            # Only update messages that are currently marked<7
            updated = self.db.execute(
                """
                UPDATE message
                SET marked = 7
                WHERE account_id = ?
                  AND counterpart_id = ?
                  AND direction = 1
                  AND time <= ?
                  AND marked < 7
                """,
                (account_id, counterpart_id, marked_time)
            )

            count = updated.rowcount
            if count > 0:
                self.db.commit()
                logger.info(
                    f"Displayed marker: marked {count} message(s) up to {message_id} as READ (marked=7)"
                )
                return True
            logger.debug(f"Displayed marker: no messages to update for {message_id}")

        except Exception as e:
            logger.error(f"Failed to update displayed marker for {message_id}: {e}")
        return False

    def on_received_marker(self, account_id: int, counterpart_jid: str, message_id: str):
        """
        Handle received marker (XEP-0333).
        This is redundant with delivery receipts, so we ignore it per your strategy.

        Args:
            account_id: Account ID
            counterpart_jid: Sender's bare JID
            message_id: Message origin_id
        """
        logger.debug(f"Received marker for {message_id}: ignoring (redundant with delivery receipt)")
        # Intentionally do nothing - delivery receipts are preferred
