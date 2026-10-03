# Theme System

## Overview

The DRUNK-XMPP-GUI theme system provides:
- **Light and Dark themes** with complete color schemes
- **Font scaling** via View -> Font Size menu (±10% steps, range 0.5x-3.0x)
- **Separated styling** in external QSS files for easy customization
- **Focused styling** for chat components (contacts, messages, input, status bar)

## Architecture

```
app/styles/
├── theme_manager.py      # ThemeManager singleton
├── dark_theme.qss        # Dark theme stylesheet
├── light_theme.qss       # Light theme stylesheet
└── README.md             # This file
```

## Usage

### Switching Themes

Themes are switched via the View menu:
- **View -> Theme -> Light**
- **View -> Theme -> Dark**

Default theme is **Dark**.

### Font Scaling

Font sizes can be adjusted via View menu:
- **View -> Font Size -> Increase** (Ctrl++)
- **View -> Font Size -> Decrease** (Ctrl+-)
- **View -> Font Size -> Reset** (Ctrl+0)

Font scaling uses a base font size of **10pt** and scales proportionally.

### Programmatic Access

```python
from app.styles.theme_manager import get_theme_manager

theme_manager = get_theme_manager()

# Switch theme
theme_manager.load_theme('dark')  # or 'light'

# Font scaling
theme_manager.increase_font_size()
theme_manager.decrease_font_size()
theme_manager.reset_font_size()
```

## Theme Components

### Focused Styling (Chat Components)

These components have detailed, separate styling:

1. **Contact List / Roster** (`ContactListWidget`)
   - Search box
   - Contact tree with hover/selection states
   - Presence indicators (colored dots)

2. **Chat View - Message Area** (`ChatViewWidget`)
   - Header with contact name
   - Message display area
   - Scroll behavior

3. **Chat View - Input Area** (`ChatViewWidget`)
   - Input text field
   - OMEMO encryption toggle button (green when locked, red when unlocked)
   - Send button

4. **Status Bar** (`QStatusBar`)
   - Account connection indicators
   - Dynamic color based on connection status

### Generic Styling

Common widgets use simpler, unified styling:
- Menu bar and menus
- Buttons
- Line edits
- Splitter handles
- Scrollbars

## Creating New Themes

To create a new theme:

1. Copy an existing theme file (e.g., `dark_theme.qss`)
2. Rename it (e.g., `custom_theme.qss`)
3. Modify colors and styles as needed
4. Use `{{BASE_FONT_SIZE}}` placeholder for font sizes that should scale
5. Load it: `theme_manager.load_theme('custom_theme')`

### Font Size Placeholder

The theme manager replaces `{{BASE_FONT_SIZE}}` with the scaled font size value.

Example:
```css
QLabel {
    font-size: {{BASE_FONT_SIZE}}pt;  /* Becomes 10pt at 1.0x scale */
}
```

## Widget Object Names

Key widgets use `setObjectName()` for QSS targeting:

### ChatViewWidget
- `chatHeader` - Header frame
- `contactLabel` - Contact name label
- `messageArea` - Message display QTextEdit
- `inputFrame` - Input area frame
- `inputField` - Message input QLineEdit
- `encryptionButton` - OMEMO toggle QToolButton
- `sendButton` - Send message QPushButton

### Dynamic Properties

Some widgets use dynamic properties for state-based styling:

**Status bar labels:**
```python
label.setProperty('connectedStatus', True)   # Green
label.setProperty('connectedStatus', False)  # Gray
```

QSS selector:
```css
QStatusBar QLabel[connectedStatus="true"] {
    color: #4caf50;
}
```

## Color Scheme

### Dark Theme
- **Background**: #1e1e1e (main), #252525 (sidebar), #2d2d2d (panels)
- **Text**: #e0e0e0
- **Accent**: #0d7377 (teal)
- **Borders**: #3a3a3a
- **Success**: #4caf50 (green)
- **Error**: #f44336 (red)

### Light Theme
- **Background**: #ffffff (main), #fafafa (sidebar), #eeeeee (panels)
- **Text**: #212121
- **Accent**: #1976d2 (blue)
- **Borders**: #d0d0d0
- **Success**: #4caf50 (green)
- **Error**: #f44336 (red)
