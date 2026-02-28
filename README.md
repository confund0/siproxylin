# Siproxylin

**A privacy-focused XMPP desktop client with per-account proxies and enforced call relaying.**

---

## Quick Start

```bash
# Install go 1.24:
# https://go.dev/dl/

# Install GStreamer libraries:
# gir1.2-gstreamer-1.0, gstreamer1.0-alsa, gstreamer1.0-gl, gstreamer1.0-gtk3, gstreamer1.0-libav, gstreamer1.0-nice, gstreamer1.0-pipewire, gstreamer1.0-plugins-bad, gstreamer1.0-plugins-base, gstreamer1.0-plugins-base, gstreamer1.0-plugins-good, gstreamer1.0-plugins-good, gstreamer1.0-plugins-ugly, gstreamer1.0-pulseaudio, gstreamer1.0-tools, gstreamer1.0-x, gstreamer1.0-x, libgstreamer-gl1.0-0, libgstreamer-plugins-bad1.0-0, libgstreamer-plugins-base1.0-0, libgstreamer-plugins-base1.0-0, libgstreamer-plugins-base1.0-dev, libgstreamer1.0-0, libgstreamer1.0-0, libgstreamer1.0-dev, libgtk-4-media-gstreamer, qtgstreamer-plugins-qt5

# Install Qt6:
# libqt6*

# Install hunspell if you want the spell checker

# Build Go call service
cd drunk_call_service
./install-tools.sh 
./build.sh
cd -

# Get Python dependencies
python3 -m venv venv
venv/bin/pip install -r requirements.txt

# Run the app
venv/bin/python main.py
```

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
- ✅ **Audio calls** - Works with Dino, most XMPP clients (outgoing to Conversations.im)
- ✅ **File attachments** - HTTP Upload (XEP-0363)
- ✅ **Message features** - Reactions, replies, corrections, threading
- ✅ **Per-account proxy** - SOCKS5/HTTP proxy support with zero IP leaks
- ✅ **Account registration** - XEP-0077 with CAPTCHA support (XEP-0158)
- ✅ **Multi-language spell checking** - en, de, ru, lt, es, ro, ar
- ✅ **Themes** - Multiple color schemes (matters at night!)
- ⏳ **Video calls** - In progress
- ⏳ **Screen sharing** - Planned
- ⏳ **Windows/macOS** - Linux-only for now

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
2. **Enforced call relays** - No IP leaks during calls
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

No matter how badly I want this app to be perfect, I'm afraid it's not there yet. After all these hours spent testing, code reviewing, and three massive refactoring iterations, I still have some doubts and occasionally find issues. Even the most motivated developer using best-in-class AI assistance can start drifting into quick patches when dealing with a larger codebase, and we're talking about **100+ Python files and 35,000+ lines of code**. It took 7 weeks, which means 5k lines per week, or 1,000 lines per day.

So definitely **use it with caution**, and please don't be shy about reporting issues, I bet you'll find quite a few.

---

## Known Issues

- **Conversations.im → Siproxylin calls:** Won't connect due to ICE nomination issue in Conversations' WebRTC stack (Siproxylin → Conversations works fine)
- **Platform:** Currently Linux-only (Windows/MacOS support planned)
- **Unread counters:** Sometimes pops up after app restart, investigating
- **Unclear process of MUC membership:** There is lack of information on how members-only MUC are handled, currently it relies on the mercy of auto-approve by server
- **MUC dialog lacks real-time:** Some changes do not update dialog GUI on the fly

Report bugs: [GitHub Issues](https://github.com/confund0/siproxylin/issues)

---

## Technical Details

### The Name (Siproxylin)

When I was a kid, I enjoyed chemistry. **Pyroxylin** (smokeless powder/nitrocellulose) popped into my mind. I was **sip**ping continuously during development, and I badly wanted **proxies**. Pyroxylin → SipProxyLin. Made sense to me.

### Tech Stack

**Python + SQLite + Qt6 + slixmpp + gRPC + GStreamer + Pion (WebRTC for Go)**

### Architecture Overview

I'll confess: I borrowed Dino's DB structure to start, just to not reinvent the wheel. The XMPP part spins around **slixmpp**. Here slixmpp is wrapped into a client called **DrunkXMPP** (`./drunk_xmpp/`), which handles asynchronous signaling for protocol events and implements all required methods for client interactions.

**Jingle** was difficult. Siproxylin uses XEP-0353 from slixmpp, however XEP-0166, XEP-0167, XEP-0176, XEP-0320 have been added to `./drunk_call_hook/` on the fly. **XEP-0158** (media support for CAPTCHA) also wasn't there and had to be added. A few bugs popped up when dealing with slixmpp — runtime patches have been made for them (see `./drunk_xmpp/slixmpp_patches`).

DrunkXMPP is loaded by Siproxylin Core (`./siproxylin/core/`), which connects with the Qt6-based GUI (`./siproxylin/gui/`). When a call comes in, Jingle requests are passed to CallBridge (`./drunk_call_hook/`), which translates them into **gRPC** requests and passes them to the Go service (`./drunk_call_service/`), which uses **Pion** to handle WebRTC, ICE, TURN, and audio (video and screen sharing coming soon).

Siproxylin starts as a single Python process with two threads: one for keeping a heartbeat between CallBridge and the Go service, and another for everything else. The Go service is started by CallBridge at application startup. Each component writes logs (defaults to INFO, can be disabled via global and per-account settings), and the app has a built-in log viewer for convenience.

**Supported XEPs:** 29 total (see Help → About in the app)

### Paths

If you run `python3 main.py` (use venv with requirements.txt), the app runs in "dev" mode and creates:

```
./sip_dev_paths/
  ├── cache/      # Avatars
  ├── config/     # User preferences
  ├── data/       # Database, attachments
  └── logs/       # main.log, xmpp-protocol.log, account-{id}-app.log, drunk-call-service.log
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

### Use Cases

1. **Don't want to expose your private XMPP server?** Add Wireguard directly on your server and use **wireproxy** with the SOCKS5 socket.
2. **Sensitive group chats** - Joining a group about stuff like flat earth, alcoholism or BDSM for beginers? Install Tor and point Siproxylin to its SOCKS5 socket.
3. **Corporate network** - Only way out is via Squid proxy? Route your account through the HTTP proxy and enjoy texts and calls.

**Leak testing:** I tested with tcpdump and found **zero IP leaks** — it seems to be solid.
That includes the calls, proxy settings are passed via gRPC and applied in the Go code.

---

## Calls

Siproxylin supports **audio calls** with most XMPP clients. Works perfectly between two Siproxylin's, works with Dino in both directions. **Outgoing calls to Conversations.im work fine**, but incoming calls from Conversations won't connect due to an ICE nomination issue in their WebRTC stack (still investigating). Conversations won't nominate a successful ICE pair even with their own TURN server is advertised on both ends of the call.

### Call Privacy

Siproxylin **forces calls to be relayed** to avoid IP leaks. The call window shows technical details: advertised IP addresses of both ends and the connection choice. Siproxylin requests TURN details from your XMPP server (XEP-0215), and if not received, should fall back to the public Jami TURN servers (fallback wasn't properly tested).

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

All dependencies are compatible with AGPL-3.0.
