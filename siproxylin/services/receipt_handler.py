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

    def on_own_displayed_marker(self, account_id: int, counterpart_jid: str, message_id: str) -> bool:
        """
        Handle our own displayed marker (XEP-0333) sent from another device.
        The peer's messages up to the marked one are read there, so move
        conversation.read_up_to_item up to the newest received item at or
        before the marked item's time. It never goes down.

        Args:
            account_id: Account ID
            counterpart_jid: Peer's bare JID (the marker was sent to it)
            message_id: message_id, origin_id or stanza_id of the peer's message

        Returns:
            True if read_up_to_item went up
        """
        try:
            conv = self.db.fetchone(
                """
                SELECT c.id, c.read_up_to_item
                FROM conversation c
                JOIN jid j ON c.jid_id = j.id
                WHERE c.account_id = ? AND j.bare_jid = ? AND c.type = 0
                """,
                (account_id, counterpart_jid)
            )
            if not conv:
                logger.debug(f"Own displayed marker: no conversation with {counterpart_jid}")
                return False

            # Received message or file with this ID
            marked_item = self.db.fetchone(
                """
                SELECT ci.id, ci.time
                FROM content_item ci
                LEFT JOIN message m ON ci.foreign_id = m.id AND ci.content_type = 0
                LEFT JOIN file_transfer ft ON ci.foreign_id = ft.id AND ci.content_type = 2
                WHERE ci.conversation_id = ?
                  AND (
                      (ci.content_type = 0 AND m.direction = 0
                       AND (m.message_id = ? OR m.origin_id = ? OR m.stanza_id = ?)) OR
                      (ci.content_type = 2 AND ft.direction = 0
                       AND (ft.message_id = ? OR ft.origin_id = ? OR ft.stanza_id = ?))
                  )
                ORDER BY ci.time DESC
                LIMIT 1
                """,
                (conv['id'], message_id, message_id, message_id, message_id, message_id, message_id)
            )
            if not marked_item:
                logger.debug(f"Own displayed marker: message {message_id} not found")
                return False

            # Newest received item up to the marked item (cumulative).
            # Times have seconds only: in the same second the item ID gives the order.
            row = self.db.fetchone(
                """
                SELECT MAX(ci.id) AS max_id
                FROM content_item ci
                LEFT JOIN message m ON ci.foreign_id = m.id AND ci.content_type = 0
                LEFT JOIN file_transfer ft ON ci.foreign_id = ft.id AND ci.content_type = 2
                WHERE ci.conversation_id = ?
                  AND (
                      (ci.content_type = 0 AND m.direction = 0) OR
                      (ci.content_type = 2 AND ft.direction = 0)
                  )
                  AND (ci.time < ? OR (ci.time = ? AND ci.id <= ?))
                """,
                (conv['id'], marked_item['time'], marked_item['time'], marked_item['id'])
            )
            max_id = row['max_id'] if row else None
            if max_id is None or max_id <= conv['read_up_to_item']:
                logger.debug(f"Own displayed marker: {message_id} already read")
                return False

            self.db.execute(
                "UPDATE conversation SET read_up_to_item = ? WHERE id = ?",
                (max_id, conv['id'])
            )
            self.db.commit()
            logger.info(f"Own displayed marker: read up to item {max_id} with {counterpart_jid}")
            return True

        except Exception as e:
            logger.error(f"Failed to apply own displayed marker for {message_id}: {e}")
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
