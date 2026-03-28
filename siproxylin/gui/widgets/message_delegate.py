"""
Custom delegate for rendering chat message bubbles.

Uses QPainter to draw rounded rectangles - the only way to get actual
rounded corners in Qt without using QWebEngineView.
"""

from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem
from PySide6.QtCore import Qt, QSize, QRect, QPoint, QUrl, QRegularExpression
from PySide6.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics, QPainterPath, QTextDocument, QAbstractTextDocumentLayout, QSyntaxHighlighter, QTextCharFormat, QDesktopServices
import re

from ...styles.bubble_themes import get_bubble_colors


class XEP0393Highlighter(QSyntaxHighlighter):
    """Syntax highlighter for XEP-0393 Message Styling (visual formatting only)."""

    def __init__(self, document):
        super().__init__(document)

        # Bold: *text*
        self.bold_fmt = QTextCharFormat()
        self.bold_fmt.setFontWeight(QFont.Bold)

        # Italic: _text_
        self.italic_fmt = QTextCharFormat()
        self.italic_fmt.setFontItalic(True)

        # Strikethrough: ~text~
        self.strike_fmt = QTextCharFormat()
        self.strike_fmt.setFontStrikeOut(True)

        # Monospace: `text`
        self.mono_fmt = QTextCharFormat()
        mono_font = QFont("monospace")
        self.mono_fmt.setFont(mono_font)

        # XEP-0393 pattern: (^|\s)MARKER(\S|\S.*?\S)MARKER
        # Matches: start/space + marker + (single non-ws OR non-ws...non-ws) + marker
        self.patterns = [
            (QRegularExpression(r'(^|\s)(\*)(\S|\S.*?\S)\2'), self.bold_fmt, 2),      # *bold*
            (QRegularExpression(r'(^|\s)(_)(\S|\S.*?\S)\2'), self.italic_fmt, 2),     # _italic_
            (QRegularExpression(r'(^|\s)(~)(\S|\S.*?\S)\2'), self.strike_fmt, 2),     # ~strike~
            (QRegularExpression(r'(^|\s)(`)(\S|\S.*?\S)\2'), self.mono_fmt, 2),       # `code`
        ]

    def highlightBlock(self, text):
        """Apply formatting to text block."""
        for regex, fmt, group_offset in self.patterns:
            match_iter = regex.globalMatch(text)
            while match_iter.hasNext():
                match = match_iter.next()
                # Apply format to group 3 (the inner text, excluding markers)
                # Group 0 = whole match, Group 1 = ^|\s, Group 2 = marker, Group 3 = content
                start = match.capturedStart(3)
                length = match.capturedLength(3)
                self.setFormat(start, length, fmt)


class MessageBubbleDelegate(QStyledItemDelegate):
    """Delegate for rendering chat messages as rounded bubbles."""

    # URL detection regex pattern
    # Matches http://, https://, and www. URLs
    URL_PATTERN = re.compile(
        r'(?i)\b(?:'
        r'(?:https?://)'  # http:// or https://
        r'|(?:www\.)'     # or www.
        r')'
        r'(?:[a-z0-9][-a-z0-9]*[a-z0-9]\.)*'  # subdomains
        r'[a-z][-a-z0-9]*[a-z0-9]'            # domain
        r'(?:\.[a-z]{2,})?'                    # TLD
        r'(?::[0-9]{1,5})?'                    # optional port
        r'(?:[/?#][^\s]*)?',                   # path/query/fragment
        re.IGNORECASE
    )

    # User roles for storing message data
    ROLE_DIRECTION = Qt.UserRole + 1  # 0=received, 1=sent
    ROLE_BODY = Qt.UserRole + 2
    ROLE_TIMESTAMP = Qt.UserRole + 3
    ROLE_ENCRYPTED = Qt.UserRole + 4
    ROLE_MARKED = Qt.UserRole + 5
    ROLE_TYPE = Qt.UserRole + 6  # 0=chat, 1=groupchat, 2=error
    ROLE_NICKNAME = Qt.UserRole + 7  # MUC sender nickname
    # File transfer roles
    ROLE_FILE_PATH = Qt.UserRole + 8
    ROLE_FILE_NAME = Qt.UserRole + 9
    ROLE_MIME_TYPE = Qt.UserRole + 10
    ROLE_FILE_SIZE = Qt.UserRole + 11
    ROLE_FILE_ICON = Qt.UserRole + 12       # Pre-computed icon emoji
    ROLE_FILE_SIZE_TEXT = Qt.UserRole + 13  # Pre-computed formatted size
    ROLE_IS_CARBON = Qt.UserRole + 14       # 0=regular, 1=carbon (sent from another device)
    ROLE_MESSAGE_ID = Qt.UserRole + 15      # XMPP message ID for editing (message_id, origin_id, or stanza_id)
    ROLE_QUOTED_BODY = Qt.UserRole + 16     # Quoted message body for XEP-0461 replies (if available)
    ROLE_CONTENT_ITEM_ID = Qt.UserRole + 17 # content_item.id for reactions (XEP-0444)
    # Call roles (Phase 4)
    ROLE_CALL_STATE = Qt.UserRole + 18      # CallState enum value
    ROLE_CALL_DURATION = Qt.UserRole + 19   # Duration in seconds (or None if ongoing)
    ROLE_CALL_TYPE = Qt.UserRole + 20       # 0=audio, 1=video
    # Day separator role (Phase 4 UX)
    ROLE_IS_SEPARATOR = Qt.UserRole + 21    # True if this is a day separator item
    ROLE_SEPARATOR_TEXT = Qt.UserRole + 22  # Text to display (e.g., "Today", "Yesterday", "Fri, 29 Jan")
    ROLE_TIMESTAMP_RAW = Qt.UserRole + 23   # Raw Unix timestamp (for Info dialog full date/time)
    ROLE_OMEMO_CAPABLE = Qt.UserRole + 24   # True if this chat supports OMEMO (has devices)

    def __init__(self, parent=None, theme_name='dark', db=None, account_id=None):
        super().__init__(parent)

        # Database reference for querying reactions
        self.db = db
        self.account_id = account_id

        # Cache for loaded images and documents
        self._pixmap_cache = {}  # {file_path: QPixmap}
        self._doc_cache = {}      # {cache_key: (QTextDocument, width, height)}
        self._reaction_cache = {}  # {content_item_id: [(emoji, count), ...]}
        self._video_thumbnail_cache = {}  # {video_path: thumbnail_path}

        # Highlight state for search results
        self.highlighted_index = None  # QModelIndex to highlight temporarily

        # Track last clicked URL for context menu handling
        self.last_clicked_url = None

        # Bubble styling
        self.bubble_radius = 12
        self.padding = 10
        self.margin_vertical = 2  # Small vertical spacing between bubbles
        self.margin_vertical_file = 6  # Larger spacing for file attachments

        # Edge margins in millimeters (converted to pixels assuming ~96 DPI)
        # 7mm ≈ 26px, 10mm ≈ 38px at 96 DPI
        self.margin_left = 26   # 7mm from left edge
        self.margin_right = 38  # 10mm from right edge

        # Bubbles can be wide - up to 50% of available width
        self.max_bubble_width_ratio = 0.50
        self.spacing_between_text_and_time = 4

        # Load colors from theme
        self.set_theme(theme_name)

    def set_theme(self, theme_name: str):
        """
        Update bubble colors for a theme.

        Args:
            theme_name: Theme name ('light', 'dark', 'terminal', 'gruvbox', 'light_gray')
        """
        colors = get_bubble_colors(theme_name)
        self.sent_bg_color = colors['sent_bg']
        self.sent_text_color = colors['sent_text']
        self.received_bg_color = colors['received_bg']
        self.received_text_color = colors['received_text']
        self.timestamp_color = colors['timestamp']
        self.marker_read_color = colors['marker_read']
        self.unencrypted_sent_bg_color = colors['unencrypted_sent_bg']
        self.unencrypted_received_bg_color = colors['unencrypted_received_bg']
        self.url_sent_color = colors['url_sent']
        self.url_received_color = colors['url_received']

    def clear_reaction_cache(self):
        """Clear the reaction cache (call when messages are reloaded)."""
        self._reaction_cache.clear()

    def set_account(self, account_id):
        """
        Set the current account ID for querying reactions.

        Args:
            account_id: Current account ID
        """
        self.account_id = account_id
        self.clear_reaction_cache()  # Clear cache when switching accounts

    def _get_file_icon(self, mime_type):
        """Get appropriate emoji icon for file type."""
        if not mime_type:
            return "📎"

        mime_lower = mime_type.lower()

        # Images (shouldn't reach here, but just in case)
        if mime_lower.startswith('image/'):
            return "🖼"
        # Audio
        elif mime_lower.startswith('audio/'):
            return "🎵"
        # Video
        elif mime_lower.startswith('video/'):
            return "🎬"
        # Archives
        elif mime_lower in ('application/zip', 'application/x-rar', 'application/x-tar',
                           'application/gzip', 'application/x-7z-compressed'):
            return "📦"
        # PDFs
        elif mime_lower == 'application/pdf':
            return "📕"
        # Documents
        elif mime_lower in ('application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                           'application/vnd.oasis.opendocument.text', 'text/plain'):
            return "📄"
        # Spreadsheets
        elif mime_lower in ('application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                           'application/vnd.oasis.opendocument.spreadsheet'):
            return "📊"
        # Default
        else:
            return "📎"

    def _format_file_size(self, size_bytes):
        """Format file size in human-readable format."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _convert_text_to_html(self, text, url_color):
        """
        Convert plain text to HTML with URL links and XEP-0393 formatting.

        This function:
        1. Escapes HTML in plain text
        2. Applies XEP-0393 formatting (*bold*, _italic_, ~strike~, `code`, ```blocks```)
        3. Converts URLs to clickable links
        4. Converts newlines to <br />

        Args:
            text: Plain text with XEP-0393 formatting and possible URLs
            url_color: QColor for the URL links

        Returns:
            HTML string ready for QTextDocument
        """
        from html import escape
        import re

        # Step 1: Process multi-line code blocks FIRST (before escaping)
        # Pattern: ```language\ncode\n``` or ```\ncode\n```
        # This must be done before escaping to preserve newlines and avoid conflicts with inline `code`

        code_blocks = []
        def save_code_block(match):
            """Replace code blocks with placeholders and save them."""
            lang = match.group(1) if match.group(1) else ''
            code = match.group(2)

            # Escape HTML in code content
            escaped_code = escape(code)

            # Create styled code block with language hint if present
            # Using <small> for font size since QTextDocument doesn't respect font-size CSS well
            # Using table to limit width (QTextDocument doesn't respect inline-block well)
            lang_label = f'<small><small><span style="color: rgba(127,127,127,0.6);">{escape(lang)}</span></small></small><br />' if lang else ''

            # Preserve whitespace (spaces, tabs) and newlines:
            # - Replace tabs with 4 non-breaking spaces (standard tab width)
            # - Replace spaces with non-breaking spaces to preserve indentation
            # - Replace newlines with <br />
            code_with_br = (escaped_code
                .replace('\t', '&nbsp;&nbsp;&nbsp;&nbsp;')  # Tab = 4 spaces
                .replace(' ', '&nbsp;')                      # Preserve spaces
                .replace('\n', '<br />')                     # Newlines
            )

            block_html = (
                f'<small><table cellpadding="0" cellspacing="0" style="margin: 4px 0;"><tr><td style="'
                f'font-family: monospace; '
                f'background-color: rgba(127,127,127,0.2); '
                f'padding: 8px; border-radius: 4px; '
                f'border-left: 3px solid rgba(127,127,127,0.4);">'
                f'{lang_label}'
                f'{code_with_br}'
                f'</td></tr></table></small>'
            )

            # Use a placeholder that won't be affected by HTML escaping
            # Using Unicode private use area characters to ensure uniqueness
            placeholder = f'\uE000CODEBLOCK{len(code_blocks)}\uE001'
            code_blocks.append(block_html)
            return placeholder

        # Match ```optional_language\ncode\n``` or ```code...```
        # Pattern matches:
        # - ```language (optional, only letters/numbers, no spaces)
        # - Followed by either newline OR any content
        # - Captures everything until closing ```
        # - Content can start on same line or next line
        text = re.sub(
            r'```(?:([a-zA-Z0-9]+)\n|\n?)(.+?)```',
            save_code_block,
            text,
            flags=re.DOTALL
        )

        # Step 2: Escape HTML entities (after extracting code blocks)
        escaped = escape(text)

        # Step 3: Apply XEP-0393 inline formatting
        # Process in order - monospace first to protect code from URL detection
        # XEP-0393 pattern: (^|\s)MARKER(\S|\S.*?\S)MARKER

        # Monospace `code` - with background and smaller font
        # Using <small> tag which QTextDocument respects better than font-size CSS
        escaped = re.sub(
            r'(^|\s)(`)((?:\S|\S.*?\S))\2',
            r'\1<small><code style="font-family: monospace; background-color: rgba(127,127,127,0.2); padding: 2px 4px; border-radius: 3px;">\3</code></small>',
            escaped
        )

        # Bold *text*
        escaped = re.sub(r'(^|\s)(\*)((?:\S|\S.*?\S))\2', r'\1<b>\3</b>', escaped)

        # Italic _text_
        escaped = re.sub(r'(^|\s)(_)((?:\S|\S.*?\S))\2', r'\1<i>\3</i>', escaped)

        # Strikethrough ~text~
        escaped = re.sub(r'(^|\s)(~)((?:\S|\S.*?\S))\2', r'\1<s>\3</s>', escaped)

        # Step 4: Restore code blocks (they're already HTML, don't process further)
        for i, block_html in enumerate(code_blocks):
            escaped = escaped.replace(f'\uE000CODEBLOCK{i}\uE001', block_html)

        # Step 5: Convert URLs to anchors (but NOT inside <code> or <table> tags)
        # Split by <code>...</code> and <table>...</table> to process only plain text sections
        parts = re.split(r'(<code[^>]*>.*?</code>|<table[^>]*>.*?</table>)', escaped, flags=re.DOTALL)

        result = []
        for i, part in enumerate(parts):
            if part.startswith('<code') or part.startswith('<table'):
                # This is a code span or code block, don't process URLs
                result.append(part)
            else:
                # Process URLs in non-code text
                urls = list(self.URL_PATTERN.finditer(part))
                if urls:
                    url_result = []
                    last_end = 0
                    for match in urls:
                        start, end = match.span()
                        url = match.group(0)

                        # Add text before URL
                        if start > last_end:
                            url_result.append(part[last_end:start])

                        # Add URL as anchor
                        href = url if url.startswith(('http://', 'https://')) else f'http://{url}'
                        url_result.append(f'<a href="{href}" style="color: {url_color.name()};">{url}</a>')

                        last_end = end

                    # Add remaining text
                    if last_end < len(part):
                        url_result.append(part[last_end:])

                    result.append(''.join(url_result))
                else:
                    result.append(part)

        # Step 6: Convert newlines to <br /> (only in non-code-block text)
        # Code blocks preserve their newlines as actual newlines, not <br />
        html = ''.join(result).replace('\n', '<br />')
        return html

    def _convert_urls_to_anchors(self, text, url_color):
        """
        Convert URLs in text to HTML anchor tags with theme-aware colors.

        Args:
            text: Plain text with possible URLs
            url_color: QColor for the URL links

        Returns:
            HTML string with URLs converted to <a> tags
        """
        from html import escape

        # Find all URLs in the text
        urls = list(self.URL_PATTERN.finditer(text))

        if not urls:
            # No URLs found, just escape HTML and convert newlines
            return escape(text).replace('\n', '<br />')

        # Build HTML with URLs converted to anchors
        result = []
        last_end = 0

        for match in urls:
            start, end = match.span()
            url = match.group(0)

            # Add text before this URL (escaped)
            if start > last_end:
                result.append(escape(text[last_end:start]))

            # Add URL as anchor with theme color
            # If URL doesn't start with http:// or https://, add http://
            href = url if url.startswith(('http://', 'https://')) else f'http://{url}'
            result.append(f'<a href="{escape(href)}" style="color: {url_color.name()};">{escape(url)}</a>')

            last_end = end

        # Add remaining text after last URL (escaped)
        if last_end < len(text):
            result.append(escape(text[last_end:]))

        # Convert newlines to <br /> and join
        html = ''.join(result).replace('\n', '<br />')
        return html

    def _get_reactions(self, content_item_id):
        """
        Get reactions for a content item.

        Returns list of unique emojis from the 3 most recent reactions (no counting).

        Args:
            content_item_id: ID from content_item table

        Returns:
            List of emoji strings, e.g. ['❤️', '👍', '😂']
        """
        if not self.db or not content_item_id or not self.account_id:
            return []

        # Check cache
        if content_item_id in self._reaction_cache:
            return self._reaction_cache[content_item_id]

        # Query all reactions for this content item, ordered by most recent
        # Filter by account_id to handle multi-account setups correctly
        reactions = self.db.fetchall("""
            SELECT emojis, time
            FROM reaction
            WHERE content_item_id = ? AND account_id = ?
            ORDER BY time DESC
        """, (content_item_id, self.account_id))

        if not reactions:
            self._reaction_cache[content_item_id] = []
            return []

        # Collect unique emojis from the most recent reactions
        # Format: each row has comma-separated emojis like "❤️,👍"
        seen_emojis = []

        for row in reactions:
            emojis_str = row['emojis']

            if not emojis_str:
                continue

            # Split comma-separated emojis
            emojis = [e.strip() for e in emojis_str.split(',') if e.strip()]

            for emoji in emojis:
                # Add unique emojis, preserving order by recency
                if emoji not in seen_emojis:
                    seen_emojis.append(emoji)

                # Stop after collecting 3 unique emojis
                if len(seen_emojis) >= 3:
                    break

            if len(seen_emojis) >= 3:
                break

        # Cache result
        self._reaction_cache[content_item_id] = seen_emojis
        return seen_emojis

    def _get_content_document(self, body, is_file, file_path, file_name, mime_type, font, text_width, text_color=None, direction=None):
        """Get or create cached QTextDocument for content."""
        # Create cache key (v7 = with text_color and direction for URL color)
        cache_key = ('v7', body if not is_file else file_path, text_width, font.toString(), text_color.name() if text_color else None, direction)

        if cache_key in self._doc_cache:
            return self._doc_cache[cache_key]

        # Create new document
        doc = QTextDocument()
        doc.setDefaultFont(font)

        if is_file:
            from pathlib import Path
            if mime_type and mime_type.startswith('image/') and file_path and Path(file_path).exists():
                file_url = QUrl.fromLocalFile(file_path).toString()
                html = f'<img src="{file_url}" style="max-width: 300px;" />'
            elif mime_type and mime_type.startswith('video/') and file_path and Path(file_path).exists():
                # Video: generate/get thumbnail - play icon will be painted over it
                thumbnail_path = self._get_video_thumbnail(file_path)
                if thumbnail_path:
                    thumb_url = QUrl.fromLocalFile(thumbnail_path).toString()
                    # Simple image tag - play icon painted separately in paint() for reliability
                    html = f'<img src="{thumb_url}" style="max-width: 300px; max-height: 400px;" />'
                else:
                    # Fallback if thumbnail generation failed
                    html = f'<p>🎬 {file_name or "video"}</p>'
            else:
                # Non-image/video file: will be rendered with custom paint, not QTextDocument
                # Return dummy dimensions - actual rendering happens in paint()
                html = f'<p>📎 {file_name or "file"}</p>'
            doc.setHtml(html)
            content_size = doc.size()
            width = int(content_size.width())
            height = int(content_size.height())
        else:
            # For text: convert to HTML with URLs, XEP-0393 formatting, etc.
            # Get appropriate URL color based on direction (0=received, 1=sent)
            url_color = self.url_sent_color if direction == 1 else self.url_received_color

            # Convert text to HTML (escapes, applies XEP-0393, converts URLs)
            html_content = self._convert_text_to_html(body, url_color)

            # Wrap in HTML with color if provided
            if text_color:
                html_body = f'<span style="color: {text_color.name()};">{html_content}</span>'
            else:
                html_body = html_content

            # Set HTML in document
            doc.setHtml(html_body)

            # Set large width to measure natural width without wrapping
            doc.setTextWidth(10000)

            # Measure natural width (all formatting is in HTML, so this is accurate)
            natural_size = doc.size()
            natural_width = int(natural_size.width())

            # If natural width fits within max, use it; otherwise wrap
            if natural_width <= text_width:
                width = natural_width
                height = int(natural_size.height())
            else:
                # Text is too wide, enable wrapping at max width
                doc.setTextWidth(text_width)
                wrapped_size = doc.size()
                # Get actual width of wrapped content (width of longest line)
                ideal_width = doc.idealWidth()
                width = int(ideal_width) if ideal_width > 0 else text_width
                height = int(wrapped_size.height())

        result = (doc, width, height)
        self._doc_cache[cache_key] = result
        return result

    def _is_image_file(self, mime_type):
        """Check if file is an image type."""
        return mime_type and mime_type.startswith('image/')

    def _is_video_file(self, mime_type):
        """Check if file is a video type."""
        return mime_type and mime_type.startswith('video/')

    def _get_video_thumbnail(self, video_path):
        """
        Get or generate thumbnail for video file.

        Args:
            video_path: Path to video file

        Returns:
            str: Path to thumbnail, or None if failed
        """
        # Check cache first
        if video_path in self._video_thumbnail_cache:
            return self._video_thumbnail_cache[video_path]

        # Generate thumbnail
        try:
            from ...utils import get_or_generate_thumbnail, get_paths
            paths = get_paths()
            cache_dir = paths.cache_dir / 'video_thumbnails'

            thumbnail_path = get_or_generate_thumbnail(video_path, cache_dir, width=320, height=0)

            # Cache result (even if None, to avoid repeated failures)
            self._video_thumbnail_cache[video_path] = thumbnail_path
            return thumbnail_path

        except Exception as e:
            import logging
            logger = logging.getLogger('siproxylin.message_delegate')
            logger.error(f"Failed to generate video thumbnail: {e}")
            self._video_thumbnail_cache[video_path] = None
            return None

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index):
        """Paint a message bubble or day separator."""
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        # Check if this is a day separator
        is_separator = index.data(self.ROLE_IS_SEPARATOR)
        if is_separator:
            self._paint_day_separator(painter, option, index)
            painter.restore()
            return

        # Get message data
        direction = index.data(self.ROLE_DIRECTION)
        body = index.data(self.ROLE_BODY) or ""
        timestamp = index.data(self.ROLE_TIMESTAMP) or ""
        encrypted = index.data(self.ROLE_ENCRYPTED) or False
        marked = index.data(self.ROLE_MARKED) or 0
        msg_type = index.data(self.ROLE_TYPE) or 0
        nickname = index.data(self.ROLE_NICKNAME) or ""

        # File transfer data
        file_path = index.data(self.ROLE_FILE_PATH)
        file_name = index.data(self.ROLE_FILE_NAME)
        mime_type = index.data(self.ROLE_MIME_TYPE)
        file_size = index.data(self.ROLE_FILE_SIZE)
        file_icon = index.data(self.ROLE_FILE_ICON)         # Pre-computed
        file_size_text = index.data(self.ROLE_FILE_SIZE_TEXT)  # Pre-computed

        # Carbon copy flag
        is_carbon = index.data(self.ROLE_IS_CARBON) or False

        # OMEMO capability flag
        omemo_capable = index.data(self.ROLE_OMEMO_CAPABLE) or False

        # Content item ID for reactions
        content_item_id = index.data(self.ROLE_CONTENT_ITEM_ID)

        # Call data (Phase 4)
        call_state = index.data(self.ROLE_CALL_STATE)
        call_duration = index.data(self.ROLE_CALL_DURATION)
        call_type = index.data(self.ROLE_CALL_TYPE)

        # Check content type
        is_call = call_state is not None
        is_file = bool(file_path) and not is_call

        # === Render calls as separate widgets (not message bubbles) ===
        if is_call:
            self._paint_call_widget(painter, option, index, call_state, call_duration, call_type, direction, timestamp)
            painter.restore()
            return

        # Build timestamp text (without markers - we'll draw them separately)
        timestamp_text = timestamp
        if encrypted:
            timestamp_text += " 🔒"

        # For non-image/video files, prepend file size to timestamp (right-aligned together)
        if is_file and not self._is_image_file(mime_type) and not self._is_video_file(mime_type):
            if file_size_text:
                timestamp_text = f"({file_size_text})  {timestamp_text}"

        # Determine marker text for sent messages
        # Carbons (sent from another device) show 🗎 instead of delivery markers
        marker_text = ""
        if direction == 1:
            if is_carbon:
                # Carbon copy (sent from another device) - show document icon
                marker_text = "🗎"
            elif is_file:
                # Files don't show markers (state is tracked in file_transfer table, not via marked field)
                pass
            elif marked == 0:
                marker_text = "⌛"      # PENDING (not yet sent/acked) - hourglass
            elif marked == 1:
                marker_text = "✓"       # SENT (server ACK) - light single check
            elif marked == 2:
                marker_text = "✓✓"      # RECEIVED (delivery receipt) - light double check
            elif marked == 7:
                marker_text = "✔✔"      # READ (displayed marker) - heavy/bold double check
            elif marked == 8:
                marker_text = "⚠"       # ERROR (won't send)

        # Get reactions for this message
        reactions = self._get_reactions(content_item_id)

        # Calculate bubble rect (needs to know about file content and reactions)
        bubble_rect = self._calculate_bubble_rect(
            painter, option.rect, body, timestamp_text, marker_text, direction, msg_type, nickname,
            is_file, file_path, file_name, mime_type, reactions
        )

        # Draw bubble background
        # Use red-tinted colors for unencrypted messages ONLY if OMEMO is supported
        # If OMEMO is not supported, use normal colors (no warning background)
        if not encrypted and omemo_capable:
            bg_color = self.unencrypted_sent_bg_color if direction == 1 else self.unencrypted_received_bg_color
        else:
            bg_color = self.sent_bg_color if direction == 1 else self.received_bg_color

        path = QPainterPath()
        path.addRoundedRect(bubble_rect, self.bubble_radius, self.bubble_radius)
        painter.fillPath(path, bg_color)

        # Draw text
        text_rect = bubble_rect.adjusted(
            self.padding, self.padding, -self.padding, -self.padding
        )

        # Save the base font to avoid state bleeding
        base_font = QFont(painter.font())

        # For MUC messages (groupchat), draw nickname first (only for received messages)
        current_y_offset = 0
        if msg_type == 1 and direction == 0 and nickname:  # groupchat, received, has nickname
            # Nickname: bold, slightly smaller, dimmed color
            nickname_font = QFont(base_font)
            nickname_font.setBold(True)
            nickname_font.setPointSize(base_font.pointSize() - 1)
            painter.setFont(nickname_font)

            # Use timestamp color for nickname (dimmed)
            painter.setPen(self.timestamp_color)

            nickname_rect = QRect(text_rect)
            nickname_rect.setHeight(QFontMetrics(nickname_font).height())

            painter.drawText(
                nickname_rect,
                Qt.AlignLeft | Qt.AlignTop,
                f"{nickname}:"
            )

            # Update offset for body text
            current_y_offset = nickname_rect.height() + 6  # 6px spacing

        # Message body - normal weight, full color
        text_color = self.sent_text_color if direction == 1 else self.received_text_color
        painter.setPen(text_color)
        painter.setFont(base_font)  # Reset to base font (not bold)

        body_rect = QRect(text_rect)
        body_rect.setTop(text_rect.top() + current_y_offset)
        body_rect.setHeight(text_rect.height() - self._get_timestamp_height(painter) - current_y_offset)

        # Calculate max text width (same as _calculate_bubble_rect to ensure consistent wrapping)
        available_width = option.rect.width() - self.margin_left - self.margin_right
        max_bubble_width = int(available_width * self.max_bubble_width_ratio)
        max_text_width = max_bubble_width - 2 * self.padding

        if is_file:
            # Files: distinguish between images/videos and other files
            if self._is_image_file(mime_type) or self._is_video_file(mime_type):
                # Images/Videos: use QTextDocument for inline display
                doc, doc_width, doc_height = self._get_content_document(body, is_file, file_path, file_name, mime_type, base_font, max_text_width, text_color, direction)
                painter.save()
                painter.translate(body_rect.topLeft())
                doc.drawContents(painter)
                painter.restore()

                # For videos: paint play icon overlay using QPainter (cross-platform reliable)
                if self._is_video_file(mime_type):
                    # Calculate center of the video thumbnail
                    thumbnail_rect = QRect(body_rect.topLeft(), QSize(doc_width, doc_height))
                    center_x = thumbnail_rect.center().x()
                    center_y = thumbnail_rect.center().y()

                    # Draw play icon background (semi-transparent circle)
                    from PySide6.QtGui import QBrush
                    painter.save()
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QBrush(QColor(0, 0, 0, 120)))  # Semi-transparent black
                    icon_radius = 20
                    painter.drawEllipse(QPoint(center_x, center_y), icon_radius, icon_radius)

                    # Draw play triangle
                    painter.setPen(QPen(QColor(255, 255, 255, 230), 2))  # White outline
                    painter.setBrush(QBrush(QColor(255, 255, 255, 200)))  # Semi-transparent white fill

                    # Triangle points (pointing right)
                    triangle_size = 10
                    triangle = [
                        QPoint(center_x - triangle_size//2, center_y - triangle_size),
                        QPoint(center_x - triangle_size//2, center_y + triangle_size),
                        QPoint(center_x + triangle_size, center_y)
                    ]
                    from PySide6.QtGui import QPolygon
                    painter.drawPolygon(QPolygon(triangle))
                    painter.restore()
            else:
                # Non-image files:
                # Line 1: [Icon] filename.ext (left-aligned)
                # Line 2: (size) + timestamp + encryption (right-aligned, shared with timestamp rendering below)
                # Use pre-computed values (file_icon, file_size_text)

                painter.setFont(base_font)
                fm = QFontMetrics(base_font)

                # Line 1: Icon + filename
                icon_str = file_icon or "📎"
                display_name = file_name or "file"

                # Measure icon width
                icon_width = fm.horizontalAdvance(icon_str + " ")

                # Available width for filename
                available_filename_width = body_rect.width() - icon_width

                # Truncate filename if too long
                if fm.horizontalAdvance(display_name) > available_filename_width:
                    display_name = fm.elidedText(display_name, Qt.ElideMiddle, available_filename_width)

                filename_line = f"{icon_str} {display_name}"
                filename_rect = QRect(body_rect.left(), body_rect.top(), body_rect.width(), fm.height())
                painter.drawText(
                    filename_rect,
                    Qt.AlignLeft | Qt.AlignTop,
                    filename_line
                )

                # Line 2: File size will be prepended to timestamp
                # Modify timestamp_text to include file size
                # This will be rendered by the timestamp code below (right-aligned)
                # We need to update timestamp_text BEFORE the timestamp rendering section

                # Store the file size to prepend to timestamp later
                # (will be handled in timestamp section below)

                # Reset font for timestamp rendering
                painter.setFont(base_font)
        else:
            # Regular text: use QTextDocument for XEP-0393 formatting
            # Use max_text_width (same as _calculate_bubble_rect) for consistent wrapping
            doc, _, _ = self._get_content_document(body, is_file, file_path, file_name, mime_type, base_font, max_text_width, text_color, direction)
            painter.save()
            painter.translate(body_rect.topLeft())
            doc.drawContents(painter)
            painter.restore()

        # Timestamp - smaller, dimmed color
        painter.setPen(self.timestamp_color)
        timestamp_font = QFont(base_font)
        timestamp_font.setPointSize(base_font.pointSize() - 2)
        painter.setFont(timestamp_font)

        timestamp_rect = QRect(text_rect)
        timestamp_rect.setTop(body_rect.bottom() + self.spacing_between_text_and_time)

        painter.drawText(
            timestamp_rect,
            Qt.AlignRight | Qt.AlignTop,
            timestamp_text
        )

        # Draw markers separately with monospace font and custom color
        if marker_text:
            # Use monospace font for tighter spacing
            marker_font = QFont("monospace", timestamp_font.pointSize())
            marker_font.setStyleHint(QFont.Monospace)
            marker_fm = QFontMetrics(marker_font)
            painter.setFont(marker_font)

            # Use different color for read markers
            if marked == 7:
                painter.setPen(self.marker_read_color)
            else:
                painter.setPen(self.timestamp_color)

            # Calculate exact positions:
            # 1. Timestamp is right-aligned, so it ends at timestamp_rect.right()
            # 2. Measure timestamp width to find where it starts
            # 3. Place markers to the LEFT of timestamp start (since right-aligned layout)
            timestamp_fm = QFontMetrics(timestamp_font)
            timestamp_width = timestamp_fm.horizontalAdvance(timestamp_text)
            timestamp_start_x = timestamp_rect.right() - timestamp_width

            marker_width = marker_fm.horizontalAdvance(marker_text)
            marker_x = timestamp_start_x - marker_width - 3  # 3px gap

            marker_rect = QRect(
                marker_x,
                timestamp_rect.top(),
                marker_width,
                timestamp_rect.height()
            )

            painter.drawText(
                marker_rect,
                Qt.AlignLeft | Qt.AlignTop,
                marker_text
            )

        # Draw reactions on same line as timestamp, left-aligned (if any)
        if reactions:
            painter.setPen(self.timestamp_color)
            reaction_font = QFont(base_font)
            reaction_font.setPointSize(base_font.pointSize() - 1)
            painter.setFont(reaction_font)

            # Build reaction text: just emojis without spaces "❤️👍😂"
            reaction_text = "".join(reactions)

            # Draw reactions left-aligned on same line as timestamp
            # Timestamp/markers are right-aligned, reactions are left-aligned
            # This matches the bubble width calculation which accounts for both
            reaction_rect = QRect(text_rect)
            reaction_rect.setTop(body_rect.bottom() + self.spacing_between_text_and_time)

            painter.drawText(
                reaction_rect,
                Qt.AlignLeft | Qt.AlignTop,
                reaction_text
            )

        # Draw highlight overlay for search results
        if self.highlighted_index and self.highlighted_index == index:
            # Draw a semi-transparent yellow overlay over the entire item area
            highlight_color = QColor(255, 255, 0, 60)  # Yellow with alpha
            painter.fillRect(option.rect, highlight_color)

        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:
        """Calculate the size needed for this message bubble or separator."""
        # Check if this is a day separator
        is_separator = index.data(self.ROLE_IS_SEPARATOR)
        if is_separator:
            # Separator height: text + padding
            return QSize(option.rect.width(), 40)

        # Get message data
        direction = index.data(self.ROLE_DIRECTION)
        body = index.data(self.ROLE_BODY) or ""
        timestamp = index.data(self.ROLE_TIMESTAMP) or ""
        encrypted = index.data(self.ROLE_ENCRYPTED) or False
        marked = index.data(self.ROLE_MARKED) or 0
        msg_type = index.data(self.ROLE_TYPE) or 0
        nickname = index.data(self.ROLE_NICKNAME) or ""

        # File transfer data
        file_path = index.data(self.ROLE_FILE_PATH)
        file_name = index.data(self.ROLE_FILE_NAME)
        mime_type = index.data(self.ROLE_MIME_TYPE)

        # Call data (Phase 4)
        call_state = index.data(self.ROLE_CALL_STATE)
        call_duration = index.data(self.ROLE_CALL_DURATION)
        call_type = index.data(self.ROLE_CALL_TYPE)

        # Check content type
        is_call = call_state is not None
        is_file = bool(file_path) and not is_call

        # Carbon copy flag
        is_carbon = index.data(self.ROLE_IS_CARBON) or False

        # Build timestamp text (without markers - same as paint())
        timestamp_text = timestamp
        if encrypted:
            timestamp_text += " 🔒"

        # Determine marker text for width calculation (same logic as paint())
        marker_text = ""
        if direction == 1:
            if is_carbon:
                marker_text = "🗎"      # CARBON (sent from another device)
            elif is_file:
                pass  # Files don't show markers
            elif marked == 0:
                marker_text = "⌛"      # PENDING
            elif marked == 1:
                marker_text = "✓"       # SENT
            elif marked == 2:
                marker_text = "✓✓"      # RECEIVED
            elif marked == 7:
                marker_text = "✔✔"      # READ
            elif marked == 8:
                marker_text = "⚠"       # ERROR

        # Calculate text dimensions
        font = option.font
        fm = QFontMetrics(font)

        # Calculate available width (accounting for left and right margins)
        available_width = option.rect.width() - self.margin_left - self.margin_right
        max_bubble_width = int(available_width * self.max_bubble_width_ratio)
        text_width = max_bubble_width - 2 * self.padding

        # Calculate content height - use QTextDocument for consistency with paint()
        if is_call:
            # Calls: bubble + timestamp below
            call_height = fm.height() + 2 * self.padding  # Bubble height
            timestamp_font = QFont(font)
            timestamp_font.setPointSize(font.pointSize() - 2)
            timestamp_fm = QFontMetrics(timestamp_font)
            timestamp_height = timestamp_fm.height() + 4  # +4 for spacing
            total_height = call_height + timestamp_height + 8  # +8 for vertical padding
            return QSize(option.rect.width(), total_height)
        elif is_file and not self._is_image_file(mime_type) and not self._is_video_file(mime_type):
            # Non-image/video files: just 1 line for icon+filename
            # (size is on timestamp line, handled separately)
            text_rect_height = fm.height()
        else:
            # Text, images, and videos: use cached QTextDocument
            _, _, text_rect_height = self._get_content_document(
                body, is_file, file_path, file_name, mime_type, font, text_width, None, direction
            )

        # Add timestamp height
        timestamp_font = QFont(font)
        timestamp_font.setPointSize(font.pointSize() - 2)
        timestamp_fm = QFontMetrics(timestamp_font)
        timestamp_height = timestamp_fm.height()

        # Add nickname height for MUC received messages
        nickname_height = 0
        if msg_type == 1 and direction == 0 and nickname:  # groupchat, received, has nickname
            nickname_font = QFont(font)
            nickname_font.setBold(True)
            nickname_font.setPointSize(nickname_font.pointSize() - 1)
            nickname_fm = QFontMetrics(nickname_font)
            nickname_height = nickname_fm.height() + 6  # 6px spacing

        # Use larger vertical margin for file attachments (non-images/videos)
        vertical_margin = self.margin_vertical_file if (is_file and not self._is_image_file(mime_type) and not self._is_video_file(mime_type)) else self.margin_vertical

        total_height = (
            text_rect_height +
            self.spacing_between_text_and_time +
            timestamp_height +
            nickname_height +
            2 * self.padding +
            2 * vertical_margin
        )

        return QSize(option.rect.width(), total_height)

    def _calculate_bubble_rect(self, painter, item_rect, body, timestamp_text, marker_text, direction, msg_type, nickname,
                               is_file=False, file_path=None, file_name=None, mime_type=None, reactions=None, font=None):
        """Calculate the rectangle for the bubble."""
        if font is None:
            font = painter.font()
        fm = QFontMetrics(font)

        # Calculate available width and max bubble width
        available_width = item_rect.width() - self.margin_left - self.margin_right
        max_bubble_width = int(available_width * self.max_bubble_width_ratio)
        text_width = max_bubble_width - 2 * self.padding

        # Calculate content dimensions using QTextDocument (consistent with paint())
        _, text_rect_width, text_rect_height = self._get_content_document(
            body, is_file, file_path, file_name, mime_type, font, text_width, None, direction
        )

        # Calculate timestamp width
        timestamp_font = QFont(font)
        timestamp_font.setPointSize(font.pointSize() - 2)
        timestamp_fm = QFontMetrics(timestamp_font)
        timestamp_width = timestamp_fm.horizontalAdvance(timestamp_text)

        # Calculate marker width (using monospace font)
        marker_width = 0
        if marker_text:
            marker_font = QFont("monospace", timestamp_font.pointSize())
            marker_font.setStyleHint(QFont.Monospace)
            marker_fm = QFontMetrics(marker_font)
            marker_width = marker_fm.horizontalAdvance(marker_text) + 2  # +2 for spacing

        # Calculate reaction width (same font as timestamp)
        reaction_width = 0
        if reactions:
            # reactions is now a list of emoji strings (no counts)
            reaction_text = "".join(reactions)
            reaction_width = timestamp_fm.horizontalAdvance(reaction_text)

        # Calculate nickname width for MUC received messages
        nickname_width = 0
        nickname_height = 0
        if msg_type == 1 and direction == 0 and nickname:  # groupchat, received, has nickname
            nickname_font = QFont(font)
            nickname_font.setBold(True)
            nickname_font.setPointSize(nickname_font.pointSize() - 1)
            nickname_fm = QFontMetrics(nickname_font)
            nickname_width = nickname_fm.horizontalAdvance(f"{nickname}:")
            nickname_height = nickname_fm.height() + 6  # 6px spacing

        # Bubble width is max of content, nickname, and (reactions + timestamp + marker on same line)
        # Reactions are left-aligned, timestamp+markers are right-aligned
        bottom_line_width = reaction_width + timestamp_width + marker_width + 10  # +10 for spacing between them
        # For files (especially images), use actual content width; for text, allow up to max
        bubble_content_width = max(text_rect_width, bottom_line_width, nickname_width)

        if is_file:
            # Files: use exact content width (images are already constrained by max-width CSS)
            bubble_width = bubble_content_width + 2 * self.padding
        else:
            # Text: allow wrapping up to max_bubble_width
            bubble_width = min(bubble_content_width + 2 * self.padding, max_bubble_width)

        # Bubble height
        timestamp_height = timestamp_fm.height()
        bubble_height = (
            text_rect_height +
            self.spacing_between_text_and_time +
            timestamp_height +
            nickname_height +
            2 * self.padding
        )

        # Position bubble (right for sent, left for received)
        if direction == 1:  # Sent - align right with 10mm margin
            x = item_rect.right() - bubble_width - self.margin_right
        else:  # Received - align left with 7mm margin
            x = item_rect.left() + self.margin_left

        y = item_rect.top() + self.margin_vertical

        return QRect(x, y, bubble_width, bubble_height)

    def _get_timestamp_height(self, painter):
        """Get the height needed for timestamp."""
        font = painter.font()
        timestamp_font = QFont(font)
        timestamp_font.setPointSize(font.pointSize() - 1)
        fm = QFontMetrics(timestamp_font)
        return fm.height() + self.spacing_between_text_and_time

    def _paint_call_widget(self, painter, option, index, call_state, call_duration, call_type, direction, timestamp):
        """
        Paint call widget as a separate container (not a message bubble).

        Design:
        - Wider than message bubbles (60% vs 50%)
        - Gray background with black outline
        - Status icon shows call state (colored)
        - Direction-aligned: outgoing right, incoming left
        """
        from ...core.constants import CallState

        # Map call state to colored status icon
        state_display = {
            CallState.RINGING.value: ("📞", "Ringing", None),
            CallState.ESTABLISHING.value: ("📞", "Connecting", None),
            CallState.IN_PROGRESS.value: ("📞", "Connected", None),
            CallState.OTHER_DEVICE.value: ("📱", "Other Device", None),
            CallState.ENDED.value: ("✓", "Call", Qt.darkGreen),
            CallState.DECLINED.value: ("✗", "Declined", Qt.red),
            CallState.MISSED.value: ("⚠", "Missed", QColor(255, 140, 0)),  # Orange
            CallState.FAILED.value: ("⚠", "Failed", Qt.red),
            CallState.ANSWERED_ELSEWHERE.value: ("✓", "Answered on other device", Qt.darkGreen),
            CallState.REJECTED_ELSEWHERE.value: ("✗", "Rejected on other device", Qt.red),
        }

        status_icon, state_text, icon_color = state_display.get(call_state, ("📞", "Call", None))

        # Direction text
        direction_text = "Incoming" if direction == 0 else "Outgoing"

        # Duration text
        if call_duration is not None:
            if call_duration >= 3600:
                hours = call_duration // 3600
                minutes = (call_duration % 3600) // 60
                seconds = call_duration % 60
                duration_text = f"{hours:d}h {minutes:02d}m {seconds:02d}s"
            elif call_duration >= 60:
                minutes = call_duration // 60
                seconds = call_duration % 60
                duration_text = f"{minutes:d}m {seconds:02d}s"
            else:
                duration_text = f"{call_duration:d}s"
        else:
            duration_text = ""  # No answer or ongoing

        # Build call text: "📞 Incoming Call • 22s"
        if duration_text:
            call_text = f"📞 {direction_text} {state_text} • {duration_text}"
        else:
            call_text = f"📞 {direction_text} {state_text}"

        # Calculate bubble dimensions
        font = painter.font()
        fm = QFontMetrics(font)

        # Call bubbles use natural text width (like message bubbles)
        text_width = fm.horizontalAdvance(call_text)
        bubble_width = text_width + 2 * self.padding + 40  # +40 for status icon + spacing
        bubble_height = fm.height() + 2 * self.padding

        # Position bubble based on direction
        if direction == 1:  # Outgoing - right aligned
            bubble_x = option.rect.right() - self.margin_right - bubble_width
        else:  # Incoming - left aligned
            bubble_x = option.rect.left() + self.margin_left

        bubble_y = option.rect.top() + 4

        bubble_rect = QRect(bubble_x, bubble_y, bubble_width, bubble_height)

        # Draw bubble background (gray) with border
        from ...styles.theme_manager import get_theme_manager
        theme_manager = get_theme_manager()

        if theme_manager.current_theme == 'dark':
            bg_color = QColor(60, 60, 60)  # Dark gray
            border_color = QColor(100, 100, 100)  # Lighter gray border
            text_color = QColor(220, 220, 220)  # Light text
        else:
            bg_color = QColor(240, 240, 240)  # Light gray
            border_color = QColor(180, 180, 180)  # Darker gray border
            text_color = QColor(40, 40, 40)  # Dark text

        # Draw rounded rectangle
        path = QPainterPath()
        path.addRoundedRect(bubble_rect, self.bubble_radius, self.bubble_radius)
        painter.fillPath(path, bg_color)
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(path)

        # Draw call text
        text_rect = bubble_rect.adjusted(self.padding, self.padding, -self.padding - 25, -self.padding)
        painter.setPen(text_color)
        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, call_text)

        # Draw colored status icon on the right
        status_rect = QRect(
            bubble_rect.right() - 22,
            bubble_rect.top() + self.padding,
            20,
            fm.height()
        )
        if icon_color:
            painter.setPen(icon_color)
        painter.drawText(status_rect, Qt.AlignCenter, status_icon)

        # Draw timestamp below
        timestamp_font = QFont(font)
        timestamp_font.setPointSize(font.pointSize() - 2)
        painter.setFont(timestamp_font)

        timestamp_fm = QFontMetrics(timestamp_font)
        timestamp_y = bubble_rect.bottom() + 4
        timestamp_rect = QRect(bubble_x, timestamp_y, bubble_width, timestamp_fm.height())

        painter.setPen(self.timestamp_color)
        painter.drawText(timestamp_rect, Qt.AlignCenter, timestamp)

    def editorEvent(self, event, model, option, index):
        """
        Handle mouse events for clickable links.

        Args:
            event: Mouse event
            model: Data model
            option: Style options
            index: Model index

        Returns:
            True if event was handled, False otherwise
        """
        from PySide6.QtCore import QEvent
        from PySide6.QtWidgets import QApplication, QMenu

        # Only handle mouse events
        if event.type() not in (QEvent.MouseButtonRelease, QEvent.MouseMove, QEvent.MouseButtonPress):
            return super().editorEvent(event, model, option, index)

        # Skip if this is a separator or call
        if index.data(self.ROLE_IS_SEPARATOR) or index.data(self.ROLE_CALL_STATE) is not None:
            return super().editorEvent(event, model, option, index)

        # Get message data
        body = index.data(self.ROLE_BODY) or ""
        direction = index.data(self.ROLE_DIRECTION)
        msg_type = index.data(self.ROLE_TYPE) or 0
        nickname = index.data(self.ROLE_NICKNAME) or ""
        file_path = index.data(self.ROLE_FILE_PATH)
        file_name = index.data(self.ROLE_FILE_NAME)
        mime_type = index.data(self.ROLE_MIME_TYPE)
        is_file = bool(file_path)

        # Only handle text messages (not files)
        if is_file:
            return super().editorEvent(event, model, option, index)

        # Calculate bubble rect (same logic as paint()) - without using QPainter
        timestamp = index.data(self.ROLE_TIMESTAMP) or ""
        encrypted = index.data(self.ROLE_ENCRYPTED) or False
        marked = index.data(self.ROLE_MARKED) or 0
        is_carbon = index.data(self.ROLE_IS_CARBON) or False

        # Build timestamp text
        timestamp_text = timestamp
        if encrypted:
            timestamp_text += " 🔒"

        # Build marker text
        marker_text = ""
        if direction == 1 and not is_carbon and not is_file:
            if marked == 0:
                marker_text = "⌛"
            elif marked == 1:
                marker_text = "✓"
            elif marked == 2:
                marker_text = "✓✓"
            elif marked == 7:
                marker_text = "✔✔"
            elif marked == 8:
                marker_text = "⚠"
        elif direction == 1 and is_carbon:
            marker_text = "🗎"

        # Get reactions
        content_item_id = index.data(self.ROLE_CONTENT_ITEM_ID)
        reactions = self._get_reactions(content_item_id)

        # Calculate bubble rect without QPainter
        bubble_rect = self._calculate_bubble_rect(
            None, option.rect, body, timestamp_text, marker_text, direction, msg_type, nickname,
            is_file, file_path, file_name, mime_type, reactions, font=option.font
        )

        # Calculate text area within bubble
        text_rect = bubble_rect.adjusted(
            self.padding, self.padding, -self.padding, -self.padding
        )

        # Adjust for nickname if present
        if msg_type == 1 and direction == 0 and nickname:
            nickname_font = QFont(option.font)
            nickname_font.setBold(True)
            nickname_font.setPointSize(option.font.pointSize() - 1)
            nickname_height = QFontMetrics(nickname_font).height() + 6
            text_rect.setTop(text_rect.top() + nickname_height)

        # Adjust for timestamp height at bottom
        timestamp_font = QFont(option.font)
        timestamp_font.setPointSize(option.font.pointSize() - 2)
        timestamp_height = QFontMetrics(timestamp_font).height() + self.spacing_between_text_and_time
        text_rect.setHeight(text_rect.height() - timestamp_height)

        # Check if mouse is over text area
        mouse_pos = event.position().toPoint() if hasattr(event.position(), 'toPoint') else event.pos()

        if not text_rect.contains(mouse_pos):
            # Mouse not over text area, restore default cursor
            if event.type() == QEvent.MouseMove:
                QApplication.restoreOverrideCursor()
            return super().editorEvent(event, model, option, index)

        # Get QTextDocument to check for anchors
        available_width = option.rect.width() - self.margin_left - self.margin_right
        max_bubble_width = int(available_width * self.max_bubble_width_ratio)
        max_text_width = max_bubble_width - 2 * self.padding
        text_color = self.sent_text_color if direction == 1 else self.received_text_color

        doc, _, _ = self._get_content_document(body, is_file, file_path, file_name, mime_type, option.font, max_text_width, text_color, direction)

        # Calculate position relative to document
        doc_pos = mouse_pos - text_rect.topLeft()

        # Check if there's an anchor at this position
        anchor = doc.documentLayout().anchorAt(doc_pos)

        if anchor:
            # Mouse over a link
            if event.type() == QEvent.MouseMove:
                # Change cursor to pointing hand
                QApplication.setOverrideCursor(Qt.PointingHandCursor)
            elif event.type() == QEvent.MouseButtonPress:
                # Store the URL for potential context menu
                if event.button() == Qt.RightButton:
                    self.last_clicked_url = anchor
                    # Don't consume the event - let it propagate for context menu
                    return False
            elif event.type() == QEvent.MouseButtonRelease:
                # Handle click on link
                if event.button() == Qt.LeftButton:
                    # Left-click: open URL in browser
                    QDesktopServices.openUrl(QUrl(anchor))
                    self.last_clicked_url = None
                    return True
            return True
        else:
            # Not over a link, restore cursor and clear last clicked URL
            if event.type() == QEvent.MouseMove:
                QApplication.restoreOverrideCursor()
            elif event.type() == QEvent.MouseButtonPress:
                self.last_clicked_url = None

        return super().editorEvent(event, model, option, index)

    def _paint_day_separator(self, painter, option, index):
        """
        Paint a day separator line with centered text.
        Format: "----------- Today -----------"
        """
        separator_text = index.data(self.ROLE_SEPARATOR_TEXT) or ""

        # Setup font - small, dimmed
        font = painter.font()
        separator_font = QFont(font)
        separator_font.setPointSize(font.pointSize() - 2)
        painter.setFont(separator_font)
        fm = QFontMetrics(separator_font)

        # Use timestamp color for dimmed appearance
        painter.setPen(self.timestamp_color)

        # Calculate text width
        text_width = fm.horizontalAdvance(separator_text)
        padding = 10  # Space between text and lines

        # Center text horizontally
        text_x = (option.rect.width() - text_width) // 2
        text_y = option.rect.top() + (option.rect.height() + fm.height()) // 2

        # Draw text
        painter.drawText(text_x, text_y, separator_text)

        # Draw lines on both sides
        line_y = option.rect.top() + option.rect.height() // 2
        left_line_end = text_x - padding
        right_line_start = text_x + text_width + padding

        # Left line
        if left_line_end > option.rect.left():
            painter.drawLine(option.rect.left() + 20, line_y, left_line_end, line_y)

        # Right line
        if right_line_start < option.rect.right():
            painter.drawLine(right_line_start, line_y, option.rect.right() - 20, line_y)
