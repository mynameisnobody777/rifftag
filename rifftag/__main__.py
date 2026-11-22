"""``python3 -m rifftag`` launches the window."""

import sys

from .gui.main import run

if __name__ == "__main__":
    sys.exit(run(sys.argv))
