#!/usr/bin/env bash
# One command to cut and publish a release.
#
#   ./packaging/release.sh            # build + verify, deploy nothing
#   ./packaging/release.sh --deploy   # ...then push the site live
#
# What it does, in order: run the tests, build the .deb, install it, build a
# source tarball (the GPL obliges us to offer it beside the binaries), stage
# both under web/downloads, and optionally deploy the site.
#
# The Windows build is NOT handled here: it has to be produced in the Windows
# VM (see docs/WINDOWS_PORT.md) and lives in R2 because it is over Cloudflare's
# 25 MiB per-file limit for static assets. Upload a new one with:
#   npx wrangler r2 object put rifftag-downloads/rifftag-<ver>-windows.zip \
#       --file <zip> --content-type application/zip --remote
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

VERSION="$(python3 -c 'import re,pathlib; \
    print(re.search(r"^VERSION\s*=\s*\"([^\"]+)\"", \
    pathlib.Path("rifftag/gui/about.py").read_text(), re.M).group(1))')"
DEPLOY="${1:-}"

echo "== RiffTag $VERSION =="

echo "-- tests"
PYTHONPATH=. python3 -m pytest tests -q

echo "-- .deb"
./packaging/build-deb.sh >/dev/null
sudo dpkg -i "dist/rifftag_${VERSION}_all.deb" >/dev/null
echo "   installed: $(dpkg-query -W -f='${Version}' rifftag)"

echo "-- source tarball"
mkdir -p web/downloads
# Both are version-stamped, so without this the previous release's files stay
# downloadable for ever and turn up in SHA256SUMS beside the current ones.
rm -f web/downloads/rifftag-*-source.tar.gz web/downloads/rifftag_*_all.deb
# Built from git (tracked files at HEAD), never by tarring the working folder.
# Tarring the folder shipped the gitignored Tag&Rename help text and reference
# screenshots (someone else's copyright) plus stray dev files in the 0.3
# tarball. .git is not in git archive output; web/ (the website, with its
# videos) and wrangler.jsonc are dropped by pathspec.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "   !! uncommitted changes - commit first, the tarball is built from HEAD" >&2
    exit 1
fi
git archive --format=tar.gz --prefix=rifftag/ \
    -o "web/downloads/rifftag-${VERSION}-source.tar.gz" HEAD \
    -- . ':(exclude)web' ':(exclude)wrangler.jsonc'
if tar tzf "web/downloads/rifftag-${VERSION}-source.tar.gz" \
        | grep -qE 'refpics|tagrename-help'; then
    echo "   !! Tag&Rename reference material in the tarball" >&2
    exit 1
fi
cp "dist/rifftag_${VERSION}_all.deb" web/downloads/

# A website video inside the application package is how the .deb went from
# 5.6 MB to 20 MB once. Website source art lives in web/source-art/, never in
# assets/, and this is the tripwire.
DEB_MB=$(( $(stat -c%s "dist/rifftag_${VERSION}_all.deb") / 1048576 ))
if [ "$DEB_MB" -gt 9 ]; then
    echo "   !! the .deb is ${DEB_MB} MB - something large crept into assets/" >&2
    echo "      app assets belong in assets/, website art in web/source-art/" >&2
    exit 1
fi

echo "-- staged"
ls -lh web/downloads | tail -n +2 | awk '{printf "   %-36s %s\n", $9, $5}'
# Named explicitly rather than ./* — the glob caught SHA256SUMS itself, which
# listed a checksum of a file that no longer had it.
( cd web/downloads \
  && sha256sum "rifftag_${VERSION}_all.deb" "rifftag-${VERSION}-source.tar.gz" \
       > SHA256SUMS && echo "   SHA256SUMS written" )

if [ "$DEPLOY" = "--deploy" ]; then
    echo "-- deploying"
    npx wrangler deploy
    echo "   https://rifftag.davidyeubanks.workers.dev"
else
    echo "-- not deployed (pass --deploy to publish)"
fi
