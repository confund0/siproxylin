# Release Build (Linux AppImage)

Status: planned. The old tooling (build-appimage.sh, .package-builder.sh, .github/workflows/release.yml) is still the one in use until the switch-over below.

## Goal

- The AppImage that GitHub CI publishes is built from the same inputs as the one the maintainer tested.
- The build runs without manual steps, locally and in CI, with one script.
- Base system: Debian 12 (bookworm), glibc 2.36, so the AppImage runs on Debian 12, Ubuntu 22.04 and newer. The maintainer's daily system is Debian 13; the release is still tested as the built AppImage.
- Debian 12 is in LTS. Check the LTS coverage of the bundled packages; move the base to Debian 13 when it gets thin.

## Flow

1. Local rehearsal in the maintainer's Debian 12 chroot (/opt/chroots/debian-12):
   - Update the system with apt (the maintainer decides when updates come in).
   - Check out the release commit in the app directory.
   - Run scripts/build-release.sh. It builds the call service, builds the patched dtls plugin from the Debian source, builds the AppImage and writes release-manifest.txt.
   - Test the AppImage (chroot and bwrap).
   - Commit release-manifest.txt with the release commit and tag it.
2. Push the commit and the tag.
3. GitHub CI (.github/workflows/build-release.yml) runs scripts/build-release.sh in a clean Debian 12 container, checks the manifest, and uploads the AppImage to the GitHub release.

AppImages are never uploaded by hand. CI builds and publishes them.

## Files

- scripts/build-release.sh: the one build script, used locally and by CI.
- release-manifest.txt: exact versions of the rehearsal build.
- .github/workflows/build-release.yml: the new CI workflow. Until the switch-over it runs only when started by hand (workflow_dispatch) and does not publish.

## Manifest

The rehearsal and CI must use the same inputs. The manifest records them:

- Python packages: the output of pip freeze from the build venv. CI installs the Python packages exactly from the manifest, not from requirements.txt (requirements.txt has minimum versions only; a newer slixmpp-omemo broke OMEMO once).
- Debian packages that end up in the AppImage or are used to build it: name and version from dpkg-query.
- The Debian source version of gst-plugins-bad and the checksum of the dtls patch.

CI compares its installed Debian packages with the manifest. Any difference fails the build. Then the rehearsal is done again with the updated packages. So a published AppImage never differs from a tested one.

Later, if CI fails often because Debian removed old versions from its mirrors: install from snapshot.debian.org at the date of the rehearsal.

## Patched dtls plugin

- GStreamer before 1.28 makes an RSA default DTLS certificate. When Siproxylin is the DTLS server (for example on outgoing calls, when the peer answers with setup active), peers with ECDSA-only suites (current Conversations) fail with "no shared cipher". This affects 1.22 (Debian 12) and 1.26 (Debian 13). The patch in drunk_call_service/patches/gstreamer/ is the upstream 1.28 change; it applies to the 1.22 and the 1.26 Debian source.
- make (target gst-dtls) downloads the Debian source of the installed gst-plugins-bad with apt-get source, applies the patch, builds only the dtls plugin and puts it in drunk_call_service/bin/gst-plugins/. This needs deb-src entries and dpkg-dev, meson, ninja-build, libssl-dev.
- GStreamer 1.28 and newer have the fix; there the step is skipped.
- The AppImage must contain the patched plugin in place of the system one. The build fails if the plugin in the AppDir does not contain the ECDSA marker.
- Every release builds the plugin from the current Debian source, so Debian fixes to the plugin come in with each release.

## Update cadence

- The AppImage bundles its libraries (GStreamer, OpenSSL, Qt, Python and more). Users get library fixes only with a new AppImage.
- Make a new release when a Debian security advisory (DSA) affects a bundled package. Check at least once a month.

## Build cache (later)

Not in the first version; the first version builds everything every time. If builds are too slow, add cached layers, each with a key made from all its inputs:

- Base AppDir (Python runtime, pip packages, Qt, GStreamer, system libraries): requirements, Debian package versions, build script.
- Patched dtls plugin: gst-plugins-bad version, patch checksum.
- Call service binary: C++ source, proto files, Debian package versions.
- App code: never cached, always copied.

Rules: only exact key matches, never a fallback to an older cache entry. A full rebuild can always be forced.

## Switch-over

1. The new script and workflow exist next to the old ones.
2. The new workflow is started by hand until it makes a good AppImage.
3. One commit moves the tag trigger to the new workflow.
4. After one or two good releases, the old tooling is deleted.

Windows builds stay in the old workflow until they get their own plan.
