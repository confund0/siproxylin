"""
Video thumbnail worker. Runs in its own process.

Received videos are untrusted. libVLC parses them here, not in the app
process. A VLC fault then gives only "no thumbnail".

Do not import the siproxylin package here. The parent runs this file by path.

Usage: video_thumb_worker.py VIDEO_PATH OUTPUT_PATH WIDTH HEIGHT [SEEK_TIME]
Exit code 0: PNG written to OUTPUT_PATH. Other exit codes: failed.
Messages go to stderr. The parent logs them.
"""

import ctypes
import ctypes.util
import os
import sys
import time

# POSIX only. Without it (Windows) the worker runs with no limits.
try:
    import resource
except ImportError:
    resource = None

# Limits for this process (set before VLC loads)
CPU_SECONDS = 20
# Address space limit. VLC with decoder threads needs a lot of virtual memory.
ADDRESS_SPACE_BYTES = 2 * 1024 * 1024 * 1024

CLONE_NEWUSER = 0x10000000
CLONE_NEWNET = 0x40000000


def log(msg):
    print(f"video_thumb_worker: {msg}", file=sys.stderr, flush=True)


def set_limits():
    """
    Best effort: set resource limits. No core dumps, CPU and memory caps.
    A limit that fails (for example RLIMIT_AS on macOS) is logged and skipped.
    """
    if resource is None:
        log("no resource module, limits not set")
        return
    limits = (
        ('RLIMIT_CORE', (0, 0)),
        ('RLIMIT_CPU', (CPU_SECONDS, CPU_SECONDS + 5)),
        ('RLIMIT_AS', (ADDRESS_SPACE_BYTES, ADDRESS_SPACE_BYTES)),
    )
    for name, value in limits:
        try:
            resource.setrlimit(getattr(resource, name), value)
        except (AttributeError, ValueError, OSError) as e:
            log(f"{name} not set: {e}")


def _write_file(path, text):
    with open(path, 'w') as f:
        f.write(text)


def drop_network():
    """
    Best effort, Linux only: move into a new user and network namespace (no network).
    Returns True on success. Failure (EPERM, userns not allowed) is not fatal.
    """
    uid = os.getuid()
    gid = os.getgid()
    try:
        libc = ctypes.CDLL(ctypes.util.find_library('c') or 'libc.so.6', use_errno=True)
        if libc.unshare(CLONE_NEWUSER | CLONE_NEWNET) != 0:
            err = ctypes.get_errno()
            log(f"unshare failed: {os.strerror(err)}")
            return False
    except Exception as e:
        log(f"unshare failed: {e}")
        return False

    # Map our own uid/gid, or new files can not be created (EOVERFLOW)
    try:
        _write_file('/proc/self/setgroups', 'deny')
        _write_file('/proc/self/uid_map', f'{uid} {uid} 1')
        _write_file('/proc/self/gid_map', f'{gid} {gid} 1')
    except OSError as e:
        log(f"uid/gid map failed: {e}")
    return True


def take_snapshot(video_path, output_path, width, height, seek_time):
    """Take one PNG snapshot with VLC. Returns True on success."""
    import vlc

    vlc_args = [
        '--no-audio',  # No audio needed for thumbnail
        '--no-video-title-show',  # No title overlay
        '--no-osd',  # No on-screen display
        '--snapshot-format=png',  # PNG format
        '--vout=dummy',  # Dummy video output (no window)
    ]

    instance = vlc.Instance(vlc_args)
    if not instance:
        log("Failed to create VLC instance")
        return False

    player = instance.media_player_new()
    if not player:
        log("Failed to create media player")
        return False

    media = instance.media_new(video_path)
    if not media:
        log("Failed to create media")
        return False

    player.set_media(media)
    media.release()

    # Start playback (needed to decode frames)
    player.play()

    # Wait for video to start
    max_wait = 5.0  # Maximum 5 seconds
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if player.get_state() == vlc.State.Playing:
            break
        time.sleep(0.1)

    if player.get_state() != vlc.State.Playing:
        log("Video did not start playing")
        return False

    # Seek to desired position
    if seek_time > 0:
        duration = player.get_length()
        if duration > 0:
            # Ensure we don't seek past the end
            seek_pos = min(seek_time * 1000, duration - 1000) / duration
            player.set_position(seek_pos)
            time.sleep(0.3)  # Wait for seek to complete

    result = player.video_take_snapshot(0, output_path, width, height)
    if result != 0:
        log(f"video_take_snapshot returned {result}")
        return False

    # Wait a bit for file to be written
    time.sleep(0.2)

    if not (os.path.exists(output_path) and os.path.getsize(output_path) > 0):
        log("Thumbnail file not created or empty")
        return False

    # No player cleanup: the process exits now
    return True


def main(argv):
    if len(argv) not in (5, 6):
        log("usage: video_thumb_worker.py VIDEO OUTPUT WIDTH HEIGHT [SEEK_TIME]")
        return 2

    video_path, output_path = argv[1], argv[2]
    try:
        width, height = int(argv[3]), int(argv[4])
        seek_time = float(argv[5]) if len(argv) == 6 else 1.0
    except ValueError:
        log("bad width, height or seek time")
        return 2

    set_limits()
    if sys.platform.startswith('linux'):
        drop_network()

    try:
        ok = take_snapshot(video_path, output_path, width, height, seek_time)
    except ImportError:
        log("python-vlc not installed")
        return 3
    except Exception as e:
        log(f"failed: {e}")
        return 1

    # os._exit: skip VLC teardown, it can hang or crash on bad files
    sys.stderr.flush()
    os._exit(0 if ok else 1)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
