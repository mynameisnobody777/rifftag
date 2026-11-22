#!/usr/bin/env bash
# Build a shareable .deb for RiffTag.
#
# Re-runnable: wipes its staging dir each time and writes dist/rifftag_<ver>_all.deb.
# Architecture is "all" because nothing here is compiled — the heavy lifting
# (PyQt6, mutagen) comes from the distro's own packages, which keeps the .deb
# at a few megabytes instead of the ~80MB a bundled runtime would cost.
#
#   ./packaging/build-deb.sh            # build
#   sudo dpkg -i dist/rifftag_*.deb     # install
#   sudo apt -f install                 # pull deps if any are missing
#   sudo dpkg -r rifftag                # remove
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Read the version out of the file as TEXT, never by importing it. This
# project lives on a CIFS share where a just-edited module can still be
# served from a stale __pycache__ entry — a build right after a version bump
# shipped 0.1 when the source already said 0.2.
VERSION="$(python3 -c 'import re,pathlib; \
    print(re.search(r"^VERSION\s*=\s*\"([^\"]+)\"", \
    pathlib.Path("rifftag/gui/about.py").read_text(), re.M).group(1))')"
PKG="rifftag"
STAGE="$ROOT/build/deb"
OUT="$ROOT/dist"

rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/lib/python3/dist-packages" \
         "$STAGE/usr/share/$PKG" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/$PKG" \
         "$STAGE/usr/bin" \
         "$OUT"

# --- code -------------------------------------------------------------
cp -r rifftag "$STAGE/usr/lib/python3/dist-packages/"
find "$STAGE/usr/lib/python3/dist-packages/rifftag" \
     -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true

# --- artwork ----------------------------------------------------------
cp -r assets "$STAGE/usr/share/$PKG/"
for px in 16 22 24 32 48 64 128 256 512; do
    d="$STAGE/usr/share/icons/hicolor/${px}x${px}/apps"
    mkdir -p "$d"
    cp "assets/icon-$px.png" "$d/$PKG.png"
done

# --- launcher ---------------------------------------------------------
cat > "$STAGE/usr/bin/$PKG" <<'LAUNCH'
#!/bin/sh
exec python3 -m rifftag "$@"
LAUNCH
chmod 755 "$STAGE/usr/bin/$PKG"

cat > "$STAGE/usr/share/applications/$PKG.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=RiffTag
GenericName=Music Tag Editor
Comment=Edit. Rename. Organize.
Exec=rifftag %F
Icon=rifftag
Terminal=false
Categories=AudioVideo;Audio;Music;
MimeType=audio/mpeg;audio/flac;audio/ogg;audio/x-vorbis+ogg;audio/opus;audio/mp4;audio/x-m4a;audio/x-ms-wma;audio/x-wav;audio/x-aiff;audio/x-ape;audio/x-wavpack;audio/x-musepack;
Keywords=tag;id3;mp3;flac;rename;music;metadata;
DESKTOP

# --- metadata ---------------------------------------------------------
# Summed directly rather than with du: on the CIFS mount this project lives
# on, du reports 0 for a tree that was written moments earlier because block
# accounting has not settled yet, and the package ships Installed-Size: 0.
INSTALLED_KB="$(python3 - "$STAGE" <<'SIZE'
import os, sys
total = sum(os.path.getsize(os.path.join(root, name))
            for root, _, names in os.walk(sys.argv[1])
            for name in names)
print(max(1, -(-total // 1024)))
SIZE
)"
cat > "$STAGE/DEBIAN/control" <<CONTROL
Package: $PKG
Version: $VERSION
Section: sound
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-pyqt6 (>= 6.2), python3-mutagen (>= 1.45)
Recommends: python3-pyqt6.qtmultimedia, ffmpeg
Installed-Size: $INSTALLED_KB
Maintainer: Dave Yeubanks <davidyeubanks@gmail.com>
Homepage: https://example.invalid/rifftag
Description: Music tag editor and organiser
 RiffTag edits the information tags inside music files and organises the
 files themselves.
 .
 It can rename files and folders from their tags using templates, read tags
 back out of existing file names, edit a whole album at once, manage cover
 art, and fetch album details from MusicBrainz or Discogs.
 .
 It also plays the files, converts between formats, cleans lyrics up for
 streaming delivery and times them to the music as an .lrc, and runs a
 release check that reports what a distributor would send back.
 .
 Playback needs python3-pyqt6.qtmultimedia; converting and the loudness
 measurement need ffmpeg. Both are recommended, not required: without them
 the rest of the program works and those features explain what is missing.
 .
 Supports mp3, flac, ogg, opus, m4a/mp4, wma, wav, aiff, dsf, ape, wv, mpc
 and more, covering ID3v2, iTunes, Vorbis comments, APEv2 and Windows Media
 tags.
CONTROL

cat > "$STAGE/DEBIAN/postinst" <<'POSTINST'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    # Icon and desktop caches are only consulted if they are current.
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -q /usr/share/icons/hicolor 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
fi
exit 0
POSTINST

cat > "$STAGE/DEBIAN/postrm" <<'POSTRM'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -q /usr/share/icons/hicolor 2>/dev/null || true
    fi
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database -q /usr/share/applications 2>/dev/null || true
    fi
fi
exit 0
POSTRM
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

cp README.md "$STAGE/usr/share/doc/$PKG/README.md"
gzip -9n "$STAGE/usr/share/doc/$PKG/README.md"
cat > "$STAGE/usr/share/doc/$PKG/copyright" <<'COPY'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: RiffTag

Files: *
Copyright: 2026 Dave Yeubanks
License: GPL-3+
 This program is free software: you can redistribute it and/or modify it
 under the terms of the GNU General Public License as published by the Free
 Software Foundation, either version 3 of the License, or (at your option)
 any later version.
 .
 This program is distributed in the hope that it will be useful, but WITHOUT
 ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
 FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
 more details.
 .
 On Debian systems, the complete text of the GNU General Public License
 version 3 can be found in "/usr/share/common-licenses/GPL-3".
COPY

# dpkg is strict about ownership; staging as the current user would ship
# files owned by whoever built them.
find "$STAGE" -type d -exec chmod 755 {} +
find "$STAGE/usr/lib" "$STAGE/usr/share" -type f -exec chmod 644 {} +
chmod 755 "$STAGE/usr/bin/$PKG" "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

DEB="$OUT/${PKG}_${VERSION}_all.deb"
rm -f "$DEB"
fakeroot dpkg-deb --build --root-owner-group "$STAGE" "$DEB" >/dev/null 2>&1 \
    || dpkg-deb --build --root-owner-group "$STAGE" "$DEB"

echo "built: $DEB"
dpkg-deb --info "$DEB" | sed -n '2,12p'
echo
echo "size: $(du -h "$DEB" | cut -f1)"
