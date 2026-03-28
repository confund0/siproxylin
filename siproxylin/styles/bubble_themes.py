"""
Bubble color themes for chat message display.

Each theme defines colors for message bubbles that match the overall theme aesthetic.
Colors are defined separately for sent and received messages.
"""

from PySide6.QtGui import QColor


BUBBLE_THEMES = {
    'light': {
        'sent_bg': '#dcf8c6',           # Light green (WhatsApp style)
        'sent_text': '#000000',         # Black text on light bubble
        'received_bg': '#e8e8e8',       # Light gray
        'received_text': '#212121',     # Dark text
        'timestamp': '#666666',         # Medium gray
        'marker_read': '#0088cc',       # Blue for read markers
        'unencrypted_sent_bg': '#ffcccc',     # Light red (warning)
        'unencrypted_received_bg': '#ffe0e0', # Very light red
        'url_sent': '#0066cc',          # Medium blue - classic hyperlink
        'url_received': '#1976d2',      # Material blue
    },

    'light_gray': {
        'sent_bg': '#b8d4b8',           # Muted sage green
        'sent_text': '#2a2a2a',         # Very dark gray
        'received_bg': '#c0c0c0',       # Lighter gray (subtle contrast against #d0d0d0 bg)
        'received_text': '#2a2a2a',     # Very dark gray (good contrast)
        'timestamp': '#606060',         # Darker gray
        'marker_read': '#4a7a4a',       # Darker green for read markers
        'unencrypted_sent_bg': '#d4a8a8',     # Muted red-gray
        'unencrypted_received_bg': '#d0a8a8', # Lighter red-gray
        'url_sent': '#0055aa',          # Darker blue - better contrast
        'url_received': '#1565c0',      # Darker material blue
    },

    'dark': {
        'sent_bg': '#3d6b3d',           # Dark green
        'sent_text': '#e8f5e8',         # Very light green tint
        'received_bg': '#3a3a3a',       # Dark gray
        'received_text': '#e0e0e0',     # Light gray
        'timestamp': '#b0b0b0',         # Medium light gray
        'marker_read': '#66ff66',       # Bright green for read markers
        'unencrypted_sent_bg': '#6b3d3d',     # Dark red
        'unencrypted_received_bg': '#4a2828', # Darker red
        'url_sent': '#66b3ff',          # Light blue - readable on dark green
        'url_received': '#64b5f6',      # Material light blue
    },

    'terminal': {
        'sent_bg': '#1a251a',           # Subtle dark green background (low intensity like received)
        'sent_text': '#66ff66',         # Bright terminal green text
        'received_bg': '#1a1a0a',       # Subtle dark background (just visible)
        'received_text': '#cccc00',     # Dimmed yellow text (less shouting)
        'timestamp': '#66ff66',         # Bright green
        'marker_read': '#00ff00',       # Extra bright green for read markers
        'unencrypted_sent_bg': '#4d1a1a',     # Dark terminal red
        'unencrypted_received_bg': '#1a0a0a', # Subtle dark background
        'url_sent': '#00ffff',          # Cyan - classic terminal link color
        'url_received': '#00aaff',      # Bright cyan-blue
    },

    'gruvbox': {
        'sent_bg': '#504945',           # Gruvbox gray2
        'sent_text': '#d79921',         # Gruvbox neutral yellow (peachy/apricot, not too bright)
        'received_bg': '#282828',       # Gruvbox dark0 (darker for more contrast)
        'received_text': '#ebdbb2',     # Gruvbox light cream foreground
        'timestamp': '#a89984',         # Gruvbox gray (muted)
        'marker_read': '#b8bb26',       # Gruvbox bright green for read markers
        'unencrypted_sent_bg': '#504040',     # Gruvbox gray with red tint
        'unencrypted_received_bg': '#3c1f1e', # Gruvbox dark with red tint
        'url_sent': '#83a598',          # Gruvbox bright blue
        'url_received': '#8ec07c',      # Gruvbox bright aqua
    },
}


def get_bubble_colors(theme_name: str) -> dict:
    """
    Get bubble colors for a theme.

    Args:
        theme_name: Theme name ('light', 'dark', 'terminal', 'gruvbox', 'light_gray')

    Returns:
        Dictionary with QColor objects for bubble styling, or default colors if theme not found
    """
    # Default to 'dark' if theme not found
    colors = BUBBLE_THEMES.get(theme_name, BUBBLE_THEMES['dark'])

    return {
        'sent_bg': QColor(colors['sent_bg']),
        'sent_text': QColor(colors['sent_text']),
        'received_bg': QColor(colors['received_bg']),
        'received_text': QColor(colors['received_text']),
        'timestamp': QColor(colors['timestamp']),
        'marker_read': QColor(colors['marker_read']),
        'unencrypted_sent_bg': QColor(colors['unencrypted_sent_bg']),
        'unencrypted_received_bg': QColor(colors['unencrypted_received_bg']),
        'url_sent': QColor(colors['url_sent']),
        'url_received': QColor(colors['url_received']),
    }
