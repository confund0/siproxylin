# Plan

Open and planned items only. Done work is in the git log.

## Now (main)

- Auto-reconnect follow-ups
  - The GUI shows "disconnected" during auto-reconnect. The account menu "Disconnect" then does nothing, so the user cannot stop the reconnect. Show "connecting" and let "Disconnect" stop it.
  - While the server is down, slixmpp's own connect loop waits 5, 15, 35 ... up to 300 seconds between attempts. Reconnect can come minutes after the server is back.
  - reload_and_reconnect() while offline: the old client keeps reconnecting with the old settings.
  - Run the auto-reconnect on Windows (slixmpp 1.8.5). It was checked against the 1.8.5 source only.
  - slixmpp 1.8.5 keepalive reconnect calls connect() without an address, so the server override is lost on that path.

- Attachment download leak (option B, plan ready)
  - Downloads go through the account proxy, with no local DNS. A bad proxy setting blocks the download.
  - Automatic download only from roster contacts and from our own devices. Others: "click to download".
  - Size limit, streaming to a temporary file, background download.
  - A failed download must still store the message. Today the message is lost.
- Found during the analysis
  - libVLC makes video thumbnails from untrusted received files automatically.
  - Message edits (XEP-0308) of a file message are not applied. In MAM an edit becomes a new file.
  - aesgcm URLs with a 16-byte IV (96 hex chars) are rejected.
  - A MAM message that is one https link is stored as a file.

## Next (branch: onion-soup)

Goal: Siproxylin works with a Prosody server that is reachable only as a Tor onion service.

- Scope
  - Closed group of users. No federation.
  - Onion routing only. No Tor exit nodes.
  - Messaging first.
  - Calls stay on clearnet, but use random public TURN servers, not the TURN servers of the XMPP server.
- Server
  - Prosody on this box, listening on localhost only.
  - Tor onion service maps the client port to Prosody.
  - Internal CA for the server certificate.
- Client: connection
  - slixmpp resolves SRV, A and AAAA locally before the proxy is used. Every proxied account leaks DNS, and .onion names go to the local resolver. Fix: no local DNS when a proxy is set; give the host name to the proxy.
  - On Windows (slixmpp 1.8.5) the proxy override is never called. The XMPP connection goes out directly. Fix: own connect code for all slixmpp versions.
  - A bad proxy setting silently gives a direct connection. Fix: "Tor required" flag per account; fail if the proxy is not usable.
  - Build the python-socks Proxy object directly, not from a URL. Credentials are not escaped in the URL today.
  - Registration logs the proxy URL with the password.
  - Per-account TLS mode (direct TLS or STARTTLS), so Tor does not try both.
  - Longer connection test timeout for onion accounts.
- Client: TLS
  - Per-account CA file. It replaces the system CAs, it does not add to them. The same SSL context is used for HTTP upload and download.
  - The "ignore TLS errors" and "require strong TLS" account options do nothing. Wire them or remove them.
- Client: other traffic
  - Received attachments download automatically, without proxy and with local DNS. Any contact can learn the real IP. Fix: one proxied HTTP session per account; no automatic download from strangers; size limit.
  - HTTP upload (slixmpp XEP-0363 plugin) sends the PUT without proxy.
  - Account deletion and password change connect without proxy and ignore the server override.
  - Registration resolves SRV locally and does not check TLS. Disable it for onion accounts.
  - Links open in the system browser on clearnet. Warn or copy only for onion accounts.
- Client: fingerprint
  - Disco identity "DrunkXMPP", slixmpp caps node, XEP-0092 software version, User-Agent headers and the "siproxylin." resource prefix identify the client. Use neutral values and a random resource per session.
- Calls
  - The account proxy is passed to the call service. With Tor, media would go into Tor. Fix: separate call network setting.
  - TURN comes only from XEP-0215, and only the first server is used. An onion server returns useless TURN hosts. Fix: TURN list from settings with random choice; skip XEP-0215 for onion accounts.
  - The Jami TURN fallback does not exist. With relay-only and no TURN, the call fails.
  - "turns:" loses its TLS in the C++ call service.
  - Public TURN servers without credentials are rare. We need a curated list with credentials.
- Security
  - The local gRPC port of the call service has no authentication.

## Release tooling

- Next Linux release: check if CI builds the C++ call service or restores an old binary from cache. Suspected cause: restore-keys matches any old cache entry, and proto/ is not in the key. Not proven. Check the build log and the call service binary in the AppImage.
- Windows release waits until the Linux release with the reconnect fix is confirmed in daily use.
- generate-changelog.sh includes deps-v* tags. CHANGELOG.md already has a wrong deps-v0.0.27 section.
- The pre-push hook rejects deps-v* tag pushes.
- The changelog is made before the bump commit, so the bump commit and fixes before the tag are missing from the released changelog.
- docs/CONTRIBUTING.md and docs/BUILD.md describe an old release process.
- siproxylin.appdata.xml has placeholder URLs and no real releases.
- dist/ is not in .gitignore.
- .package-builder.sh calls log_error before it is defined, and prints "vv0.0.29".

## Calls on GStreamer 1.26 (Debian 13)

- Answerer with video: test a video call where video is on m-line 0 (Conversations).
- Outgoing call logs "Creating offer..." twice. Check if the offer is created twice.
- Move the base to Debian 13 (trixie): daily chroot and AppImage build. Debian 12 is dropped. The AppImage must bundle the patched dtls plugin.
- CI (release.yml) runs `make release`, which now needs deb-src lines, dpkg-dev, meson, ninja-build and libssl-dev in the build container. The call service binary cache key does not cover the patch and the plugin.
- build-appimage.sh must copy bin/gst-plugins/libgstdtls.so over the bundled system libgstdtls.so, on every build, and fail if the file lacks the ECDSA marker. appimage.yml sets GST_PLUGIN_SYSTEM_PATH to the AppDir plugins only.
- docs/BUILD.md Alpine chroot section still says Debian 12.
- The destructor does not free offer_video_codec_caps_ and negotiated_video_pad_.
- The call service crashed once at EndSession. Nothing restarts it. Its stderr file is emptied on every start, so the crash trace is lost.
- A failed outgoing call leaves "Another call is already in progress".
- Log line "Offerer audio pipeline already created with payload=97" is wrong; the caps use 111.

## Known issues

- Proxy is not applied on all call sockets (C++ / GStreamer call service).
- Unread counters sometimes appear after app restart.
- Members-only MUC membership depends on server auto-approve.

## Docs

- README still says "Go service" in places. The call service is C++ now.
- The NATIVE-CALL-WINDOW-PLAN files (v1, v2, v3) describe finished or failed work. Remove them or move the lessons to docs/VIDEO.md.
