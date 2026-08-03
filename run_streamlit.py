"""Start Streamlit with the project favicon installed before Safari sees it."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
from pathlib import Path

import streamlit


PROJECT_ROOT = Path(__file__).resolve().parent
FAVICON = PROJECT_ROOT / "assets" / "favicon.png"
STREAMLIT_STATIC = Path(streamlit.__file__).resolve().parent / "static"
STREAMLIT_FAVICON = STREAMLIT_STATIC / "favicon.png"
INDEX_HTML = STREAMLIT_STATIC / "index.html"


def install_initial_favicon() -> None:
    """Replace the favicon in Streamlit's initial HTML response.

    Safari commonly keeps the favicon that arrives with Streamlit's HTML shell
    and ignores the later icon update sent by ``st.set_page_config``.
    """
    # Version the URL separately from the file hash: Safari can otherwise retain
    # an earlier favicon mapping even after the image itself is replaced.
    favicon_hash = hashlib.sha256(FAVICON.read_bytes()).hexdigest()[:12]
    favicon_url = f"./favicon.png?v={favicon_hash}-safari-2"
    shutil.copyfile(FAVICON, STREAMLIT_FAVICON)

    html = INDEX_HTML.read_text(encoding="utf-8")
    updated_html, replacements = re.subn(
        r'<link rel="(?:shortcut )?icon" href="\./favicon\.png(?:\?[^\"]*)?"\s*/>',
        f'<link rel="icon" type="image/png" href="{favicon_url}" />',
        html,
        count=1,
    )
    if replacements != 1:
        raise RuntimeError("Could not find Streamlit's favicon link in index.html.")
    INDEX_HTML.write_text(updated_html, encoding="utf-8")


if __name__ == "__main__":
    install_initial_favicon()
    os.execv(sys.executable, [sys.executable, "-m", "streamlit", "run", "ine_explorer_v2.py"])
