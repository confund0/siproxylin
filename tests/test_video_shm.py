#!/usr/bin/env python3
"""
Offline test: video frames over shared memory (drunk_call_hook/video_shm.py),
the VideoView and SelfView widgets and the control bar of the call window.

The test writer below follows the protocol of the C++ writer
(drunk_call_service/src/video_shm.cpp).

Run with: QT_QPA_PLATFORM=offscreen <venv>/bin/python -m unittest tests/test_video_shm.py
"""

import mmap
import os
import re
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'  # no display in tests

sys.path.insert(0, str(Path(__file__).parent.parent))

from PySide6.QtWidgets import QApplication, QPushButton, QToolTip
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent

# Load the module by path: the drunk_call_hook package imports grpc,
# which the offline venv does not have
import importlib.util
_spec = importlib.util.spec_from_file_location(
    'video_shm', Path(__file__).parent.parent / 'drunk_call_hook' / 'video_shm.py')
shm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shm)
from siproxylin.gui.widgets.video_view import VideoView, SelfView
from siproxylin.gui import call_window

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


class SelfStreamReaderTests(unittest.TestCase):
    """Stream 1 (self-view) uses its own control block and slots."""

    def setUp(self):
        self.mm = make_shm()
        self.writer = TestWriter(self.mm)
        self.reader = shm.Reader(self.mm)

    def test_self_stream_read(self):
        self.assertEqual(shm.STREAM_SELF, 1)
        self.writer.begin_stream(stream=shm.STREAM_SELF)
        self.assertTrue(self.reader.active(shm.STREAM_SELF))
        self.assertFalse(self.reader.active(shm.STREAM_REMOTE))
        self.writer.write_frame(8, 6, GREEN, stream=shm.STREAM_SELF)
        self.assertIsNone(self.reader.latest_frame(shm.STREAM_REMOTE))
        f = self.reader.latest_frame(shm.STREAM_SELF)
        self.assertIsNotNone(f)
        self.assertEqual((f.width, f.height, f.generation), (8, 6, 1))
        self.assertEqual(bytes(f.data[0:4]), bytes(GREEN))
        # The mark is in the stream 1 control block only
        mark0 = struct.unpack_from('<I', self.mm, shm.control_offset(0) + shm.CTL_READER)[0]
        mark1 = struct.unpack_from('<I', self.mm, shm.control_offset(1) + shm.CTL_READER)[0]
        self.assertEqual(mark0, shm.NONE)
        self.assertEqual(mark1, f.slot)

    def test_both_streams(self):
        self.writer.begin_stream(stream=shm.STREAM_REMOTE)
        self.writer.begin_stream(stream=shm.STREAM_SELF)
        self.writer.write_frame(4, 2, RED, stream=shm.STREAM_REMOTE)
        self.writer.write_frame(4, 2, BLUE, stream=shm.STREAM_SELF)
        remote = self.reader.latest_frame(shm.STREAM_REMOTE)
        own = self.reader.latest_frame(shm.STREAM_SELF)
        self.assertEqual(bytes(remote.data[0:4]), bytes(RED))
        self.assertEqual(bytes(own.data[0:4]), bytes(BLUE))
        self.reader.release(shm.STREAM_SELF)
        mark0 = struct.unpack_from('<I', self.mm, shm.control_offset(0) + shm.CTL_READER)[0]
        self.assertEqual(mark0, remote.slot)


class FakeSettings:
    """Stands in for the app database (get_setting / set_setting)."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_setting(self, key, default=None):
        return self.values.get(key, default)

    def set_setting(self, key, value):
        self.values[key] = str(value)


def send_mouse(widget, kind, pos):
    """Send a left button mouse event at pos (widget coordinates)."""
    local = QPointF(pos)
    glob = QPointF(widget.mapToGlobal(pos))
    buttons = Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton
    event = QMouseEvent(kind, local, glob, Qt.LeftButton, buttons, Qt.NoModifier)
    QApplication.sendEvent(widget, event)


class SelfViewTests(unittest.TestCase):

    def setUp(self):
        self.mm = make_shm()
        self.writer = TestWriter(self.mm)
        self.reader = shm.Reader(self.mm)
        self.settings = FakeSettings()
        self.view = VideoView(self.reader, 'alice@localhost')
        self.view.resize(500, 400)
        self.self_view = None

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        APP.processEvents()

    def make_self_view(self):
        self.self_view = SelfView(self.reader, self.view, settings=self.settings)
        self.view.show()
        APP.processEvents()
        return self.self_view

    def camera_frame(self, width=320, height=240, color=GREEN):
        self.writer.begin_stream(stream=shm.STREAM_SELF)
        self.writer.write_frame(width, height, color, stream=shm.STREAM_SELF)
        self.self_view._poll()
        APP.processEvents()

    def test_default_corner_bottom_right(self):
        sv = self.make_self_view()
        self.assertEqual(sv.corner, 'bottom_right')
        # 20% of 500 = 100 wide, 4:3 = 75 high, 16 px margin
        self.assertEqual(sv.geometry().getRect(), (500 - 16 - 100, 400 - 16 - 75, 100, 75))

    def test_position_per_corner(self):
        sv = self.make_self_view()
        expected = {
            'top_left': (16, 16),
            'top_right': (500 - 16 - 100, 16),
            'bottom_left': (16, 400 - 16 - 75),
            'bottom_right': (500 - 16 - 100, 400 - 16 - 75),
        }
        for corner, (x, y) in expected.items():
            with self.subTest(corner=corner):
                r = sv.corner_rect(corner)
                self.assertEqual((r.x(), r.y(), r.width(), r.height()), (x, y, 100, 75))

    def test_saved_corner_used(self):
        self.settings.values['call_self_view_corner'] = 'top_left'
        sv = self.make_self_view()
        self.assertEqual(sv.geometry().topLeft(), QPoint(16, 16))

    def test_bad_saved_corner_gives_default(self):
        self.settings.values['call_self_view_corner'] = 'middle'
        sv = self.make_self_view()
        self.assertEqual(sv.corner, 'bottom_right')

    def test_follows_parent_resize_and_frame_shape(self):
        sv = self.make_self_view()
        self.view.resize(1000, 600)
        APP.processEvents()
        self.assertEqual(sv.geometry().getRect(), (1000 - 16 - 200, 600 - 16 - 150, 200, 150))
        # Portrait camera frame: same width, taller
        self.camera_frame(240, 320)
        self.assertEqual(sv.geometry().getRect(), (1000 - 16 - 200, 600 - 16 - 266, 200, 266))

    def test_transparent_before_frame_then_drawn(self):
        sv = self.make_self_view()
        self.view._poll()
        self.assertFalse(sv.has_frame())
        img = self.view.grab().toImage()
        c = sv.geometry().center()
        self.assertEqual(QColor(img.pixel(c.x(), c.y())), QColor(0, 0, 0))
        self.camera_frame(color=BLUE)
        self.assertTrue(sv.has_frame())
        img = self.view.grab().toImage()
        self.assertEqual(QColor(img.pixel(c.x(), c.y())), QColor(0, 0, 255))

    def test_drag_snaps_to_nearest_corner(self):
        sv = self.make_self_view()
        self.camera_frame()
        # Grab in the middle, drop near the top left area (not exactly in the corner)
        send_mouse(sv, QEvent.MouseButtonPress, QPoint(50, 37))
        send_mouse(sv, QEvent.MouseMove, sv.mapFromParent(QPoint(150, 120)))
        # While dragging the widget follows the mouse
        self.assertEqual(sv.geometry().topLeft(), QPoint(150 - 50, 120 - 37))
        send_mouse(sv, QEvent.MouseButtonRelease, sv.mapFromParent(QPoint(150, 120)))
        self.assertEqual(sv.corner, 'top_left')
        self.assertEqual(sv.geometry().topLeft(), QPoint(16, 16))
        self.assertEqual(self.settings.values['call_self_view_corner'], 'top_left')

        # Drag to the right half, lower half: bottom right
        send_mouse(sv, QEvent.MouseButtonPress, QPoint(10, 10))
        send_mouse(sv, QEvent.MouseMove, sv.mapFromParent(QPoint(300, 260)))
        send_mouse(sv, QEvent.MouseButtonRelease, sv.mapFromParent(QPoint(300, 260)))
        self.assertEqual(sv.corner, 'bottom_right')
        self.assertEqual(sv.geometry().topLeft(), QPoint(500 - 16 - 100, 400 - 16 - 75))
        self.assertEqual(self.settings.values['call_self_view_corner'], 'bottom_right')

        # Top right
        send_mouse(sv, QEvent.MouseButtonPress, QPoint(10, 10))
        send_mouse(sv, QEvent.MouseMove, sv.mapFromParent(QPoint(400, 20)))
        send_mouse(sv, QEvent.MouseButtonRelease, sv.mapFromParent(QPoint(400, 20)))
        self.assertEqual(sv.corner, 'top_right')

    def test_drag_kept_in_video_area(self):
        sv = self.make_self_view()
        self.camera_frame()
        send_mouse(sv, QEvent.MouseButtonPress, QPoint(10, 10))
        send_mouse(sv, QEvent.MouseMove, sv.mapFromParent(QPoint(-300, -300)))
        self.assertEqual(sv.geometry().topLeft(), QPoint(0, 0))
        send_mouse(sv, QEvent.MouseButtonRelease, sv.mapFromParent(QPoint(-300, -300)))
        self.assertEqual(sv.corner, 'top_left')

    def test_no_drag_before_frame(self):
        sv = self.make_self_view()
        before = sv.geometry()
        send_mouse(sv, QEvent.MouseButtonPress, QPoint(10, 10))
        send_mouse(sv, QEvent.MouseMove, sv.mapFromParent(QPoint(30, 30)))
        send_mouse(sv, QEvent.MouseButtonRelease, sv.mapFromParent(QPoint(30, 30)))
        self.assertEqual(sv.geometry(), before)
        self.assertNotIn('call_self_view_corner', self.settings.values)

    def test_hidden_saved_and_restored(self):
        sv = self.make_self_view()
        self.assertTrue(sv.isVisible())
        sv.set_user_hidden(True)
        self.assertFalse(sv.isVisible())
        self.assertEqual(self.settings.values['call_self_view_hidden'], 'true')

        # Next call: a new self-view starts hidden
        sv2 = SelfView(self.reader, self.view, settings=self.settings)
        APP.processEvents()
        self.assertTrue(sv2.is_user_hidden())
        self.assertFalse(sv2.isVisible())
        sv2.set_user_hidden(False)
        self.assertTrue(sv2.isVisible())
        self.assertEqual(self.settings.values['call_self_view_hidden'], 'false')

    def test_hide_releases_mark(self):
        sv = self.make_self_view()
        self.camera_frame()
        sv.set_user_hidden(True)
        mark = struct.unpack_from('<I', self.mm, shm.control_offset(1) + shm.CTL_READER)[0]
        self.assertEqual(mark, shm.NONE)

    def test_remote_view_not_changed_by_self_stream(self):
        self.make_self_view()
        self.camera_frame()
        self.view._poll()
        self.assertFalse(self.view.has_frame())


class CallWindowSelfViewTests(unittest.TestCase):

    def make_window(self, settings, media=('audio', 'video'), reader=True):
        mm = make_shm()
        with patch.object(call_window, 'get_db', return_value=settings):
            w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', list(media), 'outgoing',
                                       video_reader=shm.Reader(mm) if reader else None)
        w._mm = mm  # keep the memory alive
        return w

    def close(self, w):
        w.close()
        w.deleteLater()
        APP.processEvents()

    def test_button_toggles_and_saves(self):
        settings = FakeSettings()
        w = self.make_window(settings)
        try:
            w.show()
            APP.processEvents()
            self.assertTrue(w.self_view_button.isChecked())
            self.assertEqual(w.self_view_button.toolTip(), 'Hide self-view')
            self.assertTrue(w.self_view.isVisible())
            w.self_view_button.click()
            self.assertFalse(w.self_view.isVisible())
            self.assertEqual(w.self_view_button.toolTip(), 'Show self-view')
            self.assertEqual(settings.values['call_self_view_hidden'], 'true')
        finally:
            self.close(w)

        # Next call window starts with the self-view hidden
        w = self.make_window(settings)
        try:
            w.show()
            APP.processEvents()
            self.assertFalse(w.self_view_button.isChecked())
            self.assertEqual(w.self_view_button.toolTip(), 'Show self-view')
            self.assertFalse(w.self_view.isVisible())
        finally:
            self.close(w)

    def test_audio_call_has_no_self_view(self):
        w = self.make_window(FakeSettings(), media=('audio',))
        try:
            self.assertIsNone(w.self_view)
            self.assertFalse(hasattr(w, 'self_view_button'))
        finally:
            self.close(w)

    def test_no_reader_has_no_self_view(self):
        w = self.make_window(FakeSettings(), reader=False)
        try:
            self.assertIsNone(w.self_view)
            self.assertFalse(hasattr(w, 'self_view_button'))
        finally:
            self.close(w)


class CallWindowControlBarTests(unittest.TestCase):

    def make_window(self, reader=True):
        mm = make_shm()
        with patch.object(call_window, 'get_db', return_value=FakeSettings()):
            w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', ['audio', 'video'],
                                       'outgoing', video_reader=shm.Reader(mm) if reader else None)
        w._mm = mm  # keep the memory alive
        return w

    def close(self, w):
        w.close()
        w.deleteLater()
        APP.processEvents()

    def center_x(self, w, button):
        bar = w.slim_container
        return button.mapTo(bar, button.rect().center()).x(), bar.width() / 2

    def test_hangup_centered_mute_left_camera_right(self):
        w = self.make_window()
        try:
            w.show()
            for width in (800, 600):
                w.resize(width, 600)
                APP.processEvents()
                hangup_x, bar_center = self.center_x(w, w.hangup_button)
                self.assertLessEqual(abs(hangup_x - bar_center), 1, width)
                mute_x, _ = self.center_x(w, w.mute_button)
                camera_x, _ = self.center_x(w, w.camera_button)
                self.assertLess(mute_x, hangup_x)
                self.assertGreater(camera_x, hangup_x)
                self.assertGreater(w.hangup_button.width(), w.mute_button.width())
        finally:
            self.close(w)

    def test_hangup_centered_with_long_status(self):
        # reader=False: slim mode (video call without the video view)
        for reader in (True, False):
            w = self.make_window(reader=reader)
            try:
                w.show()
                for text in ("Status: Call Ended (connectivity-error)", "Connecting..."):
                    w.status_label.setText(text)
                    for width in (600, 400):
                        w.resize(width, w.height())
                        APP.processEvents()
                        hangup_x, bar_center = self.center_x(w, w.hangup_button)
                        self.assertLessEqual(abs(hangup_x - bar_center), 1,
                                             (reader, text, width))
            finally:
                self.close(w)

    def bar_buttons(self, w):
        return [w.mute_button, w.camera_button, w.self_view_button, w.expand_button]

    def test_bar_buttons_round_same_size_hangup_largest(self):
        w = self.make_window()
        try:
            for button in self.bar_buttons(w):
                self.assertEqual(button.size(), w.mute_button.size())
                self.assertIn(f'border-radius: {button.width() // 2}px', button.styleSheet())
                self.assertIn('palette(highlight)', button.styleSheet())
            self.assertGreater(w.hangup_button.width(), w.mute_button.width())
            self.assertIn(f'border-radius: {w.hangup_button.width() // 2}px',
                          w.hangup_button.styleSheet())
            self.assertIn('#d32f2f', w.hangup_button.styleSheet())
        finally:
            self.close(w)

    def test_bar_button_colors_follow_theme(self):
        # Buttons darker than the bar, brighter on hover, in a dark and a light theme
        try:
            for bar_color in ('#2b2b2b', '#d0d0d0'):
                APP.setStyleSheet(f"QWidget {{ background-color: {bar_color}; }}")
                w = self.make_window()
                try:
                    bar = QColor(bar_color).value()
                    for button in self.bar_buttons(w) + [w.hangup_button]:
                        style = button.styleSheet()
                        normal = re.search(r'QPushButton \{\s*background-color: (#\w+)', style)
                        hover = re.search(r':hover \{\s*background-color: (#\w+)', style)
                        self.assertLess(QColor(normal.group(1)).value(), bar, bar_color)
                        self.assertGreater(QColor(hover.group(1)).value(), bar, bar_color)
                finally:
                    self.close(w)
        finally:
            APP.setStyleSheet('')

    def test_status_font_same_before_and_after_state(self):
        w = self.make_window()
        try:
            w.show()
            APP.processEvents()
            size = w.status_label.font().pointSizeF()
            for state in ('connecting', 'connected'):
                w.on_call_state_changed(state)
                APP.processEvents()
                self.assertEqual(w.status_label.font().pointSizeF(), size, state)
        finally:
            self.close(w)

    def test_tooltip_in_window_font_size(self):
        # Theme like: small fixed tooltip size, larger widget font
        APP.setStyleSheet("QWidget { font-size: 15pt; } QToolTip { font-size: 7pt; }")
        w = self.make_window()
        try:
            w.show()
            APP.processEvents()
            QToolTip.showText(QPoint(10, 10), w.mute_button.toolTip(), w.mute_button)
            APP.processEvents()
            tips = [t for t in APP.topLevelWidgets()
                    if t.metaObject().className() == 'QTipLabel' and t.isVisible()]
            self.assertTrue(tips)
            self.assertEqual(tips[0].font().pointSizeF(), 15)
        finally:
            QToolTip.hideText()
            self.close(w)
            APP.setStyleSheet('')

    def test_camera_disabled(self):
        w = self.make_window()
        try:
            self.assertFalse(w.camera_button.isEnabled())
            self.assertEqual(w.camera_button.toolTip(), 'Camera on/off (not available yet)')
        finally:
            self.close(w)

    def test_buttons_wired(self):
        w = self.make_window()
        try:
            w.show()
            APP.processEvents()
            hangups = []
            w.hangup_requested.connect(lambda: hangups.append(True))
            w.hangup_button.click()
            self.assertEqual(hangups, [True])
            w.mute_button.click()
            self.assertTrue(w.mute_button.isChecked())
            self.assertTrue(w.mute_button_full.isChecked())
            w.mute_button.click()
            self.assertFalse(w.mute_button.isChecked())
            self.assertFalse(w.mute_button_full.isChecked())
        finally:
            self.close(w)



class CallWindowAudioTests(unittest.TestCase):

    def close(self, w):
        w.close()
        w.deleteLater()
        APP.processEvents()

    def button_texts(self, w):
        return [b.text() for b in w.findChildren(QPushButton)]

    def test_audio_window_has_no_slim_mode_button(self):
        w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', ['audio'], 'outgoing')
        try:
            self.assertNotIn("↓ Slim Mode", self.button_texts(w))
            self.assertTrue(w.full_container.isVisibleTo(w))
            self.assertFalse(w.slim_container.isVisibleTo(w))
        finally:
            self.close(w)

    def test_slim_video_call_keeps_slim_mode_button(self):
        w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', ['audio', 'video'],
                                   'outgoing')
        try:
            self.assertIn("↓ Slim Mode", self.button_texts(w))
        finally:
            self.close(w)

    def test_audio_mute_button_text(self):
        w = call_window.CallWindow(None, 1, 'sid1', 'bob@localhost', ['audio'], 'outgoing')
        try:
            w.show()
            APP.processEvents()
            self.assertEqual(w.mute_button_full.text(), "🎤 Mute")
            w.mute_button_full.click()
            self.assertTrue(w.mute_button_full.isChecked())
            self.assertTrue(w.mute_button.isChecked())
            self.assertEqual(w.mute_button_full.text(), "🎤 Unmute")
            w.mute_button_full.click()
            self.assertEqual(w.mute_button_full.text(), "🎤 Mute")
        finally:
            self.close(w)


if __name__ == '__main__':
    unittest.main()
