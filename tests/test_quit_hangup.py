#!/usr/bin/env python3
"""
Offline tests for quitting the app during a call.

Closing the main window with an active call hangs up each call first
(the peer gets a session-terminate), then the real close runs once,
outside the hang-up task (from a Qt timer).
Closing without a call runs the real close at once.

Needs PySide6 (offscreen).

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_quit_hangup.py
"""

import asyncio
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from siproxylin.gui.main_window import MainWindow  # noqa: E402

APP = QApplication.instance() or QApplication([])


class FakeAccount:
    def __init__(self, delay=0.0):
        self.hangups = []
        self.delay = delay

    async def hangup_call(self, session_id):
        await asyncio.sleep(self.delay)
        self.hangups.append(session_id)


def make_window(session_map, accounts):
    """Fake main window with the real closeEvent and quit hang-up code."""
    w = SimpleNamespace()
    w._quit_hangup = None
    w._signal_shutdown = False
    w.call_manager = MagicMock()
    w.call_manager.call_session_map = session_map
    w.account_manager = MagicMock()
    w.account_manager.get_account.side_effect = lambda aid: accounts.get(aid)
    w.events = []
    w.close_tasks = []  # current asyncio task at each real close

    def close():
        event = MagicMock()
        MainWindow.closeEvent(w, event)
        w.events.append(event)
        if event.accept.called:
            w.close_tasks.append(asyncio.current_task())

    w.close = close
    w._hangup_calls_and_close = lambda: MainWindow._hangup_calls_and_close(w)
    return w


class TestQuitHangup(unittest.TestCase):

    def run_async(self, coro):
        return asyncio.run(coro)

    async def pump(self, seconds):
        """Run the asyncio loop and the Qt events (for QTimer.singleShot).

        Qt events run from plain loop callbacks, not inside a task,
        like under qasync.
        """
        loop = asyncio.get_running_loop()

        def tick():
            APP.processEvents()
            handle[0] = loop.call_later(0.01, tick)

        handle = [loop.call_soon(tick)]
        await asyncio.sleep(seconds)
        handle[0].cancel()

    def test_close_with_calls_hangs_up_then_closes_once(self):
        acc1 = FakeAccount(delay=0.05)
        acc2 = FakeAccount()
        w = make_window({'sid1': (1, 'bob@localhost'), 'sid2': (2, 'carol@localhost')},
                        {1: acc1, 2: acc2})

        async def scenario():
            w.close()
            w.close()  # second quit request while waiting
            # Nothing is closed yet
            w.call_manager.shutdown_service.assert_not_called()
            await self.pump(0.3)

        self.run_async(scenario())
        self.assertEqual(acc1.hangups, ['sid1'])
        self.assertEqual(acc2.hangups, ['sid2'])
        w.call_manager.shutdown_service.assert_called_once_with(signal_shutdown=False)
        w.account_manager.disconnect_all.assert_called_once()
        self.assertEqual(w._quit_hangup, 'done')
        # The first two close events are ignored, the last one is accepted
        self.assertEqual(len(w.events), 3)
        w.events[0].ignore.assert_called_once()
        w.events[1].ignore.assert_called_once()
        w.events[2].accept.assert_called_once()
        # The real close did not run inside the hang-up task
        self.assertEqual(w.close_tasks, [None])

    def test_close_without_calls_closes_at_once(self):
        w = make_window({}, {})

        async def scenario():
            w.close()

        self.run_async(scenario())
        w.call_manager.shutdown_service.assert_called_once_with(signal_shutdown=False)
        w.account_manager.disconnect_all.assert_called_once()
        self.assertIsNone(w._quit_hangup)
        w.events[0].accept.assert_called_once()
        w.events[0].ignore.assert_not_called()

    def test_hangup_timeout_still_closes(self):
        acc = FakeAccount(delay=10.0)
        w = make_window({'sid1': (1, 'bob@localhost')}, {1: acc})

        async def scenario():
            w.close()
            await self.pump(2.5)

        self.run_async(scenario())
        self.assertEqual(acc.hangups, [])
        w.call_manager.shutdown_service.assert_called_once()
        self.assertEqual(w._quit_hangup, 'done')
        self.assertEqual(w.close_tasks, [None])


if __name__ == '__main__':
    unittest.main()
