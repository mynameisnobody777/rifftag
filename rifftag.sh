#!/usr/bin/env bash
# Launcher. The project lives on the CIFS brain share, so it is run in place
# rather than installed; python3-pyqt6 and python3-mutagen come from apt.
exec python3 -m rifftag "$@"
