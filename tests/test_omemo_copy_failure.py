#!/usr/bin/env python3
"""
Offline tests for a second copy of an OMEMO message that fails to decrypt.

OMEMO keys work only once. The same message can come twice (for example the
offline copy live and the MAM copy in the catch-up): the second decryption
fails. XEP-0384 (Business Rules): ignore that failure, show nothing.

Covers both orders (MAM copy first, live copy first) for 1:1 and group
chats, a failure while the other copy is still being decrypted, and real
failures (no other copy decrypted), which are still reported.

No network, no PySide6.

Run with: <venv>/bin/python -m unittest tests/test_omemo_copy_failure.py
"""

import sys
import types
import asyncio
import logging
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock
from xml.etree import ElementTree as ET
from copy import copy

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from slixmpp.jid import JID
from slixmpp.stanza import Message

from drunk_xmpp.client import DrunkXMPP

OUR_JID = 'user@example.org'
PEER = 'peer@example.net'
ROOM = 'room@muc.example.org'
FAILED = '[Failed to decrypt OMEMO message]'

ENC = ('<body>fallback</body><encrypted xmlns="eu.siacs.conversations.axolotl">'
       '<header sid="1"/></encrypted>')


def stanza(xml):
    return Message(xml=ET.fromstring(xml))


def peer_msg(msg_id='p1', stanza_id=None):
    sid = f'<stanza-id xmlns="urn:xmpp:sid:0" by="{OUR_JID}" id="{stanza_id}"/>' if stanza_id else ''
    return stanza(f'<message xmlns="jabber:client" type="chat" from="{PEER}/phone" to="{OUR_JID}" id="{msg_id}">'
                  f'<origin-id xmlns="urn:xmpp:sid:0" id="{msg_id}"/>{sid}{ENC}</message>')


def room_msg(msg_id='g1', stanza_id=None):
    sid = f'<stanza-id xmlns="urn:xmpp:sid:0" by="{ROOM}" id="{stanza_id}"/>' if stanza_id else ''
    return stanza(f'<message xmlns="jabber:client" type="groupchat" from="{ROOM}/bob" to="{OUR_JID}/test" '
                  f'id="{msg_id}">{sid}{ENC}</message>')


class OnceOMEMO:
    """Replaces xep_0384: each message (by id) decrypts only once, like OMEMO keys."""

    def __init__(self, can_decrypt=True, wait=None):
        self.used = set()
        self.can_decrypt = can_decrypt
        self.wait = wait  # asyncio.Event: decrypt_message waits for it
        self.calls = 0

    def is_encrypted(self, msg):
        return msg.xml.find('{eu.siacs.conversations.axolotl}encrypted') is not None

    async def decrypt_message(self, msg):
        self.calls += 1
        if self.wait is not None and self.calls == 1:
            await self.wait.wait()
        msg_id = msg['id']
        if not self.can_decrypt or msg_id in self.used:
            raise ValueError('Key material decryption failed.')
        self.used.add(msg_id)
        out = copy(msg)
        out['body'] = 'plain text'
        return out, types.SimpleNamespace(device_id=1)


class FakeMAM:
    """Replaces xep_0313.iterate(): yields fake MAM results."""

    def __init__(self, results):
        self.results = results

    async def iterate(self, **kwargs):
        for archive_id, msg in self.results:
            yield {'mam_result': {'id': archive_id, 'forwarded': {
                'stanza': msg,
                'delay': {'stamp': datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)},
            }}}


class Harness:
    """A DrunkXMPP with fake OMEMO, MAM and MUC plugins; records the callbacks."""

    def __init__(self, omemo, mam_results=()):
        self.private, self.room = [], []

        async def on_private(from_jid, body, metadata, msg):
            self.private.append((body, metadata.decrypt_failed))

        async def on_room(room, nick, body, metadata, msg):
            self.room.append((body, metadata.decrypt_failed))

        self.client = DrunkXMPP(jid=OUR_JID + '/test', password='secret', rooms={}, enable_omemo=False,
                                on_private_message_callback=on_private, on_message_callback=on_room)
        self.client.omemo_enabled = True
        self.client.rooms[ROOM] = {'nick': 'me'}
        xep_0045 = mock.Mock()
        xep_0045.get_jid_property.side_effect = lambda room, nick, prop: 'bob@example.net/x'
        self.plugins = {'xep_0313': FakeMAM(mam_results), 'xep_0384': omemo, 'xep_0045': xep_0045}

    async def mam(self, jid):
        pages = [page async for page in self.client.retrieve_history(jid=jid, with_jid=jid)]
        return [e['body'] for page in pages for e in page]

    async def live_private(self, msg):
        await self.client._on_private_message(msg)

    async def live_room(self, msg):
        await self.client._on_groupchat_message(msg)


def run(harness, body):
    async def main():
        with mock.patch.object(harness.client, 'plugin', harness.plugins):
            return await body()

    logging.disable(logging.CRITICAL)
    try:
        return asyncio.run(main())
    finally:
        logging.disable(logging.NOTSET)


class PrivateCopyTests(unittest.TestCase):

    def test_mam_copy_first_live_failure_ignored(self):
        h = Harness(OnceOMEMO(), [('a1', peer_msg())])

        async def body():
            bodies = await h.mam(PEER)
            await h.live_private(peer_msg(stanza_id='a1'))
            return bodies

        self.assertEqual(run(h, body), ['plain text'])
        self.assertEqual(h.private, [])

    def test_live_copy_first_mam_failure_ignored(self):
        h = Harness(OnceOMEMO(), [('a1', peer_msg())])

        async def body():
            await h.live_private(peer_msg(stanza_id='a1'))
            return await h.mam(PEER)

        self.assertEqual(run(h, body), [])
        self.assertEqual(h.private, [('plain text', False)])

    def test_failure_while_other_copy_decrypts(self):
        # The live copy waits in decryption; the MAM copy fails meanwhile
        # (here because the fake fails all): ignored while the live copy runs
        wait = asyncio.Event()
        omemo = OnceOMEMO(wait=wait)
        h = Harness(omemo, [('a1', peer_msg())])

        async def body():
            live = asyncio.create_task(h.live_private(peer_msg(stanza_id='a1')))
            await asyncio.sleep(0)
            omemo.used.add('p1')  # the key is used by the waiting live copy
            bodies = await h.mam(PEER)
            omemo.used.discard('p1')
            wait.set()
            await live
            return bodies

        self.assertEqual(run(h, body), [])
        self.assertEqual(h.private, [('plain text', False)])

    def test_real_failure_still_reported(self):
        h = Harness(OnceOMEMO(can_decrypt=False), [('a1', peer_msg())])

        async def body():
            bodies = await h.mam(PEER)
            await h.live_private(peer_msg('p2', stanza_id='a2'))
            return bodies

        self.assertEqual(run(h, body), [FAILED])
        self.assertEqual(h.private, [(FAILED, True)])

    def test_same_id_other_peer_not_ignored(self):
        h = Harness(OnceOMEMO())
        other = stanza(f'<message xmlns="jabber:client" type="chat" from="other@example.net/x" '
                       f'to="{OUR_JID}" id="p1">{ENC}</message>')

        async def body():
            await h.live_private(peer_msg())
            await h.live_private(other)

        run(h, body)
        self.assertEqual(h.private, [('plain text', False), (FAILED, True)])


class RoomCopyTests(unittest.TestCase):

    def test_mam_copy_first_live_failure_ignored(self):
        h = Harness(OnceOMEMO(), [('ra1', room_msg())])

        async def body():
            bodies = await h.mam(ROOM)
            await h.live_room(room_msg(stanza_id='ra1'))
            return bodies

        self.assertEqual(run(h, body), ['plain text'])
        self.assertEqual(h.room, [])

    def test_live_copy_first_mam_failure_ignored(self):
        h = Harness(OnceOMEMO(), [('ra1', room_msg())])

        async def body():
            await h.live_room(room_msg(stanza_id='ra1'))
            return await h.mam(ROOM)

        self.assertEqual(run(h, body), [])
        self.assertEqual(h.room, [('plain text', False)])

    def test_real_failure_still_reported(self):
        h = Harness(OnceOMEMO(can_decrypt=False))

        async def body():
            await h.live_room(room_msg(stanza_id='ra1'))

        run(h, body)
        self.assertEqual(h.room, [(FAILED, True)])


if __name__ == '__main__':
    unittest.main()
