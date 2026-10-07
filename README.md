# Siproxylin

**A privacy-focused XMPP desktop client with per-account proxies and enforced call relaying.**

---

## News

**2026-10-07 v0.0.33 "Dublin deluge" released: call video in the app window**

Call video now shows in the Siproxylin call window on Linux and Windows, with your own camera in a corner and a control bar for mute, hang up, self-view and technical details. The Windows installer is back.

**On Windows:** turn on "Let desktop apps access your microphone" and the same switch for the camera in the Windows privacy settings. Windows does not ask; without it the call window shows "Microphone error" or "Camera error".

**2026-10-03 v0.0.32 "Morning beer" released: live bookmark sync**

Group chat bookmarks now sync live with your other devices. A room you add, remove or change on your phone is joined or left here at once, without a restart.

**Updating from v0.0.31:** a group chat that is not in your server bookmarks is no longer joined at login. To join it at every login, and to see it on your other devices, turn on Auto-join (right-click the room, or open the room details).

**2026-09-29 v0.0.30 "Drunk dial" released: outgoing calls fixed, files through the proxy**

Outgoing calls to current Conversations work again. GStreamer before 1.28 used an RSA certificate for DTLS. Current Conversations offers only ECDSA suites. The AppImage now includes a patched GStreamer dtls plugin with an ECDSA certificate.

Received files now download through the account proxy. The proxy resolves the host name, so there is no direct connection. Files download automatically only from roster contacts with a subscription and from your own devices, up to 25 MB. Group chats and other senders show "Click to download", up to 256 MB. Only https links are fetched. Sent files (plain and OMEMO) now also go through the account proxy.

The app now reconnects automatically after a network loss. The wait between tries is at most 30 s.

File messages from the group chat archive (MAM) now show as files.

**2026-04-14 v0.0.29 released, supporting video calls** 

It's still very first release, the video window looks super primitive, for now using default GStreamer window which seems too complicated to decorate for Wayland, _but it does work_. Windows package has been removed as users reported issues that will require some fixes. Windows will come back in a later version.

**2026-03-29 first successful tests of video calls** 

Confirmed video calls working with Conversations and Dino. Feature still in the branch and tested only on Linux. Next steps - testing/fixing on Windows.

**2026-03-26 first version, containing an installer for Windows, has been released (v0.0.28)**

It took an eternity to cherry-pick GStreamer and VCPKG dll files one by one to make the package as small as possible. Installer contains bundled libraries including base Python. During installation Python pulls a lot of dependencies, be aware of disk space. All libraries are installed inside of the app directory itself, so should not interfere with any system libs.

---

## Quick Start

Install build + runtime system packages — see [docs/BUILD.md](docs/BUILD.md) for the full list (Debian/Ubuntu `apt` commands, split into build vs runtime vs spell-check).

```bash
# Build C++ call service
cd drunk_call_service
make clean
make
cd -

# Get Python dependencies
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Run the app
venv/bin/python main.py
```

On GStreamer before 1.28, `make` also builds a patched dtls plugin from the Debian source. See [docs/BUILD.md](docs/BUILD.md).

### AppImage builds

**Download:** [Latest AppImage](https://github.com/confund0/siproxylin/releases/latest) (tested on: Debian 12-13, Arch)

**Run:**
```bash
chmod +x Siproxylin-*.AppImage
./Siproxylin-*.AppImage
```

---

## What Works Now ✅

- ✅ **Text messaging** - 1-to-1 and group chats (MUC)
- ✅ **OMEMO encryption** - End-to-end encrypted messaging (XEP-0384)
- ✅ **Audio calls** - Works with Conversations, Monal and Dino (calls always go through a TURN relay)
- ✅ **File attachments** - HTTP Upload (XEP-0363). Received and sent files go through the account proxy
- ✅ **Message features** - Reactions, replies, corrections
- ⚠️ **Per-account proxy** - SOCKS5/HTTP proxy for XMPP, registration, received and sent files. Not yet for call media
- ✅ **Account registration** - XEP-0077 with CAPTCHA support (XEP-0158)
- ✅ **Multi-language spell checking** - en, de, ru, lt, es, ro, ar
- ✅ **Themes** - Multiple color schemes (matters at night!)
- ✅ **Video calls** - Linux (since v0.0.29). Works with Conversations, Monal and Dino. Windows and macOS later
- ⏳ **Screen sharing** - Planned
- ⏳ **Windows** - A build exists but lags behind (older slixmpp 1.8.5). The release waits until Linux is stable
- ⏳ **macOS** - Planned

---

## Screenshots

<p float="left">
  <img src=".github/screenshots/Siproxylin-start.png" alt="Main Window" width="48%" />
  <img src=".github/screenshots/Siproxylin-view.png" alt="Chat View" width="48%" />
</p>

<p float="left">
  <img src=".github/screenshots/Siproxylin-test-msg.png" alt="Messaging" width="48%" />
  <img src=".github/screenshots/call-window-ok.png" alt="Audio Call" width="48%" />
</p>

<details>
<summary>More Screenshots (Account Management, MUC)</summary>

<p float="left">
  <img src=".github/screenshots/Siproxylin-add-acc.png" alt="Add Account" width="48%" />
  <img src=".github/screenshots/Siproxylin-create-acc.png" alt="Register Account" width="48%" />
</p>

<p float="left">
  <img src=".github/screenshots/Siproxylin-edit-acc.png" alt="Edit Account" width="48%" />
  <img src=".github/screenshots/Siproxylin-MUC-dialog.png" alt="Group Chat (MUC)" width="48%" />
</p>

</details>

---

## The Idea

Yes, it's another XMPP client. But hear me out.

Someone will say "meh, just another XMPP client". Most won't even look here because they simply don't know, nor do they care. They have WhatsApp, some have Signal, most have some popular social media app which supports messaging. "Privacy" is becoming a buzzword without meaning, "self-hosted" sounds like a name of a sex toy, while "convenient" is anything that helps pollute our informational space with another set of filter-applied selfies *right now*.

But I like XMPP. Since the first time I heard about it back when Google enabled web-driven chat on Gmail, I saw it as a big step forward from IRC. XMPP smelled like progress driven by (let's be honest here) a pretty ugly set of XMLs, but hey, we got a federated network with an extensible **standard**. And then, after some years, I accidentally ran into Conversations.im and tried their app. I realized that there are more people nostalgic enough to resurrect and enhance something that was great from the beginning and just shamefully forgotten. This flipped my brain: we have e2e encryption, we can self-host isolated or become part of a larger network, and there are people who actually use it and apps that do it. Cool!

But after a short while I realized we don't really have a solid desktop application. Sure there is Dino, and it's good — it even respects HTTPS_PROXY variables to bring you some anonymity — but it lacks many features I'd love to see in an e2e messenger. So I quickly drafted in my head the missing features:

1. **Proxy per account** - Route different identities through different networks
2. **Enforced call relays** - the peer never sees your IP address
3. **Multi-platform** - Works everywhere (Linux first, others coming)
4. **Contacts grouped by account** - Clean separation of identities
5. **Configurable logging** - Debug when needed, silent when not
6. **Local files encryption** - Protect config, DB, attachments, logs (available via gocryptfs, see --dot-data-dir option)
7. **Notifications privacy** - Hide text/sender when needed
8. **Standard classic menus** - No twisted GNOME labyrinth, simple File->Add, Edit->Account, intuitive right-click context menus, etc.
9. **Spell checker** - Actually works (Dino's didn't for me)
10. **Theme support** - Dark mode matters, well most of the current design sucks, but themes are separated and easy to tweak.
11. **Screen sharing** - Coming soon
12. **Group calls** - Future goal

Making a fully working client, for a single person who's not even an experienced developer (I'm an infra guy), would take a year. So I was carrying this idea with me, looking for existing options to start with, and then someone asked me: did you try AI-assisted development? I decided to give it a try (honestly I didn't believe we'd get far), but here we go: the version I'm releasing today became functional after **7 weeks of intense work and a considerable amount of non-halal beverages**. Russian-Irish mix, which I happen to be, comes with certain cultural obligations... Hence the app core is created using "brewery," "barrels," and "taps" - metaphors wherever they fitted.

---

## The Disclaimer

No matter how badly I want this app to be perfect, I'm afraid it's not there yet. After all these hours spent testing, code reviewing, and three massive refactoring iterations, I still have some doubts and occasionally find issues. Even the most motivated developer using best-in-class AI assistance can start drifting into quick patches when dealing with a larger codebase, and we're talking about **150+ Python files and 55,000+ lines of code**. It took 7 weeks, which means 5k lines per week, or 1,000 lines per day.

So definitely **use it with caution**, and please don't be shy about reporting issues, I bet you'll find quite a few.

---

## Known Issues

- **Platform:** Currently Linux-only. A Windows build exists but lags behind; macOS is planned.
- **Calls:** Call media does not go through the proxy yet.
- **Unread counters:** sometimes wrong after a restart.
- **Members-only group chats:** joining depends on the server approving you automatically.

Report bugs: [GitHub Issues](https://github.com/confund0/siproxylin/issues)

---

## Technical Details

### The Name (Siproxylin)

When I was a kid, I enjoyed chemistry. **Pyroxylin** (smokeless powder/nitrocellulose) popped into my mind. I was **sip**ping continuously during development, and I badly wanted **proxies**. Pyroxylin → SipProxyLin. Made sense to me.

### Tech Stack

**Python + SQLite + Qt6 + slixmpp + gRPC + GStreamer (webrtcbin) + C++**

### Architecture Overview

I'll confess: I borrowed Dino's DB structure to start, just to not reinvent the wheel. The XMPP part spins around **slixmpp**. Here slixmpp is wrapped into a client called **DrunkXMPP** (`./drunk_xmpp/`), which handles asynchronous signaling for protocol events and implements all required methods for client interactions.

**Jingle** was difficult. Siproxylin uses XEP-0353 from slixmpp, however XEP-0166, XEP-0167, XEP-0176, XEP-0320 have been added to `./drunk_call_hook/` on the fly. **XEP-0158** (media support for CAPTCHA) also wasn't there and had to be added. A few bugs popped up when dealing with slixmpp — runtime patches have been made for them (see `./drunk_xmpp/slixmpp_patches`).

DrunkXMPP is loaded by Siproxylin Core (`./siproxylin/core/`), which connects with the Qt6-based GUI (`./siproxylin/gui/`). When a call comes in, Jingle requests are passed to CallBridge (`./drunk_call_hook/`), which translates them into **gRPC** requests and passes them to the C++ service (`./drunk_call_service/`), which uses **GStreamer** to handle WebRTC, trickle-ICE, TURN, and audio/video (screen sharing coming soon).

Siproxylin starts as a single Python process with two threads: one keeps a heartbeat between CallBridge and the C++ call service, the other does everything else. CallBridge starts the call service when the app starts. Each component writes logs (defaults to INFO, can be disabled via global and per-account settings), and the app has a built-in log viewer for convenience.

**Supported XEPs:** 29 total (see Help → About in the app)

### Paths

If you run `python3 main.py` (use venv with requirements.txt), the app runs in "dev" mode and creates:

```
./sip_dev_paths/
  ├── cache/      # Avatars
  ├── config/     # User preferences
  ├── data/       # Database, attachments
  └── logs/       # main.log, xmpp-protocol.log, account-{id}-app.log, drunk-call-service.log, drunk-call-service-stdout.log, drunk-call-service.err
```

For production, two command-line parameters are available:

1. `--xdg` - Respects `~/.config` and `~/.local` paths
2. `--dot-data-dir` - Uses old-fashioned `~/.siproxylin` with everything inside

**AppImage default: `--dot-data-dir`** because of three reasons:

1. Easy to navigate (convenience)
2. Easy to delete (security)
3. Easy to mount as gocryptfs or equivalent (privacy)

---

## Proxies

Siproxylin supports **proxies per account**. Even the **registration wizard** asks if you'd like to use a proxy. SOCKS5 and HTTP are both supported, and if you register an account using a proxy, it's automatically saved with that account's settings.

**Received files** are downloaded through the account proxy, and the proxy resolves the host name. If the proxy setting is broken, the download fails; it never falls back to a direct connection. Files download automatically only from roster contacts with a subscription and from your own other devices, up to 25 MB. Files from other senders and from group chats show "Click to download" (up to 256 MB). Only https links are fetched. Sent files (HTTP Upload, plain and OMEMO) also go through the account proxy.

### Use Cases

1. **Don't want to expose your private XMPP server?** Add Wireguard directly on your server and use **wireproxy** with the SOCKS5 socket.
2. **Sensitive group chats** - Joining a group about stuff like flat earth, alcoholism or BDSM for beginers? Install Tor and point Siproxylin to its SOCKS5 socket.
3. **Corporate network** - Only way out is via Squid proxy? Route your account through the HTTP proxy and enjoy texts. Calls do not go through the proxy yet.

---

## Calls

Siproxylin supports **audio and video calls**. They are tested with Conversations (Android), Monal (iOS) and Dino (Linux). Incoming calls from Conversations work since the switch from Go/Pion to C++/GStreamer/webrtcbin. Outgoing calls to current Conversations work since v0.0.30: current Conversations offers only ECDSA suites, and GStreamer before 1.28 used an RSA certificate, so Siproxylin now ships a patched dtls plugin.

### Call Privacy

Siproxylin **forces calls to be relayed**, so the peer never sees your IP address. The TURN server sees it. The call window shows technical details: advertised IP addresses of both ends and the connection choice. Siproxylin requests TURN details from your XMPP server (XEP-0215). If your server gives no TURN details, the call cannot connect, because Siproxylin allows only relayed connections. Your XMPP server must offer a TURN server (XEP-0215).

---

## Installation

### AppImage

Download the latest AppImage from [Releases](https://github.com/confund0/siproxylin/releases):

```bash
chmod +x Siproxylin-*.AppImage
./Siproxylin-*.AppImage
```

### From Source

See [docs/BUILD.md](docs/BUILD.md) for build instructions.

---

## License

Siproxylin is dual-licensed:

### AGPL-3.0 (Open Source)

For open source projects and personal use, Siproxylin is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**.

This means:
- ✅ Free to use, modify, and distribute
- ✅ Perfect for open source projects
- ✅ Use in personal/non-commercial projects
- ⚠️ **Network use = distribution** - If you run Siproxylin as a service (SaaS, internal corporate tool, etc.), you must open source your entire application under AGPL-3.0

---

*"Free to use for free software. Wanna commercialize it? Let's talk business."*

---

## Contributing

Contributions are welcome! By contributing, you agree that your contributions will be licensed under AGPL-3.0.

**Note:** This is a solo project built with AI assistance. Progress may be sporadic, but issues are tracked and appreciated.

Found a bug? Have a feature request? [Open an issue](https://github.com/confund0/siproxylin/issues)

---

## Documentation

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

[docs/CONTRIBUTING.md](docs/CONTRIBUTING.md)

[docs/ROADMAP-PUBLIC.md](docs/ROADMAP-PUBLIC.md)

[docs/BUILD.md](docs/BUILD.md)

---

## Dependencies

- Python 3.11+
- PySide6 (Qt6) - LGPL-3.0
- slixmpp - MIT
- GStreamer - LGPL-2.1+
- gRPC - Apache-2.0
- cryptography - BSD-3-Clause/Apache-2.0
- slixmpp-omemo, aiohttp, aiohttp-socks, python-socks, qasync

All dependencies are compatible with AGPL-3.0.

## Contact

As this is a side solo project there is no offcial support, however you can try your luck in a channel siproxylin@conference.conversations.im.
