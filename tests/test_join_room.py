#!/usr/bin/env python3
"""
Offline tests for the Add Group flow.

Covers: resolve_room_jid() (room name or full address to room JID),
and MucBarrel user joins: the bookmark is written only after the join
works, and join errors carry the origin of the join ('' for joins the
user did not start, so no dialog).

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_join_room.py
"""

import os
import sys
import asyncio
import logging
import unittest
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtWidgets import QApplication
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.join_room_dialog import resolve_room_jid
    from siproxylin.core.barrels import muc as muc_mod
logging.disable(logging.NOTSET)

ROOM = 'room@conference.localhost'


class ResolveRoomJidTests(unittest.TestCase):

    def test_name_with_service(self):
        self.assertEqual(resolve_room_jid(' Team ', 'conference.localhost', 'localhost'),
                         ('team@conference.localhost', None))

    def test_full_address_lowercase(self):
        self.assertEqual(resolve_room_jid(' Room@muc.example.org ', None, 'localhost'),
                         ('room@muc.example.org', None))

    def test_full_address_invalid(self):
        for text in ('@muc.example.org', 'room@muc.example.org/nick', 'room@muc.example.org/',
                     'my room@muc.example.org', 'room@muc. example.org', 'room@@muc.example.org',
                     'room@muc..example.org', 'ro"om@muc.example.org'):
            jid, error = resolve_room_jid(text, 'conference.localhost', 'localhost')
            self.assertIsNone(jid, text)
            self.assertIn('Invalid room address', error)

    def test_full_address_without_dot(self):
        jid, error = resolve_room_jid('room@localhost', 'conference.localhost', 'localhost')
        self.assertIsNone(jid)
        self.assertIn('Invalid room address', error)

    def test_name_without_service(self):
        jid, error = resolve_room_jid('team', None, 'localhost')
        self.assertIsNone(jid)
        self.assertEqual(error, 'No group chat service found on localhost: enter the full address')

    def test_bad_names(self):
        for text in ('my team', 'a/b', 'a:b', 'a<b', "a'b", 'a\tb'):
            jid, error = resolve_room_jid(text, 'conference.localhost', 'localhost')
            self.assertIsNone(jid, text)
            self.assertIn('cannot have', error)

    def test_empty(self):
        jid, error = resolve_room_jid('  ', 'conference.localhost', 'localhost')
        self.assertIsNone(jid)
        self.assertTrue(error)


class UserJoinTests(unittest.TestCase):

    def setUp(self):
        self.client = mock.MagicMock()
        self.client.rooms = {}
        self.client.is_joined.return_value = False
        self.client.join_room = mock.AsyncMock()
        self.client.get_room_features = mock.AsyncMock(return_value={})
        self.client.get_room_config = mock.AsyncMock(return_value={})
        self.error_signal = mock.Mock()
        signals = {'muc_join_error': self.error_signal, 'roster_updated': mock.Mock()}
        self.barrel = muc_mod.MucBarrel(1, self.client, mock.Mock(), None, signals,
                                        {'bare_jid': 'alice@localhost'})
        self.barrel.create_or_update_bookmark = mock.AsyncMock()
        self.barrel.get_bookmark = mock.Mock(return_value=None)
        self.barrel._retrieve_muc_history = mock.AsyncMock()
        patcher = mock.patch.object(muc_mod, 'get_db', return_value=mock.Mock())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bookmark = {'name': 'Team', 'nick': 'alice', 'password': None, 'autojoin': True}

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_bookmark_after_join_works(self):
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice', room_name='Team',
                                                     origin='dialog', bookmark=self.bookmark))
        self.client.join_room.assert_awaited_once_with(ROOM, 'alice', None, room_name='Team')
        self.barrel.create_or_update_bookmark.assert_not_awaited()

        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.barrel.create_or_update_bookmark.assert_awaited_once_with(room_jid=ROOM, **self.bookmark)

    def test_no_bookmark_after_error(self):
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice', origin='dialog', bookmark=self.bookmark))
        self.run_async(self.barrel.on_muc_join_error(ROOM, 'remote-server-timeout', ''))
        self.barrel.create_or_update_bookmark.assert_not_awaited()
        self.assertNotIn(ROOM, self.client.rooms)
        args = self.error_signal.emit.call_args[0]
        self.assertEqual(args[0], ROOM)
        self.assertEqual(args[3], 'dialog')

        # A late self-presence: no bookmark to write, and the room is left
        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.barrel.create_or_update_bookmark.assert_not_awaited()
        self.client.leave_room.assert_called_once_with(ROOM)

    def test_late_join_with_bookmark_not_left(self):
        self.barrel.get_bookmark.return_value = mock.Mock()
        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.client.leave_room.assert_not_called()

    def test_late_join_in_rooms_not_left(self):
        self.client.rooms[ROOM] = {'nick': 'alice'}
        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.client.leave_room.assert_not_called()

    def test_join_other_case_not_left(self):
        # Old rows can keep the case as typed; slixmpp gives the room JID lowercase
        self.client.rooms['Room@Conference.localhost'] = {'nick': 'alice'}
        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.client.leave_room.assert_not_called()

    def test_header_bookmark_after_join_works(self):
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice', origin='header', bookmark=self.bookmark))
        self.barrel.create_or_update_bookmark.assert_not_awaited()
        self.run_async(self.barrel.on_muc_joined(ROOM, 'alice'))
        self.barrel.create_or_update_bookmark.assert_awaited_once_with(room_jid=ROOM, **self.bookmark)
        self.client.leave_room.assert_not_called()

    def test_header_error_without_bookmark_forgets_room(self):
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice', origin='header', bookmark=self.bookmark))
        self.run_async(self.barrel.on_muc_join_error(ROOM, 'not-authorized', ''))
        self.assertNotIn(ROOM, self.client.rooms)
        self.barrel.create_or_update_bookmark.assert_not_awaited()

    def test_header_origin(self):
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice'))
        self.run_async(self.barrel.on_muc_join_error(ROOM, 'not-authorized', ''))
        self.assertEqual(self.error_signal.emit.call_args[0][3], 'header')

    def test_autojoin_error_has_no_origin(self):
        self.run_async(self.barrel.on_muc_join_error(ROOM, 'forbidden', ''))
        self.assertEqual(self.error_signal.emit.call_args[0][3], '')

    def test_already_joined_writes_bookmark_now(self):
        self.client.is_joined.return_value = True
        self.run_async(self.barrel.add_and_join_room(ROOM, 'alice', origin='dialog', bookmark=self.bookmark))
        self.barrel.create_or_update_bookmark.assert_awaited_once_with(room_jid=ROOM, **self.bookmark)
        self.assertNotIn(ROOM, self.barrel._user_joins)


if __name__ == '__main__':
    unittest.main()
