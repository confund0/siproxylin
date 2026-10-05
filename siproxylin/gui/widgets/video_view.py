"""
Video view for call video from the call service (shared memory).

A timer polls the reader (drunk_call_hook/video_shm.py). A new frame is
wrapped in a QImage with no copy and drawn scaled, aspect ratio kept,
centered on black. Before the first frame of the current stream, or when
the stream is not active, it shows the contact name and "Connecting".
After the call ended, it shows "Call ended".

SelfView is the own camera (stream 1) in a corner over the VideoView. It can
be dragged to another corner and hidden; both choices are saved in the app
settings (database settings table).
"""

from typing import Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRect, QEvent, QPoint
from PySide6.QtGui import QImage, QPainter, QColor, QPen


POLL_MS = 30

STREAM_SELF = 1  # same as drunk_call_hook/video_shm.py

SELF_VIEW_WIDTH = 0.2  # part of the video area width
SELF_VIEW_MARGIN = 16
CORNERS = ('top_left', 'top_right', 'bottom_left', 'bottom_right')
DEFAULT_CORNER = 'bottom_right'
SETTING_CORNER = 'call_self_view_corner'
SETTING_HIDDEN = 'call_self_view_hidden'


class VideoView(QWidget):
    """Shows one video stream of the shared memory."""

    def __init__(self, reader, contact_name: str, stream: int = 0, parent=None):
        super().__init__(parent)
        self._reader = reader
        self._contact_name = contact_name
        self._stream = stream
        self._ended = False

        # The QImage points into the shared memory: keep the frame (and its
        # memoryview) as long as the image is used
        self._frame = None
        self._image: Optional[QImage] = None

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)

        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setMinimumSize(160, 120)

    def start(self):
        """Start polling for frames."""
        if self._ended:
            return
        if not self._timer.isActive():
            self._timer.start()
        self._poll()

    def stop(self):
        """Stop polling and free the slot this view draws."""
        self._timer.stop()
        self._drop_frame()
        self._reader.release(self._stream)
        self.update()

    def show_call_ended(self):
        """Stop the video and show "Call ended" instead of "Connecting"."""
        self._ended = True
        self.stop()

    def has_frame(self) -> bool:
        return self._image is not None

    def _drop_frame(self):
        self._image = None
        self._frame = None

    def _poll(self):
        reader = self._reader
        if not reader.active(self._stream):
            if self._image is not None:
                self._drop_frame()
                reader.release(self._stream)
                self.update()
            return

        generation = reader.generation(self._stream)
        if self._frame is not None and self._frame.generation != generation:
            # A new stream started: the old frame is not valid any more
            self._drop_frame()
            self.update()

        frame = reader.latest_frame(self._stream)
        if frame is None or frame.generation != generation:
            return
        if self._frame is not None and (frame.slot, frame.seq) == (self._frame.slot, self._frame.seq):
            return

        self._frame = frame
        self._image = QImage(frame.data, frame.width, frame.height, frame.stride,
                             QImage.Format_RGBX8888)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))

        if self._image is not None:
            img_w = self._image.width()
            img_h = self._image.height()
            scale = min(self.width() / img_w, self.height() / img_h)
            w = max(1, int(img_w * scale))
            h = max(1, int(img_h * scale))
            target = QRect((self.width() - w) // 2, (self.height() - h) // 2, w, h)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.drawImage(target, self._image)
        else:
            painter.setPen(QColor(220, 220, 220))
            text = "Call ended" if self._ended else "Connecting"
            painter.drawText(self.rect(), Qt.AlignCenter,
                             f"{self._contact_name}\n{text}")
        painter.end()

    def showEvent(self, event):
        super().showEvent(event)
        self.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.stop()


class SelfView(VideoView):
    """
    Own camera in a corner of the parent VideoView. Narcissism is supported.

    Shows nothing until camera frames come. Drag it with the mouse; on release
    it snaps to the nearest corner. settings is an object with get_setting()
    and set_setting() (the app database), or None (no saved choices).
    """

    def __init__(self, reader, parent: QWidget, settings=None):
        super().__init__(reader, '', stream=STREAM_SELF, parent=parent)
        self._settings = settings
        # Transparent until the first frame: the parent placeholder stays visible
        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setMinimumSize(0, 0)
        self.setCursor(Qt.OpenHandCursor)

        corner = settings.get_setting(SETTING_CORNER, DEFAULT_CORNER) if settings else DEFAULT_CORNER
        self._corner = corner if corner in CORNERS else DEFAULT_CORNER
        hidden = settings.get_setting(SETTING_HIDDEN, 'false') if settings else 'false'
        self._user_hidden = str(hidden).lower() == 'true'

        self._drag_offset: Optional[QPoint] = None
        self._frame_size = (4, 3)  # shape before the first frame

        parent.installEventFilter(self)
        self.place()
        self.setVisible(not self._user_hidden)

    @property
    def corner(self) -> str:
        return self._corner

    def is_user_hidden(self) -> bool:
        return self._user_hidden

    def set_user_hidden(self, hidden: bool):
        """Hide or show the self-view and save the choice."""
        self._user_hidden = hidden
        if self._settings:
            self._settings.set_setting(SETTING_HIDDEN, 'true' if hidden else 'false')
        self.setVisible(not hidden)
        if not hidden:
            self.raise_()

    def corner_rect(self, corner: str) -> QRect:
        """Geometry of the self-view in the given corner of the parent."""
        parent = self.parentWidget()
        frame_w, frame_h = self._frame_size
        w = max(1, int(parent.width() * SELF_VIEW_WIDTH))
        h = max(1, int(w * frame_h / frame_w))
        x = SELF_VIEW_MARGIN if corner.endswith('left') else parent.width() - SELF_VIEW_MARGIN - w
        y = SELF_VIEW_MARGIN if corner.startswith('top') else parent.height() - SELF_VIEW_MARGIN - h
        return QRect(x, y, w, h)

    def place(self):
        """Move to the current corner (after a resize, a drag or a new frame shape)."""
        self.setGeometry(self.corner_rect(self._corner))

    def nearest_corner(self) -> str:
        parent = self.parentWidget()
        center = self.geometry().center()
        vertical = 'top' if center.y() < parent.height() / 2 else 'bottom'
        horizontal = 'left' if center.x() < parent.width() / 2 else 'right'
        return f"{vertical}_{horizontal}"

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize and self._drag_offset is None:
            self.place()
        return False

    def _poll(self):
        super()._poll()
        if self._image is not None:
            size = (self._image.width(), self._image.height())
            if size != self._frame_size:
                self._frame_size = size
                if self._drag_offset is None:
                    self.place()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or not self.has_frame():
            event.ignore()
            return
        self._drag_offset = event.position().toPoint()
        self.setCursor(Qt.ClosedHandCursor)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_offset is None:
            event.ignore()
            return
        parent = self.parentWidget()
        pos = self.mapToParent(event.position().toPoint()) - self._drag_offset
        x = min(max(0, pos.x()), max(0, parent.width() - self.width()))
        y = min(max(0, pos.y()), max(0, parent.height() - self.height()))
        self.move(x, y)
        event.accept()

    def mouseReleaseEvent(self, event):
        if self._drag_offset is None or event.button() != Qt.LeftButton:
            event.ignore()
            return
        self._drag_offset = None
        self.setCursor(Qt.OpenHandCursor)
        corner = self.nearest_corner()
        if corner != self._corner:
            self._corner = corner
            if self._settings:
                self._settings.set_setting(SETTING_CORNER, corner)
        self.place()
        event.accept()

    def paintEvent(self, event):
        if self._image is None:
            return  # no frame yet: draw nothing
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(self.rect(), self._image)
        painter.setPen(QPen(QColor(220, 220, 220), 1))
        painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
        painter.end()
