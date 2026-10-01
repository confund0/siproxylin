#!/usr/bin/env python3
"""
Offline tests for the group chat join watch of DrunkXMPP (_watch_join).

No network: join_muc() is replaced by a fake that never answers, and
error presences are given to _on_muc_error by hand. MUC_JOIN_TIMEOUT is
set short.

Run with: <venv>/bin/python -m unittest tests/test_muc_join_watch.py
"""

import sys
import asyncio
import unittest
from pathlib import Path
from unittest import mock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from drunk_xmpp.client import DrunkXMPP

ROOM = 'room@conference.example.org'
TIMEOUT = 0.3


def make_client(errors) -> DrunkXMPP:
    """Client without OMEMO; join errors are added to the errors list."""
    async def on_error(room_jid, condition, text):
        errors.append((room_jid, condition))

    client = DrunkXMPP(
        jid='user@example.org/test',
        password='secret',
        rooms={},
        enable_omemo=False,
        on_muc_join_error_callback=on_error,
    )
    client.MUC_JOIN_TIMEOUT = TIMEOUT
    client._connection_state = True
    client.join_calls = []
    muc = client.plugin['xep_0045']

    # Join that never gets an answer; puts the room in the plugin like slixmpp
    def join_muc(room, nick, **kw):
        client.join_calls.append(room)
        muc.rooms[None][room] = {}
        muc.our_nicks[None][room] = nick
        return asyncio.get_event_loop().create_future()
    muc.join_muc = join_muc
    return client


def error_presence(client, condition='forbidden'):
    pres = client.make_presence(pfrom=ROOM + '/user', ptype='error')
    pres['error']['condition'] = condition
    return pres


class JoinWatchTests(unittest.TestCase):

    def test_timeout_reported_once(self):
        async def body():
            errors = []
            client = make_client(errors)
            await client.join_room(ROOM, 'user')
            await asyncio.sleep(TIMEOUT * 3)
            self.assertEqual(errors, [(ROOM, 'remote-server-timeout')])
            self.assertNotIn(ROOM, client._join_tasks)
        asyncio.run(body())

    def test_no_timeout_while_offline(self):
        async def body():
            errors = []
            client = make_client(errors)
            await client.join_room(ROOM, 'user')
            client._connection_state = False
            await asyncio.sleep(TIMEOUT * 4)
            self.assertEqual(errors, [])
            self.assertIn(ROOM, client._join_tasks)
            # Stream resumed: a full timeout, then the report
            client._connection_state = True
            await asyncio.sleep(TIMEOUT * 3)
            self.assertEqual(errors, [(ROOM, 'remote-server-timeout')])
        asyncio.run(body())

    def test_session_end_stops_watch(self):
        async def body():
            errors = []
            client = make_client(errors)
            await client.join_room(ROOM, 'user')
            await client._on_session_end(None)
            await asyncio.sleep(TIMEOUT * 3)
            self.assertEqual(errors, [])
        asyncio.run(body())

    def test_late_error_after_timeout_not_reported(self):
        async def body():
            errors = []
            client = make_client(errors)
            await client.join_room(ROOM, 'user')
            await asyncio.sleep(TIMEOUT * 3)
            await client._on_muc_error(error_presence(client))
            self.assertEqual(errors, [(ROOM, 'remote-server-timeout')])
            # A new join reports errors again
            await client.join_room(ROOM, 'user')
            await client._on_muc_error(error_presence(client))
            self.assertEqual(errors, [(ROOM, 'remote-server-timeout'), (ROOM, 'forbidden')])
        asyncio.run(body())

    def test_error_stops_timeout(self):
        async def body():
            errors = []
            client = make_client(errors)
            await client.join_room(ROOM, 'user')
            await client._on_muc_error(error_presence(client))
            await client._on_muc_error(error_presence(client))
            await asyncio.sleep(TIMEOUT * 3)
            self.assertEqual(errors, [(ROOM, 'forbidden')])
        asyncio.run(body())

    def test_rejoin_skipped_while_join_pending(self):
        async def body():
            errors = []
            client = make_client(errors)
            client.MUC_JOIN_TIMEOUT = 10
            await client.join_room(ROOM, 'user')
            with mock.patch.object(client, '_join_room') as join:
                await client._rejoin_room_delayed(ROOM, 0)
                join.assert_not_called()
            await client._on_session_end(None)
        asyncio.run(body())

    def test_failed_join_removed_from_plugin(self):
        # slixmpp drops invites from rooms in rooms[None]
        async def body():
            client = make_client([])
            muc = client.plugin['xep_0045']
            await client.join_room(ROOM, 'user')
            self.assertIn(ROOM, muc.rooms[None])
            await client._on_muc_error(error_presence(client, 'registration-required'))
            self.assertNotIn(ROOM, muc.rooms[None])
            self.assertNotIn(ROOM, muc.our_nicks[None])
        asyncio.run(body())

    def test_timed_out_join_removed_from_plugin(self):
        async def body():
            client = make_client([])
            muc = client.plugin['xep_0045']
            await client.join_room(ROOM, 'user')
            await asyncio.sleep(TIMEOUT * 3)
            self.assertNotIn(ROOM, muc.rooms[None])
            self.assertNotIn(ROOM, muc.our_nicks[None])
        asyncio.run(body())

    def test_one_join_per_session(self):
        async def body():
            client = make_client([])
            client.MUC_JOIN_TIMEOUT = 10
            # session_start (_join_all_rooms) and the app join the same room
            client.rooms[ROOM] = {'nick': 'user'}
            await client._join_all_rooms()
            await client.join_room(ROOM, 'user')
            self.assertEqual(client.join_calls, [ROOM])
            # Joined: no new join
            client.joined_rooms.add(ROOM)
            await client._join_all_rooms()
            self.assertEqual(client.join_calls, [ROOM])
            # New session: join again
            await client._on_session_end(None)
            await client._join_all_rooms()
            self.assertEqual(client.join_calls, [ROOM, ROOM])
            await client._on_session_end(None)
        asyncio.run(body())

    def test_join_after_failed_join(self):
        async def body():
            client = make_client([])
            client.MUC_JOIN_TIMEOUT = 10
            await client.join_room(ROOM, 'user')
            await client._on_muc_error(error_presence(client, 'registration-required'))
            await client.join_room(ROOM, 'user')
            self.assertEqual(client.join_calls, [ROOM, ROOM])
            await client._on_session_end(None)
        asyncio.run(body())

    def test_rejoin_after_kick(self):
        async def body():
            client = make_client([])
            client.rooms[ROOM] = {'nick': 'user'}
            client.joined_rooms.add(ROOM)
            # Kicked: _on_muc_presence removes the room from joined_rooms
            client.joined_rooms.discard(ROOM)
            await client._rejoin_room_delayed(ROOM, 0)
            self.assertEqual(client.join_calls, [ROOM])
            await client._on_session_end(None)
        asyncio.run(body())

    def test_leave_sends_to_our_nick(self):
        async def body():
            client = make_client([])
            muc = client.plugin['xep_0045']
            muc.rooms[None][ROOM] = {}
            muc.our_nicks[None][ROOM] = 'server-nick'
            client.rooms[ROOM] = {'nick': 'user'}
            client.joined_rooms.add(ROOM)
            with mock.patch.object(client, 'send_presence') as send:
                client.leave_room(ROOM)
            self.assertEqual(send.call_args.kwargs['pto'], ROOM + '/server-nick')
            self.assertEqual(send.call_args.kwargs['pstatus'], 'Leaving')
            self.assertNotIn(ROOM, client.rooms)
        asyncio.run(body())


if __name__ == '__main__':
    unittest.main()
