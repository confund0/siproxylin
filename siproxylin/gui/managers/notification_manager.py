"""
NotificationManager - Manages OS notifications for messages and calls.

Extracted from MainWindow to improve maintainability.
"""

import logging


logger = logging.getLogger('siproxylin.notification_manager')


class NotificationManager:
    """
    Manages OS notifications for messages, calls, and missed calls.

    Responsibilities:
    - Send message notifications
    - Send call notifications (incoming/missed)
    - Query database for display names
    - Handle notification icons
    """

    def __init__(self, main_window):
        """
        Initialize NotificationManager.

        Args:
            main_window: MainWindow instance (for accessing DB and notification_service)
        """
        self.main_window = main_window
        self.db = main_window.db
        self.notification_service = main_window.notification_service

        # Get icon path from project root (vodka bottle logo) - works in both dev and AppImage
        self.icon_path = str(main_window.paths.project_root / 'siproxylin' / 'resources' / 'icons' / 'siproxylin.svg')

        logger.debug(f"NotificationManager initialized (icon: {self.icon_path})")

    def send_message_notification(self, account_id: int, from_jid: str):
        """
        Send OS notification for new message.

        Args:
            account_id: Account ID
            from_jid: Sender JID
        """
        try:
            # Get sender display name
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (from_jid,))
            if not jid_row:
                logger.warning(f"Cannot send notification: JID {from_jid} not in database")
                return

            jid_id = jid_row['id']

            # Check if notifications are enabled for this conversation
            # Determine conversation type (0=chat, 1=groupchat/MUC)
            # TODO: Properly detect MUC conversations (check bookmark or conversation table)
            conv_type = 0  # Default to 1-1 chat

            # Get or create conversation
            conversation_id = self.db.get_or_create_conversation(account_id, jid_id, conv_type)

            # Check notification setting
            conv = self.db.fetchone("""
                SELECT notification FROM conversation WHERE id = ?
            """, (conversation_id,))

            if conv and conv['notification'] == 0:
                logger.debug(f"Notifications disabled for {from_jid}, skipping OS notification")
                return

            # Try to get display name from roster
            roster_row = self.db.fetchone("""
                SELECT name FROM roster
                WHERE account_id = ? AND jid_id = ?
            """, (account_id, jid_id))

            if roster_row and roster_row['name']:
                sender_name = roster_row['name']
            else:
                # Fall back to bare JID
                sender_name = from_jid

            # Get most recent message from this sender
            message_row = self.db.fetchone("""
                SELECT body, time FROM message
                WHERE account_id = ? AND counterpart_id = ? AND direction = 0
                ORDER BY time DESC
                LIMIT 1
            """, (account_id, jid_id))

            # Files are stored in file_transfer, not in message. If the newest received
            # item is a file, show the file, not the older text message.
            file_row = self.db.fetchone("""
                SELECT file_name, time FROM file_transfer
                WHERE account_id = ? AND counterpart_id = ? AND direction = 0
                ORDER BY time DESC
                LIMIT 1
            """, (account_id, jid_id))

            if file_row and (not message_row or file_row['time'] >= message_row['time']):
                message_body = f"File: {file_row['file_name']}"
            elif message_row and message_row['body']:
                message_body = message_row['body']
            else:
                message_body = "New message"

            # Send notification via notification service
            self.notification_service.send_notification(
                account_id=account_id,
                jid=from_jid,
                title=sender_name,
                body=message_body,
                icon=self.icon_path
            )

            logger.debug(f"OS notification sent for message from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send OS notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def send_call_notification(self, account_id: int, from_jid: str, media_types: list):
        """
        Send OS notification for incoming call.

        Args:
            account_id: Account ID
            from_jid: Caller JID
            media_types: List of media types (['audio'] or ['audio', 'video'])
        """
        try:
            # Get caller display name from roster
            caller_name = self._get_display_name(account_id, from_jid)

            # Send notification via notification service
            self.notification_service.send_call_notification(
                account_id=account_id,
                jid=from_jid,
                caller_name=caller_name,
                media_types=media_types,
                icon=self.icon_path
            )

            logger.debug(f"OS notification sent for incoming call from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send call OS notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def send_missed_call_notification(self, account_id: int, from_jid: str):
        """
        Send OS notification for missed call (timeout).

        This replaces the ringing notification with a persistent missed call notification.

        Args:
            account_id: Account ID
            from_jid: Caller JID
        """
        try:
            # Get caller display name from roster
            caller_name = self._get_display_name(account_id, from_jid)

            # Send missed call notification (uses call_notification_ids, replaces ringing notification)
            # media_types = ['missed'] to indicate missed call
            self.notification_service.send_call_notification(
                account_id=account_id,
                jid=from_jid,
                caller_name=caller_name,
                media_types=['missed'],  # Special marker for missed call
                icon=self.icon_path
            )

            logger.debug(f"OS notification sent for missed call from {from_jid}")

        except Exception as e:
            logger.error(f"Failed to send missed call notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _get_display_name(self, account_id: int, jid: str) -> str:
        """
        Get display name for a JID from roster, or fall back to JID.

        Args:
            account_id: Account ID
            jid: Bare JID

        Returns:
            Display name or JID
        """
        try:
            jid_row = self.db.fetchone("SELECT id FROM jid WHERE bare_jid = ?", (jid,))
            if not jid_row:
                logger.warning(f"JID {jid} not in database, using JID as display name")
                return jid

            jid_id = jid_row['id']

            # Try to get display name from roster
            roster_row = self.db.fetchone("""
                SELECT name FROM roster
                WHERE account_id = ? AND jid_id = ?
            """, (account_id, jid_id))

            if roster_row and roster_row['name']:
                return roster_row['name']
            else:
                # Fall back to bare JID
                return jid

        except Exception as e:
            logger.error(f"Error getting display name for {jid}: {e}")
            return jid
