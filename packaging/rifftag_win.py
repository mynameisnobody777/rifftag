"""PyInstaller entry point.

Deliberately NOT ``rifftag/__main__.py``: that file imports with
``from .gui.main import run``, and PyInstaller analyses whatever you hand it
as a loose script rather than as part of a package. The relative import then
resolves to nothing, the whole application drops out of the dependency graph,
and the build "succeeds" with a 23 MB exe containing the Python runtime and
none of the program. Absolute imports here keep the analyser on the trail.
"""

import sys

from rifftag.gui.main import run

if __name__ == "__main__":
    sys.exit(run(sys.argv))
