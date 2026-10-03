#!/usr/bin/env python3
"""
Offline tests for displayed markers (XEP-0333) and received files.

Opening a 1:1 chat whose newest received item is a file sends the
displayed marker with the file's ID (message_id first, like text).
A group chat sends no marker. On the sender side, a displayed marker
for our sent file marks our sent messages up to that time as read.

Uses the real _send_displayed_markers, the real ReceiptHandler and the
real Database on a file in tmp/. A fake client records send_marker.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: <venv>/bin/python -m unittest tests/test_file_markers.py
"""

import os
import sys
import types
import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict clears and refills sys.modules on exit (crash in Qt otherwise)
from PySide6.QtWidgets import QApplication
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.chat_view.taps.messages import MessageDisplayWidget
    from siproxylin.services.receipt_handler import ReceiptHandler
    from siproxylin.db.database import Database
logging.disable(logging.NOTSET)

APP = QApplication.instance() or QApplication([])

REPO_TMP = Path(__file__).parent.parent / 'tmp'
OUR_JID = 'user@example.org'
PEER = 'peer@example.net'
ROOM = 'room@conference.example.net'
ACCOUNT = 1


class FileMarkerTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.tmp = tempfile.TemporaryDirectory(dir=str(REPO_TMP) if REPO_TMP.is_dir() else None)
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(db_path=Path(self.tmp.name) / 't.db')
        self.addCleanup(self.db.close)
        schema = (Path(__file__).parent.parent / 'siproxylin/db/schema.sql').read_text()
        self.db.connection.executescript(schema)
        self.db.execute("INSERT INTO account (id, bare_jid, enabled) VALUES (?, ?, 1)",
                        (ACCOUNT, OUR_JID))
        self.db.commit()
        self.client = mock.Mock()

    def conversation(self, jid=PEER, is_muc=False):
        jid_id = self.db.get_or_create_jid(jid)
        conv_id = self.db.get_or_create_conversation(ACCOUNT, jid_id, 1 if is_muc else 0)
        return jid_id, conv_id

    def add_text(self, direction, time, jid=PEER, is_muc=False, **ids):
        jid_id, conv_id = self.conversation(jid, is_muc)
        _, item = self.db.insert_message_atomic(
            ACCOUNT, jid_id, conv_id, direction, 1 if is_muc else 0, time, time,
            'hello', 0, 0, 0, **ids)
        return item

    def add_file(self, direction, time, jid=PEER, is_muc=False, **ids):
        jid_id, conv_id = self.conversation(jid, is_muc)
        _, item = self.db.insert_file_transfer_atomic(
            ACCOUNT, jid_id, conv_id, direction, time, time, 'photo.jpg', None,
            'image/jpeg', 100, 2, 0, 0, 0, url='https://upload.example.net/photo.jpg', **ids)
        return item

    def read_up_to(self, jid=PEER, is_muc=False):
        _, conv_id = self.conversation(jid, is_muc)
        return self.db.fetchone("SELECT read_up_to_item FROM conversation WHERE id = ?",
                                (conv_id,))['read_up_to_item']

    def run_markers(self, jid=PEER, is_muc=False):
        widget = types.SimpleNamespace(
            current_account_id=ACCOUNT, current_jid=jid, current_is_muc=is_muc, db=self.db,
            account_manager=mock.Mock(**{'get_account.return_value': mock.Mock(client=self.client)}),
            parent=None)
        MessageDisplayWidget._send_displayed_markers(widget)

    def test_newest_file_sends_marker_with_message_id(self):
        self.add_text(0, 100, message_id='t1', origin_id='t1-origin')
        item = self.add_file(0, 200, message_id='f1', origin_id='f1-origin', stanza_id='f1-stanza')
        self.run_markers()
        self.client.send_marker.assert_called_once_with(PEER, 'f1', 'displayed')
        self.assertEqual(self.read_up_to(), item)
        # Nothing new: no second marker
        self.run_markers()
        self.client.send_marker.assert_called_once()

    def test_file_without_message_id_uses_origin_id(self):
        self.add_file(0, 200, origin_id='f1-origin', stanza_id='f1-stanza')
        self.run_markers()
        self.client.send_marker.assert_called_once_with(PEER, 'f1-origin', 'displayed')

    def test_newest_text_after_file_sends_text_id(self):
        self.add_file(0, 100, message_id='f1')
        self.add_text(0, 200, message_id='t1')
        self.run_markers()
        self.client.send_marker.assert_called_once_with(PEER, 't1', 'displayed')

    def test_markers_off_no_marker_for_file(self):
        item = self.add_file(0, 200, message_id='f1')
        self.db.execute("UPDATE conversation SET send_marker = 0")
        self.db.commit()
        self.run_markers()
        self.client.send_marker.assert_not_called()
        self.assertEqual(self.read_up_to(), item)

    def test_group_chat_file_no_marker(self):
        item = self.add_file(0, 200, jid=ROOM, is_muc=True, message_id='f1')
        self.run_markers(jid=ROOM, is_muc=True)
        self.client.send_marker.assert_not_called()
        self.assertEqual(self.read_up_to(jid=ROOM, is_muc=True), item)

    def test_marker_for_sent_file_marks_sent_messages_read(self):
        self.add_text(1, 100, message_id='s1', origin_id='s1')
        self.add_file(1, 200, origin_id='sf1')
        self.add_text(1, 300, message_id='s2', origin_id='s2')
        handler = ReceiptHandler(self.db)
        self.assertTrue(handler.on_displayed_marker(ACCOUNT, PEER, 'sf1'))
        rows = self.db.fetchall("SELECT origin_id, marked FROM message ORDER BY time")
        self.assertEqual([tuple(r) for r in rows], [('s1', 7), ('s2', 0)])

    def test_marker_for_unknown_id(self):
        self.add_text(1, 100, message_id='s1', origin_id='s1')
        handler = ReceiptHandler(self.db)
        self.assertFalse(handler.on_displayed_marker(ACCOUNT, PEER, 'nothing'))


if __name__ == '__main__':
    unittest.main()
