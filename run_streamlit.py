"""Start the Streamlit application from the repository root."""

from __future__ import annotations

import os
import sys


if __name__ == "__main__":
    os.execv(
        sys.executable,
        [sys.executable, "-m", "streamlit", "run", "ine_explorer_v2.py"],
    )
