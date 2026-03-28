"""
Theme manager for DRUNK-XMPP-GUI.

Manages theme switching and font scaling.
"""

import logging
import json
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from .roster_themes import RosterStyle, create_roster_style


logger = logging.getLogger('siproxylin.theme_manager')


class ThemeManager:
    """Manages application themes and font scaling."""

    def __init__(self, config_dir=None):
        self.current_theme = 'light_gray'
        self.font_scale = 1.0  # Base scale factor
        self.base_font_size = 11  # Base font size in points
        self.roster_mode = 'classic'  # 'classic' (emoji) or 'ascii' (text-only)

        # Config file for persistence
        if config_dir:
            self.config_file = Path(config_dir) / 'theme.json'
        else:
            self.config_file = None

        # Load saved preferences
        self._load_preferences()

    def load_theme(self, theme_name: str, save=True):
        """
        Load and apply a theme.

        Args:
            theme_name: 'light' or 'dark'
            save: Whether to save this preference (default True)
        """
        theme_path = Path(__file__).parent / f"{theme_name}_theme.qss"

        if not theme_path.exists():
            logger.error(f"Theme file not found: {theme_path}")
            return False

        try:
            with open(theme_path, 'r', encoding='utf-8') as f:
                stylesheet = f.read()

            # Replace font size placeholders with scaled values
            stylesheet = self._apply_font_scaling(stylesheet)

            app = QApplication.instance()
            if app:
                app.setStyleSheet(stylesheet)
                self.current_theme = theme_name
                logger.info(f"Theme loaded: {theme_name} (scale: {self.font_scale})")

                # Save preference
                if save:
                    self._save_preferences()

                return True

        except Exception as e:
            logger.error(f"Failed to load theme: {e}")
            return False

    def increase_font_size(self):
        """Increase font size by 10%."""
        self.font_scale = min(self.font_scale + 0.1, 3.0)  # Max 3x
        self._apply_current_theme()
        self._save_preferences()
        logger.info(f"Font scale increased to {self.font_scale:.1f}")

    def decrease_font_size(self):
        """Decrease font size by 10%."""
        self.font_scale = max(self.font_scale - 0.1, 0.5)  # Min 0.5x
        self._apply_current_theme()
        self._save_preferences()
        logger.info(f"Font scale decreased to {self.font_scale:.1f}")

    def reset_font_size(self):
        """Reset font size to default."""
        self.font_scale = 1.0
        self._apply_current_theme()
        self._save_preferences()
        logger.info("Font scale reset to 1.0")

    def set_roster_mode(self, mode: str):
        """
        Set roster display mode.

        Args:
            mode: 'classic' (emoji icons) or 'ascii' (text-only)
        """
        if mode not in ('classic', 'ascii'):
            logger.warning(f"Invalid roster mode: {mode}")
            return

        self.roster_mode = mode
        self._save_preferences()
        logger.info(f"Roster mode set to: {mode}")

    def get_roster_style(self) -> RosterStyle:
        """
        Get complete roster styling for current theme and mode.

        Returns:
            RosterStyle with all styling configured
        """
        # Classic mode = emoji + steady colors
        # ASCII mode = text indicators + dynamic colors
        uses_emoji = (self.roster_mode == 'classic')
        return create_roster_style(self.current_theme, uses_emoji=uses_emoji)

    def _apply_current_theme(self):
        """Reapply current theme with updated font scale."""
        self.load_theme(self.current_theme, save=False)

    def _apply_font_scaling(self, stylesheet: str) -> str:
        """
        Apply font scaling to stylesheet.

        Replaces {{BASE_FONT_SIZE}} with scaled value.

        Args:
            stylesheet: Original stylesheet content

        Returns:
            Stylesheet with scaled font sizes
        """
        scaled_size = int(self.base_font_size * self.font_scale)
        return stylesheet.replace('{{BASE_FONT_SIZE}}', str(scaled_size))

    def _load_preferences(self):
        """Load saved theme preferences from config file."""
        if not self.config_file or not self.config_file.exists():
            return

        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                prefs = json.load(f)

            self.current_theme = prefs.get('theme', 'dark')
            self.font_scale = prefs.get('font_scale', 1.0)
            self.roster_mode = prefs.get('roster_mode', 'classic')

            logger.info(f"Loaded preferences: theme={self.current_theme}, font_scale={self.font_scale}, roster_mode={self.roster_mode}")

        except Exception as e:
            logger.warning(f"Failed to load theme preferences: {e}")

    def _save_preferences(self):
        """Save theme preferences to config file."""
        if not self.config_file:
            return

        try:
            # Ensure config directory exists
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            prefs = {
                'theme': self.current_theme,
                'font_scale': self.font_scale,
                'roster_mode': self.roster_mode
            }

            with open(self.config_file, 'w') as f:
                json.dump(prefs, f, indent=2)

            logger.debug(f"Saved preferences: theme={self.current_theme}, font_scale={self.font_scale}, roster_mode={self.roster_mode}")

        except Exception as e:
            logger.warning(f"Failed to save theme preferences: {e}")


# Global singleton instance
_theme_manager = None


def get_theme_manager(config_dir=None) -> ThemeManager:
    """
    Get the global theme manager instance.

    Args:
        config_dir: Optional config directory path for persistence
    """
    global _theme_manager
    if _theme_manager is None:
        _theme_manager = ThemeManager(config_dir=config_dir)
    return _theme_manager
