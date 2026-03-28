"""
Message information dialog.

Shows metadata and reactions for a message.
"""

from datetime import datetime
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QWidget,
    QHeaderView
)
from PySide6.QtCore import Qt


def create_selectable_label(text):
    """
    Create a QLabel with selectable text.

    Args:
        text: Label text (supports HTML)

    Returns:
        QLabel with text selection enabled
    """
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)
    return label


def show_message_info_dialog(parent, info_data, db, current_account_id):
    """
    Show message info dialog with metadata and reactions table.

    Args:
        parent: Parent widget
        info_data: Dictionary with message metadata
        db: Database connection
        current_account_id: Current account ID for filtering reactions
    """
    # Extract message data from dictionary (already extracted to avoid dangling pointers)
    direction = info_data['direction']
    body = info_data['body']
    timestamp = info_data['timestamp']
    encrypted = info_data['encrypted']
    marked = info_data['marked']
    msg_type = info_data['msg_type']
    is_carbon = info_data['is_carbon']
    message_id = info_data['message_id']
    origin_id = info_data.get('origin_id')
    stanza_id = info_data.get('stanza_id')
    db_message_id = info_data.get('db_message_id')
    content_item_id = info_data['content_item_id']
    file_path = info_data['file_path']
    file_name = info_data['file_name']
    mime_type = info_data['mime_type']

    # Create dialog
    dialog = QDialog(parent)
    dialog.setWindowTitle("Message Information")
    dialog.setMinimumWidth(600)
    dialog.setMinimumHeight(400)

    layout = QVBoxLayout(dialog)

    # Create tab widget
    tabs = QTabWidget()
    layout.addWidget(tabs)

    # --- Metadata Tab ---
    metadata_widget = QWidget()
    metadata_layout = QVBoxLayout(metadata_widget)

    # Direction
    direction_text = "Sent" if direction == 1 else "Received"
    if is_carbon:
        direction_text += " (from another device)"
    metadata_layout.addWidget(create_selectable_label(f"<b>Direction:</b> {direction_text}"))

    # Type
    type_text = "Group Chat" if msg_type == 1 else "Direct Message"
    metadata_layout.addWidget(create_selectable_label(f"<b>Type:</b> {type_text}"))

    # Timestamp
    metadata_layout.addWidget(create_selectable_label(f"<b>Timestamp:</b> {timestamp}"))

    # Encryption
    encryption_text = "Yes (OMEMO)" if encrypted else "No"
    metadata_layout.addWidget(create_selectable_label(f"<b>Encrypted:</b> {encryption_text}"))

    # Delivery status
    if direction == 1:
        marker_names = {0: "Pending", 1: "Sent", 2: "Delivered", 7: "Read", 8: "Error"}
        status_text = marker_names.get(marked, f"Unknown ({marked})")

        # Style error status in red
        if marked == 8:
            status_label = create_selectable_label(f"<b>Status:</b> <span style='color: #c0392b;'>{status_text}</span>")
        else:
            status_label = create_selectable_label(f"<b>Status:</b> {status_text}")
        metadata_layout.addWidget(status_label)

        # Show error details if message failed
        if marked == 8:
            # Query error_text from database
            error_text = None
            if message_id and content_item_id and db:
                # Find message by content_item_id
                msg_row = db.fetchone("""
                    SELECT m.error_text
                    FROM message m
                    JOIN content_item ci ON m.id = ci.foreign_id
                    WHERE ci.id = ? AND ci.content_type = 0
                """, (content_item_id,))
                if msg_row:
                    error_text = msg_row['error_text']

            if error_text:
                error_label = create_selectable_label(f"<b>Error Details:</b> <span style='color: #c0392b;'>{error_text}</span>")
                metadata_layout.addWidget(error_label)

    # Message IDs section
    metadata_layout.addWidget(create_selectable_label("<b>Message IDs:</b>"))

    # Origin ID (XEP-0359 - client-generated, stable across edits)
    if origin_id:
        metadata_layout.addWidget(create_selectable_label(f"  • <b>Origin ID:</b> <span style='font-family: monospace;'>{origin_id}</span>"))
    else:
        metadata_layout.addWidget(create_selectable_label("  • <b>Origin ID:</b> <span style='color: #7f8c8d;'>Not set</span>"))

    # Stanza ID (XEP-0359 - server-assigned, used for MUC reactions)
    if stanza_id:
        metadata_layout.addWidget(create_selectable_label(f"  • <b>Stanza ID:</b> <span style='font-family: monospace;'>{stanza_id}</span>"))
    else:
        metadata_layout.addWidget(create_selectable_label("  • <b>Stanza ID:</b> <span style='color: #7f8c8d;'>Not set</span>"))

    # Message ID (from 'id' attribute in stanza)
    if db_message_id:
        metadata_layout.addWidget(create_selectable_label(f"  • <b>Message ID:</b> <span style='font-family: monospace;'>{db_message_id}</span>"))
    else:
        metadata_layout.addWidget(create_selectable_label("  • <b>Message ID:</b> <span style='color: #7f8c8d;'>Not set</span>"))

    # Show which ID is being used for reactions (the "selected" ID)
    if message_id:
        id_type = "unknown"
        if message_id == origin_id:
            id_type = "Origin ID"
        elif message_id == stanza_id:
            id_type = "Stanza ID"
        elif message_id == db_message_id:
            id_type = "Message ID"

        metadata_layout.addWidget(create_selectable_label(f"  • <b>Used for reactions:</b> {id_type}"))

    # Content Item ID
    if content_item_id:
        metadata_layout.addWidget(create_selectable_label(f"<b>Content Item ID:</b> {content_item_id}"))

    # File info if applicable
    if file_path:
        metadata_layout.addWidget(create_selectable_label(f"<b>File Name:</b> {file_name}"))
        metadata_layout.addWidget(create_selectable_label(f"<b>MIME Type:</b> {mime_type}"))

    # Message body (truncated if too long)
    if body:
        display_body = body if len(body) < 200 else body[:200] + "..."
        metadata_layout.addWidget(create_selectable_label(f"<b>Body:</b><br>{display_body}"))

    metadata_layout.addStretch()
    tabs.addTab(metadata_widget, "Metadata")

    # --- Reactions Tab ---
    reactions_widget = QWidget()
    reactions_layout = QVBoxLayout(reactions_widget)

    # Query all reactions for this message
    # Handle both 1-1 (jid_id) and MUC (occupant_id) reactions
    reactions = []
    if content_item_id and db:
        reactions = db.fetchall("""
            SELECT r.emojis, r.time,
                   j.bare_jid,
                   o.nick as muc_nick
            FROM reaction r
            LEFT JOIN jid j ON r.jid_id = j.id
            LEFT JOIN occupant o ON r.occupant_id = o.id
            WHERE r.content_item_id = ? AND r.account_id = ?
            ORDER BY r.time DESC
        """, (content_item_id, current_account_id))

    if reactions:
        # Create table with proper columns
        table = QTableWidget()
        table.setColumnCount(3)
        table.setHorizontalHeaderLabels(["Who", "Reaction", "When"])
        table.setRowCount(len(reactions))
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)

        # Populate table
        for row, reaction in enumerate(reactions):
            # Determine display name: nickname for MUC, JID for 1-1
            if reaction['muc_nick']:
                display_name = reaction['muc_nick']
            else:
                display_name = reaction['bare_jid'] or 'Unknown'

            emojis = reaction['emojis']
            time_ms = reaction['time']
            time_obj = datetime.fromtimestamp(time_ms / 1000.0)
            time_str = time_obj.strftime("%Y-%m-%d %H:%M:%S")

            # Who column
            who_item = QTableWidgetItem(display_name)
            table.setItem(row, 0, who_item)

            # Reaction column (emoji)
            reaction_item = QTableWidgetItem(emojis)
            table.setItem(row, 1, reaction_item)

            # When column
            when_item = QTableWidgetItem(time_str)
            table.setItem(row, 2, when_item)

        # Auto-resize columns
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)

        reactions_layout.addWidget(table)
    else:
        reactions_layout.addWidget(create_selectable_label("No reactions yet"))

    reactions_layout.addStretch()
    tabs.addTab(reactions_widget, f"Reactions ({len(reactions)})")

    # OK button
    button_box = QDialogButtonBox(QDialogButtonBox.Ok)
    button_box.accepted.connect(dialog.accept)
    layout.addWidget(button_box)

    dialog.exec_()
