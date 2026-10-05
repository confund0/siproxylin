"""
Video frames from the call service over shared memory (Linux only).

The app makes a memfd, sets up the header and passes the fd number to the
call service in the env variable SIPROXYLIN_VIDEO_SHM_FD. The service writes
decoded frames into it; the app reads them on a Qt timer.
C++ side: drunk_call_service/src/video_shm.h (the layout and the constants
must match).

Layout (little-endian, native alignment):
- Header at offset 0 (64 bytes):
    u32 magic 0x46565053 ("SPVF"), u32 version 1, u32 stream_count 2,
    u32 slot_count 3, u32 max_width 960, u32 max_height 960,
    u64 slot_bytes (960*960*4), u64 data_offset
- Stream control blocks, 64 bytes each, right after the header
  (stream 0 = remote video, stream 1 = self-view):
    u32 latest (slot of the newest ready frame, 0xFFFFFFFF = none),
    u32 reader (slot the app draws now, 0xFFFFFFFF = none; only the app
                writes it),
    u32 generation (the service adds 1 when a video stream starts),
    u32 active (1 while video flows, 0 after stop)
- Slot headers, 64 bytes each, after the control blocks, for stream s
  slot i at index s*slot_count + i:
    u64 seq (odd while being written, even when ready), u32 width,
    u32 height, u32 stride, u32 generation, u64 pts_ns
- Pixel data at data_offset (4096-aligned): stream s slot i at
  data_offset + (s*slot_count + i)*slot_bytes. Format RGBx.

Reader (this module): read latest L, store reader = L, then read latest and
the seq of L again; if latest changed or seq is odd, try again. The writer
never writes the slot in reader, so the frame stays stable while the app
draws it. Slot header values are checked before use.
"""

import mmap
import os
import struct
import sys
from typing import NamedTuple, Optional

MAGIC = 0x46565053  # "SPVF"
VERSION = 1
STREAM_COUNT = 2
SLOT_COUNT = 3
MAX_WIDTH = 960
MAX_HEIGHT = 960
SLOT_BYTES = MAX_WIDTH * MAX_HEIGHT * 4
NONE = 0xFFFFFFFF

STREAM_REMOTE = 0
STREAM_SELF = 1

HEADER_SIZE = 64
CONTROL_SIZE = 64
SLOT_HEADER_SIZE = 64
META_SIZE = HEADER_SIZE + STREAM_COUNT * CONTROL_SIZE + STREAM_COUNT * SLOT_COUNT * SLOT_HEADER_SIZE
DATA_OFFSET = (META_SIZE + 4095) // 4096 * 4096
TOTAL_SIZE = DATA_OFFSET + STREAM_COUNT * SLOT_COUNT * SLOT_BYTES

ENV_FD = 'SIPROXYLIN_VIDEO_SHM_FD'

# Control block field offsets
CTL_LATEST = 0
CTL_READER = 4
CTL_GENERATION = 8
CTL_ACTIVE = 12

# Slot header field offsets
SLOT_SEQ = 0
SLOT_WIDTH = 8
SLOT_HEIGHT = 12
SLOT_STRIDE = 16
SLOT_GENERATION = 20
SLOT_PTS = 24

READ_TRIES = 3

_HEADER_FMT = '<IIIIIIQQ'


def control_offset(stream: int) -> int:
    return HEADER_SIZE + stream * CONTROL_SIZE


def slot_header_offset(stream: int, slot: int) -> int:
    return HEADER_SIZE + STREAM_COUNT * CONTROL_SIZE + (stream * SLOT_COUNT + slot) * SLOT_HEADER_SIZE


def slot_data_offset(stream: int, slot: int) -> int:
    return DATA_OFFSET + (stream * SLOT_COUNT + slot) * SLOT_BYTES


def init_header(mm) -> None:
    """Write the header and an empty state (no frame, not active)."""
    struct.pack_into(_HEADER_FMT, mm, 0, MAGIC, VERSION, STREAM_COUNT, SLOT_COUNT,
                     MAX_WIDTH, MAX_HEIGHT, SLOT_BYTES, DATA_OFFSET)
    for s in range(STREAM_COUNT):
        struct.pack_into('<IIII', mm, control_offset(s), NONE, NONE, 0, 0)


class Frame(NamedTuple):
    slot: int
    seq: int
    data: memoryview  # stride * height bytes of the slot, no copy
    width: int
    height: int
    stride: int
    generation: int


class Reader:
    """Reads frames from the shared memory. Use it from one thread only."""

    def __init__(self, mm):
        self._mm = mm
        self._bytes = memoryview(mm)
        # Typed views give single 4 and 8 byte loads and stores
        self._u32 = self._bytes.cast('I')
        self._u64 = self._bytes.cast('Q')
        self._held = [NONE] * STREAM_COUNT

    def _load32(self, offset: int) -> int:
        return self._u32[offset // 4]

    def _store32(self, offset: int, value: int) -> None:
        self._u32[offset // 4] = value

    def _load64(self, offset: int) -> int:
        return self._u64[offset // 8]

    def active(self, stream: int = STREAM_REMOTE) -> bool:
        return self._load32(control_offset(stream) + CTL_ACTIVE) == 1

    def generation(self, stream: int = STREAM_REMOTE) -> int:
        return self._load32(control_offset(stream) + CTL_GENERATION)

    def latest_frame(self, stream: int = STREAM_REMOTE) -> Optional[Frame]:
        """
        Mark and return the newest ready frame, or None.

        The returned slot stays marked as reader until the next call or
        release(), so the service does not write into it while the app
        draws it. On None the mark stays on the slot held before.
        """
        ctl = control_offset(stream)
        for _ in range(READ_TRIES):
            slot = self._load32(ctl + CTL_LATEST)
            if slot >= SLOT_COUNT:
                self._store32(ctl + CTL_READER, self._held[stream])
                return None

            self._store32(ctl + CTL_READER, slot)
            hdr = slot_header_offset(stream, slot)
            seq = self._load64(hdr + SLOT_SEQ)
            if self._load32(ctl + CTL_LATEST) != slot or seq & 1:
                continue

            width = self._load32(hdr + SLOT_WIDTH)
            height = self._load32(hdr + SLOT_HEIGHT)
            stride = self._load32(hdr + SLOT_STRIDE)
            generation = self._load32(hdr + SLOT_GENERATION)
            if not (0 < width <= MAX_WIDTH and 0 < height <= MAX_HEIGHT
                    and stride >= width * 4 and stride * height <= SLOT_BYTES):
                break

            self._held[stream] = slot
            start = slot_data_offset(stream, slot)
            data = self._bytes[start:start + stride * height]
            return Frame(slot, seq, data, width, height, stride, generation)

        # No usable frame: keep the mark on the frame drawn before
        self._store32(ctl + CTL_READER, self._held[stream])
        return None

    def release(self, stream: int = STREAM_REMOTE) -> None:
        """The app does not draw any frame of this stream now."""
        self._held[stream] = NONE
        self._store32(control_offset(stream) + CTL_READER, NONE)


class VideoShm:
    """The memfd and its mapping. Keep it open while the service runs."""

    def __init__(self, fd: int, mm):
        self.fd = fd
        self.mm = mm
        self.reader = Reader(mm)

    def close_fd(self) -> None:
        """Close our fd after the child has its copy. The mapping stays."""
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def create() -> Optional[VideoShm]:
    """Make the memfd with the header. None when not on Linux."""
    if not sys.platform.startswith('linux') or not hasattr(os, 'memfd_create'):
        return None
    fd = os.memfd_create('siproxylin-video')
    try:
        os.ftruncate(fd, TOTAL_SIZE)
        mm = mmap.mmap(fd, TOTAL_SIZE, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE)
    except Exception:
        os.close(fd)
        raise
    init_header(mm)
    return VideoShm(fd, mm)
