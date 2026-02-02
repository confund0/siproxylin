"""
Call Log dialog for Siproxylin.

Shows call history across all accounts with ability to:
- View call details (JID, direction, state, duration, date/time)
- Filter by account
- Search by JID
- Sort by date, duration, etc.
"""

import logging
from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
    QComboBox, QWidget
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..db.database import get_db
from ..core.constants import CallState, CallDirection


logger = logging.getLogger('siproxylin.call_log')


class CallLogDialog(QDialog):
    """Dialog for viewing call history across all accounts."""

    # Call state emoji/text mapping
    STATE_DISPLAY = {
        CallState.RINGING.value: "📞 Ringing",
        CallState.ESTABLISHING.value: "🔄 Connecting",
        CallState.IN_PROGRESS.value: "✓ Connected",
        CallState.OTHER_DEVICE.value: "📱 Other Device",
        CallState.ENDED.value: "✓ Ended",
        CallState.DECLINED.value: "✗ Declined",
        CallState.MISSED.value: "⚠ Missed",
        CallState.FAILED.value: "⚠ Failed",
        CallState.ANSWERED_ELSEWHERE.value: "✓ Answered on other device",
        CallState.REJECTED_ELSEWHERE.value: "✗ Rejected on other device",
    }

    DIRECTION_DISPLAY = {
        CallDirection.INCOMING.value: "← Incoming",
        CallDirection.OUTGOING.value: "→ Outgoing",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.db = get_db()

        self.setWindowTitle("Call Log")
        self.setMinimumSize(1000, 600)

        # Main layout
        layout = QVBoxLayout(self)

        # Top controls: Search + Filter
        controls_layout = QHBoxLayout()

        # Search box
        search_label = QLabel("Search:")
        controls_layout.addWidget(search_label)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by JID...")
        self.search_box.textChanged.connect(self._apply_filters)
        controls_layout.addWidget(self.search_box)

        controls_layout.addSpacing(20)

        # Account filter
        account_label = QLabel("Account:")
        controls_layout.addWidget(account_label)

        self.account_filter = QComboBox()
        self.account_filter.currentIndexChanged.connect(self._apply_filters)
        controls_layout.addWidget(self.account_filter)

        controls_layout.addStretch()

        layout.addLayout(controls_layout)

        # Table
        self.table = self._create_table()
        layout.addWidget(self.table)

        # Bottom buttons
        button_layout = QHBoxLayout()

        button_layout.addStretch()

        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._load_calls)
        button_layout.addWidget(refresh_button)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)

        # Load initial data
        self._populate_account_filter()
        self._load_calls()

        logger.debug("Call Log dialog opened")

    def _create_table(self):
        """Create table widget for displaying calls."""
        table = QTableWidget()
        table.setColumnCount(7)
        table.setHorizontalHeaderLabels([
            "Date/Time", "JID", "Account", "Direction", "State", "Duration", "Type"
        ])

        # Configure table appearance
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)

        # Column resizing
        header = table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)

        return table

    def _populate_account_filter(self):
        """Populate account filter dropdown."""
        self.account_filter.clear()
        self.account_filter.addItem("All Accounts", None)

        # Get all accounts
        accounts = self.db.fetchall("""
            SELECT id, bare_jid, alias
            FROM account
            ORDER BY id
        """)

        for account in accounts:
            display_name = account['alias'] or account['bare_jid']
            self.account_filter.addItem(display_name, account['id'])

    def _load_calls(self):
        """Load all calls from database."""
        # Disable sorting while loading
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)

        # Query all calls with account and JID info
        calls = self.db.fetchall("""
            SELECT
                c.id,
                c.account_id,
                c.counterpart_id,
                c.direction,
                c.time,
                c.end_time,
                c.state,
                c.type,
                j.bare_jid,
                a.bare_jid as account_jid,
                a.alias as account_alias
            FROM call c
            JOIN jid j ON c.counterpart_id = j.id
            LEFT JOIN account a ON c.account_id = a.id
            ORDER BY c.time DESC
        """)

        # Populate table
        for call in calls:
            row_position = self.table.rowCount()
            self.table.insertRow(row_position)

            # Date/Time
            call_time = datetime.fromtimestamp(call['time'])
            time_str = call_time.strftime("%Y-%m-%d %H:%M:%S")
            time_item = QTableWidgetItem(time_str)
            time_item.setData(Qt.UserRole, {
                'call_id': call['id'],
                'account_id': call['account_id'],
                'timestamp': call['time']
            })
            self.table.setItem(row_position, 0, time_item)

            # JID
            jid_item = QTableWidgetItem(call['bare_jid'])
            self.table.setItem(row_position, 1, jid_item)

            # Account
            if call['account_jid']:
                account_display = call['account_alias'] or call['account_jid']
            else:
                account_display = f"[DELETED: {call['account_id']}]"
            account_item = QTableWidgetItem(account_display)
            self.table.setItem(row_position, 2, account_item)

            # Direction
            direction_text = self.DIRECTION_DISPLAY.get(
                call['direction'],
                f"Unknown ({call['direction']})"
            )
            direction_item = QTableWidgetItem(direction_text)
            self.table.setItem(row_position, 3, direction_item)

            # State
            state_text = self.STATE_DISPLAY.get(
                call['state'],
                f"Unknown ({call['state']})"
            )
            state_item = QTableWidgetItem(state_text)

            # Color code by state
            if call['state'] == CallState.MISSED.value:
                state_item.setForeground(Qt.red)
                font = state_item.font()
                font.setBold(True)
                state_item.setFont(font)
            elif call['state'] == CallState.FAILED.value:
                state_item.setForeground(Qt.red)
            elif call['state'] == CallState.ENDED.value:
                state_item.setForeground(Qt.darkGreen)

            self.table.setItem(row_position, 4, state_item)

            # Duration
            if call['end_time']:
                duration_sec = call['end_time'] - call['time']
                if duration_sec >= 3600:
                    hours = duration_sec // 3600
                    minutes = (duration_sec % 3600) // 60
                    seconds = duration_sec % 60
                    duration_text = f"{hours:d}h {minutes:02d}m {seconds:02d}s"
                elif duration_sec >= 60:
                    minutes = duration_sec // 60
                    seconds = duration_sec % 60
                    duration_text = f"{minutes:d}m {seconds:02d}s"
                else:
                    duration_text = f"{duration_sec:d}s"
            else:
                duration_text = "—"

            duration_item = QTableWidgetItem(duration_text)
            duration_item.setData(Qt.UserRole, call['end_time'] - call['time'] if call['end_time'] else 0)
            self.table.setItem(row_position, 5, duration_item)

            # Type (audio/video)
            type_text = "🎤 Audio" if call['type'] == 0 else "📹 Video"
            type_item = QTableWidgetItem(type_text)
            self.table.setItem(row_position, 6, type_item)

        # Auto-size columns
        self.table.resizeColumnsToContents()

        # Re-enable sorting
        self.table.setSortingEnabled(True)

        # Apply filters
        self._apply_filters()

        logger.debug(f"Loaded {len(calls)} calls")

    def _apply_filters(self):
        """Apply search and account filters to table."""
        search_text = self.search_box.text().lower()
        selected_account_id = self.account_filter.currentData()

        for row in range(self.table.rowCount()):
            # Get row data
            jid = self.table.item(row, 1).text().lower()
            account_data = self.table.item(row, 0).data(Qt.UserRole)
            account_id = account_data['account_id']

            # Apply filters
            matches_search = search_text in jid
            matches_account = selected_account_id is None or account_id == selected_account_id

            # Show/hide row
            self.table.setRowHidden(row, not (matches_search and matches_account))
