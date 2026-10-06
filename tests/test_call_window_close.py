#!/usr/bin/env python3
"""
Offline tests for closing the call window.

Closing the call window with X during a call hangs up the call
(hangup_requested is emitted once). Closing it after the call has
ended does not emit hangup_requested.

Needs PySide6 (offscreen).

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_call_window_close.py
"""

import os
import sys
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402

from siproxylin.gui import call_window  # noqa: E402

APP = QApplication.instance() or QApplication([])


class TestCallWindowClose(unittest.TestCase):

    def make_window(self):
        w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', ['audio'], 'outgoing')
        self.hangups = []
        w.hangup_requested.connect(lambda: self.hangups.append(1))
        w.show()
        APP.processEvents()
        return w

    def test_close_during_call_hangs_up_once(self):
        w = self.make_window()
        try:
            w.close()
            w.close()
            APP.processEvents()
            self.assertEqual(len(self.hangups), 1)
        finally:
            w.deleteLater()

    def test_close_after_terminated_does_not_hang_up(self):
        w = self.make_window()
        try:
            w.on_call_terminated('success')
            w.close()
            APP.processEvents()
            self.assertEqual(self.hangups, [])
        finally:
            w.deleteLater()

    def test_close_after_state_closed_does_not_hang_up(self):
        w = self.make_window()
        try:
            w.on_call_state_changed('closed')
            w.close()
            APP.processEvents()
            self.assertEqual(self.hangups, [])
        finally:
            w.deleteLater()


if __name__ == '__main__':
    unittest.main()
