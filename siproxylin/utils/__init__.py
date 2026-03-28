"""
Utility modules for DRUNK-XMPP-GUI.
"""

from .paths import get_paths, Paths, PATH_MODE
from .logger import (
    setup_main_logger,
    setup_account_logger,
    get_account_logger,
    set_log_level,
    cleanup_old_logs
)
from .jid_utils import generate_resource
from .audio_devices import get_audio_device_manager, AudioDevice, AudioDeviceManager
from .file_utils import open_file_with_external_app, save_file_as
from .video_utils import generate_video_thumbnail, get_or_generate_thumbnail, get_cached_thumbnail_path

__all__ = [
    'get_paths',
    'Paths',
    'PATH_MODE',
    'setup_main_logger',
    'setup_account_logger',
    'get_account_logger',
    'set_log_level',
    'cleanup_old_logs',
    'generate_resource',
    'get_audio_device_manager',
    'AudioDevice',
    'AudioDeviceManager',
    'open_file_with_external_app',
    'save_file_as',
    'generate_video_thumbnail',
    'get_or_generate_thumbnail',
    'get_cached_thumbnail_path',
]
