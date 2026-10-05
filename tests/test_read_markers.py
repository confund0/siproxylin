#!/usr/bin/env python3
"""
Offline tests for displayed markers and the read state in the GUI.

Covers: displayed markers only when the user sees the newest messages
(live view, at bottom, window active and not minimized): refresh, arrival
at bottom, the scroll-to-bottom button, window activation (changeEvent),
new messages (roster_manager) and own sent messages. Scrolled up or in a
search view, refresh updates rows in place only. _send_displayed_markers
moves read_up_to_item also with markers off and to the highest item ID.

The chat view tests use the real MessageDisplayWidget and ScrollManager
with a real database in a temp directory.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: <venv>/bin/python -m unittest tests/test_read_markers.py
"""

import os
import sys
import time
import types
import asyncio
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
# PySide6 first: patch.dict clears and refills sys.modules on exit, which breaks
# the lazy loading of PySide6 classes imported inside the block (crash in Qt)
from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.main_window import MainWindow
    from siproxylin.gui.managers.roster_manager import RosterManager
    from siproxylin.gui.managers.message_manager import MessageManager
    from siproxylin.gui.chat_view.taps import messages as messages_mod
    from siproxylin.gui.chat_view.taps.messages import MessageDisplayWidget
    from siproxylin.gui.chat_view.taps.scroll_manager import ScrollManager, AT_BOTTOM_PX
    from siproxylin.db.database import Database
logging.disable(logging.NOTSET)

from tests.test_carbon_markers import FakeDB, ACCOUNT, PEER


def delete_now(widget):
    """Delete a widget now, so no signal or timer of it runs in a later test."""
    widget.hide()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


APP = QApplication.instance() or QApplication([])


class RosterManagerTests(unittest.TestCase):

    def run_message(self, active, minimized, is_marker=False):
        rm = RosterManager.__new__(RosterManager)
        rm.chat_view = mock.Mock(current_account_id=ACCOUNT, current_jid=PEER)
        rm.main_window = mock.Mock()
        rm.main_window.isActiveWindow.return_value = active
        rm.main_window.isMinimized.return_value = minimized
        rm.contact_list = mock.Mock()
        rm.notification_manager = mock.Mock()
        rm.on_message_received(ACCOUNT, PEER, is_marker)
        return rm

    # Markers depend on the view state only (MessageDisplayWidget.mark_read_if_seen)

    def test_active_window_refreshes_no_notification(self):
        rm = self.run_message(True, False)
        rm.chat_view.refresh.assert_called_once_with()
        rm.notification_manager.send_message_notification.assert_not_called()

    def test_background_window_refreshes_notifies(self):
        rm = self.run_message(False, False)
        rm.chat_view.refresh.assert_called_once_with()
        rm.notification_manager.send_message_notification.assert_called_once_with(ACCOUNT, PEER)

    def test_minimized_window_notifies(self):
        rm = self.run_message(True, True)
        rm.chat_view.refresh.assert_called_once_with()
        rm.notification_manager.send_message_notification.assert_called_once()

    def test_marker_update_refreshes_later(self):
        rm = self.run_message(True, False, is_marker=True)
        rm.chat_view.refresh_later.assert_called_once_with()
        rm.chat_view.refresh.assert_not_called()


class MainWindowChangeEventTests(unittest.TestCase):

    def make_window(self, active, minimized, jid=PEER):
        # Plain QMainWindow setup only (MainWindow.__init__ starts the whole app)
        win = MainWindow.__new__(MainWindow)
        super(MainWindow, win).__init__()
        win.isActiveWindow = lambda: active
        win.isMinimized = lambda: minimized
        win.chat_view = mock.Mock(current_jid=jid)
        self.addCleanup(win.deleteLater)
        return win

    def send(self, win, event_type):
        win.changeEvent(QEvent(event_type))
        return win.chat_view.message_widget.mark_read_if_seen

    def test_activation_sends_markers(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.ActivationChange).assert_called_once()

    def test_restore_sends_markers(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.WindowStateChange).assert_called_once()

    def test_inactive_or_minimized_or_no_chat_sends_nothing(self):
        for args in ((False, False), (True, True), (True, False, None)):
            win = self.make_window(*args)
            self.send(win, QEvent.ActivationChange).assert_not_called()

    def test_other_event_sends_nothing(self):
        win = self.make_window(True, False)
        self.send(win, QEvent.FontChange).assert_not_called()

    def test_no_chat_view_yet(self):
        win = MainWindow.__new__(MainWindow)
        super(MainWindow, win).__init__()
        self.addCleanup(win.deleteLater)
        win.isActiveWindow = lambda: True
        win.isMinimized = lambda: False
        win.changeEvent(QEvent(QEvent.ActivationChange))  # no exception


class SendDisplayedMarkersTests(unittest.TestCase):

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.db = FakeDB()
        self.db.update_conversation_read_up_to = lambda conv_id, item_id: self.db.execute(
            "UPDATE conversation SET read_up_to_item = ? WHERE id = ?", (item_id, conv_id))
        self.client = mock.Mock()
        self.widget = types.SimpleNamespace(
            current_account_id=ACCOUNT, current_jid=PEER, current_is_muc=False, db=self.db,
            account_manager=mock.Mock(**{'get_account.return_value': mock.Mock(client=self.client)}),
            parent=None)

    def run_markers(self):
        MessageDisplayWidget._send_displayed_markers(self.widget)

    def test_read_up_to_is_highest_id_marker_to_newest_by_time(self):
        newest = self.db.add(0, 300, 0, message_id='p3')
        self.db.add(0, 100, 0, message_id='p1')
        highest = self.db.add(0, 200, 0, message_id='p2')
        self.run_markers()
        self.client.send_marker.assert_called_once_with(PEER, 'p3', 'displayed')
        self.assertEqual(self.db.read_up_to(), highest)
        self.assertLess(newest, highest)
        # Nothing new: no second marker
        self.run_markers()
        self.client.send_marker.assert_called_once()

    def test_markers_off_still_moves_read_up_to(self):
        item = self.db.add(0, 100, 0, message_id='p1')
        self.db.execute("UPDATE conversation SET send_marker = 0")
        self.run_markers()
        self.client.send_marker.assert_not_called()
        self.assertEqual(self.db.read_up_to(), item)


def scrollbar_mock(value, maximum):
    return mock.Mock(**{'value.return_value': value, 'maximum.return_value': maximum})


class IsAtBottomTests(unittest.TestCase):

    def at_bottom(self, value, maximum):
        sm = types.SimpleNamespace(message_area=mock.Mock(
            **{'verticalScrollBar.return_value': scrollbar_mock(value, maximum)}))
        return ScrollManager.is_at_bottom(sm)

    def test_content_fits(self):
        self.assertTrue(self.at_bottom(0, 0))

    def test_distance(self):
        self.assertTrue(self.at_bottom(1000, 1000))   # 0 px
        self.assertTrue(self.at_bottom(1000 - AT_BOTTOM_PX, 1000))
        self.assertFalse(self.at_bottom(999 - AT_BOTTOM_PX, 1000))


class UserSeesNewestTests(unittest.TestCase):

    def make(self, **kwargs):
        values = dict(view_mode='live', stale=False, at_bottom=True, active=True, minimized=False)
        values.update(kwargs)
        return types.SimpleNamespace(
            view_mode=values['view_mode'], _live_stale=values['stale'],
            scroll_manager=mock.Mock(**{'is_at_bottom.return_value': values['at_bottom']}),
            main_window=mock.Mock(**{'isActiveWindow.return_value': values['active'],
                                     'isMinimized.return_value': values['minimized']}))

    def test_all_conditions_true(self):
        self.assertTrue(MessageDisplayWidget.user_sees_newest(self.make()))

    def test_each_failed_condition(self):
        for kwargs in ({'view_mode': 'search'}, {'stale': True}, {'at_bottom': False},
                       {'active': False}, {'minimized': True}):
            with self.subTest(**kwargs):
                self.assertFalse(MessageDisplayWidget.user_sees_newest(self.make(**kwargs)))

    def test_no_main_window(self):
        w = self.make()
        w.main_window = None
        self.assertFalse(MessageDisplayWidget.user_sees_newest(w))


class ChatViewCase(unittest.TestCase):
    """Real MessageDisplayWidget and ScrollManager with a real database (300 messages)."""

    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Database(Path(tmp.name) / 'test.db')
        self.db.initialize()
        self.addCleanup(self.db.close)
        self.db.execute("INSERT INTO account (id, bare_jid, enabled) VALUES (?, 'me@example.org', 1)",
                        (ACCOUNT,))
        self.jid_id = self.db.get_or_create_jid(PEER)
        self.conv = self.db.get_or_create_conversation(ACCOUNT, self.jid_id, 0)
        self.time0 = int(time.time()) - 5000
        self.count = 0
        for _ in range(300):
            self.add()

        self.client = mock.Mock()
        account_manager = mock.Mock(**{'get_account.return_value': mock.Mock(client=self.client)})
        self.host = QWidget()
        self.host.resize(500, 700)
        layout = QVBoxLayout(self.host)
        self.w = MessageDisplayWidget(self.db, account_manager, self.host)
        layout.addWidget(self.w.message_area)
        self.sm = ScrollManager(self.w.message_area, self.host, self.w)
        self.w.set_scroll_manager(self.sm)
        self.w.main_window = mock.Mock(**{'isActiveWindow.return_value': True,
                                          'isMinimized.return_value': False})

        # Deferred calls are collected here and run by run_deferred()
        self.deferred = []
        patcher = mock.patch.object(messages_mod, 'QTimer')
        timer = patcher.start()
        self.addCleanup(patcher.stop)
        timer.singleShot.side_effect = lambda msec, fn: self.deferred.append(fn)
        # Runs before patcher.stop: no real timer is left for a later test file
        self.addCleanup(delete_now, self.host)

        self.w.load_messages(ACCOUNT, PEER, False, self.conv)
        APP.processEvents()
        self.sb = self.w.message_area.verticalScrollBar()
        self.assertGreater(self.sb.maximum(), 3000)  # enough content to scroll

    def add(self):
        """Add a received message; returns its content item ID."""
        self.count += 1
        _, item_id = self.db.insert_message_atomic(
            ACCOUNT, self.jid_id, self.conv, 0, 0, self.time0 + self.count, self.time0 + self.count,
            f'message {self.count}', 0, 0, 0, message_id=f'm{self.count}')
        self.db.commit()
        return item_id

    def run_deferred(self):
        calls, self.deferred = self.deferred, []
        for fn in calls:
            fn()
        return len(calls)

    def scroll_to(self, value):
        self.sb.setValue(value)
        APP.processEvents()

    def scroll_up(self):
        self.scroll_to(self.sb.maximum() // 2)

    def set_active(self, active):
        self.w.main_window.isActiveWindow.return_value = active

    def loaded_ids(self):
        model = self.w.message_model
        ids = (model.item(row).data(messages_mod.MessageBubbleDelegate.ROLE_CONTENT_ITEM_ID)
               for row in range(model.rowCount()))
        return [item_id for item_id in ids if item_id is not None]

    def read_up_to(self):
        return self.db.fetchone("SELECT read_up_to_item FROM conversation WHERE id = ?",
                                (self.conv,))['read_up_to_item']

    def markers(self):
        return self.client.send_marker.call_count

    def search(self, item_id):
        self.w.load_around_message(item_id)
        self.run_deferred()  # scroll to target and highlight
        APP.processEvents()


class RefreshTests(ChatViewCase):

    def test_opened_at_bottom_no_marker_from_load(self):
        self.assertTrue(self.sm.is_at_bottom())
        self.assertEqual(self.markers(), 0)

    def test_at_bottom_active_reloads_and_marks(self):
        item = self.add()
        self.w.refresh()
        self.assertIn(item, self.loaded_ids())
        self.assertTrue(self.sm.is_at_bottom())
        self.assertEqual(self.markers(), 1)
        self.assertEqual(self.read_up_to(), item)
        # Nothing new: no second marker
        self.w.refresh()
        self.assertEqual(self.markers(), 1)

    def test_at_bottom_inactive_reloads_no_marker(self):
        self.set_active(False)
        item = self.add()
        self.w.refresh()
        self.assertIn(item, self.loaded_ids())
        self.assertEqual(self.markers(), 0)

    def test_scrolled_up_updates_in_place(self):
        self.scroll_up()
        value = self.sb.value()
        with mock.patch.object(self.w, '_update_file_rows') as update_files, \
                mock.patch.object(self.w.message_delegate, 'clear_reaction_cache') as clear_cache:
            self.w.refresh()
        update_files.assert_called_once()
        clear_cache.assert_called_once()
        self.assertEqual(self.sb.value(), value)
        self.assertFalse(self.w._live_stale)  # DB has nothing newer

        item = self.add()
        self.w.refresh()
        self.assertNotIn(item, self.loaded_ids())
        self.assertTrue(self.w._live_stale)
        self.assertEqual(self.markers(), 0)

    def test_search_no_reload_no_marker(self):
        self.search(self.loaded_ids()[150])
        self.scroll_to(self.sb.maximum())
        rows = self.w.message_model.rowCount()
        item = self.add()
        self.w.refresh()
        self.assertEqual(self.w.message_model.rowCount(), rows)
        self.assertNotIn(item, self.loaded_ids())
        self.assertFalse(self.w._live_stale)
        self.assertEqual(self.markers(), 0)

    def test_scroll_down_to_middle_no_marker(self):
        # Bug 1: scrolling down past 50% marked everything as read
        self.scroll_to(0)
        self.run_deferred()
        self.add()
        with mock.patch.object(self.w, 'refresh', wraps=self.w.refresh) as refresh:
            for pct in (40, 60, 90):
                self.scroll_to(self.sb.maximum() * pct // 100)
                self.assertGreater(self.sb.maximum() - self.sb.value(), 100)
                self.run_deferred()
            refresh.assert_not_called()
        self.assertEqual(self.markers(), 0)
        self.assertEqual(self.read_up_to(), -1)


class ArrivalAtBottomTests(ChatViewCase):

    def setUp(self):
        super().setUp()
        self.scroll_up()
        self.run_deferred()

    def arrive(self):
        self.scroll_to(self.sb.maximum())
        return self.run_deferred()

    def test_stale_reloads_and_marks(self):
        item = self.add()
        self.w.refresh()  # scrolled up: in place only
        self.assertTrue(self.w._live_stale)
        self.assertEqual(self.arrive(), 1)
        self.assertIn(item, self.loaded_ids())
        self.assertFalse(self.w._live_stale)
        self.assertEqual(self.read_up_to(), item)
        self.assertEqual(self.markers(), 1)

    def test_not_stale_marks_only(self):
        with mock.patch.object(self.w, '_reload_live') as reload_live:
            self.assertEqual(self.arrive(), 1)
        reload_live.assert_not_called()
        self.assertEqual(self.markers(), 1)

    def test_staying_at_bottom_no_repeat(self):
        self.arrive()
        self.scroll_to(self.sb.maximum() - AT_BOTTOM_PX // 2)
        self.scroll_to(self.sb.maximum())
        self.assertEqual(self.run_deferred(), 0)
        self.assertEqual(self.markers(), 1)

    def test_inactive_no_marker(self):
        self.set_active(False)
        self.arrive()
        self.assertEqual(self.markers(), 0)
        # Activation later marks (MainWindow.changeEvent)
        self.set_active(True)
        self.w.mark_read_if_seen()
        self.assertEqual(self.markers(), 1)

    def test_search_arrival_does_nothing(self):
        self.search(self.loaded_ids()[150])
        self.scroll_up()
        self.run_deferred()
        self.assertEqual(self.arrive(), 0)
        self.assertEqual(self.w.view_mode, 'search')
        self.assertEqual(self.markers(), 0)

    def test_prefetch_still_triggers(self):
        with mock.patch.object(self.w, '_load_more_messages') as load_more:
            self.w.last_load_time = 0
            self.scroll_to(self.sb.maximum() // 10)
        load_more.assert_called_once()


class ReturnToLiveTests(ChatViewCase):

    def test_button_from_scrolled_up(self):
        self.scroll_up()
        item = self.add()
        self.w.refresh()
        self.sm._scroll_to_bottom()
        self.assertEqual(self.w.view_mode, 'live')
        self.assertIn(item, self.loaded_ids())
        self.assertTrue(self.sm.is_at_bottom())
        self.assertTrue(self.sm.scroll_to_bottom_btn.isHidden())
        self.assertEqual(self.markers(), 1)

    def test_button_from_search(self):
        self.search(self.loaded_ids()[150])
        self.assertFalse(self.sm.scroll_to_bottom_btn.isHidden())
        item = self.add()
        self.sm._scroll_to_bottom()
        self.assertEqual(self.w.view_mode, 'live')
        self.assertIsNone(self.w.message_delegate.highlighted_index)
        self.assertIn(item, self.loaded_ids())
        self.assertTrue(self.sm.is_at_bottom())
        self.assertEqual(self.markers(), 1)

    def test_button_inactive_no_marker(self):
        self.scroll_up()
        self.set_active(False)
        self.sm._scroll_to_bottom()
        self.assertTrue(self.sm.is_at_bottom())
        self.assertEqual(self.markers(), 0)

    def test_own_new_message_is_loaded(self):
        # At bottom and not stale: the new item in the DB still gives a reload
        item = self.add()
        self.w.return_to_live()
        self.assertIn(item, self.loaded_ids())

    def test_button_always_visible_in_search(self):
        self.search(self.loaded_ids()[150])
        self.scroll_to(self.sb.maximum())
        self.assertTrue(self.sm.is_at_bottom())
        self.assertFalse(self.sm.scroll_to_bottom_btn.isHidden())

    def test_button_coloured_while_unread(self):
        unread = lambda: bool(self.sm.scroll_to_bottom_btn.property('unread'))
        self.scroll_up()
        self.add()
        self.w.refresh()
        self.assertTrue(unread())
        # Window inactive: back at bottom, nothing is marked, the colour stays
        self.set_active(False)
        self.sm._scroll_to_bottom()
        self.assertTrue(unread())
        # Window active: marked, colour gone
        self.set_active(True)
        self.w.mark_read_if_seen()
        self.assertFalse(unread())

    def test_scroll_manager_button_calls_return_to_live(self):
        sm = types.SimpleNamespace(message_widget=mock.Mock(), message_area=mock.Mock())
        ScrollManager._scroll_to_bottom(sm)
        sm.message_widget.return_to_live.assert_called_once_with()


class SearchViewTests(ChatViewCase):

    def test_click_keeps_search_view(self):
        self.search(self.loaded_ids()[150])
        self.assertIsNotNone(self.w.message_delegate.highlighted_index)
        self.w.eventFilter(self.w.message_area, QEvent(QEvent.MouseButtonPress))
        self.assertIsNone(self.w.message_delegate.highlighted_index)
        self.assertEqual(self.w.view_mode, 'search')

    def test_load_more_in_search_has_no_gap(self):
        ids = self.loaded_ids()
        self.search(ids[150])
        self.w.last_load_time = 0
        self.w._load_more_messages()
        loaded = self.loaded_ids()
        start = ids.index(loaded[0])
        self.assertEqual(loaded, ids[start:start + len(loaded)])
        self.assertLess(start, 100)

    def test_load_messages_after_search_resets(self):
        self.search(self.loaded_ids()[150])
        self.w._live_stale = True
        self.w.load_messages(ACCOUNT, PEER, False, self.conv)
        self.assertEqual(self.w.view_mode, 'live')
        self.assertFalse(self.w._live_stale)
        self.assertIsNone(self.w.message_delegate.highlighted_index)
        self.assertTrue(self.sm.is_at_bottom())
        self.assertTrue(self.sm.scroll_to_bottom_btn.isHidden())


class OwnMessageTests(unittest.TestCase):

    def make_manager(self, jid=PEER):
        mm = MessageManager.__new__(MessageManager)
        mm.chat_view = mock.Mock(current_account_id=ACCOUNT, current_jid=jid)
        mm.db = mock.Mock()
        mm.db.fetchone.side_effect = [None, {'id': 1}]  # not a MUC, jid row
        mm.db.insert_message_atomic.return_value = (1, 1)
        return mm

    def send(self, mm):
        account = mock.Mock(account_id=ACCOUNT, **{'is_connected.return_value': False})
        logging.disable(logging.CRITICAL)
        try:
            asyncio.run(mm._send_message_async(account, PEER, 'hi', False))
        finally:
            logging.disable(logging.NOTSET)

    def test_sent_message_returns_to_live(self):
        mm = self.make_manager()
        self.send(mm)
        mm.chat_view.return_to_live.assert_called_once_with()
        mm.chat_view.refresh.assert_not_called()

    def test_other_chat_open_refreshes_only(self):
        mm = self.make_manager(jid='other@example.net')
        self.send(mm)
        mm.chat_view.return_to_live.assert_not_called()
        mm.chat_view.refresh.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
