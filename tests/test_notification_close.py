#!/usr/bin/env python3
"""
Offline test: when the unread count of a chat goes to 0 (read here or on
another device), the app closes the OS notification of that chat.

The real path runs: ContactListWidget.update_unread_indicators ->
NotificationService.dismiss_notification -> gdbus CloseNotification.
Only subprocess.run (the bus call) is faked.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_notification_close.py
"""

import os
import sys
import logging
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QTreeWidget, QTreeWidgetItem
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.contact_list import ContactListWidget
    from siproxylin.gui.models import ContactDisplayData, AccountDisplayData
    from siproxylin.services import notification
logging.disable(logging.NOTSET)

APP = QApplication.instance() or QApplication([])

ACC = 1
ALICE = 'alice@localhost'
BOB = 'bob@localhost'


class NotificationCloseTests(unittest.TestCase):

    def setUp(self):
        # Real notification service on "Linux", with a fake DB
        with mock.patch.object(notification, 'get_db', return_value=mock.Mock()):
            self.service = notification.NotificationService()
        self.service.system = 'Linux'
        patcher = mock.patch.object(notification, '_notification_service', self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Fake bus call: gdbus succeeds; the event tells the test that it ran
        self.called = threading.Event()

        def fake_run(cmd, **kwargs):
            self.called.set()
            return mock.Mock(returncode=0, stdout='()\n', stderr='')

        self.run = mock.Mock(side_effect=fake_run)
        patcher = mock.patch.object(notification.subprocess, 'run', self.run)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Only the parts of ContactListWidget that update_unread_indicators uses
        self.w = ContactListWidget.__new__(ContactListWidget)
        QWidget.__init__(self.w)
        self.addCleanup(self.w.deleteLater)
        self.w.contact_tree = QTreeWidget()
        self.w._update_item_from_data = mock.Mock()
        self.w._update_account_item = mock.Mock()
        self.unread = {ALICE: 0, BOB: 0}
        self.w.db = mock.Mock()
        self.w.db.get_unread_conversations_for_account.side_effect = lambda acc_id: [
            {'jid': jid, 'unread_count': n} for jid, n in self.unread.items() if n > 0]
        self.w.db.get_total_unread_for_account.side_effect = lambda acc_id: sum(self.unread.values())

        account_item = QTreeWidgetItem(self.w.contact_tree, ['account'])
        account_item.setData(0, Qt.UserRole, AccountDisplayData(account_id=ACC, bare_jid='me@localhost', name='me'))
        for jid in (ALICE, BOB):
            item = QTreeWidgetItem(account_item, [jid])
            item.setData(0, Qt.UserRole, ContactDisplayData(jid=jid, name=jid, account_id=ACC, item_type='contact'))

    def wait_close(self):
        """Wait for the close thread; True if the bus call ran."""
        return self.called.wait(2)

    def close_cmd_ids(self):
        """Notification ids sent to CloseNotification."""
        ids = []
        for call in self.run.call_args_list:
            cmd = call.args[0]
            self.assertEqual(cmd[0], 'gdbus')
            self.assertIn('org.freedesktop.Notifications.CloseNotification', cmd)
            ids.append(cmd[-1])
        return ids

    def test_read_closes_notification(self):
        # Alice wrote; a notification is open
        self.service.chat_notification_ids[(ACC, ALICE)] = 42
        self.unread[ALICE] = 2
        self.w.update_unread_indicators(ACC, ALICE)
        self.assertFalse(self.called.wait(0.2))

        # Chat read on another device: unread goes to 0
        self.unread[ALICE] = 0
        self.w.update_unread_indicators(ACC, ALICE)
        self.assertTrue(self.wait_close())
        self.assertEqual(self.close_cmd_ids(), ['42'])
        self.assertNotIn((ACC, ALICE), self.service.chat_notification_ids)

        # A later update does not close again
        self.called.clear()
        self.w.update_unread_indicators(ACC, ALICE)
        self.assertFalse(self.called.wait(0.2))
        self.assertEqual(self.run.call_count, 1)

    def test_unread_left_keeps_notification(self):
        self.service.chat_notification_ids[(ACC, ALICE)] = 42
        self.unread[ALICE] = 1
        self.w.update_unread_indicators(ACC, None)
        self.assertFalse(self.called.wait(0.2))
        self.run.assert_not_called()
        self.assertEqual(self.service.chat_notification_ids[(ACC, ALICE)], 42)

    def test_no_notification_no_call(self):
        self.w.update_unread_indicators(ACC, None)
        self.assertFalse(self.called.wait(0.2))
        self.run.assert_not_called()

    def test_account_update_closes_only_read_chat(self):
        # Alice read, Bob still unread
        self.service.chat_notification_ids[(ACC, ALICE)] = 42
        self.service.chat_notification_ids[(ACC, BOB)] = 43
        self.unread[BOB] = 3
        self.w.update_unread_indicators(ACC, None)
        self.assertTrue(self.wait_close())
        self.assertEqual(self.close_cmd_ids(), ['42'])
        self.assertEqual(self.service.chat_notification_ids, {(ACC, BOB): 43})

    def test_no_gdbus_falls_back_without_error(self):
        # No gdbus binary: the close falls back to notify-send and never raises
        self.service.chat_notification_ids[(ACC, ALICE)] = 42
        done = threading.Event()

        def fake_run(cmd, **kwargs):
            if cmd[0] == 'gdbus':
                raise FileNotFoundError('gdbus')
            done.set()
            return mock.Mock(returncode=0, stdout='', stderr='')

        self.run.side_effect = fake_run
        self.w.update_unread_indicators(ACC, ALICE)
        self.assertTrue(done.wait(2))
        cmd = self.run.call_args_list[-1].args[0]
        self.assertEqual(cmd[:5], ['notify-send', '-a', 'DRUNK-XMPP', '-r', '42'])
        self.assertNotIn((ACC, ALICE), self.service.chat_notification_ids)


if __name__ == '__main__':
    unittest.main()
