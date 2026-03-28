"""
Video utility functions for thumbnail generation.
"""

import logging
from pathlib import Path
import tempfile

logger = logging.getLogger('siproxylin.utils.video_utils')


def generate_video_thumbnail(video_path, output_path=None, width=320, height=0, seek_time=1.0):
    """
    Generate a thumbnail from a video file using VLC.

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
    try:
        import vlc
        import time

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

        # Create VLC instance with minimal output
        vlc_args = [
            '--no-audio',  # No audio needed for thumbnail
            '--no-video-title-show',  # No title overlay
            '--no-osd',  # No on-screen display
            '--snapshot-format=png',  # PNG format
            '--vout=dummy',  # Dummy video output (no window)
        ]

        instance = vlc.Instance(vlc_args)
        if not instance:
            logger.error("Failed to create VLC instance for thumbnail")
            return None

        # Create media player
        player = instance.media_player_new()
        if not player:
            logger.error("Failed to create media player for thumbnail")
            instance.release()
            return None

        # Load media
        media = instance.media_new(str(video_path))
        if not media:
            logger.error("Failed to create media for thumbnail")
            player.release()
            instance.release()
            return None

        player.set_media(media)
        media.release()

        # Start playback (needed to decode frames)
        player.play()

        # Wait for video to start
        max_wait = 5.0  # Maximum 5 seconds
        start_time = time.time()
        while time.time() - start_time < max_wait:
            state = player.get_state()
            if state == vlc.State.Playing:
                break
            time.sleep(0.1)

        if player.get_state() != vlc.State.Playing:
            logger.warning("Video did not start playing for thumbnail")
            player.stop()
            player.release()
            instance.release()
            return None

        # Seek to desired position
        if seek_time > 0:
            duration = player.get_length()
            if duration > 0:
                # Ensure we don't seek past the end
                seek_pos = min(seek_time * 1000, duration - 1000) / duration
                player.set_position(seek_pos)
                time.sleep(0.3)  # Wait for seek to complete

        # Take snapshot
        result = player.video_take_snapshot(0, str(output_path), width, height)

        if result == 0:
            # Wait a bit for file to be written
            time.sleep(0.2)

            # Verify file was created
            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"Generated thumbnail: {output_path}")
                player.stop()
                player.release()
                instance.release()
                return str(output_path)
            else:
                logger.warning("Thumbnail file not created or empty")
        else:
            logger.warning(f"video_take_snapshot returned {result}")

        # Cleanup
        player.stop()
        player.release()
        instance.release()
        return None

    except ImportError:
        logger.error("python-vlc not installed, cannot generate thumbnails")
        return None
    except Exception as e:
        logger.error(f"Failed to generate video thumbnail: {e}", exc_info=True)
        return None


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
