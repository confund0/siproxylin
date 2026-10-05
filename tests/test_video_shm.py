#!/usr/bin/env python3
"""
Offline test: video frames over shared memory (drunk_call_hook/video_shm.py)
and the VideoView widget.

The test writer below follows the protocol of the C++ writer
(drunk_call_service/src/video_shm.cpp).

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_video_shm.py
"""

import mmap
import os
import struct
import sys
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor

# Load the module by path: the drunk_call_hook package imports grpc,
# which the offline venv does not have
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'video_shm', Path(__file__).parent.parent / 'drunk_call_hook' / 'video_shm.py')
shm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shm)
from siproxylin.gui.widgets.video_view import VideoView

APP = QApplication.instance() or QApplication([])


class TestWriter:
    """Python copy of the C++ writer (video_shm.cpp)."""

    def __init__(self, mm):
        self.mm = mm
        self.owner = None
        self.generation = [struct.unpack_from('<I', mm, shm.control_offset(s) + shm.CTL_GENERATION)[0]
                           for s in range(shm.STREAM_COUNT)]
        self.writes = []  # slots written, in order
        # Hook to run between "seq odd" and the reader check (race tests)
        self.after_seq_odd = None

    def _ctl(self, stream, field):
        return struct.unpack_from('<I', self.mm, shm.control_offset(stream) + field)[0]

    def _set_ctl(self, stream, field, value):
        struct.pack_into('<I', self.mm, shm.control_offset(stream) + field, value)

    def _seq_off(self, stream, slot):
        return shm.slot_header_offset(stream, slot) + shm.SLOT_SEQ

    def begin_stream(self, stream=0, owner='s1'):
        self.owner = owner
        self.generation[stream] += 1
        self._set_ctl(stream, shm.CTL_LATEST, shm.NONE)
        self._set_ctl(stream, shm.CTL_GENERATION, self.generation[stream])
        self._set_ctl(stream, shm.CTL_ACTIVE, 1)

    def end_stream(self, stream=0, owner='s1'):
        if self.owner != owner:
            return
        self.owner = None
        self._set_ctl(stream, shm.CTL_ACTIVE, 0)
        self._set_ctl(stream, shm.CTL_LATEST, shm.NONE)

    def write_frame(self, width, height, color, stream=0, owner='s1'):
        if owner != self.owner:
            return False
        latest = self._ctl(stream, shm.CTL_LATEST)
        reader = self._ctl(stream, shm.CTL_READER)
        slot = 0
        while slot == latest or slot == reader:
            slot += 1
        seq = struct.unpack_from('<Q', self.mm, self._seq_off(stream, slot))[0] & ~1
        struct.pack_into('<Q', self.mm, self._seq_off(stream, slot), seq + 1)
        if self.after_seq_odd:
            self.after_seq_odd(slot)
        if self._ctl(stream, shm.CTL_READER) == slot:
            struct.pack_into('<Q', self.mm, self._seq_off(stream, slot), seq)
            taken = slot
            slot = 0
            while slot == latest or slot == taken:
                slot += 1
            seq = struct.unpack_from('<Q', self.mm, self._seq_off(stream, slot))[0] & ~1
            struct.pack_into('<Q', self.mm, self._seq_off(stream, slot), seq + 1)

        stride = width * 4
        start = shm.slot_data_offset(stream, slot)
        self.mm[start:start + stride * height] = bytes(color) * (width * height)
        hdr = shm.slot_header_offset(stream, slot)
        struct.pack_into('<IIII', self.mm, hdr + shm.SLOT_WIDTH,
                         width, height, stride, self.generation[stream])
        struct.pack_into('<Q', self.mm, hdr + shm.SLOT_PTS, 0)
        struct.pack_into('<Q', self.mm, self._seq_off(stream, slot), seq + 2)
        self._set_ctl(stream, shm.CTL_LATEST, slot)
        self.writes.append(slot)
        return True


def make_shm():
    mm = mmap.mmap(-1, shm.TOTAL_SIZE)
    shm.init_header(mm)
    return mm


RED = (255, 0, 0, 0)
GREEN = (0, 255, 0, 0)
BLUE = (0, 0, 255, 0)


class LayoutTests(unittest.TestCase):

    def test_constants(self):
        self.assertEqual(shm.SLOT_BYTES, 960 * 960 * 4)
        self.assertEqual(shm.DATA_OFFSET % 4096, 0)
        self.assertGreaterEqual(shm.DATA_OFFSET, shm.META_SIZE)
        self.assertEqual(shm.META_SIZE, 64 + 2 * 64 + 6 * 64)

    def test_header(self):
        mm = make_shm()
        self.assertEqual(struct.unpack_from('<IIIIIIQQ', mm, 0),
                         (0x46565053, 1, 2, 3, 960, 960, 960 * 960 * 4, shm.DATA_OFFSET))
        self.assertEqual(mm[0:4], b'SPVF')
        for s in range(shm.STREAM_COUNT):
            self.assertEqual(struct.unpack_from('<IIII', mm, shm.control_offset(s)),
                             (shm.NONE, shm.NONE, 0, 0))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'memfd is Linux only')
    def test_create_memfd(self):
        v = shm.create()
        self.assertIsNotNone(v)
        try:
            self.assertEqual(os.fstat(v.fd).st_size, shm.TOTAL_SIZE)
            self.assertEqual(v.mm[0:4], b'SPVF')
            self.assertFalse(v.reader.active())
        finally:
            v.close_fd()
        self.assertEqual(v.fd, -1)


class ReaderTests(unittest.TestCase):

    def setUp(self):
        self.mm = make_shm()
        self.writer = TestWriter(self.mm)
        self.reader = shm.Reader(self.mm)

    def reader_mark(self):
        return struct.unpack_from('<I', self.mm, shm.control_offset(0) + shm.CTL_READER)[0]

    def test_no_frame(self):
        self.assertIsNone(self.reader.latest_frame())
        self.assertFalse(self.reader.active())
        self.writer.begin_stream()
        self.assertTrue(self.reader.active())
        self.assertIsNone(self.reader.latest_frame())
        self.assertEqual(self.reader_mark(), shm.NONE)

    def test_frame_read(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        f = self.reader.latest_frame()
        self.assertIsNotNone(f)
        self.assertEqual((f.width, f.height, f.stride), (4, 2, 16))
        self.assertEqual(f.generation, 1)
        self.assertEqual(len(f.data), 32)
        self.assertEqual(bytes(f.data[0:4]), bytes(RED))
        self.assertEqual(f.seq % 2, 0)
        self.assertEqual(self.reader_mark(), f.slot)

    def test_size_change(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        f1 = self.reader.latest_frame()
        self.writer.write_frame(8, 6, GREEN)
        f2 = self.reader.latest_frame()
        self.assertEqual((f2.width, f2.height, f2.stride), (8, 6, 32))
        self.assertEqual(len(f2.data), 32 * 6)
        self.assertNotEqual(f1.slot, f2.slot)
        self.assertEqual(bytes(f2.data[-4:]), bytes(GREEN))

    def test_held_slot_never_written(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        held = self.reader.latest_frame()
        for i in range(20):
            self.writer.write_frame(4, 2, BLUE if i % 2 else GREEN)
            self.assertNotEqual(self.writer.writes[-1], held.slot)
        # The held frame is unchanged
        self.assertEqual(bytes(held.data), bytes(RED) * 8)
        # The writer used both other slots
        self.assertEqual(set(self.writer.writes[1:]), {0, 1, 2} - {held.slot})
        # The next read gets the newest frame
        f = self.reader.latest_frame()
        self.assertEqual(f.slot, self.writer.writes[-1])
        self.assertEqual(bytes(f.data[0:4]), bytes(BLUE))

    def test_writer_backs_off_when_reader_marks_slot(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)  # slot 0, latest = 0
        # The app marks the slot the writer chose (after seq odd)
        mark = lambda slot: struct.pack_into('<I', self.mm, shm.control_offset(0) + shm.CTL_READER, slot)
        self.writer.after_seq_odd = mark
        self.writer.write_frame(4, 2, GREEN)
        self.assertEqual(self.writer.writes[-1], 2)
        # The backed-off slot has an even seq again
        seq1 = struct.unpack_from('<Q', self.mm, shm.slot_header_offset(0, 1))[0]
        self.assertEqual(seq1 % 2, 0)

    def test_odd_seq_is_skipped(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        first = self.reader.latest_frame()
        self.writer.write_frame(4, 2, GREEN)
        latest = self.writer.writes[-1]
        off = shm.slot_header_offset(0, latest) + shm.SLOT_SEQ
        seq = struct.unpack_from('<Q', self.mm, off)[0]
        struct.pack_into('<Q', self.mm, off, seq + 1)
        self.assertIsNone(self.reader.latest_frame())
        # The mark goes back to the frame read before
        self.assertEqual(self.reader_mark(), first.slot)

    def test_bad_values_rejected(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        good = self.reader.latest_frame()
        self.writer.write_frame(4, 2, GREEN)
        slot = self.writer.writes[-1]
        hdr = shm.slot_header_offset(0, slot)
        bad = [
            (0, 2, 16),                 # width 0
            (4, 0, 16),                 # height 0
            (961, 2, 961 * 4),          # width over max
            (4, 961, 16),               # height over max
            (4, 2, 15),                 # stride under width * 4
            (960, 960, 960 * 4 + 4),    # stride * height over slot_bytes
        ]
        for width, height, stride in bad:
            with self.subTest(width=width, height=height, stride=stride):
                struct.pack_into('<III', self.mm, hdr + shm.SLOT_WIDTH, width, height, stride)
                self.assertIsNone(self.reader.latest_frame())
                self.assertEqual(self.reader_mark(), good.slot)

    def test_latest_out_of_range(self):
        self.writer.begin_stream()
        struct.pack_into('<I', self.mm, shm.control_offset(0) + shm.CTL_LATEST, 7)
        self.assertIsNone(self.reader.latest_frame())

    def test_release(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        self.reader.latest_frame()
        self.reader.release()
        self.assertEqual(self.reader_mark(), shm.NONE)

    def test_end_stream(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 2, RED)
        self.writer.end_stream(owner='other')  # not the owner: no change
        self.assertTrue(self.reader.active())
        self.writer.end_stream()
        self.assertFalse(self.reader.active())
        self.assertIsNone(self.reader.latest_frame())
        self.assertFalse(self.writer.write_frame(4, 2, RED))


class VideoViewTests(unittest.TestCase):

    def setUp(self):
        self.mm = make_shm()
        self.writer = TestWriter(self.mm)
        self.reader = shm.Reader(self.mm)
        self.view = VideoView(self.reader, 'alice@localhost')
        self.view.resize(200, 100)
        self.view.show()
        APP.processEvents()

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        APP.processEvents()

    def poll(self):
        self.view._poll()
        APP.processEvents()

    def center_color(self):
        img = self.view.grab().toImage()
        return QColor(img.pixel(img.width() // 2, img.height() // 2))

    def test_placeholder_before_stream(self):
        self.poll()
        self.assertFalse(self.view.has_frame())
        img = self.view.grab().toImage()
        self.assertEqual(QColor(img.pixel(1, 1)), QColor(0, 0, 0))

    def test_frame_shown_and_scaled(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 4, RED)
        self.poll()
        self.assertTrue(self.view.has_frame())
        self.assertEqual(self.center_color(), QColor(255, 0, 0))
        # Square frame in a 200x100 view: black bars left and right
        img = self.view.grab().toImage()
        self.assertEqual(QColor(img.pixel(5, 50)), QColor(0, 0, 0))

    def test_new_frame_replaces_old(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 4, RED)
        self.poll()
        self.writer.write_frame(4, 4, GREEN)
        self.poll()
        self.assertEqual(self.center_color(), QColor(0, 255, 0))

    def test_old_generation_frame_not_shown(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 4, RED)
        self.poll()
        self.assertTrue(self.view.has_frame())
        # New stream: the old frame goes, placeholder until a new frame
        self.writer.begin_stream()
        self.poll()
        self.assertFalse(self.view.has_frame())
        # A frame of the old generation in latest is not shown
        self.writer.generation[0] -= 1
        self.writer.write_frame(4, 4, GREEN)
        self.writer.generation[0] += 1
        self.poll()
        self.assertFalse(self.view.has_frame())
        self.writer.write_frame(4, 4, BLUE)
        self.poll()
        self.assertTrue(self.view.has_frame())
        self.assertEqual(self.center_color(), QColor(0, 0, 255))

    def test_inactive_shows_placeholder(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 4, RED)
        self.poll()
        self.writer.end_stream()
        self.poll()
        self.assertFalse(self.view.has_frame())
        self.assertEqual(self.center_color().red(), self.center_color().green())

    def test_stop_releases_mark(self):
        self.writer.begin_stream()
        self.writer.write_frame(4, 4, RED)
        self.poll()
        self.view.stop()
        mark = struct.unpack_from('<I', self.mm, shm.control_offset(0) + shm.CTL_READER)[0]
        self.assertEqual(mark, shm.NONE)
        self.assertFalse(self.view.has_frame())


if __name__ == '__main__':
    unittest.main()
