#!/usr/bin/env python3
"""
Offline tests for bookmark changes from other devices (XEP-0402).

App side: the real MucBarrel handlers with the real Database on a file
in tmp/. A bookmark push adds or updates the row and joins the room if
autojoin is on. A retract push turns autojoin off and leaves the room;
the row and the messages stay. The session start sync does the same for
a room with autojoin on that is no longer on the server. A push or the
session start sync with autojoin turned off leaves the room too.
Auto join at session start waits for the bookmark sync. The Join
button writes and publishes a new row with autojoin off. For an
existing row it publishes the row as stored and never changes it. The room menu item "Auto-join"
uses the same call as the room details dialog.

drunk_xmpp side: the real push handlers with a parsed push stanza.
A push from another JID is ignored.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_bookmark_push.py
"""

import os
import sys
import asyncio
import logging
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

# The call service needs grpc (not in the repo venv)
_stubs = {name: mock.MagicMock() for name in (
    'grpc', 'drunk_call_hook', 'drunk_call_hook.protocol', 'drunk_call_hook.protocol.jingle')}
# PySide6 first: patch.dict refills sys.modules on exit, which breaks PySide6 lazy loading
from PySide6.QtWidgets import QApplication, QWidget, QMenu
from drunk_xmpp.client import DrunkXMPP  # noqa: E402  (slixmpp loads its plugins later)
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.core.barrels import muc as muc_mod
    from siproxylin.gui import contact_list as contact_list_mod
    from siproxylin.gui.models import ContactDisplayData
    from siproxylin.db.database import Database
    from drunk_xmpp.bookmarks import BookmarksMixin
    from slixmpp import JID, Message
logging.disable(logging.NOTSET)

REPO_TMP = Path(__file__).parent.parent / 'tmp'
OUR_JID = 'alice@localhost'
ROOM = 'room@conference.localhost'
OTHER = 'other@conference.localhost'
ACCOUNT = 1

APP = QApplication.instance() or QApplication([])


class FakeClient:
    """Rooms state of DrunkXMPP; leave_room works like the real one."""

    def __init__(self):
        self.rooms = {}
        self.joined_rooms = set()
        self.bookmarks_synced = asyncio.Event()
        self.join_room = mock.AsyncMock()
        self.add_bookmark = mock.AsyncMock()
        self.get_room_features = mock.AsyncMock(return_value={})
        self.get_room_config = mock.AsyncMock(return_value={})
        self.left = []

    def is_joined(self, room_jid):
        return room_jid in self.joined_rooms

    def leave_room(self, room_jid):
        self.left.append(room_jid)
        self.joined_rooms.discard(room_jid)
        self.rooms.pop(room_jid, None)


class BarrelTestBase(unittest.TestCase):
    """Real MucBarrel with the real Database on a file in tmp/."""

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
        patcher = mock.patch.object(muc_mod, 'get_db', return_value=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = FakeClient()
        self.roster_updated = mock.Mock()
        self.barrel = muc_mod.MucBarrel(ACCOUNT, self.client, self.db, None,
                                        {'roster_updated': self.roster_updated},
                                        {'bare_jid': OUR_JID})

    def add_bookmark(self, room, autojoin, name='Room', nick='alice', password_b64=None):
        jid_id = self.db.get_or_create_jid(room)
        self.db.execute("INSERT INTO bookmark (account_id, jid_id, name, nick, password, autojoin)"
                        " VALUES (?, ?, ?, ?, ?, ?)", (ACCOUNT, jid_id, name, nick, password_b64, autojoin))
        self.db.commit()

    def add_message(self, room):
        jid_id = self.db.get_or_create_jid(room)
        conv_id = self.db.get_or_create_conversation(ACCOUNT, jid_id, 1)
        self.db.insert_message_atomic(ACCOUNT, jid_id, conv_id, 0, 1, 100, 100,
                                      'hello', 0, 0, 0, message_id='m1')

    def bookmark_row(self, room):
        return self.db.fetchone("""
            SELECT b.name, b.nick, b.autojoin FROM bookmark b JOIN jid j ON b.jid_id = j.id
            WHERE b.account_id = ? AND j.bare_jid = ?""", (ACCOUNT, room))

    def message_count(self):
        return self.db.fetchone("SELECT COUNT(*) AS n FROM message")['n']

    def joined(self, room):
        """Room is joined like after _perform_room_join and the self-presence."""
        self.client.rooms[room] = {'nick': 'alice', 'password': None}
        self.client.joined_rooms.add(room)


class AppBookmarkPushTests(BarrelTestBase):

    def test_publish_new_bookmark_adds_row_and_joins(self):
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Team', 'nick': 'al', 'password': '', 'autojoin': True}))
        row = self.bookmark_row(ROOM)
        self.assertEqual((row['name'], row['nick'], row['autojoin']), ('Team', 'al', 1))
        self.client.join_room.assert_awaited_once_with(ROOM, 'al', None, room_name=None)
        self.assertIn(ROOM, self.client.rooms)

    def test_publish_changed_bookmark_updates_row_and_joins(self):
        self.add_bookmark(ROOM, 0, name='Old')
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'New', 'nick': '', 'password': 'pw', 'autojoin': True}))
        row = self.bookmark_row(ROOM)
        self.assertEqual((row['name'], row['nick'], row['autojoin']), ('New', 'alice', 1))
        self.client.join_room.assert_awaited_once_with(ROOM, 'alice', 'pw', room_name=None)

    def test_publish_without_autojoin_no_join(self):
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Team', 'nick': 'al', 'password': '', 'autojoin': False}))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.client.join_room.assert_not_awaited()

    def test_publish_for_joined_room_no_second_join(self):
        # Our own publish comes back as a push
        self.add_bookmark(ROOM, 1)
        self.joined(ROOM.upper())
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'password': '', 'autojoin': True}))
        self.client.join_room.assert_not_awaited()

    def test_own_autojoin_on_echo_no_join(self):
        # Setting turned on here: our own publish comes back as a push
        self.add_bookmark(ROOM, 0)
        asyncio.run(self.barrel.update_room_settings(room_jid=ROOM, autojoin=True))
        published = self.client.add_bookmark.await_args.kwargs
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': published['jid'], 'name': published['name'], 'nick': published['nick'],
             'password': published['password'] or '', 'autojoin': published['autojoin']}))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 1)
        self.client.join_room.assert_not_awaited()
        self.assertNotIn(ROOM, self.client.rooms)

    def test_remote_autojoin_on_joins(self):
        self.add_bookmark(ROOM, 0)
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'password': '', 'autojoin': True}))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 1)
        self.client.join_room.assert_awaited_once_with(ROOM, 'alice', None, room_name=None)

    def test_retract_turns_autojoin_off_and_leaves_room(self):
        self.add_bookmark(ROOM, 1)
        self.add_message(ROOM)
        self.joined(ROOM)
        asyncio.run(self.barrel.on_bookmark_removed(ROOM))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.assertEqual(self.client.left, [ROOM])
        self.assertNotIn(ROOM, self.client.rooms)
        self.assertEqual(self.message_count(), 1)
        self.roster_updated.emit.assert_called_with(ACCOUNT)

    def test_retract_for_room_not_joined_yet(self):
        # Join sent, no self-presence yet: no leave, but no join in the next session
        self.add_bookmark(ROOM, 1)
        self.client.rooms[ROOM] = {'nick': 'alice', 'password': None}
        asyncio.run(self.barrel.on_bookmark_removed(ROOM))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.assertEqual(self.client.left, [])
        self.assertNotIn(ROOM, self.client.rooms)

    def test_retract_after_gui_leave_does_nothing(self):
        # The GUI leave deleted the row and left the room before the push came
        asyncio.run(self.barrel.on_bookmark_removed(ROOM))
        self.assertIsNone(self.bookmark_row(ROOM))
        self.assertEqual(self.client.left, [])

    def test_session_start_removed_while_offline(self):
        self.add_bookmark(ROOM, 1)
        self.add_bookmark(OTHER, 1)
        self.add_bookmark('invite@conference.localhost', 0)
        self.add_message(ROOM)
        # Room from the last session, still in client.rooms
        self.client.rooms[ROOM] = {'nick': 'alice', 'password': None}
        asyncio.run(self.barrel.sync_bookmarks(
            [{'jid': OTHER, 'name': 'Other', 'nick': 'alice', 'autojoin': True}]))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.assertNotIn(ROOM, self.client.rooms)
        self.assertEqual(self.bookmark_row(OTHER)['autojoin'], 1)
        # Local bookmark only (invite): kept as it is
        self.assertEqual(self.bookmark_row('invite@conference.localhost')['autojoin'], 0)
        self.assertEqual(self.message_count(), 1)

    def test_publish_autojoin_off_leaves_room(self):
        # Autojoin turned off on another device
        self.add_bookmark(ROOM, 1)
        self.add_message(ROOM)
        self.joined(ROOM)
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'password': '', 'autojoin': False}))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.assertEqual(self.client.left, [ROOM])
        self.assertNotIn(ROOM, self.client.rooms)
        self.assertEqual(self.message_count(), 1)

    def test_own_publish_autojoin_off_stays_in_room(self):
        # Joined by hand with autojoin off: our own publish comes back as a push
        self.add_bookmark(ROOM, 0)
        self.joined(ROOM)
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'password': '', 'autojoin': False}))
        self.assertEqual(self.client.left, [])
        self.assertIn(ROOM, self.client.rooms)

    def test_session_start_autojoin_off_on_server(self):
        self.add_bookmark(ROOM, 1)
        self.client.rooms[ROOM] = {'nick': 'alice', 'password': None}
        asyncio.run(self.barrel.sync_bookmarks(
            [{'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'autojoin': False}]))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.assertNotIn(ROOM, self.client.rooms)

    def test_auto_join_waits_for_bookmark_sync(self):
        # Removed on another device while offline; the app auto join starts
        # before the bookmark sync of drunk_xmpp is done
        self.add_bookmark(ROOM, 1)
        self.add_bookmark(OTHER, 1)

        async def run():
            auto_join = asyncio.create_task(self.barrel.auto_join_bookmarked_rooms())
            await asyncio.sleep(0.05)
            await self.barrel.sync_bookmarks(
                [{'jid': OTHER, 'name': 'Other', 'nick': 'alice', 'autojoin': True}])
            self.client.bookmarks_synced.set()
            await auto_join

        asyncio.run(run())
        joined = [c.args[0] for c in self.client.join_room.await_args_list]
        self.assertEqual(joined, [OTHER])

    def test_auto_join_without_sync_after_wait(self):
        # The sync never comes (session start failed): join with the local rows
        self.add_bookmark(ROOM, 1)
        with mock.patch.object(muc_mod, 'BOOKMARK_SYNC_WAIT', 0.05):
            asyncio.run(self.barrel.auto_join_bookmarked_rooms())
        self.client.join_room.assert_awaited_once()

    def user_join(self, origin, bookmark=None, password=None):
        asyncio.run(self.barrel.add_and_join_room(ROOM, 'alice', password, origin=origin,
                                                  bookmark=bookmark))
        self.joined(ROOM)
        asyncio.run(self.barrel.on_muc_joined(ROOM, 'alice'))

    # Bookmark args of the Join button for a room with no row (header.py)
    NEW_ROW = {'name': None, 'nick': 'alice', 'password': None, 'autojoin': False}

    def test_join_button_new_row_publishes_autojoin_off(self):
        self.user_join('header', bookmark=self.NEW_ROW)
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name=ROOM, nick='alice', password=None, autojoin=False)

    def test_join_button_keeps_autojoin_on(self):
        self.add_bookmark(ROOM, 1)
        self.user_join('header')
        row = self.bookmark_row(ROOM)
        self.assertEqual((row['name'], row['nick'], row['autojoin']), ('Room', 'alice', 1))
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Room', nick='alice', password=None, autojoin=True)

    def test_join_button_keeps_autojoin_off(self):
        # Invite row: autojoin off, password from the invite (base64 of "pw")
        self.add_bookmark(ROOM, 0, name='Invite', password_b64='cHc=')
        self.user_join('header', password='pw')
        row = self.bookmark_row(ROOM)
        self.assertEqual((row['name'], row['nick'], row['autojoin']), ('Invite', 'alice', 0))
        # Local-only row: published as stored, so the phone gets it
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Invite', nick='alice', password='pw', autojoin=False)

    def test_join_button_row_written_during_join_keeps_autojoin(self):
        # No row at the click; a push writes one with autojoin on before the join works
        asyncio.run(self.barrel.add_and_join_room(ROOM, 'alice', origin='header',
                                                  bookmark=self.NEW_ROW))
        self.add_bookmark(ROOM, 1, name='Team')
        self.joined(ROOM)
        asyncio.run(self.barrel.on_muc_joined(ROOM, 'alice'))
        row = self.bookmark_row(ROOM)
        self.assertEqual((row['name'], row['autojoin']), ('Team', 1))
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Team', nick='alice', password=None, autojoin=True)

    def test_join_button_already_joined_keeps_autojoin(self):
        self.add_bookmark(ROOM, 1)
        self.joined(ROOM)
        asyncio.run(self.barrel.add_and_join_room(ROOM, 'alice', origin='header',
                                                  bookmark=self.NEW_ROW))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 1)
        self.client.add_bookmark.assert_not_awaited()

    def test_dialog_join_keeps_autojoin_choice(self):
        self.user_join('dialog', bookmark={'name': 'Team', 'nick': 'alice',
                                           'password': None, 'autojoin': False})
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Team', nick='alice', password=None, autojoin=False)

    def test_push_with_other_case_updates_same_row(self):
        # Old row with upper case, autojoin on; push with lower case turns it off
        self.add_bookmark('Room@conference.localhost', 1)
        self.joined(ROOM)
        asyncio.run(self.barrel.on_bookmark_changed(
            {'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'password': '', 'autojoin': False}))
        rows = self.db.fetchall("SELECT autojoin FROM bookmark")
        self.assertEqual([r['autojoin'] for r in rows], [0])
        self.assertEqual(self.client.left, [ROOM])

    def test_session_start_other_case_is_not_removed(self):
        self.add_bookmark('Room@conference.localhost', 1)
        asyncio.run(self.barrel.sync_bookmarks(
            [{'jid': ROOM, 'name': 'Room', 'nick': 'alice', 'autojoin': True}]))
        self.assertEqual(self.bookmark_row('Room@conference.localhost')['autojoin'], 1)


class FakeMenu(QMenu):
    """QMenu that keeps its actions and does not open (exec blocks)."""
    shown = []

    def exec_(self, *args):
        FakeMenu.shown.append(self)


class RoomMenuAutojoinTests(BarrelTestBase):
    """Room context menu item "Auto-join" with the real barrel and database."""

    def setUp(self):
        super().setUp()
        self.w = contact_list_mod.ContactListWidget.__new__(contact_list_mod.ContactListWidget)
        QWidget.__init__(self.w)
        self.addCleanup(self.w.deleteLater)
        self.w.db = mock.Mock(get_setting=mock.Mock(return_value='false'))
        self.w.contact_tree = mock.Mock()
        self.account = mock.Mock(muc=self.barrel)
        self.w.account_manager = mock.Mock(get_account=mock.Mock(return_value=self.account))
        FakeMenu.shown = []
        patcher = mock.patch.object(contact_list_mod, 'QMenu', FakeMenu)
        patcher.start()
        self.addCleanup(patcher.stop)

    def menu_action(self, autojoin):
        data = ContactDisplayData(jid=ROOM, name='Room', account_id=ACCOUNT, item_type='muc',
                                  is_muc=True, autojoin=autojoin)
        self.w._show_muc_context_menu(mock.Mock(), data)
        actions = [a for a in FakeMenu.shown[-1].actions() if a.text().startswith('Auto-join')]
        self.assertEqual(len(actions), 1)
        return actions[0]

    def click(self, action):
        async def run():
            action.trigger()
            for _ in range(5):
                await asyncio.sleep(0)
        asyncio.run(run())

    def test_menu_text(self):
        self.assertEqual(self.menu_action(True).text(), 'Auto-join\t[x]')
        self.assertEqual(self.menu_action(False).text(), 'Auto-join\t[ ]')
        self.assertFalse(self.menu_action(True).isCheckable())

    def test_click_turns_autojoin_on_without_join(self):
        self.add_bookmark(ROOM, 0)
        with mock.patch.object(self.barrel, 'update_room_settings',
                               wraps=self.barrel.update_room_settings) as update:
            self.click(self.menu_action(False))
        update.assert_awaited_once_with(room_jid=ROOM, autojoin=True)
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 1)
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Room', nick='alice', password=None, autojoin=True)
        self.roster_updated.emit.assert_called_with(ACCOUNT)
        self.client.join_room.assert_not_awaited()

    def test_click_turns_autojoin_off_without_leave(self):
        self.add_bookmark(ROOM, 1)
        self.joined(ROOM)
        self.click(self.menu_action(True))
        self.assertEqual(self.bookmark_row(ROOM)['autojoin'], 0)
        self.client.add_bookmark.assert_awaited_once_with(
            jid=ROOM, name='Room', nick='alice', password=None, autojoin=False)
        self.roster_updated.emit.assert_called_with(ACCOUNT)
        self.assertEqual(self.client.left, [])
        self.assertIn(ROOM, self.client.rooms)


def push(sender, inner):
    xml = ET.fromstring(
        f"<message xmlns='jabber:client' from='{sender}' to='{OUR_JID}/res'>"
        "<event xmlns='http://jabber.org/protocol/pubsub#event'>"
        f"<items node='urn:xmpp:bookmarks:1'>{inner}</items></event></message>")
    return Message(xml=xml)


ITEM = (f"<item id='{ROOM}'><conference xmlns='urn:xmpp:bookmarks:1' name='Team' autojoin='true'>"
        "<nick>al</nick></conference></item>")
RETRACT = f"<retract id='{ROOM}'/>"


class FakeXMPP(BookmarksMixin):

    def __init__(self):
        self.boundjid = JID(OUR_JID + '/res')
        self.logger = logging.getLogger('test')
        self.on_bookmark_changed_callback = mock.AsyncMock()
        self.on_bookmark_removed_callback = mock.AsyncMock()


class DrunkXmppBookmarkPushTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.xmpp = FakeXMPP()

    def test_own_publish_push(self):
        asyncio.run(self.xmpp._on_bookmark_publish(push(OUR_JID, ITEM)))
        self.xmpp.on_bookmark_changed_callback.assert_awaited_once_with(
            {'jid': ROOM, 'name': 'Team', 'nick': 'al', 'password': '', 'autojoin': True})

    def test_own_retract_push(self):
        asyncio.run(self.xmpp._on_bookmark_retract(push(OUR_JID, RETRACT)))
        self.xmpp.on_bookmark_removed_callback.assert_awaited_once_with(ROOM)

    def test_session_start_error_still_sets_bookmarks_synced(self):
        # The roster fetch fails before the bookmark step: auto join must not wait 30 s
        client = DrunkXMPP(jid=OUR_JID + '/res', password='secret', rooms={}, enable_omemo=False)
        client._start_session = mock.AsyncMock(side_effect=TimeoutError('roster'))
        client.bookmarks_synced.set()  # from the last session

        async def run():
            with self.assertRaises(TimeoutError):
                await client._on_session_start(None)
            # Cleared at the start, set again after the error
            client._start_session.assert_awaited_once()
            self.assertTrue(client.bookmarks_synced.is_set())

        with mock.patch('builtins.print'):
            asyncio.run(run())

    def test_push_from_other_jid_ignored(self):
        asyncio.run(self.xmpp._on_bookmark_publish(push('mallory@localhost', ITEM)))
        asyncio.run(self.xmpp._on_bookmark_retract(push('mallory@localhost/x', RETRACT)))
        self.xmpp.on_bookmark_changed_callback.assert_not_awaited()
        self.xmpp.on_bookmark_removed_callback.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()
