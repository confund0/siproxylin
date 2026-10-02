"""
Video utility functions for thumbnail generation.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
import tempfile

logger = logging.getLogger('siproxylin.utils.video_utils')


# Worker script, run by file path in its own process (see the file docstring)
WORKER_PATH = Path(__file__).with_name('video_thumb_worker.py')

# Wall clock limit for one thumbnail. The child is killed after it.
WORKER_TIMEOUT = 15

# Variables that point the child at sockets of the user session.
# The child gets no window, bus, sound or agent access.
_DROP_ENV = (
    'DISPLAY',
    'WAYLAND_DISPLAY',
    'WAYLAND_SOCKET',
    'XAUTHORITY',
    'DBUS_SESSION_BUS_ADDRESS',
    'DBUS_SYSTEM_BUS_ADDRESS',
    'XDG_RUNTIME_DIR',
    'PULSE_SERVER',
    'PULSE_COOKIE',
    'PIPEWIRE_REMOTE',
    'PIPEWIRE_RUNTIME_DIR',
    'SESSION_MANAGER',
    'AT_SPI_BUS_ADDRESS',
    'SSH_AUTH_SOCK',
    'GPG_AGENT_INFO',
)

_unshare_logged = False


def _worker_env():
    """Copy of os.environ without session socket pointers."""
    env = dict(os.environ)
    for name in _DROP_ENV:
        env.pop(name, None)
    return env


def _process_options():
    """Options for the child: its own session (POSIX) or process group (Windows)."""
    if sys.platform == 'win32':
        # CREATE_NO_WINDOW: no console window flashes up
        return {'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def _log_worker_stderr(stderr, failed):
    """Log the child's stderr. The unshare message is logged once only."""
    global _unshare_logged
    lines = []
    for line in (stderr or '').splitlines():
        if 'unshare failed' in line:
            if not _unshare_logged:
                _unshare_logged = True
                logger.debug(f"Thumbnail worker runs without network isolation: {line}")
            continue
        lines.append(line)
    if lines:
        text = '\n'.join(lines)
        if failed:
            logger.warning(f"Thumbnail worker output:\n{text}")
        else:
            logger.debug(f"Thumbnail worker output:\n{text}")


def generate_video_thumbnail(video_path, output_path=None, width=320, height=0, seek_time=1.0):
    """
    Generate a thumbnail from a video file using VLC.

    VLC runs in a child process with resource limits (video_thumb_worker.py).
    A crash or hang there gives None, not a crash of the app.

    Args:
        video_path: Path to video file
        output_path: Optional output path for thumbnail (PNG format)
                    If None, creates in temp directory
        width: Thumbnail width in pixels (0 = original, recommended: 320)
        height: Thumbnail height in pixels (0 = preserve aspect ratio)
        seek_time: Time in seconds to seek to for thumbnail (default 1.0)

    Returns:
        str: Path to generated thumbnail, or None if failed
    """
    tmp_path = None
    try:
        # Check if video exists
        if not Path(video_path).exists():
            logger.error(f"Video file not found: {video_path}")
            return None

        # Generate output path if not provided
        if output_path is None:
            video_name = Path(video_path).stem
            output_path = Path(tempfile.gettempdir()) / f"vlc_thumb_{video_name}.png"
        else:
            output_path = Path(output_path)

        # Ensure output directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Remove existing thumbnail if present
        if output_path.exists():
            output_path.unlink()

        # The child writes to a temp name; we rename it only on success
        fd, tmp_name = tempfile.mkstemp(prefix='.thumb-', suffix='.png', dir=output_path.parent)
        os.close(fd)
        tmp_path = Path(tmp_name)

        cmd = [sys.executable, str(WORKER_PATH), str(video_path), str(tmp_path),
               str(int(width)), str(int(height)), str(float(seek_time))]
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                errors='replace',
                timeout=WORKER_TIMEOUT,
                env=_worker_env(),
                close_fds=True,
                **_process_options(),
            )
        except subprocess.TimeoutExpired as e:
            # subprocess.run() has killed the child already
            stderr = e.stderr.decode(errors='replace') if isinstance(e.stderr, bytes) else e.stderr
            _log_worker_stderr(stderr, failed=True)
            logger.warning(f"Thumbnail worker timed out after {WORKER_TIMEOUT}s: {video_path}")
            return None

        failed = result.returncode != 0
        _log_worker_stderr(result.stderr, failed)
        if result.stdout:
            logger.debug(f"Thumbnail worker stdout: {result.stdout.strip()}")

        if failed:
            logger.warning(f"Thumbnail worker failed (exit {result.returncode}): {video_path}")
            return None

        if not (tmp_path.exists() and tmp_path.stat().st_size > 0):
            logger.warning("Thumbnail file not created or empty")
            return None

        os.replace(tmp_path, output_path)
        tmp_path = None
        logger.info(f"Generated thumbnail: {output_path}")
        return str(output_path)

    except Exception as e:
        logger.error(f"Failed to generate video thumbnail: {e}", exc_info=True)
        return None
    finally:
        # Remove leftovers on failure
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                logger.debug(f"Could not remove temp thumbnail {tmp_path}: {e}")


def get_cached_thumbnail_path(video_path, cache_dir):
    """
    Get path for cached video thumbnail.

    Args:
        video_path: Path to video file
        cache_dir: Cache directory for thumbnails

    Returns:
        Path: Path where thumbnail should be cached
    """
    import hashlib

    # Generate unique filename based on video path
    video_path_str = str(Path(video_path).resolve())
    path_hash = hashlib.md5(video_path_str.encode()).hexdigest()

    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    return cache_dir / f"{path_hash}.png"


def get_or_generate_thumbnail(video_path, cache_dir, width=320, height=0):
    """
    Get cached thumbnail or generate new one.

    Args:
        video_path: Path to video file
        cache_dir: Directory for thumbnail cache
        width: Thumbnail width
        height: Thumbnail height

    Returns:
        str: Path to thumbnail, or None if failed
    """
    # Check cache first
    cached_path = get_cached_thumbnail_path(video_path, cache_dir)

    if cached_path.exists():
        # Verify video file hasn't been modified since thumbnail was created
        video_mtime = Path(video_path).stat().st_mtime
        thumb_mtime = cached_path.stat().st_mtime

        if thumb_mtime > video_mtime:
            logger.debug(f"Using cached thumbnail: {cached_path}")
            return str(cached_path)
        else:
            logger.debug("Video modified since thumbnail, regenerating")
            cached_path.unlink()

    # Generate new thumbnail
    logger.info(f"Generating thumbnail for: {video_path}")
    return generate_video_thumbnail(video_path, output_path=cached_path, width=width, height=height)
