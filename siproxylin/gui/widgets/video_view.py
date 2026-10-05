"""
Video view for call video from the call service (shared memory).

A timer polls the reader (drunk_call_hook/video_shm.py). A new frame is
wrapped in a QImage with no copy and drawn scaled, aspect ratio kept,
centered on black. Before the first frame of the current stream, or when
the stream is not active, it shows the contact name and "Connecting".
After the call ended, it shows "Call ended".
"""

from typing import Optional

from PySide6.QtWidgets import QWidget
from PySide6.QtCore import Qt, QTimer, QRect
from PySide6.QtGui import QImage, QPainter, QColor


POLL_MS = 30


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
