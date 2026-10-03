#!/usr/bin/env python3
"""
Offline tests for the duplicate check of file_transfer rows.

A plain file we send has no origin-id element. Our sent row keeps the id
in origin_id, the archive copy has it only in message_id. The MAM
catch-up must not store this copy a second time.

Uses the real Database on a file in tmp/.

Run with: <venv>/bin/python -m unittest tests/test_file_dedup.py
"""

import os
import sys
import logging
import tempfile
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from siproxylin.db.database import Database

REPO_TMP = Path(__file__).parent.parent / 'tmp'
OUR_JID = 'user@example.org'
PEER = 'peer@example.net'
ACCOUNT = 1


class FileDedupTests(unittest.TestCase):

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
        self.jid_id = self.db.get_or_create_jid(PEER)
        self.conv_id = self.db.get_or_create_conversation(ACCOUNT, self.jid_id, 0)

    def add_file(self, direction=1, **ids):
        ft_id, _ = self.db.insert_file_transfer_atomic(
            account_id=ACCOUNT, counterpart_id=self.jid_id, conversation_id=self.conv_id,
            direction=direction, time=100, local_time=100, file_name='a.txt', path=None,
            mime_type='text/plain', size=None, state=2, encryption=0, provider=0,
            is_carbon=0, url='https://upload.example.org/a.txt', **ids)
        return ft_id

    def count(self):
        return self.db.fetchone("SELECT COUNT(*) AS n FROM file_transfer")['n']

    def test_archive_copy_with_message_id_is_duplicate(self):
        """Sent row has origin_id X; archive copy has message_id X only."""
        self.assertIsNotNone(self.add_file(origin_id='X'))
        self.assertIsNone(self.add_file(message_id='X', stanza_id='arch-1'))
        self.assertEqual(self.count(), 1)

    def test_copy_with_origin_id_matches_stored_message_id(self):
        """Stored row has message_id X only; new copy has origin_id X."""
        self.assertIsNotNone(self.add_file(direction=0, message_id='X', stanza_id='arch-1'))
        self.assertIsNone(self.add_file(direction=0, origin_id='X'))
        self.assertEqual(self.count(), 1)

    def test_other_id_is_stored(self):
        self.assertIsNotNone(self.add_file(origin_id='X'))
        self.assertIsNotNone(self.add_file(message_id='Y', stanza_id='arch-2'))
        self.assertEqual(self.count(), 2)

    def test_same_origin_id_still_duplicate(self):
        """Sent OMEMO file: the archive copy carries the origin-id."""
        self.assertIsNotNone(self.add_file(origin_id='X'))
        self.assertIsNone(self.add_file(message_id='X', origin_id='X', stanza_id='arch-1'))
        self.assertEqual(self.count(), 1)


if __name__ == '__main__':
    unittest.main()
