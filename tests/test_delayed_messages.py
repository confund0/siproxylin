#!/usr/bin/env python3
"""
Offline tests for live 1:1 messages with a delay stamp (XEP-0203).

Offline delivery or a resend (Dino, our own retry) adds a delay stamp.
DrunkXMPP then sets is_history. The message must still notify the GUI
(message_received with is_marker False), once only. A sent carbon with a
delay stamp must not notify. The same for a file message (OOB URL).

Group chat: a live resend keeps the sender's delay stamp (no from). It
must notify and store the sender's time. Join history has a delay with
from=room JID: it must not notify.

The stanza goes through DrunkXMPP's handler into MessageBarrel's
_on_private_message (and FileBarrel for files), with the real Database
on a file in tmp/.

No network, no PySide6.

Run with: <venv>/bin/python -m unittest tests/test_delayed_messages.py
"""

import sys
import types
import asyncio
import logging
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slixmpp.stanza import Message

from drunk_xmpp.client import DrunkXMPP
from siproxylin.db.database import Database

# siproxylin.core/__init__ imports the GUI side; load only the barrel modules
_core = types.ModuleType('siproxylin.core')
_core.__path__ = [str(Path(__file__).parent.parent / 'siproxylin' / 'core')]
with mock.patch.dict(sys.modules, {'siproxylin.core': _core}):
    from siproxylin.core.barrels.messages import MessageBarrel
    from siproxylin.core.barrels.files import FileBarrel

REPO_TMP = Path(__file__).parent.parent / 'tmp'
OUR_JID = 'user@example.org'
PEER = 'peer@example.net'
ACCOUNT = 1
STAMP = '2026-10-01T10:00:00Z'
STAMP_TS = int(datetime(2026, 10, 1, 10, 0, tzinfo=timezone.utc).timestamp())
FILE_URL = 'https://upload.example.net/abc/photo.jpg'


def stanza(xml: str) -> Message:
    return Message(xml=ET.fromstring(xml))


class DelayedPrivateMessageTests(unittest.TestCase):

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
        self.emits = []

    def run_stanzas(self, xmls, carbon_sent=False):
        async def body():
            signals = {'message_received': types.SimpleNamespace(
                emit=lambda *a: self.emits.append(a))}
            barrel = MessageBarrel.__new__(MessageBarrel)
            barrel.account_id = ACCOUNT
            barrel.db = self.db
            barrel.logger = None
            barrel.signals = signals
            barrel.receipt_handler = None
            barrel.files_barrel = FileBarrel(ACCOUNT, None, self.db, None, signals,
                                             {'proxy_type': None})
            client = DrunkXMPP(
                jid=OUR_JID + '/test', password='secret', rooms={}, enable_omemo=False,
                on_private_message_callback=barrel._on_private_message,
            )
            barrel.client = client
            for xml in xmls:
                if carbon_sent:
                    await client._on_carbon_sent({'carbon_sent': stanza(xml)})
                else:
                    await client._on_private_message(stanza(xml))

        asyncio.run(body())

    def rows(self):
        return [tuple(r) for r in self.db.fetchall("SELECT direction, time, body FROM message")]

    def file_rows(self):
        return [tuple(r) for r in self.db.fetchall("SELECT direction, time, url FROM file_transfer")]

    def test_delayed_incoming_notifies(self):
        self.run_stanzas([
            f'<message xmlns="jabber:client" type="chat" from="{PEER}/phone" to="{OUR_JID}/test" id="m1">'
            f'<body>hello</body><delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/></message>'])
        self.assertEqual(self.emits, [(ACCOUNT, PEER, False)])
        self.assertEqual(self.rows(), [(0, STAMP_TS, 'hello')])

    def test_delayed_duplicate_notifies_once(self):
        xml = (f'<message xmlns="jabber:client" type="chat" from="{PEER}/phone" to="{OUR_JID}/test" id="m1">'
               f'<body>hello</body><delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/></message>')
        self.run_stanzas([xml, xml])
        self.assertEqual(self.emits, [(ACCOUNT, PEER, False)])
        self.assertEqual(len(self.rows()), 1)

    def test_delayed_sent_carbon_no_notify(self):
        self.run_stanzas([
            f'<message xmlns="jabber:client" type="chat" from="{OUR_JID}/other" to="{PEER}" id="c1">'
            f'<body>hi</body><delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/></message>'],
            carbon_sent=True)
        self.assertEqual(self.emits, [])
        self.assertEqual(self.rows(), [(1, STAMP_TS, 'hi')])

    def test_delayed_file_duplicate_notifies_once(self):
        xml = (f'<message xmlns="jabber:client" type="chat" from="{PEER}/phone" to="{OUR_JID}/test" id="f1">'
               f'<body>{FILE_URL}</body><x xmlns="jabber:x:oob"><url>{FILE_URL}</url></x>'
               f'<delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/></message>')
        self.run_stanzas([xml])
        # FileBarrel refreshes the chat (True), then the new-message signal (False)
        self.assertEqual(self.emits, [(ACCOUNT, PEER, True), (ACCOUNT, PEER, False)])
        self.assertEqual(self.file_rows(), [(0, STAMP_TS, FILE_URL)])
        self.assertEqual(self.rows(), [])

        self.emits.clear()
        self.run_stanzas([xml])
        self.assertEqual(self.emits, [])
        self.assertEqual(len(self.file_rows()), 1)


ROOM = 'room@conference.example.net'


class DelayedGroupchatMessageTests(unittest.TestCase):

    setUp = DelayedPrivateMessageTests.setUp
    rows = DelayedPrivateMessageTests.rows

    def run_stanzas(self, xmls):
        async def body():
            signals = {'message_received': types.SimpleNamespace(
                emit=lambda *a: self.emits.append(a))}
            barrel = MessageBarrel.__new__(MessageBarrel)
            barrel.account_id = ACCOUNT
            barrel.db = self.db
            barrel.logger = None
            barrel.signals = signals
            barrel.receipt_handler = None
            client = DrunkXMPP(
                jid=OUR_JID + '/test', password='secret',
                rooms={ROOM: {'nick': 'me'}}, enable_omemo=False,
                on_message_callback=barrel._on_message,
            )
            barrel.client = client
            for xml in xmls:
                await client._on_groupchat_message(stanza(xml))

        asyncio.run(body())

    def test_live_resend_notifies(self):
        self.run_stanzas([
            f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/peer" to="{OUR_JID}/test" id="g1">'
            f'<body>resent</body><delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/></message>'])
        self.assertEqual(self.emits, [(ACCOUNT, ROOM, False)])
        self.assertEqual(self.rows(), [(0, STAMP_TS, 'resent')])

    def test_join_history_no_notify(self):
        self.run_stanzas([
            f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/peer" to="{OUR_JID}/test" id="g2">'
            f'<body>old</body><delay xmlns="urn:xmpp:delay" stamp="{STAMP}"/>'
            f'<delay xmlns="urn:xmpp:delay" stamp="2026-10-02T10:00:00Z" from="{ROOM}"/></message>'])
        self.assertEqual(self.emits, [])
        self.assertEqual(len(self.rows()), 1)


if __name__ == '__main__':
    unittest.main()
