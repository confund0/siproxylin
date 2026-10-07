#!/usr/bin/env python3
"""
Offline tests for mic, speaker and camera errors in the call window.

The call service sends an ErrorEvent when the mic, the speaker or the camera fails.
The path: CallBridge -> JingleAdapter -> CallBarrel -> call_error signal
-> CallManager -> CallWindow. The call window shows the text and the
call stays up. An error for another session does not reach the window.
An error that comes before the window opens (outgoing call: the mic
starts with the offer) is kept and shown when the window opens. Errors
of a call that ended before its window opened are dropped. Errors are
kept per account: a call to an own account rings that account with the
same session id, and its end must not drop the errors of the caller.

Needs PySide6 (offscreen). grpc is replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_call_audio_error.py
"""

import os
import sys
import asyncio
import logging
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'grpc.aio', 'drunk_call_hook.proto.call_pb2_grpc')}
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from drunk_call_hook.proto import call_pb2
    from drunk_call_hook.bridge import CallBridge
    from drunk_call_hook.protocol.jingle import JingleAdapter
    from siproxylin.core.barrels.calls import CallBarrel
    from siproxylin.gui.managers.call_manager import CallManager
logging.disable(logging.NOTSET)

SID = 'A14qA4pgzl7k_I56b2PklQ'
ERROR_TEXT = 'Microphone error: Failed to open device. Access denied.'
ERROR_NAME = 'Microphone error'
CAMERA_TEXT = 'Camera error: Failed to start'
CAMERA_NAME = 'Camera error'

APP = QApplication.instance() or QApplication([])


def shown_error_label(window):
    """The error label of the current mode: control bar (slim) or full mode."""
    if window.slim_container.isVisibleTo(window):
        return window.error_label
    return window.full_error_label


class FakeAccount(QObject):
    call_incoming = Signal(int, str, str, list)
    call_initiated = Signal(int, str, str, list)
    call_accepted = Signal(int, str)
    call_state_changed = Signal(int, str, str)
    call_terminated = Signal(int, str, str, str)
    call_error = Signal(int, str, str)

    jingle_adapter = SimpleNamespace(
        get_session_info=lambda sid: {'peer_jid': 'bob@localhost', 'media': ['audio']})

    def __init__(self, account_id):
        super().__init__()
        self.account_id = account_id
        self.calls = None

    async def hangup_call(self, session_id):
        pass


class FakeMainWindow(QWidget):
    def _update_status_bar_stats(self):
        pass


class CallErrorBase(unittest.TestCase):
    """Real CallManager and the bridge path, no window open yet."""

    def setUp(self):
        logger = logging.getLogger('test_call_audio_error')
        logger.disabled = True
        self.accounts = {}
        self.bridges = {}
        for account_id in (1, 2):
            account = FakeAccount(account_id)
            bridge = CallBridge(logger=logger)
            account.calls = CallBarrel(account_id, None, logger, {'call_error': account.call_error})
            JingleAdapter(mock.MagicMock(), bridge,
                          on_call_error=account.calls._on_call_error, logger=logger)
            self.accounts[account_id] = account
            self.bridges[account_id] = bridge
        self.account = self.accounts[1]
        self.bridge = self.bridges[1]

        self.main_window = FakeMainWindow()
        self.main_window.account_manager = SimpleNamespace(get_account=self.accounts.get)
        self.main_window.contact_list = mock.MagicMock()
        self.manager = CallManager(self.main_window)
        self.manager.go_call_service = None
        for account in self.accounts.values():
            self.manager.connect_account_signals(account)
        self.window = None

    def tearDown(self):
        for dialog in list(self.manager.outgoing_call_dialogs.values()):
            dialog.close()
        if self.window is not None:
            self.window._call_ended = True  # no hangup on close
            self.window.close()
            self.window.deleteLater()
        self.main_window.deleteLater()
        APP.processEvents()

    def start_call(self, session_id=SID):
        """Outgoing call: the dialog shows, no call window yet."""
        # The barrel creates the call service session with the offer
        self.account.calls._bridge_sessions.add(session_id)
        self.manager.on_call_initiated(1, session_id, 'bob@localhost', ['audio'])
        APP.processEvents()

    def accept_call(self, session_id=SID):
        """The peer accepts: the call window opens."""
        self.manager.on_call_accepted(1, session_id)
        self.window = self.manager.call_windows[session_id]
        APP.processEvents()

    def end_call(self, account_id, session_id, reason):
        """The barrel cleans up its state, then sends call_terminated."""
        account = self.accounts[account_id]
        account.calls._bridge_sessions.discard(session_id)
        account.calls._incoming_calls.discard(session_id)
        account.call_terminated.emit(account_id, session_id, reason, 'bob@localhost')
        APP.processEvents()

    def send_error(self, session_id, text, account_id=1):
        event = call_pb2.CallEvent(session_id=session_id,
                                   error=call_pb2.ErrorEvent(message=text))
        asyncio.run(self.bridges[account_id]._handle_event(session_id, event))
        APP.processEvents()


class TestCallAudioError(CallErrorBase):

    def setUp(self):
        super().setUp()
        self.start_call()
        self.accept_call()
        self.hangups = []
        self.window.hangup_requested.connect(lambda: self.hangups.append(1))

    def test_error_shows_in_window(self):
        self.assertFalse(shown_error_label(self.window).isVisibleTo(self.window))
        self.send_error(SID, ERROR_TEXT)
        self.assertTrue(shown_error_label(self.window).isVisibleTo(self.window))
        self.assertEqual(shown_error_label(self.window).text(), ERROR_NAME)
        self.assertEqual(shown_error_label(self.window).toolTip(), ERROR_TEXT)
        # The call stays up
        self.assertFalse(self.window._call_ended)
        self.assertEqual(self.hangups, [])

    def test_mic_and_speaker_errors_both_show(self):
        speaker = 'Speaker error: Class not registered'
        self.send_error(SID, ERROR_TEXT)
        self.send_error(SID, speaker)
        self.assertEqual(shown_error_label(self.window).text(), 'Microphone error, Speaker error')

    def test_error_of_other_session_is_not_shown(self):
        self.send_error('other-session', ERROR_TEXT)
        self.assertFalse(shown_error_label(self.window).isVisibleTo(self.window))
        self.assertEqual(shown_error_label(self.window).text(), '')

    def test_camera_error_shows(self):
        self.send_error(SID, CAMERA_TEXT)
        self.assertEqual(shown_error_label(self.window).text(), CAMERA_NAME)


class TestCallErrorBeforeWindow(CallErrorBase):

    def test_error_before_window_shows_when_window_opens(self):
        self.start_call()
        self.send_error(SID, ERROR_TEXT)
        self.accept_call()
        self.assertTrue(shown_error_label(self.window).isVisibleTo(self.window))
        self.assertEqual(shown_error_label(self.window).text(), ERROR_NAME)
        self.assertEqual(self.manager.call_errors, {})

    def test_errors_before_and_after_window_show_once(self):
        self.start_call()
        self.send_error(SID, ERROR_TEXT)
        self.accept_call()
        self.send_error(SID, CAMERA_TEXT)
        self.assertEqual(shown_error_label(self.window).text(), f'{ERROR_NAME}, {CAMERA_NAME}')

    def test_errors_of_ended_call_are_dropped(self):
        self.start_call()
        self.send_error(SID, ERROR_TEXT)
        self.end_call(1, SID, 'decline')
        self.assertEqual(self.manager.call_errors, {})
        # A late error after the end is not kept
        self.send_error(SID, ERROR_TEXT)
        self.assertEqual(self.manager.call_errors, {})

    def test_error_of_unknown_session_is_not_kept(self):
        self.send_error('other-session', ERROR_TEXT)
        self.assertEqual(self.manager.call_errors, {})

    def test_end_on_own_other_account_keeps_error(self):
        # Account 1 calls account 2: account 2 rings with the same session id
        self.start_call()
        self.accounts[2].calls._incoming_calls.add(SID)
        self.manager.on_call_incoming(2, SID, 'alice@localhost', ['audio'])
        APP.processEvents()
        # The peer answers on its phone: account 2 ends its side
        self.end_call(2, SID, 'answered_elsewhere')
        for dialog in list(self.manager.incoming_call_dialogs.values()):
            dialog.close()
        # The camera error of account 1 comes after that
        self.send_error(SID, CAMERA_TEXT)
        self.assertEqual(self.manager.call_errors, {(1, SID): [CAMERA_TEXT]})
        self.accept_call()
        self.assertEqual(self.window.account_id, 1)
        self.assertEqual(shown_error_label(self.window).text(), CAMERA_NAME)
        self.assertEqual(self.manager.call_errors, {})
        # A later error shows once, and an error of account 2 does not show
        self.send_error(SID, ERROR_TEXT)
        self.send_error(SID, 'Speaker error: account 2', account_id=2)
        self.assertEqual(shown_error_label(self.window).text(), f'{CAMERA_NAME}, {ERROR_NAME}')
        self.assertEqual(self.manager.call_errors, {})


if __name__ == '__main__':
    unittest.main()
