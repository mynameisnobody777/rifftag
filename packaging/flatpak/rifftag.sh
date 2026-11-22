#!/bin/sh
# RiffTag inside the sandbox. PYTHONPATH is not needed — the package is
# installed into the runtime's site-packages — but /app/bin has to be on
# PATH so the bundled ffmpeg is the one found by shutil.which("ffmpeg").
PATH="/app/bin:$PATH"
export PATH
exec python3 -m rifftag "$@"
