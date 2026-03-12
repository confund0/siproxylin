"""
OS notification service for DRUNK-XMPP-GUI.

Sends desktop notifications for incoming messages with privacy controls.
"""

import platform
import logging
import subprocess
from typing import Optional, Dict, Tuple

from ..db.database import get_db

# Windows notifications (import only on Windows)
if platform.system() == 'Windows':
    try:
        from win10toast import ToastNotifier
    except ImportError:
        ToastNotifier = None


logger = logging.getLogger('siproxylin.notification')


class NotificationService:
    """
    Platform-agnostic OS notification service.

    Supports:
    - Linux: notify-send (mako, dunst, etc.) - full support with dismissal
    - macOS: osascript - basic support (no dismissal)
    - Windows: Windows 10+ toast notifications via win10toast-ng - full support (auto-dismiss)
    """

    def __init__(self):
        """Initialize notification service."""
        self.db = get_db()
        self.system = platform.system()

        # Track notification IDs separately for chat and calls
        # Chat notifications persist until chat is opened
        # Call notifications are short-lived (dismissed when call ends)
        self.chat_notification_ids: Dict[Tuple[int, str], int] = {}
        self.call_notification_ids: Dict[Tuple[int, str], int] = {}

        # Windows toast notifier (initialized only on Windows)
        self.toast_notifier = None
        if self.system == 'Windows' and ToastNotifier is not None:
            try:
                self.toast_notifier = ToastNotifier()
                logger.debug("Windows ToastNotifier initialized")
            except Exception as e:
                logger.warning(f"Failed to initialize Windows ToastNotifier: {e}")

        logger.debug(f"Notification service initialized for {self.system}")

    def send_notification(self, account_id: int, jid: str, title: str, body: str, icon: Optional[str] = None):
        """
        Send OS notification for chat messages with privacy settings applied.

        Args:
            account_id: Account ID (for tracking)
            jid: Contact JID (for tracking)
            title: Notification title (e.g., sender name)
            body: Notification body (message text)
            icon: Icon path (optional)
        """
        # Check if chat notifications are enabled
        if not self._are_chat_notifications_enabled():
            logger.debug("Chat notifications disabled in settings")
            return

        # Check privacy settings
        show_body = self._should_show_body()
        show_sender = self._should_show_sender()

        # Apply privacy mode for body text
        if not show_body:
            body = "New message"

        # Apply privacy mode for sender name
        if not show_sender:
            title = "Siproxylin"

        # Truncate long titles to prevent wrapping (max 25 chars)
        # Even medium JIDs like "hippopotamus@conversations.im" can wrap
        if len(title) > 25:
            title = title[:22] + "..."

        logger.info(f"Sending notification: {title} (show_sender={show_sender}, show_body={show_body})")

        try:
            if self.system == 'Linux':
                self._send_linux(account_id, jid, title, body, icon, is_call=False)
            elif self.system == 'Darwin':  # macOS
                self._send_macos(account_id, jid, title, body, is_call=False)
            elif self.system == 'Windows':
                self._send_windows(account_id, jid, title, body, is_call=False)
            else:
                logger.warning(f"Notifications not supported on {self.system}")
        except Exception as e:
            logger.error(f"Failed to send notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _should_show_body(self) -> bool:
        """
        Check if notification should show message body text.

        Returns:
            True if body should be shown, False for privacy mode
        """
        # Check global setting (default: true)
        # Later this will be per-account setting
        setting = self.db.get_setting('notification_show_body', default='true')
        return setting.lower() in ('true', '1', 'yes')

    def _should_show_sender(self) -> bool:
        """
        Check if notification should show sender name.

        Returns:
            True if sender should be shown, False for privacy mode
        """
        # Check global setting (default: true)
        setting = self.db.get_setting('notification_show_sender', default='true')
        return setting.lower() in ('true', '1', 'yes')

    def _are_chat_notifications_enabled(self) -> bool:
        """
        Check if chat notifications are enabled.

        Returns:
            True if chat notifications enabled, False otherwise
        """
        setting = self.db.get_setting('notification_chat_enabled', default='true')
        return setting.lower() in ('true', '1', 'yes')

    def _are_call_notifications_enabled(self) -> bool:
        """
        Check if call notifications are enabled.

        Returns:
            True if call notifications enabled, False otherwise
        """
        setting = self.db.get_setting('notification_calls_enabled', default='true')
        return setting.lower() in ('true', '1', 'yes')

    def send_call_notification(self, account_id: int, jid: str, caller_name: str, media_types: list, icon: Optional[str] = None):
        """
        Send OS notification for incoming call with privacy settings applied.

        Args:
            account_id: Account ID (for tracking)
            jid: Caller JID (for tracking)
            caller_name: Caller's display name
            media_types: List of media types (['audio'] or ['audio', 'video'])
            icon: Icon path (optional)
        """
        # Check if call notifications are enabled
        if not self._are_call_notifications_enabled():
            logger.debug("Call notifications disabled in settings")
            return

        # Check privacy settings
        show_sender = self._should_show_sender()

        # Determine call type
        if 'missed' in media_types:
            # Missed call notification
            call_type = "missed call"
            prefix = ""  # No "Incoming" prefix for missed calls
        elif 'video' in media_types:
            call_type = "video call"
            prefix = "Incoming "
        else:
            call_type = "audio call"
            prefix = "Incoming "

        # Apply privacy mode for caller name
        if show_sender:
            title = caller_name
            body = f"{prefix}{call_type}".strip()  # Strip in case prefix is empty
        else:
            title = "Siproxylin"
            body = f"{prefix}{call_type}".strip().capitalize()  # "Missed call" not "missed call"

        # Truncate long titles to prevent wrapping (max 25 chars)
        if len(title) > 25:
            title = title[:22] + "..."

        logger.info(f"Sending call notification: {title} - {body} (show_sender={show_sender})")

        try:
            if self.system == 'Linux':
                self._send_linux(account_id, jid, title, body, icon, is_call=True)
            elif self.system == 'Darwin':  # macOS
                self._send_macos(account_id, jid, title, body, is_call=True)
            elif self.system == 'Windows':
                self._send_windows(account_id, jid, title, body, is_call=True)
            else:
                logger.warning(f"Notifications not supported on {self.system}")
        except Exception as e:
            logger.error(f"Failed to send call notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _send_linux(self, account_id: int, jid: str, title: str, body: str, icon: Optional[str] = None, is_call: bool = False):
        """
        Send notification via notify-send (Linux).

        Works with: mako, dunst, notify-osd, etc.

        Args:
            account_id: Account ID
            jid: Contact JID
            title: Notification title
            body: Notification body
            icon: Icon path (optional)
            is_call: True for call notifications, False for chat
        """
        key = (account_id, jid)

        # Use appropriate tracking dict based on notification type
        notification_ids = self.call_notification_ids if is_call else self.chat_notification_ids

        # Check if we already have a notification for this conversation/call
        # If so, replace it instead of creating a new one
        replace_id = notification_ids.get(key, 0)

        cmd = ['notify-send', '-a', 'DRUNK-XMPP', '-p']  # -p to print notification ID

        # Use replace-id to replace existing notification
        if replace_id > 0:
            cmd.extend(['-r', str(replace_id)])

        if icon:
            cmd.extend(['-i', icon])

        cmd.extend([title, body])

        # Run notify-send and capture notification ID
        result = subprocess.run(cmd, check=False, capture_output=True, text=True)

        if result.returncode != 0:
            logger.warning(f"notify-send failed: {result.stderr}")
        else:
            # Store notification ID for later dismissal
            try:
                notification_id = int(result.stdout.strip())
                notification_ids[key] = notification_id
                logger.debug(f"Linux {'call' if is_call else 'chat'} notification sent successfully (ID: {notification_id}, replaced: {replace_id > 0})")
            except (ValueError, AttributeError) as e:
                logger.warning(f"Could not parse notification ID: {e}")

    def _send_macos(self, account_id: int, jid: str, title: str, body: str, is_call: bool = False):
        """
        Send notification via osascript (macOS).

        Args:
            account_id: Account ID
            jid: Contact JID
            title: Notification title
            body: Notification body
            is_call: True for call notifications, False for chat
        """
        # Placeholder for macOS implementation
        # Use osascript to display notification
        script = f'display notification "{body}" with title "{title}"'
        cmd = ['osascript', '-e', script]

        result = subprocess.run(cmd, check=False, capture_output=True, text=True)

        if result.returncode != 0:
            logger.warning(f"osascript failed: {result.stderr}")
        else:
            logger.debug(f"macOS {'call' if is_call else 'chat'} notification sent successfully")
            # macOS doesn't easily provide notification IDs for dismissal

    def _send_windows(self, account_id: int, jid: str, title: str, body: str, is_call: bool = False):
        """
        Send notification via Windows 10+ toast notification.

        Args:
            account_id: Account ID
            jid: Contact JID
            title: Notification title
            body: Notification body
            is_call: True for call notifications, False for chat

        Note:
            For future click handling to jump to message in chat view:
            - Store message_id in notification metadata
            - Use callback_on_click parameter to handle notification click
            - Callback should emit signal to focus conversation and scroll to message
            - See notification_manager.py for signal connection to GUI
        """
        if self.toast_notifier is None:
            logger.warning("Windows ToastNotifier not available (win10toast-ng not installed)")
            return

        key = (account_id, jid)
        notification_ids = self.call_notification_ids if is_call else self.chat_notification_ids

        # Generate unique tag for this notification (for future dismissal tracking)
        # Tag format: "chat_<account_id>_<jid_hash>" or "call_<account_id>_<jid_hash>"
        tag = f"{'call' if is_call else 'chat'}_{account_id}_{hash(jid)}"

        try:
            # Show toast notification (threaded=True to not block)
            # Duration: 5 seconds (default) for calls, 10 seconds for messages
            duration = 5 if is_call else 10

            # Note: callback_on_click can be used for future message click handling
            # callback_on_click=lambda: self._handle_notification_click(account_id, jid, message_id)
            self.toast_notifier.show_toast(
                title=title,
                msg=body,
                duration=duration,
                threaded=True,
                icon_path=None  # Future: use app icon or contact avatar
            )

            # Store tag for tracking (even though dismissal is not fully supported yet)
            notification_ids[key] = hash(tag)  # Store hash as placeholder ID
            logger.debug(f"Windows {'call' if is_call else 'chat'} notification sent successfully (tag: {tag})")

        except Exception as e:
            logger.error(f"Failed to show Windows toast notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def dismiss_notification(self, account_id: int, jid: str, is_call: bool = False):
        """
        Dismiss/close notification for a conversation or call.

        Args:
            account_id: Account ID
            jid: Contact JID
            is_call: True to dismiss call notification, False for chat (default)
        """
        key = (account_id, jid)
        notification_ids = self.call_notification_ids if is_call else self.chat_notification_ids

        if key not in notification_ids:
            logger.debug(f"No {'call' if is_call else 'chat'} notification to dismiss for {jid}")
            return

        notification_id = notification_ids[key]
        logger.info(f"Dismissing {'call' if is_call else 'chat'} notification {notification_id} for {jid}")

        try:
            if self.system == 'Linux':
                self._dismiss_linux(notification_id)
            elif self.system == 'Darwin':  # macOS
                self._dismiss_macos(notification_id)
            elif self.system == 'Windows':
                self._dismiss_windows(notification_id)

            # Remove from tracking
            del notification_ids[key]

        except Exception as e:
            logger.error(f"Failed to dismiss notification: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _dismiss_linux(self, notification_id: int):
        """
        Dismiss notification on Linux using D-Bus.

        Args:
            notification_id: Notification ID to close
        """
        # Try D-Bus method first (proper way to close notifications)
        cmd = [
            'gdbus', 'call', '--session',
            '--dest', 'org.freedesktop.Notifications',
            '--object-path', '/org/freedesktop/Notifications',
            '--method', 'org.freedesktop.Notifications.CloseNotification',
            str(notification_id)
        ]

        result = subprocess.run(cmd, check=False, capture_output=True, text=True)

        if result.returncode != 0:
            logger.debug(f"gdbus close failed, trying notify-send replace: {result.stderr}")

            # Fallback: Replace notification with empty message (auto-expires)
            cmd_replace = [
                'notify-send', '-a', 'DRUNK-XMPP',
                '-r', str(notification_id),
                '-t', '1',  # Expire after 1ms
                '', ''  # Empty title and body
            ]
            subprocess.run(cmd_replace, check=False, capture_output=True, text=True)
        else:
            logger.debug(f"Notification {notification_id} dismissed via D-Bus")

    def _dismiss_macos(self, notification_id: int):
        """
        Dismiss notification on macOS.

        Args:
            notification_id: Notification ID to close
        """
        # macOS osascript doesn't provide easy notification dismissal
        # Would need to use NSUserNotificationCenter or other APIs
        logger.debug("macOS notification dismissal not implemented")

    def _dismiss_windows(self, notification_id: int):
        """
        Dismiss notification on Windows.

        Args:
            notification_id: Notification ID to close

        Note:
            win10toast-ng doesn't provide built-in dismissal API.
            For programmatic dismissal, would need to:
            1. Use Windows.UI.Notifications API directly via winsdk or ctypes
            2. Track notification tags and call ToastNotificationManager.History.Remove(tag)
            3. Alternative: notifications auto-dismiss after duration expires
        """
        logger.debug(f"Windows notification dismissal not fully implemented (ID: {notification_id})")
        # Notifications will auto-dismiss after their duration expires
        # For immediate dismissal, would need Windows.UI.Notifications API


# Global notification service instance
_notification_service: Optional[NotificationService] = None


def get_notification_service() -> NotificationService:
    """
    Get global notification service instance.

    Returns:
        NotificationService instance
    """
    global _notification_service
    if _notification_service is None:
        _notification_service = NotificationService()
    return _notification_service
