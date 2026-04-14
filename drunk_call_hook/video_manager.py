"""
Video Stream Manager

Manages UDP port allocation and SDP generation for RTP video streaming
from C++ service to VLC via GStreamer.
"""

import socket
import logging
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class VideoStreamManager:
    """Manages RTP video stream port allocation and SDP file generation."""

    # Use standard loopback IP (127.0.0.1) for maximum compatibility
    VIDEO_IP = "127.0.0.1"

    def __init__(self):
        self.video_port: Optional[int] = None
        self.sdp_file_path: Optional[str] = None
        self.session_id: Optional[str] = None

    def allocate_video_port(self) -> int:
        """
        Allocate a UDP port for RTP video streaming.

        CRITICAL: Socket is closed immediately after port allocation to allow
        VLC to bind and receive UDP packets. Port conflicts occurred when
        Python kept the socket open.

        Returns:
            int: Allocated UDP port number

        Raises:
            OSError: If port allocation fails
        """
        try:
            # Create UDP socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Bind to ephemeral port (OS assigns)
            sock.bind((self.VIDEO_IP, 0))

            # Get assigned port
            self.video_port = sock.getsockname()[1]

            # CRITICAL FIX: Close socket immediately so VLC can bind to receive!
            # GStreamer's udpsink only sends (doesn't bind receive port)
            # VLC must bind to this port to receive RTP packets
            sock.close()

            logger.info(f"[VideoStreamManager] Allocated video port: {self.VIDEO_IP}:{self.video_port}")
            return self.video_port

        except OSError as e:
            logger.error(f"[VideoStreamManager] Failed to allocate video port: {e}")
            raise

    def generate_sdp(self, codec: str, port: int, payload_type: int = 96) -> str:
        """
        Generate SDP (Session Description Protocol) file content for VLC.

        VLC requires SDP to understand RTP streams (codec, payload type, clock rate).

        Args:
            codec: Video codec ("VP8", "VP9", or "H264")
            port: UDP port where RTP stream will be sent
            payload_type: RTP payload type (default 96)

        Returns:
            str: SDP file content
        """
        # Video always uses 90000 Hz clock rate (RFC standard)
        clock_rate = 90000

        # Base SDP structure
        sdp = f"""v=0
o=- 0 0 IN IP4 {self.VIDEO_IP}
s=Siproxylin Video Call Stream
c=IN IP4 {self.VIDEO_IP}
t=0 0
m=video {port} RTP/AVP {payload_type}
a=rtpmap:{payload_type} {codec}/{clock_rate}
"""

        # Add codec-specific format parameters
        if codec == "H264":
            # H.264 requires packetization mode
            sdp += f"a=fmtp:{payload_type} packetization-mode=1\n"

        return sdp

    def write_sdp_file(self, session_id: str, codec: str) -> str:
        """
        Write SDP file to temporary directory for VLC playback.

        Args:
            session_id: Jingle session ID (for unique filename)
            codec: Video codec ("VP8", "VP9", or "H264")

        Returns:
            str: Absolute path to SDP file

        Raises:
            ValueError: If video port not allocated yet
        """
        if not self.video_port:
            raise ValueError("Video port must be allocated before generating SDP")

        self.session_id = session_id

        # Generate SDP content
        sdp_content = self.generate_sdp(codec, self.video_port, payload_type=96)

        # Create temp directory for SDP files
        sdp_dir = Path(tempfile.gettempdir()) / "siproxylin_video"
        sdp_dir.mkdir(exist_ok=True)

        # Write SDP file
        sdp_path = sdp_dir / f"video_{session_id}.sdp"
        sdp_path.write_text(sdp_content)

        self.sdp_file_path = str(sdp_path)
        logger.info(f"[VideoStreamManager] Generated SDP file: {self.sdp_file_path}")
        logger.debug(f"[VideoStreamManager] SDP content:\n{sdp_content}")

        return self.sdp_file_path

    def get_sdp_path(self) -> Optional[str]:
        """
        Get path to SDP file for VLC playback.

        Returns:
            str: Absolute path to SDP file, or None if not generated yet
        """
        return self.sdp_file_path

    def cleanup(self):
        """Cleanup SDP file and reset state."""
        if self.sdp_file_path:
            try:
                sdp_path = Path(self.sdp_file_path)
                if sdp_path.exists():
                    sdp_path.unlink()
                    logger.info(f"[VideoStreamManager] Deleted SDP file: {self.sdp_file_path}")
            except Exception as e:
                logger.warning(f"[VideoStreamManager] Error deleting SDP file: {e}")
            finally:
                self.sdp_file_path = None
                self.video_port = None
                self.session_id = None

    def __del__(self):
        """Cleanup on destruction."""
        self.cleanup()
