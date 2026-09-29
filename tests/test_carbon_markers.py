#!/usr/bin/env python3
"""
Offline tests for receipts and chat markers of carbons (messages sent from
our other device) and the MAM duplicate check before a resend.

Covers: receipt handler lookup by message_id with direction 1, receipts and
markers inside received carbons, MAM marker entries (mam.py), applying them
in the MAM catch-up without storing them, and message_retry iterating the
retrieve_history pages.

No network, no PySide6: Qt is replaced by a small stub where needed.

Run with: <venv>/bin/python -m unittest tests/test_carbon_markers.py
"""

import sys
import types
import asyncio
import logging
import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slixmpp.stanza import Message

from drunk_xmpp.client import DrunkXMPP
from siproxylin.services.receipt_handler import ReceiptHandler

# siproxylin.core/__init__ imports the GUI side; load only the barrel module
_core = types.ModuleType('siproxylin.core')
_core.__path__ = [str(Path(__file__).parent.parent / 'siproxylin' / 'core')]
with mock.patch.dict(sys.modules, {'siproxylin.core': _core}):
    from siproxylin.core.barrels.messages import MessageBarrel

# message_retry imports PySide6 (not in the repo venv)
_qtcore = types.ModuleType('PySide6.QtCore')
_qtcore.QObject = object
_qtcore.Signal = lambda *args, **kwargs: None
_pyside = types.ModuleType('PySide6')
_pyside.QtCore = _qtcore
with mock.patch.dict(sys.modules, {'PySide6': _pyside, 'PySide6.QtCore': _qtcore}):
    from siproxylin.services.message_retry import MessageRetryHandler

OUR_JID = 'user@example.org'
PEER = 'peer@example.net'
ROOM = 'room@muc.example.org'
ACCOUNT = 1


class FakeDB:
    """In-memory SQLite with the few columns these code paths use."""

    def __init__(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE jid (id INTEGER PRIMARY KEY, bare_jid TEXT);
            CREATE TABLE message (
                id INTEGER PRIMARY KEY, account_id INTEGER, counterpart_id INTEGER,
                direction INTEGER, time INTEGER, body TEXT, marked INTEGER,
                is_carbon INTEGER, message_id TEXT, origin_id TEXT, stanza_id TEXT);
            CREATE TABLE file_transfer (
                id INTEGER PRIMARY KEY, account_id INTEGER, counterpart_id INTEGER, stanza_id TEXT);
        """)
        self.conn.execute("INSERT INTO jid (id, bare_jid) VALUES (1, ?)", (PEER,))

    def execute(self, query, params=()):
        return self.conn.execute(query, params)

    def fetchone(self, query, params=()):
        return self.conn.execute(query, params).fetchone()

    def fetchall(self, query, params=()):
        return self.conn.execute(query, params).fetchall()

    def commit(self):
        self.conn.commit()

    def get_or_create_conversation(self, account_id, jid_id, conv_type):
        return 1

    def insert_message_atomic(self, account_id, counterpart_id, conversation_id, direction,
                              msg_type, time, local_time, body, encryption, marked,
                              is_carbon, message_id, origin_id, stanza_id, **kwargs):
        cur = self.conn.execute(
            "INSERT INTO message (account_id, counterpart_id, direction, time, body, marked,"
            " is_carbon, message_id, origin_id, stanza_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (account_id, counterpart_id, direction, time, body, marked,
             is_carbon, message_id, origin_id, stanza_id))
        return cur.lastrowid, 1

    def add(self, direction, time, marked, message_id=None, origin_id=None, is_carbon=0):
        self.conn.execute(
            "INSERT INTO message (account_id, counterpart_id, direction, time, body, marked,"
            " is_carbon, message_id, origin_id) VALUES (?, 1, ?, ?, 'x', ?, ?, ?, ?)",
            (ACCOUNT, direction, time, marked, is_carbon, message_id, origin_id))

    def marked(self, message_id):
        row = self.fetchone("SELECT marked FROM message WHERE message_id = ? OR origin_id = ?",
                            (message_id, message_id))
        return row['marked']


def stanza(xml: str) -> Message:
    return Message(xml=ET.fromstring(xml))


class ReceiptHandlerTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.db = FakeDB()
        self.rh = ReceiptHandler(self.db)

    def tearDown(self):
        logging.disable(logging.NOTSET)

    def test_receipt_matches_message_id_of_carbon(self):
        self.db.add(1, 100, 1, message_id='c1', is_carbon=1)
        self.assertTrue(self.rh.on_delivery_receipt(ACCOUNT, PEER, 'c1'))
        self.assertEqual(self.db.marked('c1'), 2)

    def test_receipt_still_matches_origin_id(self):
        self.db.add(1, 100, 1, message_id='m1', origin_id='o1')
        self.assertTrue(self.rh.on_delivery_receipt(ACCOUNT, PEER, 'o1'))
        self.assertEqual(self.db.marked('o1'), 2)

    def test_receipt_ignores_incoming_message(self):
        self.db.add(0, 100, 0, message_id='in1')
        self.assertFalse(self.rh.on_delivery_receipt(ACCOUNT, PEER, 'in1'))
        self.assertEqual(self.db.marked('in1'), 0)

    def test_receipt_does_not_lower_read(self):
        self.db.add(1, 100, 7, message_id='c1', is_carbon=1)
        self.assertFalse(self.rh.on_delivery_receipt(ACCOUNT, PEER, 'c1'))
        self.assertEqual(self.db.marked('c1'), 7)

    def test_displayed_matches_message_id_and_is_cumulative(self):
        self.db.add(1, 100, 1, message_id='c1', is_carbon=1)
        self.db.add(1, 200, 2, message_id='c2', is_carbon=1)
        self.db.add(1, 300, 1, message_id='c3', is_carbon=1)
        self.assertTrue(self.rh.on_displayed_marker(ACCOUNT, PEER, 'c2'))
        self.assertEqual(self.db.marked('c1'), 7)
        self.assertEqual(self.db.marked('c2'), 7)
        self.assertEqual(self.db.marked('c3'), 1)

    def test_displayed_ignores_incoming_message(self):
        self.db.add(1, 100, 1, message_id='c1', is_carbon=1)
        self.db.add(0, 200, 0, message_id='in1')
        self.assertFalse(self.rh.on_displayed_marker(ACCOUNT, PEER, 'in1'))
        self.assertEqual(self.db.marked('c1'), 1)


class CarbonMarkerTests(unittest.TestCase):
    """Receipts and markers inside received carbons go to the marker callbacks."""

    def run_carbon(self, inner_xml):
        receipts, markers, messages = [], [], []

        async def on_private(*args):
            messages.append(args)

        async def body():
            client = DrunkXMPP(
                jid=OUR_JID + '/test', password='secret', rooms={}, enable_omemo=False,
                on_receipt_received_callback=lambda *a: receipts.append(a),
                on_marker_received_callback=lambda *a: markers.append(a),
                on_private_message_callback=on_private,
            )
            await client._on_carbon_received({'carbon_received': stanza(inner_xml)})

        logging.disable(logging.CRITICAL)
        try:
            asyncio.run(body())
        finally:
            logging.disable(logging.NOTSET)
        return receipts, markers, messages

    def test_receipt_in_carbon(self):
        receipts, markers, messages = self.run_carbon(
            f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}/other" id="r1">'
            '<received xmlns="urn:xmpp:receipts" id="c1"/></message>')
        self.assertEqual(receipts, [(PEER, 'c1')])
        self.assertEqual(markers, [])
        self.assertEqual(messages, [])

    def test_displayed_in_carbon(self):
        receipts, markers, messages = self.run_carbon(
            f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}/other" id="d1">'
            '<displayed xmlns="urn:xmpp:chat-markers:0" id="c1"/></message>')
        self.assertEqual(receipts, [])
        self.assertEqual(markers, [(PEER, 'c1', 'displayed')])
        self.assertEqual(messages, [])


class FakeMAM:
    """Replaces xep_0313.iterate(): yields fake MAM results."""

    def __init__(self, results):
        self.results = results

    async def iterate(self, **kwargs):
        for archive_id, msg in self.results:
            yield {'mam_result': {'id': archive_id, 'forwarded': {
                'stanza': msg,
                'delay': {'stamp': datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)},
            }}}


def mam_pages(query_jid, results, rooms=None):
    async def body():
        client = DrunkXMPP(jid=OUR_JID + '/test', password='secret', rooms={}, enable_omemo=False)
        if rooms:
            client.rooms.update(rooms)
        with mock.patch.object(client.plugin['xep_0313'], 'iterate', FakeMAM(results).iterate):
            return [page async for page in client.retrieve_history(jid=query_jid, with_jid=query_jid)]

    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(body())
    finally:
        logging.disable(logging.NOTSET)


class MAMMarkerEntryTests(unittest.TestCase):

    def test_peer_markers_become_entries_in_page_order(self):
        pages = mam_pages(PEER, [
            ('a1', stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="c1">'
                          '<body>hi</body></message>')),
            ('a2', stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}/other" id="r1">'
                          '<received xmlns="urn:xmpp:receipts" id="c1"/></message>')),
            ('a3', stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}/other" id="d1">'
                          '<displayed xmlns="urn:xmpp:chat-markers:0" id="c1"/></message>')),
        ])
        entries = [e for page in pages for e in page]
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0]['body'], 'hi')
        self.assertNotIn('marker_type', entries[0])
        self.assertEqual((entries[1]['marker_type'], entries[1]['marker_for_id']), ('received', 'c1'))
        self.assertEqual((entries[2]['marker_type'], entries[2]['marker_for_id']), ('displayed', 'c1'))
        self.assertEqual(entries[2]['jid'], PEER)
        self.assertEqual(entries[2]['archive_id'], 'a3')

    def test_own_markers_are_skipped(self):
        pages = mam_pages(PEER, [
            ('a1', stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="d1">'
                          '<displayed xmlns="urn:xmpp:chat-markers:0" id="p1"/></message>')),
        ])
        self.assertEqual(pages, [])

    def test_muc_markers_are_skipped(self):
        pages = mam_pages(ROOM, [
            ('a1', stanza(f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/bob" id="d1">'
                          '<displayed xmlns="urn:xmpp:chat-markers:0" id="x1"/></message>')),
        ], rooms={ROOM: {'nick': 'me'}})
        self.assertEqual(pages, [])


class MAMStoreTests(unittest.TestCase):

    def make_barrel(self, db):
        client = types.SimpleNamespace(boundjid=types.SimpleNamespace(bare=OUR_JID))
        barrel = MessageBarrel.__new__(MessageBarrel)
        barrel.account_id = ACCOUNT
        barrel.client = client
        barrel.db = db
        barrel.logger = None
        barrel.signals = {}
        barrel.receipt_handler = ReceiptHandler(db)
        barrel.files_barrel = None
        return barrel

    def test_markers_applied_not_stored(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db = FakeDB()
        barrel = self.make_barrel(db)
        ts = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
        carbon = stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="c1">'
                        '<body>hi</body></message>')
        page = [
            {'jid': OUR_JID, 'body': 'hi', 'timestamp': ts, 'is_encrypted': False,
             'archive_id': 'a1', 'message': carbon},
            {'marker_type': 'received', 'marker_for_id': 'c1', 'jid': PEER,
             'archive_id': 'a2', 'timestamp': ts},
            {'marker_type': 'displayed', 'marker_for_id': 'c1', 'jid': PEER,
             'archive_id': 'a3', 'timestamp': ts},
        ]
        result = asyncio.run(barrel._process_and_store_mam_messages(page, PEER, 1))
        self.assertEqual(result, (1, 0, 2))
        self.assertEqual(db.fetchone("SELECT COUNT(*) AS n FROM message")['n'], 1)
        self.assertEqual(db.marked('c1'), 7)

        # Second run (overlap): nothing new, markers change nothing
        result = asyncio.run(barrel._process_and_store_mam_messages(page, PEER, 1))
        self.assertEqual(result, (0, 0, 0))
        self.assertEqual(db.fetchone("SELECT COUNT(*) AS n FROM message")['n'], 1)


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.closed = False

    def retrieve_history(self, **kwargs):
        async def gen():
            try:
                for page in self.pages:
                    yield page
            finally:
                self.closed = True
        return gen()


class MessageRetryMAMTests(unittest.TestCase):

    def check(self, pages):
        handler = MessageRetryHandler.__new__(MessageRetryHandler)
        client = FakeClient(pages)
        msg = {'counterpart_jid': PEER, 'time': 1_790_000_000, 'origin_id': 'o1'}
        found = asyncio.run(handler._check_message_in_mam(msg, client, logging.getLogger('test')))
        return found, client

    def test_found_on_second_page(self):
        marker = {'marker_type': 'displayed', 'marker_for_id': 'o1', 'jid': PEER}
        other = {'message': stanza('<message xmlns="jabber:client" id="x1"/>')}
        own = {'message': stanza('<message xmlns="jabber:client" id="o1"/>')}
        found, client = self.check([[marker, other], [own]])
        self.assertTrue(found)
        self.assertTrue(client.closed)

    def test_not_found_marker_only(self):
        marker = {'marker_type': 'received', 'marker_for_id': 'o1', 'jid': PEER}
        found, _ = self.check([[marker]])
        self.assertFalse(found)


if __name__ == '__main__':
    unittest.main()
