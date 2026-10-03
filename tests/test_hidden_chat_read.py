#!/usr/bin/env python3
"""
Offline test: a chat hidden behind the home page is not marked read.

The home page keeps the last chat loaded (for the draft). Window activation
(MainWindow.changeEvent) then calls mark_read_if_seen for that chat. It must
send no displayed marker and keep read_up_to_item while the home page is
shown. Opening the chat again (ChatViewWidget.load_conversation) marks it.

Real path: a plain MainWindow shell with the real ChatViewWidget (real
stack, MessageDisplayWidget and ScrollManager) and a real database in a
temp directory. MainWindow._on_home_requested switches to the home page.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: <venv>/bin/python -m unittest tests/test_hidden_chat_read.py
"""

import os
import sys
import time
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict clears and refills sys.modules on exit, which breaks
# the lazy loading of PySide6 classes imported inside the block (crash in Qt)
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.main_window import MainWindow
    from siproxylin.gui.chat_view import chat_view as chat_view_mod
    from siproxylin.gui.chat_view.taps import messages as messages_mod
    from siproxylin.db.database import Database
logging.disable(logging.NOTSET)

from tests.test_carbon_markers import ACCOUNT, PEER

APP = QApplication.instance() or QApplication([])


class HiddenChatTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Database(Path(tmp.name) / 'test.db')
        self.db.initialize()
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO account (id, bare_jid, enabled) VALUES (?, 'me@example.org', 1)",
                        (ACCOUNT,))
        self.jid_id = self.db.get_or_create_jid(PEER)
        self.conv = self.db.get_or_create_conversation(ACCOUNT, self.jid_id, 0)
        self.time0 = int(time.time()) - 5000
        self.count = 0
        for _ in range(50):
            self.add()

        self.client = mock.Mock()
        account_manager = mock.Mock(**{'get_account.return_value': mock.Mock(client=self.client)})

        # Deferred calls (scroll to bottom after load) run at once
        patcher = mock.patch.object(messages_mod, 'QTimer')
        timer = patcher.start()
        self.addCleanup(patcher.stop)
        timer.singleShot.side_effect = lambda msec, fn: fn()

        # Plain QMainWindow setup only (MainWindow.__init__ starts the whole app)
        self.win = MainWindow.__new__(MainWindow)
        super(MainWindow, self.win).__init__()
        self.addCleanup(self.win.deleteLater)
        self.win.isActiveWindow = lambda: True
        self.win.isMinimized = lambda: False
        self.win.contact_list = mock.Mock()
        with mock.patch.object(chat_view_mod, 'get_db', return_value=self.db), \
                mock.patch.object(chat_view_mod, 'get_account_manager', return_value=account_manager):
            self.chat_view = chat_view_mod.ChatViewWidget(parent=self.win)
        self.win.chat_view = self.chat_view
        self.win.setCentralWidget(self.chat_view)
        self.win.resize(600, 800)

        self.chat_view.load_conversation(ACCOUNT, PEER)
        APP.processEvents()
        self.assertEqual(self.chat_view.stack.currentIndex(), 1)
        self.assertTrue(self.chat_view.scroll_manager.is_at_bottom())

    def add(self):
        """Add a received message; returns its content item ID."""
        self.count += 1
        _, item_id = self.db.insert_message_atomic(
            ACCOUNT, self.jid_id, self.conv, 0, 0, self.time0 + self.count, self.time0 + self.count,
            f'message {self.count}', 0, 0, 0, message_id=f'm{self.count}')
        self.db.commit()
        return item_id

    def read_up_to(self):
        return self.db.fetchone("SELECT read_up_to_item FROM conversation WHERE id = ?",
                                (self.conv,))['read_up_to_item']

    def markers(self):
        return self.client.send_marker.call_count

    def go_home_and_activate(self):
        self.win._on_home_requested()
        self.assertEqual(self.chat_view.stack.currentIndex(), 0)
        self.assertEqual(self.chat_view.current_jid, PEER)  # chat stays loaded (draft)
        self.win.changeEvent(QEvent(QEvent.ActivationChange))

    def test_home_page_activation_no_marker(self):
        before_markers = self.markers()
        before = self.read_up_to()
        self.add()
        self.go_home_and_activate()
        self.assertEqual(self.markers(), before_markers)
        self.assertEqual(self.read_up_to(), before)

    def test_open_again_marks(self):
        item = self.add()
        self.go_home_and_activate()
        before_markers = self.markers()
        # Back to the chat: the real open path
        self.chat_view.load_conversation(ACCOUNT, PEER)
        APP.processEvents()
        self.assertEqual(self.markers(), before_markers + 1)
        self.assertEqual(self.read_up_to(), item)


if __name__ == '__main__':
    unittest.main()
