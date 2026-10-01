#!/usr/bin/env python3
"""
Offline test: a right click in the roster opens the context menu but does not
move the selection away from the open chat.

Needs PySide6 (offscreen). The call service modules (grpc) are replaced by stubs.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_roster_selection.py
"""

import os
import sys
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
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QTreeWidget, QTreeWidgetItem, QLineEdit
logging.disable(logging.CRITICAL)
with mock.patch.dict(sys.modules, _stubs):
    from siproxylin.gui.contact_list import ContactListWidget
logging.disable(logging.NOTSET)

APP = QApplication.instance() or QApplication([])


class RightClickSelectionTests(unittest.TestCase):

    def setUp(self):
        # Only the parts of ContactListWidget that the event filter uses
        self.w = ContactListWidget.__new__(ContactListWidget)
        QWidget.__init__(self.w)
        self.addCleanup(self.w.deleteLater)
        self.w.search_box = QLineEdit()
        self.w.contact_tree = QTreeWidget()
        self.w.contact_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.w._on_context_menu = mock.Mock()  # no real menu (it blocks)
        # As in the app; a second menu from this signal must not open
        self.w.contact_tree.customContextMenuRequested.connect(lambda pos: self.w._on_context_menu(pos))
        self.w.contact_tree.viewport().installEventFilter(self.w)
        QVBoxLayout(self.w).addWidget(self.w.contact_tree)
        self.open_chat = QTreeWidgetItem(self.w.contact_tree, ['open chat'])
        self.other = QTreeWidgetItem(self.w.contact_tree, ['other'])
        self.w.show()
        QTest.qWaitForWindowExposed(self.w)
        self.w.contact_tree.setCurrentItem(self.open_chat)

    def click(self, button, item):
        tree = self.w.contact_tree
        pos = tree.viewport().mapTo(self.w, tree.visualItemRect(item).center())
        # Through the window, so Qt creates the context menu event as in the app
        QTest.mouseClick(self.w.windowHandle(), button, Qt.NoModifier, pos)
        APP.processEvents()

    def test_right_click_opens_menu_keeps_selection(self):
        self.click(Qt.RightButton, self.other)
        self.assertIs(self.w.contact_tree.currentItem(), self.open_chat)
        self.w._on_context_menu.assert_called_once()
        pos = self.w._on_context_menu.call_args.args[0]
        self.assertIs(self.w.contact_tree.itemAt(pos), self.other)

    def test_left_click_still_selects(self):
        self.click(Qt.LeftButton, self.other)
        self.assertIs(self.w.contact_tree.currentItem(), self.other)
        self.w._on_context_menu.assert_not_called()


if __name__ == '__main__':
    unittest.main()
