"""
Runtime patches for slixmpp bugs.

These patches fix upstream bugs in slixmpp until they're fixed in the library itself.
Each patch is documented with the issue it fixes and can be submitted upstream.
"""

from .xep_0199_keepalive_fix import apply_patch as apply_xep0199_patch
from .xep_0077_deletion_stream_error import cancel_registration_with_stream_handling
from .xep_0280_carbon_reactions import apply_patch as apply_xep0280_reactions_patch
from .xep_0353_finish_message import apply_patch as apply_xep0353_finish_patch
from .xep_0045_membership import apply_patch as apply_xep0045_membership_patch
from .cert_stdin_prevention import apply_patch as apply_cert_stdin_prevention_patch
from .xep_0363_upload_proxy import apply_patch as apply_xep0363_upload_proxy_patch

__all__ = [
    'apply_xep0199_patch',
    'cancel_registration_with_stream_handling',
    'apply_xep0280_reactions_patch',
    'apply_xep0353_finish_patch',
    'apply_xep0045_membership_patch',
    'apply_cert_stdin_prevention_patch',
    'apply_xep0363_upload_proxy_patch'
]

def apply_all_patches():
    """Apply all slixmpp patches. Call this before using slixmpp."""
    apply_xep0199_patch()
    apply_xep0280_reactions_patch()
    apply_xep0353_finish_patch()
    apply_xep0045_membership_patch()
    apply_cert_stdin_prevention_patch()
    apply_xep0363_upload_proxy_patch()
