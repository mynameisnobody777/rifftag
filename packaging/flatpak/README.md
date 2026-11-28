# Flatpak packaging

Build and install locally:

```bash
flatpak install -y flathub org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
sudo apt install flatpak-builder          # once
cd /mnt/claude-brain/apps/rifftag
flatpak-builder --user --install --force-clean build packaging/flatpak/com.nobody777.RiffTag.yml
flatpak run com.nobody777.RiffTag
```

Verified working in the sandbox on 2026-11-22: reads a real mp3, converts it
to FLAC, measures loudness (-12.35 LUFS, true peak -1.17 dBTP) and runs the
release check. Installed size ~281 MB.

## The three things that were not obvious

**ffmpeg has to be built here.** `org.freedesktop.Platform.ffmpeg-full` ships
*libraries only* — checked: its `bin/` contains one binary, `dec265`. RiffTag's
converter and the release check's loudness reading both shell out to the
`ffmpeg` command, so without this module those two features are dead inside
the sandbox. It is configured down to what RiffTag actually asks for, so
`lavfi` is absent — which only matters because the *test fixtures* use it, not
the app.

**The runtime is freedesktop, not KDE.** PyQt6's wheels bundle their own Qt6,
so building against a runtime that also ships Qt would put two copies of Qt in
the image.

**`sys.prefix` is `/usr`, not `/app`.** The app is installed under `/app` but
runs the *runtime's* python, so the `sys.prefix/share/rifftag/assets` candidate
in `splash._find_assets()` never matches and the window comes up with no
artwork at all. There is an explicit `/app/share/rifftag/assets` candidate for
this, with a test pinning it.

## Cutting a new version

1. Bump `VERSION` in `rifftag/gui/about.py`.
2. `./packaging/release.sh --deploy` — publishes the source tarball.
3. Update the `sha256` of the `rifftag` module source in the manifest to match
   the new tarball (`sha256sum web/downloads/rifftag-*-source.tar.gz`).
4. Add a `<release>` entry to the metainfo.
5. Rebuild and test before submitting.

## Submitting to Flathub

Requires a GitHub account: submission is a pull request against
`github.com/flathub/flathub`, reviewed by a person. Before opening it, the
source wants to live in a public git repo — Flathub accepts an archive URL
with a hash (what the manifest uses today), but a tagged GitHub release is
what reviewers expect and it settles the GPL source-availability obligation
at the same time.

## Matroska is not in the Flatpak (yet)

MKVToolNix is not in the freedesktop runtime and was not built into the
manifest (it needs boost, libebml, libmatroska, …). Inside the sandbox
`.mkv/.mka/.webm` files list with "MKVToolNix is needed" as their error.
The fix, if wanted, is an `mkvtoolnix` module built with only the CLI tools
(`--disable-gui`).
