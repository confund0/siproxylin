"""
Welcome view for chat area (shown when no conversation is selected).

Displays ASCII art XEP heart with welcome message.
"""

import random
import os
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTextBrowser, QPushButton, QHBoxLayout
from PySide6.QtCore import Qt, Signal

from ....version import SUPPORTED_XEPS


class WelcomeView(QWidget):
    """Welcome screen with ASCII art XEP heart - shown when no conversation is selected."""

    # Signals for button actions
    add_account_requested = Signal()
    create_account_requested = Signal()

    # Color palette for XEPs (will cycle through these)
    XEP_COLORS = [
        "#e74c3c",  # Red
        "#3498db",  # Blue
        "#2ecc71",  # Green
        "#f39c12",  # Orange
        "#9b59b6",  # Purple
        "#1abc9c",  # Turquoise
        "#e67e22",  # Carrot
        "#34495e",  # Dark gray
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Setup the welcome view UI."""
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)

        # Generate complete HTML with table layout
        full_html = self._generate_full_html()

        # Text browser for the heart and welcome message
        browser = QTextBrowser()
        browser.setObjectName("welcomeBrowser")
        browser.setOpenExternalLinks(True)
        browser.setHtml(full_html)
        browser.setFrameStyle(0)
        browser.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        browser.setStyleSheet("background: transparent; border: none;")
        layout.addWidget(browser)

        # Add spacing before buttons
        layout.addSpacing(20)

        # Buttons at the bottom, centered
        button_layout = QHBoxLayout()
        button_layout.setSpacing(10)
        button_layout.addStretch()

        add_account_btn = QPushButton("Add Account")
        add_account_btn.setMinimumHeight(28)
        add_account_btn.setMinimumWidth(100)
        add_account_btn.setStyleSheet("""
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)
        add_account_btn.clicked.connect(self.add_account_requested.emit)
        button_layout.addWidget(add_account_btn)

        create_account_btn = QPushButton("Create Account")
        create_account_btn.setMinimumHeight(28)
        create_account_btn.setMinimumWidth(100)
        create_account_btn.setStyleSheet("""
            QPushButton {
                background-color: #2ecc71;
                color: white;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #27ae60;
            }
        """)
        create_account_btn.clicked.connect(self.create_account_requested.emit)
        button_layout.addWidget(create_account_btn)

        button_layout.addStretch()
        layout.addLayout(button_layout)

        # Add spacing at the bottom to push buttons up
        layout.addSpacing(80)

    def _generate_full_html(self):
        """Generate complete HTML with table layout for centering."""
        # Load welcome view template
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "resources",
            "templates",
            "welcome_view.html"
        )

        try:
            with open(template_path, 'r') as f:
                template = f.read()
        except FileNotFoundError:
            return "<center><h1 style='color: red;'>Welcome template not found</h1></center>"

        # Generate heart pre and replace placeholders
        heart_pre = self._generate_heart_pre()
        html = template.replace('{HEART}', heart_pre)

        # Inject version info
        from ....version import VERSION, BUILD_CODENAME
        version_str = f'{VERSION} - "{BUILD_CODENAME}"'
        html = html.replace('{VERSION}', version_str)

        return html

    def _generate_heart_pre(self):
        """
        Generate HTML for the XEP heart.

        Template format: " XEP-{00} " (8 chars + 2 spaces = 10 total)

        Note: We only color the XEP numbers with spans.
        Lines and backticks stay plain to avoid breaking spacing.
        """
        # Load heart template
        template_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
            "resources",
            "templates",
            "heart.txt"
        )

        try:
            with open(template_path, 'r') as f:
                template = f.read()
        except FileNotFoundError:
            return "<pre style='color: red;'>Heart template not found</pre>"

        # Get XEP numbers (just the numbers, not descriptions)
        xep_numbers = [xep[0] for xep in SUPPORTED_XEPS]

        # Shuffle for random placement
        random.shuffle(xep_numbers)

        # We need 20 XEPs for the placeholders (XEP-{00} through XEP-{19})
        # If we have fewer, repeat some
        while len(xep_numbers) <= 22:
            xep_numbers.extend([xep[0] for xep in SUPPORTED_XEPS])
        xep_numbers = xep_numbers[:22]  # Take exactly 22

        # Replace placeholders with colored XEP numbers
        # Template already has spaces, so NO surrounding spaces here!
        result = template
        for i in range(22):  # 0-21 = 22 placeholders
            placeholder = f"XEP-{{{i:02d}}}"  # XEP-{00}, XEP-{01}, etc.
            xep_color = random.choice(self.XEP_COLORS)
            # Replace with clickable link to XEP spec
            colored_xep = f'<a href="https://xmpp.org/extensions/xep-{xep_numbers[i]}.html" style="color: {xep_color}; text-decoration: none;">XEP-{xep_numbers[i]}</a>'
            result = result.replace(placeholder, colored_xep)

        # Escape HTML entities to preserve formatting
        # Do NOT wrap - and ` in spans - it breaks spacing!
        # Instead, use plain text and rely on base color

        # Wrap in <pre> with strict fixed-width monospace font
        # Use Courier or Courier New for guaranteed fixed-width
        # NO text-align here - let the table handle centering!
        # Color is inherited from parent (table cell) so you can change it in the template!
        html = f'''<pre style="
            font-family: 'Courier New', Courier, monospace;
            font-size: 15px;
            line-height: 1.0;
            letter-spacing: 0;
            word-spacing: 0;
            margin: 0;
            padding: 0;
        ">{result}</pre>'''
        return html
