"""
CallManager - Manages call windows, dialogs, and Go call service.

Extracted from MainWindow to improve maintainability.
"""

import logging
import asyncio
import subprocess
from typing import Optional, Dict
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt, QTimer

# Import Go call service (optional - may not be available)
try:
    from drunk_call_hook import GoCallService
    GO_CALL_SERVICE_AVAILABLE = True
except ImportError:
    GO_CALL_SERVICE_AVAILABLE = False

from ..dialogs import IncomingCallDialog, OutgoingCallDialog
from ..call_window import CallWindow


logger = logging.getLogger('siproxylin.call_manager')


class CallManager:
    """
    Manages call lifecycle, dialogs, and Go call service.

    Responsibilities:
    - Handle incoming/outgoing call signals
    - Manage call windows and dialogs
    - Track call sessions
    - Update roster call indicators
    - Manage Go call service lifecycle
    """

    def __init__(self, main_window):
        """
        Initialize CallManager.

        Args:
            main_window: MainWindow instance (for accessing widgets and services)
        """
        self.main_window = main_window
        self.account_manager = main_window.account_manager
        self.contact_list = main_window.contact_list
        self.notification_manager = None  # Will be set after NotificationManager is created

        # Track open call windows and dialogs
        self.call_windows: Dict[str, CallWindow] = {}  # {session_id: CallWindow}
        self.incoming_call_dialogs: Dict[str, IncomingCallDialog] = {}  # {session_id: dialog}
        self.outgoing_call_dialogs: Dict[str, OutgoingCallDialog] = {}  # {session_id: dialog}
        self.call_session_map: Dict[str, tuple] = {}  # {session_id: (account_id, jid)}

        # Go call service (app-level, shared by all accounts)
        self.go_call_service: Optional[object] = None
        if GO_CALL_SERVICE_AVAILABLE:
            self.go_call_service = GoCallService(logger=logger)

        logger.debug("CallManager initialized")

    def connect_account_signals(self, account):
        """
        Connect call-related signals from an account.

        Args:
            account: XMPPAccount instance
        """
        account.call_incoming.connect(self.on_call_incoming)
        account.call_initiated.connect(self.on_call_initiated)
        account.call_accepted.connect(self.on_call_accepted)
        account.call_terminated.connect(self.on_call_terminated)
        account.call_state_changed.connect(self.on_call_state_changed)
        logger.debug(f"Connected call signals for account {account.account_id}")

    async def start_service(self):
        """Start Go call service asynchronously."""
        if not self.go_call_service:
            return

        try:
            logger.debug("Starting Go call service...")
            success = await self.go_call_service.start()
            if success:
                logger.debug("Go call service started successfully")
            else:
                logger.error("Failed to start Go call service")
        except Exception as e:
            logger.error(f"Error starting Go call service: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def shutdown_service(self, signal_shutdown: bool = False):
        """
        Shutdown Go call service.

        Args:
            signal_shutdown: True if shutdown triggered by signal (Ctrl+C)
        """
        if not self.go_call_service:
            return

        try:
            if signal_shutdown:
                # Signal-triggered shutdown - Go service handles SIGINT itself
                logger.debug("Signal-triggered shutdown - skipping Go RPC, letting Go handle signal")
                if self.go_call_service._process:
                    try:
                        self.go_call_service._process.wait(timeout=2.0)
                        logger.debug("Go service exited")
                    except subprocess.TimeoutExpired:
                        logger.warning("Go service didn't exit, terminating")
                        self.go_call_service._process.terminate()
            else:
                # Normal shutdown (File->Quit): use gRPC for graceful shutdown
                logger.debug("Sending shutdown to Go call service...")
                from PySide6.QtCore import QEventLoop
                loop = QEventLoop()
                future = asyncio.ensure_future(self.go_call_service.stop())
                future.add_done_callback(lambda _: loop.quit())
                loop.exec()  # Wait for stop() to complete
                logger.debug("Go call service shutdown request completed")
        except Exception as e:
            logger.error(f"Error stopping Go call service: {e}")

    def on_call_incoming(self, account_id: int, session_id: str, from_jid: str, media: list):
        """
        Handle incoming call - show IncomingCallDialog.

        Args:
            account_id: Account receiving the call
            session_id: Jingle session ID
            from_jid: Caller JID
            media: Media types (['audio'] or ['audio', 'video'])
        """
        logger.info(f"Incoming call from {from_jid}: {media} (session {session_id})")

        # Track session → jid mapping for roster indicators
        self.call_session_map[session_id] = (account_id, from_jid)

        # Update roster indicator: incoming call (ringing)
        self.contact_list.update_call_indicator(account_id, from_jid, 'incoming')

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot handle incoming call: account {account_id} not found")
            return

        # Send OS notification for incoming call
        if self.notification_manager:
            self.notification_manager.send_call_notification(account_id, from_jid, media)

        # Show incoming call dialog
        dialog = IncomingCallDialog(self.main_window, account_id, session_id, from_jid, media)

        # Track dialog so we can close it on timeout
        self.incoming_call_dialogs[session_id] = dialog

        # Connect dialog signals
        # Use QTimer.singleShot to defer execution outside signal handler context
        def schedule_accept():
            def do_accept():
                asyncio.ensure_future(self._accept_call(account_id, session_id, from_jid, media))
            QTimer.singleShot(0, do_accept)

        def schedule_reject():
            def do_reject():
                asyncio.ensure_future(account.hangup_call(session_id))
            QTimer.singleShot(0, do_reject)

        dialog.call_accepted.connect(schedule_accept)
        dialog.call_rejected.connect(schedule_reject)
        dialog.call_silenced.connect(lambda: logger.info(
            f"Call silenced (ignored): {session_id} from {from_jid}"
        ))

        # Show dialog (non-blocking)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        # Clean up tracking when dialog closes
        dialog.finished.connect(lambda: self.incoming_call_dialogs.pop(session_id, None))

        dialog.show()

    async def _accept_call(self, account_id: int, session_id: str, from_jid: str, media: list):
        """
        Accept incoming call and open call window.

        Args:
            account_id: Account accepting the call
            session_id: Jingle session ID
            from_jid: Caller JID
            media: Media types
        """
        logger.debug(f"Accepting call from {from_jid} (session {session_id})")

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot accept call: account {account_id} not found")
            return

        try:
            # Accept the call (creates WebRTC connection)
            await account.accept_call(session_id)

            # Open call window
            self._open_call_window(account_id, session_id, from_jid, media, 'incoming')

        except Exception as e:
            logger.error(f"Failed to accept call: {e}")
            import traceback
            logger.error(traceback.format_exc())
            QMessageBox.critical(
                self.main_window,
                "Call Failed",
                f"Could not accept call: {e}"
            )

    def on_call_initiated(self, account_id: int, session_id: str, peer_jid: str, media: list):
        """
        Handle outgoing call initiated - show OutgoingCallDialog.

        Args:
            account_id: Account initiating the call
            session_id: Jingle session ID
            peer_jid: JID being called
            media: Media types (['audio'] or ['audio', 'video'])
        """
        logger.info(f"Outgoing call initiated to {peer_jid}: {media} (session {session_id})")

        # Track session → jid mapping for roster indicators
        self.call_session_map[session_id] = (account_id, peer_jid)

        # Update roster indicator: outgoing call (ringing)
        self.contact_list.update_call_indicator(account_id, peer_jid, 'outgoing')

        # Get account
        account = self.account_manager.get_account(account_id)
        if not account:
            logger.error(f"Cannot handle outgoing call: account {account_id} not found")
            return

        # Show outgoing call dialog
        dialog = OutgoingCallDialog(self.main_window, account_id, session_id, peer_jid, media)

        # Track dialog so we can close it when call is answered/rejected/timeout
        self.outgoing_call_dialogs[session_id] = dialog

        # Connect cancel signal
        def schedule_cancel():
            def do_cancel():
                asyncio.ensure_future(account.hangup_call(session_id))
            QTimer.singleShot(0, do_cancel)

        dialog.call_cancelled.connect(schedule_cancel)

        # Show dialog (non-blocking)
        dialog.setAttribute(Qt.WA_DeleteOnClose)

        # Clean up tracking when dialog closes
        dialog.finished.connect(lambda: self.outgoing_call_dialogs.pop(session_id, None))

        dialog.show()

    def on_call_accepted(self, account_id: int, session_id: str):
        """
        Handle call accepted (outgoing call accepted by peer) - open call window.

        Args:
            account_id: Account that initiated the call
            session_id: Jingle session ID
        """
        logger.debug(f"Handler on_call_accepted called: account_id={account_id}, session_id={session_id}")
        logger.debug(f"Outgoing call accepted (session {session_id})")

        # Update roster indicator: call active (connected)
        if session_id in self.call_session_map:
            acc_id, jid = self.call_session_map[session_id]
            self.contact_list.update_call_indicator(acc_id, jid, 'active')

        # Get account and session info
        account = self.account_manager.get_account(account_id)
        if not account or not account.jingle_adapter:
            logger.error(f"Cannot handle call acceptance: account or adapter not found")
            return

        # Use JingleAdapter's public API (encapsulation)
        session_info = account.jingle_adapter.get_session_info(session_id)
        if not session_info:
            logger.error(f"Cannot handle call acceptance: session {session_id} not found")
            return

        peer_jid = session_info['peer_jid']
        media = session_info['media']

        # Close outgoing call dialog (peer accepted - transition to call window)
        if session_id in self.outgoing_call_dialogs:
            dialog = self.outgoing_call_dialogs.pop(session_id)
            dialog.accept()  # Close dialog
            logger.debug(f"Closed outgoing call dialog - peer accepted: {session_id}")

        # Open call window
        self._open_call_window(account_id, session_id, peer_jid, media, 'outgoing')

    def _open_call_window(self, account_id: int, session_id: str, peer_jid: str,
                          media: list, direction: str):
        """
        Open call window for active call.

        Args:
            account_id: Account in call
            session_id: Jingle session ID
            peer_jid: Peer JID
            media: Media types
            direction: 'incoming' or 'outgoing'
        """
        logger.debug(f"Opening call window: {peer_jid} ({direction}, {media})")

        # Get account
        account = self.account_manager.get_account(account_id)

        # Create call window
        call_window = CallWindow(self.main_window, account_id, session_id, peer_jid, media, direction, account=account)

        # Connect signals
        if account:
            # Update call window when state changes
            account.call_state_changed.connect(
                lambda aid, sid, state: (
                    call_window.on_call_state_changed(state)
                    if sid == session_id else None
                )
            )

            # Update call window when call terminates
            account.call_terminated.connect(
                lambda aid, sid, reason, peer: (
                    call_window.on_call_terminated(reason)
                    if sid == session_id else None
                )
            )

            # Handle hangup button click
            call_window.hangup_requested.connect(
                lambda: asyncio.create_task(account.hangup_call(session_id))
            )

        # Track window
        self.call_windows[session_id] = call_window

        # Show window
        call_window.show()

    def on_call_state_changed(self, account_id: int, session_id: str, state: str):
        """
        Handle call state change (WebRTC connection state).

        Args:
            account_id: Account in call
            session_id: Jingle session ID
            state: Connection state
        """
        logger.debug(f"Call state changed: {state} (session {session_id})")
        # State updates are already forwarded to call window via signal connections
        # in _open_call_window, so nothing more to do here

    def on_call_terminated(self, account_id: int, session_id: str, reason: str, peer_jid: str):
        """
        Handle call termination - cleanup call window and dialogs.

        Args:
            account_id: Account that was in call
            session_id: Jingle session ID
            reason: Termination reason
            peer_jid: JID of the peer
        """
        logger.info(f"Call terminated: {reason} (session {session_id}, peer {peer_jid})")

        # Update roster indicator: clear call state
        if session_id in self.call_session_map:
            acc_id, jid = self.call_session_map.pop(session_id)
            self.contact_list.update_call_indicator(acc_id, jid, None)

        # Handle call notification based on termination reason
        if reason == 'timeout':
            # Missed call
            if self.notification_manager:
                self.notification_manager.send_missed_call_notification(account_id, peer_jid)
        else:
            # Call was answered, rejected, or ended - just dismiss the notification
            if self.notification_manager:
                self.notification_manager.notification_service.dismiss_notification(account_id, peer_jid, is_call=True)

        # Close incoming call dialog if still open (e.g., timeout)
        if session_id in self.incoming_call_dialogs:
            dialog = self.incoming_call_dialogs.pop(session_id)
            dialog.reject()  # Close dialog
            logger.debug(f"Closed incoming call dialog due to {reason}: {session_id}")

        # Update outgoing call dialog if still open (e.g., peer rejected or timeout)
        if session_id in self.outgoing_call_dialogs:
            dialog = self.outgoing_call_dialogs[session_id]
            # Update dialog to show rejection/timeout/error status
            dialog.update_on_status_change(reason)
            logger.debug(f"Updated outgoing call dialog for {reason}: {session_id}")

        # Call window will auto-close after 2 seconds (handled in CallWindow itself)
        if session_id in self.call_windows:
            logger.debug(f"Call window for {session_id} will auto-close")

        # Update status bar to reflect call stats
        self.main_window._update_status_bar_stats()

    def request_call_mute(self, account_id: int, session_id: str, muted: bool):
        """
        Request microphone mute state change for a session.

        Called by CallWindow when user toggles mute button.

        Args:
            account_id: Account ID
            session_id: Jingle session ID
            muted: True to mute, False to unmute
        """
        async def set_mute():
            try:
                # Get account manager for this account
                account = self.account_manager.get_account(account_id)
                if not account or not hasattr(account, 'calls'):
                    logger.error(f"Account {account_id} not found or has no call functionality")
                    return

                # Set mute state via CallBarrel
                await account.calls.set_mute(session_id, muted)
                logger.info(f"Mute state set: session={session_id}, muted={muted}")

            except Exception as e:
                logger.error(f"Error setting mute state: {e}")
                import traceback
                logger.error(traceback.format_exc())

        # Schedule async work
        asyncio.ensure_future(set_mute())

    def request_call_stats(self, account_id: int, session_id: str):
        """
        Request call statistics update for a session.

        Called by CallWindow every 2 seconds to update tech details.

        Args:
            account_id: Account ID
            session_id: Jingle session ID
        """
        async def fetch_and_update():
            try:
                # Get account manager for this account
                account = self.account_manager.get_account(account_id)
                if not account or not hasattr(account, 'calls'):
                    return

                # Get stats from CallBarrel
                stats = await account.calls.get_call_stats(session_id)

                # Update call window if still open
                if session_id in self.call_windows:
                    call_window = self.call_windows[session_id]
                    call_window.update_stats(stats)

            except Exception as e:
                logger.error(f"Error fetching call stats: {e}")

        # Schedule async task
        asyncio.ensure_future(fetch_and_update())
