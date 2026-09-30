#!/usr/bin/env python3
"""
Offline tests for receipts and chat markers of carbons (messages sent from
our other device) and the MAM duplicate check before a resend.

Covers: receipt handler lookup by message_id with direction 1, receipts and
markers inside received carbons, MAM marker entries (mam.py), applying them
in the MAM catch-up without storing them, and message_retry iterating the
retrieve_history pages. Also our own displayed markers from other devices
(sent carbons and MAM) and read_up_to_item, one notification per 1:1
catch-up, MUC MAM OMEMO decryption for senders who left the room, own
undecryptable messages for the resend check (include_own_ids), and no
second decryption of stored OMEMO messages in the catch-up (is_stored).

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

from copy import copy

from slixmpp.jid import JID
from slixmpp.stanza import Message

from drunk_xmpp.client import DrunkXMPP
from siproxylin.services.receipt_handler import ReceiptHandler

# siproxylin.core/__init__ imports the GUI side; load only the barrel module
_core = types.ModuleType('siproxylin.core')
_core.__path__ = [str(Path(__file__).parent.parent / 'siproxylin' / 'core')]
with mock.patch.dict(sys.modules, {'siproxylin.core': _core}):
    from siproxylin.core.barrels.messages import MessageBarrel
    from siproxylin.core.barrels.muc import MucBarrel

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
                id INTEGER PRIMARY KEY, account_id INTEGER, counterpart_id INTEGER,
                direction INTEGER, time INTEGER, message_id TEXT, origin_id TEXT, stanza_id TEXT);
            CREATE TABLE conversation (
                id INTEGER PRIMARY KEY, account_id INTEGER, jid_id INTEGER, type INTEGER,
                read_up_to_item INTEGER NOT NULL DEFAULT -1, send_marker INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE content_item (
                id INTEGER PRIMARY KEY, conversation_id INTEGER, time INTEGER,
                content_type INTEGER, foreign_id INTEGER, hide INTEGER NOT NULL DEFAULT 0);
        """)
        self.conn.execute("INSERT INTO jid (id, bare_jid) VALUES (1, ?)", (PEER,))
        self.conn.execute("INSERT INTO conversation (id, account_id, jid_id, type) VALUES (1, ?, 1, 0)",
                          (ACCOUNT,))

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
        item = self.conn.execute(
            "INSERT INTO content_item (conversation_id, time, content_type, foreign_id) VALUES (1, ?, 0, ?)",
            (time, cur.lastrowid))
        return cur.lastrowid, item.lastrowid

    def add(self, direction, time, marked, message_id=None, origin_id=None, is_carbon=0):
        """Add a message and its content item; returns the content item ID."""
        cur = self.conn.execute(
            "INSERT INTO message (account_id, counterpart_id, direction, time, body, marked,"
            " is_carbon, message_id, origin_id) VALUES (?, 1, ?, ?, 'x', ?, ?, ?, ?)",
            (ACCOUNT, direction, time, marked, is_carbon, message_id, origin_id))
        return self.conn.execute(
            "INSERT INTO content_item (conversation_id, time, content_type, foreign_id) VALUES (1, ?, 0, ?)",
            (time, cur.lastrowid)).lastrowid

    def add_file(self, direction, time, message_id=None):
        """Add a file transfer and its content item; returns the content item ID."""
        cur = self.conn.execute(
            "INSERT INTO file_transfer (account_id, counterpart_id, direction, time, message_id)"
            " VALUES (?, 1, ?, ?, ?)", (ACCOUNT, direction, time, message_id))
        return self.conn.execute(
            "INSERT INTO content_item (conversation_id, time, content_type, foreign_id) VALUES (1, ?, 2, ?)",
            (time, cur.lastrowid)).lastrowid

    def read_up_to(self):
        return self.fetchone("SELECT read_up_to_item FROM conversation WHERE id = 1")['read_up_to_item']

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

    def test_own_displayed_moves_read_up_to_by_time(self):
        # Item IDs are not in time order (catch-up stores older messages later)
        i1 = self.db.add(0, 300, 0, message_id='p3')
        i2 = self.db.add(0, 100, 0, message_id='p1')
        i3 = self.db.add(0, 200, 0, message_id='p2')
        self.db.add(1, 250, 1, message_id='c1')
        self.assertTrue(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'p2'))
        self.assertEqual(self.db.read_up_to(), i3)
        self.assertFalse(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'p3'))
        self.assertEqual(self.db.read_up_to(), i3)  # i3 > i1, never lowered
        self.assertFalse(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'p1'))
        self.assertEqual(self.db.read_up_to(), i3)
        self.assertLess(i1, i2)

    def test_own_displayed_matches_file_and_origin_id(self):
        f1 = self.db.add_file(0, 100, message_id='f1')
        self.assertTrue(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'f1'))
        self.assertEqual(self.db.read_up_to(), f1)
        i2 = self.db.add(0, 200, 0, message_id='m2', origin_id='o2')
        self.db.add(0, 200, 0, message_id='m3')
        self.assertTrue(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'o2'))
        self.assertEqual(self.db.read_up_to(), i2)  # same second: the item ID gives the order

    def test_own_displayed_ignores_own_message_and_unknown_id(self):
        self.db.add(1, 100, 1, message_id='c1')
        self.assertFalse(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'c1'))
        self.assertFalse(self.rh.on_own_displayed_marker(ACCOUNT, PEER, 'nope'))
        self.assertFalse(self.rh.on_own_displayed_marker(ACCOUNT, 'other@example.net', 'c1'))
        self.assertEqual(self.db.read_up_to(), -1)


class CarbonMarkerTests(unittest.TestCase):
    """Receipts and markers inside received carbons go to the marker callbacks."""

    def run_carbon(self, inner_xml, sent=False):
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
            if sent:
                await client._on_carbon_sent({'carbon_sent': stanza(inner_xml)})
            else:
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

    def test_own_displayed_in_sent_carbon(self):
        receipts, markers, messages = self.run_carbon(
            f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="d1">'
            '<displayed xmlns="urn:xmpp:chat-markers:0" id="p1"/></message>', sent=True)
        self.assertEqual(receipts, [])
        self.assertEqual(markers, [(PEER, 'p1', 'displayed_own')])
        self.assertEqual(messages, [])

    def test_sent_carbon_with_body_is_a_message(self):
        receipts, markers, messages = self.run_carbon(
            f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="c1">'
            '<body>hi</body><markable xmlns="urn:xmpp:chat-markers:0"/></message>', sent=True)
        self.assertEqual(markers, [])
        self.assertEqual(len(messages), 1)

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


class FakeOMEMO:
    """Replaces xep_0384: finds the sender like slixmpp_omemo and records it."""

    def __init__(self, xep_0045, can_decrypt):
        self.xep_0045 = xep_0045
        self.can_decrypt = can_decrypt  # bare JIDs whose messages decrypt
        self.senders = []

    def is_encrypted(self, msg):
        return msg.xml.find('{eu.siacs.conversations.axolotl}encrypted') is not None

    async def decrypt_message(self, msg):
        if msg['type'] == 'groupchat':
            real = self.xep_0045.get_jid_property(JID(msg['from'].bare), msg['from'].resource, 'jid')
            if real is None:
                raise ValueError(f"Couldn't find real JID of sender from groupchat JID {msg['from']}")
            sender = JID(real).bare
        else:
            sender = msg['from'].bare
        self.senders.append(sender)
        if sender not in self.can_decrypt:
            raise ValueError('MessageNotForUs')
        out = copy(msg)
        out['body'] = 'plain text'
        return out, types.SimpleNamespace(device_id=1)


def mam_pages_omemo(query_jid, results, occupants=None, can_decrypt=(), rooms=None, **kwargs):
    """mam_pages with OMEMO on. occupants: {nick: real JID} of the room now."""
    occupants = occupants or {}
    xep_0045 = mock.Mock()
    xep_0045.get_jid_property.side_effect = lambda room, nick, prop: occupants.get(nick)
    omemo = FakeOMEMO(xep_0045, set(can_decrypt))

    async def body():
        client = DrunkXMPP(jid=OUR_JID + '/test', password='secret', rooms={}, enable_omemo=False)
        client.omemo_enabled = True
        if rooms:
            client.rooms.update(rooms)
        plugins = {'xep_0313': FakeMAM(results), 'xep_0384': omemo, 'xep_0045': xep_0045}
        with mock.patch.object(client, 'plugin', plugins):
            return [page async for page in client.retrieve_history(jid=query_jid, **kwargs)]

    logging.disable(logging.CRITICAL)
    try:
        return [e for page in asyncio.run(body()) for e in page], omemo
    finally:
        logging.disable(logging.NOTSET)


ENC = ('<body>fallback</body><encrypted xmlns="eu.siacs.conversations.axolotl">'
       '<header sid="1"/></encrypted>')


class MAMOmemoTests(unittest.TestCase):

    def muc_msg(self, nick, real_jid=None, msg_id='g1'):
        item = (f'<x xmlns="http://jabber.org/protocol/muc#user"><item jid="{real_jid}/phone"/></x>'
                if real_jid else '')
        return stanza(f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/{nick}" id="{msg_id}">'
                      f'{ENC}{item}</message>')

    def test_muc_sender_in_room_uses_groupchat_path(self):
        entries, omemo = mam_pages_omemo(ROOM, [('a1', self.muc_msg('bob', 'bob@example.net'))],
                                         occupants={'bob': 'carl@example.net/x'},
                                         can_decrypt={'carl@example.net'}, rooms={ROOM: {'nick': 'me'}})
        self.assertEqual(omemo.senders, ['carl@example.net'])
        self.assertEqual(entries[0]['body'], 'plain text')

    def test_muc_sender_left_uses_archive_real_jid(self):
        msg = self.muc_msg('bob', 'bob@example.net')
        entries, omemo = mam_pages_omemo(ROOM, [('a1', msg)], can_decrypt={'bob@example.net'},
                                         rooms={ROOM: {'nick': 'me'}})
        self.assertEqual(omemo.senders, ['bob@example.net'])
        self.assertEqual(entries[0]['body'], 'plain text')
        self.assertEqual(entries[0]['nick'], 'bob')
        self.assertEqual(entries[0]['jid'], ROOM)
        # The archived stanza is not changed
        self.assertEqual(entries[0]['message']['type'], 'groupchat')
        self.assertEqual(str(entries[0]['message']['from']), f'{ROOM}/bob')

    def test_muc_sender_left_no_real_jid_fails_as_before(self):
        entries, omemo = mam_pages_omemo(ROOM, [('a1', self.muc_msg('bob'))],
                                         rooms={ROOM: {'nick': 'me'}})
        self.assertEqual(omemo.senders, [])
        self.assertEqual(entries[0]['body'], '[Failed to decrypt OMEMO message]')
        self.assertIsNone(msg_muc_element(entries[0]['message']))


def msg_muc_element(msg):
    return msg.xml.find('{http://jabber.org/protocol/muc#user}x')


class MAMOwnIdsTests(unittest.TestCase):

    def own_1to1(self):
        return stanza(f'<message xmlns="jabber:client" type="chat" from="{OUR_JID}/other" to="{PEER}" id="o1">'
                      f'{ENC}</message>')

    def test_own_1to1_skipped_by_default(self):
        entries, _ = mam_pages_omemo(PEER, [('a1', self.own_1to1())], with_jid=PEER)
        self.assertEqual(entries, [])

    def test_own_1to1_entry_with_include_own_ids(self):
        entries, _ = mam_pages_omemo(PEER, [('a1', self.own_1to1())], with_jid=PEER, include_own_ids=True)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertTrue(e['own_undecryptable'])
        self.assertEqual(e['message'].get('id'), 'o1')
        self.assertEqual((e['archive_id'], e['jid']), ('a1', OUR_JID))
        self.assertNotIn('body', e)

    def test_own_muc_reflection_entry_with_include_own_ids(self):
        refl = stanza(f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/me" id="o2">'
                      f'{ENC}</message>')
        rooms = {ROOM: {'nick': 'me'}}
        entries, _ = mam_pages_omemo(ROOM, [('a1', refl)], occupants={'me': OUR_JID + '/test'}, rooms=rooms)
        self.assertEqual(entries, [])
        entries, _ = mam_pages_omemo(ROOM, [('a1', refl)], occupants={'me': OUR_JID + '/test'},
                                     rooms=rooms, include_own_ids=True)
        self.assertEqual([(e['own_undecryptable'], e['message'].get('id')) for e in entries], [(True, 'o2')])


class MAMStoredTests(unittest.TestCase):
    """is_stored: stored OMEMO messages are skipped before decryption."""

    def peer_msg(self, body=ENC):
        return stanza(f'<message xmlns="jabber:client" type="chat" from="{PEER}/phone" to="{OUR_JID}" id="p1">'
                      f'<origin-id xmlns="urn:xmpp:sid:0" id="or1"/>{body}</message>')

    def test_stored_encrypted_not_decrypted(self):
        calls = []
        entries, omemo = mam_pages_omemo(PEER, [('a1', self.peer_msg())], can_decrypt={PEER}, with_jid=PEER,
                                         is_stored=lambda *ids: calls.append(ids) or True)
        self.assertEqual(entries, [])
        self.assertEqual(omemo.senders, [])
        self.assertEqual(calls, [('a1', 'or1', 'p1')])

    def test_not_stored_decrypted(self):
        entries, omemo = mam_pages_omemo(PEER, [('a1', self.peer_msg())], can_decrypt={PEER}, with_jid=PEER,
                                         is_stored=lambda *ids: False)
        self.assertEqual(omemo.senders, [PEER])
        self.assertEqual([e['body'] for e in entries], ['plain text'])

    def test_without_is_stored_unchanged(self):
        entries, omemo = mam_pages_omemo(PEER, [('a1', self.peer_msg())], can_decrypt={PEER}, with_jid=PEER)
        self.assertEqual(omemo.senders, [PEER])
        self.assertEqual([e['body'] for e in entries], ['plain text'])

    def test_plaintext_and_markers_not_checked(self):
        calls = []
        marker = stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}" id="d1">'
                        '<displayed xmlns="urn:xmpp:chat-markers:0" id="c1"/></message>')
        entries, _ = mam_pages_omemo(PEER, [('a1', self.peer_msg('<body>hi</body>')), ('a2', marker)],
                                     with_jid=PEER, is_stored=lambda *ids: calls.append(ids) or True)
        self.assertEqual(calls, [])
        self.assertEqual(entries[0]['body'], 'hi')
        self.assertEqual(entries[1]['marker_type'], 'displayed')

    def test_muc_stored_not_decrypted(self):
        calls = []
        msg = stanza(f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/bob" id="g1">{ENC}</message>')
        entries, omemo = mam_pages_omemo(ROOM, [('ra1', msg)], occupants={'bob': 'bob@example.net/x'},
                                         can_decrypt={'bob@example.net'}, rooms={ROOM: {'nick': 'me'}},
                                         is_stored=lambda *ids: calls.append(ids) or True)
        self.assertEqual((entries, omemo.senders), ([], []))
        self.assertEqual(calls, [('ra1', None, 'g1')])


class StoredCheckTests(unittest.TestCase):
    """The barrel is_stored checks follow the insert duplicate rules."""

    def barrels(self, db):
        msg = MessageBarrel.__new__(MessageBarrel)
        muc = MucBarrel.__new__(MucBarrel)
        for barrel in (msg, muc):
            barrel.account_id = ACCOUNT
            barrel.db = db
        return msg, muc

    def test_rules(self):
        db = FakeDB()
        db.execute("INSERT INTO message (account_id, counterpart_id, message_id, origin_id, stanza_id)"
                   " VALUES (?, 1, 'm1', 'o1', 's1')", (ACCOUNT,))
        db.execute("INSERT INTO file_transfer (account_id, counterpart_id, message_id, origin_id, stanza_id)"
                   " VALUES (?, 2, 'fm', 'fo', 'fs')", (ACCOUNT,))
        db.execute("INSERT INTO message (account_id, counterpart_id, message_id, origin_id, stanza_id)"
                   " VALUES (2, 1, 'x1', 'x2', 'x3')")
        cases = [
            (('s1', None, None), True), ((None, 'o1', None), True), ((None, None, 'm1'), True),
            (('fs', None, None), True), ((None, 'fo', None), True), ((None, None, 'fm'), True),
            (('new', 'new', 'm1'), True),  # any one ID is enough, like the insert
            (('m1', None, None), False),  # archive_id is compared with stanza_id only
            (('x3', 'x2', 'x1'), False),  # other account
            (('new', 'new', 'new'), False), ((None, None, None), False),
        ]
        for barrel in self.barrels(db):
            for ids, expected in cases:
                self.assertEqual(barrel._is_mam_message_stored(*ids), expected, (type(barrel).__name__, ids))

    def test_catchup_passes_is_stored(self):
        db = FakeDB()
        barrel = MAMStoreTests.make_barrel(None, db)
        barrel.signals = {'message_received': types.SimpleNamespace(emit=lambda *a: None)}
        client = FakeClient([])
        barrel.client.retrieve_history = client.retrieve_history
        asyncio.run(barrel._retrieve_private_chat_history(PEER, 1, 1_790_000_000, None))
        self.assertEqual(client.kwargs['is_stored'], barrel._is_mam_message_stored)


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

    def test_own_displayed_marker_is_entry_own_receipt_skipped(self):
        pages = mam_pages(PEER, [
            ('a1', stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="r1">'
                          '<received xmlns="urn:xmpp:receipts" id="p1"/></message>')),
            ('a2', stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="d1">'
                          '<displayed xmlns="urn:xmpp:chat-markers:0" id="p1"/></message>')),
        ])
        entries = [e for page in pages for e in page]
        self.assertEqual(len(entries), 1)
        self.assertEqual((entries[0]['marker_type'], entries[0]['marker_for_id']), ('displayed', 'p1'))
        self.assertEqual(entries[0]['jid'], OUR_JID)
        self.assertEqual(entries[0]['archive_id'], 'a2')

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

    def test_own_displayed_entry_moves_read_up_to(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db = FakeDB()
        barrel = self.make_barrel(db)
        ts = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
        incoming = stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}" id="p1">'
                          '<body>hello</body></message>')
        page = [
            {'jid': PEER, 'body': 'hello', 'timestamp': ts, 'is_encrypted': False,
             'archive_id': 'a1', 'message': incoming},
            {'marker_type': 'displayed', 'marker_for_id': 'p1', 'jid': OUR_JID,
             'archive_id': 'a2', 'timestamp': ts},
        ]
        result = asyncio.run(barrel._process_and_store_mam_messages(page, PEER, 1))
        self.assertEqual(result, (1, 1, 1))
        item_id = db.fetchone("SELECT MAX(id) AS i FROM content_item")['i']
        self.assertEqual(db.read_up_to(), item_id)

    def run_catchup(self, db, pages):
        barrel = self.make_barrel(db)
        emits = []
        barrel.signals = {'message_received': types.SimpleNamespace(emit=lambda *a: emits.append(a))}
        barrel.client.retrieve_history = FakeClient(pages).retrieve_history
        asyncio.run(barrel._retrieve_private_chat_history(PEER, 1, 1_790_000_000, None))
        return emits

    def test_catchup_no_notify_when_marker_in_later_page(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db = FakeDB()
        old = db.add(0, 50, 0, message_id='p0')
        db.execute("UPDATE conversation SET read_up_to_item = ?", (old,))
        ts = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
        incoming = stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}" id="p1">'
                          '<body>hello</body></message>')
        page1 = [{'jid': PEER, 'body': 'hello', 'timestamp': ts, 'is_encrypted': False,
                  'archive_id': 'a1', 'message': incoming}]
        page2 = [{'marker_type': 'displayed', 'marker_for_id': 'p1', 'jid': OUR_JID,
                  'archive_id': 'a2', 'timestamp': ts}]
        emits = self.run_catchup(db, [page1, page2])
        self.assertEqual(emits, [(ACCOUNT, PEER, True), (ACCOUNT, PEER, True)])

    def test_catchup_notifies_once_for_unread(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db = FakeDB()
        old = db.add(0, 50, 0, message_id='p0')  # unread, but not from this run
        ts = datetime(2026, 9, 29, 10, 0, tzinfo=timezone.utc)
        own = stanza(f'<message xmlns="jabber:client" from="{OUR_JID}/other" to="{PEER}" id="c1">'
                     '<body>hi</body></message>')
        incoming = stanza(f'<message xmlns="jabber:client" from="{PEER}/phone" to="{OUR_JID}" id="p1">'
                          '<body>hello</body></message>')
        page1 = [{'jid': OUR_JID, 'body': 'hi', 'timestamp': ts, 'is_encrypted': False,
                  'archive_id': 'a1', 'message': own}]
        # Only own messages: refresh, no notification (p0 is older than this run)
        self.assertEqual(self.run_catchup(db, [page1]), [(ACCOUNT, PEER, True)])
        page2 = [{'jid': PEER, 'body': 'hello', 'timestamp': ts, 'is_encrypted': False,
                  'archive_id': 'a2', 'message': incoming}]
        emits = self.run_catchup(db, [page1, page2])
        self.assertEqual(emits, [(ACCOUNT, PEER, True), (ACCOUNT, PEER, False)])
        self.assertLess(old, db.fetchone("SELECT MAX(id) AS i FROM content_item")['i'])

    def test_live_own_displayed_marker(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        db = FakeDB()
        item = db.add(0, 100, 0, message_id='p1')
        barrel = self.make_barrel(db)
        emits = []
        barrel.signals = {'message_received': types.SimpleNamespace(emit=lambda *a: emits.append(a))}
        barrel._on_marker_received(PEER, 'p1', 'displayed_own')
        self.assertEqual(db.read_up_to(), item)
        self.assertEqual(emits, [(ACCOUNT, PEER, True)])


class FakeClient:
    def __init__(self, pages, rooms=None):
        self.pages = pages
        self.closed = False
        self.rooms = rooms or {}
        self.kwargs = None

    def retrieve_history(self, **kwargs):
        self.kwargs = kwargs

        async def gen():
            try:
                for page in self.pages:
                    yield page
            finally:
                self.closed = True
        return gen()


class MessageRetryMAMTests(unittest.TestCase):

    def check(self, pages, jid=PEER, rooms=None):
        handler = MessageRetryHandler.__new__(MessageRetryHandler)
        client = FakeClient(pages, rooms)
        msg = {'counterpart_jid': jid, 'time': 1_790_000_000, 'origin_id': 'o1'}
        found = asyncio.run(handler._check_message_in_mam(msg, client, logging.getLogger('test')))
        return found, client

    def test_own_undecryptable_found_and_query_args(self):
        own = {'own_undecryptable': True, 'message': stanza('<message xmlns="jabber:client" id="o1"/>'),
               'archive_id': 'a1', 'jid': OUR_JID}
        found, client = self.check([[own]])
        self.assertTrue(found)
        self.assertEqual(client.kwargs['with_jid'], PEER)
        self.assertTrue(client.kwargs['include_own_ids'])
        self.assertNotIn('is_stored', client.kwargs)  # own messages must come as entries
        found, client = self.check([[own]], jid=ROOM, rooms={ROOM: {}})
        self.assertTrue(found)
        self.assertIsNone(client.kwargs['with_jid'])

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
