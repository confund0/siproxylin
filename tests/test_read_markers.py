#!/usr/bin/env python3
"""
Offline tests for displayed markers and the read state in the GUI.

Covers: a new message in the open chat sends displayed markers only while
the window is active and not minimized (roster_manager), the main window
sends them when it becomes active (changeEvent), and _send_displayed_markers
moves read_up_to_item also with markers off and to the highest item ID.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: <venv>/bin/python -m unittest tests/test_read_markers.py
"""

import os
import sys
import types
import logging
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication
    from siproxylin.gui.main_window import MainWindow
    from siproxylin.gui.managers.roster_manager import RosterManager
    from siproxylin.gui.chat_view.taps.messages import MessageDisplayWidget
logging.disable(logging.NOTSET)

from tests.test_carbon_markers import FakeDB, ACCOUNT, PEER

APP = QApplication.instance() or QApplication([])


class RosterManagerTests(unittest.TestCase):

    def run_message(self, active, minimized, is_marker=False):
        rm = RosterManager.__new__(RosterManager)
        rm.chat_view = mock.Mock(current_account_id=ACCOUNT, current_jid=PEER)
        rm.main_window = mock.Mock()
        rm.main_window.isActiveWindow.return_value = active
        rm.main_window.isMinimized.return_value = minimized
        rm.contact_list = mock.Mock()
        rm.notification_manager = mock.Mock()
        rm.on_message_received(ACCOUNT, PEER, is_marker)
        return rm

    def test_active_window_sends_markers_no_notification(self):
        rm = self.run_message(True, False)
        rm.chat_view.refresh.assert_called_once_with(send_markers=True)
        rm.notification_manager.send_message_notification.assert_not_called()

    def test_background_window_no_markers_notifies(self):
        rm = self.run_message(False, False)
        rm.chat_view.refresh.assert_called_once_with(send_markers=False)
        rm.notification_manager.send_message_notification.assert_called_once_with(ACCOUNT, PEER)

    def test_minimized_window_no_markers(self):
        rm = self.run_message(True, True)
        rm.chat_view.refresh.assert_called_once_with(send_markers=False)
        rm.notification_manager.send_message_notification.assert_called_once()


class MainWindowChangeEventTests(unittest.TestCase):

    def make_window(self, active, minimized, jid=PEER):
        # Plain QMainWindow setup only (MainWindow.__init__ starts the whole app)
        win = MainWindow.__new__(MainWindow)
        super(MainWindow, win).__init__()
        win.isActiveWindow = lambda: active
        win.isMinimized = lambda: minimized
        win.chat_view = mock.Mock(current_jid=jid)
        self.addCleanup(win.deleteLater)
        return win

    def send(self, win, event_type):
        win.changeEvent(QEvent(event_type))
        return win.chat_view.message_widget._send_displayed_markers

    def test_activation_sends_markers(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.ActivationChange).assert_called_once()

    def test_restore_sends_markers(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.WindowStateChange).assert_called_once()

    def test_inactive_or_minimized_or_no_chat_sends_nothing(self):
        for args in ((False, False), (True, True), (True, False, None)):
            win = self.make_window(*args)
            self.send(win, QEvent.ActivationChange).assert_not_called()

    def test_other_event_sends_nothing(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.FontChange).assert_not_called()

    def test_no_chat_view_yet(self):
        win = MainWindow.__new__(MainWindow)
        super(MainWindow, win).__init__()
        self.addCleanup(win.deleteLater)
        win.isActiveWindow = lambda: True
        win.isMinimized = lambda: False
        win.changeEvent(QEvent(QEvent.ActivationChange))  # no exception


class SendDisplayedMarkersTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.db = FakeDB()
        self.db.update_conversation_read_up_to = lambda conv_id, item_id: self.db.execute(
            "UPDATE conversation SET read_up_to_item = ? WHERE id = ?", (item_id, conv_id))
        self.client = mock.Mock()
        self.widget = types.SimpleNamespace(
            current_account_id=ACCOUNT, current_jid=PEER, current_is_muc=False, db=self.db,
            account_manager=mock.Mock(**{'get_account.return_value': mock.Mock(client=self.client)}),
            parent=None)

    def run_markers(self):
        MessageDisplayWidget._send_displayed_markers(self.widget)

    def test_read_up_to_is_highest_id_marker_to_newest_by_time(self):
        newest = self.db.add(0, 300, 0, message_id='p3')
        self.db.add(0, 100, 0, message_id='p1')
        highest = self.db.add(0, 200, 0, message_id='p2')
        self.run_markers()
        self.client.send_marker.assert_called_once_with(PEER, 'p3', 'displayed')
        self.assertEqual(self.db.read_up_to(), highest)
        self.assertLess(newest, highest)
        # Nothing new: no second marker
        self.run_markers()
        self.client.send_marker.assert_called_once()

    def test_markers_off_still_moves_read_up_to(self):
        item = self.db.add(0, 100, 0, message_id='p1')
        self.db.execute("UPDATE conversation SET send_marker = 0")
        self.run_markers()
        self.client.send_marker.assert_not_called()
        self.assertEqual(self.db.read_up_to(), item)


if __name__ == '__main__':
    unittest.main()
