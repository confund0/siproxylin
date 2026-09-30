#!/usr/bin/env python3
"""
Offline test: every theme style sheet parses without errors.

Qt drops the rules after a syntax error and only prints a warning, so a typo
silently disables the rest of a theme.

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_themes.py
"""

import os
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QPushButton

STYLES_DIR = Path(__file__).parent.parent / 'siproxylin' / 'styles'
APP = QApplication.instance() or QApplication([])


class ThemeParseTests(unittest.TestCase):

    def test_all_themes_parse(self):
        themes = sorted(STYLES_DIR.glob('*_theme.qss'))
        self.assertTrue(themes)
        for path in themes:
            with self.subTest(theme=path.name):
                warnings = []
                qInstallMessageHandler(lambda mode, context, message: warnings.append(message))
                try:
                    APP.setStyleSheet(path.read_text(encoding='utf-8').replace('{{BASE_FONT_SIZE}}', '11'))
                    button = QPushButton('x')
                    button.show()  # the style sheet is parsed when a widget is polished
                    APP.processEvents()
                    button.deleteLater()
                finally:
                    qInstallMessageHandler(None)
                    APP.setStyleSheet('')
                self.assertFalse([w for w in warnings if 'parse' in w.lower()], path.name)


if __name__ == '__main__':
    unittest.main()
