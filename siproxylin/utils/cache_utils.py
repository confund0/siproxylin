"""
Cache management utilities for Siproxylin.

Handles cleanup of various cache directories to prevent corruption and bloat.
"""

import os
import sys
import shutil
import glob
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)


def cleanup_gstreamer_cache() -> None:
    """
    Clean up GStreamer registry cache on startup.

    GStreamer cache can become corrupted and cause video call failures.
    This function safely removes GStreamer cache directories on startup.

    Paths cleaned:
    - Linux: ~/.cache/gstreamer-1.0/, /tmp/gst-*
    - Windows: %LOCALAPPDATA%\gstreamer-1.0\,
               %LOCALAPPDATA%\Microsoft\Windows\INetCache\gstreamer-1.0\,
               %TEMP%\gst-*
    """
    cleaned_paths: List[str] = []
    failed_paths: List[str] = []

    try:
        if sys.platform == 'win32':
            # Windows: Multiple possible cache locations
            localappdata = os.getenv('LOCALAPPDATA', '')
            temp = os.getenv('TEMP', '')

            if localappdata:
                # 1. Common location: %LOCALAPPDATA%\gstreamer-1.0\
                gst_cache_1 = Path(localappdata) / 'gstreamer-1.0'
                if gst_cache_1.exists():
                    try:
                        shutil.rmtree(gst_cache_1)
                        cleaned_paths.append(str(gst_cache_1))
                    except Exception as e:
                        failed_paths.append(f"{gst_cache_1}: {e}")

                # 2. Default INetCache location: %LOCALAPPDATA%\Microsoft\Windows\INetCache\gstreamer-1.0\
                gst_cache_2 = Path(localappdata) / 'Microsoft' / 'Windows' / 'INetCache' / 'gstreamer-1.0'
                if gst_cache_2.exists():
                    try:
                        shutil.rmtree(gst_cache_2)
                        cleaned_paths.append(str(gst_cache_2))
                    except Exception as e:
                        failed_paths.append(f"{gst_cache_2}: {e}")

            # 3. Temp files: %TEMP%\gst-*
            if temp:
                temp_gst_pattern = str(Path(temp) / 'gst-*')
                tmp_gst_files = glob.glob(temp_gst_pattern)
                for tmp_file in tmp_gst_files:
                    try:
                        tmp_path = Path(tmp_file)
                        if tmp_path.is_dir():
                            shutil.rmtree(tmp_path)
                        else:
                            tmp_path.unlink()
                        cleaned_paths.append(tmp_file)
                    except Exception as e:
                        failed_paths.append(f"{tmp_file}: {e}")
        else:
            # Linux: ~/.cache/gstreamer-1.0/
            gst_cache = Path.home() / '.cache' / 'gstreamer-1.0'
            if gst_cache.exists():
                try:
                    shutil.rmtree(gst_cache)
                    cleaned_paths.append(str(gst_cache))
                except Exception as e:
                    failed_paths.append(f"{gst_cache}: {e}")

            # Linux: /tmp/gst-*
            tmp_gst_files = glob.glob('/tmp/gst-*')
            for tmp_file in tmp_gst_files:
                try:
                    tmp_path = Path(tmp_file)
                    if tmp_path.is_dir():
                        shutil.rmtree(tmp_path)
                    else:
                        tmp_path.unlink()
                    cleaned_paths.append(tmp_file)
                except Exception as e:
                    failed_paths.append(f"{tmp_file}: {e}")

        # Log results
        if cleaned_paths:
            logger.debug(f"GStreamer cache cleanup: Removed {len(cleaned_paths)} path(s)")
            for path in cleaned_paths:
                logger.debug(f"  ✓ Cleaned: {path}")
        else:
            logger.debug("GStreamer cache cleanup: No cache found (already clean)")

        if failed_paths:
            logger.warning(f"GStreamer cache cleanup: Failed to remove {len(failed_paths)} path(s)")
            for failure in failed_paths:
                logger.warning(f"  ✗ Failed: {failure}")

    except Exception as e:
        logger.error(f"GStreamer cache cleanup failed: {e}", exc_info=True)
