"""
Send XEP-0363 HTTP uploads through the account proxy.

BUG DESCRIPTION:
slixmpp's XEP-0363 upload_file() opens a bare aiohttp ClientSession for the PUT.
This session does not use the account proxy and resolves the host with local DNS.

IMPACT:
- The upload server learns the real IP address of the user
- Local DNS leaks the upload host
- Also for OMEMO files: XEP-0454 upload_file() calls XEP-0363 upload_file()

FIX:
Replace the module-level name `ClientSession` in slixmpp.plugins.xep_0363.http_upload
with upload_client_session(). It reads the proxy fields of the current account
from the ContextVar upload_proxy_fields and returns a session from
http_download.make_session() (aiohttp_socks ProxyConnector with remote DNS).

The caller sets the ContextVar with use_upload_proxy() around the upload.
- ContextVar not set -> UploadProxyError, no connection
- {'proxy_type': None} -> plain session (account has no proxy)
- Bad proxy fields -> DownloadError('proxy error'), never a direct connection
"""

import contextlib
import contextvars
import logging

log = logging.getLogger(__name__)

# Proxy fields for http_download.make_session() of the account that uploads now
upload_proxy_fields = contextvars.ContextVar('upload_proxy_fields')


class UploadProxyError(RuntimeError):
    """Upload refused: we do not know which proxy to use."""


@contextlib.contextmanager
def use_upload_proxy(fields: dict):
    """Set the upload proxy fields for the code inside the with block."""
    token = upload_proxy_fields.set(dict(fields))
    try:
        yield
    finally:
        upload_proxy_fields.reset(token)


def upload_client_session(*args, headers=None, **kwargs):
    """
    Drop-in for aiohttp.ClientSession in the XEP-0363 upload code.

    Only headers are used from the slixmpp arguments. Other arguments are
    ignored, so slixmpp cannot set a connector or a proxy here.
    No total timeout: big uploads over Tor are slow. Connect and read limits stay.
    """
    from drunk_xmpp.http_download import make_session

    try:
        fields = upload_proxy_fields.get()
    except LookupError:
        raise UploadProxyError('upload refused: proxy settings of the account are not known')

    if args or kwargs:
        log.debug(f"Ignored ClientSession arguments from slixmpp: {len(args)} args, {sorted(kwargs)}")

    return make_session(**fields, headers=headers, timeout_total=None)


def apply_patch():
    """Replace ClientSession in slixmpp's XEP-0363 upload module."""
    try:
        from slixmpp.plugins.xep_0363 import http_upload
    except ImportError:
        log.warning("Could not import slixmpp XEP-0363 plugin, skipping upload proxy patch")
        return

    if not hasattr(http_upload, 'ClientSession'):
        log.warning("slixmpp XEP-0363 has no ClientSession, upload proxy patch not applied")
        return

    if http_upload.ClientSession is upload_client_session:
        return

    http_upload.ClientSession = upload_client_session
    log.debug("XEP-0363 upload proxy patch applied")
