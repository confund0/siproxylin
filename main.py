#!/usr/bin/env python3
"""
DRUNK-XMPP-GUI - Privacy-focused XMPP desktop client

Main entry point for the application.
"""

import sys
import os
import argparse
from pathlib import Path

# Add project root to path for drunk-xmpp.py import
sys.path.insert(0, str(Path(__file__).parent))


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='DRUNK-XMPP-GUI - Privacy-focused XMPP desktop client'
    )
    parser.add_argument(
        '--profile',
        default='default',
        help='Profile name (default: default)'
    )
    parser.add_argument(
        '--log-level',
        default='INFO',
        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
        help='Log level (default: INFO)'
    )
    parser.add_argument(
        '--xdg',
        action='store_true',
        help='Use XDG Base Directory paths (~/.config, ~/.local/share, ~/.cache)'
    )
    parser.add_argument(
        '--dot-data-dir',
        action='store_true',
        help='Use ~/.siproxylin directory for all data (default for AppImage)'
    )
    return parser.parse_args()


def main():
    """Main application entry point."""
    args = parse_args()

    # Set path mode based on arguments BEFORE importing siproxylin modules
    # (paths.py reads PATH_MODE at import time, so this must happen first)
    if args.dot_data_dir:
        os.environ['SIPROXYLIN_PATH_MODE'] = 'dot'
    elif args.xdg:
        os.environ['SIPROXYLIN_PATH_MODE'] = 'xdg'
    # else: defaults to 'dev' mode

    # NOW import siproxylin modules (after PATH_MODE is set)
    from PySide6.QtWidgets import QApplication, QToolTip
    from PySide6.QtCore import Qt
    import qasync
    import asyncio
    import platform

    from siproxylin.utils import setup_main_logger, get_paths
    from siproxylin.db.database import get_db
    from siproxylin.gui.main_window import MainWindow
    from siproxylin.core import get_account_manager

    # Enable high DPI scaling BEFORE creating QApplication
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)

    # Get paths first (needed for config loading)
    paths = get_paths(args.profile)

    # Load logging configuration (optional overrides)
    import json
    import logging
    logging_config_path = paths.config_dir / 'logging.json'
    logging_config = {
        'main_log_enabled': True,
        'main_log_level': 'INFO',
        'xml_log_enabled': True
    }

    if logging_config_path.exists():
        try:
            with open(logging_config_path, 'r') as f:
                user_config = json.load(f)
                logging_config.update(user_config)  # Override defaults with user settings
        except Exception as e:
            # Can't log yet, just print to console
            print(f"Warning: Failed to load logging config: {e}, using defaults")

    # Setup main logger with user preferences (or command line override)
    if logging_config.get('main_log_enabled', True):
        # Command line --log-level takes precedence over config file
        log_level = args.log_level if args.log_level != 'INFO' else logging_config.get('main_log_level', 'INFO')
        logger = setup_main_logger(log_level)
        if logging_config_path.exists():
            logger.debug(f"Loaded logging config from {logging_config_path}")
    else:
        # Minimal console-only logger if user disabled main log
        logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
        logger = logging.getLogger('siproxylin')
        logger.info("Main log file disabled by user configuration")

    logger.info("=" * 60)
    logger.info("DRUNK-XMPP-GUI Starting...")
    logger.info("=" * 60)
    logger.info(f"Profile: {args.profile}")
    logger.info(f"Log level: {log_level if logging_config.get('main_log_enabled', True) else 'WARNING (console only)'}")

    # Setup global XML logging (shared by all XMPP connections)
    if logging_config.get('xml_log_enabled', True):
        from logging.handlers import RotatingFileHandler
        xml_log_path = paths.log_dir / 'xmpp-protocol.log'

        # Configure slixmpp root logger to allow propagation
        slixmpp_root = logging.getLogger('slixmpp')
        slixmpp_root.setLevel(logging.DEBUG)

        # Configure the slixmpp XMLstream logger for SEND/RECV output
        xml_logger = logging.getLogger('slixmpp.xmlstream.xmlstream')
        xml_logger.setLevel(logging.DEBUG)
        xml_logger.propagate = False  # Don't propagate to parent logger

        xml_handler = RotatingFileHandler(
            xml_log_path,
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        xml_handler.setLevel(logging.DEBUG)
        xml_formatter = logging.Formatter('%(asctime)s - %(message)s')
        xml_handler.setFormatter(xml_formatter)
        xml_logger.addHandler(xml_handler)

        logger.debug(f"XML protocol logging enabled: {xml_log_path}")
    else:
        logger.info("XML protocol logging disabled by user configuration")

    # Show paths
    logger.info(f"Config dir: {paths.config_dir}")
    logger.info(f"Data dir: {paths.data_dir}")
    logger.info(f"Cache dir: {paths.cache_dir}")
    logger.info(f"Log dir: {paths.log_dir}")
    logger.info(f"Database: {paths.database_path}")

    # Auto-fix audio card profiles (Linux only)
    # Switches USB audio cards to duplex profiles so both mic and speakers are available
    from siproxylin.utils.audio_profiles import fix_audio_card_profiles
    fix_audio_card_profiles()

    # Initialize database
    logger.info("Initializing database...")
    try:
        db = get_db()
        db.initialize()  # Run schema creation + migrations (blocking)
        logger.info("Database initialized successfully")
    except RuntimeError as e:
        logger.error(f"Failed to initialize database: {e}")
        print(f"\nERROR: {e}")
        print("Please close the other instance before starting a new one.\n")
        return 1

    # Initialize account manager (but don't load accounts yet - need asyncio loop first)
    logger.info("Initializing account manager...")
    account_manager = get_account_manager()
    logger.info("Account manager initialized")

    # Create Qt application
    app = QApplication(sys.argv)
    app.setApplicationName("Siproxylin")
    app.setOrganizationName("Siproxylin")
    app.setOrganizationDomain("siproxylin.local")

    # Set application icon (taskbar, window, notifications)
    from PySide6.QtGui import QIcon
    icon_dir = Path(__file__).parent / "siproxylin" / "resources" / "icons"
    # Prefer .ico on Windows (better taskbar/notification support), SVG elsewhere
    if platform.system() == "Windows":
        icon_path = icon_dir / "siproxylin.ico"
        if not icon_path.exists():
            icon_path = icon_dir / "siproxylin.svg"  # Fallback to SVG
    else:
        icon_path = icon_dir / "siproxylin.svg"

    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
        logger.info(f"Application icon set: {icon_path}")
    else:
        logger.warning(f"Application icon not found: {icon_path}")

    # Windows-specific: Set AppUserModelID for proper taskbar icon grouping
    # Without this, Windows taskbar shows default Python icon instead of app icon
    if platform.system() == "Windows":
        try:
            import ctypes
            myappid = 'com.siproxylin.siproxylin.1.0'  # Arbitrary string
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
            logger.info(f"Windows AppUserModelID set: {myappid}")
        except Exception as e:
            logger.warning(f"Failed to set Windows AppUserModelID: {e}")

    # Configure tooltips
    QToolTip.setFont(app.font())  # Use application font for tooltips

    # Load bundled emoji font on Windows for better emoji rendering
    # (Linux uses system fonts-noto-color-emoji from AppImage)
    if platform.system() == "Windows":
        from PySide6.QtGui import QFontDatabase
        emoji_font_path = Path(__file__).parent / "siproxylin" / "resources" / "fonts" / "windows" / "NotoColorEmoji_WindowsCompatible.ttf"
        if emoji_font_path.exists():
            font_id = QFontDatabase.addApplicationFont(str(emoji_font_path))
            if font_id != -1:
                logger.info("Loaded bundled Noto Color Emoji font for Windows")
            else:
                logger.warning("Failed to load bundled emoji font, using system default")
        else:
            logger.warning(f"Windows emoji font not found at {emoji_font_path}, using system default")

    # Install event filter to increase tooltip show delay
    # Default Qt behavior: shows after ~700ms, hides after ~5000ms
    # We'll increase the show delay to ~1200ms for better UX
    from siproxylin.gui.utils.tooltip_filter import TooltipEventFilter
    tooltip_filter = TooltipEventFilter(delay_ms=1200)
    app.installEventFilter(tooltip_filter)
    logger.info("Tooltip configuration: show delay increased to 1200ms")

    # Create asyncio event loop integrated with Qt
    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)
    logger.info("Asyncio event loop integrated with Qt")

    # Create and show main window
    logger.info("Creating main window...")
    window = MainWindow()

    # Set icon on main window explicitly (needed on Windows for taskbar icon)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))

    window.show()

    # Setup signal handlers for graceful shutdown (Ctrl+C, SIGTERM)
    import signal

    def handle_exit_signal(signum, frame):
        """Handle SIGINT (Ctrl+C) and SIGTERM gracefully."""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        window._signal_shutdown = True  # Flag to skip gRPC shutdown (Go handles signal itself)
        window.close()  # Triggers closeEvent() → existing cleanup logic

    signal.signal(signal.SIGINT, handle_exit_signal)
    signal.signal(signal.SIGTERM, handle_exit_signal)
    logger.info("Signal handlers registered (SIGINT, SIGTERM)")

    # Now that asyncio loop is set, load and connect accounts
    logger.info("Loading accounts...")
    account_manager.load_accounts()
    logger.info(f"Loaded {len(account_manager.accounts)} accounts")

    # Setup account indicators in GUI
    window.setup_accounts()

    logger.info("Application ready!")
    logger.info("=" * 60)

    # Run application (qasync integrates asyncio with Qt's event loop)
    with loop:
        sys.exit(loop.run_forever())


if __name__ == '__main__':
    sys.exit(main())
