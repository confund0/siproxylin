"""
Dialog windows for chat view and calls.
"""

from .incoming_call_dialog import IncomingCallDialog
from .outgoing_call_dialog import OutgoingCallDialog
from .invite_contact_dialog import InviteContactDialog
from .select_muc_dialog import SelectMucDialog
from .image_viewer_dialog import ImageViewerDialog
from .video_viewer_dialog import VideoViewerDialog

__all__ = ['IncomingCallDialog', 'OutgoingCallDialog', 'InviteContactDialog', 'SelectMucDialog', 'ImageViewerDialog', 'VideoViewerDialog']
